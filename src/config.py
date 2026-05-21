from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
PARSED_DIR = DATA_DIR / "parsed"
QDRANT_DIR = DATA_DIR / "qdrant"
PARENTS_DB = DATA_DIR / "parents.sqlite"

for d in (DATA_DIR, PARSED_DIR, QDRANT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Chunking — sizes are in characters (Chinese ≈ 1 char per token for budgeting)
PARENT_SIZE = 1200
PARENT_OVERLAP = 100
CHILD_SIZE = 256
CHILD_OVERLAP = 32

# Embedding
EMBED_MODEL = "BAAI/bge-m3"
EMBED_DIM = 1024
EMBED_BATCH = 32

# Retrieval
DENSE_TOP_K = 60
SPARSE_TOP_K = 60
# Code-boost prefetch (extra pool restricted to children whose text contains
# a detected standard-code identifier; only fires when codes appear in the query).
CODE_BOOST_TOP_K = 40
# Children handed to the cross-encoder reranker before parent dedupe.
RERANK_TOP_K = 40
FINAL_TOP_K = 5
MAX_CONTEXT_CHARS = 6000

# Reranker (cross-encoder). Set RERANK_ENABLED=False to disable and fall back
# to RRF order from Qdrant.
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_ENABLED = True
# When True, the reranker scores `"{doc_title} > {section_path}\n\n{text}"`
# instead of raw `text`. Mirrors what the dense embedder sees (Child.embed_text)
# and prevents loss of section-identifying terms (e.g. product codes in
# section headers) at rerank time.
RERANK_USE_HEADER = True

# Qdrant
COLLECTION = "pincheng_docs"

# MinerU cloud API (set to use cloud parsing instead of local CLI)
MINERU_API_KEY = os.getenv("MINERU_API_KEY", "")
MINERU_API_BASE = "https://mineru.net/api/v4"
MINERU_MAX_PAGES = 200  # cloud API per-file page limit; larger PDFs are split

# LLM — Zhipu GLM via OpenAI-compatible API
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
LLM_MODEL = os.getenv("LLM_MODEL", "glm-4.6")
LLM_TEMPERATURE = 0.2
