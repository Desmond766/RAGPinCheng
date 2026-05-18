# Plan: Internal RAG over PinCheng Steel-Structure Document Corpus

## Context

You want a RAG system for your company's internal knowledge. The corpus (14 Chinese engineering PDFs in [docs/](docs/)) is steel-structure design standards, calculation manuals, and welding/bolt/cable-tray specifications — content that is reference-heavy, table-heavy, and formula-heavy. Your goal: ask Chinese natural-language questions and get cited answers grounded in these PDFs.

You've already studied [rag_cheatsheet.html](rag_cheatsheet.html) and want the build to follow its recommendations. Per your answers:

- **Deployment:** Cloud API LLM — **GLM (Zhipu AI)** via OpenAI-compatible API
- **v1 scope:** Pragmatic RAG — structural-first parse, **parent-child chunking**, **hybrid retrieval** (BGE-M3 dense + sparse, RRF fusion). Defer reranker, eval harness, and query rewriting to v2.
- **Interface:** Streamlit web chat UI with citations
- **PDF parsing:** MinerU (best-in-class for Chinese technical PDFs with tables/formulas)

This is heavier than pure Naive RAG, but the two upgrades (parent-child + hybrid) are justified by the corpus shape:
- **Parent-child** matters because queries hit precise clauses ("Q235设计强度") but the LLM needs surrounding context (units, conditions, related formulas) to answer correctly.
- **Hybrid** matters because the corpus is dense with exact identifiers — steel grades (Q235, Q345), code numbers (GB50017), clause references (§5.1.1) — where BM25/sparse matching outperforms pure dense.

Reranker and eval harness are still deferred to v2 — to be added *after* baseline works and you can measure improvements against it.

---

## Architecture (v1 — Parent-Child + Hybrid)

```
PDFs (docs/)
  ↓ [ingest.py]   MinerU → Markdown + LaTeX formulas + tables
  ↓ [chunk.py]   Heading split → parent sections (~1200 tok)
                              → children (~256 tok, 32 overlap) with parent_id
  ↓ [embed.py]   BGE-M3 (local) → produces dense + sparse vectors per child
  ↓ [index.py]   Qdrant (local, named vectors: "dense" + "sparse")
                  + parent store (simple JSON / SQLite keyed by parent_id)
                  ─── one-shot offline build ───
                  ─── query-time below ───
User query → BGE-M3 encode (dense + sparse)
          → [retrieve.py] parallel: dense top-20 + sparse top-20
                         → RRF fusion → top-5 children
                         → expand each to parent section → dedupe
          → [generate.py] GLM-4 chat completion with cited parent sections
          → [app.py] Streamlit chat UI with source links
```

Folder layout:

```
RAGPinCheng/
├── docs/                      # existing PDFs (untouched)
├── data/
│   ├── parsed/                # MinerU markdown output (gitignored)
│   ├── qdrant/                # vector index — dense + sparse (gitignored)
│   └── parents.sqlite         # parent_id → full section text (gitignored)
├── src/
│   ├── ingest.py              # PDF → parsed markdown via MinerU
│   ├── chunk.py               # markdown → chunks with metadata
│   ├── embed.py               # BGE-M3 wrapper
│   ├── index.py               # build / load Chroma collection
│   ├── retrieve.py            # query → top-k chunks
│   ├── generate.py            # chunks + query → LLM answer
│   └── config.py              # paths, model names, k, chunk sizes, API keys
├── app.py                     # Streamlit entrypoint
├── scripts/
│   └── build_index.py         # one-shot: ingest + chunk + embed + index
├── requirements.txt
└── .env                       # DEEPSEEK_API_KEY (gitignored)
```

---

## Stage-by-stage decisions (matched to cheatsheet)

### 1. Parsing — MinerU
- Run MinerU once per PDF; cache markdown output to `data/parsed/<pdf_stem>.md`.
- MinerU preserves **tables as Markdown/HTML and formulas as LaTeX** — non-negotiable for engineering docs (cheatsheet §3).
- Skip-if-cached check so re-runs are cheap.
- Capture metadata per document: `source_path`, `category` (derived from folder: `钢结构-重要` / `其他规范` / `冷弯薄壁`), `doc_title`.

### 2. Chunking — Parent-Child with structural backbone
- Step A (structural): `MarkdownHeaderTextSplitter` splits MinerU output by `#`/`##`/`###`. Each resulting section becomes a **parent** (target ~1200 tokens; if a section is larger, use `RecursiveCharacterTextSplitter` with size 1200 / overlap 100 to break it into multiple parents).
- Step B (children): each parent is further split into **children** of ~256 tokens with 32-token overlap. Each child stores `parent_id` pointing back to its parent.
- **Enrichment:** prepend `doc_title > section_path` to each child *before embedding*. Cheap recall lift the cheatsheet flags as a baseline.
- **Table/formula protection:** detect Markdown table blocks and `$$...$$` LaTeX blocks; treat each as an atomic child (never split mid-row or mid-formula). If a table is huge, keep it as one child anyway and accept the size — splitting tables is worse than oversized chunks.
- Children store metadata: `parent_id`, `doc_title`, `category` (folder name), `section_path`, `source_path`, `content_type` ∈ {prose, table, formula}.
- Parents stored in `parents.sqlite` (key: `parent_id`, value: full section markdown + metadata).

### 3. Embedding — BGE-M3 (dense + sparse from one model)
- `BAAI/bge-m3` via `FlagEmbedding`. Critical advantage: **one model call returns both dense (1024-dim) and sparse (lexical weights) vectors** — exactly what hybrid retrieval needs, no separate BM25 index to maintain.
- Local inference, no API cost. Batch-encode children during index build.
- Warm-cache the model in Streamlit on app start (singleton); first query incurs the load.

### 4. Vector store — Qdrant (local)
- **Switch from Chroma to Qdrant** because Qdrant has first-class **named vectors** support: one collection holds both `"dense"` (cosine, 1024-dim) and `"sparse"` (BGE-M3 sparse) vectors per point. Chroma's hybrid story is weaker.
- Run Qdrant locally via `qdrant-client` with embedded/local mode (no Docker needed for v1; can upgrade later).
- Single collection `pincheng_docs`. HNSW index for the dense side.

### 5. Retrieval — Hybrid (dense + sparse) with RRF fusion
- Encode query with BGE-M3 → get both dense and sparse vectors.
- **Parallel search:** dense top-20 + sparse top-20.
- **Fuse with Reciprocal Rank Fusion** (`score = Σ 1/(k+rank)`, k=60 standard). Qdrant supports this natively via `query_points` with prefetch + fusion, or do it in Python.
- Take top-5 children after fusion → look up `parent_id` for each → fetch parents from `parents.sqlite` → **dedupe** (different children often point to the same parent; we want unique parents).
- Final context: 3–5 unique parent sections. Cap total context at ~6000 tokens to stay well within GLM's window.
- No cross-encoder reranker in v1 — deferred to v2 (highest-leverage upgrade once we can measure).

### 6. Generation — GLM-4 (Zhipu AI)
- Use Zhipu's OpenAI-compatible API: base URL `https://open.bigmodel.cn/api/paas/v4/`, model `glm-4-plus` (or `glm-4-flash` for cheaper smoke tests). Standard `openai` Python SDK works once `base_url` and `api_key` (`ZHIPU_API_KEY`) are set.
- System prompt (Chinese):
  - Role: 钢结构工程知识助手.
  - Strict refusal: 如果上下文中没有答案，明确说"资料中未找到相关内容"，禁止编造.
  - Citation rule: each fact must cite `[doc_title §section_path]`; answers without citations flagged in post-processing.
- Context block: retrieved parents delimited by `<source id="..." doc="..." section="...">...</source>`.
- Temperature 0.2 for factual consistency.

### 7. UI — Streamlit chat
- `st.chat_input` / `st.chat_message` for conversation flow.
- Below each answer: expandable "参考来源" panel listing the 5 retrieved chunks with `doc_title`, `section_path`, and a preview snippet.
- Sidebar: corpus stats (doc count, chunk count, embedding model name).
- No multi-turn memory in v1 (each query is independent) — keeps retrieval clean. Add conversational rewriting in v2 if needed.

---

## Critical files to create

| File | Purpose |
|---|---|
| [src/config.py](src/config.py) | Central config: paths, model names, chunk size, k, API base URL |
| [src/ingest.py](src/ingest.py) | MinerU runner with cache-skip |
| [src/chunk.py](src/chunk.py) | Markdown → enriched chunks with metadata |
| [src/embed.py](src/embed.py) | BGE-M3 load + encode (batched) |
| [src/index.py](src/index.py) | Qdrant collection (named vectors) + parents.sqlite build / load |
| [src/retrieve.py](src/retrieve.py) | Hybrid query (dense + sparse) → RRF → top-5 children → parent expansion |
| [src/generate.py](src/generate.py) | Parents + query → GLM-4 answer with citations |
| [scripts/build_index.py](scripts/build_index.py) | One-shot: ingest → chunk → embed → index |
| [scripts/eval_query.py](scripts/eval_query.py) | CLI: `python eval_query.py "<question>"` → prints answer + cited sources. Used by the testing subagent. |
| [app.py](app.py) | Streamlit entrypoint |
| [requirements.txt](requirements.txt) | mineru, langchain-text-splitters, FlagEmbedding, qdrant-client, openai, streamlit, python-dotenv |
| [.env.example](.env.example) | `ZHIPU_API_KEY=` placeholder |

---

## Verification (end-to-end test)

### Build steps (manual, run once)

1. **Install:** `pip install -r requirements.txt`. MinerU may need a separate GPU/CPU setup step — follow its README.
2. **Configure:** copy `.env.example` → `.env`, paste `ZHIPU_API_KEY`.
3. **Build index:** `python scripts/build_index.py`
   - Expect MinerU to take 5–30 min for 14 PDFs depending on hardware.
   - Sanity check: `data/parsed/` contains 14 `.md` files; Qdrant collection count is in the low thousands of children; `parents.sqlite` holds the parent sections.
4. **Launch UI:** `streamlit run app.py` — confirm chat interface loads and the sidebar shows non-zero corpus stats.

### Testing — delegate to a subagent

Once the index is built, spawn a **general-purpose subagent** with the prompt below. The subagent runs queries through the retrieval+generation pipeline (either via a small `scripts/eval_query.py` CLI we'll write, or by hitting the Streamlit backend programmatically) and reports a pass/fail table.

**Subagent prompt to use:**

> You are testing a Chinese-language RAG system over engineering documents. The system is built and the index lives at `data/qdrant/` + `data/parents.sqlite`. Run queries via `python scripts/eval_query.py "<query>"` which prints: (1) the GLM-4 answer, (2) the list of cited sources with `doc_title` and `section_path`.
>
> **Corpus contents** (14 Chinese steel-structure PDFs in `docs/`):
> - `钢结构-重要/`: GB50017-2017《钢结构设计标准》, 钢结构设计标准-2017-条文说明, 钢结构设计手册, 钢结构设计计算示例, 建筑钢结构施工手册, 建筑结构静力计算手册, 钢结构基础上册
> - `冷弯薄壁/`: GB50018-2002 冷弯薄壁型钢结构技术规范, 轻型钢结构设计手册（第2版）
> - `其他规范/`: GB50661-2011 钢结构焊接规范, JGJ82-2011 高强度螺栓连接技术规程, 03S402 室内管道支架及吊架, 04D701-3 电缆桥架安装, 钢结构连接节点设计手册（第三版）
>
> **Run these queries and judge each on three axes** — (a) retrieval relevance (was the right PDF cited?), (b) answer factual correctness (does the answer make engineering sense against the cited source?), (c) refusal behavior on out-of-scope queries.
>
> **Positive queries (factual lookup — must answer with citation):**
> 1. "Q235钢的抗拉强度设计值是多少？" → expect GB50017-2017
> 2. "高强度螺栓M20的预拉力是多少？" → expect JGJ82-2011
> 3. "冷弯薄壁型钢受压构件的容许长细比限值是多少？" → expect GB50018-2002
> 4. "钢结构焊接的预热温度如何确定？" → expect GB50661-2011
> 5. "电缆桥架水平安装的支撑间距要求？" → expect 04D701-3
> 6. "室内管道支架的最大间距规定？" → expect 03S402
> 7. "请举一个轴心受压柱的设计计算示例" → expect 钢结构设计计算示例 or 设计手册
>
> **Negative queries (must refuse, no hallucination):**
> 8. "公司2026年的年假政策是什么？" → must say "资料中未找到相关内容" or equivalent
> 9. "巴黎铁塔的高度是多少？" → must refuse
>
> **Edge queries (test parent-child + hybrid):**
> 10. "GB50017中第5.1.1条的内容是什么？" → tests exact-clause matching (sparse retrieval should shine here)
> 11. "Q345和Q235的强度差异" → tests multi-clause synthesis from one or more standards
>
> For each query, report: query, top cited `doc_title`, pass/fail on (a)(b)(c), and a one-line note if it failed. End with a summary: how many of 11 passed, and the top 2 failure modes (if any). Keep the report under 400 words.

If the subagent reports ≥9/11 passing with correct citations and proper refusal on the negative cases, v1 is shippable. Otherwise the failure modes point directly at what to tune (chunking, k, RRF weights, or system prompt).

---

## v2 backlog (do NOT build yet — wait for baseline metrics)

The cheatsheet's stronger recommendations to layer in *after* v1 ships and you have a small eval set:

- **Cross-encoder reranker:** `BAAI/bge-reranker-v2-m3` over top-20 (post-RRF) → top-5. Cheatsheet calls this the single highest-leverage v2 addition. Should be the FIRST v2 upgrade.
- **Eval harness:** LLM-synthesize ~50 Q/A pairs from the corpus using GLM-4, hand-review/edit, run Ragas (faithfulness + context precision/recall) before each future pipeline change. Promotes the testing subagent's ad-hoc questions into a regression suite.
- **Query rewriting:** LLM normalizes abbreviations / expands jargon before retrieval (helpful for engineering domain).
- **Conversational memory:** multi-turn rewriting so follow-ups work ("那它的安全系数呢？").

Build v1, run real queries, find what fails, then pick from this list based on observed failure mode — exactly the cheatsheet's "20% retrieval cleverness, 80% data + eval discipline" principle.
