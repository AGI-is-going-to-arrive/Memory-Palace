# Memory Palace Technical Overview

This document is intended for technical users who need to understand the internal system implementation or perform secondary development, covering the backend, frontend, MCP tool layer, runtime, and deployment architecture.

---

## 1. Technology Stack

| Layer | Technology | Version Requirement | Role |
|---|---|---|---|
| Backend | FastAPI + SQLAlchemy + SQLite | FastAPI ≥0.109 · SQLAlchemy ≥2.0 · aiosqlite ≥0.19 | Memory R/W, retrieval, review, maintenance |
| MCP | `mcp.server.fastmcp` | mcp ≥0.1 | Exposes a unified tool interface for Codex / Claude Code / Gemini CLI / OpenCode; for IDE hosts such as `Cursor / Windsurf / VSCode-host / Antigravity`, the recommended path is repo-local `AGENTS.md` plus an MCP config snippet |
| Frontend | React + Vite + TailwindCSS + Framer Motion | React ≥18.2 · Vite ≥7.3 · TailwindCSS ≥3.3 · Framer Motion ≥12.34 | Visual management Dashboard |
| Runtime | Built-in queue and worker | — | Write serialization, index rebuilding, vitality decay, sleep consolidation |
| Deployment | Docker Compose + profile scripts | Docker ≥20 · Compose ≥2.0 | Quick deployment with A/B/C/D tiers |

Core dependencies can be found in `backend/requirements.txt` and `frontend/package.json`.

---

## 2. Backend Structure

```
backend/
├── main.py               # FastAPI entry point, route registration, lifecycle management
├── mcp_server.py          # Implementation of 9 MCP tools
├── runtime_state.py       # Management of write lane, index worker, vitality decay, cleanup review
├── run_sse.py             # SSE transport layer, supports API Key authentication gating
├── mcp_wrapper.py         # MCP startup wrapper
├── api/
│   ├── __init__.py        # Route export
│   ├── browse.py          # Memory browsing and writing interfaces (prefix: /browse)
│   ├── review.py          # Review, rollback, and integration interfaces (prefix: /review)
│   ├── maintenance.py     # Maintenance, observation, and vitality cleanup interfaces (prefix: /maintenance)
│   ├── setup.py           # First-run setup and local .env write interfaces (prefix: /setup)
│   └── utils.py           # Diff calculation tools (prefers diff-match-patch, falls back to difflib.HtmlDiff)
├── db/
│   ├── __init__.py        # Client factory (get_sqlite_client / close_sqlite_client)
│   ├── sqlite_client.py   # Core database layer (CRUD, retrieval, write_guard, gist, vitality, embedding, rerank)
│   ├── snapshot.py        # Snapshot manager (records pre-state by session and filters Review visibility by current database scope)
│   ├── migration_runner.py# Automatic database migration executor
│   └── migrations/        # SQL migration script directory
├── models/
│   ├── __init__.py        # Model export
│   └── schemas.py         # Pydantic data model definitions
```

> Additional Note: Scripts for deployment, profile application, pre-sharing self-checks, etc., are located in the `scripts/` directory at the repository root, not in the `backend/` subdirectory.

### Core Module Description

- **`main.py`**: FastAPI application entry point, responsible for lifecycle management (database initialization, legacy database file compatibility recovery), CORS configuration, route registration (`review`, `browse`, `maintenance`, `setup`), and health checks (including index status, write lane, and index worker runtime status reports). Default CORS origins are converged to a local common list (`localhost/127.0.0.1` on `5173/3000`); explicitly configured wildcards (`*`) will automatically disable credentials; legacy sqlite recovery will execute regular-file + quick_check + core table existence validation before proceeding, and will strip query/fragment when parsing SQLite URLs, skipping non-file targets like `:memory:` / `file::memory:`.
- **`mcp_server.py`**: Implements 9 MCP tools, including URI parsing (`domain://path` format), snapshot management, write guard decision-making, session caching, and asynchronous index enqueuing logic. It also provides system URI resources (`system://boot`, `system://index`, `system://index-lite`, `system://audit`, `system://recent`). The currently public MCP entry points are `stdio` and SSE: `stdio` connects directly to the tool process; remote access goes through the `/sse + /messages` SSE chain and remains subject to API Key and network-side security controls.
- **`runtime_state.py`**: Manages the write lane (serialized write operations), index worker (asynchronous queue processing for index rebuilding tasks), vitality decay scheduling, cleanup review approval process, and sleep consolidation scheduling. The current session-first retrieval cache and flush tracker both apply in-process bounds with **per-session caps plus a total session limit**, so long-running services do not grow without bound just because many distinct sessions have touched the process.
- **`run_sse.py`**: SSE transport layer, responsible for API Key authentication and session management for the `/sse` and `/messages` links. The current implementation clears sessions upon client disconnection; if you continue to send requests to `/messages` using an old `session_id`, the server will explicitly return `404/410` instead of pretending `202 Accepted`. It also exposes a lightweight `/health` endpoint primarily for Docker / process-level readiness probes; the actual SSE data channel remains `/sse` and continues to be protected by authentication.
- **`db/sqlite_client.py`**: SQLite database operation layer, containing memory CRUD, keyword/semantic/hybrid retrieval modes, write_guard logic (supports three-level determination: semantic matching + keyword matching + LLM decision), gist generation and caching, vitality scoring and decay, embedding retrieval (supports remote API and local hash modes), and reranker integration. Database initialization now uses `.init.lock` based on the database file path for process-level serialization, preventing `backend` / `sse` from competing for the database during initial concurrent startup; non-file targets like `:memory:` will not generate this lock.

---

## 3. HTTP API Endpoints

In plain English:

- `/browse`: The most commonly used, responsible for **viewing and writing memories**.
- `/review`: Used when changes need to be reviewed, responsible for **viewing diffs, rolling back, and confirming integration**.
- `/maintenance`: System O&M entry point, responsible for **cleanup, rebuilding indices, and viewing runtime status**.

If you are just connecting a regular client, usually looking at `/browse` and `/review` is enough.

### Browsing and Writing (`/browse`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/browse/node` | API Key | Browse memory tree (including child nodes, breadcrumbs, gist, aliases) |
| `POST` | `/browse/node` | API Key | Create memory node (includes write_guard) |
| `PUT` | `/browse/node` | API Key | Update memory node (includes write_guard) |
| `DELETE` | `/browse/node` | API Key | Delete memory path |

This is the most "business-like" group of interfaces:

- Memory tree browsing
- Create / Update / Delete memories
- Returns results including the current node, child nodes, breadcrumbs, gist, and other data for direct frontend use

### Review and Rollback (`/review`)

Route-level API Key authentication (all endpoints require authentication).

One implementation boundary to keep in mind:

- snapshot files still live under the repo-level `snapshots/` directory;
- however, session listing, snapshot listing, and snapshot reads are filtered by the **current database scope**, so changing `DATABASE_URL`, switching to another temporary SQLite file, or pointing Docker at another data volume does not mix rollback sessions from a different database into the current Review queue;
- legacy snapshot sessions that were created before scope metadata existed are hidden by default instead of being exposed under the wrong database context.

| Method | Path | Description |
|---|---|---|
| `GET` | `/review/sessions` | List review sessions |
| `GET` | `/review/sessions/{session_id}/snapshots` | View list of session snapshots |
| `GET` | `/review/sessions/{session_id}/snapshots/{resource_id}` | View snapshot details |
| `GET` | `/review/sessions/{session_id}/diff/{resource_id}` | View version diff |
| `POST` | `/review/sessions/{session_id}/rollback/{resource_id}` | Execute rollback |
| `DELETE` | `/review/sessions/{session_id}/snapshots/{resource_id}` | Confirm integration (delete snapshot) |
| `DELETE` | `/review/sessions/{session_id}` | Clear snapshots for the entire session |
| `GET` | `/review/deprecated` | List all deprecated memories |
| `DELETE` | `/review/memories/{memory_id}` | Permanently delete reviewed memory |
| `POST` | `/review/diff` | Universal text diff calculation |

This group of interfaces is more like a "change review area":

- Check session first
- Then check snapshot / diff
- Finally decide whether to rollback or integrate

### Maintenance and Observability (`/maintenance`)

Route-level API Key authentication (all endpoints require authentication).

| Method | Path | Description |
|---|---|---|
| `GET` | `/maintenance/orphans` | View orphaned memories (deprecated or no path pointing to them) |
| `GET` | `/maintenance/orphans/{memory_id}` | View orphaned memory details |
| `DELETE` | `/maintenance/orphans/{memory_id}` | Permanently delete orphaned memory |
| `POST` | `/maintenance/import/prepare` | Prepare external import task (generates executable plan) |
| `POST` | `/maintenance/import/execute` | Execute external import task |
| `GET` | `/maintenance/import/jobs/{job_id}` | View import task status |
| `POST` | `/maintenance/import/jobs/{job_id}/rollback` | Rollback import task |
| `POST` | `/maintenance/learn/trigger` | Trigger explicit learning task |
| `GET` | `/maintenance/learn/jobs/{job_id}` | View explicit learning task status |
| `POST` | `/maintenance/learn/jobs/{job_id}/rollback` | Rollback explicit learning task |
| `POST` | `/maintenance/vitality/decay` | Trigger vitality decay |
| `POST` | `/maintenance/vitality/candidates/query` | Query cleanup candidate memories (supports `domain` / `path_prefix` filtering) |
| `POST` | `/maintenance/vitality/cleanup/prepare` | Prepare cleanup approval (generates review_id + token) |
| `POST` | `/maintenance/vitality/cleanup/confirm` | Confirm and execute cleanup (requires review_id + token + confirmation phrase) |
| `GET` | `/maintenance/index/worker` | View index worker status |
| `GET` | `/maintenance/index/job/{job_id}` | View index task details |
| `POST` | `/maintenance/index/job/{job_id}/cancel` | Cancel index task |
| `POST` | `/maintenance/index/job/{job_id}/retry` | Retry index task |
| `POST` | `/maintenance/index/rebuild` | Trigger full index rebuild |
| `POST` | `/maintenance/index/reindex/{memory_id}` | Reindex single item |
| `POST` | `/maintenance/index/sleep-consolidation` | Trigger sleep consolidation |
| `POST` | `/maintenance/observability/search` | Observability search (including retrieval statistics) |
| `GET` | `/maintenance/observability/summary` | Observability overview |

This group of interfaces is large but can be categorized into 5 types:

1. **Import / Learning Tasks**: `import/*`, `learn/*`
2. **Orphaned Memory Cleanup**: `orphans*`
3. **Vitality Governance**: `vitality/*`
4. **Index Tasks**: `index/*`
5. **Runtime Observability**: `observability/*`

Full API documentation can be accessed via Swagger UI at `http://127.0.0.1:8000/docs` after starting the backend.

---

## 4. MCP Tool Implementation

Implementation file: `backend/mcp_server.py`

| Tool | Type | Description |
|---|---|---|
| `read_memory` | Read | Read memory content, supports full text and segments (chunk_id / range / max_chars), supports system URIs (`system://boot`, `system://index`, `system://index-lite`, `system://audit`, `system://recent`) |
| `create_memory` | Write | Create new memory node (includes write_guard, enters write lane for serialization; recommended to fill `title` explicitly) |
| `update_memory` | Write | Update existing memory (prefers `old_string/new_string` for precise replacement; `append` is only for true tail appending, includes write_guard) |
| `delete_memory` | Write | Delete memory path (enters write lane for serialization) |
| `add_alias` | Write | Add alias path for the same memory (can cross domains) |
| `search_memory` | Retrieval | Unified retrieval entry point (keyword/semantic/hybrid), supports intent classification and strategy templates |
| `compact_context` | Governance | Compress current session context into long-term memory summary (enters write lane for serialization) |
| `rebuild_index` | Maintenance | Full or single index rebuild, supports synchronous waiting and sleep consolidation |
| `index_status` | Maintenance | Query index availability, runtime status, and configuration switches |

For tool return conventions and degradation semantics, see: [TOOLS_EN.md](TOOLS_EN.md)

---

## 5. Frontend Structure

```
frontend/src/
├── App.jsx                                    # Routes and page skeleton
├── main.jsx                                   # React entry point
├── i18n.js                                    # react-i18next initialization, default language, and persistence
├── index.css                                  # Global styles (TailwindCSS)
├── locales/
│   ├── en.js                                  # English copy
│   └── zh-CN.js                               # Chinese copy
├── features/
│   ├── memory/MemoryBrowser.jsx               # Tree browsing, editing, gist view
│   ├── review/ReviewPage.jsx                  # diff, rollback, integrate
│   ├── maintenance/MaintenancePage.jsx        # vitality cleanup and maintenance tasks
│   └── observability/ObservabilityPage.jsx    # retrieval stats and task observability
├── components/
│   ├── AgentationLite.jsx                     # Lightweight Agent integration component
│   ├── DiffViewer.jsx                         # Diff visualization
│   ├── FluidBackground.jsx                    # Fluid animation background
│   ├── GlassCard.jsx                          # Glassmorphism card
│   └── SnapshotList.jsx                       # Snapshot list
├── lib/
│   ├── api.js                                 # Unified API client and runtime auth injection
│   ├── format.js                              # Date/number formatting following current language
│   ├── api.test.js                            # API client unit tests
│   └── api.contract.test.js                   # API auth contract tests
└── test/                                      # Frontend test directory
```

### Dashboard's Four Functional Modules

| Module | Route | Function |
|---|---|---|
| Memory Browser | `/memory` | Browse tree by domain, inline editing, view gist summaries, alias management |
| Review | `/review` | View write snapshot diffs, support rollback and integrate confirmation, clean up deprecated memories |
| Maintenance | `/maintenance` | View vitality scores, clean up orphaned memories, trigger index rebuilds, manage cleanup approval process, support `domain` / `path_prefix` filtering |
| Observability | `/observability` | Retrieval logs and statistics, task execution records, index worker status, system status overview, support `scope_hint` and more granular runtime snapshots |

Additional notes:

- The current version of the frontend defaults to English; the application shell provides a language switch entry and a unified authentication entry in the upper right corner.
- Language switching supports one-click switching between English and Chinese, results will be saved in the browser's `localStorage` as `memory-palace.locale`.
- Common static copy, date/number formats, and common frontend-side error mappings will follow the current language switch.
- If authentication is not yet configured, the page shell will still open, but protected data requests will show an authorization prompt, empty state, or `401`.
- When starting via the recommended one-click Docker path, protected requests usually already work: the frontend proxy automatically forwards the same `MCP_API_KEY` on the server side; however, the page may still keep showing `Set API key`, because the browser page itself does not know the proxy-held key. Only when protected data also starts failing with `401` or empty states should you keep troubleshooting env / proxy configuration.

---

## 6. Frontend Authentication Injection Model

The frontend does not read maintenance keys from `VITE_*` build variables; it uses runtime injection:

```html
<script>
  window.__MEMORY_PALACE_RUNTIME__ = {
    maintenanceApiKey: "<YOUR_MCP_API_KEY>",
    maintenanceApiKeyMode: "header"
  };
</script>
```

`maintenanceApiKeyMode` supports: `header` (sends `X-MCP-API-Key` header) or `bearer` (sends `Authorization: Bearer` header).

> Compatibility: The runtime object is also compatible with the old field name `window.__MCP_RUNTIME_CONFIG__`.
>
> Code reference: `readWindowRuntimeMaintenanceAuth()` and `getMaintenanceAuthState()` in `frontend/src/lib/api.js`.
>
> In plain English: the frontend makes authentication "runtime-decided," so you can either fill in the key directly at the top of the page or have it injected by a deployment script before the page loads.
>
> The extra value of `run_memory_palace_mcp_stdio.sh` is not that `mcp_server.py` would otherwise "randomly pick the wrong database" by itself. Its value is that it gives CLI/client configs a safer default entry: prefer the repository `.env` / `DATABASE_URL`; only when the checkout has neither a local `.env` nor an `.env.docker` file does it fall back to the repo's default SQLite path. If `.env.docker` exists without `.env`, the wrapper now refuses that `demo.db` fallback explicitly so local stdio traffic does not get mixed up with Docker container data.

> Docker one-click deployment uses a third way: it doesn't inject the key into the page but automatically forwards it at the frontend proxy layer.

---

## 7. Data and Task Flow

### Write Path

1. `create_memory` / `update_memory` enters the **write lane** (serialized write operations).
2. Executes **write_guard** determination before writing (core decisions: `ADD` / `UPDATE` / `NOOP` / `DELETE`; `BYPASS` is a process marker for upper-layer metadata-only updates).
   - write_guard supports a three-level determination chain: semantic matching → keyword matching → LLM decision (optional).
3. Generates **snapshot** and version changes (recorded separately by `path` and `memory` dimensions).
4. Enqueues **index task** (returns `index_dropped` / `queue_full` if queue is full).

### Retrieval Path

1. **`preprocess_query`** preprocesses the query text (standardizes whitespace, tokenizes, preserves multilingual/URI).
2. **`classify_intent`** routes by default based on 4 core intents; defaults to `factual` (template `factual_high_precision`) when no significant keyword signals are present, and falls back to `unknown` (template `default`) when signals conflict or are mixed with low signals:
   - `factual` → Strategy template `factual_high_precision` (High-precision matching)
   - `exploratory` → Strategy template `exploratory_high_recall` (High-recall exploration)
   - `temporal` → Strategy template `temporal_time_filtered` (Time-filtered)
   - `causal` → Strategy template `causal_wide_pool` (Causal reasoning, wide candidate pool)
   - `unknown` → Strategy template `default` (Conservative fallback when signals conflict or are mixed)
3. Executes **keyword / semantic / hybrid** retrieval.
4. Optional **reranker** re-ranking (via remote API call).
5. Supports additional query-side constraints, such as `scope_hint`, `domain`, `path_prefix`, `max_priority`.
6. Returns `results` and `degrade_reasons`.

> Intent classification is implemented using the `keyword_scoring_v2` method (`db/sqlite_client.py` `classify_intent` method), inferring intent through keyword matching scores and rankings without external model calls.
>
> **Configuration Strategy Notes**:
> - This project supports two approaches: `1)` directly configuring embedding / reranker / llm separately; `2)` unifying these capabilities via a `router` proxy.
> - `INTENT_LLM_ENABLED` is disabled by default; when enabled, it will first attempt LLM intent classification and fall back to existing keyword rules if it fails.
> - `RETRIEVAL_MMR_ENABLED` is disabled by default; deduplication / diversity re-ranking only occurs under `hybrid` retrieval.
> - `RETRIEVAL_SQLITE_VEC_ENABLED` is disabled by default; the legacy vector path remains the default implementation, with sqlite-vec undergoing controlled rollout.
> - For local development, the former is generally recommended because failures in the three links are usually independent, making it easier to confirm which model, endpoint, or set of keys has an issue.
> - `router` is more suitable as a unified entry point for production/client environments: convenient for centralized authentication, rate limiting, auditing, model switching, and fallback orchestration.

![Memory Write and Review Sequence Diagram](images/记忆写入与审查时序图.png)

---

## 8. Deployment Specifications

| Scenario | Host Port | Internal Container Port | Description |
|---|---|---|---|
| Local Development | Backend `8000` · Frontend `5173` | — | Direct startup |
| Docker Default | Backend `18000` · Frontend `3000` · SSE `3000/sse` | Backend `8000` · Frontend `8080` · SSE `8000` | Ports can be overridden via env vars |

Docker port environment variables:

- Backend: `MEMORY_PALACE_BACKEND_PORT` (falls back to `NOCTURNE_BACKEND_PORT`, default `18000`)
- Frontend: `MEMORY_PALACE_FRONTEND_PORT` (falls back to `NOCTURNE_FRONTEND_PORT`, default `3000`)

One more thing:

- Changing the SSE listening address to `0.0.0.0` (or another non-loopback address) only means remote clients can connect to this listening address; it does not mean `MCP_API_KEY`, reverse proxies, firewalls, or TLS security controls can be bypassed.

Related files:

- Compose file: `docker-compose.yml`
- Image definition: `deploy/docker/Dockerfile.backend` (based on `python:3.11-slim`), `deploy/docker/Dockerfile.frontend` (build stage `node:22-alpine`, run stage `nginxinc/nginx-unprivileged:1.27-alpine`)
- Nginx configuration template: `deploy/docker/nginx.conf.template`
- Entrypoint scripts: `deploy/docker/backend-entrypoint.sh`, `deploy/docker/frontend-entrypoint.sh`
- Backup scripts: `scripts/backup_memory.sh`, `scripts/backup_memory.ps1`
- Pre-publishing check: `scripts/pre_publish_check.sh`

---

## 9. Security Defaults

- All `/maintenance/*` and `/review/*` endpoints require API Key authentication.
- All `/browse` read/write operations (GET/POST/PUT/DELETE) are gated via endpoint-level `Depends(require_maintenance_api_key)`.
- Public HTTP endpoints include `/`, `/health`, and FastAPI's default documentation endpoints; all other Browse / Review / Maintenance and SSE channels follow the same authentication logic.
- Defaults to **fail-closed** (rejects requests) if `MCP_API_KEY` is empty.
- Access is only allowed locally without a key if `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true` **and** the request is loopback (`127.0.0.1` / `::1` / `localhost`), and only for direct loopback requests without forwarding headers.
- Docker containers run as non-root users by default:
  - Backend: Custom user `app` (UID `10001`, GID `10001`)
  - Frontend: Uses official `nginx-unprivileged` non-root image

Detailed policy: [SECURITY_AND_PRIVACY_EN.md](SECURITY_AND_PRIVACY_EN.md)
