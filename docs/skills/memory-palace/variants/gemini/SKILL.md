---
name: memory-palace
description: >-
  Use this skill whenever the user asks about Memory Palace durable memory,
  recall across sessions, saving notes for later, memory deduplication,
  guard_action or guard_target_uri handling, search_memory, compact_context,
  rebuild_index, index_status, or read_memory("system://boot"). Also use it
  whenever the user asks about this repository's memory-palace skill itself:
  its first memory tool call, NOOP behavior, trigger sample path, workflow
  rules, or how to use it in Gemini CLI. Do not answer these requests from
  generic intuition; load this skill first and use its repository-local facts.
---

# Memory Palace

Use this skill for Memory Palace memory operations and for questions about this skill itself.

## Repository-local anchors

- First memory tool call: `read_memory("system://boot")`
- If `guard_action` is `NOOP`: stop the write, inspect `guard_target_uri` / `guard_target_id`, read the suggested target, then decide whether to update or leave unchanged
- Trigger sample set path: `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`

## Required workflow

- Read `Memory-Palace/docs/skills/memory-palace/references/mcp-workflow.md` before choosing tools when the user is asking about workflow or tool behavior.
- If the URI is unknown, use `search_memory(..., include_session=true)` before `read_memory`.
- Read before every mutation: `create_memory`, `update_memory`, `delete_memory`, `add_alias`.
- Use `index_status()` before `rebuild_index(wait=true)` unless the user explicitly wants immediate rebuild.
- Use `compact_context(force=false)` for long or noisy sessions that should be distilled.

## Important boundary

- Prefer repo-visible canonical paths under `Memory-Palace/docs/skills/...`
- Do not rely on hidden mirror-relative paths such as `.gemini/skills/...` when answering repository-local questions
