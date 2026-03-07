# Memory Palace Skills 快速上手

> 如果你只想把当前仓库这条 skill + MCP 链路接通，按这份做就够了。

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

仓库里的 `.claude/.codex/.gemini/.opencode/...` 都是 **mirror**。

这套设计已经对齐 `Anthropic skill-creator` 的核心要求：

- 有 `frontmatter`
- `description` 承担触发契约
- 细节放在 `references/`
- 有 `eval / smoke / e2e` 回归入口

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

这一步会明确做两件事：

1. 继续保持 repo-local skill mirrors
2. 给 **当前仓库** 落两个直连入口：
   - `Claude Code` → `.mcp.json`
   - `Gemini CLI` → `.gemini/settings.json`

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
- `OpenCode` → `~/.config/opencode/opencode.json`

---

## 5. 四个客户端现在怎么理解

### Claude Code

- repo-local skill：有
- workspace MCP：有

结论：

- **打开当前仓库即可直接用**

### Gemini CLI

- repo-local skill：有
- workspace MCP：有

结论：

- **workspace 入口已经就位**
- 如果你想更稳，或者准备跨仓复用，再补一次 `--scope user --with-mcp`
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

这条命令会在本地生成脱敏 smoke 摘要：

```text
docs/skills/TRIGGER_SMOKE_REPORT.md
```

### 真实 MCP 调用链

```bash
cd backend
python ../scripts/evaluate_memory_palace_mcp_e2e.py
```

这条命令会在本地生成真实 MCP e2e 摘要：

```text
docs/skills/MCP_LIVE_E2E_REPORT.md
```

这两份报告默认建议留在你自己的机器上，不把它们当成主入口文档。

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
