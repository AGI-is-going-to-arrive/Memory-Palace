# Memory Palace Trigger Samples

This file is the trigger-evaluation sample set for the `memory-palace` skill.

## What this sample set is for

- Check whether the skill triggers when it should
- Check whether the skill stays silent when it should not
- Review whether the skill chooses the correct first move after triggering
- Provide a stable prompt set for future trigger regression tests

## How to use it

For each sample:

1. Decide whether the skill **should trigger**
2. If it should trigger, check whether the first move matches the expected workflow
3. If it should not trigger, check that the agent does not route into Memory Palace-specific behavior

## Should Trigger

### T01

- Prompt: `帮我把“用户偏好简洁回答”写进 Memory Palace，并避免重复创建。`
- Why: explicit memory write + dedupe requirement
- Expected first move: `read_memory("system://boot")`, then `search_memory(..., include_session=true)`

### T02

- Prompt: `先从 system://boot 读一下，再帮我查最近关于部署偏好的记忆。`
- Why: explicit Memory Palace URI and recall
- Expected first move: `read_memory("system://boot")`

### T03

- Prompt: `这条记忆可能已经存在，帮我判断应该 create 还是 update。`
- Why: explicit write-guard / dedupe decision
- Expected first move: recall or read before any mutation

### T04

- Prompt: `最近 search_memory 结果不太对，帮我看看要不要 rebuild_index。`
- Why: explicit retrieval degradation diagnosis
- Expected first move: inspect retrieval state, usually `index_status()`

### T05

- Prompt: `把这段很长的会话压缩进 notes，别直接丢信息。`
- Why: explicit context compaction workflow
- Expected first move: boot or inspect current context, then `compact_context(force=false)`

### T06

- Prompt: `帮我把 core://agent 下这条规则迁移到新路径，并保留旧别名。`
- Why: alias + delete migration flow
- Expected first move: read target, then `add_alias`, then controlled cleanup

### T07

- Prompt: `跨会话回忆一下我们之前记住的发布口径。`
- Why: long-term recall across sessions
- Expected first move: `read_memory("system://boot")` or `search_memory(..., include_session=true)`

### T08

- Prompt: `这个 Memory Palace 写入被 guard 拦截了，帮我找真实目标。`
- Why: explicit guard handling
- Expected first move: inspect `guard_target_uri` / `guard_target_id`, then `read_memory(...)`

### T09

- Prompt: `请排查 index_status 里的 degrade_reasons，并给出恢复顺序。`
- Why: explicit maintenance / recovery semantics
- Expected first move: `index_status()`

### T10

- Prompt: `更新一下这个仓库里的 Memory Palace skill 文档，让它和真实 MCP 工作流一致。`
- Why: repository-local skill maintenance for Memory Palace
- Expected first move: open the canonical skill/docs references, not generic docs editing only

## Should Not Trigger

### N01

- Prompt: `给我重写 README 的开头介绍。`
- Why: generic documentation edit, no Memory Palace operation
- Expected behavior: do not route into Memory Palace MCP workflow

### N02

- Prompt: `修一下前端按钮 hover 样式。`
- Why: UI task only
- Expected behavior: no Memory Palace-specific tool planning

### N03

- Prompt: `帮我分析 benchmark 图表。`
- Why: evaluation discussion, not memory operations
- Expected behavior: stay in general analysis mode

### N04

- Prompt: `把这个 Docker 脚本改成支持 arm64。`
- Why: deployment/code task only
- Expected behavior: no boot/search/update memory flow

### N05

- Prompt: `写一个新的 skill 给 UI 设计用。`
- Why: skill-authoring task, but not the Memory Palace memory system itself
- Expected behavior: no Memory Palace tool workflow

### N06

- Prompt: `帮我整理 llmdoc 的目录结构。`
- Why: documentation system task, unrelated to memory operations
- Expected behavior: no Memory Palace trigger

### N07

- Prompt: `看看这个 API 为什么 500。`
- Why: backend debugging task without memory semantics
- Expected behavior: normal debugging workflow, not Memory Palace workflow

### N08

- Prompt: `为这个组件补单元测试。`
- Why: generic coding/testing task
- Expected behavior: no Memory Palace trigger

### N09

- Prompt: `把这段英文翻译成中文。`
- Why: pure language task
- Expected behavior: no Memory Palace trigger

### N10

- Prompt: `总结一下这篇博客。`
- Why: general summarization
- Expected behavior: no Memory Palace trigger

## Borderline Cases

These are useful when refining `description`.

### B01

- Prompt: `把这次调试结论记下来。`
- Why: ambiguous; could mean local notes or Memory Palace
- Preferred decision: trigger only if surrounding context clearly points to Memory Palace or durable project memory

### B02

- Prompt: `回忆一下我们刚刚说过的话。`
- Why: may refer to current chat context, not long-term memory
- Preferred decision: do not trigger unless the user signals cross-session recall

### B03

- Prompt: `帮我保存这个结论，后面还要用。`
- Why: saving could mean file edit, issue comment, or Memory Palace
- Preferred decision: trigger only if durable memory persistence is explicitly intended

### B04

- Prompt: `这个知识库是不是该重建一下。`
- Why: “知识库” could be a docs index, vector DB, or Memory Palace index
- Preferred decision: trigger only if Memory Palace retrieval/index context is established

## Review Checklist

- Did the skill trigger on all T-series prompts?
- Did it stay silent on all N-series prompts?
- For T-series prompts, did it choose the correct first move?
- For B-series prompts, were trigger decisions conservative and well-justified?
