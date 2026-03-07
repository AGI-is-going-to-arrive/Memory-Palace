# Memory Palace Trigger Smoke Report

## Summary

| Check | Status | Summary |
|---|---|---|
| `structure` | `PASS` | canonical bundle 结构与 YAML 通过 |
| `mirrors` | `PASS` | all mirrors are byte-identical to canonical |
| `sync_check` | `PASS` | All memory-palace skill mirrors are in sync. |
| `gate_syntax` | `PASS` | run_post_change_checks.sh 语法通过 |
| `claude` | `PASS` | Claude smoke 通过 |
| `codex` | `PASS` | Codex smoke 通过 |
| `opencode` | `PASS` | OpenCode smoke 通过 |
| `gemini` | `PASS` | Gemini smoke 通过 |
| `cursor` | `PARTIAL` | Cursor runtime 存在，但当前机器缺少登录/鉴权 |
| `agent` | `PARTIAL` | agent 仅完成 mirror 结构校验 |
| `antigravity` | `PARTIAL` | Antigravity app-bundled CLI 已发现，global_workflow 已安装；仍需 GUI 手工 smoke |

## Details

### structure

- Status: `PASS`
- Summary: canonical bundle 结构与 YAML 通过

### mirrors

- Status: `PASS`
- Summary: all mirrors are byte-identical to canonical

### sync_check

- Status: `PASS`
- Summary: All memory-palace skill mirrors are in sync.

### gate_syntax

- Status: `PASS`
- Summary: run_post_change_checks.sh 语法通过

### claude

- Status: `PASS`
- Summary: Claude smoke 通过

```text
- **First memory tool call**: `read_memory("system://boot")` — Initialize Memory Palace context before any other operation (per MEMORY_PALACE_SKILLS.md §4)

- **When guard_action is NOOP**: Don't continue writing; check for duplicate records first to decide if `update_memory` should replace `create_memory` (per MEMORY_PALACE_SKILLS.md §4, line 156)

- **Trigger sample set canonical path**: `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md` — Contains 10 should-trigger + 10 should-not-trigger + 4 borderline test cases for regression validation (per MEMORY_PALACE_SKILLS.md lines 8–12, 84–90)
```

### codex

- Status: `PASS`
- Summary: Codex smoke 通过

```text
{"first_move": "`read_memory(\"system://boot\")`", "noop_handling": "遇到 `guard_action=NOOP` 时先停止当前操作，并检查建议目标（优先看 `guard_target_uri` / `guard_target_id`）", "trigger_samples_path": "`Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`"}
```

### opencode

- Status: `PASS`
- Summary: OpenCode smoke 通过

```text
- First move: call `read_memory("system://boot")` before any real Memory Palace operation in the session.
- If `guard_action=NOOP`: stop the write, inspect `guard_target_uri` / `guard_target_id`, then read that target before deciding whether anything should change.
- Trigger sample path: `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`

[0m
> Sisyphus (Ultraworker) · gpt-5.3-codex
[0m
[0m→ [0mSkill "memory-palace"
```

### gemini

- Status: `PASS`
- Summary: Gemini smoke 通过

```text
I will activate the `memory-palace` skill to retrieve the repository-specific facts required to answer your questions.
* The first memory tool call required by the skill is `read_memory("system://boot")`.
* When `guard_action` is `NOOP`, you must stop the write, inspect the `guard_target_uri` or `guard_target_id`, read the suggested target, and then decide whether to update the target or leave it unchanged.
* The canonical repository-visible path of the trigger sample set is `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`.

Warning: --allowed-tools cli argument and tools.allowed in settings.json are deprecated and will be removed in 1.0: Migrate to Policy Engine: https://geminicli.com/docs/core/policy-engine/Loaded cached credentials.
Server 'chrome-devtools' supports tool updates. Listening for changes...
Server 'grok-search' supports tool updates. Listening for changes...
```

### cursor

- Status: `PARTIAL`
- Summary: Cursor runtime 存在，但当前机器缺少登录/鉴权

```text
Error: Authentication required. Please run 'agent login' first, or set CURSOR_API_KEY environment variable.
```

### agent

- Status: `PARTIAL`
- Summary: agent 仅完成 mirror 结构校验

```text
/Users/yangjunjie/Desktop/clawanti/.agent/skills/memory-palace
```

### antigravity

- Status: `PARTIAL`
- Summary: Antigravity app-bundled CLI 已发现，global_workflow 已安装；仍需 GUI 手工 smoke

```text
/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity
/Users/yangjunjie/.gemini/antigravity/global_workflows/memory-palace.md
```
