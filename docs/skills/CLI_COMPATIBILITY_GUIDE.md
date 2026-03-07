# Memory Palace CLI Compatibility Guide

## Summary

- `Claude Code`：通过
- `Codex CLI`：通过
- `OpenCode`：通过
- `Gemini CLI`：推荐路径通过
- `Cursor`：仅完成 mirror 分发与结构校验
- `agent` 兼容目录：仅完成 mirror 分发与结构校验
- `Antigravity IDE`：官方产品存在，app-bundled CLI 已发现，workflow 已安装，但仍需 GUI 内手工 smoke

## Recommended Client Strategy

### Claude Code

- 推荐：直接使用 repo-local mirror，或安装到用户目录
- 现状：smoke 通过

### Codex CLI

- 推荐：直接使用 repo-local mirror，或安装到用户目录
- 现状：smoke 通过

### OpenCode

- 推荐：直接使用 repo-local mirror，或安装到用户目录
- 现状：smoke 通过

### Gemini CLI

- 推荐：优先安装到用户目录

```bash
python Memory-Palace/scripts/install_skill.py --targets gemini --scope user --force
```

- 已知边界：
  - `gemini skills list --all` 能发现 `memory-palace`
  - `gemini-3-flash-preview` 在本机上通过 smoke
  - 默认零参数路径仍不建议直接作为唯一依据

### Cursor

- 推荐：使用 repo-local mirror
- 现状：当前机器存在 `cursor-agent` runtime，但当前缺少登录/鉴权

最终手工验证清单：

1. 先完成 `cursor-agent` 登录或配置 `CURSOR_API_KEY`
2. 在 Cursor 中打开当前仓库
3. 确认存在 `.cursor/skills/memory-palace/`
4. 发送正向 prompt：询问 first move、`guard_action=NOOP`、`trigger-samples.md` 路径
5. 期望答案：
   - `read_memory("system://boot")`
   - `NOOP = stop + inspect guard_target_uri / guard_target_id`
   - `Memory-Palace/docs/skills/memory-palace/references/trigger-samples.md`
6. 再发送反向 prompt：重写 README opening，不应进入 Memory Palace 工作流
7. 若正向命中且反向不过触发，可判定 Cursor 通过

### agent 兼容目录

- 推荐：使用 repo-local mirror
- 现状：当前机器未做独立 runtime smoke

`.agent` 对应什么：

- 当前仓库里，`.agent/skills/` 是**兼容目录名**
- 它不是这台机器上已确认存在的某个独立 IDE/CLI 产品
- 本机 `which agent` 为空，因此目前只能把它视为“agent-compatible runtime target”

最终手工验证清单：

1. 确认目标 runtime 真的读取 `.agent/skills/`
2. 发送与 Cursor 相同的正向/反向 prompt
3. 检查它是否使用 repo-local `memory-palace` 规则，而非泛化 memory 建议
4. 只有 runtime 存在且正反向都符合预期，才能把 `.agent` 从 `PARTIAL` 提升到 `PASS`

### Antigravity IDE

- 产品属性：Google 的 agent-first IDE / development platform（官方产品站已可访问）
- 当前机器状态：有 app-bundled CLI：`/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity`
- 当前限制：该 CLI 更像 GUI/IDE 启动入口，不是完整 headless skill runtime；但 workflow 已可安装到 `~/.gemini/antigravity/global_workflows/`
- 当前仓库口径：Antigravity 更接近 `Workspace Rules / Project Instructions` 路径，而不是 repo-local `.agent` mirror 路径

推荐安装路径：

```bash
python Memory-Palace/scripts/install_skill.py --targets antigravity --scope user --force
```

安装目标：

- user scope → `~/.gemini/antigravity/global_workflows/memory-palace.md`
- workspace scope → `.agent/workflows/memory-palace.md`

最终手工验证清单：

1. 在 Antigravity 中打开当前仓库
2. 通过其 workspace rules / project instructions 机制接入 Memory Palace 规则
3. 发送正向 prompt：询问 first move、`NOOP` 处理、trigger sample 路径
4. 发送反向 prompt：重写 README opening，不应触发 Memory Palace
5. 如果 Antigravity 支持项目级规则且正反向结果正确，可判定“Antigravity 可成功使用”
6. 如果缺少项目级规则入口，或只能得到泛化回答，则只能判定“理论兼容，未验证通过”

## Commands

### Repo-local sync

```bash
python Memory-Palace/scripts/sync_memory_palace_skill.py
python Memory-Palace/scripts/sync_memory_palace_skill.py --check
```

### User-scope install

```bash
python Memory-Palace/scripts/install_skill.py --targets claude,codex,gemini,opencode,cursor,agent --scope user --force
```

Antigravity workflow install:

```bash
python Memory-Palace/scripts/install_skill.py --targets antigravity --scope user --force
```

### Smoke evaluation

```bash
python Memory-Palace/scripts/evaluate_memory_palace_skill.py
```

Report output:

```text
Memory-Palace/docs/skills/TRIGGER_SMOKE_REPORT.md
```
