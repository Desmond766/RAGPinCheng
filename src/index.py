"""Qdrant collection (dense + sparse named vectors) + parents.sqlite store.

Qdrant in local file mode (no server). Parents go to sqlite keyed by parent_id.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from qdrant_client import QdrantClient, models
from tqdm import tqdm

from .chunk import Child, Parent
from .config import COLLECTION, EMBED_BATCH, EMBED_DIM, PARENTS_DB, QDRANT_DIR
from .embed import encode


def _client() -> QdrantClient:
    return QdrantClient(path=str(QDRANT_DIR))


def _ensure_collection(client: QdrantClient, reset: bool = False) -> bool:
    """Create the Qdrant collection if missing. If reset=True, drop first.

    Returns True when the collection was just (re)created — callers can use
    this to skip the existing-id probe when the collection is known to be
    empty (saves N round-trips on first-time indexing).

    Also ensures payload indexes used by the retriever:
      - `category` (keyword)  — fast equality filter for category scoping.
      - `text`     (full-text) — enables MatchText for the code-boost prefetch.
    Indexes are created idempotently; failure to create (e.g. already exists)
    is swallowed so this stays a no-op on warm runs.
    """
    if client.collection_exists(COLLECTION):
        if not reset:
            _ensure_payload_indexes(client)
            return False
        client.delete_collection(COLLECTION)
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "dense": models.VectorParams(size=EMBED_DIM, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(),
        },
    )
    _ensure_payload_indexes(client)
    return True


def _ensure_payload_indexes(client: QdrantClient) -> None:
    try:
        client.create_payload_index(
            collection_name=COLLECTION,
            field_name="category",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass
    try:
        client.create_payload_index(
            collection_name=COLLECTION,
            field_name="text",
            field_schema=models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.MULTILINGUAL,
                min_token_len=2,
                max_token_len=20,
                lowercase=True,
            ),
        )
    except Exception:
        pass


def _init_parents_db(reset: bool = False) -> sqlite3.Connection:
    """Open parents.sqlite and ensure schema. If reset=True, wipe all rows.

    Schema is migrated forward on open: missing columns are added in place so
    incremental builds work after a code update without requiring --reset.
    """
    conn = sqlite3.connect(PARENTS_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS parents (
            parent_id TEXT PRIMARY KEY,
            doc_title TEXT,
            category TEXT,
            section_path TEXT,
            source_path TEXT,
            text TEXT,
            doc_type TEXT,
            start_time TEXT,
            company TEXT
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(parents)").fetchall()}
    if "doc_type" not in existing:
        conn.execute("ALTER TABLE parents ADD COLUMN doc_type TEXT")
    if "start_time" not in existing:
        conn.execute("ALTER TABLE parents ADD COLUMN start_time TEXT")
    if "company" not in existing:
        conn.execute("ALTER TABLE parents ADD COLUMN company TEXT")
    if reset:
        conn.execute("DELETE FROM parents")
    return conn


def reset_index() -> None:
    """Drop the Qdrant collection and wipe parents.sqlite. Use before a full rebuild."""
    client = _client()
    try:
        if client.collection_exists(COLLECTION):
            client.delete_collection(COLLECTION)
    finally:
        client.close()
    conn = _init_parents_db(reset=True)
    conn.commit()
    conn.close()
    print("[reset] dropped Qdrant collection and cleared parents.sqlite")


def store_parents(parents: Iterable[Parent], reset: bool = False) -> None:
    """Insert/replace parents. With reset=True, wipes the table first."""
    conn = _init_parents_db(reset=reset)
    rows = [
        (
            p.parent_id,
            p.doc_title,
            p.category,
            p.section_path,
            p.source_path,
            p.text,
            p.doc_type,
            p.start_time,
            p.company,
        )
        for p in parents
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO parents "
        "(parent_id, doc_title, category, section_path, source_path, text, doc_type, start_time, company) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    print(f"[parents] wrote {len(rows)} rows to {PARENTS_DB}")


def fetch_parents(parent_ids: list[str]) -> dict[str, dict]:
    if not parent_ids:
        return {}
    conn = sqlite3.connect(PARENTS_DB)
    placeholders = ",".join("?" * len(parent_ids))
    rows = conn.execute(
        f"SELECT parent_id, doc_title, category, section_path, source_path, text, "
        f"doc_type, start_time, company "
        f"FROM parents WHERE parent_id IN ({placeholders})",
        parent_ids,
    ).fetchall()
    conn.close()
    return {
        r[0]: {
            "parent_id": r[0],
            "doc_title": r[1],
            "category": r[2],
            "section_path": r[3],
            "source_path": r[4],
            "text": r[5],
            "doc_type": r[6] or "pdf",
            "start_time": r[7],
            "company": r[8],
        }
        for r in rows
    }


def index_children(children: list[Child], reset: bool = False) -> None:
    """Embed and upsert children into Qdrant. With reset=True, drops the
    collection first; otherwise upserts (deterministic IDs mean re-running
    the same doc overwrites in place rather than duplicating).

    Skip-existing: when not resetting, we query Qdrant for which `child_id`s
    are already present and only embed the remainder. Since IDs are
    deterministic UUIDv5 over content, an existing ID guarantees its vector
    was computed from the same `embed_text` we'd produce now — re-embedding
    would be wasted work. Edits to source content produce a NEW id, so this
    skip never masks stale data; it only suppresses redundant re-embeds.
    """
    client = _client()
    just_created = _ensure_collection(client, reset=reset)

    to_index: list[Child] = children
    # Skip the existing-id probe when the collection is known-empty
    # (reset, or just created). Otherwise check Qdrant for which child_ids
    # are already present so we don't re-embed them.
    if not reset and not just_created and children:
        all_ids = [c.child_id for c in children]
        existing: set[str] = set()
        # qdrant-client's retrieve() tolerates large id lists; chunk anyway
        # to stay polite on local file mode.
        CHUNK = 256
        for i in range(0, len(all_ids), CHUNK):
            recs = client.retrieve(
                collection_name=COLLECTION,
                ids=all_ids[i : i + CHUNK],
                with_payload=False,
                with_vectors=False,
            )
            existing.update(str(r.id) for r in recs)
        to_index = [c for c in children if c.child_id not in existing]
        skipped = len(children) - len(to_index)
        if skipped:
            print(f"[qdrant] skipping {skipped} already-indexed children")

    if not to_index:
        client.close()
        print(f"[qdrant] nothing new to index for '{COLLECTION}'")
        return

    for start in tqdm(range(0, len(to_index), EMBED_BATCH), desc="embed+upsert"):
        batch = to_index[start : start + EMBED_BATCH]
        embs = encode([c.embed_text for c in batch])
        points = []
        for c, e in zip(batch, embs):
            points.append(
                models.PointStruct(
                    id=c.child_id,
                    vector={
                        "dense": e.dense,
                        "sparse": models.SparseVector(
                            indices=e.sparse_indices, values=e.sparse_values
                        ),
                    },
                    payload={
                        "parent_id": c.parent_id,
                        "doc_title": c.doc_title,
                        "category": c.category,
                        "section_path": c.section_path,
                        "source_path": c.source_path,
                        "content_type": c.content_type,
                        "text": c.text,
                        "doc_type": c.doc_type,
                        "start_time": c.start_time,
                        "company": c.company,
                    },
                )
            )
        client.upsert(collection_name=COLLECTION, points=points)
    client.close()
    print(f"[qdrant] indexed {len(to_index)} new children into '{COLLECTION}'")


def collection_stats() -> dict:
    client = _client()
    try:
        if not client.collection_exists(COLLECTION):
            return {"children": 0}
        info = client.get_collection(COLLECTION)
        return {"children": info.points_count or 0}
    finally:
        client.close()


def list_categories() -> list[str]:
    """Distinct categories present in the parents store, sorted alphabetically."""
    if not PARENTS_DB.exists():
        return []
    conn = sqlite3.connect(PARENTS_DB)
    rows = conn.execute(
        "SELECT DISTINCT category FROM parents WHERE category IS NOT NULL "
        "ORDER BY category"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


def parents_count() -> int:
    if not PARENTS_DB.exists():
        return 0
    conn = sqlite3.connect(PARENTS_DB)
    n = conn.execute("SELECT COUNT(*) FROM parents").fetchone()[0]
    conn.close()
    return int(n)
