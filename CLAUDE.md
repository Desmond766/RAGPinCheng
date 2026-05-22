# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Chinese-language enterprise knowledge base ("品成 BIM 知识库") for an internal BIM consultancy. The company's main business is producing Revit / CAD models and drawings for clients, plus developing asset-management software. This RAG unifies the company's accumulated assets — **industry standards & codes, customer requirements, internal standards, past project deliverables / retrospectives, and training-video transcripts** — so staff can quickly look up standards, look up prior project experience, and onboard new hires. The product is a general BIM knowledge base, **not a steel-structure-specific assistant**; early test docs happened to be steel-structure codes but the corpus is intentionally broad.

PDFs in `docs/` are parsed to markdown, chunked, embedded with BGE-M3, indexed in a local Qdrant store with parent text in SQLite, retrieved via a dense+sparse RRF hybrid plus a BGE-reranker-v2-m3 cross-encoder pass, and answered by Zhipu GLM-4 with citations. A `ChatSession` layer orchestrates multi-turn conversations (rewrite → retrieve → merge → generate, both sync and streaming). Two frontends consume it: a Streamlit app (`app.py`) and a FastAPI (`api/`) + React/Vite (`frontend/`) web UI that streams answers over SSE.

## Commands

All commands run from the repo root, with the project venv (`.venv`) activated.

- **Install dependencies**: `pip install -r requirements.txt`
- **Run the Streamlit UI**: `streamlit run app.py`
- **Run the FastAPI backend** (serves the React frontend): `uvicorn api.main:app --reload --port 8000`. Lifespan warms BGE-M3 + the reranker on startup (set `API_SKIP_WARMUP=1` to skip), runs a one-shot `parents.sqlite` schema check, and starts a session sweeper. CORS allows `http://localhost:5173` by default; override with comma-separated `API_CORS_ORIGINS`.
- **Run the React frontend** (Vite dev server on :5173): `cd frontend && npm install && npm run dev`. Production build: `npm run build`. Talks to the FastAPI backend over `/api/*`.
- **Maintenance: rename / drop stale category labels in Qdrant payload** (no re-embedding): `python scripts/migrate_categories.py [--dry-run] [--drop-uncategorized]`. Currently renames `transcriptions` → `教学视频` and patches `source_path` accordingly.
- **Build / extend the index** (parse PDFs → chunk → embed → upsert): `python scripts/build_index.py`
  - **Incremental by default** — re-running with new PDFs in `docs/` keeps existing indexed content (deterministic UUIDv5 IDs mean same content overwrites in place, new content is appended).
  - `--force-parse` re-parses PDFs even if cached markdown exists in `data/parsed/`.
  - `--reset` drops the Qdrant collection and wipes `parents.sqlite` before building (full rebuild from scratch — use after changing chunking or embedding logic).
- **Single-doc isolation test**: `python scripts/test_single_doc.py "<path-to-pdf>"` — wipes Qdrant + parents.sqlite, indexes only that PDF, drops into a REPL. Intentionally destructive; use `build_index.py` for non-destructive incremental indexing.
- **Retrieval smoke test** (no LLM, no API key needed): `python scripts/test_retrieve.py "<question>"` (or no arg for default probes).
- **Interactive RAG agent with full debug output** (requires `ZHIPU_API_KEY`): `python scripts/eval_query.py ["<seed question>"]`. Drops into a REPL after the optional seed turn; slash commands: `/reset`, `/history`, `/verbose N`, `/full`, `/short`, `/exit`. Routes through the same `ChatSession` as the Streamlit app, so eval reproduces UI behavior exactly.
- **Eval harness** (golden-set regression for retrieval + no-answer compliance):
  - Sample parents for synthesis: `python scripts/sample_for_eval.py [--seed N] [--factual N --table-formula N ...]`. Writes `src/eval/sampled_parents.json`.
  - Synthesize Q/A drafts: spawn an Agent (Claude Code subagent, no API key) with `src/eval/sampled_parents.json` and per-kind generation rules → it writes `src/eval/drafts.jsonl`. User then hand-reviews into `src/eval/golden.jsonl`.
  - Append hand-written items (12 multi-turn pairs + 6 no-answer): `python scripts/append_handwritten.py` (idempotent by id; edit the `PAIRS` / `NO_ANSWER` literals in the script to extend).
  - Run baseline: `python scripts/run_eval_retrieval.py` (requires `ZHIPU_API_KEY`). Solo retrieval-graded items call `retrieve()` directly (no LLM); multi-turn pairs and no_answer items go through full `ChatSession.ask()`. Prints Recall@1/@5 + MRR@5 by kind; writes per-item log to `src/eval/runs/run_<ts>.jsonl`.
  - Diff two runs: `python scripts/diff_eval_runs.py <baseline.jsonl> <candidate.jsonl>` — surfaces fixed / regressed items and rank shifts.

No test suite, lint, or typecheck is configured.

## Required environment (.env)

- `ZHIPU_API_KEY` — Zhipu GLM (OpenAI-compatible at `https://open.bigmodel.cn/api/paas/v4/`). Required for generation, not for retrieval.
- `MINERU_API_KEY` — if set, `src/ingest.py` uses the MinerU cloud API (fast, ~1 min/PDF). If unset, falls back to local `mineru` CLI (slow, CPU-only).
- `LLM_MODEL` — overrides the default Zhipu model (default `glm-4.6`, see `src/config.py`).

## Architecture

The pipeline has four data stages owned by `src/` modules, plus a session orchestration layer that sits on top for multi-turn conversations. Code uses dataclasses to pass typed records between stages — read the dataclass at the top of each file to understand its contract.

1. **Ingest** (`src/ingest.py`): walks `docs/<category>/*.pdf` and produces `data/parsed/<safe_stem>.md`. Cloud path = presign → PUT upload → submit task → poll → download (zip or .md). The folder under `docs/` becomes the `category` metadata. Markdown is cached; re-runs skip already-parsed PDFs unless `--force`.
   - **Video transcripts** (`docs/transcriptions/MinerU_markdown_文字记录：*.md`) are picked up here too via `iter_transcripts()`. They are *already* markdown (MinerU-exported) — no parse pass needed; the file is consumed directly. `doc_title` is read from the first `**文字记录：<title>**` line of the file. `智能纪要：` summary files in the same folder are skipped on purpose (decision: index transcripts only so every video citation carries a timestamp). Each transcript ParsedDoc carries `doc_type="transcript"` and `category="transcriptions"`.

2. **Chunk** (`src/chunk.py`): parent-child chunking on the markdown. Branches on `ParsedDoc.doc_type`:
   - **Transcripts** (`doc_type="transcript"`) go through `chunk_transcript`: `TRANSCRIPT_TURN_RE` splits the file by `说话人 N HH:MM:SS` markers. Each speaker turn becomes one atomic child carrying its own `start_time`; consecutive turns are greedy-packed into parents up to `PARENT_SIZE`. The parent inherits the **first** child's timestamp, which is what gets rendered in citations. No header-splitter, no table/formula detection — transcripts are flat prose.
   - **PDFs** (`doc_type="pdf"`) use the original header-anchored path described below.

   Both Parent and Child dataclasses carry `doc_type` and an optional `start_time`. PDFs leave `start_time=None`.
   - `MarkdownHeaderTextSplitter` (#/##/###) → header-anchored sections form parents. Oversized sections are further split by `RecursiveCharacterTextSplitter` (PARENT_SIZE=1200 chars).
   - Children (CHILD_SIZE=256) are produced **per parent**, but tables (HTML `<table>` blocks, pipe `| ... |` tables) and `$$ ... $$` formula blocks are detected by `_find_protected_spans` / `_split_protected` and emitted as **atomic children** — never split mid-row or mid-LaTeX. Each child carries a `content_type` of `prose | table | formula`.
   - **Table-atomicity overrides PARENT_SIZE**: HTML tables up to `ATOMIC_TABLE_MAX = 2 * PARENT_SIZE` stay in a single parent even when they overflow the regular parent budget. Larger HTML tables are row-split by `_split_table_with_header`, which **prepends the original first `<tr>` (header row) to every fragment** so each chunk still carries its column labels.
   - Each child's `embed_text` prepends `doc_title > section_path` so embeddings see the heading context.
   - IDs are deterministic UUIDv5 (see `_stable_id`). `parent_id` hashes the **full parent text**, not a prefix — header-propagated table fragments share their first ~80 chars (the column row) and would otherwise collide.

3. **Index** (`src/index.py`): two stores.
   - **Qdrant** (local file mode at `data/qdrant/`, no server) holds children with two named vectors: `dense` (1024-d cosine) and `sparse`. `_ensure_collection` creates the collection only if missing; the collection is dropped only when `index_children(..., reset=True)` is passed (or via `reset_index()`). Default path is upsert, relying on deterministic IDs to overwrite same-content points in place.
   - **SQLite** at `data/parents.sqlite` holds full parent text keyed by `parent_id`. `store_parents` uses `INSERT OR REPLACE`; only wiped when `reset=True`.
   - Embeddings come from `src/embed.py` — a `BGEM3FlagModel` (BGE-M3) that returns dense + lexical-sparse in one pass. The model is `lru_cache`d; first load downloads weights.

4. **Retrieve + Rerank + Generate** (`src/retrieve.py`, `src/rerank.py`, `src/generate.py`):
   - `retrieve(query, categories=None)` runs Qdrant's native `query_points` with up to three `Prefetch`es fused by `FusionQuery(RRF)`: dense (semantic), sparse (lexical), and — only when the query mentions a standard code like `GB 50017` — a sparse **code-boost** prefetch restricted to children whose `text` literally contains the code (via the full-text payload index). Over-fetches `RERANK_TOP_K` children, then optionally applies a Qdrant `category` equality filter.
   - **Cross-encoder rerank** (`src/rerank.py`): BGE-reranker-v2-m3 re-scores each `(query, child_text)` pair so the top-k handed to the LLM reflects fine-grained relevance, not just RRF order. Disable with `RERANK_ENABLED=False` to fall back to RRF. The reranker is also used by `retrieve_for_turn` to rescore carry-forward parents so their scores live on the same scale as fresh hits.
   - After rerank, children are deduped by `parent_id` (best-reranked child wins) and parents are expanded from SQLite into `RetrievedParent` dataclasses (carry the cross-encoder `score`, the underlying `rrf_score`, and matched child snippets).
   - `generate(query, parents, history, budget)` packs parents into `<source>` blocks up to the per-turn `budget` (chars). The block shape branches on `doc_type`: PDFs render as `<source id=… doc=… section=… type="pdf">`, transcripts as `<source id=… doc=… time="HH:MM:SS" type="transcript">`. History turns are interleaved as native chat messages, then Zhipu is called via the `openai` SDK. The system prompt forces Chinese, dual citation formats (`[doc §section]` for PDFs, `[doc @HH:MM:SS]` for transcripts), and "资料中未找到相关内容。" on miss — do not weaken these rules without intent.
   - `stream_generate(...)` returns `(GenerationPrep, Iterator[str])` for token streaming. The prep object carries the source list / messages / budget so the UI can render headers before the first token arrives.
   - `rewrite_query(history, question)` does one cheap LLM call to rewrite a follow-up question into a standalone one using recent chat history, so retrieval works across turns. Returns `question` unchanged on empty history or any error (best-effort, never raises).

5. **Session orchestration** (`src/session.py`): `ChatSession.ask(query, categories=None)` drives the 5-stage per-turn pipeline: **① rewrite → ② retrieve fresh → ③ merge with carry-forward → ④ generate → ⑤ update state**. `ask_stream(query, categories=None)` is the streaming variant — returns `(StreamingTurnPrep, Iterator[str])`; after the generator exhausts (or is closed), `self.last_turn_result` holds the full `TurnResult`. Both `app.py`, `scripts/eval_query.py`, and the FastAPI `chat` endpoint go through this — never call `retrieve()` / `generate()` directly from UI/CLI code, route through `ChatSession`.
   - **Carry-forward**: `retrieve_for_turn` appends the top `CARRY_SOURCES` (=2) parents from the previous turn to the fresh retrieval, deduped by `parent_id`, **rescored against the current query** via the cross-encoder so they sort on the same scale. When `categories` is set, carry-forward parents outside the filter are dropped — otherwise a category switch (e.g. 行业规范 → 教学视频) would leak last turn's off-category sources in.
   - **Dynamic context budget**: `budget = MAX_CONTEXT_CHARS - history_chars - RESERVE_CHARS` (RESERVE_CHARS=700). History grows turn-by-turn; sources are the elastic component that shrinks to fit.
   - **History window**: only the last `HISTORY_TURNS` (=4) user/assistant pairs are fed to `generate()`. The full message log lives in `SessionState.messages` for replay, but the LLM only sees the recent window.
   - **Channel separation** (load-bearing invariant): the conversation channel (`SessionState.messages`) is text only — `<source>` blocks are **never** stored in assistant messages. The knowledge channel (`SessionState.last_sources`, typed `RetrievedParent` objects) carries sources for the next turn's carry-forward and for the UI. The two never mix inside the message list sent to the LLM. If you change how sources are surfaced, preserve this split — leaking source XML into stored assistant content will pollute future-turn history.
   - `TurnResult` bundles the answer with telemetry (rewrite applied, fresh/final source counts, history chars, budget, per-stage timings) for debug panels and the eval CLI.

6. **Eval module** (`src/eval/`): typed records + retrieval-graded golden set living next to the code.
   - `types.py` — `EvalItem` dataclass with `kind ∈ {factual, table_formula, code_lookup, transcript, multi_turn, no_answer}`. Grading is parent-id set-based (`expected_parent_ids`); no LLM judge, deterministic.
   - `sample.py` — weighted sampling from `parents.sqlite` by kind. Buckets are disjoint by `parent_id`; allocation order is transcript → table_formula → code_lookup → factual so most-restrictive pools don't starve. Deterministic given `--seed`.
   - `metrics.py` — Recall@k, MRR computed on retrieved parent_id sequences.
   - `io.py` — JSONL load/save for `EvalItem`.
   - `golden.jsonl` — curated set; `drafts.jsonl` — pre-review synth output; `runs/run_<ts>.jsonl` — per-item logs from `run_eval_retrieval.py`.
   - **Grading model**: retrieval-graded kinds (factual / table_formula / code_lookup / transcript / multi_turn) hit iff any `expected_parent_id` appears in the top-k returned. `no_answer` items must produce `资料中未找到相关内容。` as the answer prefix (the prompt forces a trailing `**资料来源：**` footer even on refusals, so `startswith` is the correct check, not `==`).
   - **Multi-turn grading**: turn-1 and turn-2 are both recorded; turn-2 is the one that actually tests the rewriter + carry-forward. Pairs share a single `ChatSession` instance; everything else uses a fresh one. When a multi-turn pair's source parent also fails as a solo, the t1/t2 misses are *inherited* — when interpreting multi-turn metrics, check whether the underlying solo item passed first.

7. **HTTP layer** (`api/`): a thin FastAPI wrapper around `ChatSession`. Routes live in `api/routes.py` under the `/api` prefix:
   - `POST /sessions` → `{session_id}`; `GET /sessions/{id}` returns the message log; `DELETE /sessions/{id}` evicts.
   - `POST /sessions/{id}/chat` returns an SSE stream (`sse-starlette`) with four event types: `prep` (search query, budget, used sources up-front), `token` (streamed text deltas), `done` (final answer text, sources, timings), and `error`. The handler offloads the sync `ChatSession.ask_stream` call and per-chunk iteration to worker threads with `asyncio.to_thread` so the event loop stays responsive; client disconnects close the underlying generator so partial state still flushes via the `try/finally` in `_wrap_stream`.
   - `GET /health`, `GET /config`, `GET /categories` — observability + filter UI metadata.
   - `POST /feedback` — appends one JSON record per call to `data/feedback.jsonl` for offline review. Two kinds: `answer` (👍/👎 on a turn) and `citation` (a specific source was wrong). See `api/feedback.py`.
   - **Session store** (`api/session_store.py`): in-process registry of `ChatSession` instances keyed by `session_id`, each with an `asyncio.Lock` that serializes concurrent turns on the same session. A background sweeper evicts sessions idle longer than `SESSION_TTL_SECONDS` (2h). Sessions are lost on restart — by design; persisting across restarts is a v2 concern.

## Prompts

All prompt text lives in `prompts/*.md` — **never inline prompt strings in Python code**. The loader is `src/prompts.py` (`load_prompt(name)` / `render_prompt(name, **kwargs)`, both `lru_cache`d). Current prompts:

- `answer_system.md` — main QA system prompt (role = BIM-company internal knowledge assistant; enforces citations, no-answer behavior, Revit/CAD answers preferring company practice over generic best practice).
- `answer_user.md` — user-message template with `{context}` and `{query}` placeholders.
- `rewrite_system.md` — standalone-query rewrite system prompt.
- `rewrite_user.md` — rewrite user-message template with `{history}` and `{question}` placeholders.

To add a new prompt: drop a `.md` in `prompts/`, then call `load_prompt("name")` or `render_prompt("name", **kw)`.

## Things to know when editing

- **Config is centralized** in `src/config.py` — chunk sizes, top-k values, collection name, model IDs all live there. Session-orchestration constants (`RESERVE_CHARS`, `HISTORY_TURNS`, `CARRY_SOURCES`) live at the top of `src/session.py`. Don't sprinkle constants elsewhere.
- **Indexing is incremental by default** — `_ensure_collection` creates the collection only if missing, and `store_parents` uses `INSERT OR REPLACE`. Deterministic UUIDv5 IDs (from `_stable_id` in `chunk.py`) mean re-indexing the same content overwrites in place. Adding new PDFs to `docs/` and re-running `scripts/build_index.py` appends them without touching existing entries. For destructive rebuilds (changed chunking / embedding logic), pass `--reset` to `build_index.py` or call `src.index.reset_index()`. `scripts/test_single_doc.py` always wipes (intentional for isolation testing).
- **PARENT_SIZE / CHILD_SIZE are in characters**, not tokens. Chinese text is ~1 char per token, so they double as a rough token budget.
- **Qdrant file mode locks the directory** — only one process can open `data/qdrant/` at a time. `_client()` in `index.py` and the inline `QdrantClient(path=...)` in `retrieve.py` both open per-call and close immediately; don't hold a client open across requests or you'll lock out the Streamlit app.
- **The protected-block regexes in `chunk.py` matter**: if PDFs start producing differently-shaped tables or inline math, update `HTML_TABLE_RE` / `PIPE_TABLE_RE` / `FORMULA_RE` rather than letting them split mid-row. For oversized HTML tables, also check `_split_table_with_header` still finds the `<tr>` header row to propagate.
- **The category metadata** is derived from the first folder under `docs/`. Adding a new PDF directly in `docs/` (with no subfolder) produces `category="uncategorized"`. The intended top-level categories are `行业规范` (industry codes), `客户标准` (customer requirements), `公司内部标准` (internal standards), `项目资料` (project deliverables / retrospectives), and `教学视频` / `transcriptions` (training-video transcripts). Existing subfolders may still reflect early test material (steel-structure codes); broaden naturally as more BIM / Revit / asset-management content is ingested.
- **Scratch / reference files at repo root** are not part of the runtime: `TODO` (running todo list), `i-want-to-build-nested-moore.md` (original design plan), `rag_cheatsheet.html` (reference cheatsheet). Don't treat them as authoritative — code + this CLAUDE.md are the source of truth.
- **Don't bypass `ChatSession`** for new UIs or scripts — replicating the rewrite/carry/budget/channel-separation logic in another caller is how invariants drift. Add new entry points by instantiating `ChatSession` and consuming `TurnResult` (sync) or `(StreamingTurnPrep, Iterator[str])` + `last_turn_result` (streaming), the way `app.py`, `eval_query.py`, and `api/routes.py` do.
- **Qdrant lock + the FastAPI process**: the file-mode Qdrant directory can only be opened by one process at a time. Running `uvicorn api.main:app` and `streamlit run app.py` simultaneously against the same `data/qdrant/` will fail — pick one frontend per process, or move to Qdrant server mode if both need to run.
- **Frontend ↔ backend contract**: the SSE event payloads (`prep` / `token` / `done` / `error`) and `SourceDTO` shape in `api/schemas.py` are consumed by `frontend/src/hooks/useChat.ts` and rendered by `Message.tsx` / `SourcesPanel.tsx` / `FeedbackBar.tsx`. Citation parsing for the two doc types lives in `frontend/src/components/citations.ts`; KaTeX rendering goes through `remark-math` + `rehype-katex` in `react-markdown`. Changes to event shape or `SourceDTO` need matching frontend updates.
