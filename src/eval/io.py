"""JSONL load/save for EvalItem records."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .types import EvalItem


def load_jsonl(path: Path) -> list[EvalItem]:
    items: list[EvalItem] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            items.append(EvalItem.from_dict(json.loads(line)))
    return items


def save_jsonl(path: Path, items: Iterable[EvalItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
