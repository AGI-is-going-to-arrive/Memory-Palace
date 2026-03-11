# Memory Palace 常见问题排查

本文档帮助你快速定位和解决 Memory Palace 使用过程中的常见问题。

---

## 1. 前端 502 或接口超时

**现象**：页面能打开，但列表为空或接口报错。

> 📌 当前版本还有一种很常见的“看起来像坏了，其实是正常门控”的情况：
>
> - 页面右上角出现 `Set API key`
> - `Memory / Review / Maintenance / Observability` 里出现空态、等待提示或 `401`
>
> 这通常不是前端挂了，而是**你还没给受保护接口授权**。
>
> 如果你用的是 **Docker 一键部署**，按推荐路径启动时，受保护请求通常已经能直接使用；但页面右上角**仍可能继续显示** `Set API key`，因为浏览器并不知道代理层自动转发的那把 key。优先先看受保护数据是不是也一起 `401` 或空态；只有这些请求也失败时，才需要继续检查 `apply_profile.*` / `docker_one_click.*` 生成的 Docker env 文件、代理配置或手工改动。

**排查步骤**：

1. 确认**后端已启动**：

   ```bash
   curl -fsS http://127.0.0.1:8000/health
   ```

   > 后端健康检查端点 `GET /health` 会返回 `status`、`index`、`runtime` 等字段（参见 `backend/main.py` 中 `health()` 函数）。

2. 确认**前端代理目标正确**：

   检查 `frontend/vite.config.js` 中 `apiProxyTarget` 的值：

   ```javascript
   // 默认目标: http://127.0.0.1:8000
   const apiProxyTarget =
     process.env.MEMORY_PALACE_API_PROXY_TARGET ||
     process.env.NOCTURNE_API_PROXY_TARGET ||
     'http://127.0.0.1:8000'
   ```

   如果后端运行在其他端口，请设置环境变量：

   ```bash
   MEMORY_PALACE_API_PROXY_TARGET=http://127.0.0.1:9000 npm run dev
   ```

3. **Docker 场景**下确认端口映射：

   - 默认后端端口：`18000`（映射到容器内 `8000`）
   - 默认前端端口：`3000`（映射到容器内 `8080`）
   - 可通过 `MEMORY_PALACE_BACKEND_PORT`、`MEMORY_PALACE_FRONTEND_PORT` 环境变量覆盖（参见 `docker-compose.yml`）

4. 检查后端日志：

   ```bash
   # 本地直接启动（uvicorn/python run_sse.py）时，优先看当前终端输出
   # Docker 部署时查看容器日志
   docker compose -f docker-compose.yml logs backend --tail=50
   ```

---

## 1.1 页面默认是英文，怎么切到中文？

**现象**：

- 页面能正常打开
- 但界面默认显示英文

**这不是前端没加载完整**。当前版本的前端默认语言就是英文。

**处理方式**：

1. 看页面右上角的语言按钮
2. 直接点一下，就可以在英文和中文之间切换
3. 浏览器会记住你的选择；下次再打开时会优先使用你上次切到的语言

如果你 first-run 一上来先被首启配置向导挡住，也不用先把它关掉：

1. 向导右上角也有语言按钮
2. 可以直接在弹窗内部切到中文
3. 切完之后页面主体和后续弹窗也会一起跟着切换

**补充说明**：

- 语言切换只影响前端显示，不会改变 MCP、API、鉴权或数据库行为
- 如果你看到的是英文界面，但功能、按钮、数据都正常，这属于预期行为，不是故障

---

## 2. `/maintenance/*`、`/review/*` 或 `/browse/*` 返回 401

**常见原因**：请求没带鉴权头。注意 `/browse/node` 的读操作也受保护，而且这些接口默认就是 fail-closed；只有显式开启 `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true` 且请求来自 loopback 时才会放行。

**排查与处理**：

- **方式一**：curl 加鉴权头：

  ```bash
  curl -fsS http://127.0.0.1:8000/maintenance/orphans \
    -H "X-MCP-API-Key: <YOUR_MCP_API_KEY>"
  ```

- **方式二**：使用 Bearer 格式：

  ```bash
  curl -fsS http://127.0.0.1:8000/maintenance/orphans \
    -H "Authorization: Bearer <YOUR_MCP_API_KEY>"
  ```

- **前端**：注入 `window.__MEMORY_PALACE_RUNTIME__`（详见 [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) 第 4 节）
- **前端页面**：也可以直接点右上角的 `Set API key` / `Update API key`
  - 当前版本会先打开首启配置向导
  - 如果你只是想先让 Dashboard 通过鉴权，优先使用“只保存 Dashboard 密钥”
  - `.env` 写入只建议在本地 checkout + 非 Docker 运行时使用

- **本地调试** 可设置 insecure local override（仅 loopback 生效）：

  ```bash
  # .env 中添加
  MCP_API_KEY_ALLOW_INSECURE_LOCAL=true
  ```

- **如果你明明改了仓库 `.env`，服务却还在使用旧 key / 错 key**：
  - 先检查当前终端有没有额外导出过 `MCP_API_KEY` 或 `MCP_API_KEY_ALLOW_INSECURE_LOCAL`
  - 当前实现里，**进程环境变量优先级高于仓库 `.env`**
  - 我们在真实本地复测里就遇到过：`run_sse.py` 读取到了外层 shell 里的 `MCP_API_KEY`，结果即使仓库 `.env` 里写的是另一把 key，`/sse` 仍然按外层环境变量鉴权，表现成 `401 invalid_or_missing_api_key`

  ```bash
  env | rg '^MCP_API_KEY=|^MCP_API_KEY_ALLOW_INSECURE_LOCAL='
  unset MCP_API_KEY MCP_API_KEY_ALLOW_INSECURE_LOCAL
  ```

  > 清掉外层环境变量后，请重新启动 `backend` / `run_sse.py` 再复测。

**根据返回的 `reason` 字段判断具体原因（参见 `backend/api/maintenance.py`）：**

| `reason` | 含义 | 处理方式 |
|---|---|---|
| `invalid_or_missing_api_key` | Key 错误或未提供 | 检查 Key 是否正确 |
| `api_key_not_configured` | 本地手动启动时 `.env` 中 `MCP_API_KEY` 为空 | 设置 Key 或启用 insecure local |
| `insecure_local_override_requires_loopback` | 启用了 insecure local 但请求非 loopback | 确保从 `127.0.0.1` 或 `localhost` 访问 |

> 💡 如果你看到的是：
>
> - `Awaiting Input`
> - `Failed to load node`
> - `Connection Lost`
> - `maintenance_auth_failed | api_key_not_configured`
>
> 优先先配 key，再判断是不是别的问题。

---

## 3. SSE 启动失败或端口占用

**现象**：

- 本地手动启动 `python run_sse.py` 报 `address already in use`
- 或 Docker 下访问 `http://127.0.0.1:3000/sse` 失败

**处理**：

1. 先确认 SSE 进程是不是已经起来：

   ```bash
   curl -fsS http://127.0.0.1:8010/health
   ```

   如果这里都不通，优先排查进程和端口；如果这里已经返回 `{"status":"ok","service":"memory-palace-sse"}`，再继续看 `/sse` 和 `/messages` 的鉴权或 session。

2. 更换端口（SSE 默认端口为 `8000`）：

   ```bash
   HOST=127.0.0.1 PORT=8010 python run_sse.py
   ```

3. 或查找并释放被占用端口：

   ```bash
   # macOS / Linux
   lsof -i :8000
   kill -9 <PID>
   ```

   ```powershell
   # Windows PowerShell
   netstat -ano | findstr :8000
   taskkill /PID <PID> /F
   ```

---

## 3.1 `/messages` 返回 404 或 410

**现象**：

- 你先连上了 `/sse`
- 也拿到了 `event: endpoint`
- 但后面往 `/messages/?session_id=...` 发请求时返回 `404` 或 `410`

**这通常不是鉴权错了**，而是你手里的 `session_id` 已经失效了。

当前真实行为是：

- `404`：服务端已经找不到这条 session
- `410`：session 还在映射里，但底层 SSE writer 已经关闭

说人话就是：

- 只要 SSE 流断开了，就别继续复用旧的 `session_id`
- 你需要重新连一次 `/sse`，拿一条新的 `event: endpoint`

**处理方式**：

1. 重新建立 SSE 连接：

   ```bash
   curl -i \
     -H 'Accept: text/event-stream' \
     -H 'X-MCP-API-Key: <YOUR_MCP_API_KEY>' \
     http://127.0.0.1:8010/sse
   ```

2. 从新返回的 `event: endpoint` 里取新的 `session_id`

3. 再向新的 `/messages/?session_id=...` 发请求

**补充说明**：

- 这是当前版本故意做成 fail-closed 的行为
- 目的就是避免“旧会话其实已经死了，但服务端还回你一个假 `202 Accepted`”

---

## 3.2 后端启动时报 `No module named 'diff_match_patch'`

**现象**：

- 本地启动 `uvicorn main:app` 时，日志里出现：

  ```text
  ModuleNotFoundError: No module named 'diff_match_patch'
  ```

**现在的实际行为**：

- 当前代码会优先使用 `diff_match_patch`
- 如果这个包在你的 Python 环境里缺失，`/review/diff` 会自动退回到 `difflib.HtmlDiff` 表格 diff
- 这不应该再把整个后端启动直接打死

**如果你还是看到了这个错误**，优先排查这两件事：

1. 你是不是在用项目自己的虚拟环境启动：

   ```bash
   cd backend
   source .venv/bin/activate
   python -m uvicorn main:app --reload --port 8000
   ```

2. 依赖是不是装在了别的 Python 解释器里：

   ```bash
   cd backend
   .venv/bin/python -m pip install -r requirements.txt
   ```

**补充说明**：

- `diff_match_patch` 仍然在 `backend/requirements.txt` 里，正常安装时最好带上
- 现在加的 fallback 只是为了把故障从“后端起不来”降成“review diff 降级”，不是建议长期缺着这个依赖

3. Docker 一键部署时，优先检查前端入口：

   ```bash
   curl -i -H 'Accept: text/event-stream' http://127.0.0.1:3000/sse
   ```

   正常情况下你应该能看到：

   ```text
   event: endpoint
   data: /messages/?session_id=...
   ```

4. 如果你是远程客户端接入，请再确认这三件事：

   - `run_sse.py` 启动时不要继续绑在 `HOST=127.0.0.1`
   - 请求里带了正确的 `X-MCP-API-Key` 或 `Authorization: Bearer ...`
   - 反向代理 / 防火墙没有把请求拦掉

   可以先用一条最小请求排查：

   ```bash
   curl -i \
     -H 'Accept: text/event-stream' \
     -H 'X-MCP-API-Key: <YOUR_MCP_API_KEY>' \
     http://<YOUR_HOST>:<YOUR_PORT>/sse
   ```

---

## 3.3 运行 `python mcp_server.py` 时提示 `No module named 'sqlalchemy'`

**现象**：

- 本地直接运行 `python mcp_server.py`，启动前就报：

  ```text
  ModuleNotFoundError: No module named 'sqlalchemy'
  ```

**这通常不是 Memory Palace 功能逻辑坏了**，而是当前启动这个 MCP 进程的 Python 环境里没有装后端依赖。

`sqlalchemy` 是 `backend/requirements.txt` 里的硬依赖；`mcp_server.py` 启动时会先导入数据库层，所以如果解释器不对，进程会在真正启动前直接退出。

**最常见的两种情况**：

1. 你开了一个新终端，但没有重新激活 `backend/.venv`
2. 你在 Claude Code / Codex / OpenCode 这类客户端里配置本地 MCP 时，写的是系统 `python`，不是项目自己的 `.venv` Python

**处理方式**：

1. 先确认你在项目自己的虚拟环境里安装过依赖：

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. 如果你只是想在当前终端里直接启动 MCP：

   ```bash
   cd backend
   ./.venv/bin/python mcp_server.py
   ```

   Windows PowerShell：

   ```powershell
   cd backend
   .\.venv\Scripts\python.exe mcp_server.py
   ```

3. 如果你是在客户端里配置本地 stdio MCP，优先把 `command` 直接写成项目自己的 `.venv` Python，不要只写 `python`。

**快速自检**：

```bash
cd backend
./.venv/bin/python -c "import sqlalchemy; print(sqlalchemy.__version__)"
```

如果这条命令能正常输出版本号，再启动 `mcp_server.py` 就不会卡在这个问题上。

---

## 4. Docker 一键脚本失败

**排查步骤**：

1. 确认 Docker 可用：

   ```bash
   docker compose version
   ```

2. 确认 profile 合法（`a`、`b`、`c`、`d`）：

   ```bash
   # 查看帮助
   bash scripts/docker_one_click.sh --help
   ```

3. 端口冲突时指定端口：

   ```bash
   bash scripts/docker_one_click.sh --profile b --frontend-port 3100 --backend-port 18100
   ```

   如果脚本发现你指定的端口也被占用，会继续自动换到附近空闲端口。
   这时请以脚本最后打印出来的 `Frontend` / `Backend API` 地址为准，不要继续死盯你最初输入的端口。

4. 镜像构建失败时，检查 Dockerfile 是否完整：
   - `deploy/docker/Dockerfile.backend` — 基于 `python:3.11-slim`
   - `deploy/docker/Dockerfile.frontend` — 基于 `node:22-alpine`（构建）+ `nginxinc/nginx-unprivileged:1.27-alpine`（运行）

> 💡 Windows 用户可使用 `scripts/docker_one_click.ps1`（PowerShell 版本）。

---

## 5. 搜索质量突然下降

**排查步骤**：

1. **查看 `degrade_reasons`**：`search_memory` MCP 工具返回的 `degrade_reasons` 字段会告诉你检索链路的具体降级原因。常见值包括：

   | `degrade_reasons` 值 | 含义 | 来源文件 |
   |---|---|---|
   | `embedding_fallback_hash` | Embedding API 不可达，回退到本地 hash | `backend/db/sqlite_client.py` |
   | `embedding_config_missing` | Embedding 配置缺失 | `backend/db/sqlite_client.py` |
   | `embedding_request_failed` | Embedding API 请求失败 | `backend/db/sqlite_client.py` |
   | `reranker_request_failed` | Reranker API 请求失败 | `backend/db/sqlite_client.py` |
   | `reranker_config_missing` | Reranker 配置缺失 | `backend/db/sqlite_client.py` |
   | `compact_gist_llm_empty` | Compact Gist LLM 返回空结果 | `backend/mcp_server.py` |
   | `index_enqueue_dropped` | 索引任务入队被丢弃 | `backend/mcp_server.py` |

   > `write_guard_exception` 属于写入/学习链路（如 `create_memory`、`update_memory`、显式学习触发），语义为写入已 fail-closed 拒绝，并非检索质量降级。

2. **检查 Embedding / Reranker API 可达性**：

   ```bash
   # 配置语义：RETRIEVAL_EMBEDDING_BACKEND 只控制 embedding。
   # reranker 不存在 RETRIEVAL_RERANKER_BACKEND；如需本地强制走自有 reranker API，
   # 请显式设置 RETRIEVAL_RERANKER_ENABLED=true 与 RETRIEVAL_RERANKER_API_BASE/API_KEY/MODEL。
   # 注意：RETRIEVAL_*_API_BASE 可能已包含 /v1，避免再手动拼接 /v1
   # 用实际调用端点做健康检查更准确：
   curl -fsS -X POST <RETRIEVAL_EMBEDDING_API_BASE>/embeddings \
     -H "Content-Type: application/json" \
     -d '{"model":"<RETRIEVAL_EMBEDDING_MODEL>","input":"ping"}'
   curl -fsS -X POST <RETRIEVAL_RERANKER_API_BASE>/rerank \
     -H "Content-Type: application/json" \
     -d '{"model":"<RETRIEVAL_RERANKER_MODEL>","query":"ping","documents":["pong"]}'
   ```

   > **排障顺序建议**：
   > - 先确认你当前使用的是 `router` 方案，还是分别直配 `RETRIEVAL_EMBEDDING_*`、`RETRIEVAL_RERANKER_*`、`WRITE_GUARD_LLM_* / COMPACT_GIST_LLM_*`。
   > - 如果采用直配方案，优先检查实际 `*_API_BASE` / `*_API_KEY` / `*_MODEL`。
   > - 如果采用 `router` 方案，再检查 `ROUTER_*` 配置和 router 服务本身。

3. **重建索引**（通过 MCP 工具调用）：

   ```python
   # 重建索引
   rebuild_index(wait=true)
   # 检查索引状态
   index_status()
   ```

4. **查看观测摘要**（通过 HTTP API）：

   ```bash
   curl -fsS http://127.0.0.1:8000/maintenance/observability/summary \
     -H "X-MCP-API-Key: <YOUR_MCP_API_KEY>"
   ```

5. **检查配置参数**：确认 `RETRIEVAL_RERANKER_WEIGHT` 在合理范围（`.env.example` 注释建议 `0.20 ~ 0.40`，默认 `0.25`）

6. **观测页里看到新增字段不要慌**：

   - `scope_hint`：只是告诉检索“优先看哪个范围”
   - `sm-lite`：是当前版本新增的一组轻量运行时状态，不是报错
   - `Runtime Snapshot`：是帮助你排障的摘要，不是必须每项都有值

---

## 6. 前端构建失败

```bash
cd frontend
rm -rf node_modules       # 清理缓存
npm ci                     # 全新安装依赖
npm run test               # 运行测试
npm run build              # 构建产物
```

> **Windows 用户**：使用 `rmdir /s /q node_modules` 替代 `rm -rf`。

常见原因：

- Node.js 版本不兼容：建议使用 Node.js `20.19+`（或 `>=22.12`）
- 网络问题导致 `npm ci` 失败：可配置 NPM Mirror

---

## 7. 测试失败或想做更深验证

如果你只是想先确认安装可用，先做最小运行检查：

```bash
curl -fsS http://127.0.0.1:8000/health
```

如果你想继续确认后端/前端都可用，再运行仓库自带测试：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate           # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest tests -q

cd ../frontend
npm ci
npm run test
npm run build
```

> **Windows PowerShell 用户**：`source` 命令不可用，使用 `.\.venv\Scripts\Activate.ps1` 激活虚拟环境。

**快速定位技巧**：优先查看最近改动文件对应的测试集，再扩大全量回归：

```bash
# 只运行特定测试文件
pytest tests/test_week6_maintenance_auth.py -q

# 只运行匹配名称的测试
pytest tests -k "test_search" -q
```

---

## 8. 数据库迁移异常

**现象**：启动时报迁移锁超时，类似 `Timed out waiting for migration lock`。

**背景**：Memory Palace 使用基于文件锁的迁移机制（参见 `backend/db/migration_runner.py`），防止多个进程同时执行迁移。

**排查与处理**：

1. **检查是否有重复进程同时启动**

2. **调整锁超时**：在 `.env` 中设置（默认 `10` 秒）：

   ```bash
   DB_MIGRATION_LOCK_TIMEOUT_SEC=30
   ```

3. **手动指定锁文件路径**：

   ```bash
   DB_MIGRATION_LOCK_FILE=/tmp/memory_palace.migrate.lock
   ```

   > 如果不设置，默认锁文件为 `<数据库文件>.migrate.lock`（例如 `demo.db.migrate.lock`），保存在与数据库文件同一目录下。
   >
   > 当前版本另外还有一把启动初始化锁：`<数据库文件>.init.lock`。它用于把 `init_db()` 串行化，避免 `backend` 和 `sse` 首次并发启动时互相抢库。
   >
   > 这把 `.init.lock` 只会用于**文件型 SQLite 数据库**。像 `:memory:` 这类目标不会生成它；如果你的 `DATABASE_URL` 带 query string，锁文件也会按真实数据库文件名生成，不会把 `?cache=shared` 这类参数拼进文件名里。

4. **手动删除残留锁文件后重启**：

   ```bash
   # 找到锁文件并删除（默认在数据库文件旁）
   rm -f /path/to/demo.db.migrate.lock
   rm -f /path/to/demo.db.init.lock
   ```

**验证锚点**：当前仓库中的 `backend/tests/test_migration_runner.py` 覆盖了迁移锁与超时场景。

---

## 9. 索引重建后仍无改善

**排查步骤**：

1. **确认索引已就绪**：

   ```python
   # MCP 工具调用
   index_status()
   # 返回中应包含 index_available=true
   ```

2. **检查 Embedding 后端配置是否正确**（参见 `.env.example`）：

   | 部署档位 | `RETRIEVAL_EMBEDDING_BACKEND` 应设为 | 说明 |
   |---|---|---|
   | Profile A | `none` | 纯关键字搜索，不使用 Embedding |
   | Profile B | `hash` | 本地 hash Embedding（默认值） |
   | Profile C/D | `api` 或 `router` | 本地开发优先用 `api` 直配排障；发布验证默认回到 `router` 主链路 |

3. **确认有记忆内容**：

   ```bash
   curl -fsS \
     -H "X-MCP-API-Key: ${MCP_API_KEY}" \
     "http://127.0.0.1:8000/browse/node?domain=core&path="
   ```

4. **尝试 Sleep Consolidation**（通过 MCP 工具）：

   ```python
   rebuild_index(sleep_consolidation=true, wait=true)
   ```

   > Sleep Consolidation 会触发深度索引重建（参见 `backend/runtime_state.py` 中 `SleepTimeConsolidator`）。

5. **检查 `degrade_reasons`** 中是否存在降级标识（参见本文档第 5 节降级原因表）

---

## 10. CORS 报错（跨域访问被拒绝）

**现象**：前端请求后端 API 时浏览器报 CORS 错误。

**说明**：当前默认**不是允许所有 Origin**。后端在 `CORS_ALLOW_ORIGINS` 留空时，只放行本地常用来源：

```python
http://localhost:5173
http://127.0.0.1:5173
http://localhost:3000
http://127.0.0.1:3000
```

**如果仍然报错**，通常原因是：

- 前端开发服务器的代理未正确配置（检查 `frontend/vite.config.js`）
- Docker 部署时前端 Nginx 没有正确转发到后端（检查 `deploy/docker/nginx.conf.template`）
- 你正在从一个**不在允许列表里的浏览器来源**访问后端

**处理建议**：

- 本地开发：
  - 保持 `CORS_ALLOW_ORIGINS=` 留空即可
- 生产浏览器访问：
  - 把 `CORS_ALLOW_ORIGINS` 显式写成你的前端地址列表
  - 例如：`CORS_ALLOW_ORIGINS=https://app.example.com,https://admin.example.com`
- 不建议为了省事直接写 `*`
  - 尤其是你还需要 credentials / cookie / auth 头的时候

---

## 11. 获取帮助

如果以上步骤无法解决你的问题：

1. 查看后端完整日志：本地看启动终端输出，Docker 看 `docker compose -f docker-compose.yml logs backend --tail=200`
2. 检查 `GET /health` 返回的 `status` 和 `index` 字段
3. 通过 `GET /maintenance/observability/summary` 查看系统运行概况（该接口受 `MCP_API_KEY` 保护，请携带 `X-MCP-API-Key` 或 `Authorization: Bearer`）
4. 提交 Issue 时请附上：错误信息、操作系统、Python 版本、Node.js 版本、使用的 Profile 档位
