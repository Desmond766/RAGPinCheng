"""Incremental index build: ingest → chunk → embed → upsert.

By default this is **non-destructive**:
  - Already-parsed PDFs in data/parsed/ are reused (cached markdown).
  - Already-indexed chunks are overwritten in place via deterministic UUIDv5
    IDs (same content → same id → upsert), so re-running adds new docs
    without duplicating existing ones.

Flags:
  --force-parse   re-parse PDFs even if their markdown is cached.
  --reset         drop the Qdrant collection and wipe parents.sqlite before
                  building (full rebuild from scratch).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunk import chunk_all
from src.index import index_children, reset_index, store_parents
from src.ingest import ingest_all
from src.table_summary import summarize_table_children


def main(force_parse: bool = False, reset: bool = False) -> None:
    if reset:
        print("=== Stage 0: Reset existing index ===")
        reset_index()

    print("\n=== Stage 1: MinerU parse ===")
    docs = ingest_all(force=force_parse)

    print("\n=== Stage 2: Parent-child chunking ===")
    parents, children = chunk_all(docs)
    print(f"Total: {len(parents)} parents, {len(children)} children")

    print("\n=== Stage 3: Table summarization (retrieval-time keywords) ===")
    summary_stats = summarize_table_children(children)
    print(f"[table-summary] {summary_stats}")

    print("\n=== Stage 4: Parent store (upsert) ===")
    store_parents(parents)

    print("\n=== Stage 5: Embed + Qdrant upsert ===")
    index_children(children)

    print("\nDone.")


if __name__ == "__main__":
    main(
        force_parse="--force-parse" in sys.argv,
        reset="--reset" in sys.argv,
    )
