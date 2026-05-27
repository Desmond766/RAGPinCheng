# 品成 BIM 知识库

公司内部 RAG 知识检索系统，面向 BIM 咨询业务。把多年积累的资料——**行业规范、客户标准、公司内部标准、项目资料、培训视频文字记录**——统一索引，让员工用自然语言提问，拿到带文献来源的答案（`[文档名 §章节]` 或 `[文档名 @HH:MM:SS]` 格式，可自行核对原文）。

---

## 本地快速启动

**环境要求：** Python 3.11+，Node.js 18+，约 10 GB 磁盘空间，一个在运行的 Qdrant 实例。

```bash
# 启动 Qdrant（或直接用下面的 Docker 部署方式）
docker run -d -p 6333:6333 qdrant/qdrant

# 后端
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 至少填写 ZHIPU_API_KEY、MINERU_API_KEY、ADMIN_EMPLOYEE_ID、ADMIN_PASSWORD
uvicorn api.main:app --reload --port 8000

# 前端（另开一个终端）
cd frontend && npm install && npm run dev   # 访问 http://localhost:5173
```

首次启动会从 `ADMIN_EMPLOYEE_ID` / `ADMIN_PASSWORD` 自动创建管理员账号。普通员工在 `/register` 自助注册。

如需从本地 `docs/` 目录批量建索引：

```bash
python scripts/build_index.py
```

---

## Docker 部署（自建服务器）

两个服务：`qdrant`（向量库）和 `backend`（FastAPI 同时提供 `/api/*` 和 React 前端，监听 80 端口）。

```bash
cp .env.example .env   # 填写 ZHIPU_API_KEY、MINERU_API_KEY、ADMIN_EMPLOYEE_ID、ADMIN_PASSWORD
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f backend   # 看首次模型下载进度（约 3 GB）
```

首次启动因下载 BGE-M3 + 重排序权重需 5～15 分钟。后端镜像通过多阶段构建：node 阶段执行 `npm run build` 产出 React 静态资源，Python 阶段直接挂载提供，无需独立的前端容器或 nginx 反向代理。

**访问前端**（等 `docker compose ps` 显示 `backend` 为 `healthy` 后）：

- **本机访问**（Mac/Linux 开发机）：浏览器打开 `http://localhost/`。
- **远端服务器**：浏览器打开 `http://<服务器 IP>/`（compose 已把容器 8000 端口映射到宿主 80 端口）。局域网内也可用主机名（macOS/Bonjour 下 `http://<hostname>.local/`）。
- **首次登录**：使用 `.env` 中填写的 `ADMIN_EMPLOYEE_ID` / `ADMIN_PASSWORD` —— 该账号在首次启动时自动写入数据库。登录后在 `/admin` 管理面板可创建其他用户。
- **健康检查**：在 SPA 可用前可先访问 `curl http://localhost/api/health`，应返回 `{"status":"ok"}`。
- **HTTPS**：容器内只跑 HTTP（容器 8000 → 宿主 80）。生产环境请在前面架一层反向代理（Caddy、nginx、Cloudflare Tunnel、云厂商 LB 均可），并保持 `.env` 中 `SESSION_COOKIE_SECURE=true`（默认值）。

> 环境变量加载：仓库根目录的 `.env` 是唯一真相，`docker/.env` 是它的软链接。这样 Compose v2 在 project 目录下的 `.env` 自动发现（用于解析 YAML 中的 `${VAR}`，例如 `BUILD_PLATFORM`）能命中；后端服务又通过 `env_file: - ../.env` 把所有 key 注入容器运行时。新克隆仓库后请重建软链接：`ln -s ../.env docker/.env`。

**建立初始索引**（仅在直接往文件系统里放文件时需要）：

```bash
docker compose -f docker/docker-compose.yml exec backend python scripts/build_index.py
```

**更新代码：**

```bash
git pull && docker compose -f docker/docker-compose.yml build && docker compose -f docker/docker-compose.yml up -d
```

**常用环境变量**（写在 `.env` 里）：
- `ZHIPU_API_KEY` — 生成答案必需
- `MINERU_API_KEY` — 强烈建议配置；云端解析 PDF 约 1 分钟，本地 CLI 需 30 分钟以上
- `ADMIN_EMPLOYEE_ID` / `ADMIN_PASSWORD` — 首次启动时创建管理员
- `SESSION_COOKIE_SECURE` — 纯 HTTP 开发环境设为 `false`
- `HF_ENDPOINT=https://hf-mirror.com` — 访问不了 huggingface.co 时用镜像
- `LLM_MODEL` / `LLM_REWRITE_MODEL` — 覆盖默认模型（`glm-4.7-flashx` / `glm-4.5-air`）

---

## 新增资料

**通过管理后台**（`/admin` → 资料管理 → 上传资料）：直接在浏览器里上传 `.pdf` 或 `.md` 文件。PDF 会自动经 MinerU 解析、分块、向量化，进度在"索引任务"表格里实时显示。无需登录服务器。

**通过文件系统 + 命令行**（批量导入）：

```bash
cp 新规范.pdf docs/行业规范/
python scripts/build_index.py   # 增量更新，只处理新文件
```

资料分类由 `docs/` 下的第一级目录名决定。只有 `客户标准` 使用二级目录（`客户标准/<客户名>/`）。`教学视频/` 下的 `.md` 文件按视频转写格式处理（按发言段落切分，引用带时间戳）；其它目录下的 `.md` 当普通 Markdown 文档处理。

---

## 调试

```bash
# 只看检索结果，不调 LLM，不需要 API Key
python scripts/test_retrieve.py "Q345 钢手工焊用什么焊条？"

# 完整 RAG 链路，带调试信息（需要 ZHIPU_API_KEY）
python scripts/eval_query.py "Q345 钢手工焊用什么焊条？"
# 跑完第一轮进入 REPL：/reset /history /full /short /exit
```

---

## 评估

`src/eval/` 里有一套检索评估框架（约 97 条标注，涵盖六种题型）。

```bash
python scripts/run_eval_retrieval.py                              # 输出 R@1、R@5、MRR@5
python scripts/diff_eval_runs.py <基准>.jsonl <候选>.jsonl        # 对比两次结果
```

当前基线（2026 年 5 月）：**R@1 = 90%，R@5 = 96%，拒答合规率 = 100%**。

---

## 系统原理

```
PDF / .md                   解析后 markdown    分块          向量              回答
docs/<分类>/   →(1)→   data/parsed/   →(2)→  父块+   →(3)→ Qdrant +   →(4)→ GLM-4
               MinerU                          子块       SQLite            带引用
                                                          BGE-M3 + 重排序
```

1. **解析** — PDF 经 MinerU 转为 markdown；`.md` 文件跳过此步。
2. **分块** — `chunk.py` 按 Markdown 标题切父块（1200 字）/ 子块（256 字）。表格、公式整体保留不拆；转写文字记录按发言段落切，每块带 `HH:MM:SS` 时间戳。
3. **向量化 + 索引** — BGE-M3 一次生成密集 + 稀疏向量 → 写入 Qdrant（服务器模式）；父块原文存入 `data/parents.sqlite`。
4. **检索 + 精排 + 生成** — 密集+稀疏 RRF 混合检索，遇到规范编号（如 GB 50017）额外触发精确匹配补召，BGE-reranker-v2-m3 精排，最终由智谱 GLM-4 按格式生成答案并引用来源。

**多轮对话**（`src/session.py`）：对追问自动改写为独立问题；继承上轮 top-2 来源；上下文预算随历史增长动态收缩。

**HTTP 层**（`api/`）：FastAPI + SSE 流式输出，服务端 session cookie 鉴权（`pc_sid`），变更操作需带 CSRF Token。生产环境下同一个 FastAPI 进程也负责在 `/` 提供 React 静态资源（通过 `SPAStaticFiles` 实现客户端路由的 index.html 回退）。管理接口涵盖用户管理、对话查看、反馈日志，以及文档上传/索引任务队列。

架构细节和注意事项见 `CLAUDE.md`。
