# 品成 BIM 知识库 (PinCheng BIM Knowledge Base)

An internal Chinese-language RAG (retrieval-augmented generation) system for a BIM
consultancy. It indexes the company's accumulated knowledge — **industry codes,
customer requirements, internal standards, past-project deliverables, and
training-video transcripts** — and answers natural-language questions with
citations.

Staff can ask things like "Q345 钢手工焊用哪个型号焊条？" or "插座距地 300mm
建模时高度应输入多少？" and get an answer grounded in actual documents,
with `[doc §section]` or `[doc @HH:MM:SS]` citations they can verify.

This is **not** a steel-structure-specific assistant. Early test material happens
to be steel-structure handbooks; the corpus is intended to grow toward
general BIM / Revit / asset-management content.

---

## What it does, end-to-end

```
PDF / transcript           markdown        chunks       vectors        answer
─────────────────  →  ─────────────  →  ───────  →  ──────────  →  ──────────
docs/<category>/      data/parsed/      parent +    Qdrant +       Zhipu GLM-4
                       (MinerU)          child       SQLite        (with citations)
                                                    + BGE-M3
                                                    + reranker
```

1. **Parse** PDFs → markdown via MinerU (cloud API if `MINERU_API_KEY` is set;
   local CLI fallback otherwise).
2. **Chunk** markdown into parent / child chunks. Tables and formulas are kept
   atomic; transcripts are split by speaker turn so every citation carries a
   timestamp.
3. **Embed** child chunks with BGE-M3 (dense + sparse in one pass).
4. **Index** vectors into a local Qdrant store (`data/qdrant/`); parent text into
   `data/parents.sqlite`.
5. **Retrieve** with hybrid dense+sparse RRF, optional code-boost prefetch
   (queries mentioning `GB 50017` etc.), then re-rank with BGE-reranker-v2-m3.
6. **Generate** with Zhipu GLM-4 — packs retrieved parents into the prompt with
   strict citation rules and a `资料中未找到相关内容。` no-answer contract.
7. **`ChatSession`** orchestrates multi-turn conversations on top: query rewrite,
   carry-forward of last turn's sources, dynamic context budget.

Two front-ends consume the same `ChatSession`:

- **Streamlit** (`app.py`) — single-script demo, easiest to run locally.
- **FastAPI** (`api/`) + **React/Vite** (`frontend/`) — production-shaped web UI
  with SSE streaming, session management, feedback buttons.

---

## Deployment options

Two ways to run the system:

| | Local (venv) | Docker |
|---|---|---|
| **Best for** | Development, debugging, single-user use | Self-hosted server, team access |
| **Setup** | Python 3.11+ venv + Node.js 18+ | Docker Engine 24+ with compose plugin |
| **Front-end** | Streamlit or Vite dev server | nginx (serves built bundle on port 80) |
| **Qdrant lock** | One process at a time | Same restriction — one uvicorn worker |

Both paths share the same `.env` secrets and `data/` directory layout.

---

## Local setup (venv)

You need: macOS or Linux, Python 3.11+, ~10 GB free disk (model weights + index),
a Zhipu API key, optionally a MinerU API key, and Node.js 18+ if you want the
React front-end.

### 1. Clone and set up Python

```bash
git clone <this-repo-url> RAGPinCheng
cd RAGPinCheng

python3.12 -m venv .venv
source .venv/bin/activate          # bash/zsh
pip install -r requirements.txt
```

First-time `pip install` pulls PyTorch, FlagEmbedding, qdrant-client, etc. —
expect 5-10 min and ~3 GB.

### 2. Configure secrets

```bash
cp .env.example .env
# Edit .env and fill in at minimum:
#   ZHIPU_API_KEY=...              (required for generation; get one at bigmodel.cn)
#   MINERU_API_KEY=...             (optional; fast cloud PDF parsing — strongly recommended)
#   LLM_MODEL=glm-4.7-flashx      (optional override; default is glm-4.7-flashx)
#   LLM_REWRITE_MODEL=glm-4.5-air (optional; cheaper model for the rewrite step only —
#                                   defaults to glm-4.5-air when unset)
```

Retrieval works without `ZHIPU_API_KEY` (you can run `scripts/test_retrieve.py`
to verify), but you can't generate answers without it.

### 3. Drop documents into `docs/`

The folder name under `docs/` becomes the **category** metadata:

```
docs/
├── 行业规范/          # industry codes (PDFs)
├── 客户标准/          # customer requirements (PDFs)
├── 公司内部标准/      # internal standards (PDFs)
├── 项目资料/          # past project deliverables (PDFs)
└── 教学视频/          # MinerU-exported video transcripts (markdown, not PDF)
```

Transcript files must live in `docs/教学视频/` with a `.md` extension —
any filename works. The chunker detects timestamps from `说话人 N HH:MM:SS`
lines in the file body. The video title is read from the first
`**文字记录：<title>**` line of the file; the filename stem is only a fallback.
Files whose names start with `智能纪要：` are skipped on purpose — we index
transcripts only so every citation carries a timestamp.

### 4. Build the index

```bash
python scripts/build_index.py
```

First-time build for a corpus of ~3 large handbooks + a couple transcripts:
- With `MINERU_API_KEY`: ~5-10 minutes (PDF parse runs in cloud).
- Without `MINERU_API_KEY`: 30+ minutes (local mineru CLI is CPU-only).

The build is **incremental by default**. Drop more PDFs into `docs/<category>/`
and re-run — only new files get parsed and indexed; existing content is
overwritten in place (deterministic UUIDv5 IDs). For a full rebuild after
changing chunking or embedding logic, add `--reset`.

### 5. Run a front-end

**Streamlit** (simplest):

```bash
streamlit run app.py
# opens http://localhost:8501
```

**FastAPI + React** (production-shaped):

```bash
# Terminal A — backend
uvicorn api.main:app --reload --port 8000

# Terminal B — frontend
cd frontend
npm install                  # first time only
npm run dev                  # opens http://localhost:5173
```

> **Heads up — Qdrant single-writer**: `data/qdrant/` can only be opened by one
> process at a time. Don't run Streamlit and the FastAPI backend
> simultaneously against the same data directory. Pick one front-end per
> session.

---

---

## Docker deployment (self-hosted server)

The repo ships with a two-container Docker setup intended for self-hosted
deployment on a Linux x86_64 server. Everything below assumes you're the
engineer running and maintaining this system in production.

> **Apple Silicon Mac?** The default build target is `linux/amd64` (for the
> production server). To build natively on an arm64 Mac (much faster — avoids
> QEMU emulation), add `BUILD_PLATFORM=linux/arm64` to your `.env` before
> running `docker compose -f docker/docker-compose.yml build`. The resulting image won't run on an x86
> server; use the default for production builds.

### What ships in the deployment

| Container | Image base | Role |
|---|---|---|
| `backend` | `python:3.11-slim` | FastAPI + ChatSession + BGE-M3 + reranker. Single uvicorn worker. Not exposed to the host. |
| `frontend` | `nginx:1.27-alpine` (multi-stage with `node:20-alpine` build) | Serves the built React bundle + reverse-proxies `/api/*` to the backend with SSE-friendly settings. Exposes port 80 on the host. |

**What lives where:**

- **Image** (immutable artifact): app code, Python deps, nginx config. Rebuilt only when code changes.
- **Bind mount `./data` → `/app/data`**: Qdrant index, `parents.sqlite`, parsed markdown cache, `feedback.jsonl`. Grows with corpus size. **This is your stateful data.**
- **Bind mount `./docs` → `/app/docs`**: source PDFs + transcripts. Read by `scripts/build_index.py` only; the running API doesn't touch it.
- **Named volume `hf_cache`**: BGE-M3 + reranker weights (~3 GB). Downloaded on first container start, then persists.

Streamlit (`app.py`) and `mineru[core]` (local PDF parsing CLI) are intentionally **not** shipped in the production image. MinerU runs cloud-only in deployment.

### Prerequisites on the server

- Linux x86_64 (Ubuntu 22.04+ / Debian 12 / RHEL 9 etc.)
- Docker Engine 24+ and the `docker compose` plugin
- ~10 GB free disk for images + model cache, plus whatever your corpus needs
- Port 80 free (or change the `ports:` mapping in `docker/docker-compose.yml`)
- Network access to `bigmodel.cn` (LLM) and `huggingface.co` (model weights, or use the `HF_ENDPOINT` mirror — see below)

### First deploy

```bash
# 1. Get the code on the server
git clone <repo-url> /srv/pincheng-rag
cd /srv/pincheng-rag

# 2. Provide secrets — same format as local .env
cat > .env <<'EOF'
ZHIPU_API_KEY=...
MINERU_API_KEY=...
# Uncomment if huggingface.co is unreachable from the server:
# HF_ENDPOINT=https://hf-mirror.com
EOF

# 3. Get the data into place — choose ONE of:

# 3a. Carry existing index from your dev machine:
rsync -av --progress \
    /Users/you/Codes/RAGPinCheng/data/ \
    user@server:/srv/pincheng-rag/data/
rsync -av --progress \
    /Users/you/Codes/RAGPinCheng/docs/ \
    user@server:/srv/pincheng-rag/docs/

# 3b. OR build the index fresh on the server:
#     Put PDFs into ./docs/<category>/ first, then:
docker compose -f docker/docker-compose.yml run --rm backend python scripts/build_index.py

# 4. Start the system
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d

# 5. Watch the first-boot model download (~3 GB; 5-15 min depending on network)
docker compose -f docker/docker-compose.yml logs -f backend
```

System is live at `http://<server-ip>/` once `docker compose -f docker/docker-compose.yml ps` shows `backend` as `healthy`.

### When to run `build_index.py` (this matters)

**Only when you add, replace, or remove source documents in `docs/`.** It is **not** part of container startup — the backend starts directly against the existing `data/qdrant/` and `parents.sqlite` on disk. If those are empty on first boot, the API will run but every question will get `资料中未找到相关内容。` until you build an index.

Operationally:

```bash
# After copying new PDFs into docs/<category>/ on the server:
docker compose -f docker/docker-compose.yml exec backend python scripts/build_index.py

# Full rebuild (changed chunking / embedding logic):
docker compose -f docker/docker-compose.yml exec backend python scripts/build_index.py --reset
```

Indexing is incremental by default — only new content gets parsed and embedded. Existing entries are overwritten in place via deterministic UUIDv5 IDs, so re-running is safe and idempotent. You can run it while the API is serving traffic; Qdrant file-mode uses short-lived clients so brief read/write coexistence works. For very large indexing passes (hundreds of new PDFs), stop the API first to avoid file-lock contention:

```bash
docker compose -f docker/docker-compose.yml stop backend
docker compose -f docker/docker-compose.yml run --rm backend python scripts/build_index.py
docker compose -f docker/docker-compose.yml start backend
```

### Day-to-day operations

```bash
# Tail logs
docker compose -f docker/docker-compose.yml logs -f backend
docker compose -f docker/docker-compose.yml logs -f frontend

# Restart one service (preserves the other)
docker compose -f docker/docker-compose.yml restart backend

# Stop everything (data on disk is preserved)
docker compose -f docker/docker-compose.yml down

# Restart everything
docker compose -f docker/docker-compose.yml up -d

# Deploy new code
git pull
docker compose -f docker/docker-compose.yml build         # rebuilds images for any changed layers
docker compose -f docker/docker-compose.yml up -d         # recreates containers using the new images
                             # bind-mounted data/ and docs/ are untouched

# Open a shell inside the backend container for debugging
docker compose -f docker/docker-compose.yml exec backend bash

# Check resource use
docker stats

# See current image sizes
docker images | grep pincheng-rag
```

### Backup

Everything stateful is in two bind-mounted directories on the host. There is **no database to dump** — file-level snapshot is enough.

```bash
# Stop the backend so Qdrant isn't being written during the snapshot
docker compose -f docker/docker-compose.yml stop backend

# Snapshot the data directory (Qdrant + SQLite + parsed markdown + feedback)
tar czf pincheng-data-$(date +%Y%m%d).tar.gz data/

# Optionally also archive source documents
tar czf pincheng-docs-$(date +%Y%m%d).tar.gz docs/

docker compose -f docker/docker-compose.yml start backend
```

The `hf_cache` named volume holds nothing irreplaceable (it's just downloaded HuggingFace weights — `docker compose up` will re-download if missing). Don't bother backing it up.

### HF endpoint mirror (restricted networks)

If the server can't reach `huggingface.co` directly (common in mainland China), add this line to `.env`:

```
HF_ENDPOINT=https://hf-mirror.com
```

Then `docker compose -f docker/docker-compose.yml up -d`. The first-boot model download will use the mirror automatically — no code changes needed. Default points at the real CDN, so leaving it unset works wherever HF is reachable.

### TLS / HTTPS

The frontend container serves plain HTTP on port 80. For HTTPS, put a company reverse proxy (nginx, Caddy, Traefik) in front of port 80 and terminate TLS there. The compose setup deliberately doesn't manage certs.

### Deployment-time pitfalls

- **First boot looks hung for 5-15 min.** That's BGE-M3 + reranker downloading into `hf_cache`. The healthcheck `start_period` is set to 15 min for exactly this reason. Tail logs to confirm progress; you should see "warming embed model (BGE-M3)..." and then "api ready".
- **`docker compose -f docker/docker-compose.yml down -v` deletes the model cache.** The `-v` flag removes named volumes. Use plain `docker compose -f docker/docker-compose.yml down` for routine stop/start.
- **Building from a Mac (arm64) produces an x86 image** because the Dockerfiles pin `--platform=linux/amd64`. Builds are slower on Mac due to emulation but the resulting image runs natively on the x86 server. Prefer building on the server itself.
- **`scripts/build_index.py` from the host venv works too**, since `data/` is bind-mounted. You don't strictly need to exec into the container — but keeping all indexing inside the container avoids the "did I activate the right venv?" class of bugs.
- **Backend port 8000 is not published to the host.** If you need to hit the API directly (curl, Postman) for debugging, add `ports: ["8000:8000"]` to the backend service in `docker/docker-compose.yml` temporarily, then `docker compose -f docker/docker-compose.yml up -d`.

---

## Daily use

### Asking questions

The system answers in Chinese, cites every claim, and falls back to
`资料中未找到相关内容。` when the corpus doesn't contain the answer. Two
citation formats:

- `[文档名 §章节路径]` for PDFs
- `[文档名 @HH:MM:SS]` for video transcripts

Multi-turn works — the rewriter resolves follow-ups like "那 Q390 呢？" against
the previous turn's context, and the previous turn's top sources are
carried forward as a safety net.

### Adding documents later

```bash
cp new_standard.pdf docs/行业规范/
python scripts/build_index.py
# only new_standard.pdf is parsed and indexed; existing entries untouched.
```

### Renaming categories

If you rename a folder under `docs/` after indexing, the Qdrant payload still
holds the old label. Run the maintenance script to fix it without
re-embedding:

```bash
python scripts/migrate_categories.py --dry-run    # see what would change
python scripts/migrate_categories.py              # apply renames
```

### Debugging a specific query

```bash
# retrieval only — no LLM, no API key needed
python scripts/test_retrieve.py "Q345 钢手工焊用什么焊条？"

# full RAG with debug output — requires ZHIPU_API_KEY
python scripts/eval_query.py "Q345 钢手工焊用什么焊条？"
# drops into REPL after the first turn; slash commands: /reset, /history, /full, /short, /exit
```

`eval_query.py` is the fastest way to see exactly what was retrieved, how the
context budget was spent, and what the LLM did with it.

---

## Evaluation

A retrieval-graded golden set lives in `src/eval/`. It's how we measure
whether tuning changes (chunk sizes, reranker, prompt edits) actually help.

```bash
# Sample 100+ parent chunks weighted by question kind
python scripts/sample_for_eval.py

# Hand-curated golden.jsonl already exists with ~97 items across:
#   factual / table_formula / code_lookup / transcript / multi_turn / no_answer

# Run the evaluation (requires ZHIPU_API_KEY)
python scripts/run_eval_retrieval.py
# Prints Recall@1, Recall@5, MRR@5 by question kind.
# Detailed per-item log lands in src/eval/runs/run_<timestamp>.jsonl.

# Diff two runs to see what fixed / regressed
python scripts/diff_eval_runs.py \
    src/eval/runs/run_<baseline>.jsonl \
    src/eval/runs/run_<candidate>.jsonl
```

Baseline numbers (May 2026, 97 items): **R@1 = 90%, R@5 = 96%, no-answer
compliance = 100%**. Any tuning change that drops below these is a regression
and needs investigation before merging.

---

## Project layout

```
RAGPinCheng/
├── app.py                  # Streamlit UI (local dev only; not in prod image)
├── src/
│   ├── ingest.py           # PDF → markdown (MinerU)
│   ├── chunk.py            # markdown → parent/child chunks
│   ├── embed.py            # BGE-M3 dense + sparse
│   ├── index.py            # Qdrant + parents.sqlite
│   ├── retrieve.py         # hybrid + rerank
│   ├── rerank.py           # BGE-reranker-v2-m3
│   ├── generate.py         # Zhipu GLM call + prompt packing
│   ├── session.py          # multi-turn orchestration (ChatSession)
│   ├── prompts.py          # prompt loader
│   ├── config.py           # all tuning knobs in one place
│   └── eval/               # retrieval golden set + metrics
├── api/                    # FastAPI backend (routes, SSE, session store)
├── frontend/               # React + Vite UI source
├── docker/                 # all Docker-related files
│   ├── docker-compose.yml  # two-service production setup
│   ├── Dockerfile.backend  # backend image (FastAPI + ML models, CPU torch)
│   ├── Dockerfile.frontend # multi-stage: node build → nginx serve
│   ├── nginx.conf          # static + /api reverse proxy with SSE settings
│   ├── Dockerfile.backend.dockerignore
│   └── Dockerfile.frontend.dockerignore
├── prompts/                # all LLM prompts as .md files
├── scripts/                # build_index, sample_for_eval, run_eval, etc.
├── docs/                   # source corpus (PDFs + transcript markdown)
├── data/                   # parsed markdown, Qdrant index, parents.sqlite
│   └── feedback.jsonl      # 👍/👎 from the React UI, append-only
├── requirements.txt        # full local-dev deps (includes streamlit, mineru[core])
└── requirements-prod.txt   # slimmed deps shipped in the backend image
```

See `CLAUDE.md` for architectural detail, invariants, and what to be careful
about when editing.

---

## Common pitfalls

- **"Address already in use" on uvicorn**: another `uvicorn api.main:app` is
  already running, or the Streamlit process has the Qdrant lock. `lsof -i :8000`
  / `lsof data/qdrant/` to find it.
- **Reranker takes 30+ seconds on first query**: BGE-reranker-v2-m3 weights
  (~600 MB) are downloading. Subsequent queries are fast.
- **"资料中未找到相关内容。" on questions you expected to work**: either the
  document hasn't been indexed (check `data/qdrant/` was rebuilt after you
  added it), or the topic is genuinely outside the corpus. Run
  `scripts/test_retrieve.py` to see what came back.
- **Large PDFs and the 200-page cloud limit**: the cloud API accepts at most
  `MINERU_MAX_PAGES = 200` pages per submission. `_cloud_parse()` handles this
  automatically — it splits the PDF into ≤200-page chunks, submits each as a
  separate batch job, and concatenates the resulting markdown. No fallback to
  local parsing; the whole file still goes through the cloud.

---

## What `CLAUDE.md` is

`CLAUDE.md` is documentation **for Claude Code** (the AI coding assistant) when
it works in this repo — architectural deep-dive, invariants, the kind of
internal context an AI agent needs to make safe changes. Humans can read it
too, but the README is the human entry point.
