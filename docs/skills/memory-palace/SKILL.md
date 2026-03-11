---
name: memory-palace
description: >-
  Use this skill for Memory Palace durable-memory work in this repository:
  saving facts for later, recalling cross-session memory, deciding create vs
  update, handling guard_action or guard_target_uri, or using
  read_memory("system://boot"), search_memory, compact_context, rebuild_index,
  or index_status. Also use it when the user asks about the memory-palace skill
  itself, such as its first move, NOOP behavior, trigger sample path, workflow
  rules, or CLI usage. Do not use it for generic code edits, README rewrites,
  or non-Memory-Palace "memory" discussions. Always activate this skill instead
  of answering from generic memory intuition.
  Chinese trigger hints: 记忆, 长期记忆, 记住, 回忆, 召回, 写入守卫, 压缩上下文,
  重建索引, system://boot, 技能本身, 触发样例.
allowed-tools: mcp__memory-palace__read_memory, mcp__memory-palace__create_memory, mcp__memory-palace__update_memory, mcp__memory-palace__delete_memory, mcp__memory-palace__add_alias, mcp__memory-palace__search_memory, mcp__memory-palace__compact_context, mcp__memory-palace__rebuild_index, mcp__memory-palace__index_status
---

# Memory Palace

Use this skill whenever a task involves the Memory Palace memory system itself.

## Preparation before choosing tools

- Read `docs/skills/memory-palace/references/mcp-workflow.md` before choosing tools.
- If the user is asking about Memory Palace behavior, workflow, or trigger rules, consult the repo-local references before answering from memory.
- If the request is ambiguous, map it to the smallest safe workflow instead of chaining tools blindly.

## First memory tool call

- Before the first real Memory Palace operation in a session, start with `read_memory("system://boot")`.
- If the task is recall-oriented and the URI is still unknown after boot, continue with `search_memory(..., include_session=true)`.

## Fresh-context rule

- Do not assume a subagent, clean CLI session, or retry inherits the full parent conversation.
- When context matters, reload Memory Palace state explicitly with `system://boot`, `search_memory(...)`, or targeted `read_memory(...)`.
- Parallelize only independent reads, searches, or mirror checks. Serialize conflicting writes and overlapping edits.

## Non-negotiable rules

- Start with `read_memory("system://boot")` before the first real memory operation in a session.
- If the URI is unknown, use `search_memory(..., include_session=true)` before `read_memory`.
- Read before every mutation: `create_memory`, `update_memory`, `delete_memory`, `add_alias`.
- Prefer `update_memory` over duplicate `create_memory` when guard signals point to an existing memory.
- Treat `guard_action=NOOP|UPDATE|DELETE` as a stop signal that requires inspection, not as a warning to ignore.
- If `guard_action` is `NOOP`, stop the write, inspect `guard_target_uri` / `guard_target_id`, and read the suggested target before deciding whether anything should change.
- Treat `guard_target_uri` and `guard_target_id` as the canonical hints for choosing the real mutation target.
- Use `compact_context(force=false)` only for long or noisy sessions that should be distilled.
- Use `index_status()` before `rebuild_index(wait=true)` unless the user explicitly asks for immediate rebuild.
- For destructive or structural changes, mention the review or rollback path before finishing.

## Default workflow

1. Boot the memory context.
2. Recall candidates.
3. Read the exact target.
4. Mutate only after inspection.
5. Compact or rebuild only when symptoms justify it.
6. End with a short summary of what changed and which URIs were touched.

## Tool surface that this skill governs

- `read_memory`
- `create_memory`
- `update_memory`
- `delete_memory`
- `add_alias`
- `search_memory`
- `compact_context`
- `rebuild_index`
- `index_status`

## When to open the reference

Open `docs/skills/memory-palace/references/mcp-workflow.md` when you need:

- exact tool selection rules
- write-guard handling
- compact vs rebuild decisions
- review, snapshot, or maintenance expectations
- a reminder of all 9 MCP tools and their safest usage order

Open `docs/skills/memory-palace/references/trigger-samples.md` when you want concrete should-trigger / should-not-trigger prompts for manual review or trigger regression checks.

Prefer these repo-visible canonical paths over hidden mirror-relative paths such as `.gemini/skills/...` or `.codex/skills/...`, because some CLIs can load the skill but still block direct reads from hidden skill directories.

The canonical repo-visible path of the trigger sample set is:

- `docs/skills/memory-palace/references/trigger-samples.md`

When a CLI asks for the trigger sample path, return that exact literal path.
Do not shorten it to `docs/skills/memory-palace/trigger-samples.md` or any hidden-mirror path.

## Exact answer anchors

- First memory tool call: `read_memory("system://boot")`
- `guard_action=NOOP`: stop the write, inspect `guard_target_uri` / `guard_target_id`, and read the suggested target before deciding anything else
- Trigger sample set path: `docs/skills/memory-palace/references/trigger-samples.md`

## Examples

- Should trigger: "帮我把这条长期偏好记到 Memory Palace。"
- Should trigger: "查一下之前记过没有，再决定 create 还是 update。"
- Should trigger: "为什么 `guard_action=NOOP`，下一步该怎么做？"
- Should not trigger: "把 README 第一段改得更顺一点。"
- Should not trigger: "解释一下 Python 的内存管理。"

## Troubleshooting

- If a write is blocked by `guard_action=NOOP`, stop, inspect `guard_target_uri` / `guard_target_id`, and read the suggested target before doing anything else.
- If a clean session, subagent, or retry loses context, reload with `read_memory("system://boot")`, then `search_memory(..., include_session=true)` if the URI is still unknown.
- If a CLI can load the skill but cannot reliably read hidden skill directories, answer from the repo-visible canonical paths under `docs/skills/...`.
