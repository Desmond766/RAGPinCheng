"""LLM endpoint health probe.

Shared by `scripts/check_llm.py` (CLI) and `api/routes.py` (HTTP). Issues a
tiny chat-completion against each configured Zhipu model and reports per-model
reachability + latency. Used to surface upstream outages (Zhipu's flashx/air
endpoints occasionally hang while glm-4.6 stays healthy) in the UI instead of
letting them manifest as cryptic 16-second retries inside `ChatSession.ask`.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Literal

import httpx
from openai import APIError, APITimeoutError, OpenAI

from .config import LLM_MODEL, LLM_REWRITE_MODEL, ZHIPU_API_KEY, ZHIPU_BASE_URL

# Probe-call timeout in seconds. Set close to the user-experience cliff: under
# this, generation feels acceptable; over it, users start asking "is it stuck?".
# Zhipu's flashx tier routinely returns first byte in 9–12s at peak hours, so
# anything below ~12s here will false-alarm. 15s gives ~3s headroom.
PROBE_TIMEOUT_S = 15.0

Role = Literal["generation", "rewrite"]


@dataclass
class ModelHealth:
    model: str
    role: Role
    ok: bool
    latency_ms: int | None
    error: str | None


@dataclass
class LLMHealth:
    ok: bool                 # all models healthy
    key_present: bool
    base_url: str
    checked_at: float        # unix timestamp
    models: list[ModelHealth]


def _probe_one(model: str, role: Role) -> ModelHealth:
    """Single-shot probe. Bypasses the openai SDK retry loop so we see the
    real first-attempt latency instead of waiting 2x for a retry."""
    if not ZHIPU_API_KEY:
        return ModelHealth(
            model=model, role=role, ok=False,
            latency_ms=None, error="ZHIPU_API_KEY not set",
        )
    client = OpenAI(
        api_key=ZHIPU_API_KEY,
        base_url=ZHIPU_BASE_URL,
        timeout=httpx.Timeout(PROBE_TIMEOUT_S, connect=PROBE_TIMEOUT_S),
        max_retries=0,
    )
    t0 = time.perf_counter()
    try:
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
        latency = int((time.perf_counter() - t0) * 1000)
        return ModelHealth(model=model, role=role, ok=True, latency_ms=latency, error=None)
    except APITimeoutError:
        latency = int((time.perf_counter() - t0) * 1000)
        return ModelHealth(
            model=model, role=role, ok=False, latency_ms=latency,
            error=f"timeout after {latency} ms",
        )
    except APIError as exc:
        latency = int((time.perf_counter() - t0) * 1000)
        status = getattr(exc, "status_code", None)
        msg = f"HTTP {status}" if status else exc.__class__.__name__
        return ModelHealth(model=model, role=role, ok=False, latency_ms=latency, error=msg)
    except Exception as exc:  # noqa: BLE001 — surface anything else verbatim
        latency = int((time.perf_counter() - t0) * 1000)
        return ModelHealth(
            model=model, role=role, ok=False, latency_ms=latency,
            error=f"{exc.__class__.__name__}: {exc}",
        )


def check_llm() -> LLMHealth:
    """Probe every configured LLM model in parallel. Deduplicates models so
    we don't double-probe when LLM_MODEL == LLM_REWRITE_MODEL."""
    targets: list[tuple[str, Role]] = [(LLM_MODEL, "generation")]
    if LLM_REWRITE_MODEL and LLM_REWRITE_MODEL != LLM_MODEL:
        targets.append((LLM_REWRITE_MODEL, "rewrite"))

    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        results = list(ex.map(lambda t: _probe_one(*t), targets))

    return LLMHealth(
        ok=all(r.ok for r in results),
        key_present=bool(ZHIPU_API_KEY),
        base_url=ZHIPU_BASE_URL,
        checked_at=time.time(),
        models=results,
    )


def masked_key() -> str:
    """Render the key as e.g. `4e60…0a19e` for safe display in CLI/API output."""
    if not ZHIPU_API_KEY:
        return "(unset)"
    k = ZHIPU_API_KEY
    if len(k) <= 8:
        return "…" + k[-2:]
    return f"{k[:4]}…{k[-5:]}"


def to_dict(h: LLMHealth) -> dict:
    return {
        "ok": h.ok,
        "key_present": h.key_present,
        "key_masked": masked_key(),
        "base_url": h.base_url,
        "checked_at": h.checked_at,
        "models": [asdict(m) for m in h.models],
    }
