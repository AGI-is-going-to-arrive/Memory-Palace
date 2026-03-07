# Memory Palace Skills

`Memory-Palace/docs/skills/` 现在既负责 `memory-palace` skill 的**设计说明与验证规则**，也承载仓库内的 canonical skill bundle。

## 目录

```text
docs/skills/
├── README.md
├── MEMORY_PALACE_SKILLS.md
└── memory-palace/
    ├── agents/
    │   └── openai.yaml
    ├── SKILL.md
    ├── references/
    │   ├── mcp-workflow.md
    │   └── trigger-samples.md
    └── variants/
        └── gemini/
            └── SKILL.md
```

## 同步到各 CLI

在仓库根目录执行：

```bash
python Memory-Palace/scripts/sync_memory_palace_skill.py
```

只检查是否漂移：

```bash
python Memory-Palace/scripts/sync_memory_palace_skill.py --check
```

## 安装到其他工作区或用户目录

如果你不是在当前仓库里直接使用，而是要把 canonical skill 安装到别的工作区或用户目录，执行：

```bash
python Memory-Palace/scripts/install_skill.py --targets claude,codex,opencode,cursor,agent --scope workspace --force
```

安装到用户目录：

```bash
python Memory-Palace/scripts/install_skill.py --targets claude,codex,gemini,opencode,cursor,agent --scope user --force
```

如果要安装 `Antigravity` workflow：

```bash
python Memory-Palace/scripts/install_skill.py --targets antigravity --scope user --force
```

对于 `Gemini CLI`，当前更推荐 **user scope** 安装，而不是优先依赖仓库内 `.gemini/skills/...`：

- `gemini skills list --all` 能发现 workspace-local skill
- 但实际触发时，Gemini 可能尝试读取隐藏目录 `.gemini/skills/...`
- 该路径在某些本地策略下会被 ignore patterns 拦截，导致 skill 已发现但执行不稳定

因此，Gemini 的稳妥安装方式优先是：

```bash
python Memory-Palace/scripts/install_skill.py --targets gemini --scope user --force
```

安装脚本会自动为 Gemini 使用 `variants/gemini/SKILL.md` 这一份更强触发、更加直接的变体。

## 触发 smoke 评估

在仓库根目录执行：

```bash
python Memory-Palace/scripts/evaluate_memory_palace_skill.py
```

会生成：

```text
Memory-Palace/docs/skills/TRIGGER_SMOKE_REPORT.md
```

## Live MCP 端到端回归

在仓库根目录执行：

```bash
Memory-Palace/backend/.venv/bin/python Memory-Palace/scripts/evaluate_memory_palace_mcp_e2e.py
```

会生成：

```text
Memory-Palace/docs/skills/MCP_LIVE_E2E_REPORT.md
```

Gemini 兼容提示：

```bash
gemini -m gemini-3-flash-preview \
  -p 'In this repository, answer in exactly 3 bullets only: (1) the first memory tool call required by the memory-palace skill, (2) what to do when guard_action is NOOP, and (3) the canonical repo-visible path of the trigger sample set.' \
  --output-format text \
  --allowed-tools activate_skill,read_file
```

## 目标目录

同步脚本会把 canonical bundle 分发到：

- `.claude/skills/memory-palace/`
- `.codex/skills/memory-palace/`
- `.opencode/skills/memory-palace/`
- `.cursor/skills/memory-palace/`
- `.agent/skills/memory-palace/`（兼容旧目录）

## 维护入口

- 设计说明：`MEMORY_PALACE_SKILLS.md`
- 技能本体：`memory-palace/SKILL.md`
- 工具工作流参考：`memory-palace/references/mcp-workflow.md`
- 触发样例集：`memory-palace/references/trigger-samples.md`
- CLI 兼容指南：`CLI_COMPATIBILITY_GUIDE.md`
- Antigravity workflow 变体：`memory-palace/variants/antigravity/global_workflows/memory-palace.md`
- 安装脚本：`../../scripts/install_skill.py`
- 自动评估脚本：`../../scripts/evaluate_memory_palace_skill.py`
- Live MCP e2e 脚本：`../../scripts/evaluate_memory_palace_mcp_e2e.py`
- smoke 报告：`TRIGGER_SMOKE_REPORT.md`
- Live MCP 报告：`MCP_LIVE_E2E_REPORT.md`

补充说明：

- `.agent/skills/` 当前是**兼容目录名**，不是这台机器上已确认存在的某个独立产品
- `Antigravity IDE` 当前已确认存在 app-bundled CLI，且可安装到 `~/.gemini/antigravity/global_workflows/`；但真实 GUI 行为仍需手工 smoke
- CLI 兼容说明：`CLI_COMPATIBILITY_GUIDE.md`
