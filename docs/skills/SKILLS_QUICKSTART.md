# Memory Palace Skills 快速上手

> 这份文档专门写给“先跑通、先用起来”的人。
>
> 不讲一大堆抽象概念，就回答三件事：**这套 skills 到底是什么、当前仓库怎么直接用、四个客户端分别怎么配。**

---

## 🚀 先说结论

当前这个仓库已经把 `memory-palace` 的 **canonical skill**、同步脚本和安装脚本整理好了。按下面命令执行后，你可以在**自己的本地工作区**把 skill + MCP 主链路接起来：

| 客户端 | skill 自动识别 | MCP 连接现状 | 你该怎么做 |
|---|---|---|---|
| `Claude Code` | 执行 workspace 安装后即可 | workspace 安装后会生成项目级入口；非交互模式路径已有验证 | 先执行本文命令，再在本仓库打开 |
| `Gemini CLI` | 执行 workspace 安装后即可 | workspace 安装后可生成 `.gemini/settings.json`，但 `live MCP` 仍有个别场景待补验 | 先执行本文命令；需要更稳时再补一次 user-scope 安装 |
| `Codex CLI` | sync 后有 repo-local skill | 最近验证环境里已修正为真实 backend 启动命令 | 若是首次在你的机器上使用，先执行 1 条 `codex mcp add` |
| `OpenCode` | sync 后有 repo-local skill | 最近验证环境里 live smoke 已通过；新机器通常要先确认本地 MCP 注册 | 先看 `opencode mcp list`，没有再补注册 |

一句话理解：

- **skill** 负责“什么时候该进入 Memory Palace 工作流”
- **MCP** 负责“真正去调用 `read_memory / search_memory / update_memory` 这些工具”
- 两个都到位，才叫“真的能自动触发并且真的能干活”

> 当前公开口径：
>
> - `Claude / Codex / OpenCode / Gemini` 的 **smoke** 已有结果
> - `Gemini live` 还没到可以写成“完全通过”的程度
> - `Cursor / agent / Antigravity` 目前仍是 **PARTIAL**
>
> **Windows 用户先看这个前提**：
>
> - 当前仓库里的 repo-local MCP wrapper 是 `scripts/run_memory_palace_mcp_stdio.sh`
> - `install_skill.py` 生成的本地 MCP 启动命令也统一走 `bash` / `/bin/zsh`
> - 所以如果你是原生 Windows 环境，请先准备 **Git Bash** 或 **WSL**
> - 如果你现在只有 PowerShell，先不要把下面这些 `/bin/zsh` / `bash` 示例当成“原生 Windows 直接可用”
> - 这不是文档偷懒，而是当前代码的真实边界

---

## 🧠 skill 和 MCP 到底啥关系

<p align="center">
  <img src="../images/skill_vs_mcp.png" width="800" alt="Skill vs MCP 工作原理" />
</p>

可以把它理解成：

- **skill** = 司机脑子里的“出车规则”
- **MCP** = 真正的车和方向盘

只有 skill，没有 MCP：

- 模型知道“这时候应该用 Memory Palace”
- 但真到要读写记忆时，没工具可调

只有 MCP，没有 skill：

- 工具明明存在
- 但模型不一定知道什么时候该用，容易漏触发、误触发

所以当前仓库做的事情，本质上就是把这两层一起补齐。

---

## ✅ 运行同步 / 安装后，本地通常会看到什么

公开仓库默认只带 canonical bundle。你执行上面的同步 / 安装命令后，本地工作区通常会看到这些关键入口：

| 文件 | 作用 |
|---|---|
| `docs/skills/memory-palace/` | canonical skill 真源（公开仓库默认存在） |
| `.claude/skills/memory-palace/SKILL.md` | Claude Code 的 repo-local skill 镜像（本地生成） |
| `.codex/skills/memory-palace/SKILL.md` | Codex 的 repo-local skill 镜像（本地生成） |
| `.opencode/skills/memory-palace/SKILL.md` | OpenCode 的 repo-local skill 镜像（本地生成） |
| `.gemini/skills/memory-palace/SKILL.md` | Gemini 的 repo-local skill 入口（本地生成） |
| `.gemini/settings.json` | Gemini 的项目级 MCP 配置（workspace 安装后生成） |
| `.mcp.json` | Claude Code 的项目级 MCP 配置（workspace 安装后生成） |

所以：

- `Claude Code`、`Gemini CLI` 在**当前仓库执行完 workspace 安装后**是最省心的路线
- `Codex CLI` 和 `OpenCode` 的 **skill** 已经就位
- 最近验证环境里的 `Codex` MCP 已经修正完成
- `OpenCode` 建议先手动确认一次 `mcp list`

---

## 🛠️ 四个客户端怎么配

## 1) `Claude Code`

最省心。

先执行上面的 workspace 安装后，本地工作区里会有：

- `.claude/skills/memory-palace/`
- `.mcp.json`

你只要在这个仓库里启动 `Claude Code`，它就同时看得到：

1. `memory-palace` skill
2. `memory-palace` MCP server

推荐检查：

```bash
claude mcp list
```

如果你看到项目里有 `memory-palace`，基本就对了。

这次真实验证里，`Claude Code` 已经能在非交互模式下直接创建测试记忆，不再卡在权限确认。

---

## 2) `Gemini CLI`

先执行上面的 workspace 安装后，本地工作区里会补齐：

- `.gemini/skills/memory-palace/SKILL.md`
- `.gemini/settings.json`

所以在**当前工作区本地**里，Gemini 可以直接走项目级入口。

推荐检查：

```bash
gemini skills list --all
gemini mcp list
```

如果你想把这套能力带到**别的仓库**复用，再执行：

```bash
python scripts/install_skill.py --targets gemini --scope user --with-mcp --force
```

这一步属于“跨仓复用”，不是“当前仓库最小可用”的必需步骤。

如果你看到这种提示：

- `Skill conflict detected`
- `... overriding the same skill from ~/.gemini/skills/...`

这通常不是坏事，表示**当前工作区里的 skill 正在覆盖用户目录里的旧版本**。

如果你看到这种提示：

- `gemini mcp list` 里 `memory-palace` 是 `Disconnected`
- 或 Gemini 回答里出现 `MCP issues detected`

先把旧的用户级 MCP 条目删掉，再重新加项目级这一条：

```bash
gemini mcp remove memory-palace
gemini mcp add -s project -e DATABASE_URL=sqlite+aiosqlite:////<repo-root>/backend/memory.db memory-palace /bin/zsh -lc 'cd <repo-root>/backend && source .venv/bin/activate && RETRIEVAL_REMOTE_TIMEOUT_SEC=1 python mcp_server.py'
```

> 把上面的 `<repo-root>` 替换成你的实际仓库根目录。

---

## 3) `Codex CLI`

`Codex` 这边要分开看：

- **skill**：执行 `sync/install` 后，本地会有 `.codex/skills/memory-palace/`
- **MCP**：`Codex` 目前主要走用户目录 `~/.codex/config.toml`

说人话就是：

- 在这个仓库里，`Codex` 已经知道有 `memory-palace` 这套 skill
- 但你第一次在自己的机器上用时，还要告诉它“Memory Palace MCP 服务器怎么启动”

第一次执行一次：

```bash
codex mcp add memory-palace \
  --env DATABASE_URL=sqlite+aiosqlite:////ABS/PATH/TO/REPO/backend/memory.db \
  -- /bin/zsh -lc 'cd /ABS/PATH/TO/REPO/backend && source .venv/bin/activate && RETRIEVAL_REMOTE_TIMEOUT_SEC=1 python mcp_server.py'
```

然后检查：

```bash
codex mcp list
```

注意：

- 把上面的 `/ABS/PATH/TO/REPO` 换成你的真实仓库路径
- 这条配置会写到 `~/.codex/config.toml`
- 这是 `Codex CLI` 当前的产品行为，不是本仓库少了文件
- 如果你把这条命令改写成别的 shell / 客户端配置，别把 `source .venv/bin/activate` 随手删掉；要么先激活项目自己的 `.venv`，要么直接改成 `.venv` 里的 Python。否则 MCP 进程可能会在启动前就因为解释器不对而报 `No module named 'sqlalchemy'`

---

## 4) `OpenCode`

`OpenCode` 这边在你执行 `sync/install` 后，本地通常会有：

- `.opencode/skills/memory-palace/`

并且最近验证环境里的 smoke 已经通过，所以这条接法是可信的。

但如果你换一台新机器，更稳妥的顺序是：

```bash
opencode mcp list
```

如果已经能看到 `memory-palace`，那就不用折腾。

如果看不到，就在 `OpenCode` 自己的 MCP 管理入口里新增一个本地 stdio server，核心参数就是：

```text
name: memory-palace
type: local / stdio
command: /bin/zsh
args:
  - -lc
  - cd <repo-root>/backend && source .venv/bin/activate && DATABASE_URL=sqlite+aiosqlite:///$PWD/memory.db RETRIEVAL_REMOTE_TIMEOUT_SEC=1 python mcp_server.py
```

不同版本的 `OpenCode` 交互入口可能长得不一样，但要填的本质就是这几项。

---

## 🔍 怎么判断“真的触发成功了”

最简单的正向问题：

```text
先从 system://boot 读一下，再帮我查最近关于部署偏好的记忆。
```

如果命中了 `memory-palace`，回答或执行里通常会体现这些信号：

- 先走 `read_memory("system://boot")`
- 不会直接瞎写
- 会提到 `search_memory(..., include_session=true)` 或等价 recall 流程

最简单的反向问题：

```text
给我重写 README 的开头介绍。
```

这类纯文档任务**不应该**误触发 Memory Palace 工作流。

---

## 🧪 仓库里已经有的验证命令

先看 skill 镜像有没有漂移：

```bash
python scripts/sync_memory_palace_skill.py --check
```

再看四端 smoke：

```bash
python scripts/evaluate_memory_palace_skill.py
```

再看真实 MCP 调用链：

```bash
cd backend && python ../scripts/evaluate_memory_palace_mcp_e2e.py
```

这两条脚本都会在本地生成验证报告：

- `docs/skills/TRIGGER_SMOKE_REPORT.md`（脱敏后的 smoke 摘要）
- `docs/skills/MCP_LIVE_E2E_REPORT.md`

默认建议把它们当成你自己机器上的复核产物，不把它们当成主入口文档；这两份文件默认也被 `.gitignore` 排除，所以公开 GitHub 仓库里通常不会带上。

---

## 🙋 常见误区

### 误区 1：看到 skill 文件就等于能用了

不是。

skill 只解决“该不该触发”。
真正要调工具，还得有 MCP server 配置。

### 误区 2：Gemini 发现了 skill，就一定能稳定触发

也不是。

Gemini 对隐藏目录有时更保守，所以这套安装链才会在你本地同时补：

- `.gemini/skills/...`
- `.gemini/settings.json`
- `variants/gemini/SKILL.md`

### 误区 3：本地已经有 `.codex/skills/...`，就不用配 MCP

还是不够。

`Codex` 的 MCP 目前主要看用户级配置 `~/.codex/config.toml`。

---

## 📚 继续往下看什么

如果你已经能跑起来，下一步按这个顺序读：

1. `MEMORY_PALACE_SKILLS.md` —— 设计原则、Claude 规范对齐、维护边界
2. `CLI_COMPATIBILITY_GUIDE.md` —— 四端兼容口径和手工检查清单
3. `docs/skills/memory-palace/SKILL.md` —— 真正给模型看的 skill 本体

如果你只想先验证现在是不是通的，就盯住这 3 条命令：

```bash
python scripts/sync_memory_palace_skill.py --check
python scripts/evaluate_memory_palace_skill.py
cd backend && python ../scripts/evaluate_memory_palace_mcp_e2e.py
```
