# Memory Palace 改进分析：借鉴 Claude Code Memory 设计理念

## 背景

对比 [Claude Code Memory 官方文档](https://code.claude.com/docs/en/memory) 与当前 Memory Palace 项目，在 **不偏离项目原有目的**（AI Agent 长期记忆操作系统）的前提下，提炼出可落地的改进方向。

> 说明：本文件是早期方案分析，不代表当前默认执行语义。`#5/#6` 的现行口径以 `docs/improvement/hold_items_next_step_plan.md` 与 `llmdoc/reference/phase-status.md` 为准（显式触发、`actor+session` 双桶限流、`write_guard` 异常 fail-closed）。

## 核心差异对比

| 维度 | Claude Code Memory | Memory Palace 现状 | 差距评估 |
|---|---|---|---|
| **分层记忆** | 3 层：系统级 → 用户级 → 项目级 CLAUDE.md + Auto Memory | 2 层：domain URI + priority 排序 | 🟡 可增强 |
| **路径规则** | `.claude/rules/` 按 glob 模式匹配文件路径 | 无路径级规则作用域 | 🟡 可借鉴 |
| **自动记忆** | Agent 自动从纠正/偏好中学习，生成 MEMORY.md 索引 + 话题文件 | 有 `compact_context` 自动压缩，但需显式调用 | 🟡 可增强 |
| **审计 & 编辑** | `/memory` 命令查看/编辑/删除 | ✅ 已有完整的 Review 仪表盘（差异对比+回滚） | ✅ **Memory Palace 更强** |
| **检索能力** | 无独立检索引擎 | ✅ 混合检索（keyword + semantic + reranker）+ 意图分类 | ✅ **Memory Palace 更强** |
| **写入守卫** | 无 | ✅ Write Guard + 快照 + 回滚 | ✅ **Memory Palace 更强** |
| **导入外部文件** | `@path/to/import` 语法引入外部 Markdown | 无直接导入机制 | 🔴 缺失 |
| **记忆组织** | 扁平 MEMORY.md 索引 + 话题子文件 | ✅ 树形层级 URI + Gist 视图 | ✅ **Memory Palace 更强** |
| **可观测性** | 无 | ✅ 四视图仪表盘 | ✅ **Memory Palace 更强** |

## 可借鉴的改进方向

以下是 Claude Code Memory 中值得 Memory Palace 借鉴的设计理念，按优先级排列：

---

### 1. 🟢 索引记忆自动生成（MEMORY.md 模式）

**Claude Code 做法**：Auto Memory 自动从对话中提取学习笔记，生成 `MEMORY.md`（精简索引文件，每次会话都加载）+ 话题子文件（`debugging.md`、`api-conventions.md` 等）。

**MP 现状**：有 `compact_context` 可压缩会话到 `notes://` 域，但没有自动索引摘要机制。

**改进建议**：

- 增加一个 **自动索引摘要** 功能：每次 `compact_context` 后，自动在 `system://memory_index` 生成/更新一份精简索引，汇总所有重要记忆的一句话摘要
- 这个索引在 `system://boot` 时自动加载，让 Agent 一眼了解已有全部知识
- 不同于 Claude 的纯文件方式，MP 可以基于已有的 Gist 系统（`memory_gists` 表）自动聚合

> [!TIP]
> 这不需要改变 MP 的底层架构，只需在现有 `compact_context` → `generate_gist` 链路上增加一个聚合步骤。

---

### 2. 🟢 上下文作用域规则（Rules by Path）

**Claude Code 做法**：`.claude/rules/` 下的 Markdown 文件可通过 `paths` frontmatter 指定只在编辑特定文件时生效。

**MP 现状**：记忆通过 URI 层级组织，但没有"根据当前工作上下文自动激活相关记忆"的机制。

**改进建议**：

- 为记忆节点增加可选的 **`scope`** 元数据字段（glob 模式字符串列表）
- 当 Agent 发送 `search_memory` 时，可附带当前工作的文件路径/上下文标签
- 检索引擎额外对 scope 匹配的记忆给予加权
- 实现方式：在 `paths` 表或 `memory_tags` 表中增加 `scope_pattern` 列

> [!NOTE]
> 这是增量改进，不影响现有不带 scope 的记忆的行为。

---

### 3. 🟡 外部文件导入（@import 语法）

**Claude Code 做法**：在 CLAUDE.md 中用 `@path/to/file` 引用外部文件，自动内联加载。

**MP 现状**：记忆内容是自包含的，无法引用外部文件。

**改进建议**：

- 在 `create_memory` / `update_memory` 中支持一个可选的 `import_refs` 参数，指定外部文件路径列表
- 写入时自动读取这些文件内容，作为记忆的附加上下文存储
- 或者更轻量：在记忆内容中支持 `@ref:path` 语法，`read_memory` 时解析并追加引用内容

> [!IMPORTANT]
> 需要注意安全性——限制可引用的路径范围，避免读取敏感文件。可复用现有 `SECURITY_AND_PRIVACY.md` 中的 fail-closed 思路。

---

### 4. 🟡 记忆自动学习触发器

**Claude Code 做法**：Agent 在被用户纠正时自动保存学习笔记，无需显式调用。

**MP 现状**：所有写入都需要 Agent 显式调用 `create_memory` / `update_memory`，依赖 Skills 策略层引导。

**改进建议**：

- 在 MCP Server 层增加一个 **`learn_from_correction`** 工具（或增强现有 `compact_context`）
- 当 Agent 检测到用户纠正行为时，自动提取教训并写入专门的 `corrections://` 域
- 这些纠正记忆可以有更高的优先级和更长的活力半衰期

> [!NOTE]
> 这需要 AI 客户端侧的配合（检测纠正意图），但 MP 可以先提供 MCP 工具接口。

---

### 5. 🟡 多层级记忆继承

**Claude Code 做法**：记忆从系统级 → 用户级 → 项目级层层加载，子目录自动继承父目录的 CLAUDE.md。

**MP 现状**：有 domain 分离（`core://`、`notes://`、`writer://` 等），但没有层级继承的概念。

**改进建议**：

- 利用现有的 URI 树形结构增加 **继承搜索**：当读取 `core://agent/project_a/module_x` 时，自动聚合 `core://agent`、`core://agent/project_a` 的上级记忆
- 在 `read_memory` 中增加 `include_ancestors: bool = false` 参数
- 在 `system://boot` 的启动序列中利用这个机制自动加载层级上下文

---

### 6. 🟢 `/memory` 快速审计命令

**Claude Code 做法**：`/memory` 命令一键查看所有已加载的记忆文件和 auto memory 内容。

**MP 现状**：有 `index_status` 工具和 Review 仪表盘，但缺少一个面向 Agent 的快速审计入口。

**改进建议**：

- 增加一个 `system://audit` 系统 URI，返回：
  - 当前会话已加载的记忆摘要列表
  - Auto flush 历史
  - Write Guard 最近的拦截记录
  - 活力值低于阈值的记忆数量
- 这是一个只读的综合视图，比现有的多个工具分散查询更高效

---

## 不建议借鉴的部分

以下是 Claude Code Memory 的设计中 **不适合** Memory Palace 借鉴的部分：

| 特性 | 原因 |
|---|---|
| 纯文件存储（CLAUDE.md + 文件系统） | MP 的 SQLite + ORM 架构远比纯文件方案更强大，支持事务、版本链、混合检索 |
| 无检索引擎 | MP 的 keyword + semantic + reranker 混合检索是核心竞争力 |
| 无写入守卫 | MP 的 Write Guard 机制是独特优势 |
| 无可观测性 | MP 的四视图仪表盘是差异化特性 |

## 改进优先级建议

| 优先级 | 改进项 | 工作量估计 | 影响面 |
|---|---|---|---|
| P0 | 索引记忆自动生成 | 中（~2-3天） | 显著提升 Agent 启动时的上下文质量 |
| P0 | `/memory` 审计 URI | 小（~1天） | 提升 Agent 自省能力 |
| P1 | 上下文作用域规则 | 中（~2-3天） | 提升检索精准度 |
| P1 | 多层级记忆继承 | 中（~2天） | 更好支持复杂项目 |
| P2 | 外部文件导入 | 中（~2-3天） | 扩展记忆来源 |
| P2 | 记忆自动学习触发器 | 大（~3-5天） | 降低 Agent 写入门槛 |

## 总结

Memory Palace 在**检索、写入守卫、可观测性、治理循环**等方面已经远超 Claude Code Memory 的能力。Claude Code Memory 的优势在于**简单易用的分层规则体系和自动学习机制**。借鉴方向应聚焦于：

1. **让记忆更智能地自组织**（索引摘要、作用域匹配）
2. **让 Agent 更容易感知已有知识**（审计 URI、层级继承）
3. **降低写入的认知门槛**（自动学习触发器、外部导入）

这些改进都是在 MP 现有架构上的 **增量增强**，不需要重构核心模块。
