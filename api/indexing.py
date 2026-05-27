"""Index-job queue + background worker.

One asyncio.Queue, one worker task. Jobs run FIFO, one at a time. The
"one at a time" rule is load-bearing: BGE-M3 inference + parents.sqlite
writes serialize cleanly when only one job is in flight, but become a
contention nightmare under parallel work. The worker holds no DB
connection between iterations — it opens a short-lived one per status
update so reads from other handlers aren't blocked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from src.indexing_pipeline import index_single

from .db import connect

logger = logging.getLogger("api.indexing")

_queue: asyncio.Queue[int] = asyncio.Queue()
_worker_task: asyncio.Task | None = None


# ── job row helpers ────────────────────────────────────────────────────────


def create_job(
    user_id: int,
    filename: str,
    category: str,
    doc_type: str,
    source_path: Path,
    file_size: int,
) -> int:
    """Insert a pending job row, return its id."""
    now = int(time.time())
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO index_jobs (user_id, filename, category, doc_type, "
            "source_path, file_size, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (user_id, filename, category, doc_type, str(source_path), file_size, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _update_status(job_id: int, **fields) -> None:
    """Patch a job row with arbitrary fields."""
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [job_id]
    conn = connect()
    try:
        conn.execute(f"UPDATE index_jobs SET {cols} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def enqueue(job_id: int) -> None:
    """Add a pending job to the queue. Safe to call from sync code."""
    # asyncio.Queue.put_nowait is the right tool here — we're not awaiting.
    # If called from a sync context with no running loop, the queue is still
    # process-global so the worker (which IS in the loop) will pick it up.
    _queue.put_nowait(job_id)


def queue_depth() -> int:
    """Approximate # of pending jobs waiting in the queue."""
    return _queue.qsize()


# ── worker loop ────────────────────────────────────────────────────────────


async def _run_one(job_id: int) -> None:
    """Run a single job to completion, persisting status as it progresses."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT source_path, doc_type, status FROM index_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        logger.warning("worker: job %s not found, skipping", job_id)
        return
    # Skip if a retry handler ran ahead of us or the job was deleted-then-re-enqueued.
    if row["status"] not in ("pending",):
        logger.info("worker: job %s status=%s, skipping", job_id, row["status"])
        return

    source_path = Path(row["source_path"])
    doc_type = row["doc_type"]
    # `.md` files (transcript or regular document) skip the MinerU upload/queue
    # phases entirely — they're already markdown on disk. Start at "chunking"
    # so the badge doesn't briefly flash a misleading "uploading" state.
    initial_status = "uploading" if source_path.suffix.lower() == ".pdf" else "chunking"
    _update_status(job_id, status=initial_status, started_at=int(time.time()), error=None)

    loop = asyncio.get_running_loop()

    def on_status(stage: str) -> None:
        # Status callbacks fire from the worker thread; bounce DB write
        # through to the event loop via run_coroutine_threadsafe isn't
        # needed because _update_status uses its own SQLite connection
        # (check_same_thread=False).
        _update_status(job_id, status=stage)

    try:
        # Run the CPU-bound pipeline in a worker thread so the event loop
        # (and other admin requests) stay responsive while embedding runs.
        result = await loop.run_in_executor(
            None,
            lambda: index_single(source_path, doc_type, on_status),
        )
        _update_status(
            job_id,
            status="done",
            finished_at=int(time.time()),
            stats_json=json.dumps(
                {"parents": result.parents, "children": result.children},
                ensure_ascii=False,
            ),
            error=None,
        )
        logger.info(
            "indexed %s: %d parents, %d children",
            source_path.name, result.parents, result.children,
        )
    except Exception as exc:  # noqa: BLE001 — propagate via DB, not crash worker
        logger.exception("indexing job %s failed", job_id)
        _update_status(
            job_id,
            status="failed",
            finished_at=int(time.time()),
            error=str(exc)[:2000],
        )


async def _worker_loop() -> None:
    logger.info("indexing worker started")
    while True:
        try:
            job_id = await _queue.get()
        except asyncio.CancelledError:
            break
        try:
            await _run_one(job_id)
        except Exception:
            # _run_one already catches and persists; this is defense in depth.
            logger.exception("worker iteration crashed; continuing")
        finally:
            _queue.task_done()


async def start_worker() -> None:
    """Idempotent: start the worker task if not already running."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop())


async def stop_worker() -> None:
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except (asyncio.CancelledError, Exception):
        pass
    _worker_task = None


def resume_pending_on_boot() -> None:
    """Re-enqueue any jobs left in pending/parsing/chunking/embedding from a
    prior process. Mid-flight jobs become zombies on restart; we mark them
    failed (with a restart note) so the admin can retry rather than having
    them sit in a non-pending state forever.
    """
    conn = connect()
    try:
        # Anything that wasn't `done` or `failed` was in-flight; flip it to
        # failed so the admin sees a clear "needs retry" signal.
        rows = conn.execute(
            "SELECT id, status FROM index_jobs WHERE status NOT IN ('done', 'failed')"
        ).fetchall()
        now = int(time.time())
        for r in rows:
            if r["status"] == "pending":
                # Truly never started — safe to re-queue.
                _queue.put_nowait(int(r["id"]))
            else:
                conn.execute(
                    "UPDATE index_jobs SET status='failed', error=?, finished_at=? "
                    "WHERE id = ?",
                    ("后端重启时该任务正在运行，已中止 — 请重试", now, int(r["id"])),
                )
        conn.commit()
    finally:
        conn.close()
