# Memory Palace 部署档位（Deployment Profiles）

本文档帮助你根据自己的硬件条件和使用场景，选择合适的 Memory Palace 配置档位（A / B / C / D），并完成部署。

---

## 快速导航

| 章节 | 内容 |
|---|---|
| [1. 三步上手](#1-三步上手) | 最快了解如何开始 |
| [2. 档位一览](#2-档位一览) | A/B/C/D 四种配置的区别 |
| [3. 各档位详细配置](#3-各档位详细配置) | 每种档位的 `.env` 参数说明 |
| [4. 可选 LLM 参数](#4-可选-llm-参数writeguardcompact_context) | 写入守卫与上下文压缩 |
| [5. Docker 一键部署](#5-docker-一键部署推荐) | 推荐的容器化部署方式 |
| [6. 手动启动](#6-手动启动) | 不用 Docker 的本地启动方式 |
| [7. 本地推理服务参考](#7-本地推理服务参考) | Ollama / LM Studio / vLLM / SGLang |
| [8. Vitality 参数](#8-vitality-参数) | 记忆活力衰减与清理机制 |
| [9. API 鉴权](#9-api-鉴权) | Maintenance / SSE / Browse / Review 接口安全 |
| [10. 调参与故障排查](#10-调参与故障排查) | 常见问题与调优建议 |
| [11. 辅助脚本一览](#11-辅助脚本一览) | 所有部署相关脚本 |

---

## 1. 三步上手

1. **选择档位**：根据你的硬件选择 `A`、`B`、`C` 或 `D`（不确定就选 **B**，零依赖即可运行）
2. **生成配置**：运行 `apply_profile` 脚本生成 `.env` 文件
3. **启动服务**：使用 Docker 一键部署 **或** 手动启动后端 + 前端

> **💡 新手建议**：先用 **Profile B** 跑通整个流程，熟悉后再升级到 C/D 获得更高检索精度。

---

## 2. 档位一览

| 档位 | 搜索模式 | Embedding 方式 | Reranker | 适用场景 |
|:---:|---|---|---|---|
| **A** | `keyword` | 关闭（`none`） | ❌ 关闭 | 最低配要求，纯关键词检索，快速验证 |
| **B** | `hybrid` | 本地哈希（`hash`） | ❌ 关闭 | **默认推荐**，单机开发，无需额外服务 |
| **C** | `hybrid` | API 调用（`router`） | ✅ 开启 | 本地部署 embedding/reranker 模型服务 |
| **D** | `hybrid` | API 调用（`router`） | ✅ 开启 | 使用远程 API 服务，无需本地 GPU |

**关键区别**：

- **A → B**：从纯关键词升级为混合检索，使用内置哈希向量（不依赖任何外部服务）
- **B → C/D**：接入真实的 embedding + reranker 模型，获得最佳语义检索效果
- **C vs D**：算法路径一致；默认模板中主要差异为模型服务地址（本地 vs 远程），并且默认 `RETRIEVAL_RERANKER_WEIGHT` 也不同（C=`0.30`，D=`0.35`）

> **口径说明（避免与评测文档混淆）**：部署模板里的 C 默认开启 reranker；`docs/EVALUATION.md` 的“真实 A/B/C/D 运行”里，`profile_c` 作为对照组会关闭 reranker（`profile_d` 才开启），用于观测增益。

---

## 3. 各档位详细配置

### Profile A —— 纯关键词（最低配）

零依赖，仅使用关键词匹配：

```bash
# 核心配置（参见 deploy/profiles/macos/profile-a.env）
SEARCH_DEFAULT_MODE=keyword
RETRIEVAL_EMBEDDING_BACKEND=none
RETRIEVAL_RERANKER_ENABLED=false
RUNTIME_INDEX_WORKER_ENABLED=false    # 无需索引 worker
```

### Profile B —— 混合检索 + 本地哈希（默认）

使用内置的 64 维哈希向量，提供基础语义能力：

```bash
# 核心配置（参见 deploy/profiles/macos/profile-b.env）
SEARCH_DEFAULT_MODE=hybrid
RETRIEVAL_EMBEDDING_BACKEND=hash
RETRIEVAL_EMBEDDING_MODEL=hash-v1
RETRIEVAL_EMBEDDING_DIM=64
RETRIEVAL_RERANKER_ENABLED=false
RUNTIME_INDEX_WORKER_ENABLED=true     # 开启异步索引
RUNTIME_INDEX_DEFER_ON_WRITE=true
```

### Profile C/D —— 混合检索 + 真实模型（最优效果）

C 和 D 的算法路径相同，均使用 `router` 后端调用 OpenAI-compatible API；默认模板中 D 的 reranker 权重更高（`0.35`）。

**Profile C**（本地模型服务）——适合有 GPU 或使用 Ollama/vLLM 等本地推理：

```bash
# 核心配置（参见 deploy/profiles/macos/profile-c.env）
SEARCH_DEFAULT_MODE=hybrid
RETRIEVAL_EMBEDDING_BACKEND=router

# Embedding 配置
ROUTER_API_BASE=http://127.0.0.1:PORT/v1          # ← 替换 PORT 为实际端口
ROUTER_API_KEY=replace-with-your-key
ROUTER_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
RETRIEVAL_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
RETRIEVAL_EMBEDDING_API_BASE=http://127.0.0.1:PORT/v1
RETRIEVAL_EMBEDDING_API_KEY=replace-with-your-key
RETRIEVAL_EMBEDDING_DIM=4096

# Reranker 配置
RETRIEVAL_RERANKER_ENABLED=true
RETRIEVAL_RERANKER_API_BASE=http://127.0.0.1:PORT/v1
RETRIEVAL_RERANKER_API_KEY=replace-with-your-key
RETRIEVAL_RERANKER_MODEL=Qwen/Qwen3-Reranker-8B
RETRIEVAL_RERANKER_WEIGHT=0.30                     # 推荐 0.20 ~ 0.40
```

**Profile D**（远程 API 服务）——无需本地 GPU，使用云端模型：

```bash
# 与 C 的主要区别：API 地址指向远程，且默认 reranker 权重更高
ROUTER_API_BASE=https://<your-router-host>/v1
RETRIEVAL_EMBEDDING_API_BASE=https://<your-router-host>/v1
RETRIEVAL_RERANKER_API_BASE=https://<your-router-host>/v1
RETRIEVAL_RERANKER_WEIGHT=0.35                     # 远程推荐略高
```

> **🔑 C/D 第一调参项**：`RETRIEVAL_RERANKER_WEIGHT`，建议范围 `0.20 ~ 0.40`，以 `0.05` 步长微调。

### 推荐模型选型

项目档位模板中默认配置的模型：

| 用途 | 默认模型 | 维度 | 说明 |
|---|---|---|---|
| Embedding | `Qwen/Qwen3-Embedding-8B` | 4096 | 多语言，支持中英文，精度高 |
| Reranker | `Qwen/Qwen3-Reranker-8B` | — | 高精度重排序，支持中英文 |

你也可以替换为其他 OpenAI-compatible 模型，例如 `bge-m3`、`text-embedding-3-small` 等，只需修改对应的 `*_MODEL` 和 `*_DIM` 参数。

---

## 4. 可选 LLM 参数（write_guard / compact_context）

这些参数控制两个可选的 LLM 功能：**写入守卫**（质量过滤）和**上下文压缩**（摘要生成）。

在 `.env` 中配置：

```bash
# Write Guard LLM（写入守卫，过滤低质量记忆）
WRITE_GUARD_LLM_ENABLED=false
WRITE_GUARD_LLM_API_BASE=             # OpenAI-compatible /chat/completions 端点
WRITE_GUARD_LLM_API_KEY=
WRITE_GUARD_LLM_MODEL=

# Compact Context Gist LLM（上下文压缩，生成摘要）
COMPACT_GIST_LLM_ENABLED=false
COMPACT_GIST_LLM_API_BASE=
COMPACT_GIST_LLM_API_KEY=
COMPACT_GIST_LLM_MODEL=
```

> **回退机制**：当 `COMPACT_GIST_LLM_*` 未配置时，`compact_context` 会自动回退使用 `WRITE_GUARD_LLM_*` 的配置。两条链路均使用 OpenAI-compatible chat 接口（`/chat/completions`）。

---

## 5. Docker 一键部署（推荐）

### 前置要求

- 已安装 [Docker](https://docs.docker.com/get-docker/) 并启动 Docker Engine
- 支持 `docker compose`（Docker Desktop 默认包含）

### macOS / Linux

```bash
cd <project-root>
bash scripts/docker_one_click.sh --profile b
```

### Windows PowerShell

```powershell
cd <project-root>
.\scripts\docker_one_click.ps1 -Profile b
```

### 部署完成后的访问地址

| 服务 | 宿主机默认端口 | 容器内部端口 | 访问方式 |
|---|:---:|:---:|---|
| Frontend（Web UI） | `3000` | `8080` | `http://localhost:3000` |
| Backend（API） | `18000` | `8000` | `http://localhost:18000` |
| 健康检查 | `18000` | `8000` | `http://localhost:18000/health` |

### 一键脚本做了什么

1. 调用 profile 脚本从模板生成 `.env.docker`（macOS/Linux 使用 `apply_profile.sh`，Windows 使用 `apply_profile.ps1`）
2. 自动检测端口占用，若默认端口被占用则自动递增寻找空闲端口
3. 检测是否存在历史数据卷（`memory_palace_data` 或 `nocturne_*` 系列），自动复用以保留历史数据
4. 使用 `docker compose` 构建并启动前后端容器

### 安全说明

- **Backend 容器**：以非 root 用户运行（`UID=10001`，见 `deploy/docker/Dockerfile.backend`）
- **Frontend 容器**：使用 `nginxinc/nginx-unprivileged` 镜像（默认 `UID=101`）
- Docker Compose 配置了 `security_opt: no-new-privileges:true`

### 停止服务

```bash
cd <project-root>
docker compose -f docker-compose.yml down
```

---

## 6. 手动启动

如果不使用 Docker，可以手动启动后端和前端。

### 第一步：生成 `.env` 配置

```bash
# macOS / Linux（生成 Profile C 配置）
cd <project-root>
bash scripts/apply_profile.sh macos c

# Windows PowerShell
.\scripts\apply_profile.ps1 -Platform windows -Profile c
```

> 脚本执行逻辑：复制 `.env.example` 为 `.env`，然后追加 `deploy/profiles/<platform>/profile-<x>.env` 中的覆盖参数。

### 第二步：启动后端

```bash
cd <project-root>/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 18000
```

### 第三步：启动前端

```bash
cd <project-root>/frontend
npm install
MEMORY_PALACE_API_PROXY_TARGET=http://127.0.0.1:18000 npm run dev -- --host 127.0.0.1 --port 3000
```

---

## 7. 本地推理服务参考

如果使用 Profile C，需要在本地运行 embedding/reranker 模型。以下是常用的本地推理服务：

| 服务 | 官方文档 | 硬件建议 |
|---|---|---|
| Ollama | [docs.ollama.com](https://docs.ollama.com/gpu) | CPU 可跑；GPU 推荐按模型大小匹配 VRAM |
| LM Studio | [lmstudio.ai](https://lmstudio.ai/docs/app/system-requirements) | 建议 16GB+ RAM |
| vLLM | [docs.vllm.ai](https://docs.vllm.ai/en/stable/getting_started/installation/gpu.html) | Linux-first；NVIDIA 计算能力 7.0+ |
| SGLang | [docs.sglang.ai](https://docs.sglang.ai/index.html) | 支持 NVIDIA / AMD / CPU / TPU |

**OpenAI-compatible 接口文档**：

- Ollama：[OpenAI Compatibility](https://docs.ollama.com/api/openai-compatibility)
- LM Studio：[OpenAI Endpoints](https://lmstudio.ai/docs/app/api/endpoints/openai)

> **重要**：Memory Palace 的 embedding/reranker 均通过 OpenAI-compatible API 调用。若你开启了 reranker（C/D 默认开启），后端服务除 `/v1/embeddings` 外还需要可用的 rerank 端点（默认调用 `/rerank`）。

---

## 8. Vitality 参数

Vitality（活力值）系统用于自动管理记忆生命周期：**访问强化 → 自然衰减 → 候选清理 → 人工确认**。

| 参数 | 默认值 | 说明 |
|---|:---:|---|
| `VITALITY_MAX_SCORE` | `3.0` | 活力分上限 |
| `VITALITY_REINFORCE_DELTA` | `0.08` | 每次被检索命中后增加的分数 |
| `VITALITY_DECAY_HALF_LIFE_DAYS` | `30` | 衰减半衰期（天），30 天后活力值衰减一半 |
| `VITALITY_DECAY_MIN_SCORE` | `0.05` | 衰减下限，不会降到此值以下 |
| `VITALITY_CLEANUP_THRESHOLD` | `0.35` | 活力分低于此值的记忆列为清理候选 |
| `VITALITY_CLEANUP_INACTIVE_DAYS` | `14` | 不活跃天数阈值，配合活力分判定清理候选 |
| `RUNTIME_VITALITY_DECAY_CHECK_INTERVAL_SECONDS` | `600` | 衰减检查间隔（秒），默认 10 分钟 |
| `RUNTIME_CLEANUP_REVIEW_TTL_SECONDS` | `900` | 清理确认窗口（秒），默认 15 分钟 |
| `RUNTIME_CLEANUP_REVIEW_MAX_PENDING` | `64` | 最大待确认清理数 |

**调参建议**：

1. 先保持默认值，观察 1~2 周后再调整
2. 如果清理候选过多 → 提高 `VITALITY_CLEANUP_THRESHOLD` 或 `VITALITY_CLEANUP_INACTIVE_DAYS`
3. 如果确认窗口太短 → 调大 `RUNTIME_CLEANUP_REVIEW_TTL_SECONDS`

---

## 9. API 鉴权

以下接口受 `MCP_API_KEY` 保护（**fail-closed**：未配置 key 时默认返回 `401`）：

- `GET/POST/DELETE /maintenance/*`
- `POST/PUT/DELETE /browse/*` 与 `GET/POST/DELETE /review/*`
- SSE 接口（`/sse` 与 `/messages`，由 `run_sse.py` 启动）

### 请求头格式（二选一）

```
X-MCP-API-Key: <你的 MCP_API_KEY>
Authorization: Bearer <你的 MCP_API_KEY>
```

### 本地调试放行

设置 `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true` 可在本地调试时跳过鉴权：

- 仅对 loopback 请求生效（`127.0.0.1` / `::1` / `localhost`）
- 非 loopback 请求仍返回 `401`（附带 `reason=insecure_local_override_requires_loopback`）

> **MCP stdio 模式**不经过 HTTP/SSE 鉴权中间层，因此不受此限制。

### 前端访问受保护接口

通过运行时注入 API Key（不建议在构建变量中写死）：

```html
<script>
  window.__MEMORY_PALACE_RUNTIME__ = {
    maintenanceApiKey: "<MCP_API_KEY>",
    maintenanceApiKeyMode: "header"   // 或 "bearer"
  };
</script>
```

> 也兼容旧字段名：`window.__MCP_RUNTIME_CONFIG__`

### SSE 启动示例

```bash
HOST=127.0.0.1 PORT=8010 python run_sse.py
```

---

## 10. 调参与故障排查

### 常见问题

| 问题 | 原因与解决 |
|---|---|
| 检索效果差 | 确认 `SEARCH_DEFAULT_MODE` 是否为 `hybrid`；C/D 档位检查 `RETRIEVAL_RERANKER_WEIGHT` 是否合理 |
| 模型服务不可用 | 系统会自动降级，检查响应中的 `degrade_reasons` 字段定位具体原因 |
| Docker 端口冲突 | 一键脚本会自动寻找空闲端口；也可手动指定（bash：`--frontend-port` / `--backend-port`，PowerShell：`-FrontendPort` / `-BackendPort`） |
| SSE 启动失败 `address already in use` | 释放占用的端口，或通过 `PORT=<空闲端口>` 切换 |
| 升级后数据库丢失 | 后端启动时会自动从历史文件名（`agent_memory.db` / `nocturne_memory.db` / `nocturne.db`）恢复 |

### 调参提示

1. **`RETRIEVAL_RERANKER_WEIGHT`**：过高会过度依赖重排序模型，建议以 `0.05` 步长调试
2. **Docker 数据持久化**：默认使用 `memory_palace_data` 卷（见 `docker-compose.yml`）
3. **旧版兼容**：一键脚本自动识别旧版 `NOCTURNE_*` 环境变量和历史数据卷
4. **迁移锁**：`DB_MIGRATION_LOCK_FILE`（默认 `<db_file>.migrate.lock`）和 `DB_MIGRATION_LOCK_TIMEOUT_SEC`（默认 `10` 秒）用于防止多进程并发迁移冲突

---

## 11. 辅助脚本一览

| 脚本 | 说明 |
|---|---|
| `scripts/apply_profile.sh` | 从模板生成 `.env`（macOS / Linux） |
| `scripts/apply_profile.ps1` | 从模板生成 `.env`（Windows PowerShell） |
| `scripts/docker_one_click.sh` | Docker 一键部署（macOS / Linux） |
| `scripts/docker_one_click.ps1` | Docker 一键部署（Windows PowerShell） |

### 配置模板文件结构

```
deploy/profiles/
├── macos/
│   ├── profile-a.env
│   ├── profile-b.env
│   ├── profile-c.env
│   └── profile-d.env
├── windows/
│   ├── profile-a.env
│   ├── profile-b.env
│   ├── profile-c.env
│   └── profile-d.env
└── docker/
    ├── profile-a.env
    ├── profile-b.env
    ├── profile-c.env
    └── profile-d.env
```
