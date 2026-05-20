"""Hybrid retrieval: dense + sparse → RRF → top-k children → expand to parents.

Uses Qdrant's native query_points prefetch + FusionQuery(RRF).
"""
from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from .config import COLLECTION, DENSE_TOP_K, FINAL_TOP_K, QDRANT_DIR, SPARSE_TOP_K
from .embed import encode_one
from .index import fetch_parents


@dataclass
class RetrievedParent:
    parent_id: str
    doc_title: str
    category: str
    section_path: str
    source_path: str
    text: str
    score: float
    matched_children: list[str]
    doc_type: str = "pdf"
    start_time: str | None = None


def retrieve(query: str, top_k: int = FINAL_TOP_K) -> list[RetrievedParent]:
    emb = encode_one(query)
    client = QdrantClient(path=str(QDRANT_DIR))
    try:
        result = client.query_points(
            collection_name=COLLECTION,
            prefetch=[
                models.Prefetch(query=emb.dense, using="dense", limit=DENSE_TOP_K),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=emb.sparse_indices, values=emb.sparse_values
                    ),
                    using="sparse",
                    limit=SPARSE_TOP_K,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k * 4,  # over-fetch children, dedupe to parents below
            with_payload=True,
        )
    finally:
        client.close()

    # Dedupe children by parent_id, keeping the best score per parent
    parent_order: list[str] = []
    parent_score: dict[str, float] = {}
    parent_children: dict[str, list[str]] = {}
    for point in result.points:
        pid = point.payload["parent_id"]
        if pid not in parent_score:
            parent_score[pid] = point.score
            parent_order.append(pid)
            parent_children[pid] = []
        snippet = point.payload["text"][:120].replace("\n", " ")
        parent_children[pid].append(snippet)
        if len(parent_order) >= top_k:
            break

    parents = fetch_parents(parent_order)
    out: list[RetrievedParent] = []
    for pid in parent_order:
        p = parents.get(pid)
        if not p:
            continue
        out.append(
            RetrievedParent(
                parent_id=pid,
                doc_title=p["doc_title"],
                category=p["category"],
                section_path=p["section_path"],
                source_path=p["source_path"],
                text=p["text"],
                score=parent_score[pid],
                matched_children=parent_children[pid],
                doc_type=p.get("doc_type") or "pdf",
                start_time=p.get("start_time"),
            )
        )
    return out
