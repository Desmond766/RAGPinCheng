# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Chinese-language RAG assistant for an internal BIM consultancy. The company's main business is producing Revit / CAD models and drawings for clients, plus developing asset-management software. This RAG indexes the company's internal knowledge — industry standards & codes, past project deliverables and retrospectives, technical notes — so staff can query specs and prior project experience.

PDFs in `docs/` are parsed to markdown, chunked, embedded with BGE-M3, indexed in a local Qdrant store with parent text in SQLite, retrieved via dense+sparse RRF hybrid, and answered by Zhipu GLM-4 with citations. A `ChatSession` layer orchestrates multi-turn conversations (rewrite → retrieve → merge → generate). Frontend is Streamlit.

## Commands

All commands run from the repo root, with the project venv (`.venv`) activated.

- **Run the chat UI**: `streamlit run app.py`
- **Build / extend the index** (parse PDFs → chunk → embed → upsert): `python scripts/build_index.py`
  - **Incremental by default** — re-running with new PDFs in `docs/` keeps existing indexed content (deterministic UUIDv5 IDs mean same content overwrites in place, new content is appended).
  - `--force-parse` re-parses PDFs even if cached markdown exists in `data/parsed/`.
  - `--reset` drops the Qdrant collection and wipes `parents.sqlite` before building (full rebuild from scratch — use after changing chunking or embedding logic).
- **Retrieval smoke test** (no LLM, no API key needed): `python scripts/test_retrieve.py "<question>"` (or no arg for default probes).
- **Interactive RAG agent with full debug output** (requires `ZHIPU_API_KEY`): `python scripts/eval_query.py ["<seed question>"]`. Drops into a REPL after the optional seed turn; slash commands: `/reset`, `/history`, `/verbose N`, `/full`, `/short`, `/exit`. Routes through the same `ChatSession` as the Streamlit app, so eval reproduces UI behavior exactly.

No test suite, lint, or typecheck is configured.

## Required environment (.env)

- `ZHIPU_API_KEY` — Zhipu GLM (OpenAI-compatible at `https://open.bigmodel.cn/api/paas/v4/`). Required for generation, not for retrieval.
- `MINERU_API_KEY` — if set, `src/ingest.py` uses the MinerU cloud API (fast, ~1 min/PDF). If unset, falls back to local `mineru` CLI (slow, CPU-only).
- `LLM_MODEL` — overrides the default Zhipu model (default `glm-4.6v`).

## Architecture

The pipeline has four data stages owned by `src/` modules, plus a session orchestration layer that sits on top for multi-turn conversations. Code uses dataclasses to pass typed records between stages — read the dataclass at the top of each file to understand its contract.

1. **Ingest** (`src/ingest.py`): walks `docs/<category>/*.pdf` and produces `data/parsed/<safe_stem>.md`. Cloud path = presign → PUT upload → submit task → poll → download (zip or .md). The folder under `docs/` becomes the `category` metadata. Markdown is cached; re-runs skip already-parsed PDFs unless `--force`.

2. **Chunk** (`src/chunk.py`): parent-child chunking on the markdown.
   - `MarkdownHeaderTextSplitter` (#/##/###) → header-anchored sections form parents. Oversized sections are further split by `RecursiveCharacterTextSplitter` (PARENT_SIZE=1200 chars).
   - Children (CHILD_SIZE=256) are produced **per parent**, but tables (HTML `<table>` blocks, pipe `| ... |` tables) and `$$ ... $$` formula blocks are detected by `_find_protected_spans` / `_split_protected` and emitted as **atomic children** — never split mid-row or mid-LaTeX. Each child carries a `content_type` of `prose | table | formula`.
   - **Table-atomicity overrides PARENT_SIZE**: HTML tables up to `ATOMIC_TABLE_MAX = 2 * PARENT_SIZE` stay in a single parent even when they overflow the regular parent budget. Larger HTML tables are row-split by `_split_table_with_header`, which **prepends the original first `<tr>` (header row) to every fragment** so each chunk still carries its column labels.
   - Each child's `embed_text` prepends `doc_title > section_path` so embeddings see the heading context.
   - IDs are deterministic UUIDv5 (see `_stable_id`). `parent_id` hashes the **full parent text**, not a prefix — header-propagated table fragments share their first ~80 chars (the column row) and would otherwise collide.

3. **Index** (`src/index.py`): two stores.
   - **Qdrant** (local file mode at `data/qdrant/`, no server) holds children with two named vectors: `dense` (1024-d cosine) and `sparse`. `_ensure_collection` creates the collection only if missing; the collection is dropped only when `index_children(..., reset=True)` is passed (or via `reset_index()`). Default path is upsert, relying on deterministic IDs to overwrite same-content points in place.
   - **SQLite** at `data/parents.sqlite` holds full parent text keyed by `parent_id`. `store_parents` uses `INSERT OR REPLACE`; only wiped when `reset=True`.
   - Embeddings come from `src/embed.py` — a `BGEM3FlagModel` (BGE-M3) that returns dense + lexical-sparse in one pass. The model is `lru_cache`d; first load downloads weights.

4. **Retrieve + Generate** (`src/retrieve.py`, `src/generate.py`):
   - `retrieve()` runs Qdrant's native `query_points` with two `Prefetch`es (dense, sparse) fused by `FusionQuery(RRF)`. Over-fetches `top_k * 4` children, then dedupes by `parent_id` and expands the surviving parents from SQLite. Returns `RetrievedParent` dataclasses with score + matched child snippets.
   - `generate(query, parents, history, budget)` packs parents into `<source id=… doc=… section=…>` blocks up to the per-turn `budget` (chars), interleaves `history` turns as native chat messages, then calls Zhipu via the `openai` SDK. The system prompt forces Chinese, citation format `[doc_title §section_path]`, and "资料中未找到相关内容。" on miss — do not weaken these rules without intent.
   - `rewrite_query(history, question)` does one cheap LLM call to rewrite a follow-up question into a standalone one using recent chat history, so retrieval works across turns. Returns `question` unchanged on empty history or any error (best-effort, never raises).

5. **Session orchestration** (`src/session.py`): `ChatSession.ask(query)` drives the 5-stage per-turn pipeline: **① rewrite → ② retrieve fresh → ③ merge with carry-forward → ④ generate → ⑤ update state**. Both `app.py` and `scripts/eval_query.py` go through this — never call `retrieve()` / `generate()` directly from UI/CLI code, route through `ChatSession`.
   - **Carry-forward**: `retrieve_for_turn` appends the top `CARRY_SOURCES` (=2) parents from the previous turn to the fresh retrieval, deduped by `parent_id`. Safety net for thin follow-up rewrites that fail to re-find a parent the user is clearly still discussing.
   - **Dynamic context budget**: `budget = MAX_CONTEXT_CHARS - history_chars - RESERVE_CHARS` (RESERVE_CHARS=700). History grows turn-by-turn; sources are the elastic component that shrinks to fit.
   - **History window**: only the last `HISTORY_TURNS` (=4) user/assistant pairs are fed to `generate()`. The full message log lives in `SessionState.messages` for replay, but the LLM only sees the recent window.
   - **Channel separation** (load-bearing invariant): the conversation channel (`SessionState.messages`) is text only — `<source>` blocks are **never** stored in assistant messages. The knowledge channel (`SessionState.last_sources`, typed `RetrievedParent` objects) carries sources for the next turn's carry-forward and for the UI. The two never mix inside the message list sent to the LLM. If you change how sources are surfaced, preserve this split — leaking source XML into stored assistant content will pollute future-turn history.
   - `TurnResult` bundles the answer with telemetry (rewrite applied, fresh/final source counts, history chars, budget) for debug panels and the eval CLI.

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
- **The category metadata** is derived from the first folder under `docs/`. Adding a new PDF directly in `docs/` (with no subfolder) produces `category="uncategorized"`. Current subfolders reflect early test material (steel-structure codes) and will broaden over time as BIM / Revit / asset-management content is added.
- **Don't bypass `ChatSession`** for new UIs or scripts — replicating the rewrite/carry/budget/channel-separation logic in another caller is how invariants drift. Add new entry points by instantiating `ChatSession` and consuming `TurnResult`, the way `app.py` and `eval_query.py` do.
