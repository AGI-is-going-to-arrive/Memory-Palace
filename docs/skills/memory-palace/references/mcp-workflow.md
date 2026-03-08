# Memory Palace MCP Workflow

This file is the operational reference for the `memory-palace` skill.

## Reference path policy

- Canonical visible path for this file: `docs/skills/memory-palace/references/mcp-workflow.md`
- Canonical visible path for trigger samples: `docs/skills/memory-palace/references/trigger-samples.md`
- Prefer these repo-visible paths when a CLI can load the skill but cannot directly read hidden mirror directories such as `.gemini/skills/...` or `.cursor/skills/...`

## Core intent map

- **Boot session context** → `read_memory("system://boot")`
- **Find memory by topic or fuzzy hint** → `search_memory`
- **Inspect a concrete memory** → `read_memory`
- **Create a new memory** → `create_memory`
- **Change an existing memory** → `update_memory`
- **Delete a memory** → `delete_memory`
- **Rename or migrate a path safely** → `add_alias` then `delete_memory`
- **Compress long session context** → `compact_context`
- **Check or repair search/index state** → `index_status`, then `rebuild_index`

## Safe-by-default sequences

### 1. Boot

Run this before the first real memory action in a session:

```python
read_memory("system://boot")
```

### 2. Recall before read

When the user gives a topic, not an exact URI:

```python
search_memory(query="...", include_session=True)
read_memory("best-match-uri")
```

### 3. Read before write

Never mutate first.

```python
read_memory("target-or-parent-uri")
update_memory(...)
```

Use `create_memory(...)` only when the target truly does not exist.
When you do create, prefer giving an explicit `title` so the new URI stays readable and stable.

### 4. Guard-aware writing

After `create_memory` or `update_memory`, inspect these fields if returned:

- `guard_action`
- `guard_reason`
- `guard_method`
- `guard_target_uri`
- `guard_target_id`

Operational rule:

- `NOOP` → stop and inspect the suggested target
- `UPDATE` → read the suggested target and usually switch to `update_memory`
- `DELETE` → stop and confirm the old memory should be replaced

Practical default:

- prefer `update_memory` patch mode for normal edits
- use `append` only when you really want to add text at the end
- use metadata-only updates when only `priority` / `disclosure` should change

If `guard_target_id` is present, prefer it over fuzzy similarity when deciding whether the target is truly the same memory.

### 5. Compact vs rebuild

Use `compact_context(force=false)` when the problem is:

- session context is too long
- notes are noisy
- you need a distilled summary, not index repair

Use `index_status()` and then `rebuild_index(wait=true)` when the problem is:

- repeated retrieval degradation
- index freshness is suspicious
- search quality is clearly below normal

## Parallelism boundary

- Safe to parallelize: independent recalls, independent search probes, mirror drift checks
- Keep serial: overlapping writes, alias/delete migrations, or any file edits against the same skill/docs set

## Tool checklist

All 9 MCP tools that this skill must stay aligned with:

- `read_memory`
- `create_memory`
- `update_memory`
- `delete_memory`
- `add_alias`
- `search_memory`
- `compact_context`
- `rebuild_index`
- `index_status`

## Review-facing summary template

When you finish a Memory Palace operation, summarize:

- which workflow was used
- which URIs were read
- which URIs were changed
- whether a guard intercepted anything
- whether compact or rebuild was triggered

## Trigger quality examples

### Should trigger

- “帮我把这条用户偏好写进 Memory Palace”
- “先从 system://boot 读一下，再帮我查最近这类记忆”
- “这个记忆可能重复了，帮我判断是 update 还是 create”
- “最近 search 质量下降了，帮我看看要不要 rebuild_index”
- “我想清理长会话，把它压缩成 notes”

### Should not trigger

- “给我重写 README”
- “修一下前端按钮样式”
- “帮我分析 benchmark 结果”
- “更新 docs/skills 的文字说明”
