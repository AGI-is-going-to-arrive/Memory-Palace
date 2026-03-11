# Memory Palace Deployment Profiles

This document helps you choose the appropriate Memory Palace configuration profile (A / B / C / D) based on your hardware conditions and usage scenarios, and guides you through the deployment process.

---

## Quick Navigation

| Section | Content |
|---|---|
| [1. Three-Step Quick Start](#1-three-step-quick-start) | The fastest way to get started |
| [2. Profiles Overview](#2-profiles-overview) | Differences between A/B/C/D configurations |
| [3. Detailed Configuration for Each Profile](#3-detailed-configuration-for-each-profile) | `.env` parameter descriptions for each profile |
| [4. Optional LLM Parameters (write_guard / compact_context / intent)](#4-optional-llm-parameters-write_guard--compact_context--intent) | Write Guard, context compaction, and intent enhancement |
| [5. Docker One-Click Deployment](#5-docker-one-click-deployment-recommended) | Recommended containerized deployment method |
| [6. Manual Startup](#6-manual-startup) | Local startup method without Docker |
| [7. Local Inference Service Reference](#7-local-inference-service-reference) | Ollama / LM Studio / vLLM / SGLang |
| [8. Vitality Parameters](#8-vitality-parameters) | Memory vitality decay and cleanup mechanism |
| [9. API Authentication](#9-api-authentication) | Security for Maintenance / SSE / Browse / Review interfaces |
| [10. Tuning and Troubleshooting](#10-tuning-and-troubleshooting) | Common issues and optimization suggestions |
| [11. Assistant Scripts Overview](#11-assistant-scripts-overview) | List of all deployment-related scripts |

---

## 1. Three-Step Quick Start

1.  **Choose a Profile**: Select `A`, `B`, `C`, or `D` based on your hardware (choose **B** if unsure; choose **C** if you have model services ready and want long-term use).
2.  **Generate Configuration**: Run the `apply_profile` script to generate the `.env` file.
3.  **Start Services**: Use Docker one-click deployment **OR** manually start the backend + frontend.

> **💡 Note**: **Profile B remains the default starting profile** because it has zero external dependencies. However, as long as you have model services ready, **Profile C is the strongly recommended profile**. Before upgrading to C, please ensure you fill in the embedding / reranker settings in the `.env` file; if you also want to enable LLM-assisted capabilities, continue filling in the corresponding LLM configurations.

---

## 2. Profiles Overview

| Profile | Search Mode | Embedding Method | Reranker | Use Case |
|:---:|---|---|---|---|
| **A** | `keyword` | Disabled (`none`) | ❌ Disabled | Minimum requirements, pure keyword search, fast verification |
| **B** | `hybrid` | Local Hash (`hash`) | ❌ Disabled | **Default starting profile**, single-machine development, no extra services |
| **C** | `hybrid` | API Call (`router`) | ✅ Enabled | **Strongly recommended**, local deployment of embedding/reranker model services |
| **D** | `hybrid` | API Call (`router`) | ✅ Enabled | Uses remote API services, no local GPU required |

**Key Differences**:

*   **A → B**: Upgrades from pure keyword to hybrid search using built-in hash vectors (no external dependencies).
*   **B → C/D**: Connects to real embedding + reranker models for optimal semantic retrieval.
*   **C vs D**: Identical algorithm paths; the main difference in the default template is the model service address (local vs remote) and the default `RETRIEVAL_RERANKER_WEIGHT` (C=`0.30`, D=`0.35`).

> **Terminology Note (to avoid confusion with evaluation docs)**: In the deployment templates, C has the reranker enabled by default. In the "Real A/B/C/D Runs" section of `docs/EVALUATION_EN.md`, `profile_c` acts as a control group with the reranker disabled (`profile_d` enables it) to observe the gain.
>
> **Additional Note**: C/D templates follow the `router` path by default. If your deployment doesn't use a unified router, you can also directly configure `RETRIEVAL_EMBEDDING_*`, `RETRIEVAL_RERANKER_*`, and `WRITE_GUARD_LLM_* / COMPACT_GIST_LLM_*` to connect to OpenAI-compatible services.
>
> **Why not force everything through a router?**
> - The models, addresses, keys, and failure modes for `embedding`, `reranker`, and `llm` links are different. Configuring them separately makes them easier to locate and replace.
> - The repository already supports independent configuration: `RETRIEVAL_EMBEDDING_*`, `RETRIEVAL_RERANKER_*`, and `WRITE_GUARD_LLM_* / COMPACT_GIST_LLM_*` can all work independently.
> - The primary value of a `router` is on the production side: unified entry point, model orchestration, authentication, rate limiting, auditing, and subsequent provider switching. It is suitable as the **default template standard**, but it is not the only supported method.
>
> **Configuration Priority (to avoid misconfiguration)**:
> - `RETRIEVAL_EMBEDDING_BACKEND` only affects the Embedding link, not the Reranker.
> - There is no `RETRIEVAL_RERANKER_BACKEND` switch; whether it is enabled is controlled solely by `RETRIEVAL_RERANKER_ENABLED`.
> - Reranker addresses/keys prioritize `RETRIEVAL_RERANKER_API_BASE/API_KEY`. If missing, they fall back to `ROUTER_API_BASE/ROUTER_API_KEY`, then finally to `OPENAI_BASE_URL/OPENAI_API_BASE` and `OPENAI_API_KEY`.

---

## 3. Detailed Configuration for Each Profile

### Profile A —— Pure Keyword (Minimal)

Zero dependencies, uses keyword matching only:

```bash
# Core Configuration (see deploy/profiles/macos/profile-a.env)
SEARCH_DEFAULT_MODE=keyword
RETRIEVAL_EMBEDDING_BACKEND=none
RETRIEVAL_RERANKER_ENABLED=false
RUNTIME_INDEX_WORKER_ENABLED=false    # No index worker needed
```

### Profile B —— Hybrid Retrieval + Local Hash (Default)

Uses built-in 64-dimensional hash vectors to provide basic semantic capabilities:

```bash
# Core Configuration (see deploy/profiles/macos/profile-b.env)
SEARCH_DEFAULT_MODE=hybrid
RETRIEVAL_EMBEDDING_BACKEND=hash
RETRIEVAL_EMBEDDING_MODEL=hash-v1
RETRIEVAL_EMBEDDING_DIM=64
RETRIEVAL_RERANKER_ENABLED=false
RUNTIME_INDEX_WORKER_ENABLED=true     # Enable asynchronous indexing
RUNTIME_INDEX_DEFER_ON_WRITE=true
```

### Profile C/D —— Hybrid Retrieval + Real Models (Recommended Goal; C is Highly Recommended)

Profiles C and D use the same algorithm path, both calling OpenAI-compatible APIs via a `router`; in the default template, D has a higher reranker weight (`0.35`).

> **The Bottom Line**:
> - **Profile B**: Default start, ensures you can get up and running today.
> - **Profile C**: Strongly recommended if you already have available model services.
> - **Profile D**: Remote API / Customer environments.
>
> **Minimum requirements before upgrading to Profile C**:
> - Embedding: `RETRIEVAL_EMBEDDING_*`
> - Reranker: `RETRIEVAL_RERANKER_*`
> - If you also want to enable LLM-assisted write guard / gist / intent routing: Fill in `WRITE_GUARD_LLM_*`, `COMPACT_GIST_LLM_*`, and optionally `INTENT_LLM_*`.

**Profile C** (Local Model Services) —— Suitable for those with a GPU or using local inference like Ollama/vLLM:

```bash
# Core Configuration (see deploy/profiles/macos/profile-c.env)
SEARCH_DEFAULT_MODE=hybrid
RETRIEVAL_EMBEDDING_BACKEND=router

# Embedding Configuration
ROUTER_API_BASE=http://127.0.0.1:PORT/v1          # ← Replace PORT with actual port
ROUTER_API_KEY=replace-with-your-key
ROUTER_EMBEDDING_MODEL=<your-embedding-model-id>
RETRIEVAL_EMBEDDING_MODEL=<your-embedding-model-id>
RETRIEVAL_EMBEDDING_API_BASE=http://127.0.0.1:PORT/v1
RETRIEVAL_EMBEDDING_API_KEY=replace-with-your-key
RETRIEVAL_EMBEDDING_DIM=4096

# Reranker Configuration
RETRIEVAL_RERANKER_ENABLED=true
RETRIEVAL_RERANKER_API_BASE=http://127.0.0.1:PORT/v1
RETRIEVAL_RERANKER_API_KEY=replace-with-your-key
RETRIEVAL_RERANKER_MODEL=<your-reranker-model-id>
RETRIEVAL_RERANKER_WEIGHT=0.30                     # Recommended 0.20 ~ 0.40
```

If you do not use a unified `router`, you can also directly configure OpenAI-compatible embedding / reranker services:

```bash
# Connect directly to OpenAI-compatible services
RETRIEVAL_EMBEDDING_BACKEND=api
RETRIEVAL_RERANKER_ENABLED=true
RETRIEVAL_RERANKER_API_BASE=http://127.0.0.1:PORT/v1
RETRIEVAL_RERANKER_API_KEY=replace-with-your-key
# Fill in the following two items according to your actual model names
RETRIEVAL_EMBEDDING_MODEL=<your-embedding-model-id>
RETRIEVAL_RERANKER_MODEL=<your-reranker-model-id>
# Note: There is no RETRIEVAL_RERANKER_BACKEND configuration item
```

**Profile D** (Remote API Services) —— No local GPU required, uses cloud models:

```bash
# Main difference from C: API address points to remote, default reranker weight is higher
ROUTER_API_BASE=https://<your-router-host>/v1
RETRIEVAL_EMBEDDING_API_BASE=https://<your-router-host>/v1
RETRIEVAL_RERANKER_API_BASE=https://<your-router-host>/v1
RETRIEVAL_RERANKER_WEIGHT=0.35                     # Remote recommended slightly higher
```

> **🔑 Primary Tuning Parameter for C/D**: `RETRIEVAL_RERANKER_WEIGHT`, suggested range `0.20 ~ 0.40`, fine-tune in `0.05` increments.
>
> **Model ID Reminder**: The `<your-embedding-model-id>` / `<your-reranker-model-id>` above are recommended placeholders. The project is not bound to any specific model family; please fill in your own provider's actual model ID.

If you adopt the direct connection method, note one boundary first:

- `docker_one_click.sh/.ps1` does **not** directly read your manually edited repository `.env` as the final Docker configuration.
- On each run, it first generates a Docker env file from `deploy/profiles/docker/profile-*.env`, and only then decides whether to inject runtime overrides based on the script arguments.
- So if you only write your final direct-API settings into the repository-root `.env` and then run `bash scripts/docker_one_click.sh --profile c`, the actual startup still uses the profile template path, not necessarily the final values you just wrote.

The minimum verification path should therefore be split into two cases:

```bash
# Option A: local debugging via the one-click script (explicit injection)
# Use this when your current shell already has the embedding / reranker / LLM API base, key, and model values prepared.
bash scripts/docker_one_click.sh --profile c --allow-runtime-env-injection
```

```bash
# Option B: verify the final Docker env file you actually plan to deploy
# In this case, explicitly point compose to that file instead of assuming the one-click script will read the repository .env
MEMORY_PALACE_DOCKER_ENV_FILE=/absolute/path/to/your-docker.env docker compose up -d --build
```

Then verify the basic interfaces:

```bash
curl -fsS http://127.0.0.1:18000/health
curl -fsS http://127.0.0.1:18000/browse/node -H "X-MCP-API-Key: <YOUR_MCP_API_KEY>"
```

Decision Criteria:

1.  Please only compare and accept results from the **same final deployment configuration**. Do not mix results from different paths.
2.  For `docker_one_click`, `--allow-runtime-env-injection` is a **local debugging path**, not a promise that the script is consuming the repository `.env` as the final Docker config.
3.  If you want to validate the exact direct-API Docker configuration you plan to deploy, start from that final Docker env file and then run the startup + health checks against that same file.
4.  If startup fails under placeholder endpoints/keys, it is an expected fail-closed; please replace them with real, available values and re-verify.

### Model ID Examples

It is recommended that you fill these in based on "Purpose -> Real model ID":

| Purpose | Suggested Writing | Description |
|---|---|---|
| Embedding | `<your-embedding-model-id>` | Fill in your provider's actual embedding model ID |
| Reranker | `<your-reranker-model-id>` | Fill in your provider's actual reranker model ID |
| Optional LLM | `<your-chat-model-id>` | Used for `write_guard` / `compact_context` / `intent` |

Whether you go through a `router` or a direct API, the project simply passes these strings as-is to your OpenAI-compatible service; it does not enforce any specific model brand or family.

---

## 4. Optional LLM Parameters (write_guard / compact_context / intent)

These parameters control three optional LLM features: **Write Guard** (quality filtering), **Context Compaction** (summary generation), and **Intent enhancement** (experimental classification support).

Configure these in `.env`:

```bash
# Write Guard LLM (Filters low-quality memories)
WRITE_GUARD_LLM_ENABLED=false
WRITE_GUARD_LLM_API_BASE=             # OpenAI-compatible /chat/completions endpoint
WRITE_GUARD_LLM_API_KEY=
WRITE_GUARD_LLM_MODEL=your-chat-model-id

# Compact Context Gist LLM (Generates summaries)
COMPACT_GIST_LLM_ENABLED=false
COMPACT_GIST_LLM_API_BASE=
COMPACT_GIST_LLM_API_KEY=
COMPACT_GIST_LLM_MODEL=your-chat-model-id

# Intent LLM (Experimental intent classification enhancement)
INTENT_LLM_ENABLED=false
INTENT_LLM_API_BASE=
INTENT_LLM_API_KEY=
INTENT_LLM_MODEL=your-chat-model-id
```

> **Fallback Mechanism**: When `COMPACT_GIST_LLM_*` is not configured, `compact_context` will automatically fall back to using the `WRITE_GUARD_LLM_*` configuration. Both links use the OpenAI-compatible chat interface (`/chat/completions`).
>
> **Note**: The model IDs here are only placeholders. As long as your service is compatible with OpenAI-style `/embeddings`, `/chat/completions`, or reranker endpoints, you can change them to your own actual model IDs.
>
> If your provider uses a different model ID naming convention, please keep within the same model family and change it to your provider's actual model ID.
>
> **Additional Note**: `INTENT_LLM_*` is an experimental capability. When disabled or unavailable, it will fall back to keyword rules directly without affecting the default retrieval path.
>
> **Complete Advanced Configuration**: `CORS_ALLOW_*`, `RETRIEVAL_MMR_*`, `INDEX_LITE_ENABLED`, `AUDIT_VERBOSE`, runtime observation/sleep consolidation limits, etc., are not covered in detail in this section. Please refer to `.env.example` for the full list.
>
> **Enabling Suggestions (Recommended to follow these)**:
> - `INTENT_LLM_ENABLED=false`
>   - Suitable for default production / default user deployments.
>   - Only try this if you have a stable chat model and want to enhance intent classification for fuzzy queries.
> - `RETRIEVAL_MMR_ENABLED=false`
>   - Keep disabled by default.
>   - Open only if the redundancy in the top hybrid retrieval results is noticeably high.
> - `CORS_ALLOW_ORIGINS=`
>   - Leave blank for local development to use the built-in local allowlist.
>   - For production browser access, please explicitly list allowed domains; using `*` is not recommended.
> - `RETRIEVAL_SQLITE_VEC_ENABLED=false`
>   - Currently remains a rollout switch.
>   - Not recommended for standard user deployments; enable only during maintenance to verify extension paths, readiness, and fallback links.

---

## 5. Docker One-Click Deployment (Recommended)

### Prerequisites

*   [Docker](https://docs.docker.com/get-docker/) installed and Docker Engine running.
*   Supports `docker compose` (included by default in Docker Desktop).

### macOS / Linux

```bash
cd <project-root>
bash scripts/docker_one_click.sh --profile b
# To inject the current shell's API address/key/model into this Docker env file (disabled by default):
bash scripts/docker_one_click.sh --profile c --allow-runtime-env-injection
```

### Windows PowerShell

```powershell
cd <project-root>
.\scripts\docker_one_click.ps1 -Profile b
# To inject the current PowerShell process environment into this Docker env file (disabled by default):
.\scripts\docker_one_click.ps1 -Profile c -AllowRuntimeEnvInjection
```

> `apply_profile.ps1` now performs a unified deduplication of **all duplicate env keys**, keeping the last value, instead of just handling `DATABASE_URL`.
>
> For native Windows / `pwsh`, it is still recommended to run it separately in the target environment; these steps are for deployment verification and are not intended for beginner-level reading.
>
> `docker_one_click.sh/.ps1` will generate an independent temporary Docker env file for each run by default and pass it to `docker compose` via `MEMORY_PALACE_DOCKER_ENV_FILE`. It will only reuse a specified path if that environment variable is explicitly set, rather than always sharing `.env.docker`.
>
> If the `MCP_API_KEY` in this Docker env file is empty, `apply_profile.sh/.ps1` will automatically generate a local key for both the Dashboard proxy and SSE.
>
> The current compose also waits for **both the backend and SSE `/health` checks** to pass before the frontend is considered ready. If you see the container has just started but the browser isn't connecting, wait a few seconds first; do not immediately judge it as a deployment failure.
>
> Concurrent one-click deployments under the same checkout will be serialized by a deployment lock to prevent shared compose projects/env files from overwriting each other.
>
> If you enable `--allow-runtime-env-injection` for `profile c/d`, the script will switch this run to explicit API mode and additionally force `RETRIEVAL_EMBEDDING_BACKEND=api`. When `RETRIEVAL_EMBEDDING_API_*` / `RETRIEVAL_RERANKER_API_*` are not explicitly provided, it will prioritize reusing the `ROUTER_API_BASE/ROUTER_API_KEY` from the current process as a fallback; if you have also set `INTENT_LLM_*`, that link will also be injected.

### Access Addresses After Deployment

| Service | Host Default Port | Container Internal Port | Access Method |
|---|:---:|:---:|---|
| Frontend (Web UI) | `3000` | `8080` | `http://localhost:3000` |
| Backend (API) | `18000` | `8000` | `http://localhost:18000` |
| SSE (Frontend Proxy) | `3000` | `8080 -> 8000` | `http://localhost:3000/sse` |
| Health Check | `18000` | `8000` | `http://localhost:18000/health` |

### What the One-Click Script Does

1.  Calls the profile script to generate the Docker env file for this run from the template (per-run temporary file by default; reuses the specified path only if `MEMORY_PALACE_DOCKER_ENV_FILE` is explicitly set).
2.  Disables runtime environment injection by default to avoid implicit template overwriting; parameters are only overridden when injection is explicitly enabled. For `profile c/d`, the injection mode additionally forces `RETRIEVAL_EMBEDDING_BACKEND=api` for local debugging; if explicit `RETRIEVAL_*` is not provided, it prioritizes reusing `ROUTER_API_BASE/ROUTER_API_KEY` as a fallback for the embedding / reranker API base+key, while also passing through the optional `INTENT_LLM_*`.
3.  Automatically detects port conflicts; if the default port is occupied, it automatically increments to find an idle port.
4.  Detects and injects Docker persistent volumes: the data volume defaults to `memory_palace_data` (compatible with the old `nocturne_*` volume), and the snapshot volume defaults to `memory_palace_snapshots`.
5.  Adds a deployment lock to concurrent deployments under the same checkout to prevent multiple `docker_one_click` instances from overwriting each other.
6.  Uses `docker compose` to build and start the backend, SSE, and frontend containers.

### Security Notes

*   **Backend Container**: Runs as a non-root user (`UID=10001`, see `deploy/docker/Dockerfile.backend`).
*   **Frontend Container**: Uses the `nginxinc/nginx-unprivileged` image (default `UID=101`).
*   Docker Compose is configured with `security_opt: no-new-privileges:true`.

### Stopping Services

```bash
cd <project-root>
COMPOSE_PROJECT_NAME=<printed-compose-project-name> docker compose -f docker-compose.yml down --remove-orphans
```

> The `down --remove-orphans` command above will not delete `memory_palace_data` or `memory_palace_snapshots`; the database and Review snapshots will only be cleared if you explicitly execute `down -v` or manually delete the corresponding volumes.

---

## 6. Manual Startup

If you are not using Docker, you can start the backend and frontend manually.

### Step 1: Generate `.env` Configuration

```bash
# macOS / Linux (Generates Profile B configuration by default; Linux also uses the `macos` template value)
cd <project-root>
bash scripts/apply_profile.sh macos b

# If your embedding / reranker model services are ready, switch to Profile C
# bash scripts/apply_profile.sh macos c

# Windows PowerShell
.\scripts\apply_profile.ps1 -Platform windows -Profile b

# If model services are ready, switch to Profile C
# .\scripts\apply_profile.ps1 -Platform windows -Profile c
```

> Script Logic: Copies `.env.example` to `.env`, then appends the override parameters from `deploy/profiles/<platform>/profile-<x>.env`.
>
> `apply_profile.sh/.ps1` currently deduplicates env keys after generation to prevent inconsistent behavior across different parsers for keys that appear multiple times.
>
> If you are just running the repository manually for the first time, Profile B is the safest start; switch to Profile C only when the embedding / reranker links are available.

### Step 2: Start the Backend

```bash
cd <project-root>/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 18000
```

### Step 3: Start the Frontend

```bash
cd <project-root>/frontend
npm install
MEMORY_PALACE_API_PROXY_TARGET=http://127.0.0.1:18000 npm run dev -- --host 127.0.0.1 --port 3000
```

---

## 7. Local Inference Service Reference

If using Profile C, you need to run embedding/reranker models locally. Below are common local inference services:

| Service | Official Documentation | Hardware Recommendations |
|---|---|---|
| Ollama | [docs.ollama.com](https://docs.ollama.com/gpu) | Can run on CPU; GPU recommended based on model size for VRAM |
| LM Studio | [lmstudio.ai](https://lmstudio.ai/docs/app/system-requirements) | 16GB+ RAM recommended |
| vLLM | [docs.vllm.ai](https://docs.vllm.ai/en/stable/getting_started/installation/gpu.html) | Linux-first; NVIDIA compute capability 7.0+ |
| SGLang | [docs.sglang.ai](https://docs.sglang.ai/index.html) | Supports NVIDIA / AMD / CPU / TPU |

**OpenAI-compatible Interface Documentation**:

*   Ollama: [OpenAI Compatibility](https://docs.ollama.com/api/openai-compatibility)
*   LM Studio: [OpenAI Endpoints](https://lmstudio.ai/docs/app/api/endpoints/openai)

> **Important**: Memory Palace embedding/reranker calls are made via the OpenAI-compatible API. If you enable the reranker (enabled by default in C/D), the backend service needs an available rerank endpoint in addition to `/v1/embeddings` (calls `/rerank` by default).

---

## 8. Vitality Parameters

The Vitality system is used for automatic memory lifecycle management: **Access Reinforcement → Natural Decay → Cleanup Candidate → Manual Confirmation**.

| Parameter | Default Value | Description |
|---|:---:|---|
| `VITALITY_MAX_SCORE` | `3.0` | Maximum vitality score |
| `VITALITY_REINFORCE_DELTA` | `0.08` | Score increase per retrieval hit |
| `VITALITY_DECAY_HALF_LIFE_DAYS` | `30` | Decay half-life (days); vitality halves after 30 days |
| `VITALITY_DECAY_MIN_SCORE` | `0.05` | Decay floor; will not drop below this value |
| `VITALITY_CLEANUP_THRESHOLD` | `0.35` | Memories below this value are listed as cleanup candidates |
| `VITALITY_CLEANUP_INACTIVE_DAYS` | `14` | Inactivity threshold, used with vitality score to determine candidates |
| `RUNTIME_VITALITY_DECAY_CHECK_INTERVAL_SECONDS` | `600` | Decay check interval (seconds); default 10 minutes |
| `RUNTIME_CLEANUP_REVIEW_TTL_SECONDS` | `900` | Cleanup confirmation window (seconds); default 15 minutes |
| `RUNTIME_CLEANUP_REVIEW_MAX_PENDING` | `64` | Maximum pending cleanup confirmations |

**Tuning Suggestions**:

1.  Keep default values first and observe for 1-2 weeks before adjusting.
2.  If there are too many cleanup candidates → increase `VITALITY_CLEANUP_THRESHOLD` or `VITALITY_CLEANUP_INACTIVE_DAYS`.
3.  If the confirmation window is too short → increase `RUNTIME_CLEANUP_REVIEW_TTL_SECONDS`.

---

## 9. API Authentication

The following interfaces are protected by `MCP_API_KEY` (**fail-closed**: returns `401` if the key is not configured):

*   `GET/POST/DELETE /maintenance/*`
*   `GET/POST/PUT/DELETE /browse/*` and `GET/POST/DELETE /review/*`
*   SSE interfaces (`/sse` and `/messages`, started by `run_sse.py`)

### Header Format (Choose one)

```
X-MCP-API-Key: <YOUR_MCP_API_KEY>
Authorization: Bearer <YOUR_MCP_API_KEY>
```

### Local Debugging Override

Setting `MCP_API_KEY_ALLOW_INSECURE_LOCAL=true` allows skipping authentication during local debugging:

*   Only takes effect for loopback requests (`127.0.0.1` / `::1` / `localhost`).
*   Non-loopback requests still return `401` (with `reason=insecure_local_override_requires_loopback`).

> **MCP stdio mode** does not go through the HTTP/SSE authentication middleware and is therefore not subject to this restriction.

### Frontend Access to Protected Interfaces

When **manually starting frontend and backend locally**, if you are just debugging locally, you can inject the API Key at runtime (not recommended in build variables):

```html
<script>
  window.__MEMORY_PALACE_RUNTIME__ = {
    maintenanceApiKey: "<MCP_API_KEY>",
    maintenanceApiKeyMode: "header"   // or "bearer"
  };
</script>
```

> Do not put real `MCP_API_KEY` in public pages, shared static resources, or HTML files delivered to end users. The browser can read this global object directly. For deployments intended for others, a server-side proxy is recommended over exposing the key in frontend pages.
>
> Also compatible with the old field name: `window.__MCP_RUNTIME_CONFIG__`

When using **Docker one-click deployment**, you don't need to write the key into the browser page:

*   The frontend container automatically includes the same `MCP_API_KEY` for `/api/*`, `/sse`, and `/messages` at the proxy layer.
*   This key is saved in the Docker env file used for this run by default.
*   The browser only sees the proxied results and does not directly receive the real key.
*   Treat that frontend port as a trusted admin/operator surface. If you expose `3000` beyond a trusted network, add your own VPN, reverse-proxy auth, or network ACL in front of it.

### SSE Startup Example

```bash
HOST=127.0.0.1 PORT=8010 python run_sse.py
```

> `HOST=127.0.0.1` here is a local loopback debugging example. To allow other machines to access it, change it to `0.0.0.0` (or your actual listening address) and supplement with your own `MCP_API_KEY`, network isolation, reverse proxy, and TLS protection.

For Docker one-click deployment, access directly at:

```bash
http://localhost:3000/sse
```

---

## 10. Tuning and Troubleshooting

### Common Issues

| Issue | Cause and Solution |
|---|---|
| Poor retrieval results | Confirm if `SEARCH_DEFAULT_MODE` is `hybrid`; for C/D profiles, check if `RETRIEVAL_RERANKER_WEIGHT` is reasonable |
| Model service unavailable | The system will downgrade automatically; check the `degrade_reasons` field in the response to locate the specific cause |
| C/D shows `embedding_request_failed` / `embedding_fallback_hash` | Usually indicates the external embedding/reranker link is unreachable (e.g., local router model not deployed), not a backend crash; see "C/D Downgrade Signal Troubleshooting" below |
| Docker port conflict | One-click script automatically finds idle ports; can also be manually specified (bash: `--frontend-port` / `--backend-port`, PowerShell: `-FrontendPort` / `-BackendPort`) |
| SSE startup fail `address already in use` | Free the port or switch via `PORT=<idle-port>` |
| Database lost after upgrade | The backend automatically restores from historical filenames (`agent_memory.db` / `nocturne_memory.db` / `nocturne.db`) on startup |

### C/D Downgrade Signal Troubleshooting (Local Debugging)

```bash
# First check if the service is actually up
curl -fsS http://127.0.0.1:18000/health
```

1.  If the log or results still contain `embedding_request_failed` / `embedding_fallback_hash`, first check if the embedding/reranker service itself is reachable and if the API key is valid.
2.  Checking the actual calling endpoints directly is more reliable than just looking at the config file:

```bash
curl -fsS -X POST <RETRIEVAL_EMBEDDING_API_BASE>/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"<RETRIEVAL_EMBEDDING_MODEL>","input":"ping"}'
curl -fsS -X POST <RETRIEVAL_RERANKER_API_BASE>/rerank \
  -H "Content-Type: application/json" \
  -d '{"model":"<RETRIEVAL_RERANKER_MODEL>","query":"ping","documents":["pong"]}'
```

3.  If troubleshooting only on the current machine, you can temporarily change to `RETRIEVAL_EMBEDDING_BACKEND=api` and directly configure embedding / reranker / llm; restore the target environment's `router` config and re-verify once before going live.

### PowerShell / Windows Verification Suggestions

*   Both `scripts/apply_profile.sh` and `scripts/apply_profile.ps1` perform unified deduplication of duplicate env keys.
*   If delivering to a Windows environment, it is recommended to run the startup and smoke tests on the target Windows machine using the same template.
*   The main documentation only keeps steps that are publicly executable; target environment-specific verification suggestions should be recorded separately.

### Tuning Tips

1.  **`RETRIEVAL_RERANKER_WEIGHT`**: Too high an emphasis will over-rely on the reranking model; suggested debugging step is `0.05`.
2.  **Docker Data Persistence**: By default, `memory_palace_data` (mounted at `/app/data`) and `memory_palace_snapshots` (mounted at `/app/snapshots`) are used simultaneously to persist the database and review snapshots respectively (see `docker-compose.yml`).
3.  **Legacy Compatibility**: The one-click script automatically identifies legacy `NOCTURNE_*` environment variables and historical data volumes.
4.  **Migration Lock**: `DB_MIGRATION_LOCK_FILE` (default `<db_file>.migrate.lock`) and `DB_MIGRATION_LOCK_TIMEOUT_SEC` (default `10` seconds) are used to prevent concurrent migration conflicts across multiple processes.

---

## 11. Assistant Scripts Overview

| Script | Description |
|---|---|
| `scripts/apply_profile.sh` | Generates `.env` from template (macOS / Linux) |
| `scripts/apply_profile.ps1` | Generates `.env` from template (Windows PowerShell) |
| `scripts/docker_one_click.sh` | Docker one-click deployment (macOS / Linux) |
| `scripts/docker_one_click.ps1` | Docker one-click deployment (Windows PowerShell) |

### Configuration Template File Structure

```
deploy/profiles/
├── macos/
│   ├── profile-a.env
│   ├── profile-b.env
│   ├── profile-c.env
│   └── profile-d.env
├── windows/
│   ├── profile-a.env
│   ├── profile-b.env
│   ├── profile-c.env
│   └── profile-d.env
└── docker/
    ├── profile-a.env
    ├── profile-b.env
    ├── profile-c.env
    └── profile-d.env
```
