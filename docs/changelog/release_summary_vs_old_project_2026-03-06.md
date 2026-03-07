# 发布说明 / 变更摘要（相对旧项目）

> 对比对象：
>
> - 旧项目：`<old-repo>/Memory-Palace`
> - 当前项目：`Memory-Palace`
>
> 说明：
>
> - 本文只写**已落地、已验证**的内容。
> - 原始 benchmark 日志、阶段性重测草稿和维护笔记默认只在维护阶段使用；这里保留的是可直接阅读的结论版。
> - Windows 口径当前仅完成 `pwsh-in-docker` 等效验证，**未完成原生 Windows / native `pwsh` 终验**。

---

## 1. 一句话结论

当前版本相对旧项目，已经从“能用的长期记忆服务”升级为“**skills/MCP 更完整、部署更稳、验证更严、在高干扰检索场景下更强**”的版本。

---

## 2. 用户最能感知到的变化

| 变化 | 具体做了什么 | 对用户的作用 | 代码 / 测试锚点 |
|---|---|---|---|
| `skills + MCP` 产品化 | 补齐 canonical bundle、安装脚本、同步脚本、smoke、live e2e | 不再只是“有工具”，而是“知道怎么装、怎么查、怎么验” | `scripts/install_skill.py`、`scripts/sync_memory_palace_skill.py`、`scripts/evaluate_memory_palace_mcp_e2e.py`、`backend/tests/test_mcp_stdio_e2e.py` |
| Docker 部署更稳 | 一键脚本加 deployment lock，运行时注入改成显式开启 | 避免多次部署互相踩配置，也减少误把本机环境带进发布包 | `scripts/docker_one_click.sh`、`scripts/docker_one_click.ps1` |
| benchmark 更可复核 | real runner 改成唯一 workdir，并补了并发隔离测试 | 多次跑 benchmark 时不容易互相污染结果 | `backend/tests/benchmark/helpers/profile_abcd_real_runner.py`、`backend/tests/benchmark/test_profile_abcd_real_runner.py` |
| 新增系统 URI / 检索兼容点 | 新增 `system://audit`、`system://index-lite`、`include_ancestors`、`scope_hint` | 调试、审计、回忆路径更清楚，客户端工作流更顺 | `backend/mcp_server.py`、`backend/tests/test_system_uri_audit_index_lite.py`、`backend/tests/test_read_memory_include_ancestors.py`、`backend/tests/test_search_memory_scope_hint_compat.py` |
| 导入 / 显式学习链路补齐 | 新增 import / learn / rollback 相关接口与保护逻辑 | 后续扩展空间更清楚，但默认仍保持保守 | `backend/api/maintenance.py`、`backend/tests/test_external_import_api_prepare.py`、`backend/tests/test_auto_learn_explicit_service.py` |

---

## 3. 和旧项目相比，哪些地方提升最明显

### 3.1 检索质量

在更容易被干扰的对照场景里，当前版本的 C / D 档位有明显提升：

先看指标意思：

- **HR@10**：前 10 条里有没有找到正确结果
- **MRR**：正确结果排得靠不靠前
- **NDCG@10**：整体排序质量好不好

如果你只想快速判断“新版是不是更强”，优先看 **HR@10**。

<p align="center">
  <img src="../images/benchmark_comparison.png" width="900" alt="旧版 vs 当前版本检索质量与延迟对比图" />
</p>

> 📈 这张图就是这次要看的重点：同口径下，旧版和当前版本在质量与延迟上的直接对照。

| 场景 | 指标 | 旧版 C | 新版 C | 旧版 D | 新版 D |
|---|---|---:|---:|---:|---:|
| `s8,d10` | `HR@10` | 0.875 | 0.875 | 0.875 | 0.875 |
| `s8,d10` | `MRR / NDCG@10` | 0.783 / 0.805 | 0.783 / 0.805 | 0.825 / 0.837 | 0.825 / 0.837 |
| `s8,d200` | `HR@10` | 0.313 | 0.563 | 0.375 | 0.625 |
| `s8,d200` | `MRR / NDCG@10` | 0.313 / 0.313 | 0.563 / 0.563 | 0.375 / 0.375 | 0.625 / 0.625 |
| `s100,d200` | `HR@10` | 0.280 | 0.580 | 0.295 | 0.615 |
| `s100,d200` | `MRR / NDCG@10` | 0.247 / 0.255 | 0.512 / 0.529 | 0.268 / 0.275 | 0.560 / 0.573 |

补充说明：

- 低难度场景 `s8,d10` 没有被夸大，结论就是**持平**。
- 高干扰场景提升很直观：
  - `s8,d200`：C 的 `HR@10` 从 `0.313` 提到 `0.563`，D 从 `0.375` 提到 `0.625`
  - `s100,d200`：C 的 `HR@10` 从 `0.280` 提到 `0.580`，D 从 `0.295` 提到 `0.615`
- `s8,d10 / s8,d200 / s100,d200` 里的 `s` 是样本量，`d` 是干扰文档数量；`d` 越大，说明场景越难。
- 这里只保留摘要数字；详细重测工作笔记默认只在维护阶段使用。

### 3.2 延迟观察

这轮对照的主结论是**质量提升明显**，不是“所有场景延迟都更低”。

这里的 `p95` 可以简单理解成：

- 100 次请求里，排到最慢那 5 次，大概慢到什么程度
- 所以它更接近用户真实感受到的“高峰时延”

| 场景 | 仓库 | C p95(ms) | D p95(ms) |
|---|---|---:|---:|
| `s8,d10` | 旧版 | 474.5 | 2103.2 |
| `s8,d10` | 新版 | 639.5 | 2088.2 |
| `s8,d200` | 旧版 | 945.8 | 2507.1 |
| `s8,d200` | 新版 | 1150.9 | 2428.8 |
| `s100,d200` | 旧版 | 1027.8 | 2796.5 |
| `s100,d200` | 新版 | 937.6 | 2772.0 |

怎么理解这张表：

- 新版的价值主要体现在**更难场景下召回更高**
- `s100,d200` 这类更接近真实复杂检索的场景里，新版延迟并没有明显变坏
- 所以更适合对外说“**质量更强，而且延迟整体仍在可接受范围**”，而不是简单说“更快”

### 3.3 新版增强上限（补充）

在同样的 `s100,d200` 场景下，新版把 `candidate_multiplier` 从 `4` 提到 `8` 之后，3 次重复的均值为：

- C：`HR@10=0.700`、`MRR=0.607`、`NDCG@10=0.630`
- D：`HR@10=0.720`、`MRR=0.651`、`NDCG@10=0.668`

这组数字说明：

- 新版不只是“公平口径下更强”
- 在允许更高候选池的情况下，它还有进一步提升空间
- 代价是 D 档位的时延会更高，所以更适合写成“**可按业务场景调质量 / 时延权衡**”

这里的 `candidate_multiplier` 也顺手解释一下：

- 它决定第一轮先放大多少候选结果，再进入后续排序
- 通常数值越大，质量更有机会提升
- 但代价也很直接：更慢、算得更多

### 3.4 部署与发布

- 旧版更像“能起服务”；新版更强调“能稳定发布”。
- 现在有 `scripts/pre_publish_check.sh` 做分享或发布前的仓库卫生检查。
- 更大的变更后还可以走 `new/run_post_change_checks.sh` 做统一门禁。

### 3.5 客户端接入

- 旧版更多停留在“有 MCP / 有技能说明”。
- 新版把 `Claude / Codex / Gemini / OpenCode` 的安装、同步、验证链都补齐了。
- 同时把边界写清楚：`Gemini live` 还没到能写成“完全通过”的程度，`Cursor / Antigravity` 也仍保留人工环节。

---

## 4. 哪些东西其实没变

- 前端主页面还是四个核心入口：`Memory / Review / Maintenance / Observability`
- MCP 主工具面依旧是 **9 个工具**
- FastAPI + SQLite + React 这条主架构没有推倒重来

这意味着：**老用户不会完全认不出项目，但会明显感受到“更稳、更清楚、更好验收”**。

---

## 5. 当前公开验证范围

### 5.1 已明确验证

- `scripts/pre_publish_check.sh` 存在且可执行
- MCP stdio live e2e 报告为 `PASS`
- `Claude / Codex / OpenCode / Gemini` 均有 smoke 结果
- `macOS + Docker + pwsh-in-docker` 等效 Windows 口径已覆盖

### 5.2 仍需保守表述

- `Gemini live`：当前仍未完全通过
- `Cursor / agent / Antigravity`：当前仍为 `PARTIAL`
- 原生 Windows / native `pwsh`：仍需在目标环境补验

---

## 6. 对外建议怎么说

建议这样写：

> 当前版本已经完成一轮基于真实代码、真实脚本和真实测试结果的升级收口。  
> 相对旧项目，最大的变化是 `skills/MCP` 更完整、部署门禁更稳、benchmark 更可复核，并且在高干扰检索场景下表现更好。  
> 当前已完成 `macOS + Docker + pwsh-in-docker` 等效 Windows 验证；原生 Windows / native `pwsh` 仍保留为后续补验项。

不建议这样写：

- “所有客户端都已经完全开箱即用”
- “所有平台都已经完成原生终验”
- “已经证明绝对没有隐性 bug”

---

## 7. 关键实现锚点

- `skills/MCP` 安装与同步：`scripts/install_skill.py`、`scripts/sync_memory_palace_skill.py`
- live MCP e2e：`scripts/evaluate_memory_palace_mcp_e2e.py`、`backend/tests/test_mcp_stdio_e2e.py`
- 部署锁：`scripts/docker_one_click.sh`、`scripts/docker_one_click.ps1`
- 分享或发布前自检：`scripts/pre_publish_check.sh`
- benchmark 隔离：`backend/tests/benchmark/helpers/profile_abcd_real_runner.py`
- review 错误语义：`backend/api/review.py`
