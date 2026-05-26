"""Cross-encoder reranker (BGE-reranker-v2-m3) for second-stage scoring.

The hybrid retriever over-fetches candidates via RRF; this module re-scores
each (query, child_text) pair with a cross-encoder so the top-k handed to
the LLM reflects fine-grained relevance, not just lexical/semantic recall.

Model singleton uses double-checked locking — see `get_reranker`. First load
downloads weights (~600MB).
"""
from __future__ import annotations

import threading
from typing import Sequence

from FlagEmbedding import FlagReranker
import transformers

from .config import RERANKER_MODEL

# Silence the advisory "You're using a XLMRobertaTokenizerFast tokenizer..."
# message that transformers emits on every reranker batch. The hint targets
# FlagEmbedding's internal `prepare_for_model` + `pad` call pattern, which we
# can't change without forking FlagEmbedding. Keeping ERROR (not WARNING)
# still surfaces real problems (missing weights, dtype mismatches, etc.).
transformers.logging.set_verbosity_error()

_rerank_lock = threading.Lock()

# Double-checked locking around first-load — same pattern as src/embed.py.
# lru_cache is unsafe during cold load (concurrent first-callers can race
# into the constructor and corrupt MPS device placement).
_reranker: FlagReranker | None = None
_load_lock = threading.Lock()


def get_reranker() -> FlagReranker:
    global _reranker
    # Fast path: unlocked read; GIL makes module-attr reads atomic.
    if _reranker is not None:
        return _reranker
    # Slow path: only the first thread to acquire the lock builds the model.
    with _load_lock:
        if _reranker is None:
            _reranker = FlagReranker(RERANKER_MODEL, use_fp16=True)
        return _reranker


def rerank_scores(query: str, passages: Sequence[str]) -> list[float]:
    """Return one relevance score per passage. Higher = more relevant.

    Empty input returns []. Single-passage input still goes through the model
    so callers get a real score (used for thresholding, not just ordering).
    """
    if not passages:
        return []
    model = get_reranker()
    pairs = [[query, p] for p in passages]
    with _rerank_lock:
        raw = model.compute_score(pairs, normalize=True)
    # compute_score returns a float for len(pairs)==1, a list otherwise.
    if isinstance(raw, (int, float)):
        return [float(raw)]
    return [float(x) for x in raw]
