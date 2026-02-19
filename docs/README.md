# Memory Palace 文档中心

> **Memory Palace** 是一套为 AI 编程助手设计的长期记忆系统，通过 MCP（Model Context Protocol）为 Codex / Claude Code / Gemini CLI / Cursor 等客户端提供统一的记忆读写、检索、审查与维护能力。
>
> 当前版本：**v1.0.1** · 许可证：MIT

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
| [skills/MEMORY_PALACE_SKILLS.md](skills/MEMORY_PALACE_SKILLS.md) | 多客户端统一的 Skills 编排策略（Codex / Claude Code / Gemini CLI / Cursor） |

## 📊 测评与质量

| 文档 | 说明 |
|---|---|
| [EVALUATION.md](EVALUATION.md) | 基准测试方法、A/B/C/D 关键指标对比、复现命令 |

## 🚀 发布

| 文档 | 说明 |
|---|---|
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | 开源发布前的安全检查、敏感信息排查与发布前清单 |
| [GETTING_STARTED.md](GETTING_STARTED.md) | 发布前本地开发与 Docker 最小自测流程 |
