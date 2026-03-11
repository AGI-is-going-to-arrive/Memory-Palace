# Memory Palace IDE Hosts

This page is for IDE-like hosts:

- `Cursor`
- `Windsurf`
- `VSCode-host`
- `Antigravity`

Their main difference from `Claude / Codex / OpenCode / Gemini` is not branding, but **integration surface**:

- they do not expose a stable public model API for external CLIs to reuse
- the right way to project Memory Palace into them is:
  - repo-local rules
  - local MCP config snippets
  - a small host-specific compatibility layer when needed

So in `Memory-Palace`, the primary IDE-host path is no longer hidden `SKILL.md` mirrors. It is:

1. `AGENTS.md`
2. an MCP config snippet
3. an optional host-specific compatibility layer

---

## Core Positioning

### 1. The canonical skill still exists

The canonical source remains:

```text
docs/skills/memory-palace/
```

It still serves:

- CLI clients such as `Claude / Codex / OpenCode / Gemini`
- the repository's source of truth for skill design and references

### 2. IDE hosts should not rely on hidden skill mirrors as the primary path

For IDE hosts, Memory Palace should be inserted through:

- **primary entry**: repo-root `AGENTS.md`
- **execution entry**: a local MCP config pointing to `scripts/run_memory_palace_mcp_stdio.sh`
- **optional compatibility layer**: workflow / wrapper for specific hosts

That means:

- `AGENTS.md` is the rule projection for IDE hosts
- `mcpServers.memory-palace` is the tool projection for IDE hosts
- `docs/skills/memory-palace/` remains the canonical source behind both projections

### 3. Local prerequisites for the default IDE-host path

The default IDE-host path in this repository is:

- `bash` -> `scripts/run_memory_palace_mcp_stdio.sh`
- local repository `backend/.venv`
- local repository `.env`

Treat these as one bundle:

- the generated IDE-host snippet assumes the host can run `bash`
- the wrapper assumes the local `backend/.venv` already exists and has the backend dependencies installed
- the wrapper reads the local repository `.env` first to decide `DATABASE_URL`
- if `.env` is missing while `.env.docker` exists, it refuses to fall back to `demo.db`, because the repo-local stdio wrapper does **not** reuse Docker's `/app/data` database path

So if you only have the Docker / GHCR service side running and do not have a prepared local checkout runtime, do **not** use the stdio wrapper as your first IDE-host path. Point the host at the exposed `/sse` endpoint instead.

---

## Per-host view

### Cursor

- primarily consumes repo-local `AGENTS.md`
- connects through the host's local stdio MCP settings surface
- should not treat `.cursor/skills/memory-palace/` as the default primary path

### Windsurf

- same positioning as `Cursor`
- only valid when the host supports local stdio MCP and workspace/project rules

### VSCode-host

- means a VS Code extension host with agent / MCP capabilities
- does not assume VS Code itself has a first-class skill system
- if the extension supports:
  - local stdio MCP
  - repo-local project rules
  then it can reuse the same `AGENTS.md + MCP snippet` path

### Antigravity

- still belongs to the `IDE Host` category
- uses the same MCP path as the other IDE hosts
- but keeps one host-specific difference:
  - **prefer `AGENTS.md`**
  - **accept legacy `GEMINI.md`**
- it also keeps an optional workflow projection:

```text
docs/skills/memory-palace/variants/antigravity/global_workflows/memory-palace.md
```

That is an extra host-specific layer, not a different integration category.

---

## How to generate config

Do not hand-write it.

Use:

```bash
python scripts/render_ide_host_config.py --host cursor
python scripts/render_ide_host_config.py --host windsurf
python scripts/render_ide_host_config.py --host vscode
python scripts/render_ide_host_config.py --host antigravity
```

By default, this renders a repo-local MCP JSON snippet pointing to:

```text
scripts/run_memory_palace_mcp_stdio.sh
```

That default path assumes the same bundle above is already true:

- the IDE host can launch `bash`
- the repository-local `backend/.venv` is ready
- the repository-local `.env` exists when you want to reuse a specific SQLite file

If a host has `stdin/stdout` or CRLF quirks, switch to the wrapper form:

```bash
python scripts/render_ide_host_config.py --host antigravity --launcher python-wrapper
```

This renders a config snippet pointing to:

```text
backend/mcp_wrapper.py
```

and reminds you to replace `python` with the actual interpreter when your environment is managed by a virtualenv.

---

## Unified validation stance

IDE hosts should no longer be presented as repository-level “one-click live smoke” targets.

The safer layered approach is:

1. **static contract checks**
   - `AGENTS.md` exists
   - wrapper / workflow / canonical source exists
   - the MCP command really points at this checkout

2. **host connection checks**
   - the IDE can see the `memory-palace` MCP server
   - the IDE can list Memory Palace tools

3. **manual smoke checklist**
   - `read_memory("system://boot")`
   - create one `notes://ide_smoke_*`
   - try the duplicate write path and confirm guard blocks it

---

## Why this matches the reference project

This is closer to the approach used by `Dataojitori/nocturne_memory`:

- it documents client-specific MCP recipes
- `Antigravity` only keeps the minimum wrapper compatibility layer
- it does not try to turn every IDE host into a unified one-click install + live smoke target

`Memory-Palace` differs in one important way:

- it still keeps a canonical skill bundle
- but for IDE hosts, that bundle should be projected through `AGENTS.md + MCP snippet`
- instead of forcing those hosts to behave like full hidden-skill-mirror consumers
