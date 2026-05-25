"""Concurrency smoke test for the Qdrant server migration.

Fires N parallel `retrieve()` calls from a thread pool. Pre-migration this
crashes the second-and-later threads with a file-lock RuntimeError. Post-
migration all threads should return result lists with no exceptions.

Run: python scripts/smoke_concurrent_retrieve.py
"""
from __future__ import annotations

import concurrent.futures as cf
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.embed import get_model
from src.retrieve import retrieve

QUERIES = [
    "钢结构焊接质量要求",
    "梁柱节点构造",
    "高强度螺栓施工",
    "防火涂层厚度",
    "钢材力学性能",
]

N_WORKERS = 5


def _one(q: str) -> tuple[str, int, str | None]:
    try:
        hits = retrieve(q)
        return (q, len(hits), None)
    except Exception as exc:  # noqa: BLE001
        return (q, 0, f"{type(exc).__name__}: {exc}")


def main() -> int:
    # Pre-warm the embedding model once, single-threaded, before spawning
    # workers — lru_cache is not thread-safe during first load.
    print("Warming embedding model…")
    get_model()
    print("Model ready. Firing parallel queries…\n")

    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        results = list(ex.map(_one, QUERIES))
    dt = time.perf_counter() - t0

    failures = [(q, err) for q, _, err in results if err is not None]
    for q, n, err in results:
        status = "OK" if err is None else "FAIL"
        print(f"  [{status}] {q}: {n} hits" + (f" — {err}" if err else ""))
    print(f"\n{len(results) - len(failures)}/{len(results)} succeeded in {dt:.2f}s")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
