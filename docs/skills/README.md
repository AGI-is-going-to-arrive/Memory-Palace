# Memory Palace Skills Docs

本目录描述 Memory Palace 的 skills / MCP 编排方案。

如果你是第一次看这里，建议按这个顺序读：

1. **先跑通**
   - `GETTING_STARTED.md`
2. **快速理解当前仓库怎么接**
   - `SKILLS_QUICKSTART.md`
3. **再看完整设计**
   - `MEMORY_PALACE_SKILLS.md`

---

## 这些文件分别干什么

- `GETTING_STARTED.md`
  - 面向第一次接通的人
  - 重点回答“先做什么、怎么检查有没有接好”
- `SKILLS_QUICKSTART.md`
  - 面向想快速搞懂 skill + MCP 关系的人
  - 重点回答“哪些客户端现在能怎么用、哪些地方还有边界”
- `MEMORY_PALACE_SKILLS.md`
  - 面向维护者和深度使用者
  - 重点讲 canonical bundle、variants、设计原则和维护边界
- `CLI_COMPATIBILITY_GUIDE.md`
  - 面向多 CLI 接入场景
  - 重点看 Claude / Gemini / Codex / OpenCode 的差异

---

## 本地验证产物怎么理解

- `TRIGGER_SMOKE_REPORT.md`
  - 运行 `python scripts/evaluate_memory_palace_skill.py` 后在本地生成
- `MCP_LIVE_E2E_REPORT.md`
  - 运行 `cd backend && python ../scripts/evaluate_memory_palace_mcp_e2e.py` 后在本地生成

它们的定位是：

- **报告当前机器上的验证结果**
- **帮助你本地复核**
- **默认不当成主说明入口文档**

---

## canonical bundle 在哪里

真正的 canonical bundle 在这里：

- `memory-palace/`

这里面放的是：

- `SKILL.md`
- `references/`
- `variants/`
- `agents/openai.yaml`

一句话理解：

> 公开文档负责告诉用户怎么用，canonical bundle 负责定义这套 skill 到底是什么。
