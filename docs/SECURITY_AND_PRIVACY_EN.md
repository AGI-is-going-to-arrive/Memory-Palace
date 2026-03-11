# Memory Palace Security and Privacy Guide

This document is intended for users who deploy and maintain Memory Palace, covering key management, interface authentication, Docker security, and pre-sharing or official release security self-checks.

---

## 1. What You Need to Protect

The following keys **should only exist in local .env or protected deployment environment variables** and should not be committed to Git repositories.

> For the full key list, please refer to [`.env.example`](../.env.example).

| Key | Usage | Corresponding Variable in `.env.example` |
|---|---|---|
| `MCP_API_KEY` | Maintenance API, Review API, Browse read/write, and SSE authentication | `MCP_API_KEY=` |
| `RETRIEVAL_EMBEDDING_API_KEY` | Embedding model API access | `RETRIEVAL_EMBEDDING_API_KEY=` |
| `RETRIEVAL_RERANKER_API_KEY` | Reranker model API access | `RETRIEVAL_RERANKER_API_KEY=` |
| `WRITE_GUARD_LLM_API_KEY` | Write Guard LLM decision | `WRITE_GUARD_LLM_API_KEY=` |
| `COMPACT_GIST_LLM_API_KEY` | Compact Context Gist LLM (automatically falls back to Write Guard if empty) | `COMPACT_GIST_LLM_API_KEY=` |
| `INTENT_LLM_API_KEY` | Experimental Intent LLM decision | `INTENT_LLM_API_KEY=` |
| `ROUTER_API_KEY` | Embedding API access in Router mode; and the fallback key when `RETRIEVAL_RERANKER_API_KEY` is not explicitly configured for the Reranker | `ROUTER_API_KEY=` |

---

## 2. Best Practices

- ✅ Only commit `.env.example`, **do not commit** `.env` (already added to [`.gitignore`](../.gitignore))
- ✅ Only use placeholders like `<YOUR_API_KEY>` in documentation
- ✅ Ensure screenshots do not contain real keys, usernames, or absolute paths before sharing publicly
- ✅ Do not print request headers and keys in external logs
- ✅ Periodically rotate API Keys, especially after changes in team members
- ✅ In Docker scenarios, prioritize using server-side proxy forwarding for authentication headers instead of writing keys into frontend static resources

---

## 3. Interface Authentication Strategy

### Protected API Scope

The following interfaces are protected by default:

| API Prefix | Protection Scope | Code Source |
|---|---|---|
| `/maintenance/*` | All requests | `backend/api/maintenance.py` — `require_maintenance_api_key` as a route dependency |
| `/review/*` | All requests | `backend/api/review.py` — imports and depends on the same authentication function |
| `/browse/*` | All requests (including read operations) | `backend/api/browse.py` — routes are uniformly mounted with `Depends(require_maintenance_api_key)` |
| SSE Interfaces | `/sse` and `/messages` | `backend/run_sse.py` — ASGI middleware `apply_mcp_api_key_middleware` |

> 📖 `GET` requests for `/browse/node` are also within the scope of authentication; please include `X-MCP-API-Key` or `Authorization: Bearer`.

### Authentication Methods (Choose one)

**Header Method (Recommended):**

```
X-MCP-API-Key: <MCP_API_KEY>
```

**Bearer Token Method:**

```
Authorization: Bearer <MCP_API_KEY>
```

> The backend uses `hmac.compare_digest` for constant-time comparison (see the authentication logic in `backend/api/maintenance.py` and `backend/run_sse.py`) to prevent timing attacks.

### Default Behavior When No Key is Provided

Authentication follows a **fail-closed** strategy, with the specific logic as follows:

| Condition | Behavior | HTTP Response |
|---|---|---|
| `MCP_API_KEY` is set and the request carries the correct key | ✅ Allowed | — |
| `MCP_API_KEY` is set but the key is incorrect or missing | ❌ Denied | `401`, `reason: invalid_or_missing_api_key` |
| `MCP_API_KEY` is empty, `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true`, the request comes from loopback and does not contain `Forwarded` / `X-Forwarded-*` / `X-Real-IP` or other forwarding headers | ✅ Allowed | — |
| `MCP_API_KEY` is empty, `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true`, the request comes from loopback but contains `Forwarded` / `X-Forwarded-*` / `X-Real-IP` or other forwarding headers | ❌ Denied | `401`, `reason: insecure_local_override_requires_loopback` |
| `MCP_API_KEY` is empty, `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true`, the request is not from loopback | ❌ Denied | `401`, `reason: insecure_local_override_requires_loopback` |
| `MCP_API_KEY` is empty, insecure local is not enabled | ❌ Denied | `401`, `reason: api_key_not_configured` |

> 📌 Loopback addresses only include `127.0.0.1`, `::1`, and `localhost` (code constant `_LOOPBACK_CLIENT_HOSTS`); and the request must be a direct request to the local machine (no `Forwarded` / `X-Forwarded-*` / `X-Real-IP` or other forwarding headers).

### Authentication Anchors in the Current Repository

The above authentication logic is covered in the following test files in the current repository:

- `backend/tests/test_week6_maintenance_auth.py` — Maintenance API five authentication scenarios
- `backend/tests/test_week6_sse_auth.py` — SSE authentication scenarios
- `backend/tests/test_sensitive_api_auth.py` — Review and Browse read/write authentication
- `backend/tests/test_review_rollback.py` — Review operation authentication test

---

## 4. Frontend Key Injection (Runtime)

The frontend does not hardcode keys at build time; instead, it reads them via runtime injection. This method is more suitable for local debugging or private deployment environments that you control:

```html
<script>
  window.__MEMORY_PALACE_RUNTIME__ = {
    maintenanceApiKey: "<YOUR_MCP_API_KEY>",
    maintenanceApiKeyMode: "header"  // Optional values: "header" | "bearer"
  };
</script>
```

> ⚠️ This is suitable for local debugging or deployment environments you control. Do not write the real `MCP_API_KEY` directly into public pages or any static resources that will be exposed to end users, as this global object can be read directly in the browser.

**How it Works** (see `frontend/src/lib/api.js`):

1. `readRuntimeMaintenanceAuth()` reads `window.__MEMORY_PALACE_RUNTIME__`
2. axios request interceptor `isProtectedApiRequest()` determines if the request needs authentication
3. Automatically injects authentication headers for `/maintenance/*`, `/review/*`, `/browse/*`, and the new `/setup/*`

> Compatibility: Also supports the old field name `window.__MCP_RUNTIME_CONFIG__` (see the runtime config fallback logic in `frontend/src/lib/api.js`).

### Security Boundary of the First-run Setup Assistant

The current release adds a Dashboard first-run setup assistant, but it is not a generic “edit server config from the browser” backdoor:

- `/setup/status` is allowed when:
  - the request is a direct loopback request (`127.0.0.1` / `::1` / `localhost`, with no forwarded headers, and with a loopback host in the request itself), or
  - the request carries a valid `MCP_API_KEY`
- the `/setup/config` **write path is loopback-only**; even a request with a valid `MCP_API_KEY` cannot rewrite the host `.env` remotely
- the assistant only writes a white-listed set of env keys; it is not an arbitrary file writer
- it only targets the local checkout `.env`
- when the current process is running inside Docker, the assistant explicitly returns `setup_apply_unsupported` and stays in guidance mode instead of pretending it persisted container env / proxy changes
- existing secrets are never echoed back into the UI; the frontend only receives masked “configured vs missing” summaries
- only the Dashboard `MCP_API_KEY` may be stored in browser localStorage; embedding / reranker / LLM provider keys are not stored in the browser

**New validation anchors:**

- `backend/tests/test_setup_api.py` — Verifies loopback access, remote auth, white-listed `.env` writes, and Docker fail-closed behavior
- `frontend/src/App.test.jsx` — Verifies first-run auto-open and the browser-only Dashboard-key flow
- `frontend/src/lib/api.contract.test.js` — Verifies `/setup/*` also goes through the unified auth-header injection path

**Default approach for Docker one-click deployment is different:**

- `apply_profile.*` will automatically generate a local key if `MCP_API_KEY` is found to be empty under the `docker` platform.
- The frontend container will not write this key into the page; instead, Nginx proxy will forward the `X-MCP-API-Key` at the server side to `/api/*`, `/sse`, `/messages`.
- This way the browser can use the Dashboard directly without exposing the real key in the page source.
- This convenience path assumes the frontend port itself is trusted. Anyone who can directly reach the Docker Dashboard port can also use those proxied protected routes, so `MCP_API_KEY` is **not** acting as end-user auth at that layer. Put your own VPN, reverse-proxy auth, or network ACL in front of `3000` before exposing it beyond a trusted environment.

**Frontend Test Coverage:**

- `frontend/src/lib/api.contract.test.js` — Verifies runtime config injection and authentication header attachment.

---

## 5. Docker Security

The following security configurations can be directly verified in the project's Docker files:

| Security Measure | Implementation Method | File Reference |
|---|---|---|
| Non-root execution (Backend) | `groupadd --gid 10001 app && useradd --uid 10001` | `deploy/docker/Dockerfile.backend` |
| Non-root execution (Frontend) | Using `nginxinc/nginx-unprivileged:1.27-alpine` base image | `deploy/docker/Dockerfile.frontend` |
| Frontend proxy authentication | Nginx forwards `X-MCP-API-Key` at the server side; the real key is not stored on the browser side | `deploy/docker/nginx.conf.template` |
| Prohibit privilege escalation | `security_opt: no-new-privileges:true` | `docker-compose.yml` |
| Data persistence | Docker Volumes `memory_palace_data` → `/app/data`, `memory_palace_snapshots` → `/app/snapshots` | `docker-compose.yml` |
| Health check (Backend) | Python `urllib.request.urlopen('http://127.0.0.1:8000/health')` | `backend.healthcheck` in `docker-compose.yml` |
| Health check (Frontend) | `wget -q -O - http://127.0.0.1:8080/` | `frontend.healthcheck` in `docker-compose.yml` |

---

<p align="center">
  <img src="images/security_checklist.png" width="900" alt="Pre-sharing security self-check checklist" />
</p>

## 6. Pre-sharing or Release Self-check Checklist

Before sharing the project, delivering environments, or official release, please complete the following repository hygiene and security self-check steps:

0. **One-click Self-check (Recommended)**:

   ```bash
   bash scripts/pre_publish_check.sh
   ```

   The script checks: common local sensitive artifacts / tool configs / local reports presence, git tracking status, key patterns in tracked files, personal absolute path leaks, and `.env.example` API key placeholder status. It's more like a "repository hygiene check before sharing"; finding local files usually results in a `WARN` rather than a `FAIL`.

1. **Check Workspace Status** — Confirm no accidental exposure:

   ```bash
   git status
   ```

   Ensure the following files are not in the commit (already configured in `.gitignore`):
   - `.env`, `.env.docker` (if you explicitly reused a fixed Docker env file)
   - `.venv`, `.mcp.json`, `.mcp.json.bak`, `.claude/`, `.codex/`, `.cursor/`, `.opencode/`, `.gemini/`, `.agent/` (usually generated by your local sync / install scripts)
   - `*.db` (Database files)
   - `*.init.lock`, `*.migrate.lock` (Database initialization / migration lock files)
   - `backend/backend.log`, `frontend/frontend.log`
   - `snapshots/`, `frontend/dist/`
   - `backend/tests/benchmark/.real_profile_cache/`
   - `docs/skills/TRIGGER_SMOKE_REPORT.md`, `docs/skills/MCP_LIVE_E2E_REPORT.md`, `docs/skills/CLAUDE_SKILLS_AUDIT.md`
   - Any `.DS_Store`

2. **Keyword Scan** — Check for residual real keys in code and documentation:

   ```bash
   # Search for possible key leaks (suggest looking only at filenames to avoid echoing real values in terminal)
   rg -n -l "sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY" .
   ```

3. **Check Absolute Paths** — Ensure documentation does not contain local machine paths:

   ```bash
   # If you need to check manually, please replace the placeholders below with your actual path prefix
   grep -rn "<user-home>" --include="*.md" <repo-root>
   grep -rn "C:/absolute/path/to/" --include="*.md" <repo-root>
   ```

4. **Verify Running** — Confirm project can be reproducibly built:

   ```bash
   # Minimal check
   bash scripts/pre_publish_check.sh
   curl -fsS http://127.0.0.1:8000/health

   # Frontend build check
   cd frontend && npm ci && npm run test && npm run build
   ```

   > For deeper verification, run `cd backend && .venv/bin/python -m pip install -r requirements-dev.txt && .venv/bin/python -m pytest tests -q` as well.

---

## 7. Local Files That Usually Should Not Be Committed

| File / Directory | Description |
|---|---|
| `.env`, `.env.docker` (if you explicitly reused a fixed Docker env file) | May contain real API keys |
| `.venv`, `backend/.venv`, `frontend/.venv` | Local virtual environments, should not enter the repository |
| `.mcp.json`, `.mcp.json.bak`, `.claude/`, `.codex/`, `.cursor/`, `.opencode/`, `.gemini/`, `.agent/` | Local tool / MCP configuration directories (usually generated by your local sync / install scripts) |
| `*.db` | SQLite database files (e.g., `demo.db`) |
| `*.init.lock`, `*.migrate.lock` | Lock files generated during database initialization / migration |
| `backend/backend.log` | Backend running logs |
| `frontend/frontend.log` | Frontend running logs |
| `snapshots/` | Local snapshot directory |
| `backend/tests/benchmark/.real_profile_cache/` | Local benchmark temporary database |
| `__pycache__/`, `backend/.pytest_cache/` | Python cache |
| `frontend/node_modules` | NPM dependencies |
| `frontend/dist/` | Frontend build artifacts |
| `.DS_Store` | macOS system files |
| `backups/` | Local backup directory |
| `docs/improvement/` | Phase-specific plans, retest drafts, troubleshooting records |
| `<repo-root>/docs/skills/TRIGGER_SMOKE_REPORT.md` | Local skill smoke summary |
| `<repo-root>/docs/skills/MCP_LIVE_E2E_REPORT.md` | Local MCP e2e summary |
| `backend/docs/benchmark_*.md` | Local benchmark analysis notes |
| `backend/tests/benchmark_results.md` | One-off benchmark summary draft |
| `docs/evaluation_old_vs_new_executive_summary_2026-03-05.md` | One-off comparison summary |
| `docs/changelog/current_code_improvements_vs_legacy_docs.md` | Supplemental difference list |

> 💡 Keep `.env.example` as a configuration template committed to the repository.
>
> 💡 Use placeholders in public documentation:
>
> - `<repo-root>`: Repository root directory
> - `<user-home>`: User home directory
> - `/absolute/path/to/...`: macOS / Linux absolute path example
> - `C:/absolute/path/to/...`: Windows absolute path example
