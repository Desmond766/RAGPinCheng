"""Hybrid retrieval: dense + sparse → RRF → (optional code-boost) → rerank →
top-k children → expand to parents.

Pipeline per call:
  1. RRF-fuse three prefetches in Qdrant's native query_points:
       - dense (semantic),
       - sparse (lexical),
       - code-filter sparse (only when the query mentions a standard code like
         "GB 50017" — restricts to children whose `text` literally contains
         the code, full-text-indexed payload).
     Over-fetches RERANK_TOP_K children.
  2. Optional category filter (Qdrant payload index on `category`).
  3. Cross-encoder rerank (BGE-reranker-v2-m3) over the over-fetched children
     using the full child text.
  4. Dedupe by parent_id (best-reranked child wins), expand to parents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from qdrant_client import QdrantClient, models

from .config import (
    CODE_BOOST_TOP_K,
    COLLECTION,
    DENSE_TOP_K,
    FINAL_TOP_K,
    QDRANT_DIR,
    RERANK_ENABLED,
    RERANK_TOP_K,
    SPARSE_TOP_K,
)
from .embed import encode_one
from .index import _ensure_payload_indexes, fetch_parents
from .rerank import rerank_scores


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


# Matches Chinese standard codes: GB / GB/T / JGJ / JGJ/T / CECS / YB / JG /
# TB / DB / DBJ (case-insensitive), optional separator, then the number
# (e.g. "50017-2017" or "16.3-2019"). Half-width and full-width slashes both.
_CODE_RE = re.compile(
    r"\b(GB(?:[/／]T)?|JGJ(?:[/／]T)?|CECS|YB|JG|TB|DB|DBJ)\s*[-—/／\s]?\s*"
    r"(\d{2,5}(?:[-．\.]\d+)*)",
    re.IGNORECASE,
)


def _extract_code_variants(query: str) -> list[str]:
    """Find standard-code identifiers in the query and return literal variants.

    For each detected code we emit the no-space form ("GB50017"), the spaced
    form ("GB 50017"), and the hyphenated form when a suffix is present
    ("GB 50017-2017"). These are what we pass to Qdrant MatchText so the
    code-boost prefetch hits chunks regardless of how the original document
    typeset the code.
    """
    variants: list[str] = []
    seen: set[str] = set()
    for m in _CODE_RE.finditer(query):
        prefix = m.group(1).upper().replace("／", "/")
        number = m.group(2).replace("．", ".")
        candidates = [f"{prefix}{number}", f"{prefix} {number}"]
        if "-" in number or "." in number:
            head = re.split(r"[-\.]", number, 1)[0]
            candidates.append(f"{prefix} {head}")
            candidates.append(f"{prefix}{head}")
        for c in candidates:
            if c not in seen:
                seen.add(c)
                variants.append(c)
    return variants


@lru_cache(maxsize=1)
def _bootstrap_indexes() -> bool:
    """Ensure payload indexes exist on the live collection.

    Cached so we only pay the round-trip once per process — `create_payload_index`
    is idempotent server-side but the call still costs a file-lock open.
    """
    client = QdrantClient(path=str(QDRANT_DIR))
    try:
        if client.collection_exists(COLLECTION):
            _ensure_payload_indexes(client)
    finally:
        client.close()
    return True


def _category_filter(categories: list[str] | None) -> models.Filter | None:
    if not categories:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(
                key="category", match=models.MatchAny(any=list(categories))
            )
        ]
    )


def _code_filter(code_variants: list[str]) -> models.Filter | None:
    if not code_variants:
        return None
    return models.Filter(
        should=[
            models.FieldCondition(key="text", match=models.MatchText(text=v))
            for v in code_variants
        ]
    )


def _merge_filters(*filters: models.Filter | None) -> models.Filter | None:
    """AND together multiple filters. Each input's `should` clause becomes a
    nested filter under `must` so its OR semantics survive the merge."""
    parts = [f for f in filters if f is not None]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    must: list = []
    for f in parts:
        if f.must:
            must.extend(f.must)
        if f.should:
            must.append(models.Filter(should=list(f.should)))
    return models.Filter(must=must) if must else None


def retrieve(
    query: str,
    top_k: int = FINAL_TOP_K,
    categories: list[str] | None = None,
) -> list[RetrievedParent]:
    _bootstrap_indexes()
    emb = encode_one(query)
    code_variants = _extract_code_variants(query)

    cat_filter = _category_filter(categories)
    code_filter = _code_filter(code_variants)

    prefetch = [
        models.Prefetch(
            query=emb.dense,
            using="dense",
            limit=DENSE_TOP_K,
            filter=cat_filter,
        ),
        models.Prefetch(
            query=models.SparseVector(
                indices=emb.sparse_indices, values=emb.sparse_values
            ),
            using="sparse",
            limit=SPARSE_TOP_K,
            filter=cat_filter,
        ),
    ]
    if code_filter is not None:
        prefetch.append(
            models.Prefetch(
                query=models.SparseVector(
                    indices=emb.sparse_indices, values=emb.sparse_values
                ),
                using="sparse",
                limit=CODE_BOOST_TOP_K,
                filter=_merge_filters(cat_filter, code_filter),
            )
        )

    client = QdrantClient(path=str(QDRANT_DIR))
    try:
        result = client.query_points(
            collection_name=COLLECTION,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=RERANK_TOP_K,
            with_payload=True,
        )
    finally:
        client.close()

    points = list(result.points)
    if not points:
        return []

    # Cross-encoder rerank on full child text.
    if RERANK_ENABLED:
        passages = [p.payload["text"] for p in points]
        ce_scores = rerank_scores(query, passages)
        scored = sorted(
            zip(points, ce_scores), key=lambda x: x[1], reverse=True
        )
    else:
        scored = [(p, p.score) for p in points]

    # Dedupe children by parent_id, keeping the best score per parent.
    parent_order: list[str] = []
    parent_score: dict[str, float] = {}
    parent_children: dict[str, list[str]] = {}
    for point, score in scored:
        pid = point.payload["parent_id"]
        snippet = point.payload["text"][:120].replace("\n", " ")
        if pid in parent_score:
            parent_children[pid].append(snippet)
            continue
        if len(parent_order) >= top_k:
            # Cap reached: don't admit a new parent, but keep scanning so
            # already-accepted parents can still gather child snippets above.
            continue
        parent_score[pid] = float(score)
        parent_order.append(pid)
        parent_children[pid] = [snippet]
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
