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

1. **选择档位**：根据你的硬件选择 `A`、`B`、`C` 或 `D`（不确定就先选 **B** 跑通；要长期用、且模型服务已就绪时优先上 **C**）
2. **生成配置**：运行 `apply_profile` 脚本生成 `.env` 文件
3. **启动服务**：使用 Docker 一键部署 **或** 手动启动后端 + 前端

> **💡 建议口径**：**Profile B 仍是默认起步档位**，因为它零外部依赖；但只要你已经准备好模型服务，**Profile C 是强烈推荐档位**。升级到 C 前，请确认你会在 `.env` 的相应位置填写 embedding / reranker；如果还要启用 LLM 辅助能力，再继续填写对应的 LLM 配置。

---

## 2. 档位一览

| 档位 | 搜索模式 | Embedding 方式 | Reranker | 适用场景 |
|:---:|---|---|---|---|
| **A** | `keyword` | 关闭（`none`） | ❌ 关闭 | 最低配要求，纯关键词检索，快速验证 |
| **B** | `hybrid` | 本地哈希（`hash`） | ❌ 关闭 | **默认起步档位**，单机开发，无需额外服务 |
| **C** | `hybrid` | API 调用（`router`） | ✅ 开启 | **强烈推荐档位**，本地部署 embedding/reranker 模型服务 |
| **D** | `hybrid` | API 调用（`router`） | ✅ 开启 | 使用远程 API 服务，无需本地 GPU |

**关键区别**：

- **A → B**：从纯关键词升级为混合检索，使用内置哈希向量（不依赖任何外部服务）
- **B → C/D**：接入真实的 embedding + reranker 模型，获得最佳语义检索效果
- **C vs D**：算法路径一致；默认模板中主要差异为模型服务地址（本地 vs 远程），并且默认 `RETRIEVAL_RERANKER_WEIGHT` 也不同（C=`0.30`，D=`0.35`）

> **口径说明（避免与评测文档混淆）**：部署模板里的 C 默认开启 reranker；`docs/EVALUATION.md` 的“真实 A/B/C/D 运行”里，`profile_c` 作为对照组会关闭 reranker（`profile_d` 才开启），用于观测增益。
>
> **补充说明**：C/D 模板默认走 `router` 路线；如果你的部署不使用统一 router，也可以直接配置 `RETRIEVAL_EMBEDDING_*`、`RETRIEVAL_RERANKER_*`、`WRITE_GUARD_LLM_* / COMPACT_GIST_LLM_*` 连接 OpenAI-compatible 服务。
>
> **为什么不强制一切都走 router**：
> - `embedding`、`reranker`、`llm` 三条链路的模型、地址、密钥和故障模式不同，分开配置更便于定位和替换。
> - 当前仓库已经支持分别直配：`RETRIEVAL_EMBEDDING_*`、`RETRIEVAL_RERANKER_*`、`WRITE_GUARD_LLM_* / COMPACT_GIST_LLM_*` 均可独立工作。
> - `router` 的主要价值在生产侧：统一入口、模型编排、鉴权、限流、审计和后续 provider 切换；它适合作为**默认模板口径**，但不是唯一支持方式。
>
>
> **配置优先级说明（避免误配）**：
> - `RETRIEVAL_EMBEDDING_BACKEND` 只影响 Embedding 链路，不影响 Reranker。
> - Reranker 没有 `RETRIEVAL_RERANKER_BACKEND` 开关；是否启用仅由 `RETRIEVAL_RERANKER_ENABLED` 控制。
> - Reranker 的地址/密钥优先读取 `RETRIEVAL_RERANKER_API_BASE/API_KEY`，缺失时才回退 `ROUTER_API_BASE/ROUTER_API_KEY`，最后回退 `OPENAI_BASE_URL/OPENAI_API_BASE` 与 `OPENAI_API_KEY`。

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

### Profile C/D —— 混合检索 + 真实模型（推荐目标；C 为强烈推荐）

C 和 D 的算法路径相同，均使用 `router` 后端调用 OpenAI-compatible API；默认模板中 D 的 reranker 权重更高（`0.35`）。

> **先说结论**：
> - **Profile B**：默认起步，先保证你今天就能跑起来
> - **Profile C**：强烈推荐，只要你已经有可用的模型服务
> - **Profile D**：远程 API / 客户环境
>
> **升级到 Profile C 前最少要准备什么**：
> - Embedding：`RETRIEVAL_EMBEDDING_*`
> - Reranker：`RETRIEVAL_RERANKER_*`
> - 如果你还想启用 LLM 辅助的 write guard / gist / intent routing：再填写 `WRITE_GUARD_LLM_*`、`COMPACT_GIST_LLM_*`、可选的 `INTENT_LLM_*`

**Profile C**（本地模型服务）——适合有 GPU 或使用 Ollama/vLLM 等本地推理：

```bash
# 核心配置（参见 deploy/profiles/macos/profile-c.env）
SEARCH_DEFAULT_MODE=hybrid
RETRIEVAL_EMBEDDING_BACKEND=router

# Embedding 配置
ROUTER_API_BASE=http://127.0.0.1:PORT/v1          # ← 替换 PORT 为实际端口
ROUTER_API_KEY=replace-with-your-key
ROUTER_EMBEDDING_MODEL=<your-embedding-model-id>
RETRIEVAL_EMBEDDING_MODEL=<your-embedding-model-id>
RETRIEVAL_EMBEDDING_API_BASE=http://127.0.0.1:PORT/v1
RETRIEVAL_EMBEDDING_API_KEY=replace-with-your-key
RETRIEVAL_EMBEDDING_DIM=4096

# Reranker 配置
RETRIEVAL_RERANKER_ENABLED=true
RETRIEVAL_RERANKER_API_BASE=http://127.0.0.1:PORT/v1
RETRIEVAL_RERANKER_API_KEY=replace-with-your-key
RETRIEVAL_RERANKER_MODEL=<your-reranker-model-id>
RETRIEVAL_RERANKER_WEIGHT=0.30                     # 推荐 0.20 ~ 0.40
```

如果你不使用统一 `router`，也可以直接配置 OpenAI-compatible embedding / reranker 服务：

```bash
# 直连 OpenAI-compatible 服务
RETRIEVAL_EMBEDDING_BACKEND=api
RETRIEVAL_RERANKER_ENABLED=true
RETRIEVAL_RERANKER_API_BASE=http://127.0.0.1:PORT/v1
RETRIEVAL_RERANKER_API_KEY=replace-with-your-key
# 下面两项按你的服务实际模型名填写
RETRIEVAL_EMBEDDING_MODEL=<your-embedding-model-id>
RETRIEVAL_RERANKER_MODEL=<your-reranker-model-id>
# 注意：不存在 RETRIEVAL_RERANKER_BACKEND 配置项
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
>
> **模型 ID 提醒**：当前推荐写法是 `Qwen/Qwen3-Embedding-8B` 与 `Qwen/Qwen3-Reranker-8B`。如果你的 provider 要求不同写法，请保持同一模型家族，并改成你自己的 provider 实际 model id。

如果你采用直连方式，最小验证步骤如下：

```bash
# 1) 按你的最终配置启动对应档位
bash scripts/docker_one_click.sh --profile c

# 2) 复验基础接口
curl -fsS http://127.0.0.1:18000/health
curl -fsS http://127.0.0.1:18000/browse/node -H "X-MCP-API-Key: <YOUR_MCP_API_KEY>"
```

结果判定口径：

1. 请只拿**同一套最终部署配置**做对比和验收，不要混用不同链路的结果。
2. 无论你走 `router` 还是直连，都应在最终配置下通过启动 + 健康检查。
3. 若占位 endpoint/key 下启动失败，属于预期 fail-closed；请替换成真实可用值后再复验。

### 推荐模型选型

项目档位模板中默认配置的模型：

| 用途 | 默认模型 | 维度 | 说明 |
|---|---|---|---|
| Embedding | `Qwen/Qwen3-Embedding-8B` | 4096 | 多语言，支持中英文，精度高 |
| Reranker | `Qwen/Qwen3-Reranker-8B` | — | 高精度重排序，支持中英文 |

你也可以替换为其他 OpenAI-compatible 模型，例如 `bge-m3`、`text-embedding-3-small` 等，只需修改对应的 `*_MODEL` 和 `*_DIM` 参数。

---

## 4. 可选 LLM 参数（write_guard / compact_context / intent）

这些参数控制两个可选的 LLM 功能：**写入守卫**（质量过滤）和**上下文压缩**（摘要生成）。

在 `.env` 中配置：

```bash
# Write Guard LLM（写入守卫，过滤低质量记忆）
WRITE_GUARD_LLM_ENABLED=false
WRITE_GUARD_LLM_API_BASE=             # OpenAI-compatible /chat/completions 端点
WRITE_GUARD_LLM_API_KEY=
WRITE_GUARD_LLM_MODEL=gpt-5.4

# Compact Context Gist LLM（上下文压缩，生成摘要）
COMPACT_GIST_LLM_ENABLED=false
COMPACT_GIST_LLM_API_BASE=
COMPACT_GIST_LLM_API_KEY=
COMPACT_GIST_LLM_MODEL=gpt-5.4

# Intent LLM（实验性意图分类增强）
INTENT_LLM_ENABLED=false
INTENT_LLM_API_BASE=
INTENT_LLM_API_KEY=
INTENT_LLM_MODEL=gpt-5.4
```

> **回退机制**：当 `COMPACT_GIST_LLM_*` 未配置时，`compact_context` 会自动回退使用 `WRITE_GUARD_LLM_*` 的配置。两条链路均使用 OpenAI-compatible chat 接口（`/chat/completions`）。
>
> **推荐模型**：Embedding 使用 `Qwen/Qwen3-Embedding-8B`，Reranker 使用 `Qwen/Qwen3-Reranker-8B`，可选 LLM（write_guard / compact_context / intent）使用 `gpt-5.4`。
>
> 如果你的 provider 使用了不同的 model id 写法，请保持同一模型家族，并改成你自己的 provider 实际 model id。
>
> **补充说明**：`INTENT_LLM_*` 为实验性能力，关闭或不可用时会直接回退关键词规则，不影响默认检索路径。
>
> **完整高级配置**：`CORS_ALLOW_*`、`RETRIEVAL_MMR_*`、`INDEX_LITE_ENABLED`、`AUDIT_VERBOSE`、运行时观测/睡眠整合上限等不在本节逐项展开，统一以 `.env.example` 为准。
>
> **开启建议（推荐直接照这个来）**：
> - `INTENT_LLM_ENABLED=false`
>   - 适合默认生产 / 默认用户部署
>   - 只有在你已经有稳定 chat 模型、并且想增强模糊查询意图分类时再试
> - `RETRIEVAL_MMR_ENABLED=false`
>   - 默认先关
>   - 只有当 hybrid 检索前几条结果重复度明显偏高时，再打开看效果
> - `CORS_ALLOW_ORIGINS=`
>   - 本地开发建议留空，直接使用内建本地白名单
>   - 生产浏览器访问请显式写允许域名，不建议直接用 `*`
> - `RETRIEVAL_SQLITE_VEC_ENABLED=false`
>   - 当前仍属于 rollout 开关
>   - 普通用户部署默认不建议开；只有在维护阶段验证扩展路径、readiness 和回退链路时再启用

---

## 5. Docker 一键部署（推荐）

### 前置要求

- 已安装 [Docker](https://docs.docker.com/get-docker/) 并启动 Docker Engine
- 支持 `docker compose`（Docker Desktop 默认包含）

### macOS / Linux

```bash
cd <project-root>
bash scripts/docker_one_click.sh --profile b
# 如需把当前 shell 的 API 地址/密钥/模型注入本次运行的 Docker env 文件（默认关闭）：
bash scripts/docker_one_click.sh --profile c --allow-runtime-env-injection
```

### Windows PowerShell

```powershell
cd <project-root>
.\scripts\docker_one_click.ps1 -Profile b
# 如需把当前 PowerShell 进程环境注入本次运行的 Docker env 文件（默认关闭）：
.\scripts\docker_one_click.ps1 -Profile c -AllowRuntimeEnvInjection
```

> `apply_profile.ps1` 现已对 **所有重复 env key** 做“保留最后值”的统一去重，不再只处理 `DATABASE_URL`。
>
> 原生 Windows / `pwsh` 仍建议在目标环境单独补跑一次；这些步骤面向部署补验，不建议和新手入口文档混在一起读。
>
> `docker_one_click.sh/.ps1` 默认会为每次运行生成独立的临时 Docker env 文件，并通过 `MEMORY_PALACE_DOCKER_ENV_FILE` 传给 `docker compose`；只有显式设置该环境变量时才会复用指定路径，而不是固定共享 `.env.docker`。
>
> 如果本次 Docker env 文件里的 `MCP_API_KEY` 为空，`apply_profile.sh/.ps1` 会自动生成一把本地 key，供 Dashboard 代理和 SSE 共用。
>
> 同一 checkout 下的并发一键部署会被 deployment lock 串行化，避免共享 compose project / env 文件互相覆盖。
>
> 如果你对 `profile c/d` 开启 `--allow-runtime-env-injection`，脚本会把这次运行切到显式 API 模式，并额外强制 `RETRIEVAL_EMBEDDING_BACKEND=api`。当 `RETRIEVAL_EMBEDDING_API_*` / `RETRIEVAL_RERANKER_API_*` 没显式提供时，它会优先复用当前进程里的 `ROUTER_API_BASE/ROUTER_API_KEY` 作为兜底；如果你还设置了 `INTENT_LLM_*`，这条链路也会一并注入。

### 部署完成后的访问地址

| 服务 | 宿主机默认端口 | 容器内部端口 | 访问方式 |
|---|:---:|:---:|---|
| Frontend（Web UI） | `3000` | `8080` | `http://localhost:3000` |
| Backend（API） | `18000` | `8000` | `http://localhost:18000` |
| SSE（前端代理） | `3000` | `8080 -> 8000` | `http://localhost:3000/sse` |
| 健康检查 | `18000` | `8000` | `http://localhost:18000/health` |

### 一键脚本做了什么

1. 调用 profile 脚本从模板生成本次运行使用的 Docker env 文件（默认是 per-run 临时文件；仅当显式设置 `MEMORY_PALACE_DOCKER_ENV_FILE` 时才复用指定路径）
2. 默认禁用运行时环境注入，避免隐式覆盖模板；仅在显式开关注入时才覆盖运行参数。对 `profile c/d`，注入模式会额外强制 `RETRIEVAL_EMBEDDING_BACKEND=api` 用于本地联调；若显式 `RETRIEVAL_*` 未提供，则优先复用 `ROUTER_API_BASE/ROUTER_API_KEY` 作为 embedding / reranker API base+key 的兜底来源，并同步透传可选的 `INTENT_LLM_*`。
3. 自动检测端口占用，若默认端口被占用则自动递增寻找空闲端口
4. 检测并注入 Docker 持久化卷：数据卷默认使用 `memory_palace_data`（兼容旧 `nocturne_*` 数据卷），snapshot 卷默认使用 `memory_palace_snapshots`
5. 对同一 checkout 的并发部署加 deployment lock，避免多次 `docker_one_click` 互相覆盖
6. 使用 `docker compose` 构建并启动后端、SSE、前端三个容器

### 安全说明

- **Backend 容器**：以非 root 用户运行（`UID=10001`，见 `deploy/docker/Dockerfile.backend`）
- **Frontend 容器**：使用 `nginxinc/nginx-unprivileged` 镜像（默认 `UID=101`）
- Docker Compose 配置了 `security_opt: no-new-privileges:true`

### 停止服务

```bash
cd <project-root>
COMPOSE_PROJECT_NAME=<控制台打印出的 compose project> docker compose -f docker-compose.yml down --remove-orphans
```

> 上面的 `down --remove-orphans` 不会删除 `memory_palace_data` 和 `memory_palace_snapshots`；只有显式执行 `down -v`，或手动删除对应 volume 时，数据库和 Review snapshots 才会一起清空。

---

## 6. 手动启动

如果不使用 Docker，可以手动启动后端和前端。

### 第一步：生成 `.env` 配置

```bash
# macOS / Linux（默认先生成 Profile B 配置；Linux 也使用 `macos` 这个模板值）
cd <project-root>
bash scripts/apply_profile.sh macos b

# 如果你的 embedding / reranker 模型服务已经准备好，再切到 Profile C
# bash scripts/apply_profile.sh macos c

# Windows PowerShell
.\scripts\apply_profile.ps1 -Platform windows -Profile b

# 如果模型服务已经准备好，再切到 Profile C
# .\scripts\apply_profile.ps1 -Platform windows -Profile c
```

> 脚本执行逻辑：复制 `.env.example` 为 `.env`，然后追加 `deploy/profiles/<platform>/profile-<x>.env` 中的覆盖参数。
>
> `apply_profile.sh/.ps1` 当前会在生成结束后统一去重重复 env key，避免不同解析器对“同 key 多次出现”产生不一致行为。
>
> 如果你只是第一次手动跑通仓库，先从 Profile B 开始最稳；只有在 embedding / reranker 链路都已经可用时，再切到 Profile C。

### 第二步：启动后端

```bash
cd <project-root>/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
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
- `GET/POST/PUT/DELETE /browse/*` 与 `GET/POST/DELETE /review/*`
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

**本地手动启动前后端**时，如果你只是本地调试，可以通过运行时注入 API Key（不建议在构建变量中写死）：

```html
<script>
  window.__MEMORY_PALACE_RUNTIME__ = {
    maintenanceApiKey: "<MCP_API_KEY>",
    maintenanceApiKeyMode: "header"   // 或 "bearer"
  };
</script>
```

> 不要把真实 `MCP_API_KEY` 写进公开页面、共享静态资源或会交付给最终用户的 HTML 里。浏览器里可以直接读取这个全局对象。面向他人的部署更推荐走服务端代理转发，而不是把 key 暴露到前端页面。

> 也兼容旧字段名：`window.__MCP_RUNTIME_CONFIG__`

**Docker 一键部署**时，不需要把 key 写进浏览器页面：

- 前端容器会在代理层自动给 `/api/*`、`/sse`、`/messages` 带上同一把 `MCP_API_KEY`
- 这把 key 默认保存在本次运行使用的 Docker env 文件里
- 浏览器只看到代理后的结果，不会直接拿到真实 key

### SSE 启动示例

```bash
HOST=127.0.0.1 PORT=8010 python run_sse.py
```

> 这里的 `HOST=127.0.0.1` 是本机回环调试示例。若要给其他机器访问，请改成 `0.0.0.0`（或你的实际监听地址），并自行补齐 `MCP_API_KEY`、网络隔离、反向代理与 TLS 等保护。

Docker 一键部署时，直接使用：

```bash
http://localhost:3000/sse
```

---

## 10. 调参与故障排查

### 常见问题

| 问题 | 原因与解决 |
|---|---|
| 检索效果差 | 确认 `SEARCH_DEFAULT_MODE` 是否为 `hybrid`；C/D 档位检查 `RETRIEVAL_RERANKER_WEIGHT` 是否合理 |
| 模型服务不可用 | 系统会自动降级，检查响应中的 `degrade_reasons` 字段定位具体原因 |
| C/D 出现 `embedding_request_failed` / `embedding_fallback_hash` | 通常是外部 embedding/reranker 链路不可达（例如本机 router 未部署模型），不是后端主流程崩溃；按下方“C/D 降级信号快速排查”处理 |
| Docker 端口冲突 | 一键脚本会自动寻找空闲端口；也可手动指定（bash：`--frontend-port` / `--backend-port`，PowerShell：`-FrontendPort` / `-BackendPort`） |
| SSE 启动失败 `address already in use` | 释放占用的端口，或通过 `PORT=<空闲端口>` 切换 |
| 升级后数据库丢失 | 后端启动时会自动从历史文件名（`agent_memory.db` / `nocturne_memory.db` / `nocturne.db`）恢复 |

### C/D 降级信号快速排查（本地联调）

```bash
# 先检查服务是否真的起来
curl -fsS http://127.0.0.1:18000/health
```

1. 如果日志或返回结果里仍有 `embedding_request_failed` / `embedding_fallback_hash`，先检查 embedding / reranker 服务本身是否可达、API key 是否有效。
2. 直接检查真实调用端点，比只看配置文件更可靠：

```bash
curl -fsS -X POST <RETRIEVAL_EMBEDDING_API_BASE>/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"<RETRIEVAL_EMBEDDING_MODEL>","input":"ping"}'
curl -fsS -X POST <RETRIEVAL_RERANKER_API_BASE>/rerank \
  -H "Content-Type: application/json" \
  -d '{"model":"<RETRIEVAL_RERANKER_MODEL>","query":"ping","documents":["pong"]}'
```

3. 如果只是当前机器排障，可以临时改成 `RETRIEVAL_EMBEDDING_BACKEND=api` 并分别直配 embedding / reranker / llm；上线前再恢复到目标环境的 `router` 配置并复验一次。

### PowerShell / Windows 验证建议

- `scripts/apply_profile.sh` 与 `scripts/apply_profile.ps1` 都会对重复 env key 做统一去重。
- 如果你要交付 Windows 环境，建议直接在目标 Windows 机器上按同一份模板跑一次启动与 smoke。
- 主文档只保留公开可执行的步骤；目标环境专项验证建议单独记录。

### 调参提示

1. **`RETRIEVAL_RERANKER_WEIGHT`**：过高会过度依赖重排序模型，建议以 `0.05` 步长调试
2. **Docker 数据持久化**：默认同时使用 `memory_palace_data`（挂载 `/app/data`）和 `memory_palace_snapshots`（挂载 `/app/snapshots`）两个卷，分别持久化数据库与 review snapshots（见 `docker-compose.yml`）
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
