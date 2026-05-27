"""Application database — users, auth sessions, conversations, messages.

Lives in `data/app.sqlite`, on the same Docker volume as `parents.sqlite`
but completely independent from it. The RAG corpus and the app state must
NOT share a file: `scripts/build_index.py --reset` would otherwise wipe
user data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DB_PATH = REPO_ROOT / "data" / "app.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id   TEXT NOT NULL UNIQUE,
    real_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    last_login_at INTEGER
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at
    ON auth_sessions (expires_at);

CREATE TABLE IF NOT EXISTS conversations (
    id                TEXT PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,
    turn_index        INTEGER NOT NULL DEFAULT 0,
    last_sources_json TEXT,
    last_search_query TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
    ON conversations (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
    ON conversations (updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    sources_json    TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
    ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS index_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    filename      TEXT NOT NULL,
    category      TEXT NOT NULL,
    doc_type      TEXT NOT NULL,            -- 'pdf' | 'transcript'
    source_path   TEXT NOT NULL,            -- absolute path on disk under docs/
    file_size     INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
                                            -- pending | parsing | chunking | embedding | done | failed
    error         TEXT,
    stats_json    TEXT,                     -- {"parents":N,"children":N} once done
    created_at    INTEGER NOT NULL,
    started_at    INTEGER,
    finished_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_index_jobs_status_created
    ON index_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_index_jobs_created_desc
    ON index_jobs (created_at DESC);
"""


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # Foreign keys are off by default in SQLite — the cascades in the schema
    # above are no-ops without this.
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL gives us readers-during-writers, which matters as soon as the
    # sweeper runs concurrently with a chat turn.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


def connect() -> sqlite3.Connection:
    """Open a connection with the standard pragmas applied.

    Caller owns the lifecycle (close it when done). For request handlers,
    use `get_db` as a FastAPI dependency instead.
    """
    APP_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(APP_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


def init_db() -> None:
    """Create tables and indexes if missing. Idempotent; safe on every boot."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency. Yields a connection scoped to the request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
