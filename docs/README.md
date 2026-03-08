# Memory Palace 文档中心

> **Memory Palace** 是一套为 AI 编程助手设计的长期记忆系统，通过 MCP（Model Context Protocol）为 Codex / Claude Code / Gemini CLI / OpenCode 提供统一的记忆读写、检索、审查与维护路径；`Cursor` 与 `Antigravity` 仍建议先看 `docs/skills/SKILLS_QUICKSTART.md` 里的边界说明。
>
> 许可证：MIT
>
> 这里优先放**用户真正要用到**的说明；阶段性实验草稿、本机验证日志和一次性排障记录不放在这一层主入口里。
>
> 如需在你自己的工作区复核当前 smoke / live e2e 状态，请运行 `python scripts/evaluate_memory_palace_skill.py` 或 `cd backend && python ../scripts/evaluate_memory_palace_mcp_e2e.py`。脚本会在 `<repo-root>/docs/skills/TRIGGER_SMOKE_REPORT.md` 与 `<repo-root>/docs/skills/MCP_LIVE_E2E_REPORT.md` 本地生成或更新摘要；它们默认被 `.gitignore` 排除，所以公开 GitHub 仓库里通常不会看到这些文件。这些结果也不再作为这一页的主入口。

![系统架构图](images/系统架构图.png)

---

## 📖 新手入口

| 文档 | 说明 |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | 5 分钟跑通本地开发 + Docker，附 MCP 客户端配置示例 |
| [skills/GETTING_STARTED.md](skills/GETTING_STARTED.md) | 第一次把 skill + MCP 真正接到当前仓库 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 启动失败、端口冲突、鉴权失败、搜索降级等常见问题排查 |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | API Key 安全配置、隐私保护、分享前自检 |

## 🔧 核心文档

| 文档 | 说明 |
|---|---|
| [TECHNICAL_OVERVIEW.md](TECHNICAL_OVERVIEW.md) | 后端、前端、MCP、Docker 的实现结构与技术栈一览 |
| [TOOLS.md](TOOLS.md) | 9 个 MCP 工具的输入输出、返回约定与降级语义 |
| [DEPLOYMENT_PROFILES.md](DEPLOYMENT_PROFILES.md) | A/B/C/D 四档位配置模板、参数调优与部署方式 |
| [skills/SKILLS_QUICKSTART.md](skills/SKILLS_QUICKSTART.md) | 一页看懂 skills 怎么触发、怎么配 MCP、怎么验收 |

## 🧩 Skills 与客户端

| 文档 | 说明 |
|---|---|
| [skills/MEMORY_PALACE_SKILLS.md](skills/MEMORY_PALACE_SKILLS.md) | canonical `memory-palace` skill 设计、安装与多 CLI 编排策略 |
| [skills/CLI_COMPATIBILITY_GUIDE.md](skills/CLI_COMPATIBILITY_GUIDE.md) | 各 CLI 的推荐安装路径、检查方式与已知边界 |

## 📊 测评与质量

| 文档 | 说明 |
|---|---|
| [EVALUATION.md](EVALUATION.md) | 公开基准方法、A/B/C/D 关键指标摘要与复现命令 |
