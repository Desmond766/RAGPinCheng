"""Build an index for a single PDF and chat with it in the terminal.

Usage:
    python scripts/test_single_doc.py "<path-to-pdf>"

Pipeline:
    1. Parse the PDF via MinerU (cloud if MINERU_API_KEY is set, else local CLI).
       Cached markdown in data/parsed/ is reused on re-runs.
    2. Chunk → store parents in SQLite → embed + upsert children to Qdrant.
       NOTE: this WIPES the existing Qdrant collection and parents.sqlite
       first — the index will contain ONLY this document afterwards. This is
       intentional for isolation testing. Use scripts/build_index.py for
       non-destructive incremental indexing.
    3. Open a REPL: type a question, get a GLM-4 answer with citations.
       Empty line or Ctrl-C exits.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunk import chunk_document
from src.config import DOCS_DIR, PARSED_DIR
from src.generate import generate
from src.index import index_children, reset_index, store_parents
from src.ingest import ParsedDoc, _cloud_parse, _local_parse, _safe_stem
from src.config import MINERU_API_KEY
from src.retrieve import retrieve


def _derive_metadata(pdf: Path) -> tuple[str, str, Path]:
    """Return (category, doc_title, markdown_cache_path) for a PDF.

    If the PDF lives under DOCS_DIR, category = first subfolder; otherwise
    category = 'uncategorized' and the cache key is just the filename stem.
    """
    try:
        rel = pdf.relative_to(DOCS_DIR)
        category = rel.parts[0] if len(rel.parts) > 1 else "uncategorized"
        stem = _safe_stem(pdf)
    except ValueError:
        category = "uncategorized"
        stem = pdf.stem
    return category, pdf.stem, PARSED_DIR / f"{stem}.md"


def build_index_for(pdf: Path) -> ParsedDoc:
    if not pdf.exists():
        raise FileNotFoundError(pdf)

    category, doc_title, md_cache = _derive_metadata(pdf)
    use_cloud = bool(MINERU_API_KEY)

    print("=" * 70)
    print(f"Document : {pdf.name}")
    print(f"Category : {category}")
    print(f"Parser   : {'MinerU cloud API' if use_cloud else 'MinerU local CLI'}")
    print("=" * 70)

    if md_cache.exists():
        print(f"\n[1/4] Parse — using cached markdown at {md_cache.name}")
    else:
        print("\n[1/4] Parse — calling MinerU ...")
        markdown = _cloud_parse(pdf) if use_cloud else _local_parse(pdf)
        md_cache.write_text(markdown, encoding="utf-8")
        print(f"      wrote {md_cache.name} ({len(markdown):,} chars)")

    doc = ParsedDoc(
        source_path=pdf,
        category=category,
        doc_title=doc_title,
        markdown_path=md_cache,
    )

    print("\n[2/4] Chunk ...")
    parents, children = chunk_document(doc)
    print(f"      {len(parents)} parents / {len(children)} children")

    print("\n[3/4] Store parents (SQLite) — wiping existing rows ...")
    reset_index()
    store_parents(parents)

    print("\n[4/4] Embed + Qdrant upsert ...")
    index_children(children)

    print("\nIndex ready.\n")
    return doc


def repl() -> None:
    print("=" * 70)
    print("Ask questions (empty line or Ctrl-C to exit)")
    print("=" * 70)
    while True:
        try:
            query = input("\n问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return
        if not query:
            print("bye.")
            return

        parents = retrieve(query)
        if not parents:
            print("\n[ANSWER] 资料中未找到相关内容。")
            continue

        answer = generate(query, parents)
        print("\n[ANSWER]")
        print(answer.text)
        print("\n[SOURCES]")
        for i, p in enumerate(answer.sources, 1):
            print(f"  {i}. [{p.doc_title}] §{p.section_path}  (score={p.score:.4f})")


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python scripts/test_single_doc.py "<path-to-pdf>"')
        sys.exit(2)
    pdf = Path(" ".join(sys.argv[1:])).resolve()
    build_index_for(pdf)
    repl()


if __name__ == "__main__":
    main()
