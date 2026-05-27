"""Cross-cutting endpoints — health, config, categories, feedback,
LLM-health badge. Conversation / chat / auth / admin live in their own
routers and are mounted alongside this one in main.py.
"""
from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from src.config import (
    COLLECTION,
    EMBED_MODEL,
    LLM_MODEL,
    LLM_REWRITE_MODEL,
    RERANK_ENABLED,
    RERANKER_MODEL,
)
from src.index import collection_stats, list_categories, parents_count
from src.llm_health import check_llm, to_dict as llm_health_to_dict

from . import feedback as feedback_log
from .auth import CurrentUser, require_user
from .schemas import (
    CategoriesResponse,
    ConfigResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    LLMHealthResponse,
)

logger = logging.getLogger("api.routes")

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    stats = collection_stats()
    return HealthResponse(
        status="ok",
        children=int(stats.get("children", 0)),
        parents=parents_count(),
    )


# Cached LLM-health snapshot. Each probe issues two real chat-completion
# requests, so we don't want the frontend's 60s poll to fan that out further
# on every browser tab. TTL keeps the badge fresh enough to notice an outage
# within ~half a minute while bounding upstream load.
_LLM_HEALTH_TTL_S = 30.0
_llm_health_cache: dict | None = None
_llm_health_cached_at: float = 0.0
_llm_health_lock = asyncio.Lock()


@router.get("/llm_health", response_model=LLMHealthResponse)
async def llm_health(force: bool = Query(False, description="bypass the cache")) -> LLMHealthResponse:
    global _llm_health_cache, _llm_health_cached_at
    now = time.time()
    fresh = (
        not force
        and _llm_health_cache is not None
        and (now - _llm_health_cached_at) < _LLM_HEALTH_TTL_S
    )
    if fresh:
        return LLMHealthResponse(**{**_llm_health_cache, "cached": True})

    # Serialize concurrent probes so simultaneous requests share one upstream
    # round-trip instead of stampeding Zhipu when the cache is cold.
    async with _llm_health_lock:
        now = time.time()
        if (
            not force
            and _llm_health_cache is not None
            and (now - _llm_health_cached_at) < _LLM_HEALTH_TTL_S
        ):
            return LLMHealthResponse(**{**_llm_health_cache, "cached": True})
        snapshot = await asyncio.to_thread(check_llm)
        _llm_health_cache = llm_health_to_dict(snapshot)
        _llm_health_cached_at = time.time()
    return LLMHealthResponse(**{**_llm_health_cache, "cached": False})


@router.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    return ConfigResponse(
        embed_model=EMBED_MODEL,
        reranker_model=RERANKER_MODEL,
        rerank_enabled=RERANK_ENABLED,
        llm_model=LLM_MODEL,
        llm_rewrite_model=LLM_REWRITE_MODEL,
        collection=COLLECTION,
    )


@router.post("/feedback", response_model=FeedbackResponse)
def post_feedback(
    body: FeedbackRequest,
    _user: CurrentUser = Depends(require_user),
) -> FeedbackResponse:
    if body.kind not in ("answer", "citation"):
        raise HTTPException(status_code=400, detail="kind must be 'answer' or 'citation'")
    if body.kind == "answer" and body.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="answer feedback requires rating 'up' or 'down'")
    feedback_log.append(body)
    return FeedbackResponse(ok=True)


@router.get("/categories", response_model=CategoriesResponse)
def get_categories() -> CategoriesResponse:
    return CategoriesResponse(categories=list_categories())
