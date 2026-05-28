"""Table summarization at index time.

For each `content_type="table"` child produced by the chunker, generate a
short Chinese natural-language summary describing the table's subject and
key fields, then prepend it to the child's text + embed_text so dense /
sparse retrieval and the cross-encoder reranker see real keywords instead
of raw <td> soup.

The parent's stored text is NOT modified — the LLM still receives the raw
table at answer time (it reads HTML/markdown tables fine). The summary
only lives on the child (the retrieval-time representation).

Summaries are cached in parents.sqlite (table `table_summaries`) keyed by
sha256(original_table_text + doc_title + section_path), so re-running an
indexing job re-uses the prior summary at zero LLM cost.

Failure mode: if ZHIPU_API_KEY is missing or the LLM call raises, the
child is left untouched and indexing proceeds with the raw table text —
i.e. graceful degradation back to pre-summary behavior.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Callable

from openai import OpenAI

from .chunk import Child, _stable_id
from .config import (
    PARENTS_DB,
    TABLE_SUMMARY_ENABLED,
    TABLE_SUMMARY_MAX_CHARS,
    TABLE_SUMMARY_MIN_CHARS,
    TABLE_SUMMARY_MODEL,
    ZHIPU_API_KEY,
    ZHIPU_BASE_URL,
)
from .prompts import load_prompt, render_prompt

SUMMARY_MARKER = "【表格摘要】"

ProgressFn = Callable[[int, int], None]  # (done, total)


def _ensure_cache_table() -> sqlite3.Connection:
    """Open parents.sqlite and ensure the table_summaries cache exists.

    Lives in the same SQLite file as parents (deliberate — re-indexing a
    doc deletes its parents rows, but cached summaries stay across doc
    edits because they're keyed by table content, not source_path).
    """
    conn = sqlite3.connect(PARENTS_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS table_summaries (
            content_hash TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            model TEXT,
            created_at INTEGER
        )
        """
    )
    return conn


def _content_hash(table_text: str, doc_title: str, section_path: str) -> str:
    h = hashlib.sha256()
    h.update(doc_title.encode("utf-8"))
    h.update(b"\x00")
    h.update(section_path.encode("utf-8"))
    h.update(b"\x00")
    h.update(table_text.encode("utf-8"))
    return h.hexdigest()


def _client() -> OpenAI:
    if not ZHIPU_API_KEY:
        raise RuntimeError("ZHIPU_API_KEY is not set")
    return OpenAI(api_key=ZHIPU_API_KEY, base_url=ZHIPU_BASE_URL)


def _call_llm(client: OpenAI, doc_title: str, section_path: str, table_text: str) -> str:
    truncated = table_text
    if len(truncated) > TABLE_SUMMARY_MAX_CHARS:
        truncated = truncated[:TABLE_SUMMARY_MAX_CHARS] + "\n...(表格过长，已截断)"
    user_msg = render_prompt(
        "table_summary_user",
        doc_title=doc_title,
        section_path=section_path or "(无章节)",
        table=truncated,
    )
    resp = client.chat.completions.create(
        model=TABLE_SUMMARY_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": load_prompt("table_summary_system")},
            {"role": "user", "content": user_msg},
        ],
        extra_body={"thinking": {"type": "disabled"}},
    )
    text = (resp.choices[0].message.content or "").strip()
    # Strip stray wrapping quotes or leading "摘要：" the model might add.
    text = text.strip('"').strip("'").strip("“”‘’").strip()
    for prefix in ("摘要：", "摘要:", "表格摘要：", "表格摘要:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text


def summarize_table_children(
    children: list[Child],
    on_progress: ProgressFn | None = None,
) -> dict:
    """Mutate `children` in place: for each table child, prepend a generated
    summary to `text`, rebuild `embed_text`, and recompute `child_id` so the
    new content gets a new deterministic ID (re-embed on next upsert).

    Returns a stats dict: {"tables": N, "summarized": N, "cached": N,
    "skipped_short": N, "failed": N}.
    """
    stats = {
        "tables": 0,
        "summarized": 0,
        "cached": 0,
        "skipped_short": 0,
        "skipped_already": 0,
        "failed": 0,
    }

    if not TABLE_SUMMARY_ENABLED:
        return stats

    targets = [c for c in children if c.content_type == "table"]
    stats["tables"] = len(targets)
    if not targets:
        return stats

    conn = _ensure_cache_table()
    client: OpenAI | None = None
    api_failed = False  # latch — once API fails (e.g. no key), stop trying
    done = 0
    total = len(targets)

    try:
        for child in targets:
            done += 1
            if on_progress:
                on_progress(done, total)

            # Idempotent guard for re-runs that somehow already have a prefix.
            if child.text.lstrip().startswith(SUMMARY_MARKER):
                stats["skipped_already"] += 1
                continue

            if len(child.text) < TABLE_SUMMARY_MIN_CHARS:
                stats["skipped_short"] += 1
                continue

            original = child.text
            chash = _content_hash(original, child.doc_title, child.section_path)
            row = conn.execute(
                "SELECT summary FROM table_summaries WHERE content_hash = ?",
                (chash,),
            ).fetchone()

            summary: str | None = None
            if row:
                summary = row[0]
                stats["cached"] += 1
            elif not api_failed:
                if client is None:
                    try:
                        client = _client()
                    except Exception as exc:  # missing API key etc.
                        print(f"[table-summary] disabled: {exc}")
                        api_failed = True
                if client is not None:
                    try:
                        summary = _call_llm(
                            client,
                            child.doc_title,
                            child.section_path,
                            original,
                        )
                        if summary:
                            conn.execute(
                                "INSERT OR REPLACE INTO table_summaries "
                                "(content_hash, summary, model, created_at) "
                                "VALUES (?, ?, ?, strftime('%s','now'))",
                                (chash, summary, TABLE_SUMMARY_MODEL),
                            )
                            conn.commit()
                            stats["summarized"] += 1
                    except Exception as exc:
                        print(f"[table-summary] LLM call failed: {exc}")
                        stats["failed"] += 1

            if not summary:
                continue

            new_text = f"{SUMMARY_MARKER}{summary}\n\n{original}"
            header_prefix = f"{child.doc_title} > {child.section_path}\n\n"
            child.text = new_text
            child.embed_text = header_prefix + new_text
            # Recompute ID so the upsert sees this as a NEW vector and
            # re-embeds. Original IDs of the un-summarized table children
            # remain in Qdrant only if the doc was indexed before this
            # feature shipped — the admin pipeline's _purge_existing()
            # handles that, build_index.py users need --reset.
            child.child_id = _stable_id(child.parent_id, "table", new_text[:120])
    finally:
        conn.close()

    return stats
