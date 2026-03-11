# Memory Palace Skills Docs

本目录描述 Memory Palace 的 skills / MCP 编排方案。

如果你是第一次看这里，建议按这个顺序读：

1. **先看最短路径**
   - `SKILLS_QUICKSTART.md`
2. **需要分步骤接通或排障时再看**
   - `GETTING_STARTED.md`
3. **再看完整设计**
   - `MEMORY_PALACE_SKILLS.md`
4. **如果你接的是 IDE 宿主**
   - `IDE_HOSTS.md`

---

## 这些文件分别干什么

- 如果你只想先知道“我现在该执行哪条命令”，优先看 `SKILLS_QUICKSTART.md`
- 如果你已经开始接入，但想按步骤检查“skill 到底有没有被发现、MCP 到底有没有绑到当前仓库”，再看 `GETTING_STARTED.md`
- `GETTING_STARTED.md`
  - 面向第一次接通的人
  - 重点回答“按步骤怎么接、每一步怎么检查有没有接好”
- `SKILLS_QUICKSTART.md`
  - 面向想先走最短路径的人
  - 重点回答“先执行什么、哪些客户端现在怎么接、哪些边界需要先记住”
- `MEMORY_PALACE_SKILLS.md`
  - 面向想看完整设计的人
  - 重点讲 canonical bundle、variants 和工作流边界
- `CLI_COMPATIBILITY_GUIDE.md`
  - 面向多 CLI 接入场景
  - 重点看 Claude / Gemini / Codex / OpenCode 的差异
- `IDE_HOSTS.md`
  - 面向 Cursor / Windsurf / VSCode-host / Antigravity 这类 IDE 宿主
  - 重点看 `AGENTS.md + MCP snippet` 这条投影路径，而不是 hidden skill mirrors

---

## 本地验证报告

- `TRIGGER_SMOKE_REPORT.md`
  - 运行 `python scripts/evaluate_memory_palace_skill.py` 后生成
- `MCP_LIVE_E2E_REPORT.md`
  - 运行 `cd backend && python ../scripts/evaluate_memory_palace_mcp_e2e.py` 后生成

它们主要用来帮你复核当前环境的接通结果，不是主入口文档。
刚 clone 下来的 GitHub 仓库里如果暂时看不到这两份文件，属于正常现象；先运行上面的命令再看即可。
如果你准备把它们转发给别人，先自己看一遍内容；这类本地报告可能会带上你机器上的路径或客户端配置痕迹。

---

## canonical bundle 在哪里

真正的 canonical bundle 在这里：

- `docs/skills/memory-palace/`

这里面放的是：

- `SKILL.md`
- `references/`
- `variants/`
- `agents/openai.yaml`

一句话理解：

> 公开文档负责告诉用户怎么用，canonical bundle 负责定义这套 skill 到底是什么。

补一条当前仓库口径：

> `Memory-Palace/AGENTS.md` 现在也作为 repo-local 规则入口随仓提供，便于 Antigravity 等已支持 `AGENTS.md` 的客户端直接读取本仓约束；旧环境仍可继续兼容 `GEMINI.md` 路径约定。

再补一条新的统一口径：

> `Cursor / Windsurf / VSCode-host / Antigravity` 这类 IDE hosts 不再以 hidden skill mirrors 作为默认接入方式。它们应通过 `AGENTS.md + python scripts/render_ide_host_config.py ...` 这条投影路径接入本仓的 skills + MCP 能力。
