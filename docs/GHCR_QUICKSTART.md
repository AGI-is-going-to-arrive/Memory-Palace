# GHCR 预构建镜像快速使用

> 如果你本地构建总是失败，先按这份做。
>
> 目标只有一个：**先把服务跑起来**。

---

## 1. 适合谁

这条路径最适合：

- Docker 能跑，但本地镜像 build 总是失败
- 只想先把 Dashboard / API / SSE 跑起来
- 不想先排 Node / Python / Dockerfile / buildx 环境问题

如果你只是想“先能用起来”，优先走这条。

---

## 2. 最短命令

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

> 这里默认走的是 **Profile B**。

---

## 3. 启动后看哪里

默认地址：

- Dashboard：`http://localhost:3000`
- Backend API：`http://localhost:18000`
- SSE：`http://localhost:3000/sse`

先检查后端健康状态：

```bash
curl -fsS http://127.0.0.1:18000/health
```

如果你想再确认一下 Docker 下的首启向导是不是正常处于“说明模式”：

```bash
curl -fsS http://127.0.0.1:3000/api/setup/status
```

正常结果里应该能看到：

- `"running_in_docker": true`
- `"apply_supported": false`

这表示它不会假装自己能把容器 `.env` 真正持久化。

---

## 4. 最容易误会的点

### 4.1 这条路径解决什么

它解决的是：

- Dashboard
- Backend API
- SSE

### 4.2 这条路径不自动解决什么

它**不会**自动帮你配置本机上的：

- `Claude / Codex / Gemini / OpenCode`
- `Cursor / Windsurf / VSCode-host / Antigravity`
- skill / MCP / IDE host 配置

也就是说：

- Docker 负责跑服务
- 客户端接入还是宿主机侧配置
- 如果你手工把客户端接到 `http://localhost:3000/sse`，`<YOUR_MCP_API_KEY>` 默认就填刚生成的 `.env.docker` 里的 `MCP_API_KEY`
- `scripts/run_memory_palace_mcp_stdio.sh` 不是这条 Docker 路径的客户端入口：它依赖本地 `bash` 和 `backend/.venv`，不会复用容器里的 `/app/data`；若仓库里只有 `.env.docker` 而没有本地 `.env`，它会明确拒绝回退到 `demo.db`，并提示改走 Docker 的 `/sse`

如果你还想把客户端接进当前仓库，再继续看：

- `docs/skills/README.md`
- `docs/skills/GETTING_STARTED.md`

---

## 5. 端口冲突怎么办

这条 GHCR compose 路径**不会自动换端口**。

如果你本机的 `3000` 或 `18000` 已被占用，请在启动前显式设置：

```bash
export MEMORY_PALACE_FRONTEND_PORT=3300
export MEMORY_PALACE_BACKEND_PORT=18080
docker compose -f docker-compose.ghcr.yml up -d
```

---

## 6. 如果容器要访问你宿主机上的模型服务

不要把地址写成：

```text
127.0.0.1
```

对 Docker 容器来说，`127.0.0.1` 指向的是**容器自己**，不是你的宿主机。

优先使用：

```text
host.docker.internal
```

或者你自己的实际可达宿主机地址。当前 compose 已显式补 `host.docker.internal:host-gateway`，Linux Docker 现在也可以沿这条路径访问宿主机服务。

---

## 7. 停止服务

```bash
docker compose -f docker-compose.ghcr.yml down --remove-orphans
```

如果你还想把数据库和 snapshots 一起清掉：

```bash
docker compose -f docker-compose.ghcr.yml down -v
```

---

## 8. 如果还不通

优先继续看：

- `docs/GETTING_STARTED.md`
- `docs/DEPLOYMENT_PROFILES.md`
- `docs/TROUBLESHOOTING.md`
- `docs/GHCR_ACCEPTANCE_CHECKLIST.md`
