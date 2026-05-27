"""Append-only JSONL feedback log.

Each line is one feedback record (👍/👎 on an answer, or a wrong-citation
report). Stored at data/feedback.jsonl for cheap offline review.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .schemas import FeedbackRequest

logger = logging.getLogger("api.feedback")

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDBACK_PATH = REPO_ROOT / "data" / "feedback.jsonl"

_lock = threading.Lock()


def _backfill_query_and_answer(record: dict) -> None:
    """Fill in `query` (and `answer_text` if missing) from `data/app.sqlite`.

    Frontend always sends `query` for freshly-sent turns; but if a client
    omits it (e.g. an older build, or a citation report from a context that
    doesn't carry the user message), look it up from the persisted
    conversation so the feedback log is always self-describing.
    """
    cid = record.get("conversation_id")
    if not cid:
        return
    if record.get("query") and record.get("answer_text"):
        return

    # Resolve the offending assistant row. Prefer the integer `message_id`
    # the SPA passes through; fall back to the turn index if needed.
    msg_id_raw = record.get("message_id")
    msg_id: int | None = None
    if isinstance(msg_id_raw, int):
        msg_id = msg_id_raw
    elif isinstance(msg_id_raw, str) and msg_id_raw.isdigit():
        msg_id = int(msg_id_raw)
    # Otherwise it's a frontend-only random hex id — we'll fall through to
    # the turn-index path.

    try:
        from .db import connect
    except Exception:
        return

    conn = connect()
    try:
        assistant_row = None
        if msg_id is not None:
            assistant_row = conn.execute(
                "SELECT id, role, content FROM messages "
                "WHERE conversation_id = ? AND id = ?",
                (cid, msg_id),
            ).fetchone()
        if assistant_row is None:
            turn_index = record.get("turn_index")
            if isinstance(turn_index, int) and turn_index > 0:
                # Assistant turn N is the (2N)th message (1-indexed), id-ordered.
                rows = conn.execute(
                    "SELECT id, role, content FROM messages "
                    "WHERE conversation_id = ? ORDER BY id ASC",
                    (cid,),
                ).fetchall()
                idx = (turn_index * 2) - 1  # zero-based slot for assistant
                if 0 <= idx < len(rows) and rows[idx]["role"] == "assistant":
                    assistant_row = rows[idx]
        if assistant_row is None:
            return

        # The user question is the most-recent user row with id < assistant.id.
        user_row = conn.execute(
            "SELECT content FROM messages "
            "WHERE conversation_id = ? AND id < ? AND role = 'user' "
            "ORDER BY id DESC LIMIT 1",
            (cid, assistant_row["id"]),
        ).fetchone()
        if user_row is not None and not record.get("query"):
            record["query"] = user_row["content"]
        if not record.get("answer_text"):
            record["answer_text"] = assistant_row["content"]
    except Exception:
        logger.exception("feedback backfill failed (non-fatal)")
    finally:
        conn.close()


def append(req: FeedbackRequest) -> None:
    record = req.model_dump(exclude_none=True)
    _backfill_query_and_answer(record)
    record["ts"] = datetime.now(timezone.utc).isoformat()
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
