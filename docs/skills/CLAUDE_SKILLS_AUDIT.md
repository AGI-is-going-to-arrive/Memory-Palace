# Memory Palace Skills × Claude 规范对齐检查 ✅

> 这份文档只回答两件事：
>
> 1. 现在这套 `memory-palace` skill，是否符合 Claude 官方的 skills 设计规范？
> 2. 它距离 Claude 官方 `skill-creator` 提倡的“测试 / 度量 / 迭代”闭环，还差多少？

---

## 1. 一句话结论

- **结构规范：基本符合，可以放心当 Claude Code skill 用**
- **自动触发设计：基本符合，`description` 已明确写“做什么 + 什么时候用”**
- **渐进加载（progressive disclosure）：符合，正文短，细节下沉到 `references/`**
- **多端适配：比官方最低要求更强，额外补了 `Gemini` 变体和多 CLI mirror**
- **skill-creator 闭环：部分符合**
  - 已有：trigger 样例、跨 CLI smoke、真实 MCP e2e
  - 还缺：更标准化的 `evals.json / benchmark / blind comparator / description optimizer` 产物闭环

简单说：

> **这套 skill 在 Claude 这一侧已经是“能稳定用”的状态；**
> **如果要追到 Claude 官方 `skill-creator` 的满配标准，还可以继续把 eval/benchmark 这一层补厚。**

---

## 2. Claude 官方最看重什么

按官方文档，Claude Skills 最核心的要求不是“写很长”，而是下面这几条：

| 官方关注点 | 官方意思 | 当前项目现状 |
|---|---|---|
| `SKILL.md` + frontmatter | skill 目录里必须有 `SKILL.md`，并带 `name` / `description` | 已符合 |
| `description` 要写清楚触发条件 | 不只是写“做什么”，还要写“什么时候该用” | 已符合 |
| 正文要短、要克制 | 正文不要变成长论文，更多细节放到额外文件 | 已符合 |
| progressive disclosure | 先载入元信息；触发后再读正文；需要时再读参考文件 | 已符合 |
| 文件组织要清楚 | 文件名要好懂，目录别太深，最好一层就够 | 已符合 |
| 真实测试 | 不能只靠肉眼觉得像是对的，要有实际 smoke / eval | 已部分符合 |
| 持续迭代 description | 触发太多或太少，都要回头调 `description` | 已符合 |

官方参考：

- [Claude Code Docs, 2026, https://code.claude.com/docs/en/skills]
- [Anthropic Docs, 2026, https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview]
- [Anthropic Docs, 2026, https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices]
- [Claude Blog, 2026-03-03, https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills]
- [Anthropic GitHub, 2026, https://github.com/anthropics/skills/tree/main/skills/skill-creator]

---

## 3. 当前项目已经对齐的部分

### 3.1 Skill 结构是对的

当前 canonical bundle：

```text
Memory-Palace/docs/skills/memory-palace/
├── SKILL.md
├── references/
│   ├── mcp-workflow.md
│   └── trigger-samples.md
├── agents/
│   └── openai.yaml
└── variants/
    ├── gemini/
    │   └── SKILL.md
    └── antigravity/
        └── global_workflows/
            └── memory-palace.md
```

这和 Claude 官方强调的“**一个 skill 是一个目录，入口文件就是 `SKILL.md`**”是一致的。

---

### 3.2 `description` 已经承担触发契约

现在的 `SKILL.md` frontmatter 不只是说“这是个记忆 skill”，而是明确写了：

- Memory Palace durable memory
- cross-session recall
- `create vs update`
- `guard_action`
- `search_memory / compact_context / rebuild_index / index_status`
- `system://boot`
- 中文触发词：`记忆 / 长期记忆 / 回忆 / 压缩上下文 / 重建索引`

这点是符合官方要求的。

官方原话的核心意思是：

> `description` 不是简介，而是 Claude 判断“要不要触发这个 skill”的主入口。

---

### 3.3 正文足够短，没有把 skill 写成大论文

当前 `memory-palace/SKILL.md` 正文很克制，主要只保留：

- first move
- read before write
- guard 处理
- compact / rebuild 的分界
- 什么时候该去读 reference

这符合官方“**concise is key**”的原则。

换句话说：

- **正文负责规则**
- **`references/` 负责细节**

这正是 Claude 官方推荐的 progressive disclosure 写法。

---

### 3.4 参考文件拆分方式是对的

当前拆法：

- `references/mcp-workflow.md`：最小安全工作流
- `references/trigger-samples.md`：触发样例集

这比把所有内容硬塞进 `SKILL.md` 更符合官方建议。

而且文件名也比较直接，不需要模型去猜 `doc1.md`、`misc.md` 是啥。

---

### 3.5 没有依赖“隐藏 mirror 路径必须可读”这个脆弱前提

这套 skill 特意强调：

- 优先引用 `Memory-Palace/docs/skills/...` 这种 **repo-visible path**
- 不把 `.gemini/skills/...`、`.codex/skills/...` 这种 hidden mirror 路径当默认事实来源

这点很重要。

因为它等于主动避开了多客户端下最容易翻车的地方：

> **skill 明明触发了，但运行时又读不到隐藏目录里的参考文件。**

从工程上说，这是一个正确的收口方式。

---

### 3.6 多客户端适配，比 Claude 官方最低要求更完整

官方主要讲的是 Claude 自己那套 skill 机制。

而当前项目已经额外补了这些落地：

- `.claude/skills/memory-palace/`
- `.codex/skills/memory-palace/`
- `.gemini/skills/memory-palace/`
- `.opencode/skills/memory-palace/`
- `variants/gemini/SKILL.md`
- `variants/antigravity/global_workflows/memory-palace.md`

也就是说，这不是只给 Claude Code 用的一份文档，而是一套**跨客户端统一的 skill bundle**。

---

## 4. 跟 `skill-creator` 比，已经做到哪一步了

Claude 官方最新的 `skill-creator` 更强调下面这个闭环：

1. 先写草稿
2. 再写测试 prompt
3. 跑 eval / benchmark
4. 看结果
5. 调 `description`
6. 再迭代

当前项目已经有这些东西：

### 已经有的 ✅

- `references/trigger-samples.md`
  - 有 should-trigger / should-not-trigger / borderline 样例
- `scripts/evaluate_memory_palace_skill.py`
  - 会检查 structure / mirrors / Claude / Codex / OpenCode / Gemini smoke
- `scripts/evaluate_memory_palace_mcp_e2e.py`
  - 会检查真实 MCP stdio 调用链
- `scripts/sync_memory_palace_skill.py`
  - 会检查 mirror 是否漂移

这说明：

> 这套 skill 已经不是“纯手写 prompt”，而是进入了**可回归、可验收**的阶段。

---

### 还没补满的 ⚠️

如果严格按官方 `skill-creator` 的“更完整工作流”来对照，目前还差这几项：

1. **标准化 eval 数据文件**
   - 例如单独落盘的 `evals.json` / `grading.json`
2. **benchmark 结果归档**
   - 例如多轮跑分、时间、token、波动对比
3. **blind comparator / A-B 对比**
   - 明确比较“改 description 前后，谁更好”
4. **description optimizer 的固定流程**
   - 现在有 trigger 样例和 smoke，但还没有单独的 description 调优流水线产物
5. **Claude 自家多模型测试记录**
   - 官方建议至少看不同模型表现
   - 当前项目重点是多客户端 smoke，不是 Claude 单平台多模型 benchmark

所以更准确的结论不是“完全符合”。

而是：

> **结构规范已经符合；**
> **skill-creator 的评测闭环已经做了一大半，但还没补到 Anthropic 官方满配形态。**

---

## 5. 关于第三方文章里提到的 `context: fork`

你给的那篇第三方文章里，提到一个争议点：

- `context: fork` 到底是不是“真的继承主上下文”

这里要分开看：

### 官方口径

Claude 官方文档把 `context: fork` 归类为**可选 frontmatter 字段**。

也就是说：

- 它是一个**可选能力**
- 不是 skill 合法性的硬门槛
- 不是“没写就不合规”

### 当前项目做法

当前 `memory-palace` skill **没有依赖 `context: fork` 才能成立**。

反而它明确写了：

- 不要假设 subagent 会继承完整上下文
- context 重要时，要显式重新 `boot / search / read`

这个做法反而更稳。

因为它规避了一个最危险的问题：

> **把 skill 的正确性建立在某个客户端“也许会 fork 上下文”的实现细节上。**

所以这部分结论是：

- 第三方文章可以当补充观察
- 但当前项目没有把正确性押在 `context: fork` 上
- 从安全和跨端兼容角度看，这个设计是合理的

补充参考（非官方，仅作背景阅读）：

- [pdjjq, 2026, https://blog.pdjjq.org/post/claude-code-skill-25ron8]

---

## 6. 对这套项目的最终判断

### 结论 A：能不能算符合 Claude Skills 设计规范？

**可以。**

理由很直接：

- 有合法 `SKILL.md`
- 有清晰 frontmatter
- `description` 承担触发契约
- 正文简短
- reference 拆分合理
- 文件路径清楚
- 有真实 smoke 和 e2e 回归

---

### 结论 B：能不能说已经完全达到 Claude `skill-creator` 的理想状态？

**还不能说“完全达到”。**

更准确的说法是：

- **结构层面：已达标**
- **工程落地层面：已很好用**
- **评测闭环层面：还可以继续增强**

---

## 7. 下一步最值得补的，不是重写 skill，而是补评测闭环

如果后面继续往官方 `skill-creator` 靠，优先级建议这样排：

1. 先补 **description 对比评测**
2. 再补 **标准化 eval case 文件**
3. 再补 **benchmark 历史记录**
4. 最后再决定要不要引入更重的 comparator / reviewer 流程

不要反过来：

- 不要先把 `SKILL.md` 写更长
- 不要先堆更多 reference
- 不要先加一堆花哨 frontmatter

因为官方真正关心的不是“看起来复杂”，而是：

> **它到底会不会该触发时触发，不该触发时闭嘴，触发后第一步还做对。**

---

## 8. 如果你只想记住一句话 📌

这套 `memory-palace` skill 现在已经：

- **符合 Claude Skills 的基本设计规范**
- **比单纯 prompt 更工程化**
- **比官方最低要求多做了多客户端适配**

但如果你要说它完全达到 Anthropic 官方 `skill-creator` 的满配成熟度，

那还差一步：

- **把现有 smoke / trigger samples / live e2e，再继续收口成更标准的 eval + benchmark 闭环。**
