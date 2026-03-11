# Memory Palace 快速上手

本指南帮助你在 5 分钟内跑通 Memory Palace 本地开发环境或 Docker 部署。

> **Memory Palace** 是一个为 AI Agent 设计的长期记忆系统，通过 [MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 协议提供 9 个工具，让 Claude Code、Codex、Gemini CLI、OpenCode 等客户端具备持久化记忆能力；如果你接的是 `Cursor / Windsurf / VSCode-host / Antigravity` 这类 IDE 宿主，请先看 `docs/skills/IDE_HOSTS.md`。
>
> **如果你现在卡住的是 CLI 客户端里的 skill + MCP 安装，不要继续按这份排**；那条路径请直接看 `docs/skills/GETTING_STARTED.md`。

---

## 1. 环境要求

| 依赖 | 最低版本 | 检查命令 |
|---|---|---|
| Python | `3.10+` | `python3 --version` |
| Node.js | `20.19+`（或 `>=22.12`） | `node --version` |
| npm | `9+` | `npm --version` |
| Docker（可选） | `20+` | `docker --version` |
| Docker Compose（可选） | `2.0+` | `docker compose version` |

> **提示**：macOS 用户推荐使用 [Homebrew](https://brew.sh) 安装 Python 和 Node.js。Windows 用户推荐从官网下载安装包或使用 [Scoop](https://scoop.sh)。

---

## 2. 仓库结构速览

```
memory-palace/
├── backend/              # FastAPI + SQLite 后端
│   ├── main.py           # 应用入口（FastAPI 实例、/health 端点）
│   ├── mcp_server.py     # 9 个 MCP 工具实现（FastMCP）
│   ├── runtime_state.py  # 写入 Lane、索引 Worker、会话缓存
│   ├── run_sse.py        # MCP SSE 传输层（Starlette + API Key 鉴权）
│   ├── mcp_wrapper.py    # MCP 包装器
│   ├── requirements.txt  # Python 依赖清单
│   ├── requirements-dev.txt # 后端测试依赖
│   ├── db/               # 数据库 Schema、检索引擎
│   ├── api/              # HTTP 路由
│   │   ├── browse.py     # 记忆树浏览（GET /browse/node）
│   │   ├── review.py     # 审查接口（/review/*）
│   │   ├── maintenance.py# 维护接口（/maintenance/*）
│   │   └── setup.py      # 首启配置向导接口（/setup/*）
├── frontend/             # React + Vite + Tailwind Dashboard
│   ├── package.json      # 版本 1.0.1
│   └── vite.config.js    # 开发服务器 port 5173，代理到后端 8000
├── deploy/               # Docker 与 Profile 配置
│   ├── docker/           # Dockerfile.backend / Dockerfile.frontend
│   └── profiles/         # macos / windows / docker 档位模板
├── scripts/              # 运维脚本
│   ├── apply_profile.sh  # Profile 应用脚本（macOS/Linux）
│   ├── apply_profile.ps1 # Profile 应用脚本（Windows）
│   ├── docker_one_click.sh   # Docker 一键部署（macOS/Linux）
│   ├── docker_one_click.ps1  # Docker 一键部署（Windows）
├── docs/                 # 项目文档
├── .env.example          # 配置模板（包含所有可用配置项）
├── docker-compose.yml    # Compose 编排文件
└── LICENSE               # 开源许可证
```

---

<p align="center">
  <img src="images/onboarding_flow.png" width="900" alt="Memory Palace 快速开始流程图" />
</p>

> 📌 这张图只是帮助你快速记住顺序。
>
> 真正以正文命令为准：
>
> - 后端默认是 `uvicorn` 跑在 `127.0.0.1:8000`
> - 前端开发服务器默认是 `5173`
> - Docker 默认入口是 `http://127.0.0.1:3000`（Dashboard）与 `http://127.0.0.1:3000/sse`（SSE）

## 3. 本地开发（推荐先走这一条）

### Step 1：准备配置文件

```bash
cp .env.example .env
```

> 这里复制出来的是**更保守的 `.env.example` 最小模板**。它足够你先完成本地启动，但**不等于已经套用了 Profile B**。
>
> 如果你想直接使用仓库里定义好的 Profile B 默认值（例如本地 hash Embedding），请优先使用下面的 Profile 脚本；如果你继续手动改 `.env.example` 也可以，就把它理解成“从最小模板开始按需补配置”。

> **重要**：复制后请检查 `.env` 中的 `DATABASE_URL`，将路径改成你的实际路径。共享环境或接近生产的场景更推荐使用绝对路径。例如：
>
> ```
> DATABASE_URL=sqlite+aiosqlite:////absolute/path/to/memory_palace/demo.db
> ```
>
> 这条 URL 的斜杠数量是有平台差异的：`macOS / Linux` 这类绝对路径通常是 `sqlite+aiosqlite:////...`，`Windows` 盘符路径通常是 `sqlite+aiosqlite:///C:/...`。如果你是手改 `.env`，不要把这两种写法混在一起。

也可以使用 Profile 脚本快速生成带有默认配置的 `.env`：

```bash
# macOS / Linux —— 参数：平台 档位 [目标文件]
# 当前脚本接受的模板值是 macos|windows|docker；Linux 本地也使用 macos 这套模板。
bash scripts/apply_profile.sh macos b

# Windows PowerShell
.\scripts\apply_profile.ps1 -Platform windows -Profile b
```

> `deploy/profiles/*/profile-*.env` 是脚本输入模板，不是建议你直接复制使用的最终 `.env`。对用户路径来说，继续用 `apply_profile.sh/.ps1` 生成会更稳，也能自动补齐路径和去重重复 key。

> apply_profile 脚本会将 `.env.example` 复制到 `.env`（或你指定的目标文件），然后追加对应 Profile 的覆盖配置。macOS 平台还会自动检测并填充 `DATABASE_URL`。
>
> `apply_profile.sh/.ps1` 当前会在生成后统一去重重复 env key；原生 Windows / native `pwsh` 仍建议在目标环境单独补跑一次。
>
> 但要注意：**macOS / Windows 本地生成的 profile-b `.env` 不会自动补 `MCP_API_KEY`**。如果你接下来就要打开 Dashboard，或者直接调 `/browse` / `/review` / `/maintenance`、`/sse`、`/messages`，请再自行补 `MCP_API_KEY`，或仅在本机回环调试时设置 `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true`。只有 `docker` 平台的 profile 脚本会在 key 为空时自动生成一把本地 key。

#### 关键配置项说明

以下是 `.env` 中最常用的配置项（更多配置项请查看 `.env.example` 中的注释说明）：

| 配置项 | 说明 | 模板示例值 |
|---|---|---|
| `DATABASE_URL` | SQLite 数据库路径（**建议使用绝对路径**） | `sqlite+aiosqlite:////absolute/path/to/memory_palace/demo.db` |
| `SEARCH_DEFAULT_MODE` | 检索模式：`keyword` / `semantic` / `hybrid` | `keyword` |
| `RETRIEVAL_EMBEDDING_BACKEND` | 嵌入后端：`none` / `hash` / `router` / `api` / `openai` | `none` |
| `RETRIEVAL_EMBEDDING_MODEL` | Embedding 模型名 | `your-embedding-model-id` |
| `RETRIEVAL_RERANKER_ENABLED` | 是否启用 Reranker | `false` |
| `RETRIEVAL_RERANKER_API_BASE` | Reranker API 地址 | 空 |
| `RETRIEVAL_RERANKER_API_KEY` | Reranker API 密钥 | 空 |
| `RETRIEVAL_RERANKER_MODEL` | Reranker 模型名 | `your-reranker-model-id` |
| `INTENT_LLM_ENABLED` | 实验性意图 LLM 开关 | `false` |
| `RETRIEVAL_MMR_ENABLED` | hybrid 检索下的去重 / 多样性重排 | `false` |
| `RETRIEVAL_SQLITE_VEC_ENABLED` | sqlite-vec rollout 开关 | `false` |
| `MCP_API_KEY` | HTTP/SSE 接口鉴权密钥 | 空（见下方鉴权说明） |
| `MCP_API_KEY_ALLOW_INSECURE_LOCAL` | 本地调试时允许无 Key 访问（仅对直连 loopback 请求生效） | `false` |
| `CORS_ALLOW_ORIGINS` | 允许跨域访问的来源列表（留空使用本地默认） | 空 |
| `VALID_DOMAINS` | 允许的可写记忆 URI 域（`system://` 为内建只读域） | `core,writer,game,notes` |

> B 档位默认使用本地 hash Embedding 且不启用 Reranker；它仍然是**默认起步档位**。
>
> 如果你已经准备好模型服务，**更推荐尽快升级到 Profile C**：它需要你在 `.env` 中把 Embedding / Reranker 链路填好；如果还要启用 LLM 辅助的 write guard / gist / intent routing，再继续填写 `WRITE_GUARD_LLM_*`、`COMPACT_GIST_LLM_*`、可选的 `INTENT_LLM_*`。详见 [DEPLOYMENT_PROFILES.md](DEPLOYMENT_PROFILES.md)。
>
> 上表展示的是 `.env.example` 里的模板示例值；如果某些检索环境变量在运行时完全缺失，后端内部还会使用自己的回退值（例如 `hash` / `hash-v1` / `64`）。
>
> 配置语义说明：`RETRIEVAL_EMBEDDING_BACKEND` 只作用于 Embedding。Reranker 不存在 `RETRIEVAL_RERANKER_BACKEND` 开关，优先读取 `RETRIEVAL_RERANKER_*`，缺失时才回退 `ROUTER_*`（最后回退 `OPENAI_*` 的 base/key）。
>
> 更多高级选项（如 `INTENT_LLM_*`、`RETRIEVAL_MMR_*`、`RETRIEVAL_SQLITE_VEC_*`、`CORS_ALLOW_*`、运行时观测/睡眠整合开关）已写在 `.env.example`，默认保持保守值，不影响最小启动路径。
>
> 推荐默认值（直接照抄通常没问题）：
> - `INTENT_LLM_ENABLED=false`：先用内建关键词规则，少一层外部依赖
> - `RETRIEVAL_MMR_ENABLED=false`：先看原始 hybrid 结果，只有“前几条太像”时再开
> - `RETRIEVAL_SQLITE_VEC_ENABLED=false`：普通部署先保持 legacy 路径
> - `CORS_ALLOW_ORIGINS=`：本地开发留空；要开放给浏览器跨域访问时再写明确域名
>
> 上面这些模型名只是占位示例，不是项目硬依赖。Memory Palace 不绑定某个固定 provider 或模型家族；请直接改成你自己的 OpenAI-compatible 服务里实际可用的 embedding / reranker / chat model id。
>
> 如果你接下来就要在本地打开 Dashboard，或者直接用 `curl` 调 `/browse` / `/review` / `/maintenance`，建议在 `.env` 里再补一项鉴权配置（二选一）：
>
> - `MCP_API_KEY=change-this-local-key`
> - `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true`（只建议你自己机器上的回环调试时使用）

### Step 2：启动后端

```bash
cd backend
python3 -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

> 如果你接下来还要跑后端测试，再补一条：
>
> ```bash
> pip install -r requirements-dev.txt
> ```

预期输出：

```
Memory API starting...
SQLite database initialized.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

> 后端通过 `main.py` 中的 `lifespan` 上下文管理器完成初始化，包括 SQLite 数据库创建、运行时状态（Write Lane、Index Worker）启动等。
>
> 上面这条 `uvicorn main:app --host 127.0.0.1 ...` 是推荐的**本机开发**写法。
>
> 如果你改为直接运行 `python main.py`，当前默认也是绑定 `127.0.0.1:8000`，不会自动放开到 `0.0.0.0`。只有在你明确需要远程访问时，才手动改成 `0.0.0.0`（或你的实际绑定地址），并补齐 `MCP_API_KEY`、防火墙、反向代理或其他网络侧保护。

### Step 3：启动前端

```bash
cd frontend
npm install
npm run dev
```

> 前端的 i18n 依赖已经写在 `frontend/package.json` 和 `frontend/package-lock.json` 里。正常执行一次 `npm install` 即可，不需要再单独安装 `i18next`、`react-i18next` 或 `i18next-browser-languagedetector`。

预期输出：

```
VITE v7.x.x  ready in xxx ms
➜  Local:   http://127.0.0.1:5173/
```

打开浏览器访问 `http://127.0.0.1:5173`，即可看到 Memory Palace Dashboard。

如果你希望按页面逐项看 Dashboard 的按钮、字段和典型操作流程，可继续看：

- `docs/DASHBOARD_GUIDE_CN.md`

> 如果你在本地手动启动时看到右上角的 `Set API key`，这是正常现象：页面已经打开，但 `/browse/*`、`/review/*`、`/maintenance/*` 等受保护接口还没授权。现在点击这个按钮会打开**首启配置向导**，你可以只把 `MCP_API_KEY` 保存到当前浏览器，也可以在“本地 checkout + 非 Docker 运行”场景下把常见运行参数写进 `.env`。向导右上角也自带语言切换按钮，不需要先关掉弹窗才能切中文。第 5 节会继续说明本地验证方式。

> 如果你配置了 `MCP_API_KEY`，打开页面后请点右上角 `Set API key`，在向导里输入同一把 key；如果你只想先让 Dashboard 鉴权通过，优先选择“只保存 Dashboard 密钥”即可。
> 如果你启用了 `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true`，本机回环地址上的直连请求可直接访问这些受保护数据接口。

> 这个向导不会假装自己能热更新 Docker 容器里的 env / 代理配置。只要你改的是 embedding / reranker / write_guard / intent 这类后端运行参数，保存后仍然需要重启 `backend` / `sse`（Docker 路径则继续建议用 profile 脚本重启容器）。

> 当前前端默认英文；如果你更习惯中文，直接点右上角语言按钮即可切换，浏览器会记住你的选择。

> 前端开发服务器通过 `vite.config.js` 中配置的 proxy 将 `/api` 路径代理到后端 `http://127.0.0.1:8000`，因此前后端无需手动配置 CORS。

<p align="center">
  <img src="images/setup-assistant-zh.png" width="900" alt="Memory Palace 首启配置向导（中文模式）" />
</p>

<p align="center">
  <img src="images/memory-zh.png" width="900" alt="Memory Palace 中文界面示例" />
</p>

---

## 4. Docker 部署

### 4.1 直接拉取 GHCR 预构建镜像（最省事的用户路径）

如果你本地构建环境总是出问题，优先走 GHCR 预构建镜像。这条路径的目标是**先把服务跑起来**，不是在你机器上重新 build 镜像。

```bash
cd <project-root>
cp .env.example .env.docker
bash scripts/apply_profile.sh docker b .env.docker

docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

```powershell
cd <project-root>
Copy-Item .env.example .env.docker
.\scripts\apply_profile.ps1 -Platform docker -Profile b -Target .env.docker

docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

默认访问地址：

| 服务 | 地址 |
|---|---|
| Frontend | `http://localhost:3000` |
| Backend API | `http://localhost:18000` |
| SSE | `http://localhost:3000/sse` |

这条路径要注意：

- 它绕开的是**本地镜像构建**，不是“完全不需要仓库 checkout”。你仍然需要当前仓库里的 `docker-compose.ghcr.yml`、`.env.example` 和 profile 脚本。
- 它解决的是 **Dashboard / API / SSE 服务启动**。
- 它**不会**自动帮你把本机上的 `skills / MCP / IDE host` 配置一起接好。
- 如果你还想用当前仓库现成的 repo-local skill + MCP 安装链路，保留这个 checkout，再继续看 `docs/skills/GETTING_STARTED.md`。
- 如果你不走 repo-local 安装链路，也可以手工把支持远程 SSE 的 MCP 客户端指到 `http://localhost:3000/sse`，并配置同一把 API key / 鉴权头。这里的 `<YOUR_MCP_API_KEY>` 默认就填刚生成的 `.env.docker` 里的 `MCP_API_KEY`。
- `scripts/run_memory_palace_mcp_stdio.sh` 不是 Docker 客户端入口。它依赖本地 `bash` 和 `backend/.venv`，只会复用本地 `.env` / `DATABASE_URL`；如果仓库里只有 `.env.docker` 而没有本地 `.env`，它会明确拒绝回退到 `demo.db`，并提示改走 Docker 暴露的 `/sse`。
- 和 `docker_one_click.sh/.ps1` 不同，这条 GHCR compose 路径**不会自动换端口**。如果 `3000` / `18000` 已被占用，请在启动前显式设置 `MEMORY_PALACE_FRONTEND_PORT` / `MEMORY_PALACE_BACKEND_PORT`。
- 如果容器还要访问**你宿主机上的本地模型服务**，不要把容器侧地址写成 `127.0.0.1`。对容器来说，`127.0.0.1` 指向的是容器自己，不是你的宿主机。优先使用 `host.docker.internal`（或你的实际可达地址）。当前 compose 已显式补 `host.docker.internal:host-gateway`，Linux Docker 也能沿这条路径访问宿主机服务。

停止服务：

```bash
docker compose -f docker-compose.ghcr.yml down --remove-orphans
```

### 4.2 Docker 一键部署

```bash
# macOS / Linux
bash scripts/docker_one_click.sh --profile b

# Windows PowerShell
.\scripts\docker_one_click.ps1 -Profile b

# 若需把当前进程中的运行时 API 地址/密钥注入本次运行的 Docker env 文件（例如 profile c/d）
# 需显式开启注入开关（默认关闭）：
bash scripts/docker_one_click.sh --profile c --allow-runtime-env-injection
# 或
.\scripts\docker_one_click.ps1 -Profile c -AllowRuntimeEnvInjection
```

> 如果你在 `profile c/d` 下开启这类本地联调注入，脚本会把这次运行切到显式 API 模式，并额外强制 `RETRIEVAL_EMBEDDING_BACKEND=api`。当 `RETRIEVAL_EMBEDDING_API_*` / `RETRIEVAL_RERANKER_API_*` 没显式提供时，它会优先复用当前进程里的 `ROUTER_API_BASE/ROUTER_API_KEY` 作为兜底；如果你还设置了 `INTENT_LLM_*`，这条链路也会一并注入。这个模式更适合本地排障，不等于你正在验证最终发布口径的 `router` 模板。
>
> 但要注意：`--runtime-env-mode` / `--runtime-env-file` **不是** `docker_one_click.sh/.ps1` 的参数。如果你把这两个参数直接传给一键脚本，命令会报 `Unknown argument`。公开仓用户做 `profile c/d` 本地联调时，继续使用上面这条显式注入命令即可；如果你还要做更严格的发布复验，请回到你实际部署时会使用的 `.env` / router 配置，再单独跑那条验证路径。

> `docker_one_click.sh/.ps1` 默认会为**每次运行**生成独立的临时 Docker env 文件，并通过 `MEMORY_PALACE_DOCKER_ENV_FILE` 传给 `docker compose`；只有显式设置该环境变量时才会复用指定文件，而不是固定共享 `.env.docker`。
>
> 同一 checkout 下的并发部署会被 deployment lock 串行化；若已有另一条一键部署在执行，后续进程会直接退出并提示稍后重试。
>
> 如果 Docker env 文件里的 `MCP_API_KEY` 为空，`apply_profile.*` 会自动生成一把本地 key。Docker 前端会在代理层自动带上这把 key，所以**按推荐的一键脚本路径启动时**，受保护请求通常已经能直接使用；但页面右上角仍可能继续显示 `Set API key`，因为浏览器页面本身并不知道代理层的真实 key。即便看到了按钮，首启配置向导在 Docker 场景下也会明确停留在“说明模式”，不会伪装成已经持久化容器 env。
>
> 当前 Docker Compose 还会额外等 **backend 和 SSE 各自的 `/health`** 都通过，才把 frontend 视为 ready。也就是说，容器刚显示 `running` 时，页面可能还会晚几秒才真正可用，这属于正常现象。
>
> Docker 默认还会分别持久化两类运行期数据：数据库卷会按 compose project 隔离为 `<compose-project>_data`（容器内 `/app/data`），Review snapshots 会隔离为 `<compose-project>_snapshots`（容器内 `/app/snapshots`）。如果你确实要复用旧的共享卷，请显式设置 `MEMORY_PALACE_DATA_VOLUME` / `MEMORY_PALACE_SNAPSHOTS_VOLUME`。如果你执行 `docker compose down -v` 或手动删除这些卷，这两部分都会一起清空。
>
> **C/D 本地联调建议**：
>
> - 如果你本机的 `router` 还没接好 embedding / reranker / llm，可以先直接分别配置 `RETRIEVAL_EMBEDDING_*`、`RETRIEVAL_RERANKER_*`、`WRITE_GUARD_LLM_*` / `COMPACT_GIST_LLM_*`。
> - 这样更容易判断到底是哪一条链路不可达，不会把“某个模型没配好”误判成整个系统不可用。
> - 无论你最终采用 `router` 方案，还是分别直配 `RETRIEVAL_EMBEDDING_*` / `RETRIEVAL_RERANKER_*`，都建议按**最终实际部署配置**重新跑一次启动与健康检查。

> 脚本会自动执行以下步骤：
>
> 1. 调用 Profile 脚本生成本次运行使用的 Docker env 文件（默认临时文件；若显式设置 `MEMORY_PALACE_DOCKER_ENV_FILE` 则复用指定路径）
> 2. 默认不读取当前进程环境变量覆盖模板策略键（避免隐式改档）；仅在显式开启注入开关时注入 API 地址/密钥/模型字段
> 3. 检测端口占用并自动寻找可用端口
> 4. 解析并注入 Docker 持久化卷：默认按 compose project 生成隔离卷名（数据库 `<compose-project>_data`，Review snapshots `<compose-project>_snapshots`）；只有显式设置 `MEMORY_PALACE_DATA_VOLUME` / `MEMORY_PALACE_SNAPSHOTS_VOLUME` 时才复用旧卷
> 5. 对同一 checkout 的并发部署加锁，避免多次 `docker_one_click` 互相覆盖
> 6. 通过 `docker compose` 构建并启动容器

默认访问地址：

| 服务 | 地址 |
|---|---|
| Frontend | `http://localhost:3000` |
| Backend API | `http://localhost:18000` |
| SSE | `http://localhost:3000/sse` |
| Health Check | `http://localhost:18000/health` |
| API 文档 (Swagger) | `http://localhost:18000/docs` |

> **端口映射说明**（来自 `docker-compose.yml`）：
>
> - 前端容器内部运行在 `8080` 端口，对外映射到 `3000`（可通过 `MEMORY_PALACE_FRONTEND_PORT` 环境变量覆盖）
> - 后端容器内部运行在 `8000` 端口，对外映射到 `18000`（可通过 `MEMORY_PALACE_BACKEND_PORT` 环境变量覆盖）
> - Docker 默认同时持久化数据库卷（`/app/data`）和 review snapshot 卷（`/app/snapshots`）

停止服务：

```bash
COMPOSE_PROJECT_NAME=<控制台打印出的 compose project> docker compose -f docker-compose.yml down --remove-orphans
```

> 上面的 `down --remove-orphans` 不会删除数据卷；只有显式使用 `docker compose ... down -v`，或手动删除对应 volume 时，数据库和 review snapshots 才会一起被清空。

> 如果你需要验证 Windows 路径，建议直接在目标 Windows 环境里补跑一次启动与 smoke。

### 4.3 备份当前数据库

在做批量测试、迁移验证或大范围配置切换前，建议先做一次 SQLite 一致性备份：

```bash
# macOS / Linux
bash scripts/backup_memory.sh

# 指定 env / 输出目录
bash scripts/backup_memory.sh --env-file .env --output-dir backups
```

```powershell
# Windows PowerShell
.\scripts\backup_memory.ps1
```

> 备份文件默认写入 `backups/`。如果你准备分享仓库或打包交付，通常不需要把它一并带上。

### 4.4 哪些文件通常不需要提交

当前仓库已经把以下典型本地产物放入 `<repo-root>/.gitignore`：

- 运行期数据库：`*.db`、`*.sqlite`、`*.sqlite3`
- 数据库锁文件：`*.init.lock`、`*.migrate.lock`
- 本地工具配置：`.mcp.json`、`.mcp.json.bak`、`.claude/`、`.codex/`、`.cursor/`、`.opencode/`、`.gemini/`、`.agent/`
- 本地缓存与临时目录：`.tmp/`、`backend/.pytest_cache/`
- 前端本地产物：`frontend/node_modules/`、`frontend/dist/`
- 日志与快照：`*.log`、`snapshots/`、`backups/`
- 临时测试草稿：`frontend/src/*.tmp.test.jsx`
- 维护期内部文档：`docs/improvement/`、`backend/docs/benchmark_*.md`
- 一次性对照摘要：`docs/evaluation_old_vs_new_*.md`
- 本地验证报告：`docs/skills/TRIGGER_SMOKE_REPORT.md`、`docs/skills/MCP_LIVE_E2E_REPORT.md`、`docs/skills/CLAUDE_SKILLS_AUDIT.md`

如果你准备分享项目、打包交付，或者只是想做一次环境自检，建议执行：

```bash
bash scripts/pre_publish_check.sh
```

它会检查常见本地敏感产物、工具配置、本地验证报告、个人路径和 `.env.example` 占位项，帮你快速确认仓库是否适合直接交付。若只是发现这些本地文件存在，通常会给出 `WARN`，提醒你在分享前自己确认。

如果你额外运行下面这些验证脚本：

```bash
python scripts/evaluate_memory_palace_skill.py
cd backend && python ../scripts/evaluate_memory_palace_mcp_e2e.py
```

脚本会分别在 `<repo-root>/docs/skills/TRIGGER_SMOKE_REPORT.md` 和 `<repo-root>/docs/skills/MCP_LIVE_E2E_REPORT.md` 生成摘要。这两份结果主要用于本地复核，不是主说明文档。
如果你是刚 clone 下来的 GitHub 仓库，暂时看不到这两份文件也正常；它们是运行脚本后才生成的本地产物。

---

## 5. 首次验证

> 这里的检查以“先跑通系统”为主；如果你需要额外的本地 Markdown 验证摘要，再运行上面的验证脚本即可。

### 5.1 健康检查

```bash
# 本地开发
curl -fsS http://127.0.0.1:8000/health

# Docker 部署
curl -fsS http://localhost:18000/health
```

预期返回（来自 `main.py` 的 `/health` 端点）：

```json
{
  "status": "ok",
  "timestamp": "2026-02-19T08:00:00Z",
  "index": {
    "index_available": true,
    "degraded": false
  },
  "runtime": {
    "write_lanes": { ... },
    "index_worker": { ... }
  }
}
```

> `status` 为 `"ok"` 表示系统正常；若 index 不可用或报错，`status` 会变为 `"degraded"`。

### 5.2 浏览记忆树

```bash
curl -fsS "http://127.0.0.1:8000/browse/node?domain=core&path=" \
  -H "X-MCP-API-Key: <YOUR_MCP_API_KEY>"
```

> 此端点来自 `api/browse.py` 的 `GET /browse/node`，用于查看指定域下的记忆节点树。`domain` 参数对应 `.env` 中 `VALID_DOMAINS` 配置的域名。
>
> - 如果你配置了 `MCP_API_KEY`，请像上面这样带 `X-MCP-API-Key`
> - 如果你启用了 `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true`，并且请求来自本机回环地址（且没有 forwarded headers），也可以直接省略鉴权头

### 5.3 查看 API 文档

浏览器访问 `http://127.0.0.1:8000/docs`，可打开 FastAPI 自动生成的 Swagger 文档，查看所有 HTTP 端点的参数和返回格式。

---

## 6. MCP 接入

Memory Palace 通过 [MCP 协议](https://modelcontextprotocol.io/) 提供 **9 个工具**（定义在 `mcp_server.py`）：

| 工具名 | 用途 |
|---|---|
| `read_memory` | 读取记忆（支持 `system://boot`、`system://index` 等特殊 URI） |
| `create_memory` | 创建新记忆节点（建议显式填写 `title`） |
| `update_memory` | 更新已有记忆（优先使用 diff patch） |
| `delete_memory` | 删除记忆节点 |
| `add_alias` | 为记忆节点添加别名 |
| `search_memory` | 搜索记忆（keyword / semantic / hybrid 三种模式） |
| `compact_context` | 压缩上下文（清理旧会话日志） |
| `rebuild_index` | 重建搜索索引 |
| `index_status` | 查看索引状态 |

### 6.1 stdio 模式（推荐本地使用）

```bash
cd backend
python mcp_server.py

# 如果你是在新终端或客户端配置里启动，下面这条更稳
./.venv/bin/python mcp_server.py   # Windows PowerShell：.\.venv\Scripts\python.exe mcp_server.py
```

> `stdio` 模式下 MCP 工具直接通过进程的标准输入/输出通信，**不经过 HTTP/SSE 鉴权层**，无需配置 `MCP_API_KEY` 即可使用。
>
> 这里的 `python mcp_server.py` 默认你还在使用 **Step 2 里创建并装好依赖的 `backend/.venv`**。如果你换了一个新终端，或者是在客户端里单独配置本地 MCP，优先直接用项目自己的 `.venv` 解释器。否则会在 MCP 进程真正启动前就报 `ModuleNotFoundError: No module named 'sqlalchemy'` 这类错误。
>
> 如果你是在客户端配置里接入 MCP，更推荐直接用 `scripts/run_memory_palace_mcp_stdio.sh`。但要把边界理解准确：它依赖本地 `bash` 和 `backend/.venv`，优先复用当前仓库的 `.env` / `DATABASE_URL`；只有在仓库里既没有本地 `.env`、也没有 `.env.docker` 时，才会回退到仓库默认 SQLite 路径。若仓库里只有 `.env.docker`，它会明确拒绝回退到 `demo.db`，并提示你改走 Docker 暴露的 `/sse`。

### 6.2 SSE 模式

```bash
cd backend
HOST=127.0.0.1 PORT=8010 python run_sse.py
```

> `run_sse.py` 本地默认就是监听 `127.0.0.1:8000`（可通过 `HOST` 和 `PORT` 自定义），SSE 端点路径为 `/sse`。也就是说，直接运行 `python run_sse.py` 并不会自动绑定到 `0.0.0.0`。只有在你真要给远程客户端接入时，才显式设置 `HOST=0.0.0.0`（或你的实际绑定地址）。SSE 模式仍受 `MCP_API_KEY` 鉴权保护。
>
> 同一个 SSE 进程还会提供一个轻量级 `/health` 端点，主要给 Docker / 脚本做就绪检查；真正对 MCP 客户端开放的流式入口仍然是 `/sse`。
>
> 上面这条命令故意绑定到 `127.0.0.1`，更适合本机调试。如果你真的需要让其他机器访问，再把 `HOST` 改成 `0.0.0.0`（或你的实际监听地址）。这会让远程客户端可以连上监听地址，但 API Key、反向代理、防火墙和传输层安全仍然要你自己补齐。
>
> 如果你使用 Docker / Compose，SSE 会由独立容器启动；compose 内部已显式把它的 `HOST` 设为 `0.0.0.0`，再通过前端代理暴露在 `http://127.0.0.1:3000/sse`。
>
> 也请把这个 Docker 前端端口当成可信管理入口，而不是“带终端用户鉴权的公网入口”。只要有人能直接访问 `3000`，他就能使用 Dashboard 以及被代理的受保护接口；如果要暴露到受信网络之外，请先加你自己的 VPN、反向代理鉴权或网络访问控制。
>
> 上面的 `HOST=127.0.0.1 PORT=8010` 示例是**本机回环**写法。只有在你确实要开放给远程客户端时，才改为 `HOST=0.0.0.0`（或目标绑定地址），并自行补齐网络侧安全控制。
>
> 如果你自己用 `curl` 或脚本先连了一次 `/sse`，然后把这条连接断掉，再单独往 `/messages` 发同一个 `session_id`，看到 `404` / `410` 是正常的：这表示前一条 SSE session 已经关闭。真正的正常链路应该是“先保持 `/sse` 连接活着，再由客户端继续往 `/messages` 发请求”。

### 6.2.1 多客户端并发（可选，但建议提前配好）

如果你会让多个 CLI / IDE 宿主同时指向**同一份 SQLite 库**，建议在 `.env` 里显式加上这组配置：

```env
RUNTIME_WRITE_WAL_ENABLED=true
RUNTIME_WRITE_JOURNAL_MODE=wal
RUNTIME_WRITE_WAL_SYNCHRONOUS=normal
RUNTIME_WRITE_BUSY_TIMEOUT_MS=5000
```

这几项的作用可以直接理解成：

- 每个 stdio MCP 客户端通常都是独立 Python 进程
- 不同进程不会共享同一把进程内写锁，只能靠 SQLite 文件锁协调
- 打开 WAL 并适当增大 `busy_timeout`，能明显降低多客户端同时写入时的 `database is locked`

### 6.3 客户端配置示例

**stdio 模式**（适用于 Claude Code / Codex / OpenCode 等常见 stdio 客户端）：

```json
{
  "mcpServers": {
    "memory-palace": {
      "command": "bash",
      "args": ["/ABS/PATH/TO/REPO/scripts/run_memory_palace_mcp_stdio.sh"]
    }
  }
}
```

> 如果你还没创建 `backend/.venv`，先回到 **Step 2** 完成虚拟环境和依赖安装。
>
> Windows 原生环境不要把 `command` 直接改成 `python.exe` 去执行这条 `.sh`。更稳妥的做法是先准备 Git Bash / WSL，再保持 `bash + run_memory_palace_mcp_stdio.sh` 这条组合；如果当前客户端不方便跑 shell wrapper，优先参考 `docs/skills/GETTING_STARTED.md` 里的脚本化安装路径。

**SSE 模式**：

```json
{
  "mcpServers": {
    "memory-palace": {
      "url": "http://127.0.0.1:8010/sse"
    }
  }
}
```

> ⚠️ 请将这里的 `127.0.0.1:8010` 替换为你实际启动 `run_sse.py` 时使用的监听地址和端口。
>
> ⚠️ SSE 仍受 `MCP_API_KEY` 保护。多数客户端还需要额外配置请求头或 Bearer Token；具体字段名请以客户端自己的 MCP 文档为准。
>
> ⚠️ `HOST=0.0.0.0` 只表示“允许远程连接到这个监听地址”，不表示“允许无鉴权访问”。

### 6.3.1 当前已验证到什么程度

如果你只想手工把客户端接到 Docker 暴露出来的 `/sse`，当前公开口径建议分成两类看：

| 客户端 | 当前公开口径 | 推荐写法 |
|---|---|---|
| `Claude Code` | 已有官方 CLI 选项，可直接写远程 SSE | 可直接按下面示例配置 |
| `Gemini CLI` | 已有官方 CLI 选项，可直接写远程 SSE | 可直接按下面示例配置 |
| `Codex CLI` | 当前公开依据是远程 `--url`（streamable HTTP） | 这个仓库今天先推荐 repo-local stdio 路径 |
| `OpenCode` | 当前公开依据是通用 `remote + url` 结构 | 这个仓库今天先推荐 repo-local 路径；手工远程接法只建议熟悉 OpenCode 配置的人使用 |

也就是说：

- 如果你现在就是想手工把客户端接到 `http://localhost:3000/sse`
- **优先支持的公开路径是 `Claude Code` 和 `Gemini CLI`**
- `Codex` / `OpenCode` 当前不要在这个仓库里写成“已经验证过的直接 `/sse` 产品级路径”

### 6.3.2 Claude Code 手工接 `/sse`

```bash
claude mcp add \
  --transport sse \
  --scope project \
  --header "X-MCP-API-Key: <YOUR_MCP_API_KEY>" \
  memory-palace \
  http://127.0.0.1:3000/sse
```

> 如果你刚按上面的 GHCR / Docker 路径生成了 `.env.docker`，这里的 `<YOUR_MCP_API_KEY>` 默认就填那个文件里的 `MCP_API_KEY`。

检查：

```bash
claude mcp list
```

说明：

- `Claude Code` 当前官方 CLI 同时支持 `stdio`、`sse`、`http`
- 如果未来 Memory Palace 公开提供了更明确的 HTTP / streamable HTTP MCP 入口，优先按官方推荐切到 `http`
- 就当前仓库公开暴露的远程入口来说，用户可直接连接的是 `/sse`

### 6.3.3 Gemini CLI 手工接 `/sse`

```bash
gemini mcp add \
  --transport sse \
  --scope project \
  --header "X-MCP-API-Key: <YOUR_MCP_API_KEY>" \
  memory-palace \
  http://127.0.0.1:3000/sse
```

> 如果你刚按上面的 GHCR / Docker 路径生成了 `.env.docker`，这里的 `<YOUR_MCP_API_KEY>` 默认就填那个文件里的 `MCP_API_KEY`。

检查：

```bash
gemini mcp list
```

如果你更喜欢手改 `settings.json`，当前公开可确认的最小骨架仍然是：

```json
{
  "mcpServers": {
    "memory-palace": {
      "url": "http://127.0.0.1:3000/sse",
      "headers": {
        "X-MCP-API-Key": "<YOUR_MCP_API_KEY>"
      }
    }
  }
}
```

### 6.3.4 为什么这里没有直接给 `Codex / OpenCode` 的 `/sse` 抄写版

不是因为它们一定不支持远程 MCP，而是因为当前公开证据不足以让这个仓库把 `/sse` 直连写成“已经验证过、用户照抄即可”的口径：

- `Codex CLI` 当前公开可确认的是 `codex mcp add <name> --url <URL>` 对应远程 MCP / streamable HTTP
- `OpenCode` 当前公开可确认的是通用 `type = remote` / `url` 结构
- 但这个仓库今天公开暴露和已验证的远程入口是 `/sse`

所以为了不误导用户：

- `Codex / OpenCode` 当前优先继续按 repo-local 安装路径走
- 等我们补齐它们对 `Memory Palace /sse` 的实际验证后，再把那部分写成公开可照抄文档

---

## 7. HTTP/SSE 接口鉴权

Memory Palace 的部分 HTTP 接口受 `MCP_API_KEY` 保护，采用 **fail-closed** 策略（未配置 Key 时默认返回 `401`）。

### 受保护的接口

| 路由前缀 | 说明 | 鉴权方式 |
|---|---|---|
| `/maintenance/*` | 维护接口（孤立节点清理等） | `require_maintenance_api_key` |
| `/review/*` | 审查接口（内容审核流程） | `require_maintenance_api_key` |
| `/browse/*`（GET/POST/PUT/DELETE） | 记忆树读写操作 | `require_maintenance_api_key` |
| `run_sse.py` 的 `/sse` 与 `/messages` | MCP SSE 传输通道与消息入口 | `apply_mcp_api_key_middleware` |

### 鉴权方式

后端支持两种 Header 传递 API Key（定义在 `api/maintenance.py` 和 `run_sse.py`）：

```bash
# 方式一：自定义 Header
curl -fsS http://127.0.0.1:8000/maintenance/orphans \
  -H "X-MCP-API-Key: <YOUR_MCP_API_KEY>"

# 方式二：Bearer Token
curl -fsS http://127.0.0.1:8000/maintenance/orphans \
  -H "Authorization: Bearer <YOUR_MCP_API_KEY>"
```

### 前端鉴权配置

如果前端也需要访问受保护接口，可以在**本地调试或你自己控制的私有部署环境**里注入运行时配置（前端 `src/lib/api.js` 会读取 `window.__MEMORY_PALACE_RUNTIME__`）：

```html
<script>
  window.__MEMORY_PALACE_RUNTIME__ = {
    maintenanceApiKey: "<YOUR_MCP_API_KEY>",
    maintenanceApiKeyMode: "header"
  };
</script>
```

> 不要把真实 `MCP_API_KEY` 写进任何公开页面、共享静态资源或会交付给最终用户的 HTML 里。浏览器里可以直接读取这个全局对象。

> 这段配置主要用于**本地手动启动前后端**的场景。
>
> Docker 一键部署默认不需要把 key 写进页面：前端容器会在代理层自动把同一把 `MCP_API_KEY` 转发到 `/api/*`、`/sse` 和 `/messages`。

### 本地调试跳过鉴权

如果在本地开发时不想配置 API Key，可在 `.env` 中设置：

```env
MCP_API_KEY_ALLOW_INSECURE_LOCAL=true
```

> 此选项仅对来自 `127.0.0.1` / `::1` / `localhost` 的**直连请求**生效；如果请求带有 forwarded headers，仍会被拒绝。它只影响 HTTP/SSE 接口，**不影响** stdio 模式（stdio 不经过鉴权层）。

---

## 8. 常见新手问题

| 问题 | 原因与解决 |
|---|---|
| 启动后端时 `ModuleNotFoundError` | 最常见原因是没用 `backend/.venv`，或者没在这个环境里安装依赖。先执行 `source .venv/bin/activate && pip install -r requirements.txt`；如果是本地 stdio MCP，再优先改用 `./.venv/bin/python mcp_server.py`（Windows：`.\.venv\Scripts\python.exe mcp_server.py`） |
| `DATABASE_URL` 报错 | 路径建议使用绝对路径，并且要带 `sqlite+aiosqlite:///` 前缀。示例：`sqlite+aiosqlite:////absolute/path/to/memory_palace.db` |
| 前端访问 API 返回 `502` 或 `Network Error` | 确认后端已启动且运行在 `8000` 端口。检查 `vite.config.js` 中 proxy 目标与后端端口是否一致 |
| 受保护接口返回 `401` | 本地手动启动：配置 `MCP_API_KEY` 或设置 `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true`；Docker：优先确认是否使用 `apply_profile.*` / `docker_one_click.*` 生成的 Docker env 文件 |
| SSE `/messages` 返回 `429` 或 `413` | `429` 说明同一 SSE 会话短时间内 POST 太多；先检查客户端是否有重复重试或死循环。`413` 说明单次请求体超过 `SSE_MESSAGE_MAX_BODY_BYTES`，需要缩小 payload 或调整后端限制 |
| Docker 启动端口冲突 | `docker_one_click.sh` 默认会自动寻找空闲端口。也可通过 `--frontend-port` / `--backend-port` 手动指定 |

更多问题排查请参考 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

---

## 9. 继续阅读

| 文档 | 内容 |
|---|---|
| [DEPLOYMENT_PROFILES.md](DEPLOYMENT_PROFILES.md) | 部署档位（A/B/C/D）参数详解与选择指南 |
| [TOOLS.md](TOOLS.md) | 9 个 MCP 工具的完整语义、参数和返回格式 |
| [TECHNICAL_OVERVIEW.md](TECHNICAL_OVERVIEW.md) | 系统架构、数据流与技术细节 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 常见问题排查与诊断 |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | 安全模型与隐私设计 |
