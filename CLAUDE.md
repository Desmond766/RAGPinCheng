# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Chinese-language RAG assistant for an internal BIM consultancy. The company's main business is producing Revit / CAD models and drawings for clients, plus developing asset-management software. This RAG indexes the company's internal knowledge — industry standards & codes, past project deliverables and retrospectives, technical notes — so staff can query specs and prior project experience.

PDFs in `docs/` are parsed to markdown, chunked, embedded with BGE-M3, indexed in a local Qdrant store with parent text in SQLite, retrieved via dense+sparse RRF hybrid, and answered by Zhipu GLM-4 with citations. Frontend is Streamlit.

## Commands

All commands run from the repo root, with the project venv (`.venv`) activated.

- **Run the chat UI**: `streamlit run app.py`
- **Build / extend the index** (parse PDFs → chunk → embed → upsert): `python scripts/build_index.py`
  - **Incremental by default** — re-running with new PDFs in `docs/` keeps existing indexed content (deterministic UUIDv5 IDs mean same content overwrites in place, new content is appended).
  - `--force-parse` re-parses PDFs even if cached markdown exists in `data/parsed/`.
  - `--reset` drops the Qdrant collection and wipes `parents.sqlite` before building (full rebuild from scratch — use after changing chunking or embedding logic).
- **Retrieval smoke test** (no LLM, no API key needed): `python scripts/test_retrieve.py "<question>"` (or no arg for default probes).
- **End-to-end query with LLM answer**: `python scripts/eval_query.py "<question>"` (requires `ZHIPU_API_KEY`).

No test suite, lint, or typecheck is configured.

## Required environment (.env)

- `ZHIPU_API_KEY` — Zhipu GLM (OpenAI-compatible at `https://open.bigmodel.cn/api/paas/v4/`). Required for generation, not for retrieval.
- `MINERU_API_KEY` — if set, `src/ingest.py` uses the MinerU cloud API (fast, ~1 min/PDF). If unset, falls back to local `mineru` CLI (slow, CPU-only).
- `LLM_MODEL` — overrides the default Zhipu model (default `glm-4.6v`).

## Architecture

The pipeline has four stages, each owned by one module in `src/`. Code uses dataclasses to pass typed records between stages — read the dataclass at the top of each file to understand its contract.

1. **Ingest** (`src/ingest.py`): walks `docs/<category>/*.pdf` and produces `data/parsed/<safe_stem>.md`. Cloud path = presign → PUT upload → submit task → poll → download (zip or .md). The folder under `docs/` becomes the `category` metadata. Markdown is cached; re-runs skip already-parsed PDFs unless `--force`.

2. **Chunk** (`src/chunk.py`): parent-child chunking on the markdown.
   - `MarkdownHeaderTextSplitter` (#/##/###) → header-anchored sections form parents. Oversized sections are further split by `RecursiveCharacterTextSplitter` (PARENT_SIZE=1200 chars).
   - Children (CHILD_SIZE=256) are produced **per parent**, but tables (`| ... |` rows) and `$$ ... $$` formula blocks are detected by `_split_protected` and emitted as **atomic children** — never split mid-row or mid-LaTeX. Each child carries a `content_type` of `prose | table | formula`.
   - Each child's `embed_text` prepends `doc_title > section_path` so embeddings see the heading context.
   - IDs are deterministic UUIDv5 (see `_stable_id`), so re-running produces the same IDs for identical content.

3. **Index** (`src/index.py`): two stores, always rebuilt together.
   - **Qdrant** (local file mode at `data/qdrant/`, no server) holds children with two named vectors: `dense` (1024-d cosine) and `sparse`. The collection is **dropped and recreated** on each `index_children` call.
   - **SQLite** at `data/parents.sqlite` holds full parent text keyed by `parent_id`. Also wiped (`DELETE FROM parents`) on each rebuild.
   - Embeddings come from `src/embed.py` — a `BGEM3FlagModel` (BGE-M3) that returns dense + lexical-sparse in one pass. The model is `lru_cache`d; first load downloads weights.

4. **Retrieve + Generate** (`src/retrieve.py`, `src/generate.py`):
   - `retrieve()` runs Qdrant's native `query_points` with two `Prefetch`es (dense, sparse) fused by `FusionQuery(RRF)`. Over-fetches `top_k * 4` children, then dedupes by `parent_id` and expands the surviving parents from SQLite. Returns `RetrievedParent` dataclasses with score + matched child snippets.
   - `generate()` packs parents into `<source id=… doc=… section=…>` blocks up to `MAX_CONTEXT_CHARS=6000`, then calls Zhipu via the `openai` SDK. The system prompt forces Chinese, citation format `[doc_title §section_path]`, and "资料中未找到相关内容。" on miss — do not weaken these rules without intent.
   - `rewrite_query()` (also in `generate.py`) does one cheap LLM call to rewrite a follow-up question into a standalone one using recent chat history, so retrieval works across turns. `app.py` calls it before `retrieve()` when there is prior history.

## Prompts

All prompt text lives in `prompts/*.md` — **never inline prompt strings in Python code**. The loader is `src/prompts.py` (`load_prompt(name)` / `render_prompt(name, **kwargs)`, both `lru_cache`d). Current prompts:

- `answer_system.md` — main QA system prompt (role = BIM-company internal knowledge assistant; enforces citations, no-answer behavior, Revit/CAD answers preferring company practice over generic best practice).
- `answer_user.md` — user-message template with `{context}` and `{query}` placeholders.
- `rewrite_system.md` — standalone-query rewrite system prompt.
- `rewrite_user.md` — rewrite user-message template with `{history}` and `{question}` placeholders.

To add a new prompt: drop a `.md` in `prompts/`, then call `load_prompt("name")` or `render_prompt("name", **kw)`.

## Things to know when editing

- **Config is centralized** in `src/config.py` — chunk sizes, top-k values, collection name, model IDs all live there. Don't sprinkle constants elsewhere.
- **Indexing is incremental by default** — `_ensure_collection` creates the collection only if missing, and `store_parents` uses `INSERT OR REPLACE`. Deterministic UUIDv5 IDs (from `_stable_id` in `chunk.py`) mean re-indexing the same content overwrites in place. Adding new PDFs to `docs/` and re-running `scripts/build_index.py` appends them without touching existing entries. For destructive rebuilds (changed chunking / embedding logic), pass `--reset` to `build_index.py` or call `src.index.reset_index()`. `scripts/test_single_doc.py` always wipes (intentional for isolation testing).
- **PARENT_SIZE / CHILD_SIZE are in characters**, not tokens. Chinese text is ~1 char per token, so they double as a rough token budget.
- **Qdrant file mode locks the directory** — only one process can open `data/qdrant/` at a time. The Streamlit app opens a client per request and closes it; don't hold a client open across requests.
- **The protected-block regex in `chunk.py` matters**: if PDFs start producing differently-shaped tables or inline math, update `TABLE_RE` / `FORMULA_RE` rather than letting them split mid-row.
- **The category metadata** is derived from the first folder under `docs/`. Adding a new PDF directly in `docs/` (with no subfolder) produces `category="uncategorized"`. Current subfolders reflect early test material (steel-structure codes) and will broaden over time as BIM / Revit / asset-management content is added.
