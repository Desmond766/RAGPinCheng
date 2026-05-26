"""Concurrency smoke test for the model first-load path.

Fires N parallel `get_model()` + `get_reranker()` calls from a thread pool
in a fresh process. The model and reranker are lazily constructed on first
use; pre-fix, racing into `lru_cache`-wrapped constructors on MPS produced
'Cannot copy out of meta tensor' crashes. Post-fix (double-checked locking
around module-level state), every thread receives the same singleton
instance and no exceptions are raised.

Run: python scripts/smoke_concurrent_cold_load.py
"""
from __future__ import annotations

import concurrent.futures as cf
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.embed import get_model
from src.rerank import get_reranker

N_WORKERS = 8


def _load_both(_: int) -> tuple[int, int, str | None]:
    try:
        m = get_model()
        r = get_reranker()
        return (id(m), id(r), None)
    except Exception as exc:  # noqa: BLE001
        return (0, 0, f"{type(exc).__name__}: {exc}")


def main() -> int:
    print(f"Firing {N_WORKERS} parallel get_model() + get_reranker() calls in a fresh process…")
    with cf.ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        results = list(ex.map(_load_both, range(N_WORKERS)))

    failures = [(i, err) for i, (_, _, err) in enumerate(results) if err is not None]
    model_ids = {mid for mid, _, _ in results if mid}
    reranker_ids = {rid for _, rid, _ in results if rid}

    for i, (mid, rid, err) in enumerate(results):
        if err:
            print(f"  [FAIL] worker {i}: {err}")
        else:
            print(f"  [OK]   worker {i}: model={mid:#x} reranker={rid:#x}")

    ok = (
        not failures
        and len(model_ids) == 1
        and len(reranker_ids) == 1
    )

    if not ok:
        if failures:
            print(f"\n{len(failures)}/{N_WORKERS} workers raised")
        if len(model_ids) > 1:
            print(f"singleton broken: got {len(model_ids)} distinct model instances")
        if len(reranker_ids) > 1:
            print(f"singleton broken: got {len(reranker_ids)} distinct reranker instances")
        return 1

    print(f"\nOK: all {N_WORKERS} workers received the same model + reranker singletons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
