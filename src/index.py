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


def _ensure_collection(client: QdrantClient) -> None:
    if client.collection_exists(COLLECTION):
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


def _init_parents_db() -> sqlite3.Connection:
    conn = sqlite3.connect(PARENTS_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS parents (
            parent_id TEXT PRIMARY KEY,
            doc_title TEXT,
            category TEXT,
            section_path TEXT,
            source_path TEXT,
            text TEXT
        )
        """
    )
    conn.execute("DELETE FROM parents")
    return conn


def store_parents(parents: Iterable[Parent]) -> None:
    conn = _init_parents_db()
    rows = [
        (p.parent_id, p.doc_title, p.category, p.section_path, p.source_path, p.text)
        for p in parents
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO parents VALUES (?,?,?,?,?,?)", rows
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
        f"SELECT parent_id, doc_title, category, section_path, source_path, text "
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
        }
        for r in rows
    }


def index_children(children: list[Child]) -> None:
    client = _client()
    _ensure_collection(client)

    for start in tqdm(range(0, len(children), EMBED_BATCH), desc="embed+upsert"):
        batch = children[start : start + EMBED_BATCH]
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
                    },
                )
            )
        client.upsert(collection_name=COLLECTION, points=points)
    client.close()
    print(f"[qdrant] indexed {len(children)} children into '{COLLECTION}'")


def collection_stats() -> dict:
    client = _client()
    try:
        if not client.collection_exists(COLLECTION):
            return {"children": 0}
        info = client.get_collection(COLLECTION)
        return {"children": info.points_count or 0}
    finally:
        client.close()


def parents_count() -> int:
    if not PARENTS_DB.exists():
        return 0
    conn = sqlite3.connect(PARENTS_DB)
    n = conn.execute("SELECT COUNT(*) FROM parents").fetchone()[0]
    conn.close()
    return int(n)
