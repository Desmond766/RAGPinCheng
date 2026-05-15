"""One-shot index build: ingest → chunk → embed → index."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunk import chunk_all
from src.index import index_children, store_parents
from src.ingest import ingest_all


def main(force_parse: bool = False) -> None:
    print("=== Stage 1: MinerU parse ===")
    docs = ingest_all(force=force_parse)

    print("\n=== Stage 2: Parent-child chunking ===")
    parents, children = chunk_all(docs)
    print(f"Total: {len(parents)} parents, {len(children)} children")

    print("\n=== Stage 3: Parent store ===")
    store_parents(parents)

    print("\n=== Stage 4: Embed + Qdrant upsert ===")
    index_children(children)

    print("\nDone.")


if __name__ == "__main__":
    main(force_parse="--force-parse" in sys.argv)
