# 品成 BIM 知识库

公司内部的 RAG 知识检索系统，面向 BIM 咨询业务。把多年积累的资料——**行业规范、客户标准、公司内部标准、项目资料、培训视频文字记录**——统一索引，让员工可以用自然语言直接提问，并拿到带文献来源的准确答案。

比如问"Q345 钢手工焊用哪个型号焊条？"或者"插座距地 300mm 建模时高度应输入多少？"，系统会从实际文档里找到依据，答案附带 `[文档名 §章节]` 或 `[文档名 @HH:MM:SS]` 格式的引用，可以自行核对原文。

多轮对话也支持——追问"那 Q390 呢？"这种上下文依赖的问题，系统会自动理解指代关系。

---

## 整体流程

```
PDF / 文字记录          markdown        分块          向量               回答
─────────────────  →  ─────────────  →  ───────  →  ──────────  →  ──────────
docs/<分类>/           data/parsed/     父块 +       Qdrant +       智谱 GLM-4
                        (MinerU)         子块         SQLite        (带引用)
                                                    + BGE-M3
                                                    + 重排序
```

1. **解析**：PDF 通过 MinerU 转为 markdown（配置了 `MINERU_API_KEY` 走云端 API，否则本地 CLI）。
2. **分块**：父块（1200 字）保留上下文，子块（256 字）用于检索。表格、公式整体保持不拆分；视频文字记录按发言段落切分，每段带时间戳。
3. **向量化**：用 BGE-M3 一次性生成密集向量 + 稀疏向量。
4. **索引**：向量存入本地 Qdrant（`data/qdrant/`），父块原文存入 SQLite（`data/parents.sqlite`）。
5. **检索**：密集 + 稀疏混合检索，RRF 融合排序，再用 BGE-reranker-v2-m3 精排。查询里出现规范编号（如 GB 50017）时额外触发精确匹配补充召回。
6. **生成**：检索结果打包进提示词，智谱 GLM-4 生成中文回答，严格按格式引用，找不到答案时回复"资料中未找到相关内容。"
7. **多轮管理**：`ChatSession` 负责问题改写、上轮来源继承、动态上下文预算管理。

前端目前有两种：
- **Streamlit**（`app.py`）：本地单文件启动，适合调试。
- **FastAPI + React/Vite**（`api/` + `frontend/`）：支持 SSE 流式输出和会话管理，生产环境用这个。

---

## 部署方式选择

系统提供两种部署路径：

| | 本地（venv） | Docker |
|---|---|---|
| **适合场景** | 开发调试、个人使用 | 自建服务器、团队共用 |
| **环境要求** | Python 3.11+ venv + Node.js 18+ | Docker Engine 24+ 及 compose 插件 |
| **前端** | Streamlit 或 Vite 开发服务器 | nginx（编译后的产物，监听 80 端口） |
| **Qdrant 锁** | 同一时间只能一个进程 | 同样限制，单 uvicorn worker |

两种方式共用同一份 `.env` 密钥配置和 `data/` 目录结构。

---

## 本地部署（venv）

环境要求：macOS 或 Linux，Python 3.11+，约 10 GB 磁盘空间（模型权重 + 索引），智谱 API Key，可选 MinerU API Key，前端还需要 Node.js 18+。

### 第一步：克隆仓库并搭建 Python 环境

```bash
git clone <仓库地址> RAGPinCheng
cd RAGPinCheng

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

首次安装会拉取 PyTorch、FlagEmbedding、qdrant-client 等依赖，大概需要 5～10 分钟，下载量约 3 GB。

### 第二步：配置密钥

```bash
cp .env.example .env
# 编辑 .env，至少填写：
#   ZHIPU_API_KEY=...              （生成答案必需；在 bigmodel.cn 申请）
#   MINERU_API_KEY=...             （可选；云端解析 PDF 更快，强烈建议配置）
```

不配置 `ZHIPU_API_KEY` 也能跑检索部分，用 `scripts/test_retrieve.py` 验证索引是否正常即可。

### 第三步：整理资料目录

`docs/` 下的子目录名就是资料分类标签，建议按以下结构放：

```
docs/
├── 行业规范/          # 国标、行标等规范文件（PDF）
├── 客户标准/          # 甲方要求（PDF）
├── 公司内部标准/      # 内部规程（PDF）
├── 项目资料/          # 历史项目文件、复盘记录（PDF）
└── 教学视频/          # MinerU 导出的视频文字记录（.md 文件）
```

视频文字记录文件放在 `docs/教学视频/` 目录下，扩展名为 `.md`，文件名没有格式限制。系统从文件正文中的 `说话人 N HH:MM:SS` 行读取时间戳；视频标题从文件第一行的 `**文字记录：<标题>**` 读取，文件名只是解析失败时的备用。文件名以 `智能纪要：` 开头的摘要文件会被自动跳过——索引只需要原始文字记录，这样每条引用才能带时间戳。

### 第四步：建立索引

```bash
python scripts/build_index.py
```

首次构建（以三本较大手册 + 若干文字记录为例）：
- 配置了 `MINERU_API_KEY`：约 5～10 分钟（云端解析）。
- 未配置：30 分钟以上（本地 CPU 解析）。

索引默认是**增量更新**的。往 `docs/<分类>/` 丢新文件后重新跑，只有新文件会被处理，已有内容不动（基于确定性的 UUIDv5 ID，同一内容的向量会原地覆盖）。如果改了分块逻辑或嵌入模型，需要加 `--reset` 重建：

```bash
python scripts/build_index.py --reset
```

### 第五步：启动前端

**Streamlit 版**（最简单）：

```bash
streamlit run app.py
# 访问 http://localhost:8501
```

**FastAPI + React 版**：

```bash
# 终端 A — 后端
uvicorn api.main:app --reload --port 8000

# 终端 B — 前端
cd frontend
npm install        # 首次需要
npm run dev        # 访问 http://localhost:5173
```

> **注意**：`data/qdrant/` 同一时间只能被一个进程打开。Streamlit 和 FastAPI 不能同时跑，选一个用。

---

## Docker 部署（自建服务器）

仓库自带一套两容器的 Docker 部署方案，目标是 Linux x86_64 自建服务器。下面这些操作面向负责运维这套系统的工程师。

> **Apple Silicon Mac 用户**：默认构建目标是 `linux/amd64`（面向生产服务器）。在 arm64 Mac 上本地测试时，在 `.env` 里加一行 `BUILD_PLATFORM=linux/arm64` 再构建，速度会快很多（避免 QEMU 模拟）。注意这个镜像跑不了 x86 服务器，生产环境构建时不要设这个。

### 部署结构

| 容器 | 基础镜像 | 职责 |
|---|---|---|
| `backend` | `python:3.11-slim` | FastAPI + ChatSession + BGE-M3 + 重排序模型，单 uvicorn worker，不对宿主机暴露端口 |
| `frontend` | `nginx:1.27-alpine`（多阶段构建，使用 `node:20-alpine` 编译前端资源） | 提供 React 静态资源，并把 `/api/*` 反向代理到后端，已针对 SSE 流式调优。对宿主机暴露 80 端口 |

**数据存放位置：**

- **镜像内**（不可变产物）：代码、Python 依赖、nginx 配置。代码变更时才需要重建。
- **绑定挂载 `./data` → `/app/data`**：Qdrant 索引、`parents.sqlite`、解析后的 markdown 缓存、`feedback.jsonl`。随语料增长。**这是系统的有状态数据。**
- **绑定挂载 `./docs` → `/app/docs`**：源 PDF + 文字记录。只在跑 `scripts/build_index.py` 时被读取，运行中的 API 不会用到。
- **命名卷 `hf_cache`**：BGE-M3 + 重排序权重（约 3 GB）。首次启动容器时下载，之后持久保留。

Streamlit (`app.py`) 和 `mineru[core]`（本地 PDF 解析 CLI）**没有**打进生产镜像。生产环境 MinerU 走云端 API。

### 服务器准备

- Linux x86_64（Ubuntu 22.04+ / Debian 12 / RHEL 9 等均可）
- Docker Engine 24+，启用 `docker compose` 插件
- 至少 10 GB 空闲磁盘（镜像 + 模型缓存），再加语料本身的空间
- 80 端口空闲（或者在 `docker-compose.yml` 里改 `ports:` 映射）
- 能访问 `bigmodel.cn`（LLM）和 `huggingface.co`（模型权重，或用下面的 `HF_ENDPOINT` 镜像）

### 首次部署

```bash
# 1. 把代码拉到服务器上
git clone <仓库地址> /srv/pincheng-rag
cd /srv/pincheng-rag

# 2. 配置密钥 —— 格式跟本地 .env 一样
cat > .env <<'EOF'
ZHIPU_API_KEY=...
MINERU_API_KEY=...
LLM_MODEL=glm-4.7-flashx
# 可选：仅用于问题改写步骤的轻量模型，不填则默认 glm-4.5-air
# LLM_REWRITE_MODEL=glm-4.5-air
# 服务器访问不到 huggingface.co 时取消注释下面这行：
# HF_ENDPOINT=https://hf-mirror.com
EOF

# 3. 准备数据 —— 二选一：

# 3a. 把本地已建好的索引同步到服务器：
rsync -av --progress \
    /Users/you/Codes/RAGPinCheng/data/ \
    user@server:/srv/pincheng-rag/data/
rsync -av --progress \
    /Users/you/Codes/RAGPinCheng/docs/ \
    user@server:/srv/pincheng-rag/docs/

# 3b. 或者在服务器上从零开始建索引：
#     先把 PDF 放进 ./docs/<分类>/，然后：
docker compose -f docker/docker-compose.yml run --rm backend python scripts/build_index.py

# 4. 启动系统
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d

# 5. 看首次启动的模型下载（约 3 GB，5～15 分钟，取决于网速）
docker compose -f docker/docker-compose.yml logs -f backend
```

`docker compose -f docker/docker-compose.yml ps` 看到 `backend` 状态为 `healthy` 后，访问 `http://<服务器 IP>/` 即可。

### 什么时候要跑 `build_index.py`（关键）

**只在 `docs/` 里新增、替换、删除原始文档时需要跑。** 它**不是**容器启动流程的一部分——后端启动时直接读取磁盘上已有的 `data/qdrant/` 和 `parents.sqlite`。如果它们是空的，API 能起来，但任何提问都会得到"资料中未找到相关内容。"——直到你建好索引。

操作命令：

```bash
# 把新 PDF 复制到服务器上的 docs/<分类>/ 后：
docker compose -f docker/docker-compose.yml exec backend python scripts/build_index.py

# 完全重建（改了分块或嵌入逻辑时）：
docker compose -f docker/docker-compose.yml exec backend python scripts/build_index.py --reset
```

索引默认是**增量更新**——只有新内容会被解析和向量化。已有条目根据确定性的 UUIDv5 ID 原地覆盖，所以重复执行也是安全幂等的。API 正在服务请求时也能跑索引：Qdrant 文件模式用的是短连接客户端，读写短暂共存没问题。但如果一次索引非常大（几百份新 PDF），建议先停 API 避免文件锁竞争：

```bash
docker compose -f docker/docker-compose.yml stop backend
docker compose -f docker/docker-compose.yml run --rm backend python scripts/build_index.py
docker compose -f docker/docker-compose.yml start backend
```

### 日常运维

```bash
# 跟踪日志
docker compose -f docker/docker-compose.yml logs -f backend
docker compose -f docker/docker-compose.yml logs -f frontend

# 单独重启某个服务（不影响另一个）
docker compose -f docker/docker-compose.yml restart backend

# 全部停止（磁盘上的数据保留）
docker compose -f docker/docker-compose.yml down

# 全部启动
docker compose -f docker/docker-compose.yml up -d

# 部署新代码
git pull
docker compose -f docker/docker-compose.yml build         # 改动过的层会重新构建
docker compose -f docker/docker-compose.yml up -d         # 用新镜像重建容器
                             # 绑定挂载的 data/ 和 docs/ 完全不动

# 进入后端容器调试
docker compose -f docker/docker-compose.yml exec backend bash

# 看资源占用
docker stats

# 查看镜像大小
docker images | grep pincheng-rag
```

### 备份

有状态的数据全在两个绑定挂载目录里，**不需要 dump 数据库**，文件级快照就够：

```bash
# 先停后端，避免 Qdrant 在快照过程中被写入
docker compose -f docker/docker-compose.yml stop backend

# 打包 data 目录（Qdrant + SQLite + 解析缓存 + feedback）
tar czf pincheng-data-$(date +%Y%m%d).tar.gz data/

# 想的话再打包源文档
tar czf pincheng-docs-$(date +%Y%m%d).tar.gz docs/

docker compose -f docker/docker-compose.yml start backend
```

`hf_cache` 命名卷里只是从 HuggingFace 下载的权重，丢了再 `docker compose up` 会自动重新下载，不用备份。

### HuggingFace 镜像（受限网络）

服务器访问不了 `huggingface.co` 时（国内常见），在 `.env` 里加一行：

```
HF_ENDPOINT=https://hf-mirror.com
```

然后 `docker compose -f docker/docker-compose.yml up -d`，首次启动下模型自动走镜像，代码完全不用改。默认指向官方 CDN，不设置就走官方。

### TLS / HTTPS

前端容器只在 80 端口提供明文 HTTP。需要 HTTPS 时，在前面放一台公司的反向代理（nginx、Caddy、Traefik 都行）做 TLS 终止。compose 这边刻意不管证书。

### 部署常见坑

- **首次启动 5～15 分钟看着像卡住**：实际上是在下 BGE-M3 + 重排序模型到 `hf_cache`。healthcheck 的 `start_period` 已经设到 15 分钟。看日志能确认进度，应该会看到 "warming embed model (BGE-M3)..." 然后 "api ready"。
- **`docker compose -f docker/docker-compose.yml down -v` 会清掉模型缓存**：`-v` 参数会删除命名卷。日常停启用 `docker compose -f docker/docker-compose.yml down` 就够了。
- **在 Mac（arm64）上构建会得到 x86 镜像**：Dockerfile 里写死了 `--platform=linux/amd64`。Mac 上构建会因为模拟变慢，但产出的镜像能在 x86 服务器上原生跑。生产环境最好直接在服务器上构建。
- **`scripts/build_index.py` 也可以在宿主机的 venv 里跑**——因为 `data/` 是绑定挂载的。但建议都在容器里跑，少踩"venv 没激活对"这类坑。
- **后端 8000 端口不对宿主机暴露**：调试时需要直接 curl 后端的话，临时在 `docker-compose.yml` 的 backend 服务里加 `ports: ["8000:8000"]`，再 `docker compose -f docker/docker-compose.yml up -d` 即可。

---

## 日常使用

### 提问

系统用中文作答，每个结论都有来源。两种引用格式：

- `[文档名 §章节路径]`：PDF 文件
- `[文档名 @HH:MM:SS]`：视频文字记录

找不到答案时，系统会明确说"资料中未找到相关内容。"而不是胡编。

### 新增资料

```bash
cp 新规范.pdf docs/行业规范/
python scripts/build_index.py
# 只处理新文件，存量数据不受影响
```

### 重命名分类目录

改了 `docs/` 下的目录名后，Qdrant 里已有的 payload 仍是旧标签，需要跑维护脚本同步：

```bash
python scripts/migrate_categories.py --dry-run   # 预览变更
python scripts/migrate_categories.py             # 执行
```

### 调试某个问题

```bash
# 只看检索结果，不调 LLM，不需要 API Key
python scripts/test_retrieve.py "Q345 钢手工焊用什么焊条？"

# 完整 RAG 链路，带调试信息（需要 ZHIPU_API_KEY）
python scripts/eval_query.py "Q345 钢手工焊用什么焊条？"
# 跑完第一轮后进入 REPL，支持 /reset /history /full /short /exit
```

---

## 评估

`src/eval/` 里有一套基于标注集的检索评估框架，用来量化调参效果。

```bash
# 从索引里按类型抽样（已有 src/eval/sampled_parents.json）
python scripts/sample_for_eval.py

# 人工标注好的黄金集在 src/eval/golden.jsonl（约 97 条，涵盖六种题型）

# 跑一次评估（需要 ZHIPU_API_KEY）
python scripts/run_eval_retrieval.py
# 输出各题型的 Recall@1、Recall@5、MRR@5
# 明细记录写入 src/eval/runs/run_<时间戳>.jsonl

# 对比两次结果，看哪些变好了、哪些退步了
python scripts/diff_eval_runs.py \
    src/eval/runs/run_<基准>.jsonl \
    src/eval/runs/run_<候选>.jsonl
```

当前基线（2026 年 5 月，97 条）：**R@1 = 90%，R@5 = 96%，拒答合规率 = 100%**。任何调参改动低于这个数字都要先查清原因再合并。

---

## 目录结构

```
RAGPinCheng/
├── app.py                  # Streamlit 界面（仅本地调试，不打进生产镜像）
├── src/
│   ├── ingest.py           # PDF → markdown（MinerU）
│   ├── chunk.py            # markdown → 父块/子块
│   ├── embed.py            # BGE-M3 向量化（密集 + 稀疏）
│   ├── index.py            # Qdrant + parents.sqlite 写入
│   ├── retrieve.py         # 混合检索 + 精排
│   ├── rerank.py           # BGE-reranker-v2-m3
│   ├── generate.py         # 智谱 GLM 调用 + 提示词组装
│   ├── session.py          # 多轮会话编排（ChatSession）
│   ├── prompts.py          # 提示词加载器
│   ├── config.py           # 所有可调参数集中在这里
│   └── eval/               # 检索评估框架 + 黄金标注集
├── api/                    # FastAPI 后端（路由、SSE、会话管理）
├── frontend/               # React + Vite 前端源码
├── docker/                 # 所有 Docker 相关文件
│   ├── docker-compose.yml  # 生产环境两容器编排
│   ├── Dockerfile.backend  # 后端镜像（FastAPI + ML 模型，CPU 版 torch）
│   ├── Dockerfile.frontend # 多阶段构建：node 编译 → nginx 提供静态资源
│   ├── nginx.conf          # 静态资源 + /api 反向代理（SSE 已调优）
│   ├── Dockerfile.backend.dockerignore
│   └── Dockerfile.frontend.dockerignore
├── prompts/                # 提示词原文（.md 文件，不要内嵌进 Python）
├── scripts/                # 各类工具脚本
├── docs/                   # 原始语料（PDF + 文字记录）
├── data/                   # 解析结果、Qdrant 索引、parents.sqlite
│   └── feedback.jsonl      # 前端点赞/踩的记录（追加写入）
├── requirements.txt        # 本地开发完整依赖（含 streamlit、mineru[core]）
└── requirements-prod.txt   # 生产镜像的精简依赖
```

架构细节和注意事项见 `CLAUDE.md`（主要给 AI 编码助手用，人读也没问题）。

---

## 常见问题

**`uvicorn` 报端口占用**：另一个进程还在跑，或者 Streamlit 持有 Qdrant 锁。用 `lsof -i :8000` 或 `lsof data/qdrant/` 找到进程，先关掉再启动。

**第一次查询等了很久**：BGE-reranker-v2-m3 权重约 600 MB，首次自动下载。下载完成后后续查询很快。

**明明有相关文档却答"资料中未找到相关内容。"**：要么文件还没建索引（确认 `data/qdrant/` 是最新的），要么文档确实没覆盖这个知识点。跑 `scripts/test_retrieve.py` 看检索层有没有召回。

**大 PDF 的 200 页限制**：MinerU 云端 API 单次提交最多 `MINERU_MAX_PAGES = 200` 页。`_cloud_parse()` 会自动处理——把大 PDF 拆成若干个 ≤200 页的分块，分批提交云端，再把各段 markdown 拼接起来。不会降级为本地解析，整个文件始终走云端。
