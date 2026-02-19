# Memory Palace 常见问题排查

本文档帮助你快速定位和解决 Memory Palace 使用过程中的常见问题。

---

## 1. 前端 502 或接口超时

**现象**：页面能打开，但列表为空或接口报错。

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

## 2. `/maintenance/*` 或 `/review/*` 返回 401

**原因**：启用了 `MCP_API_KEY` 但请求没带鉴权头。

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

- **本地调试** 可设置 insecure local override（仅 loopback 生效）：

  ```bash
  # .env 中添加
  MCP_API_KEY_ALLOW_INSECURE_LOCAL=true
  ```

**根据返回的 `reason` 字段判断具体原因（参见 `backend/api/maintenance.py`）：**

| `reason` | 含义 | 处理方式 |
|---|---|---|
| `invalid_or_missing_api_key` | Key 错误或未提供 | 检查 Key 是否正确 |
| `api_key_not_configured` | `.env` 中 `MCP_API_KEY` 为空 | 设置 Key 或启用 insecure local |
| `insecure_local_override_requires_loopback` | 启用了 insecure local 但请求非 loopback | 确保从 `127.0.0.1` 或 `localhost` 访问 |

---

## 3. SSE 启动失败或端口占用

**现象**：`python run_sse.py` 报 `address already in use`。

**处理**：

1. 更换端口（SSE 默认端口为 `8000`，参见 `backend/run_sse.py` 第 105 行）：

   ```bash
   HOST=127.0.0.1 PORT=8010 python run_sse.py
   ```

2. 或查找并释放被占用端口：

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

4. 镜像构建失败时，检查 Dockerfile 是否完整：
   - `deploy/docker/Dockerfile.backend` — 基于 `python:3.11-slim`
   - `deploy/docker/Dockerfile.frontend` — 基于 `node:22-alpine`（构建）+ `nginxinc/nginx-unprivileged:1.27-alpine`（运行）

> 💡 Windows 用户可使用 `scripts/docker_one_click.ps1`（PowerShell 版本）。

---

## 5. 搜索质量突然下降

**排查步骤**：

1. **查看 `degrade_reasons`**：`search_memory` MCP 工具返回的 `degrade_reasons` 字段会告诉你具体降级原因。常见值包括：

   | `degrade_reasons` 值 | 含义 | 来源文件 |
   |---|---|---|
   | `embedding_fallback_hash` | Embedding API 不可达，回退到本地 hash | `backend/db/sqlite_client.py` |
   | `embedding_config_missing` | Embedding 配置缺失 | `backend/db/sqlite_client.py` |
   | `embedding_request_failed` | Embedding API 请求失败 | `backend/db/sqlite_client.py` |
   | `reranker_request_failed` | Reranker API 请求失败 | `backend/db/sqlite_client.py` |
   | `reranker_config_missing` | Reranker 配置缺失 | `backend/db/sqlite_client.py` |
   | `write_guard_exception` | Write Guard LLM 异常 | `backend/mcp_server.py` |
   | `compact_gist_llm_empty` | Compact Gist LLM 返回空结果 | `backend/mcp_server.py` |
   | `index_enqueue_dropped` | 索引任务入队被丢弃 | `backend/mcp_server.py` |

2. **检查 Embedding / Reranker API 可达性**：

   ```bash
   # 注意：RETRIEVAL_*_API_BASE 可能已包含 /v1，避免再手动拼接 /v1
   # 用实际调用端点做健康检查更准确：
   curl -fsS -X POST <RETRIEVAL_EMBEDDING_API_BASE>/embeddings \
     -H "Content-Type: application/json" \
     -d '{"model":"<RETRIEVAL_EMBEDDING_MODEL>","input":"ping"}'
   curl -fsS -X POST <RETRIEVAL_RERANKER_API_BASE>/rerank \
     -H "Content-Type: application/json" \
     -d '{"model":"<RETRIEVAL_RERANKER_MODEL>","query":"ping","documents":["pong"]}'
   ```

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

- Node.js 版本不兼容：建议使用 Node.js 22+（Docker 中使用 `node:22-alpine`）
- 网络问题导致 `npm ci` 失败：可配置 NPM Mirror

---

## 7. 后端测试失败

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest tests -q
```

> **Windows PowerShell 用户**：`source` 命令不可用，使用 `.venv\Scripts\Activate.ps1` 激活虚拟环境。

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

4. **手动删除残留锁文件后重启**：

   ```bash
   # 找到锁文件并删除（默认在数据库文件旁）
   rm -f /path/to/demo.db.migrate.lock
   ```

**对应的测试用例**：`backend/tests/test_migration_runner.py` 包含完整的迁移锁与超时场景测试。

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
   | Profile C/D | `api` 或 `router` | 调用远程 Embedding API |

3. **确认有记忆内容**：

   ```bash
   curl -fsS "http://127.0.0.1:8000/browse/node?domain=core&path="
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

**说明**：开发环境下后端已配置允许所有 Origin（参见 `backend/main.py`）：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**如果仍然报错**，通常原因是：

- 前端开发服务器的代理未正确配置（检查 `frontend/vite.config.js`）
- Docker 部署时前端 Nginx 没有正确转发到后端（检查 `deploy/docker/nginx.conf`）

---

## 11. 获取帮助

如果以上步骤无法解决你的问题：

1. 查看后端完整日志：本地看启动终端输出，Docker 看 `docker compose -f docker-compose.yml logs backend --tail=200`
2. 检查 `GET /health` 返回的 `status` 和 `index` 字段
3. 通过 `GET /maintenance/observability/summary` 查看系统运行概况（该接口受 `MCP_API_KEY` 保护，请携带 `X-MCP-API-Key` 或 `Authorization: Bearer`）
4. 提交 Issue 时请附上：错误信息、操作系统、Python 版本、Node.js 版本、使用的 Profile 档位
