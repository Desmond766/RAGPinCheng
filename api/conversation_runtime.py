"""Persistent conversation runtime.

Stitches the in-memory `ChatSession` (which owns the rewrite/retrieve/merge/
generate pipeline) onto a SQLite-backed conversation row. Each turn:

  1. Hydrate a fresh `ChatSession` from DB (replay messages, restore
     `last_sources` from JSON snapshot so carry-forward survives reloads).
  2. Drive the existing `ask_stream` pipeline.
  3. After the stream finalizes, persist the new user+assistant messages
     and update the conversation row.

Concurrent turns on the same conversation are serialized by a per-conversation
asyncio.Lock kept in `_locks`. Locks live for the process lifetime — they're
tiny, and the dict is keyed by conversation_id so it's bounded by the active
user set.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Iterator

from src.retrieve import RetrievedParent
from src.session import ChatSession, Message, StreamingTurnPrep

from .db import connect

logger = logging.getLogger("api.conversation_runtime")

# Title shown in the sidebar — derived from the first user message,
# trimmed so it fits and doesn't expose a paragraph of text.
TITLE_MAX_CHARS = 40


_locks: dict[str, asyncio.Lock] = {}


def get_lock(conversation_id: str) -> asyncio.Lock:
    lock = _locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[conversation_id] = lock
    return lock


def discard_lock(conversation_id: str) -> None:
    _locks.pop(conversation_id, None)


# ── conversation row CRUD ───────────────────────────────────────────────────


def create_conversation(conn: sqlite3.Connection, user_id: int) -> dict:
    """Insert an empty conversation row and return it as a dict."""
    cid = uuid.uuid4().hex
    now = int(time.time())
    conn.execute(
        "INSERT INTO conversations (id, user_id, title, created_at, updated_at, "
        "turn_index, last_sources_json, last_search_query) "
        "VALUES (?, ?, ?, ?, ?, 0, NULL, '')",
        (cid, user_id, "新对话", now, now),
    )
    conn.commit()
    return {
        "id": cid,
        "title": "新对话",
        "user_id": user_id,
        "created_at": now,
        "updated_at": now,
        "turn_index": 0,
    }


def list_conversations(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, title, created_at, updated_at, turn_index "
        "FROM conversations WHERE user_id = ? "
        "ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()


def get_conversation(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()


def delete_conversation(conn: sqlite3.Connection, conversation_id: str) -> bool:
    cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()
    discard_lock(conversation_id)
    return cur.rowcount > 0


def list_messages(conn: sqlite3.Connection, conversation_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, role, content, sources_json, created_at "
        "FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()


# ── ChatSession <-> DB hydration ────────────────────────────────────────────


def _retrieved_parents_to_json(parents: list[RetrievedParent]) -> str:
    return json.dumps([dataclasses.asdict(p) for p in parents], ensure_ascii=False)


def _retrieved_parents_from_json(raw: str | None) -> list[RetrievedParent]:
    if not raw:
        return []
    try:
        records = json.loads(raw)
    except Exception:
        return []
    out: list[RetrievedParent] = []
    for r in records:
        # Defensive: tolerate older snapshots missing newer fields.
        out.append(RetrievedParent(
            parent_id=r["parent_id"],
            doc_title=r["doc_title"],
            category=r.get("category", ""),
            section_path=r.get("section_path", ""),
            source_path=r.get("source_path", ""),
            text=r.get("text", ""),
            score=float(r.get("score", 0.0)),
            matched_children=list(r.get("matched_children") or []),
            doc_type=r.get("doc_type", "pdf"),
            start_time=r.get("start_time"),
            company=r.get("company"),
            rrf_score=float(r.get("rrf_score", 0.0)),
        ))
    return out


def hydrate_session(conn: sqlite3.Connection, conv_row: sqlite3.Row) -> ChatSession:
    """Build a fresh ChatSession populated from the DB conversation state."""
    session = ChatSession()
    messages = list_messages(conn, conv_row["id"])
    for m in messages:
        sources_for_ui = None
        if m["sources_json"]:
            try:
                sources_for_ui = json.loads(m["sources_json"])
            except Exception:
                sources_for_ui = None
        session.state.messages.append(Message(
            role=m["role"],
            content=m["content"],
            sources_for_ui=sources_for_ui,
        ))
    session.state.turn_index = int(conv_row["turn_index"])
    session.state.last_search_query = conv_row["last_search_query"] or ""
    session.state.last_sources = _retrieved_parents_from_json(conv_row["last_sources_json"])
    return session


@dataclass
class TurnPersistencePlan:
    """Snapshot of what to persist after the streaming turn finalizes."""
    conversation_id: str
    user_text: str
    is_first_turn: bool


def persist_turn(
    plan: TurnPersistencePlan,
    session: ChatSession,
) -> None:
    """Write the user message + assistant message + conversation update to DB.

    Called once `ChatSession._wrap_stream` has finalized state (i.e. after
    the SSE generator is exhausted or closed).
    """
    state = session.state
    # The last two messages on state.messages are the just-appended pair.
    if len(state.messages) < 2:
        # Defensive: nothing to persist (shouldn't happen if the generator
        # ran at least once).
        return
    user_msg = state.messages[-2]
    asst_msg = state.messages[-1]
    if user_msg.role != "user" or asst_msg.role != "assistant":
        return

    now = int(time.time())
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, sources_json, created_at) "
            "VALUES (?, 'user', ?, NULL, ?)",
            (plan.conversation_id, user_msg.content, now),
        )
        asst_sources_json = (
            json.dumps(asst_msg.sources_for_ui, ensure_ascii=False)
            if asst_msg.sources_for_ui
            else None
        )
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, sources_json, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (plan.conversation_id, asst_msg.content, asst_sources_json, now),
        )
        last_sources_json = _retrieved_parents_to_json(state.last_sources)
        update_sql = (
            "UPDATE conversations SET updated_at = ?, turn_index = ?, "
            "last_sources_json = ?, last_search_query = ?"
        )
        params: list = [now, state.turn_index, last_sources_json, state.last_search_query]
        if plan.is_first_turn:
            update_sql += ", title = ?"
            params.append(_title_from_user_text(plan.user_text))
        update_sql += " WHERE id = ?"
        params.append(plan.conversation_id)
        conn.execute(update_sql, params)
        conn.commit()
    except Exception:
        logger.exception("persist_turn failed for conversation %s", plan.conversation_id)
    finally:
        conn.close()


def _title_from_user_text(text: str) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= TITLE_MAX_CHARS:
        return one_line or "新对话"
    return one_line[:TITLE_MAX_CHARS] + "…"


# ── streaming wrapper ───────────────────────────────────────────────────────


def wrap_stream_with_persistence(
    raw_stream: Iterator[str],
    session: ChatSession,
    plan: TurnPersistencePlan,
) -> Iterator[str]:
    """Pump `raw_stream`, then persist on completion.

    The inner `ChatSession._wrap_stream` already updates `session.state` in
    its own finally block; we just need to write that state to disk after it
    runs. By wrapping the stream once more, our finally fires after the
    inner one — so state is already finalized when we persist.
    """
    try:
        for chunk in raw_stream:
            yield chunk
    finally:
        persist_turn(plan, session)


# ── sweeper ────────────────────────────────────────────────────────────────


CONVERSATION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def sweep_once() -> tuple[int, int]:
    """Delete expired auth sessions and conversations idle > 30 days.

    Returns (conversations_deleted, auth_sessions_deleted).
    """
    now = int(time.time())
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM conversations WHERE updated_at < ?",
            (now - CONVERSATION_TTL_SECONDS,),
        )
        conv_deleted = cur.rowcount
        cur = conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at < ?", (now,)
        )
        sess_deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return conv_deleted, sess_deleted
