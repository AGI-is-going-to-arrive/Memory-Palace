# Memory Palace 技术总览

本文档面向需要了解系统内部实现或进行二次开发的技术用户，涵盖后端、前端、MCP 工具层、运行时与部署架构。

---

## 1. 技术栈

| 层 | 技术 | 版本要求 | 作用 |
|---|---|---|---|
| Backend | FastAPI + SQLAlchemy + SQLite | FastAPI ≥0.109 · SQLAlchemy ≥2.0 · aiosqlite ≥0.19 | 记忆读写、检索、审查、维护 |
| MCP | `mcp.server.fastmcp` | mcp ≥0.1 | 为 Codex / Claude Code / Gemini CLI / Cursor 等暴露统一工具面 |
| Frontend | React + Vite + TailwindCSS + Framer Motion | React ≥18.2 · Vite ≥7.3 · TailwindCSS ≥3.3 · Framer Motion ≥12.34 | 可视化管理 Dashboard |
| Runtime | 内置队列与 worker | — | 写入串行化、索引重建、vitality 衰减、sleep consolidation |
| Deployment | Docker Compose + profile 脚本 | Docker ≥20 · Compose ≥2.0 | A/B/C/D 档位快速部署 |

核心依赖详见 `backend/requirements.txt` 和 `frontend/package.json`。

---

## 2. 后端结构

```
backend/
├── main.py               # FastAPI 入口（v1.0.1），注册路由，生命周期管理
├── mcp_server.py          # 9 个 MCP 工具实现（3100+ 行）
├── runtime_state.py       # 写入 lane、索引 worker、vitality 衰减、cleanup review 管理
├── run_sse.py             # SSE 传输层，支持 API Key 鉴权门控
├── mcp_wrapper.py         # MCP 启动封装
├── api/
│   ├── __init__.py        # 路由导出
│   ├── browse.py          # 记忆浏览与写入接口（prefix: /browse）
│   ├── review.py          # 审查、回滚与集成接口（prefix: /review）
│   ├── maintenance.py     # 维护、观测与 vitality 清理接口（prefix: /maintenance）
│   └── utils.py           # Diff 计算工具（基于 diff-match-patch）
├── db/
│   ├── __init__.py        # 客户端工厂（get_sqlite_client / close_sqlite_client）
│   ├── sqlite_client.py   # 核心数据库层（CRUD、检索、write_guard、gist、vitality、embedding、rerank）
│   ├── snapshot.py        # 快照管理器（按 session 记录写操作的前置状态）
│   ├── migration_runner.py# 自动数据库迁移执行器
│   └── migrations/        # SQL 迁移脚本目录
├── models/
│   ├── __init__.py        # 模型导出
│   └── schemas.py         # Pydantic 数据模型定义
└── tests/                 # 测试与基准测试（含 benchmark/ 目录）
```

### 核心模块说明

- **`main.py`**：FastAPI 应用入口（版本 `v1.0.1`），负责生命周期管理（数据库初始化、legacy 数据库文件兼容恢复）、CORS 配置、路由注册（`review`、`browse`、`maintenance`）和健康检查（含索引状态、write lane 与 index worker 运行时状态报告）。
- **`mcp_server.py`**：实现 9 个 MCP 工具，包括 URI 解析（`domain://path` 格式）、快照管理、write guard 决策、会话缓存、异步索引入队等核心逻辑。同时提供系统 URI (`system://boot`、`system://index`、`system://recent`) 资源。
- **`runtime_state.py`**：管理写入 lane（串行化写操作）、索引 worker（异步队列处理索引重建任务）、vitality 衰减调度、cleanup review 审批流程和 sleep consolidation 调度等运行时状态。
- **`db/sqlite_client.py`**：SQLite 数据库操作层，包含记忆 CRUD、keyword/semantic/hybrid 三种检索模式、write_guard 逻辑（支持语义匹配 + 关键词匹配 + LLM 决策三级判定）、gist 生成与缓存、vitality 评分与衰减、embedding 获取（支持远程 API 和本地 hash 两种模式）、reranker 集成。

---

## 3. HTTP API 入口

### 浏览与写入（`/browse`）

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| `GET` | `/browse/node` | 无 | 浏览记忆树（含子节点、面包屑、gist、别名） |
| `POST` | `/browse/node` | API Key | 创建记忆节点（含 write_guard） |
| `PUT` | `/browse/node` | API Key | 更新记忆节点（含 write_guard） |
| `DELETE` | `/browse/node` | API Key | 删除记忆路径 |

### 审查与回滚（`/review`）

路由级 API Key 鉴权（所有端点均需要鉴权）。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/review/sessions` | 列出审查会话 |
| `GET` | `/review/sessions/{session_id}/snapshots` | 查看会话快照列表 |
| `GET` | `/review/sessions/{session_id}/snapshots/{resource_id}` | 查看快照详情 |
| `GET` | `/review/sessions/{session_id}/diff/{resource_id}` | 查看版本 diff |
| `POST` | `/review/sessions/{session_id}/rollback/{resource_id}` | 执行回滚 |
| `DELETE` | `/review/sessions/{session_id}/snapshots/{resource_id}` | 确认集成（删除快照） |
| `DELETE` | `/review/sessions/{session_id}` | 清除整个 session 的快照 |
| `GET` | `/review/deprecated` | 列出所有 deprecated 记忆 |
| `DELETE` | `/review/memories/{memory_id}` | 永久删除已审查的记忆 |
| `POST` | `/review/diff` | 通用文本 diff 计算 |

### 维护与观测（`/maintenance`）

路由级 API Key 鉴权（所有端点均需要鉴权）。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/maintenance/orphans` | 查看孤儿记忆（deprecated 或无路径指向） |
| `GET` | `/maintenance/orphans/{memory_id}` | 查看孤儿记忆详情 |
| `DELETE` | `/maintenance/orphans/{memory_id}` | 永久删除孤儿记忆 |
| `POST` | `/maintenance/vitality/decay` | 触发 vitality 衰减 |
| `POST` | `/maintenance/vitality/candidates/query` | 查询清理候选记忆 |
| `POST` | `/maintenance/vitality/cleanup/prepare` | 准备清理审批（生成 review_id + token） |
| `POST` | `/maintenance/vitality/cleanup/confirm` | 确认并执行清理（需 review_id + token + 确认短语） |
| `GET` | `/maintenance/index/worker` | 查看索引 worker 状态 |
| `GET` | `/maintenance/index/job/{job_id}` | 查看索引任务详情 |
| `POST` | `/maintenance/index/job/{job_id}/cancel` | 取消索引任务 |
| `POST` | `/maintenance/index/job/{job_id}/retry` | 重试索引任务 |
| `POST` | `/maintenance/index/rebuild` | 触发全量索引重建 |
| `POST` | `/maintenance/index/reindex/{memory_id}` | 单条索引重建 |
| `POST` | `/maintenance/index/sleep-consolidation` | 触发 sleep consolidation |
| `POST` | `/maintenance/observability/search` | 观测搜索（含检索统计） |
| `GET` | `/maintenance/observability/summary` | 观测概览 |

完整 API 文档可启动后端后访问 `http://127.0.0.1:8000/docs`（Swagger UI）。

---

## 4. MCP 工具实现

实现文件：`backend/mcp_server.py`

| 工具 | 类型 | 说明 |
|---|---|---|
| `read_memory` | 读取 | 读取记忆内容，支持整段与分片（chunk_id / range / max_chars），支持系统 URI（`system://boot`、`system://index`、`system://recent`） |
| `create_memory` | 写入 | 创建新记忆节点（含 write_guard，进入 write lane 串行化） |
| `update_memory` | 写入 | 更新已有记忆（old_string/new_string 精准替换 或 append 追加，含 write_guard） |
| `delete_memory` | 写入 | 删除记忆路径（进入 write lane 串行化） |
| `add_alias` | 写入 | 为同一记忆添加别名路径（可跨 domain） |
| `search_memory` | 检索 | 统一检索入口（keyword/semantic/hybrid），支持意图分类与策略模板 |
| `compact_context` | 治理 | 将当前会话上下文压缩为长期记忆摘要（进入 write lane 串行化） |
| `rebuild_index` | 维护 | 全量或单条索引重建，支持同步等待与 sleep consolidation |
| `index_status` | 维护 | 查询索引可用性、运行时状态与配置开关 |

工具返回约定与降级语义详见：[TOOLS.md](TOOLS.md)

---

## 5. 前端结构

```
frontend/src/
├── App.jsx                                    # 路由与页面骨架
├── main.jsx                                   # React 入口
├── index.css                                  # 全局样式（TailwindCSS）
├── features/
│   ├── memory/MemoryBrowser.jsx               # 树形浏览、编辑、gist 视图
│   ├── review/ReviewPage.jsx                  # diff、rollback、integrate
│   ├── maintenance/MaintenancePage.jsx        # vitality 清理与维护任务
│   └── observability/ObservabilityPage.jsx    # 检索统计与任务可观测
├── components/
│   ├── AgentationLite.jsx                     # 轻量化 Agent 集成组件
│   ├── DiffViewer.jsx                         # Diff 可视化
│   ├── FluidBackground.jsx                    # 流体动画背景
│   ├── GlassCard.jsx                          # 毛玻璃卡片
│   └── SnapshotList.jsx                       # 快照列表
├── lib/
│   ├── api.js                                 # 统一 API 客户端与运行时鉴权注入
│   ├── api.test.js                            # API 客户端单元测试
│   └── api.contract.test.js                   # API 鉴权契约测试
└── test/                                      # 前端测试目录
```

### Dashboard 四大功能模块

| 模块 | 路由 | 功能 |
|---|---|---|
| Memory Browser | `/memory` | 按域（domain）树形浏览、内联编辑、查看 gist 摘要、别名管理 |
| Review | `/review` | 查看写入快照 diff、支持 rollback 回滚和 integrate 确认、清理 deprecated 记忆 |
| Maintenance | `/maintenance` | 查看 vitality 评分、清理孤儿记忆、触发索引重建、管理清理审批流程 |
| Observability | `/observability` | 检索日志与统计、任务执行记录、索引 worker 状态、系统状态概览 |

---

## 6. 前端鉴权注入模型

前端不会从 `VITE_*` 构建变量读取维护密钥，采用运行时注入方式：

```html
<script>
  window.__MEMORY_PALACE_RUNTIME__ = {
    maintenanceApiKey: "<YOUR_MCP_API_KEY>",
    maintenanceApiKeyMode: "header"
  };
</script>
```

`maintenanceApiKeyMode` 支持：`header`（发送 `X-MCP-API-Key` 头）或 `bearer`（发送 `Authorization: Bearer` 头）。

> 兼容性：运行时对象也兼容旧字段名 `window.__MCP_RUNTIME_CONFIG__`。
>
> 代码参考：`frontend/src/lib/api.js` 第 14 行。

---

## 7. 数据与任务流

### 写入路径

1. `create_memory` / `update_memory` 进入 **write lane**（串行化写操作）。
2. 写前执行 **write_guard** 判定（核心决策：`ADD` / `UPDATE` / `NOOP` / `DELETE`；`BYPASS` 为上层 metadata-only 更新时的流程标记）。
   - write_guard 支持三级判定链：语义匹配 → 关键词匹配 → LLM 决策（可选）。
3. 生成 **snapshot** 与版本变更（按 `path` 和 `memory` 两维度分别记录）。
4. 入队 **索引任务**（队列满会返回 `index_dropped` / `queue_full`）。

### 检索路径

1. **`preprocess_query`** 对查询文本进行预处理（标准化空白、分词、多语言/URI 保留）。
2. **`classify_intent`** 默认按 4 种核心意图路由；无显著关键词信号时默认 `factual`（模板 `factual_high_precision`），当信号冲突或低信号混合时回退 `unknown`（模板 `default`）：
   - `factual` → 策略模板 `factual_high_precision`（高精度匹配）
   - `exploratory` → 策略模板 `exploratory_high_recall`（高召回探索）
   - `temporal` → 策略模板 `temporal_time_filtered`（时间过滤）
   - `causal` → 策略模板 `causal_wide_pool`（因果推理，宽候选池）
   - `unknown` → 策略模板 `default`（冲突或低信号混合时保守回退）
3. 执行 **keyword / semantic / hybrid** 检索。
4. 可选 **reranker** 重排序（通过远程 API 调用）。
5. 返回 `results` 与 `degrade_reasons`。

> 意图分类使用 `keyword_scoring_v2` 方法实现（`db/sqlite_client.py` `classify_intent` 方法），通过关键词匹配评分与排名进行意图推断，无需外部模型调用。

![记忆写入与审查时序图](images/记忆写入与审查时序图.png)

---

## 8. 部署口径

| 场景 | 宿主机端口 | 容器内部端口 | 说明 |
|---|---|---|---|
| 本地开发 | Backend `8000` · Frontend `5173` | — | 直接启动 |
| Docker 默认 | Backend `18000` · Frontend `3000` | Backend `8000` · Frontend `8080` | 端口可通过环境变量覆盖 |

Docker 端口环境变量：

- Backend：`MEMORY_PALACE_BACKEND_PORT`（回退到 `NOCTURNE_BACKEND_PORT`，默认 `18000`）
- Frontend：`MEMORY_PALACE_FRONTEND_PORT`（回退到 `NOCTURNE_FRONTEND_PORT`，默认 `3000`）

相关文件：

- Compose 文件：`docker-compose.yml`
- 镜像定义：`deploy/docker/Dockerfile.backend`（基于 `python:3.11-slim`）、`deploy/docker/Dockerfile.frontend`（构建阶段 `node:22-alpine`，运行阶段 `nginxinc/nginx-unprivileged:1.27-alpine`）
- Nginx 配置：`deploy/docker/nginx.conf`
- 入口脚本：`deploy/docker/backend-entrypoint.sh`

---

## 9. 安全默认值

- `/maintenance/*`、`/review/*` 所有端点均需 API Key 鉴权。
- `/browse` 写操作（POST/PUT/DELETE）通过端点级 `Depends(require_maintenance_api_key)` 门控；GET 读取不需要鉴权。
- `MCP_API_KEY` 为空时默认 **fail-closed**（拒绝请求）。
- 仅在 `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true` **且** loopback 请求（`127.0.0.1` / `::1` / `localhost`）时可本地放行。
- Docker 容器默认以非 root 用户运行：
  - Backend：自定义用户 `app`（UID `10001`，GID `10001`）
  - Frontend：使用 `nginx-unprivileged` 官方非 root 镜像

详细策略：[SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md)
