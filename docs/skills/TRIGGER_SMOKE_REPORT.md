# Memory Palace Trigger Smoke Report

> 说明：本页保留**对外可读**的 smoke 摘要。绝对路径、用户目录、运行时 ID、时间戳和机器相关 stderr 已做脱敏处理。

## Summary

| Check | Status | Summary |
|---|---|---|
| `structure` | `PASS` | canonical bundle 结构与 YAML 通过 |
| `mirrors` | `PASS` | workspace mirrors match canonical bundle and Gemini variant |
| `sync_check` | `PASS` | All memory-palace skill mirrors are in sync. |
| `gate_syntax` | `PASS` | run_post_change_checks.sh 语法通过 |
| `mcp_bindings` | `PASS` | Claude/Codex/Gemini/OpenCode 的 memory-palace MCP 都已绑定到当前项目 |
| `claude` | `PASS` | Claude smoke 通过 |
| `codex` | `PASS` | Codex smoke 通过 |
| `opencode` | `PASS` | OpenCode smoke 通过 |
| `gemini` | `PASS` | Gemini smoke 通过 |
| `gemini_live` | `FAIL` | Gemini live MCP 链路未完全通过 |
| `cursor` | `PARTIAL` | Cursor runtime 存在，但当前机器缺少登录/鉴权 |
| `agent` | `PARTIAL` | agent 仅完成 mirror 结构校验 |
| `antigravity` | `PARTIAL` | Antigravity app-bundled CLI 已发现，global_workflow 已安装；仍需 GUI 手工 smoke |

## Details

### structure

- Status: `PASS`
- Summary: canonical bundle 结构与 YAML 通过

### mirrors

- Status: `PASS`
- Summary: workspace mirrors match canonical bundle and Gemini variant

### sync_check

- Status: `PASS`
- Summary: All memory-palace skill mirrors are in sync.

### gate_syntax

- Status: `PASS`
- Summary: run_post_change_checks.sh 语法通过

### mcp_bindings

- Status: `PASS`
- Summary: Claude/Codex/Gemini/OpenCode 的 memory-palace MCP 都已绑定到当前项目

```text
PASS claude: MCP 已绑定到当前项目 backend/memory.db（<user-home>/.claude.json）

PASS codex: MCP 已绑定到当前项目 backend/memory.db（<user-home>/.codex/config.toml）

PASS gemini: MCP 已绑定到当前项目 backend/memory.db（<user-home>/.gemini/settings.json）

PASS opencode: MCP 已绑定到当前项目 backend/memory.db（<user-home>/.config/opencode/opencode.json）
```

### claude

- Status: `PASS`
- Summary: Claude smoke 通过

```text
Based on the Memory-Palace skill documentation:

- **First memory tool call**: `read_memory("system://boot")` before any real Memory Palace operation in a session
- **When guard_action is NOOP**: Stop the write immediately, inspect `guard_target_uri` / `guard_target_id`, and read the suggested target before deciding whether anything should change
- **Canonical trigger sample path**: `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`
```

### codex

- Status: `PASS`
- Summary: Codex smoke 通过

```text
{"first_move": "`read_memory(\"system://boot\")`", "noop_handling": "当 `guard_action=NOOP` 时，停止写入，检查 `guard_target_uri` / `guard_target_id`，先读取建议目标，再决定是更新还是保持不变。", "trigger_samples_path": "`Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`"}
```

### opencode

- Status: `PASS`
- Summary: OpenCode smoke 通过

```text
- **First move:** call `read_memory("system://boot")` before any real Memory Palace operation in a session.
- **If `guard_action=NOOP`:** stop the write, inspect `guard_target_uri` / `guard_target_id`, then `read_memory` on the suggested target before deciding any change.
- **Trigger sample file:** `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`.

[0m
> Sisyphus (Ultraworker) · gpt-5.3-codex
[0m
[0m→ [0mSkill "memory-palace"
```

### gemini

- Status: `PASS`
- Summary: Gemini smoke 通过

```text
[model=gemini-3-flash-preview]
- first tool call: `read_memory("system://boot")`
- `guard_action=NOOP` handling: stop the write, inspect `guard_target_uri` / `guard_target_id`, then read the suggested target before deciding whether to update
- trigger sample path: `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`
- note: machine-specific conflict / telemetry lines removed from the public report
```

### gemini_live

- Status: `FAIL`
- Summary: Gemini live MCP 链路未完全通过

```text
- runtime db path: `<repo-root>/Memory-Palace/backend/memory.db`
- create step: this round did not stably verify create → mark whole live suite as `FAIL`
- update step: passed against an existing target
- guard step: duplicate write was blocked as expected (`guard_action=NOOP`)
- raw ids / timestamps / full payloads: `<redacted runtime output>`
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
<repo-root>/.agent/skills/memory-palace
```

### antigravity

- Status: `PARTIAL`
- Summary: Antigravity app-bundled CLI 已发现，global_workflow 已安装；仍需 GUI 手工 smoke

```text
<Applications>/Antigravity.app/Contents/Resources/app/bin/antigravity
<user-home>/.gemini/antigravity/global_workflows/memory-palace.md
```
