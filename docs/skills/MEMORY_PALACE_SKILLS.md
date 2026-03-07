# Memory Palace Skills 设计与维护说明

这份文档不再只是“给人看的策略说明”，而是 `memory-palace` skill 体系的维护基线。

当前单一真源位于：

```text
docs/skills/memory-palace/
├── SKILL.md
├── references/
│   ├── mcp-workflow.md
│   └── trigger-samples.md
├── agents/
│   └── openai.yaml
└── variants/
    └── gemini/
        └── SKILL.md
```

分发脚本位于：

```text
scripts/sync_memory_palace_skill.py
```

安装脚本位于：

```text
scripts/install_skill.py
```

## 0. 与 Claude Skills 规范的对齐结论（2026-03-07）

这轮按 `Claude Code` 官方 skills 文档、Anthropic 在 2026-03-03 发布的
`Improving skill-creator: Test, measure, and refine Agent Skills`，以及
`anthropics/skills` 仓库里的 `skill-creator` 做了一次对照。

当前结论可以直接说：

- **结构上已对齐**：采用 `skill-name/SKILL.md` 的标准 bundle 结构，目录名与 `name` 都是 `memory-palace`
- **触发契约已对齐**：`description` 同时写清“做什么”和“什么时候用”，而且保留了明确 trigger hints
- **渐进加载已对齐**：主 `SKILL.md` 保持短小，工具细节下沉到 `references/`
- **跨客户端分发已对齐**：Claude / Codex / OpenCode 走 mirror，Gemini 保留 variant；当前仓库可直接走项目级入口，跨仓复用时仍优先 user install

但和 `skill-creator` 的完整版工作流相比，当前还有一个明确边界：

- **验证层还不是 full eval / benchmark 套件**

现在仓库里已经有：

- trigger smoke
- mirror drift check
- live MCP e2e
- 跨客户端 MCP 绑定检查

但还没有完全做成 `skill-creator` 那种：

- `evals.json`
- blind comparator
- benchmark viewer
- 自动 description optimization loop

所以更准确的说法是：

- **当前 skill 设计已经符合 Claude Skills 的结构规范与触发规范**
- **当前验证方式更偏工程化 smoke / e2e，还没有走满 skill-creator 的全量评测工作流**

## 1. 为什么要这样收敛

旧设计的核心问题不是“信息太少”，而是结构分散：

- 有策略文档，但缺少仓内可分发的 canonical skill bundle
- 多 CLI 目录靠手工维护，很容易漂移
- “什么时候触发”“怎么执行”“怎么验证”没有形成闭环

现在的设计目标是：

- **可直接分发**：canonical bundle 固定落在 `docs/skills/memory-palace/`
- **可跨 CLI 使用**：通过同步脚本分发到 `.claude/.codex/.opencode/.cursor/.agent`；Gemini 在当前仓库可直接用项目级入口，跨仓时仍优先 `user-scope install`
- **可验证**：通过 `sync_memory_palace_skill.py --check` 与仓内门禁持续校验
- **可迭代**：先优化 `description` 的触发质量，再优化 `SKILL.md` 正文与 reference

Gemini 端当前有一个已知边界：

- workspace-local `.gemini/skills/...` 可以被发现
- 但真实触发时，Gemini 可能尝试直接读取隐藏 skill 目录
- 在部分本地策略下，这会被 ignore patterns 拦截

因此当前推荐分两层：

- **当前仓库里优先尝试**：先走项目级 `.gemini/skills/...` + `.gemini/settings.json`
- **跨仓复用 / 复制到别的工作区**：仍然优先 `user-scope install`

公开口径建议：

- 可以说“workspace 入口已经就位”
- 不建议直接写成“Gemini 已经完全开箱即用”

## 2. 目录职责

### `memory-palace/SKILL.md`

负责：

- 定义何时触发
- 给出最短但安全的默认流程
- 明确哪些情况必须先检查、不能盲写

### `memory-palace/variants/gemini/SKILL.md`

负责：

- 给 Gemini 提供更短、更强触发的技能正文
- 把 first move、`NOOP` 处理、trigger sample path 直接写成锚点
- 降低 Gemini 在 skill 自省问题上的 under-trigger 与答非所问

### `memory-palace/references/mcp-workflow.md`

负责：

- 维护 9 个 MCP 工具的最小安全工作流
- 记录 recall / write / compact / rebuild 的安全顺序
- 给出 should trigger / should not trigger 示例

### `memory-palace/references/trigger-samples.md`

负责：

- 提供稳定的 should-trigger / should-not-trigger / borderline prompt 集
- 让 `description` 优化有固定对照组，而不是凭感觉改
- 给后续 trigger regression / human review 留下统一输入集

### `scripts/sync_memory_palace_skill.py`

负责：

- 把 canonical bundle 分发到各 CLI 目录
- 检查 mirrors 是否漂移
- 当前工作区镜像包括 `.claude`、`.codex`、`.opencode`、`.cursor`、`.agent`

### `scripts/install_skill.py`

负责：

- 把 canonical bundle 安装到其他工作区或用户目录
- 支持 `copy` / `symlink`
- 让这套 skill 不依赖当前仓库路径也能直接使用
- 对 Gemini，这也是当前更稳妥的推荐安装路径
- 当目标是 Gemini 时，自动替换为 `variants/gemini/SKILL.md`

## 3. 设计原则

1. `description` 是**触发契约**
2. `SKILL.md` 正文只保留**执行步骤、硬约束、失败处理**
3. 工具细节下沉到 `references/`
4. 分发与校验由仓库脚本负责，不再让用户手抄 skill
5. 运行时引用优先指向 **repo-visible canonical docs/skills 路径**，不要依赖隐藏 mirror 目录可读
6. 不只检查“skill 能不能被发现”，还要检查“对应 MCP 是否真的绑到当前项目”

## 4. 默认工作流

### Boot

首次真实操作前：

```python
read_memory("system://boot")
```

### Recall

URI 不确定时：

```python
search_memory(query="...", include_session=True)
```

### Read before write

在以下操作前先读目标或候选目标：

- `create_memory`
- `update_memory`
- `delete_memory`
- `add_alias`

### Guard-aware write

不能忽略这些字段：

- `guard_action`
- `guard_reason`
- `guard_method`
- `guard_target_uri`

推荐规则：

- `NOOP` → 不继续写，先检查重复
- `UPDATE` → 优先改为 `update_memory`
- `DELETE` → 先确认旧记忆确实该被替换

### Compact / Recover

- 长会话、噪声多 → `compact_context(force=false)`
- 检索退化 → `index_status()`，必要时 `rebuild_index(wait=true)`

## 5. Trigger 设计要求

新版 `memory-palace` skill 的 `description` 必须覆盖这些触发信号：

- 用户明确说 memory / remember / recall / long-term memory
- 用户用中文说“记住”“回忆”“长期记忆”“跨会话”“压缩上下文”“重建索引”
- 用户提到 `system://boot`
- 用户提到 `search_memory` / `compact_context` / `rebuild_index`
- 用户在问“该 create 还是 update”
- 用户在做维护、回滚、索引恢复相关动作

同时要明确边界：

- 不用于普通 README / UI / benchmark / 通用代码实现任务
- 不用于与 Memory Palace MCP 无关的泛化“技能设计”任务

## 6. Test / Measure / Refine

这次不是只把 skill 写得更长，而是把维护闭环补齐：

1. 先改 `description`
2. 再改 `SKILL.md` 正文
3. 运行 `python scripts/sync_memory_palace_skill.py --check`
4. 再跑 `python scripts/evaluate_memory_palace_skill.py`
5. 再跑 `backend/.venv/bin/python scripts/evaluate_memory_palace_mcp_e2e.py`
6. 再跑 `bash new/run_post_change_checks.sh`
7. 只在确实需要时继续扩 `references/`

### Should trigger

- “帮我把这条用户偏好写进 Memory Palace”
- “先从 `system://boot` 读一下，再帮我查最近这类记忆”
- “这个记忆可能重复了，帮我判断是 update 还是 create”
- “最近 search 降级了，帮我看看要不要 `rebuild_index`”
- “我想清理长会话，把它压缩成 notes”

### Should not trigger

- “给我重写 README”
- “修一下前端按钮样式”
- “帮我分析 benchmark 结果”
- “更新 docs/skills 的说明文字”

### 样例集的具体作用

`references/trigger-samples.md` 的作用不是“多一份文档”，而是让你能稳定回答四个问题：

1. 这个 skill **会不会该触发时不触发**
2. 这个 skill **会不会不该触发时乱触发**
3. 触发之后，它的**第一步动作是否正确**
4. 改了 `description` 之后，效果到底是变好还是变差

如果没有这套样例集，后续每次调 `description` 都只能靠临时感觉，很容易出现：

- 这次为了减少误触发，把真正该命中的场景也一起打掉
- 这次为了扩大触发，把普通 docs / coding 任务也吸进来了
- 触发是触发了，但第一步不是 `boot` / `search before write`，行为仍然错

`evaluate_memory_palace_skill.py` 则把这套样例和实际 CLI smoke 固化成可重复执行的回归入口，用来回答：

- mirrors 还一致吗
- YAML/frontmatter 还合法吗
- Claude / Codex / OpenCode / Gemini 现在是通过、部分通过，还是失败
- 当前回归结果是否比上一次更好

`evaluate_memory_palace_mcp_e2e.py` 则进一步回答另一层关键问题：

- skill 规则之外，真实 MCP stdio 调用链能否跑通
- 9 个工具是否都能在隔离数据库上按设计返回
- `write_guard NOOP`、`add_alias`、`rebuild_index(wait=true)` 等关键行为是否符合项目设计
- `runtime-index-worker` 是否还存在跨 event loop 隐性 bug

## 7. Gemini 兼容口径

当前验证里已经出现过一类真实兼容边界：

- CLI 可以发现并加载 `memory-palace` skill
- 但运行时文件读取策略可能忽略隐藏 mirror 目录（例如 `.gemini/skills/...`）

因此 canonical `SKILL.md` 现在统一要求：

- 引用参考文件时，优先打开 `docs/skills/memory-palace/...`
- 不把 hidden mirror 路径当作默认参考路径

这样做的收益是：

- Claude / Codex / OpenCode 仍然可用
- Gemini / Cursor 这类对隐藏目录更保守的客户端也更容易得到一致结果

如果需要做 Gemini CLI 的更稳 smoke，当前更可靠的调用方式是：

```bash
gemini -m gemini-3-flash-preview \
  -p '<your prompt>' \
  --output-format text \
  --allowed-tools activate_skill,read_file
```

注意：这是一条**最近验证里更稳定的经验路径**，不是对所有 Gemini 版本都恒真的官方保证。

## 7. 维护边界

后续继续优化时，保持这个顺序：

1. 先调 trigger description
2. 再调执行正文
3. 再调 reference
4. 最后再调同步脚本与门禁

不要再回到“先写长文档，再让用户自己抄成 skill”的旧模式。
