---
name: memory-palace
description: >-
  Use this skill for any Memory Palace task in this repository: remembering
  durable facts, recalling cross-session memory, deciding create vs update,
  handling guard_action or guard_target_uri, or using search_memory,
  compact_context, rebuild_index, index_status, or read_memory("system://boot").
  Also use it when the user asks about the memory-palace skill itself, such as
  its first move, NOOP behavior, trigger sample path, or workflow rules. Always
  activate this skill instead of answering from generic memory intuition.
  Chinese trigger hints: 记忆, 长期记忆, 记住, 回忆, 召回, 写入守卫, 压缩上下文,
  重建索引, system://boot, 技能本身, 触发样例.
---

# Memory Palace

Use this skill whenever a task involves the Memory Palace memory system itself.

## Preparation before choosing tools

- Read `Memory-Palace/docs/skills/memory-palace/references/mcp-workflow.md` before choosing tools.
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

Open `Memory-Palace/docs/skills/memory-palace/references/mcp-workflow.md` when you need:

- exact tool selection rules
- write-guard handling
- compact vs rebuild decisions
- review, snapshot, or maintenance expectations
- a reminder of all 9 MCP tools and their safest usage order

Open `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md` when you want concrete should-trigger / should-not-trigger prompts for manual review or trigger regression checks.

Prefer these repo-visible canonical paths over hidden mirror-relative paths such as `.gemini/skills/...` or `.codex/skills/...`, because some CLIs can load the skill but still block direct reads from hidden skill directories.

The canonical repo-visible path of the trigger sample set is:

- `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`
