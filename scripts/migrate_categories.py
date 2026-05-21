"""Surgical category fixups for the Qdrant payload — no re-embedding.

Use this when the on-disk taxonomy under `docs/` has been renamed but the
existing Qdrant points still carry the old category label. Touches payload
only; vectors are left as-is.

Currently performs:
  - `transcriptions` → `教学视频` (folder was renamed; source_path patched too)

Other stale categories (e.g. `uncategorized` from earlier builds) are
reported but NOT modified — re-run with `--drop-uncategorized` to remove
them.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient, models

from src.config import COLLECTION, QDRANT_DIR as QDRANT_PATH


CATEGORY_RENAMES: dict[str, str] = {
    "transcriptions": "教学视频",
}


def _count_categories(client: QdrantClient) -> Counter:
    cats: Counter = Counter()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            limit=4000,
            offset=offset,
            with_payload=["category"],
            with_vectors=False,
        )
        for p in points:
            cats[p.payload.get("category")] += 1
        if offset is None:
            break
    return cats


def _rename_category(client: QdrantClient, old: str, new: str) -> int:
    flt = models.Filter(
        must=[models.FieldCondition(key="category", match=models.MatchValue(value=old))]
    )
    # Count first so we can report.
    before, _ = client.scroll(
        collection_name=COLLECTION,
        scroll_filter=flt,
        limit=1,
        with_payload=False,
        with_vectors=False,
    )
    if not before:
        return 0
    # Also patch source_path on transcripts that reference the old folder
    # name. We do this point-by-point so each row gets its own corrected
    # path; payload `set_payload` with a single value would clobber all of
    # them to the same path.
    n = 0
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=flt,
            limit=512,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break
        for p in points:
            patch: dict = {"category": new}
            sp = p.payload.get("source_path")
            if isinstance(sp, str) and f"/docs/{old}/" in sp:
                patch["source_path"] = sp.replace(f"/docs/{old}/", f"/docs/{new}/")
            client.set_payload(
                collection_name=COLLECTION,
                payload=patch,
                points=[p.id],
            )
            n += 1
        if offset is None:
            break
    return n


def _drop_category(client: QdrantClient, value: str) -> int:
    flt = models.Filter(
        must=[models.FieldCondition(key="category", match=models.MatchValue(value=value))]
    )
    # Use the same scroll → delete-by-id pattern; delete_points with filter
    # works too but this gives us an accurate count.
    n = 0
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=flt,
            limit=512,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        if not points:
            break
        client.delete(
            collection_name=COLLECTION,
            points_selector=models.PointIdsList(points=[p.id for p in points]),
        )
        n += len(points)
        if offset is None:
            break
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drop-uncategorized",
        action="store_true",
        help="Also delete points with category='uncategorized'.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print current category histogram, make no changes.",
    )
    args = parser.parse_args()

    qdrant_path = Path(QDRANT_PATH)
    if not qdrant_path.exists():
        raise SystemExit(f"Qdrant path not found: {qdrant_path}")

    client = QdrantClient(path=str(qdrant_path))
    try:
        print(f"[migrate] collection={COLLECTION}")
        before = _count_categories(client)
        print(f"[migrate] before: {dict(before)}")

        if args.dry_run:
            return

        for old, new in CATEGORY_RENAMES.items():
            if before.get(old, 0) == 0:
                print(f"[migrate] skip rename {old!r} — none present")
                continue
            n = _rename_category(client, old, new)
            print(f"[migrate] renamed {n} points: {old!r} → {new!r}")

        if args.drop_uncategorized and before.get("uncategorized", 0) > 0:
            n = _drop_category(client, "uncategorized")
            print(f"[migrate] dropped {n} points with category='uncategorized'")

        after = _count_categories(client)
        print(f"[migrate] after:  {dict(after)}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
