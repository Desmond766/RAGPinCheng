"""Append-only JSONL feedback log.

Each line is one feedback record (👍/👎 on an answer, or a wrong-citation
report). Stored at data/feedback.jsonl for cheap offline review.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .schemas import FeedbackRequest

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDBACK_PATH = REPO_ROOT / "data" / "feedback.jsonl"

_lock = threading.Lock()


def append(req: FeedbackRequest) -> None:
    record = req.model_dump(exclude_none=True)
    record["ts"] = datetime.now(timezone.utc).isoformat()
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
