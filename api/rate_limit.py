"""Per-user token-bucket rate limiter for the chat endpoint.

In-process only — we have one backend container so process-local state is
sufficient. If we ever horizontally scale, move this to Redis with the
same interface.

Why token bucket: it lets a user burst (e.g. type two follow-ups in quick
succession) without rejecting them, while still capping sustained load.
The single BGE-M3 embedder + reranker on `_encode_lock` is the real
bottleneck we're protecting; one user spamming should not lock everyone
else out.

Tuning:
  - CHAT_RATE_LIMIT_PER_MIN  — sustained turns/min per user (default 20)
  - CHAT_RATE_LIMIT_BURST    — bucket capacity (default = per-min value,
                               so a user can spend a full minute's budget
                               instantly then refill at the steady rate)
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


RATE_PER_MIN = _env_int("CHAT_RATE_LIMIT_PER_MIN", 20)
BURST = _env_int("CHAT_RATE_LIMIT_BURST", RATE_PER_MIN)
_REFILL_PER_SEC = RATE_PER_MIN / 60.0


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, capacity: int = BURST, refill_per_sec: float = _REFILL_PER_SEC):
        self.capacity = float(capacity)
        self.refill_per_sec = refill_per_sec
        self._buckets: dict[int, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def try_acquire(self, key: int, cost: float = 1.0) -> tuple[bool, float]:
        """Attempt to spend `cost` tokens for `key`.

        Returns (allowed, retry_after_seconds). retry_after is 0 on success.
        """
        async with self._lock:
            now = time.monotonic()
            b = self._buckets.get(key)
            if b is None:
                b = _Bucket(tokens=self.capacity, updated=now)
                self._buckets[key] = b
            else:
                elapsed = now - b.updated
                b.tokens = min(self.capacity, b.tokens + elapsed * self.refill_per_sec)
                b.updated = now
            if b.tokens >= cost:
                b.tokens -= cost
                return True, 0.0
            deficit = cost - b.tokens
            retry = deficit / self.refill_per_sec if self.refill_per_sec else 60.0
            return False, retry


chat_limiter = RateLimiter()
