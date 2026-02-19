# Memory Palace 快速上手

本指南帮助你在 5 分钟内跑通 Memory Palace 本地开发环境或 Docker 部署。

> **Memory Palace** 是一个为 AI Agent 设计的长期记忆系统，通过 [MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 协议提供 9 个工具，让 Claude Code、Codex、Gemini CLI、Cursor 等 AI 客户端具备持久化记忆能力。

---

## 1. 环境要求

| 依赖 | 最低版本 | 检查命令 |
|---|---|---|
| Python | `3.10+` | `python3 --version` |
| Node.js | `20+` | `node --version` |
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
│   ├── db/               # 数据库 Schema、检索引擎
│   ├── api/              # HTTP 路由
│   │   ├── browse.py     # 记忆树浏览（GET /browse/node）
│   │   ├── review.py     # 审查接口（/review/*）
│   │   └── maintenance.py# 维护接口（/maintenance/*）
│   └── tests/            # 测试与基准测试
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

## 3. 本地开发（推荐先走这一条）

### Step 1：准备配置文件

```bash
cp .env.example .env
```

> **重要**：复制后必须修改 `.env` 中的 `DATABASE_URL`，将路径改为你的实际绝对路径。例如：
>
> ```
> DATABASE_URL=sqlite+aiosqlite:////Users/yourname/memory-palace/memory_palace.db
> ```

也可以使用 Profile 脚本快速生成带有默认配置的 `.env`：

```bash
# macOS / Linux —— 参数：平台 档位 [目标文件]
bash scripts/apply_profile.sh macos b

# Windows PowerShell
.\scripts\apply_profile.ps1 -Platform windows -Profile b
```

> apply_profile 脚本会将 `.env.example` 复制到 `.env`（或你指定的目标文件），然后追加对应 Profile 的覆盖配置。macOS 平台还会自动检测并填充 `DATABASE_URL`。

#### 关键配置项说明

以下是 `.env` 中最常用的配置项（更多配置项请查看 `.env.example` 中的注释说明）：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `DATABASE_URL` | SQLite 数据库路径（**必须使用绝对路径**） | `sqlite+aiosqlite:////absolute/path/to/memory_palace/memory_palace.db` |
| `SEARCH_DEFAULT_MODE` | 检索模式：`keyword` / `semantic` / `hybrid` | `keyword` |
| `RETRIEVAL_EMBEDDING_BACKEND` | 嵌入后端：`none` / `hash` / `router` / `api` / `openai` | `none` |
| `MCP_API_KEY` | HTTP/SSE 接口鉴权密钥 | 空（见下方鉴权说明） |
| `MCP_API_KEY_ALLOW_INSECURE_LOCAL` | 本地调试时允许无 Key 访问（仅对 `127.0.0.1` 生效） | `false` |
| `VALID_DOMAINS` | 允许的记忆 URI 域 | `core,writer,game,notes` |

> B 档位默认使用本地 hash Embedding 且不启用 Reranker；C/D 档位需要配置外部 Embedding 与 Reranker，详见 [DEPLOYMENT_PROFILES.md](DEPLOYMENT_PROFILES.md)。

### Step 2：启动后端

```bash
cd backend
python3 -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

预期输出：

```
Memory API starting...
SQLite database initialized.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

> 后端通过 `main.py` 中的 `lifespan` 上下文管理器完成初始化，包括 SQLite 数据库创建、运行时状态（Write Lane、Index Worker）启动等。

### Step 3：启动前端

```bash
cd frontend
npm install
npm run dev
```

预期输出：

```
VITE v7.x.x  ready in xxx ms
➜  Local:   http://127.0.0.1:5173/
```

打开浏览器访问 `http://127.0.0.1:5173`，即可看到 Memory Palace Dashboard。

> 前端开发服务器通过 `vite.config.js` 中配置的 proxy 将 `/api` 路径代理到后端 `http://127.0.0.1:8000`，因此前后端无需手动配置 CORS。

---

## 4. Docker 一键部署

```bash
# macOS / Linux
bash scripts/docker_one_click.sh --profile b

# Windows PowerShell
.\scripts\docker_one_click.ps1 -Profile b
```

> 脚本会自动执行以下步骤：
>
> 1. 调用 Profile 脚本生成 `.env.docker` 配置文件（macOS/Linux: `apply_profile.sh`；Windows: `apply_profile.ps1`）
> 2. 检测端口占用并自动寻找可用端口
> 3. 通过 `docker compose` 构建并启动容器

默认访问地址：

| 服务 | 地址 |
|---|---|
| Frontend | `http://localhost:3000` |
| Backend API | `http://localhost:18000` |
| Health Check | `http://localhost:18000/health` |
| API 文档 (Swagger) | `http://localhost:18000/docs` |

> **端口映射说明**（来自 `docker-compose.yml`）：
>
> - 前端容器内部运行在 `8080` 端口，对外映射到 `3000`（可通过 `MEMORY_PALACE_FRONTEND_PORT` 环境变量覆盖）
> - 后端容器内部运行在 `8000` 端口，对外映射到 `18000`（可通过 `MEMORY_PALACE_BACKEND_PORT` 环境变量覆盖）

停止服务：

```bash
docker compose -f docker-compose.yml down
```

---

## 5. 首次验证

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
curl -fsS "http://127.0.0.1:8000/browse/node?domain=core&path="
```

> 此端点来自 `api/browse.py` 的 `GET /browse/node`，用于查看指定域下的记忆节点树。`domain` 参数对应 `.env` 中 `VALID_DOMAINS` 配置的域名。

### 5.3 查看 API 文档

浏览器访问 `http://127.0.0.1:8000/docs`，可打开 FastAPI 自动生成的 Swagger 文档，查看所有 HTTP 端点的参数和返回格式。

---

## 6. MCP 接入

Memory Palace 通过 [MCP 协议](https://modelcontextprotocol.io/) 提供 **9 个工具**（定义在 `mcp_server.py`）：

| 工具名 | 用途 |
|---|---|
| `read_memory` | 读取记忆（支持 `system://boot`、`system://index` 等特殊 URI） |
| `create_memory` | 创建新记忆节点 |
| `update_memory` | 更新已有记忆（支持 diff patch） |
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
```

> `stdio` 模式下 MCP 工具直接通过进程的标准输入/输出通信，**不经过 HTTP/SSE 鉴权层**，无需配置 `MCP_API_KEY` 即可使用。

### 6.2 SSE 模式

```bash
cd backend
HOST=127.0.0.1 PORT=8010 python run_sse.py
```

> `run_sse.py` 默认监听 `0.0.0.0:8000`（通过 `HOST` 和 `PORT` 环境变量可自定义），SSE 端点路径为 `/sse`。SSE 模式受 `MCP_API_KEY` 鉴权保护。

### 6.3 客户端配置示例

**stdio 模式**（适用于 Claude Code / Codex / Cursor 等）：

```json
{
  "mcpServers": {
    "memory-palace": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/memory-palace/backend"
    }
  }
}
```

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

> ⚠️ 请将 `/path/to/memory-palace` 替换为你的实际项目路径。SSE 模式的端口需与你启动 `run_sse.py` 时的 `PORT` 一致。

---

## 7. HTTP/SSE 接口鉴权

Memory Palace 的部分 HTTP 接口受 `MCP_API_KEY` 保护，采用 **fail-closed** 策略（未配置 Key 时默认返回 `401`）。

### 受保护的接口

| 路由前缀 | 说明 | 鉴权方式 |
|---|---|---|
| `/maintenance/*` | 维护接口（孤立节点清理等） | `require_maintenance_api_key` |
| `/review/*` | 审查接口（内容审核流程） | `require_maintenance_api_key` |
| `/browse/*`（POST/PUT/DELETE） | 记忆树写操作 | `require_maintenance_api_key` |
| `run_sse.py` 的 `/sse` | MCP SSE 传输通道 | `apply_mcp_api_key_middleware` |

> **注意**：`GET /browse/node`（读操作）**不需要**鉴权；只有写操作（POST/PUT/DELETE）才需要。

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

如果前端也需要访问受保护接口，请在 `frontend/index.html` 的 `<head>` 中注入运行时配置（前端 `src/lib/api.js` 会读取 `window.__MEMORY_PALACE_RUNTIME__`）：

```html
<script>
  window.__MEMORY_PALACE_RUNTIME__ = {
    maintenanceApiKey: "<YOUR_MCP_API_KEY>",
    maintenanceApiKeyMode: "header"
  };
</script>
```

### 本地调试跳过鉴权

如果在本地开发时不想配置 API Key，可在 `.env` 中设置：

```env
MCP_API_KEY_ALLOW_INSECURE_LOCAL=true
```

> 此选项仅对来自 `127.0.0.1` / `::1` / `localhost` 的请求生效，且仅影响 HTTP/SSE 接口，**不影响** stdio 模式（stdio 不经过鉴权层）。

---

## 8. 常见新手问题

| 问题 | 原因与解决 |
|---|---|
| 启动后端时 `ModuleNotFoundError` | 未激活虚拟环境或未安装依赖。执行 `source .venv/bin/activate && pip install -r requirements.txt` |
| `DATABASE_URL` 报错 | 路径必须是绝对路径且使用 `sqlite+aiosqlite:///` 前缀。macOS 示例：`sqlite+aiosqlite:////Users/you/memory_palace.db` |
| 前端访问 API 返回 `502` 或 `Network Error` | 确认后端已启动且运行在 `8000` 端口。检查 `vite.config.js` 中 proxy 目标与后端端口是否一致 |
| 受保护接口返回 `401` | 配置 `MCP_API_KEY` 或设置 `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true` |
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
