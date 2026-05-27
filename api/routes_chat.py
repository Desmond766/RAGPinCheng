"""Conversation + chat endpoints.

Replaces the old `/sessions/*` surface from routes.py. Every endpoint here
requires an authenticated user; cross-user access is blocked at the row
ownership check.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from .auth import CurrentUser, require_csrf, require_user
from .conversation_runtime import (
    TurnPersistencePlan,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_lock,
    hydrate_session,
    list_conversations,
    list_messages,
    wrap_stream_with_persistence,
)
from .db import get_db
from .schemas import (
    ChatRequest,
    ConversationListResponse,
    ConversationStateDTO,
    ConversationSummaryDTO,
    CreateConversationResponse,
    MessageDTO,
    SourceDTO,
    source_to_dto,
)
from .sse import event

logger = logging.getLogger("api.routes_chat")

router = APIRouter(tags=["chat"])


# ── conversation CRUD ──────────────────────────────────────────────────────


@router.get("/conversations", response_model=ConversationListResponse)
def list_my_conversations(
    user: CurrentUser = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ConversationListResponse:
    rows = list_conversations(conn, user.id)
    return ConversationListResponse(
        conversations=[
            ConversationSummaryDTO(
                id=r["id"],
                title=r["title"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                turn_index=r["turn_index"],
            )
            for r in rows
        ]
    )


@router.post("/conversations", response_model=CreateConversationResponse)
def create_my_conversation(
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> CreateConversationResponse:
    row = create_conversation(conn, user.id)
    return CreateConversationResponse(**row)


@router.get("/conversations/{conversation_id}", response_model=ConversationStateDTO)
def get_my_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ConversationStateDTO:
    row = get_conversation(conn, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if row["user_id"] != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="forbidden")
    msgs_rows = list_messages(conn, conversation_id)
    messages = [
        MessageDTO(
            id=m["id"],
            role=m["role"],
            content=m["content"],
            sources_for_ui=(
                [source_to_dto(s) for s in json.loads(m["sources_json"])]
                if m["sources_json"]
                else None
            ),
            created_at=m["created_at"],
        )
        for m in msgs_rows
    ]
    return ConversationStateDTO(
        id=row["id"],
        title=row["title"],
        user_id=row["user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        turn_index=row["turn_index"],
        messages=messages,
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_my_conversation(
    conversation_id: str,
    user: CurrentUser = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    row = get_conversation(conn, conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if row["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    delete_conversation(conn, conversation_id)


# ── streaming chat ─────────────────────────────────────────────────────────


@router.post("/conversations/{conversation_id}/chat")
async def chat(
    conversation_id: str,
    body: ChatRequest,
    request: Request,
    user: CurrentUser = Depends(require_csrf),
) -> EventSourceResponse:
    # Authorization + existence check up front, with its own short-lived
    # connection so we don't hold one through the SSE stream.
    from .db import connect as db_connect
    pre_conn = db_connect()
    try:
        row = pre_conn.execute(
            "SELECT user_id, turn_index FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    finally:
        pre_conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if row["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    is_first_turn = int(row["turn_index"]) == 0

    lock = get_lock(conversation_id)

    async def event_generator() -> AsyncIterator[ServerSentEvent]:
        async with lock:
            try:
                # Hydrate ChatSession from DB on its own short-lived conn.
                # Doing it inside the lock means no two concurrent turns
                # see the same stale snapshot.
                hydrate_conn = db_connect()
                try:
                    conv_row = hydrate_conn.execute(
                        "SELECT * FROM conversations WHERE id = ?",
                        (conversation_id,),
                    ).fetchone()
                    session = hydrate_session(hydrate_conn, conv_row)
                finally:
                    hydrate_conn.close()

                prep, raw_stream = await asyncio.to_thread(
                    session.ask_stream,
                    body.query,
                    body.categories,
                )

                plan = TurnPersistencePlan(
                    conversation_id=conversation_id,
                    user_text=body.query,
                    is_first_turn=is_first_turn,
                )
                persistent_stream = wrap_stream_with_persistence(
                    raw_stream, session, plan
                )

                yield event("prep", {
                    "search_query": prep.search_query,
                    "rewrite_applied": prep.rewrite_applied,
                    "history_chars": prep.history_chars,
                    "budget": prep.budget,
                    "fresh_count": len(prep.fresh_sources),
                    "final_count": len(prep.final_sources),
                    "used_sources": [
                        source_to_dto(s).model_dump()
                        for s in session._sources_for_ui(prep.used_sources)
                    ],
                    "no_source_fallback": prep.no_source_fallback,
                })

                _STOP = object()

                def _next_chunk():
                    try:
                        return next(persistent_stream)
                    except StopIteration:
                        return _STOP

                while True:
                    if await request.is_disconnected():
                        try:
                            persistent_stream.close()
                        except Exception:
                            pass
                        break
                    chunk = await asyncio.to_thread(_next_chunk)
                    if chunk is _STOP:
                        break
                    if chunk:
                        yield event("token", {"text": chunk})

                result = session.last_turn_result
                if result is not None:
                    yield event("done", {
                        "answer_text": result.answer_text,
                        "timings": result.timings,
                        "history_chars": result.history_chars,
                        "budget": result.budget,
                        "sources": [
                            source_to_dto(s).model_dump()
                            for s in session._sources_for_ui(result.sources)
                        ],
                    })
            except Exception as exc:  # noqa: BLE001
                logger.exception("chat stream failed")
                yield event("error", {"message": str(exc)})

    return EventSourceResponse(event_generator())
