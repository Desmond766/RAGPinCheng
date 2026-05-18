"""Retrieval-only smoke test (no LLM call).

Usage:
    python scripts/test_retrieve.py "<question>"
    python scripts/test_retrieve.py            # runs a default set of probes

Prints the top-k retrieved parent sections with their score, section path,
and a text preview — so you can verify the index/embeddings work without
needing a Zhipu API key.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.retrieve import retrieve

DEFAULT_QUERIES = [
    "冷弯薄壁型钢受压构件的容许长细比限值是多少？",
    "Q235钢的设计强度",
    "型钢截面特性如何计算",
    "本规范适用于哪些建筑物",
]


def run(query: str) -> None:
    print("=" * 70)
    print(f"QUERY: {query}")
    print("=" * 70)
    parents = retrieve(query)
    if not parents:
        print("(no chunks retrieved)\n")
        return
    for i, p in enumerate(parents, 1):
        print(f"\n#{i}  score={p.score:.4f}  [{p.doc_title}] §{p.section_path}")
        print(f"     category: {p.category}")
        preview = p.text.replace("\n", " ")[:240]
        print(f"     preview: {preview}{'...' if len(p.text) > 240 else ''}")
        if p.matched_children:
            print(f"     matched children: {len(p.matched_children)}")
    print()


def main() -> None:
    queries = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else DEFAULT_QUERIES
    for q in queries:
        run(q)


if __name__ == "__main__":
    main()
