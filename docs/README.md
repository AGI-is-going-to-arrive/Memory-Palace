# Memory Palace 文档中心

> **Memory Palace** 是一套为 AI 编程助手设计的长期记忆系统，通过 MCP（Model Context Protocol）为 Codex / Claude Code / Gemini CLI / Cursor 等客户端提供统一的记忆读写、检索、审查与维护能力。
>
> 当前版本：**v1.0.1** · 许可证：MIT
>
> 对外文档只保留**用户真正要用到**的说明；阶段性实验草稿、本机验证日志和一次性排障记录默认只保存在本地，不作为公开入口。

![系统架构图](images/系统架构图.png)

---

## 📖 新手入口

| 文档 | 说明 |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | 5 分钟跑通本地开发 + Docker，附 MCP 客户端配置示例 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 启动失败、端口冲突、鉴权失败、搜索降级等常见问题排查 |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | API Key 安全配置、隐私保护、发布前检查 |

## 🔧 技术文档

| 文档 | 说明 |
|---|---|
| [TECHNICAL_OVERVIEW.md](TECHNICAL_OVERVIEW.md) | 后端、前端、MCP、Docker 的实现结构与技术栈一览 |
| [TOOLS.md](TOOLS.md) | 9 个 MCP 工具的输入输出、返回约定与降级语义 |
| [DEPLOYMENT_PROFILES.md](DEPLOYMENT_PROFILES.md) | A/B/C/D 四档位配置模板、参数调优与部署方式 |
| [skills/SKILLS_QUICKSTART.md](skills/SKILLS_QUICKSTART.md) | 一页看懂 skills 怎么触发、怎么配 MCP、怎么验收 |
| [skills/MEMORY_PALACE_SKILLS.md](skills/MEMORY_PALACE_SKILLS.md) | canonical `memory-palace` skill 设计、安装与多 CLI 编排策略 |
| [skills/CLAUDE_SKILLS_AUDIT.md](skills/CLAUDE_SKILLS_AUDIT.md) | 对照 Claude 官方 skills / skill-creator 规范的现状审计 |
| [skills/CLI_COMPATIBILITY_GUIDE.md](skills/CLI_COMPATIBILITY_GUIDE.md) | 各 CLI 的推荐安装路径、smoke 结果与已知边界 |
| [skills/TRIGGER_SMOKE_REPORT.md](skills/TRIGGER_SMOKE_REPORT.md) | `memory-palace` skill 的脱敏 smoke 结果与当前边界 |
| [skills/MCP_LIVE_E2E_REPORT.md](skills/MCP_LIVE_E2E_REPORT.md) | 真实 MCP stdio 端到端回归结果（create/update/alias/compact/rebuild） |

## 📊 测评与质量

| 文档 | 说明 |
|---|---|
| [EVALUATION.md](EVALUATION.md) | 基准测试方法、A/B/C/D 关键指标对比、复现命令 |

## 🚀 发布

| 文档 | 说明 |
|---|---|
| [changelog/release_summary_vs_old_project_2026-03-06.md](changelog/release_summary_vs_old_project_2026-03-06.md) | 面向发布的旧项目 vs 当前版本对比摘要（基于真实代码、真实测试与现有文档） |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | 开源发布前的安全检查、敏感信息排查、公开/本地文档边界 |
| [GETTING_STARTED.md](GETTING_STARTED.md) | 发布前本地开发与 Docker 最小自测流程 |
