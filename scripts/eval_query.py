"""CLI used by the testing subagent: python scripts/eval_query.py "<question>"
Prints the GLM-4 answer and the cited sources (doc_title + section_path).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generate import generate
from src.retrieve import retrieve


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python scripts/eval_query.py "<question>"')
        sys.exit(2)
    query = " ".join(sys.argv[1:])

    parents = retrieve(query)
    if not parents:
        print("[RETRIEVAL] no chunks returned")
        print("[ANSWER] 资料中未找到相关内容。")
        return

    answer = generate(query, parents)

    print("=" * 60)
    print(f"QUERY: {query}")
    print("=" * 60)
    print("\n[ANSWER]")
    print(answer.text)
    print("\n[SOURCES]")
    for i, p in enumerate(answer.sources, 1):
        print(f"  {i}. [{p.doc_title}] §{p.section_path}  (score={p.score:.4f})")


if __name__ == "__main__":
    main()
