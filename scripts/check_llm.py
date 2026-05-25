"""CLI probe for the Zhipu LLM endpoint.

Usage:
    python scripts/check_llm.py

Pings both LLM_MODEL and LLM_REWRITE_MODEL with a one-token request, prints a
per-model summary with latency, and exits non-zero if anything is unhealthy so
this can drop into a healthcheck / cron line.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm_health import check_llm, masked_key  # noqa: E402


def _fmt_latency(ms: int | None) -> str:
    if ms is None:
        return "   —  "
    return f"{ms:>5} ms"


def main() -> int:
    h = check_llm()
    print(f"endpoint : {h.base_url}")
    print(f"api key  : {masked_key()}")
    print(f"checked  : {int(h.checked_at)}")
    print()
    print(f"{'model':<24} {'role':<11} {'latency':>10}  status")
    print("-" * 70)
    for m in h.models:
        status = "OK" if m.ok else f"FAIL  ({m.error})"
        print(f"{m.model:<24} {m.role:<11} {_fmt_latency(m.latency_ms):>10}  {status}")
    print()
    print("overall  :", "OK" if h.ok else "DEGRADED")
    return 0 if h.ok else 1


if __name__ == "__main__":
    sys.exit(main())
