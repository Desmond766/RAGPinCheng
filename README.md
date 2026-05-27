# 品成 BIM 知识库 (PinCheng BIM Knowledge Base)

An internal Chinese-language RAG system for a BIM consultancy. It indexes the company's accumulated knowledge — **industry codes, customer requirements, internal standards, past-project deliverables, and training-video transcripts** — and answers natural-language questions with citations like `[doc §section]` or `[doc @HH:MM:SS]`.

---

## Quick start — local

**Requirements:** Python 3.11+, Node.js 18+, ~10 GB disk, a running Qdrant instance.

```bash
# Start Qdrant (or use the Docker path below)
docker run -d -p 6333:6333 qdrant/qdrant

# Backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in ZHIPU_API_KEY, MINERU_API_KEY, ADMIN_EMPLOYEE_ID, ADMIN_PASSWORD
uvicorn api.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

First boot seeds an admin account from `ADMIN_EMPLOYEE_ID` / `ADMIN_PASSWORD`. Self-registration is open — staff sign up at `/register`.

To build the initial index from existing PDFs in `docs/`:

```bash
python scripts/build_index.py
```

---

## Quick start — Docker (self-hosted server)

Three services: `qdrant`, `backend`, `frontend` (nginx on port 80).

```bash
cp .env.example .env   # fill in ZHIPU_API_KEY, MINERU_API_KEY, ADMIN_EMPLOYEE_ID, ADMIN_PASSWORD
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f backend   # watch first-boot model download (~3 GB)
```

Live at `http://<server-ip>/` once `backend` is `healthy`. First boot takes 5–15 min while BGE-M3 + reranker weights download.

**Build initial index** (only needed when adding documents via the filesystem directly):

```bash
docker compose -f docker/docker-compose.yml exec backend python scripts/build_index.py
```

**Update code:**

```bash
git pull && docker compose -f docker/docker-compose.yml build && docker compose -f docker/docker-compose.yml up -d
```

**Useful env vars** (in `.env`):
- `ZHIPU_API_KEY` — required for generation
- `MINERU_API_KEY` — recommended; enables fast cloud PDF parsing (~1 min/PDF vs 30+ min local)
- `ADMIN_EMPLOYEE_ID` / `ADMIN_PASSWORD` — bootstrap admin on first boot
- `SESSION_COOKIE_SECURE` — set `false` for plain HTTP dev
- `HF_ENDPOINT=https://hf-mirror.com` — HuggingFace mirror for restricted networks
- `LLM_MODEL` / `LLM_REWRITE_MODEL` — override default models (`glm-4.7-flashx` / `glm-4.5-air`)

---

## Adding documents

**Via admin UI** (`/admin` → 资料管理 → 上传资料): upload `.pdf` or `.md` files directly from the browser. PDFs are parsed by MinerU, chunked, and embedded automatically; progress is shown in the 索引任务 table. No shell access needed.

**Via filesystem + CLI** (for bulk loads):

```bash
cp new_standard.pdf docs/行业规范/
python scripts/build_index.py   # incremental — only new files are processed
```

Document categories are derived from the first-level folder under `docs/`. Only `客户标准` uses a second level (`客户标准/<customer>/`). `.md` files in `教学视频/` are treated as video transcripts (speaker-turn + timestamp chunking); `.md` elsewhere is chunked like a parsed PDF.

---

## Debugging

```bash
# Retrieval only — no LLM, no API key
python scripts/test_retrieve.py "Q345 钢手工焊用什么焊条？"

# Full RAG with debug output (requires ZHIPU_API_KEY)
python scripts/eval_query.py "Q345 钢手工焊用什么焊条？"
# drops into REPL; /reset /history /full /short /exit
```

---

## Evaluation

A retrieval-graded golden set lives in `src/eval/` (~97 items across 6 question kinds).

```bash
python scripts/run_eval_retrieval.py          # prints R@1, R@5, MRR@5 by kind
python scripts/diff_eval_runs.py <a>.jsonl <b>.jsonl   # compare two runs
```

Baseline (May 2026): **R@1 = 90%, R@5 = 96%, no-answer compliance = 100%**.

---

## How it works

```
PDF / .md                  parsed markdown    chunks         vectors          answer
docs/<category>/   →(1)→   data/parsed/  →(2)→ parent+  →(3)→ Qdrant +   →(4)→ GLM-4
                   MinerU                       child        SQLite            citations
                                                             BGE-M3 + reranker
```

1. **Parse** — PDFs → markdown via MinerU. `.md` files skip this step.
2. **Chunk** — `chunk.py` splits by markdown headers into parent (1200 char) / child (256 char) pairs. Tables and formulas are kept atomic. Transcripts split by speaker turn; each chunk carries a `HH:MM:SS` timestamp.
3. **Embed + Index** — BGE-M3 produces dense + sparse vectors in one pass → Qdrant (server mode). Parent text → `data/parents.sqlite`.
4. **Retrieve + Rerank + Generate** — hybrid dense+sparse RRF retrieval with optional code-boost (detects standard codes like `GB 50017`), BGE-reranker-v2-m3 cross-encoder rerank, then Zhipu GLM-4 with strict citation rules.

**Multi-turn** (`src/session.py`): query rewriter resolves follow-ups; top 2 sources from the previous turn carry forward; context budget shrinks dynamically as history grows.

**HTTP layer** (`api/`): FastAPI with SSE streaming, server-side session cookie auth (`pc_sid`), CSRF token on mutating requests. Admin endpoints cover user management, conversation browsing, feedback log, and the document upload/indexing queue.

See `CLAUDE.md` for architecture invariants and what to be careful about when editing.
