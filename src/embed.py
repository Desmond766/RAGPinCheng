"""BGE-M3 wrapper that yields dense + sparse vectors in one call.

Sparse output is converted to Qdrant's (indices, values) format.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

from FlagEmbedding import BGEM3FlagModel

from .config import EMBED_BATCH, EMBED_MODEL


@dataclass
class Embedding:
    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


@lru_cache(maxsize=1)
def get_model() -> BGEM3FlagModel:
    # use_fp16 reduces memory; falls back gracefully on CPU
    return BGEM3FlagModel(EMBED_MODEL, use_fp16=True)


def _to_sparse_pair(weights: dict) -> tuple[list[int], list[float]]:
    if not weights:
        return [], []
    indices = [int(k) for k in weights.keys()]
    values = [float(v) for v in weights.values()]
    return indices, values


def encode(texts: Sequence[str]) -> list[Embedding]:
    model = get_model()
    out = model.encode(
        list(texts),
        batch_size=EMBED_BATCH,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense_vecs = out["dense_vecs"]
    sparse_vecs = out["lexical_weights"]
    results: list[Embedding] = []
    for i in range(len(texts)):
        idx, val = _to_sparse_pair(sparse_vecs[i])
        results.append(
            Embedding(
                dense=[float(x) for x in dense_vecs[i].tolist()],
                sparse_indices=idx,
                sparse_values=val,
            )
        )
    return results


def encode_one(text: str) -> Embedding:
    return encode([text])[0]
