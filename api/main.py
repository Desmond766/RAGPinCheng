"""FastAPI app entry point.

Run with:  uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import RERANK_ENABLED

from .routes import router
from .session_store import store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring parents.sqlite forward to the current schema if it pre-dates a
    # recent column addition. Idempotent and cheap (a few PRAGMA queries).
    try:
        from src.index import _init_parents_db
        conn = _init_parents_db(reset=False)
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("parents.sqlite migration check failed (non-fatal)")

    # Warm heavy models on startup so the first request isn't slow.
    # Mirrors the @st.cache_resource warmups in app.py.
    if os.getenv("API_SKIP_WARMUP") != "1":
        logger.info("warming embed model (BGE-M3)...")
        from src.embed import get_model
        get_model()
        if RERANK_ENABLED:
            logger.info("warming reranker (BGE-reranker-v2-m3)...")
            from src.rerank import get_reranker
            get_reranker()
    await store.start_sweeper()
    logger.info("api ready")
    try:
        yield
    finally:
        await store.stop_sweeper()


app = FastAPI(title="PinCheng RAG API", version="0.1.0", lifespan=lifespan)

# CORS — Vite dev server on :5173 by default. Comma-separated overrides via env.
_default_origins = "http://localhost:5173,http://127.0.0.1:5173"
_origins = [
    o.strip() for o in os.getenv("API_CORS_ORIGINS", _default_origins).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
