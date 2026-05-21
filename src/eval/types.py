"""Typed records used by the eval harness.

`EvalItem` is the on-disk schema for both `drafts.jsonl` (pre-review) and
`golden.jsonl` (curated). One line of JSONL = one EvalItem.

Grading is `parent_id` set-based (A:1 choice): a retrieval is "correct" iff
at least one of the parent_ids returned by the system is in
`expected_parent_ids`. `source_parent_id` is the parent the synthesizer
derived the question from — kept around so reviewers can broaden the gold
set during curation if the question turns out answerable from multiple
parents.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal[
    "factual",        # single-parent factual lookup
    "table_formula",  # parent contains a table or $$...$$ block
    "code_lookup",    # mentions a standard code like "GB 50017"
    "transcript",     # transcript parent, expects @HH:MM:SS citation
    "multi_turn",     # 2-turn conversation; turn-1 + turn-2
    "no_answer",      # outside the corpus; must return 资料中未找到相关内容。
]


@dataclass
class EvalItem:
    id: str
    kind: Kind
    question: str
    expected_parent_ids: list[str] = field(default_factory=list)
    # Provenance / debug fields — informational, not used for grading.
    doc_type: str = ""           # "pdf" | "transcript" | "" for no_answer
    category: str = ""           # original parent category
    source_parent_id: str = ""   # parent the synthesizer was shown
    # Optional fields for special kinds.
    followup_question: str = ""  # multi_turn turn-2
    expected_substrings: list[str] = field(default_factory=list)
    # Reviewer notes — free-text, never graded against.
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop empties so JSONL stays readable.
        return {k: v for k, v in d.items() if v not in ("", [], None)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvalItem":
        return cls(
            id=d["id"],
            kind=d["kind"],
            question=d["question"],
            expected_parent_ids=list(d.get("expected_parent_ids", [])),
            doc_type=d.get("doc_type", ""),
            category=d.get("category", ""),
            source_parent_id=d.get("source_parent_id", ""),
            followup_question=d.get("followup_question", ""),
            expected_substrings=list(d.get("expected_substrings", [])),
            notes=d.get("notes", ""),
        )
