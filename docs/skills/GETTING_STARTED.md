# Memory Palace Skills 快速上手

> 如果你只想把当前仓库这条 skill + MCP 链路接通，按这份做就够了。
>
> 如果你当前只是通过 GHCR / Docker 把 `Dashboard / API / SSE` 跑起来，但还没打算接本机客户端，这份不是第一入口。那种情况下，服务已经能用；只有当你还要让 `Claude / Codex / Gemini / OpenCode / IDE host` 真正接到当前仓库时，才继续按这里做。
>
> **先补一个边界说明**：当前仓库里的 repo-local MCP wrapper 是 `scripts/run_memory_palace_mcp_stdio.sh`，安装脚本生成的本地 MCP 启动命令也统一走 `bash` / `/bin/zsh`，并且默认依赖本地 `backend/.venv`。如果你是原生 Windows 环境，请先准备 **Git Bash** 或 **WSL**；不要把下面这些 shell 示例理解成“PowerShell 直接可用”。
>
> **再补一条很容易踩坑的边界**：这个 wrapper 只服务于“当前 checkout + 本地 `.env` + 本地 `backend/.venv`”这条 repo-local 路径，不会复用 Docker 容器里的 `/app/data`。如果仓库里只有 `.env.docker` 而没有本地 `.env`，它会明确拒绝回退到 `demo.db`，并提示你改走 Docker 暴露的 `/sse`。
>
> **再补一条范围说明**：这份主要写给 `Claude Code / Gemini CLI / Codex / OpenCode` 这类 CLI 客户端。`Cursor / Windsurf / VSCode-host / Antigravity` 这类 IDE 宿主，请直接看 `IDE_HOSTS.md`。

## 🎯 先记住一句话

`memory-palace` 分成两层：

- **skill 自动发现**
  - 负责“什么时候该进入 Memory Palace 工作流”
- **MCP 真正绑到当前仓库**
  - 负责“真正去调用这个仓库里的 Memory Palace backend”

所以“能用”必须同时满足：

1. skill 被发现
2. MCP 指向当前仓库

少任何一层，都不算真的配好。

再补一句 Docker 用户最容易误会的：

- `docker compose` 能把服务端跑起来
- 但不会自动把你机器上的 skill / MCP / IDE host 配置也一起改好
- 当前这份文档描述的是 **repo-local 安装路径**
- 如果你不走这条 repo-local 路径，而是只想让某个客户端手工接 Docker 里的 MCP，请把它视为另一条“手工远程 SSE MCP”路径

这条“手工远程 SSE MCP”路径当前已经有公开入口：

- `docs/GHCR_QUICKSTART.md`
- `docs/GETTING_STARTED.md` 里的 `6.2 SSE 模式`
- `docs/GETTING_STARTED.md` 里的 `6.3 客户端配置示例`

补一句最重要的边界：

- 当前仓库已经给出了**通用的 SSE MCP 配置骨架**
- 但不同客户端自己的字段名、GUI 入口、header 配置方式并不完全一样
- 所以如果你走这条手工路径，Memory Palace 文档负责告诉你：
  - 服务地址
  - 鉴权要求
  - 通用 JSON 骨架
- 客户端最终要填在哪个面板、字段名叫什么，仍以客户端自己的 MCP 文档为准

---

## 1. 单一真源在哪里

当前 canonical skill 在：

```text
docs/skills/memory-palace/
```

这里维护的是：

- `SKILL.md`
- `references/`
- `variants/`
- `agents/openai.yaml`

你运行同步 / 安装命令后，`.claude/.codex/.gemini/.opencode/...` 这些镜像目录才会出现在本地。刚 clone 下来、还没执行命令前看不到它们，属于正常现象。

如果你接的是 IDE 宿主，不要把 hidden mirrors 当成默认主路径。那条路径现在单独收到了：

- `IDE_HOSTS.md`
- `python scripts/render_ide_host_config.py --host ...`

---

## 2. 第一步：先同步 repo-local mirrors

```bash
python scripts/sync_memory_palace_skill.py
python scripts/sync_memory_palace_skill.py --check
```

这一步只解决 **skill 自动发现**，还没解决 MCP。

---

## 3. 第二步：打通当前仓库的 workspace 直连

`install_skill.py` 现在除了装 skill，还支持：

- `--with-mcp`
  - 同步安装 MCP 配置
- `--check`
  - 检查当前安装结果是否与 canonical 和当前仓库绑定一致

补一条很实用的细节：

- 如果脚本准备改写现有配置文件（例如 `.mcp.json`、`.gemini/settings.json`、`~/.gemini/policies/memory-palace-overrides.toml`、`~/.codex/config.toml`），现在会先在同目录生成一个 `*.bak` 备份
  - 例如：`.mcp.json.bak`、`settings.json.bak`、`memory-palace-overrides.toml.bak`、`config.toml.bak`
- 如果目标 JSON 本身已经坏掉，脚本会直接报出具体文件路径和行列号，不再吐一整屏 Python traceback

### 推荐命令

```bash
python scripts/install_skill.py \
  --targets claude,codex,gemini,opencode \
  --scope workspace \
  --with-mcp \
  --force
```

检查：

```bash
python scripts/install_skill.py \
  --targets claude,codex,gemini,opencode \
  --scope workspace \
  --with-mcp \
  --check
```

如果你看到的是：

- `workspace --check` 通过
- 但 `user --check` 失败

先别急着怀疑仓库本身坏了。更常见的情况是你自己 home 目录里还留着旧版 skill / MCP 配置。直接对同一组 target 重跑一次 `--scope user --with-mcp --force` 即可；脚本现在会先在原目录留 `*.bak`，再覆盖当前文件。

这一步会做两件事：

1. 继续保持 repo-local skill mirrors
2. 给当前仓库补两个直连入口：
   - `Claude Code` → `.mcp.json`
   - `Gemini CLI` → `.gemini/settings.json`
   - `Gemini CLI` → `.gemini/policies/memory-palace-overrides.toml`

这两个入口现在都会统一调用：

```text
scripts/run_memory_palace_mcp_stdio.sh
```

它的作用很简单：

- 用项目自己的 `backend/.venv`
- 优先复用当前仓库 `.env` 里的 `DATABASE_URL`
- 如果仓库里只有 `.env.docker` 而没有本地 `.env`，就停止并提示改走 Docker `/sse`

这样你在 Dashboard / HTTP API 里看到的库，和 MCP 客户端实际读写的库，默认就是同一份。

注意：

- `Codex/OpenCode` 在 workspace scope 下仍以 **repo-local skill 自动发现** 为主
- 它们的 MCP 仍建议走 **user-scope 注册**

---

## 4. 第三步：补上 Codex / OpenCode 的 user-scope MCP

如果你想让你自己的 `Codex/OpenCode` 明确连到这个仓库，执行：

```bash
python scripts/install_skill.py \
  --targets claude,codex,gemini,opencode \
  --scope user \
  --with-mcp \
  --force
```

检查：

```bash
python scripts/install_skill.py \
  --targets claude,codex,gemini,opencode \
  --scope user \
  --with-mcp \
  --check
```

这一步会把 MCP 注册到这些地方：

- `Claude Code` → `~/.claude.json` 当前仓库 project block
- `Codex CLI` → `~/.codex/config.toml`
- `Gemini CLI` → `~/.gemini/settings.json`
- `Gemini CLI` → `~/.gemini/policies/memory-palace-overrides.toml`
- `OpenCode` → `~/.config/opencode/opencode.json`

这些 user-scope MCP 配置本质上也还是调用同一个 wrapper，所以默认行为和 workspace 入口一致：

- 先读当前仓库 `.env`
- 再决定实际 `DATABASE_URL`
- 没有本地 `.env` 且仓库里只有 `.env.docker` 时，不会偷偷改用 `demo.db`

如果你想换到另一份数据库，优先改这个仓库自己的 `.env`，不要手工在不同客户端里各写一套不同的数据库路径。

---

## 5. 四个 CLI 客户端现在怎么理解

下面这些结论默认你已经跑完了上面的 `sync/install` 命令。

### Claude Code

- repo-local skill：有
- workspace MCP：有

结论：

- **打开当前仓库即可直接用**

### Gemini CLI

- repo-local skill：有
- workspace MCP：有
- policy 覆盖文件：有

结论：

- **workspace 入口已经就位**
- 如果你想更稳，或者准备跨仓复用，再补一次 `--scope user --with-mcp`
- 如果你看到 `Policy file warning in memory-palace-overrides.toml`，优先重跑一遍 `--scope user --with-mcp --force`
- 写给别人看时仍建议保守：smoke 已通过，但 `gemini_live` 还没有到“完全通过”的程度

### Codex CLI

- repo-local skill：有
- user-scope MCP：推荐

结论：

- **不要理解成天然开箱即用**
- 准确说法是：
  - skill 可 repo-local 自动发现
  - MCP 仍以 user-scope 注册为主

### OpenCode

- repo-local skill：有
- user-scope MCP：推荐

结论：

- **不要理解成天然开箱即用**
- 准确说法是：
  - skill 可 repo-local 自动发现
  - MCP 仍以 user-scope 注册为主

如果你接的是 `Cursor / Windsurf / VSCode-host / Antigravity`，这里先停下，不要继续套用 CLI 的 hidden mirror 逻辑。直接改看：

- `IDE_HOSTS.md`
- `python scripts/render_ide_host_config.py --host ...`

---

## 6. 最简单的验证方法

### 安装链检查

```bash
python scripts/install_skill.py \
  --targets claude,codex,gemini,opencode \
  --scope workspace \
  --with-mcp \
  --check

python scripts/install_skill.py \
  --targets claude,codex,gemini,opencode \
  --scope user \
  --with-mcp \
  --check
```

### 触发 smoke

```bash
python scripts/evaluate_memory_palace_skill.py
```

这条命令会在本地生成 smoke 摘要：

```text
docs/skills/TRIGGER_SMOKE_REPORT.md
```

如果你是刚 clone 下来的 GitHub 仓库，这个文件默认可能还不存在；先跑完命令再看，属于正常现象。它属于本地验证产物，分享前建议自己检查是否包含本机路径、客户端配置路径或其他环境痕迹。

这条检查会串行调用多种 CLI；如果你的机器上已经装了 `claude`、`codex`、`opencode`、`gemini`，完整跑完通常要几分钟，不建议看到几十秒无输出就直接当成“挂死”。
另外它默认还会尝试 `gemini_live`：如果当前 Gemini 配置能反推出真实数据库路径，会对那份库做一轮 `create/update/guard` 验证，并可能留下 `notes://gemini_suite_*` 这类测试记忆；只想做普通 smoke 时，可显式设置 `MEMORY_PALACE_SKIP_GEMINI_LIVE=1`。

### 真实 MCP 调用链

```bash
cd backend
python ../scripts/evaluate_memory_palace_mcp_e2e.py
```

这条命令会在本地生成真实 MCP e2e 摘要：

```text
docs/skills/MCP_LIVE_E2E_REPORT.md
```

同样地，这份报告默认也是“运行后才会出现”的本地产物；公开 GitHub 仓库里暂时没有，不代表接法有问题。
它默认使用隔离临时库，不会碰你的正式库；但失败时仍可能把 stderr、日志或临时目录路径写进报告。准备转发给别人前，先自己看一遍内容。

这两份报告主要用来复核当前环境的结果，不是主入口文档。

---

## 7. 正向 / 反向 prompt

正向 prompt：

```text
For this repository's memory-palace skill, answer with exactly three bullets:
(1) the first memory tool call,
(2) what to do when guard_action=NOOP,
(3) the path to the trigger sample file.
```

期望命中：

- `read_memory("system://boot")`
- `NOOP = stop + inspect guard_target_uri / guard_target_id`
- `docs/skills/memory-palace/references/trigger-samples.md`

反向 prompt：

```text
请帮我改一下 README 开头的文案，不需要碰 Memory Palace。
```

如果这条也硬触发 `memory-palace`，说明 `description` 太宽，或者客户端触发策略有问题。

---

## 8. 最容易踩的坑

### 只同步了 skill mirrors

现象：

- 看起来 skill 在
- 但 MCP 没绑到当前仓库

### 只补了 MCP，没补 skill

现象：

- 工具能用
- 但客户端不会自动进入 Memory Palace 工作流

### 把 Codex / OpenCode 误写成“天然开箱即用”

现象：

- repo-local skill 的确会被发现
- 但没有 user-scope MCP 时，仍可能不会连到当前仓库 backend

### 直接依赖隐藏路径

现象：

- skill 已加载
- 但读取 `.gemini/skills/...` 或 `.codex/skills/...` 时被本机策略拦掉

所以统一优先引用 repo-visible 路径：

```text
docs/skills/memory-palace/references/mcp-workflow.md
docs/skills/memory-palace/references/trigger-samples.md
```

---

## 9. 下一份该看什么

- 想看四端口径：`CLI_COMPATIBILITY_GUIDE.md`
- 想本地复核 skill 触发结果：先跑 `python scripts/evaluate_memory_palace_skill.py`
- 想本地复核真实 MCP e2e：先跑 `cd backend && python ../scripts/evaluate_memory_palace_mcp_e2e.py`
