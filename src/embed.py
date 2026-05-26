"""BGE-M3 wrapper that yields dense + sparse vectors in one call.

Sparse output is converted to Qdrant's (indices, values) format.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Sequence

from FlagEmbedding import BGEM3FlagModel

from .config import EMBED_BATCH, EMBED_MODEL


@dataclass
class Embedding:
    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


def _pick_device() -> str:
    """Prefer CUDA, then Apple MPS, else CPU. fp16 is unsupported on MPS — the
    caller checks `_use_fp16()` separately."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


_encode_lock = threading.Lock()

# Double-checked locking around first-load. lru_cache is NOT safe here:
# during the slow constructor call, multiple threads can enter simultaneously
# and corrupt MPS device placement (the "Cannot copy out of meta tensor" race).
# Module-level state + a load-only lock gives a single-pointer-read hot path
# and serializes only the cold path. See `_load_lock` design notes in the
# concurrency section of CLAUDE.md.
_model: BGEM3FlagModel | None = None
_load_lock = threading.Lock()


def get_model() -> BGEM3FlagModel:
    global _model
    # Fast path: unlocked read. CPython makes module-attribute reads atomic
    # under the GIL, so a reader sees either `None` or the fully-constructed
    # instance — never a half-built object.
    if _model is not None:
        return _model
    # Slow path: serialize first-load. Re-check inside the lock so only the
    # first thread to acquire it actually builds the model.
    with _load_lock:
        if _model is None:
            device = _pick_device()
            # fp16 is fine on CUDA, broken on MPS (silent NaNs), wasteful on CPU.
            use_fp16 = device == "cuda"
            print(f"[embed] loading {EMBED_MODEL} on device={device} fp16={use_fp16}")
            _model = BGEM3FlagModel(EMBED_MODEL, use_fp16=use_fp16, devices=device)
        return _model


def _to_sparse_pair(weights: dict) -> tuple[list[int], list[float]]:
    if not weights:
        return [], []
    indices = [int(k) for k in weights.keys()]
    values = [float(v) for v in weights.values()]
    return indices, values


def encode(texts: Sequence[str]) -> list[Embedding]:
    model = get_model()
    with _encode_lock:
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
