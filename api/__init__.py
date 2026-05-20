"""FastAPI transport layer around the RAG ChatSession.

This package is a thin HTTP wrapper: all retrieval / generation / state
behavior lives in ``src/`` (most importantly ``src.session.ChatSession``).
See CLAUDE.md — duplicating ChatSession invariants in another caller is
how they drift, so the API only marshals requests in and events out.
"""
