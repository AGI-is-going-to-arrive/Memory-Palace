# GHCR Prebuilt Image Quick Start

> If local image build keeps failing, start here.
>
> The goal is simple: **get the service running first**.

---

## 1. Who This Is For

This path is best for users who:

- can run Docker locally, but local image build keeps failing
- only want Dashboard / API / SSE running first
- do not want to debug Node / Python / Dockerfile / buildx issues up front

If you just want it to work first, start here.

---

## 2. Shortest Commands

```bash
git clone https://github.com/AGI-is-going-to-arrive/Memory-Palace.git
cd Memory-Palace

cp .env.example .env.docker
bash scripts/apply_profile.sh docker b .env.docker

docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

```powershell
git clone https://github.com/AGI-is-going-to-arrive/Memory-Palace.git
cd Memory-Palace

Copy-Item .env.example .env.docker
.\scripts\apply_profile.ps1 -Platform docker -Profile b -Target .env.docker

docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

> This uses **Profile B** by default.

---

## 3. Where to Check After Startup

Default addresses:

- Dashboard: `http://localhost:3000`
- Backend API: `http://localhost:18000`
- SSE: `http://localhost:3000/sse`

Check backend health first:

```bash
curl -fsS http://127.0.0.1:18000/health
```

If you also want to confirm that the first-run assistant is correctly staying in Docker guidance mode:

```bash
curl -fsS http://127.0.0.1:3000/api/setup/status
```

A normal result should include:

- `"running_in_docker": true`
- `"apply_supported": false`

That means it is not pretending that container `.env` changes can be persisted.

---

## 4. The Most Common Misunderstanding

### 4.1 What This Path Solves

It solves:

- Dashboard
- Backend API
- SSE

### 4.2 What This Path Does Not Auto-configure

It does **not** automatically configure local:

- `Claude / Codex / Gemini / OpenCode`
- `Cursor / Windsurf / VSCode-host / Antigravity`
- skill / MCP / IDE host settings

In other words:

- Docker starts the service side
- client integration is still host-side configuration
- if you later connect a client manually to `http://localhost:3000/sse`, `<YOUR_MCP_API_KEY>` normally means the `MCP_API_KEY` in the `.env.docker` file you just generated
- if you only have Docker running, prefer `/sse`; do **not** assume `scripts/run_memory_palace_mcp_stdio.sh` will reuse container data, because that wrapper needs a local `.env` plus `backend/.venv` and refuses the `demo.db` fallback when `.env.docker` exists without `.env`

If you also want to wire clients into this repository, continue with:

- `docs/skills/README_EN.md`
- `docs/skills/GETTING_STARTED_EN.md`

---

## 5. What If the Ports Are Occupied

This GHCR compose path does **not** auto-adjust ports.

If `3000` or `18000` is already occupied on your machine, set them explicitly before startup:

```bash
export MEMORY_PALACE_FRONTEND_PORT=3300
export MEMORY_PALACE_BACKEND_PORT=18080
docker compose -f docker-compose.ghcr.yml up -d
```

```powershell
$env:MEMORY_PALACE_FRONTEND_PORT = "3300"
$env:MEMORY_PALACE_BACKEND_PORT = "18080"
docker compose -f docker-compose.ghcr.yml up -d
```

---

## 6. If the Container Must Reach a Model Service on Your Host

Do not use:

```text
127.0.0.1
```

From inside a Docker container, `127.0.0.1` points back to the **container itself**, not your host machine.

Prefer:

```text
host.docker.internal
```

or your actual reachable host address.

The compose files now add `host.docker.internal:host-gateway`, so this path also works on modern Linux Docker instead of only Docker Desktop.

---

## 7. Stop Services

```bash
docker compose -f docker-compose.ghcr.yml down --remove-orphans
```

If you also want to clear the database and snapshots:

```bash
docker compose -f docker-compose.ghcr.yml down -v
```

---

## 8. If It Still Fails

Continue with:

- `docs/GETTING_STARTED_EN.md`
- `docs/DEPLOYMENT_PROFILES_EN.md`
- `docs/TROUBLESHOOTING_EN.md`
- `docs/GHCR_ACCEPTANCE_CHECKLIST_EN.md`
