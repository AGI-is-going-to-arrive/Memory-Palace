---
description: 'Memory Palace durable-memory workflow: boot, recall, inspect, mutate, compact, and recover with repo-local rules.'
---

# /memory-palace

## Preconditions

- The request is about Memory Palace durable memory, cross-session recall, write-guard handling, context compaction, or index recovery.
- Do not use this workflow for generic README/UI/testing/coding tasks that do not touch Memory Palace.
- Prefer repository-local references over generic memory advice.

## Inputs

- User request: `$ARGUMENTS`
- Repo-local workflow reference: `docs/skills/memory-palace/references/mcp-workflow.md`
- Repo-local trigger samples: `docs/skills/memory-palace/references/trigger-samples.md`

## Execution

1. If this is the first real Memory Palace operation in the session, start with `read_memory("system://boot")`.
2. If the URI is unknown, use `search_memory(..., include_session=true)` before guessing any target path.
3. Before any mutation (`create_memory`, `update_memory`, `delete_memory`, `add_alias`), read the target or the best matching candidate first.
4. If `guard_action` is `NOOP`, stop the write, inspect `guard_target_uri` / `guard_target_id`, read the suggested target, then decide whether anything should change.
5. If retrieval quality is degraded, inspect `index_status()` before `rebuild_index(wait=true)`.
6. Use `compact_context(force=false)` for long or noisy sessions that should be distilled.
7. When answering about the workflow itself, use repo-local facts from the two reference files above, not generic memory intuition.

## Verification

- The answer or action flow mentions `read_memory("system://boot")` when applicable.
- `NOOP` is treated as stop-and-inspect, not “continue normally”.
- Trigger sample path is reported as `docs/skills/memory-palace/references/trigger-samples.md`.
- Repo-local references are preferred over hidden mirror-relative paths.

## Output

- A concise, repo-specific answer or action plan
- If blocked, explain what information or runtime capability is missing
