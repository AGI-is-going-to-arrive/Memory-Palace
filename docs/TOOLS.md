# Memory Palace — MCP 工具参考手册

> **Memory Palace** 通过 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 为 AI Agent 提供持久化记忆能力。
> 本文档是所有 9 个 MCP 工具的完整参考，适合首次接入的新手用户阅读。

---

## 目录

- [快速参考表](#快速参考表)
- [核心概念](#核心概念)
- [工具详细说明](#工具详细说明)
  - [read_memory — 读取记忆](#read_memory)
  - [create_memory — 创建记忆](#create_memory)
  - [update_memory — 更新记忆](#update_memory)
  - [delete_memory — 删除记忆](#delete_memory)
  - [add_alias — 添加别名](#add_alias)
  - [search_memory — 检索记忆](#search_memory)
  - [compact_context — 会话压缩](#compact_context)
  - [rebuild_index — 索引重建](#rebuild_index)
  - [index_status — 索引状态查询](#index_status)
- [返回值通用字段](#返回值通用字段)
- [降级 (Degradation) 机制](#降级机制)
- [推荐工作流 (Skills 策略)](#推荐工作流)
- [检索配置 (Profile C/D)](#检索配置)

---

## 快速参考表

| 工具 | 类别 | 一句话说明 |
|---|---|---|
| `read_memory` | 📖 读取 | 按 URI 读取记忆内容，支持整段 / 分片 / 范围读取 |
| `create_memory` | ✏️ 写入 | 在指定父 URI 下创建新的记忆节点 |
| `update_memory` | ✏️ 写入 | 更新已有记忆的内容、优先级或 disclosure |
| `delete_memory` | ✏️ 写入 | 按 URI 删除记忆路径 |
| `add_alias` | ✏️ 写入 | 为同一条记忆创建另一个 URI 入口（别名） |
| `search_memory` | 🔍 检索 | 通过关键词 / 语义 / 混合模式搜索记忆 |
| `compact_context` | 🧹 治理 | 将当前会话上下文压缩为持久化摘要 |
| `rebuild_index` | 🔧 维护 | 触发检索索引重建或 sleep-time 整合任务 |
| `index_status` | 🔧 维护 | 查询索引可用性、队列深度与运行时状态 |

---

## 核心概念

### URI 地址体系

Memory Palace 使用 `domain://path` 格式来寻址每一条记忆：

```
core://agent              ← 核心域下的 "agent" 路径
writer://chapter_1/scene  ← 写作域下的层级路径
system://boot             ← 系统内置 URI（只读）
```

**常用域（Domain）：**

- `core` — 核心记忆（人格、偏好、关键事实）
- `writer` — 写作域（故事、章节）
- `system` — 系统保留（`boot` / `index` / `recent`），不可写入

> 💡 优先级 (`priority`) 是一个整数，**数字越小优先级越高**（0 最高）。它决定了检索排序和冲突解决时的先后顺序。

### Write Guard（写入守卫）

`create_memory` 和 `update_memory` 在执行前会自动调用 **Write Guard**，用于：

- 检测是否已有重复内容（避免冗余写入）
- 建议合并到已有记忆（返回 `UPDATE` / `NOOP` 动作）

Write Guard 的决策方法可能包括 `llm`、`embedding`、`keyword`、`fallback`、`none`，取决于当前配置和服务可用性。

---

## 工具详细说明

<a id="read_memory"></a>

### 📖 `read_memory`

**功能：** 按 URI 读取记忆内容。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:1564-1832 -->
```python
read_memory(
    uri: str,                       # 必填，记忆 URI
    chunk_id: Optional[int] = None, # 可选，分片索引（0 起始）
    range: Optional[str] = None,    # 可选，字符范围（如 "0:500"）
    max_chars: Optional[int] = None, # 可选，返回字符数上限
    include_ancestors: Optional[bool] = False # 可选，是否附带父链记忆（仅非 system URI）
)
```

**系统 URI（特殊地址）：**

| URI | 用途 | 何时使用 |
|---|---|---|
| `system://boot` | 加载核心记忆 + 最近记忆 | 每次**会话启动**时调用 |
| `system://index` | 查看所有记忆的完整索引 | 需要**概览全部记忆**时 |
| `system://index-lite` | 查看 gist 轻量索引摘要 | 需要**低成本快速概览**时 |
| `system://audit` | 查看聚合观测/审计摘要 | 需要**排障与运行态巡检**时 |
| `system://recent` | 最近修改的 10 条记忆 | 快速查看**最新变更** |
| `system://recent/N` | 最近修改的 N 条记忆 | 自定义数量（最多 100） |

**返回值格式：**

- **默认模式**（不传 `chunk_id` / `range` / `max_chars`）：返回格式化的纯文本
- **分片模式**（传入任一可选参数）：返回 JSON 字符串，包含 `selection` 元信息

**使用示例：**

```python
# 会话启动时加载核心记忆
read_memory("system://boot")

# 读取某条具体记忆
read_memory("core://agent/my_user")

# 分片读取大段内容（第 0 片）
read_memory("core://agent", chunk_id=0)

# 按字符范围读取
read_memory("core://agent", range="0:500")
```

> ⚠️ `chunk_id` 和 `range` **不能同时使用**。

---

<a id="create_memory"></a>

### ✏️ `create_memory`

**功能：** 在父 URI 下创建一条新记忆。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:1835-2015 -->
```python
create_memory(
    parent_uri: str,              # 必填，父 URI（如 "core://agent"）
    content: str,                 # 必填，记忆正文
    priority: int,                # 必填，检索优先级（数字越小越优先）
    title: Optional[str] = None,  # 可选，路径名（仅限 a-z/0-9/_/-）
    disclosure: str = ""          # 可选，触发条件描述
)
```

**关键行为：**

1. 创建前自动执行 **Write Guard** 检查
2. 若 Guard 判定为 `NOOP` / `UPDATE` / `DELETE`，创建会被阻止，返回建议目标 `guard_target_uri`
3. `title` 只允许字母、数字、下划线和连字符（不允许空格和特殊字符）
4. 若省略 `title`，系统自动分配数字 ID

**使用示例：**

```python
# 创建一条核心记忆
create_memory(
    "core://",
    "用户喜欢简洁的代码风格",
    priority=2,
    title="coding_style",
    disclosure="当我写代码或 review 代码时"
)

# 在已有路径下创建子记忆
create_memory(
    "core://agent",
    "每次对话开始时先问候用户",
    priority=1,
    title="greeting_rule",
    disclosure="每次会话启动时"
)
```

---

<a id="update_memory"></a>

### ✏️ `update_memory`

**功能：** 更新已有记忆的内容或元数据。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:2017-2383 -->
```python
update_memory(
    uri: str,                          # 必填，目标 URI
    old_string: Optional[str] = None,  # Patch 模式：待替换的原文
    new_string: Optional[str] = None,  # Patch 模式：替换后的新文本
    append: Optional[str] = None,      # Append 模式：追加到末尾的文本
    priority: Optional[int] = None,    # 可选，新优先级
    disclosure: Optional[str] = None   # 可选，新触发条件
)
```

**两种编辑模式（互斥）：**

| 模式 | 参数 | 说明 |
|---|---|---|
| **Patch 模式** | `old_string` + `new_string` | 精确查找 `old_string` 并替换为 `new_string`。`old_string` 必须唯一命中 |
| **Append 模式** | `append` | 将文本追加到现有内容末尾 |

> ⚠️ **没有全量替换模式。** 必须通过 `old_string` / `new_string` 明确指定修改内容，防止意外覆盖。
>
> ⚠️ **更新前请先 `read_memory`**，确保你了解将被修改的内容。

**使用示例：**

```python
# Patch 模式：精确替换一段文字
update_memory(
    "core://agent/my_user",
    old_string="旧的偏好描述",
    new_string="新的偏好描述"
)

# Append 模式：追加内容
update_memory("core://agent", append="\n## 新章节\n这是追加的内容")

# 仅修改元数据（不触发 Write Guard）
update_memory("core://agent/my_user", priority=5)
```

---

<a id="delete_memory"></a>

### ✏️ `delete_memory`

**功能：** 删除指定 URI 路径。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:2385-2446 -->
```python
delete_memory(
    uri: str  # 必填，要删除的 URI
)
```

**注意事项：**

- 删除的是 **URI 路径**，而非底层记忆正文的版本链
- 如果一条记忆有多个别名路径，删除其中一个不影响其他别名
- 删除前建议先 `read_memory` 确认内容

**使用示例：**

```python
delete_memory("core://agent/old_note")
```

---

<a id="add_alias"></a>

### ✏️ `add_alias`

**功能：** 为同一条记忆添加别名 URI，提升可达性。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:2448-2516 -->
```python
add_alias(
    new_uri: str,                       # 必填，新的别名 URI
    target_uri: str,                    # 必填，已有记忆的 URI
    priority: int = 0,                  # 可选，此别名的检索优先级
    disclosure: Optional[str] = None    # 可选，此别名的触发条件
)
```

**说明：** 别名可以跨域——例如将 `writer://` 域的记忆链接到 `core://` 域。

**使用示例：**

```python
add_alias(
    "core://timeline/2024/05/20",
    "core://agent/my_user/first_meeting",
    priority=1,
    disclosure="当我想回忆我们是如何认识的"
)
```

---

<a id="search_memory"></a>

### 🔍 `search_memory`

**功能：** 通过关键词、语义或混合模式检索记忆。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:2518-2845 -->
```python
search_memory(
    query: str,                                  # 必填，搜索关键词
    mode: Optional[str] = None,                  # 可选，"keyword" / "semantic" / "hybrid"
    max_results: Optional[int] = None,           # 可选，返回结果数上限
    candidate_multiplier: Optional[int] = None,  # 可选，候选池倍率
    include_session: Optional[bool] = None,      # 可选，是否包含本会话记忆
    filters: Optional[Dict] = None,              # 可选，过滤条件
    scope_hint: Optional[str] = None             # 可选，查询侧作用域提示（domain/path_prefix/URI 前缀）
)
```

**检索模式：**

| 模式 | 说明 |
|---|---|
| `keyword` | 基于 BM25 关键词匹配（默认模式） |
| `semantic` | 基于 Embedding 向量语义搜索（需配置 Embedding API） |
| `hybrid` | 关键词 + 语义 + Reranker 混合检索 |

**过滤条件 (`filters`)：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `domain` | `str` | 限定域，如 `"core"` |
| `path_prefix` | `str` | 限定路径前缀，如 `"agent/my_user"` |
| `max_priority` | `int` | 只返回 priority ≤ 此值的记忆 |
| `updated_after` | `str` | ISO 时间过滤，如 `"2026-01-31T12:00:00Z"` |

**响应字段说明：**

| 字段 | 说明 |
|---|---|
| `query_effective` | 实际生效的查询文本 |
| `query_preprocess` | 查询预处理信息 |
| `intent` | 意图分类：`factual` / `exploratory` / `temporal` / `causal` / `unknown` |
| `mode_applied` | 实际使用的检索模式 |
| `results` | 搜索结果列表 |
| `degrade_reasons` | 降级原因（如有） |

**使用示例：**

```python
# 简单关键词搜索
search_memory("coding style")

# 混合搜索 + 域过滤
search_memory(
    "chapter arc",
    mode="hybrid",
    max_results=8,
    include_session=True,
    filters={"domain": "writer", "path_prefix": "chapter_1"}
)
```

---

<a id="compact_context"></a>

### 🧹 `compact_context`

**功能：** 将当前会话上下文压缩为持久化记忆摘要。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:2847-2901 -->
```python
compact_context(
    reason: str = "manual",  # 可选，压缩原因标签
    force: bool = False,     # 可选，强制压缩（不判断阈值）
    max_lines: int = 12      # 可选，摘要最大行数（最小 3）
)
```

**摘要产物：**

- **Gist**：简短摘要，用于快速回忆
- **Trace**：原始要点留痕，保留关键上下文

**Gist 生成链路（按优先级自动降级）：**

1. `llm_gist` — 调用 LLM 生成摘要（需在 `.env` 中配置 OpenAI-compatible API）
2. `extractive_bullets` — 提取式要点
3. `sentence_fallback` — 句子级降级
4. `truncate_fallback` — 截断降级

**响应字段：**

| 字段 | 说明 |
|---|---|
| `gist_method` | 当前 Gist 生成策略 |
| `quality` | Gist 质量分（0–1） |
| `source_hash` | Trace 源内容哈希（用于一致性校验） |
| `index_queued` / `index_dropped` / `index_deduped` | 索引入队统计 |
| `degrade_reasons` | 降级原因（如有） |

**使用示例：**

```python
# 让系统自动判断是否需要压缩
compact_context(force=False)

# 强制压缩并限制摘要行数
compact_context(reason="long_session", force=True, max_lines=8)
```

---

<a id="rebuild_index"></a>

### 🔧 `rebuild_index`

**功能：** 触发检索索引重建或 sleep-time 整合任务。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:2903-3047 -->
```python
rebuild_index(
    memory_id: Optional[int] = None,     # 可选，目标记忆 ID（省略则重建全量）
    reason: str = "manual",              # 可选，审计标签
    wait: bool = False,                  # 可选，是否等待任务完成再返回
    timeout_seconds: int = 30,           # 可选，等待超时秒数（wait=True 时生效）
    sleep_consolidation: bool = False    # 可选，触发 sleep-time 整合任务
)
```

**两种模式：**

| 模式 | 条件 | 行为 |
|---|---|---|
| **索引重建** | `sleep_consolidation=False`（默认） | 执行 `rebuild_index` / `reindex_memory` 队列任务 |
| **Sleep-time 整合** | `sleep_consolidation=True` | 离线扫描碎片和重复记忆，生成清理预览 |

**Sleep-time 整合详情：**

- 扫描孤儿候选并生成去重预览
- 对碎片化路径生成 rollup 预览
- 默认是 **preview-only**（不执行实际删除/写入）：
  - 设置 `RUNTIME_SLEEP_DEDUP_APPLY=1` 才执行重复清理
  - 设置 `RUNTIME_SLEEP_FRAGMENT_ROLLUP_APPLY=1` 才写入 rollup gist
- ⚠️ `memory_id` 和 `sleep_consolidation=True` **不能同时使用**

**队列满载保护：**

- HTTP 维护接口返回 `503` + `index_job_enqueue_failed`
- MCP 返回 `ok=false` + `error=queue_full`

**使用示例：**

```python
# 全量重建并等待完成
rebuild_index(wait=True)

# 重建单条记忆的索引
rebuild_index(memory_id=42, wait=True)

# 触发 sleep-time 整合（仅预览）
rebuild_index(sleep_consolidation=True, wait=True)
```

---

<a id="index_status"></a>

### 🔧 `index_status`

**功能：** 查询检索索引可用性、统计信息和运行时状态。

**函数签名：**
<!-- 源码位置: backend/mcp_server.py:3049-3087 -->
```python
index_status()  # 无参数
```

**返回信息包含：**

| 字段 | 说明 |
|---|---|
| `index_available` | 索引是否可用 |
| `degraded` | 是否降级 |
| `runtime.index_worker` | 队列深度、活跃任务、成功/失败/取消统计 |
| `runtime.sleep_consolidation` | Sleep 整合调度状态（`enabled` / `scheduled` / `reason`） |
| `runtime.write_lanes` | 写入通道状态 |

**使用示例：**

```python
# 检查索引健康状态
index_status()
```

---

## 返回值通用字段

### Write Guard 字段

`create_memory` 和 `update_memory` 的返回值中包含以下 Write Guard 信息：

| 字段 | 可能值 | 说明 |
|---|---|---|
| `guard_action` | `ADD` / `UPDATE` / `NOOP` / `DELETE` / `BYPASS` | Guard 的决策动作 |
| `guard_reason` | 字符串 | 决策原因 |
| `guard_method` | `llm` / `embedding` / `keyword` / `fallback` / `none` | 检测方法 |

### 索引入队统计字段

`create_memory`、`update_memory`、`compact_context` 的返回值还包含：

| 字段 | 说明 |
|---|---|
| `index_queued` | 实际入队任务数 |
| `index_dropped` | 未成功入队的任务数（如队列已满） |
| `index_deduped` | 去重后未重复入队的任务数 |

> ⚠️ 当 `index_dropped > 0` 时，表示有索引任务未能入队。客户端应将其视为降级信号，结合 `degrade_reasons` 进行告警或补偿。

---

## 降级机制

检索链路中，当远程 Embedding / Reranker 服务不可用或返回异常时，系统会**自动降级**并在响应中返回 `degrade_reasons` 字段。  
写入链路中，若出现 `write_guard_exception`，系统会 fail-closed 拒绝写入并记录审计，不属于“继续写入的自动降级”。

**常见降级原因：**

| 原因 | 说明 |
|---|---|
| `embedding_fallback_hash` | Embedding API 不可用，回退到本地 hash |
| `embedding_request_failed` | Embedding 请求失败 |
| `reranker_request_failed` | Reranker 请求失败 |
| `write_guard_exception` | Write Guard 执行异常，写入已被拒绝（fail-closed） |
| `query_preprocess_failed` | 查询预处理失败 |
| `index_enqueue_dropped` | 索引任务入队失败 |

> 💡 **建议：** 客户端策略中应把 `degrade_reasons` 字段作为告警信号。当检测到降级时，可调用 `rebuild_index(wait=True)` + `index_status()` 尝试恢复。

---

## 推荐工作流

以下工作流适用于所有支持 MCP 的客户端（Codex / Claude Code / Gemini CLI / Cursor / Antigravity / Trae 等）：

### 标准会话流程

```
┌──────────────┐
│  1. 会话启动   │  read_memory("system://boot")
│              │  → 加载核心记忆 + 最近更新
└──────┬───────┘
       ▼
┌──────────────┐
│  2. 话题回忆   │  search_memory(query, include_session=True)
│              │  → 搜索相关记忆，包含本会话上下文
└──────┬───────┘
       ▼
┌──────────────┐
│  3. 写入前检查 │  search_memory → 确认无重复 → create_memory / update_memory
│              │  → 避免创建冗余记忆
└──────┬───────┘
       ▼
┌──────────────┐
│  4. 长会话压缩 │  compact_context(force=False)
│              │  → 系统自动判断是否需要压缩
└──────┬───────┘
       ▼
┌──────────────┐
│  5. 降级恢复   │  rebuild_index(wait=True) → index_status()
│              │  → 检测到降级时重建索引并确认状态
└──────────────┘
```

详细 Skills 编排策略见：[skills/MEMORY_PALACE_SKILLS.md](skills/MEMORY_PALACE_SKILLS.md)

---

## 检索配置

Memory Palace 支持多种检索 Profile。Profile C 和 D 使用混合检索路线（`keyword + semantic + reranker`），需要额外配置。

### 必需环境变量

在 `.env` 中配置 OpenAI-compatible API 参数：
<!-- 参考: .env.example 第 57-77 行 -->

```bash
# ── Embedding 配置 ──
RETRIEVAL_EMBEDDING_BACKEND=none      # 可选: none / hash / router / api / openai
RETRIEVAL_EMBEDDING_API_BASE=         # API 地址
RETRIEVAL_EMBEDDING_API_KEY=          # API 密钥
RETRIEVAL_EMBEDDING_MODEL=            # 模型名称
RETRIEVAL_EMBEDDING_DIM=1024            # 向量维度

# ── Reranker 配置 ──
RETRIEVAL_RERANKER_ENABLED=false      # 是否启用 Reranker
RETRIEVAL_RERANKER_API_BASE=          # API 地址
RETRIEVAL_RERANKER_API_KEY=           # API 密钥
RETRIEVAL_RERANKER_MODEL=             # 模型名称

# ── 权重调参 ──
RETRIEVAL_RERANKER_WEIGHT=0.25        # Reranker 权重（首要调参项）
RETRIEVAL_HYBRID_KEYWORD_WEIGHT=0.7   # 关键词权重
RETRIEVAL_HYBRID_SEMANTIC_WEIGHT=0.3  # 语义权重
```

> 💡 **首要调参项**是 `RETRIEVAL_RERANKER_WEIGHT`。即使 Embedding / Reranker 是本地部署的，也必须配置 OpenAI-compatible API 参数。
>
> 配置语义说明：`RETRIEVAL_EMBEDDING_BACKEND` 仅控制 Embedding 路径；Reranker 没有 `RETRIEVAL_RERANKER_BACKEND` 开关。Reranker 参数优先使用 `RETRIEVAL_RERANKER_*`，缺失时才回退 `ROUTER_*`（最后回退 `OPENAI_*` 的 base/key）。
>
> 预置 Profile 配置文件位于 `deploy/profiles/` 目录下（macOS / Windows / Docker）。

---

*本文档基于 `backend/mcp_server.py` 源码生成，所有参数签名和行为描述均可追溯至代码实现。*
