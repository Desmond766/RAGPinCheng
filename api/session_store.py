"""In-memory session registry with per-session locks and idle eviction.

V1: sessions live only in this process. State is lost on restart; that's
acceptable for the first iteration (see plan). A background sweeper runs
inside the FastAPI lifespan and evicts sessions whose last_accessed is
older than ``SESSION_TTL_SECONDS``.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from time import monotonic

from src.session import ChatSession

# Idle timeout for in-memory sessions. Two hours strikes a balance for a
# small internal tool — long enough that lunch breaks don't kill a chat,
# short enough that abandoned tabs don't pile up.
SESSION_TTL_SECONDS = 2 * 60 * 60
SWEEPER_INTERVAL_SECONDS = 5 * 60


@dataclass
class SessionEntry:
    session: ChatSession
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_accessed: float = field(default_factory=monotonic)

    def touch(self) -> None:
        self.last_accessed = monotonic()


class SessionStore:
    def __init__(self) -> None:
        self._entries: dict[str, SessionEntry] = {}
        self._sweeper_task: asyncio.Task[None] | None = None

    def create(self) -> str:
        sid = uuid.uuid4().hex
        self._entries[sid] = SessionEntry(session=ChatSession())
        return sid

    def get(self, session_id: str) -> SessionEntry | None:
        entry = self._entries.get(session_id)
        if entry is not None:
            entry.touch()
        return entry

    def delete(self, session_id: str) -> bool:
        return self._entries.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._entries)

    async def start_sweeper(self) -> None:
        if self._sweeper_task is not None:
            return
        self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop_sweeper(self) -> None:
        if self._sweeper_task is None:
            return
        self._sweeper_task.cancel()
        try:
            await self._sweeper_task
        except (asyncio.CancelledError, Exception):
            pass
        self._sweeper_task = None

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(SWEEPER_INTERVAL_SECONDS)
                self._sweep_once()
            except asyncio.CancelledError:
                break
            except Exception:
                # Sweeper must never crash the app; swallow and continue.
                continue

    def _sweep_once(self) -> None:
        cutoff = monotonic() - SESSION_TTL_SECONDS
        stale = [
            sid for sid, entry in self._entries.items()
            if entry.last_accessed < cutoff and not entry.lock.locked()
        ]
        for sid in stale:
            self._entries.pop(sid, None)


store = SessionStore()
