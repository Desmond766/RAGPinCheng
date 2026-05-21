"""HTTP endpoints. All paths are mounted under /api in main.py."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from src.config import (
    COLLECTION,
    EMBED_MODEL,
    LLM_MODEL,
    RERANK_ENABLED,
    RERANKER_MODEL,
)
from src.index import collection_stats, list_categories, parents_count

from . import feedback as feedback_log
from .schemas import (
    CategoriesResponse,
    ChatRequest,
    ConfigResponse,
    CreateSessionResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    MessageDTO,
    SessionStateDTO,
    SourceDTO,
    source_to_dto,
)
from .session_store import store
from .sse import event

logger = logging.getLogger("api.routes")

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    stats = collection_stats()
    return HealthResponse(
        status="ok",
        children=int(stats.get("children", 0)),
        parents=parents_count(),
    )


@router.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    return ConfigResponse(
        embed_model=EMBED_MODEL,
        reranker_model=RERANKER_MODEL,
        rerank_enabled=RERANK_ENABLED,
        llm_model=LLM_MODEL,
        collection=COLLECTION,
    )


@router.post("/feedback", response_model=FeedbackResponse)
def post_feedback(body: FeedbackRequest) -> FeedbackResponse:
    if body.kind not in ("answer", "citation"):
        raise HTTPException(status_code=400, detail="kind must be 'answer' or 'citation'")
    if body.kind == "answer" and body.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="answer feedback requires rating 'up' or 'down'")
    feedback_log.append(body)
    return FeedbackResponse(ok=True)


@router.get("/categories", response_model=CategoriesResponse)
def get_categories() -> CategoriesResponse:
    return CategoriesResponse(categories=list_categories())


@router.post("/sessions", response_model=CreateSessionResponse)
def create_session() -> CreateSessionResponse:
    sid = store.create()
    return CreateSessionResponse(session_id=sid)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    if not store.delete(session_id):
        raise HTTPException(status_code=404, detail="session not found")


@router.get("/sessions/{session_id}", response_model=SessionStateDTO)
def get_session(session_id: str) -> SessionStateDTO:
    entry = store.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found")
    msgs = [
        MessageDTO(
            role=m.role,
            content=m.content,
            sources_for_ui=(
                [source_to_dto(s) for s in m.sources_for_ui]
                if m.sources_for_ui
                else None
            ),
        )
        for m in entry.session.state.messages
    ]
    return SessionStateDTO(
        session_id=session_id,
        turn_index=entry.session.state.turn_index,
        messages=msgs,
    )


@router.post("/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest, request: Request) -> EventSourceResponse:
    entry = store.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found")

    async def event_generator() -> AsyncIterator[ServerSentEvent]:
        # Per-session lock: serialize concurrent turns on the same session.
        # Released in finally so abandoned streams free it.
        async with entry.lock:
            try:
                # ask_stream is sync; offload the prep call so the event
                # loop stays responsive during retrieval + LLM warmup.
                prep, raw_stream = await asyncio.to_thread(
                    entry.session.ask_stream,
                    body.query,
                    body.categories,
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
                        for s in entry.session._sources_for_ui(prep.used_sources)
                    ],
                    "no_source_fallback": prep.no_source_fallback,
                })

                # Pump the sync generator from a worker thread so the LLM
                # call doesn't block the event loop. Sentinel marks EOS.
                _STOP = object()

                def _next_chunk():
                    try:
                        return next(raw_stream)
                    except StopIteration:
                        return _STOP

                while True:
                    if await request.is_disconnected():
                        # Best-effort close so _wrap_stream's finally
                        # finalizes state with the partial text.
                        try:
                            raw_stream.close()
                        except Exception:
                            pass
                        break
                    chunk = await asyncio.to_thread(_next_chunk)
                    if chunk is _STOP:
                        break
                    if chunk:
                        yield event("token", {"text": chunk})

                # Stream exhausted — last_turn_result is now populated.
                result = entry.session.last_turn_result
                if result is not None:
                    yield event("done", {
                        "answer_text": result.answer_text,
                        "timings": result.timings,
                        "history_chars": result.history_chars,
                        "budget": result.budget,
                        "sources": [
                            source_to_dto(s).model_dump()
                            for s in entry.session._sources_for_ui(result.sources)
                        ],
                    })
            except Exception as exc:  # noqa: BLE001
                logger.exception("chat stream failed")
                yield event("error", {"message": str(exc)})

    return EventSourceResponse(event_generator())
