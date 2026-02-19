# Memory Palace Skills 方案（设计与运维参考文档）

> **Claude Code Skill 文件**：[`.claude/skills/memory-palace/SKILL.md`](../../.claude/skills/memory-palace/SKILL.md)
>
> 本文档是面向人类的完整策略设计文档，涵盖分层架构、参数调优、本地模型接入等运维细节。
> LLM 可执行的命令式规则已提取至上述 Skill 文件。

## 1. 目标

把 Memory Palace 的 MCP 工具变成稳定的“记忆操作系统”：

- 减少乱写、重复写、错误覆盖。
- 让检索、压缩、重建索引都有明确触发条件。
- 让人类可以在 `/review` 与 `/observability` 追踪每次决策。

---

## 2. 分层设计

### MCP 工具层（执行）

- 负责事实读写：`read_memory/create_memory/update_memory/delete_memory/add_alias`
- 负责检索与运行时：`search_memory/compact_context/rebuild_index/index_status`

### Skills 策略层（决策）

- 负责“何时调用什么工具”。
- 负责优先级、阈值、回退策略。
- 负责把降级行为反馈到可观测与运维动作。

### 多管理端落地（Codex / Claude / Gemini / IDE）

- Codex / Claude Code / Gemini CLI：把本文件规则写入项目级系统提示或 skill 指南。
- Cursor / Antigravity / Trae：把触发规则写入 workspace rules / project instructions。
- 统一目标：不同端共享同一 memory 操作策略，而不是各端各写一套逻辑。

---

## 3. 统一流程（建议默认）

1. **Boot**：会话开始调用 `read_memory("system://boot")`。
2. **Recall**：先 `search_memory(query, include_session=true)`，命中后再 `read_memory`。
3. **Write**：写前先读；修改用 `update_memory`，新知识用 `create_memory`。
4. **Compact**：达到阈值触发 `compact_context`，将会话沉淀到 `notes://`。
5. **Recover**：出现持续降级时 `rebuild_index(wait=true)`，并记录 `index_status`。

写入阶段必须解析 `create_memory/update_memory` 返回中的 guard 字段：

- `guard_action`：当为 `NOOP`/`DELETE` 时视为“本次写入被拦截”；当为 `UPDATE` 时需结合 `guard_target_id` 判断是否为同一记忆（同一记忆通常可继续更新）。
- `guard_reason` / `guard_method`：用于记录为何被拦截（embedding/keyword/llm/fallback）。
- 建议策略：连续 guard 拦截时先 `search_memory` + `read_memory`，确认是否应转为 `update_memory`。

---

## 4. 触发规则（可直接实现）

| 触发事件 | 触发条件 | 动作 |
|---|---|---|
| 新会话 | 首轮响应前 | `read_memory(system://boot)` |
| URI 不确定 | 无法定位具体节点 | `search_memory(query)` |
| 长上下文 | 字符或事件数超阈值 | `compact_context(force=false)` |
| 检索持续降级 | 同类 `degrade_reasons`（索引相关）连续出现 | `rebuild_index(wait=true)` + `index_status()` |
| 写入被 guard 拦截 | `guard_action` 为 `NOOP/UPDATE/DELETE` | 先 `search_memory` + `read_memory`，再决定是否 `update_memory` |
| 结构迁移 | 节点重命名/迁移 | `add_alias` 后 `delete_memory` |

---

## 5. Unified C/D 参数策略

`C` 与 `D` 统一看作同一路线：

- `SEARCH_DEFAULT_MODE=hybrid`
- `RETRIEVAL_RERANKER_ENABLED=true`
- `RETRIEVAL_RERANKER_WEIGHT` 作为首要调参项

推荐调参顺序：

1. `RETRIEVAL_RERANKER_WEIGHT`
2. `SEARCH_DEFAULT_CANDIDATE_MULTIPLIER`
3. embedding/reranker 模型本身

---

## 6. 本地模型接入规范

即使模型在本地，仍按 OpenAI-compatible API 接入：

- Embedding: `RETRIEVAL_EMBEDDING_API_BASE` / `API_KEY` / `MODEL`
- Reranker: `RETRIEVAL_RERANKER_API_BASE` / `API_KEY` / `MODEL`

这保证了 profile、脚本和可观测字段的一致性。

---

## 7. 运行风险与防护

1. **过度重排**：`RETRIEVAL_RERANKER_WEIGHT` 过高会掩盖关键词相关性。
2. **重复写入**：未先检索直接创建，容易产生语义重复节点。
3. **误删路径**：`delete_memory` 前未读取正文会误伤有效记忆。
4. **静默降级**：未监控 `degrade_reasons` 会长期低质量检索。

防护措施：

- 强制执行“写前读”。
- 把 `degrade_reasons` 纳入告警。
- 对关键路径变更要求 `/review` 人工确认。
