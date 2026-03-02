# Memory Palace 改进实施计划（重构版）

> 目标：把改进计划从“愿景列表”收敛为“可执行 backlog”，并确保不偏离项目初衷：**AI Agent 长期记忆操作系统**。
>
> 适用范围：`Memory-Palace/docs/improvement/` 下的改进文档与当前仓库实现。

---

## 0. 执行边界（必须遵守）

1. **定位不变**：Memory Palace 是 MCP 生态下的长期记忆引擎，不做 mem0 替代品，不引入图数据库路线。
2. **默认行为稳定**：新能力默认关闭或兼容现有默认值；`A/B/C/D` 档位契约不变。
3. **安全优先**：任何“自动读文件/自动写记忆”能力必须先通过鉴权与最小权限审查。
4. **增量演进**：优先在现有 `Write Guard + Gist + Observability + Runtime` 链路上扩展，不重写主干。
5. **先证据后开关**：涉及 LLM 路由、向量加速、并发优化的改造，必须先做 A/B 或 spike，再决定是否默认开启。

---

## 1. 当前基线（与代码一致）

### 1.1 已具备能力（不再重复规划）

- 检索权重可配（hybrid 权重与策略模板权重）。
- 自动 flush（含阈值触发与 compact_context 链路）。
- 嵌入缓存（`EmbeddingCache`）。
- Write Lane 双层并发协调（session lane + global lane）。
- 运行时短期记忆雏形（`SessionSearchCache` + `SessionFlushTracker`）。

### 1.2 原 14 项改进状态矩阵（重排）

状态定义：
- `DONE`：已实现并可用
- `PARTIAL`：已有基础，但与目标形态不完全一致
- `BACKLOG`：建议进入实施队列
- `HOLD`：高风险/高不确定，先冻结
- `DROP`：不建议继续推进

| # | 改进项 | 当前状态 | 处理结论 |
|---|---|---|---|
| 1 | 索引自动生成 | `PARTIAL` | 进入 `BACKLOG`（做 index-lite，复用 gist） |
| 2 | 审计 URI | `PARTIAL` | 进入 `BACKLOG`（聚合现有分散能力） |
| 3 | 作用域规则 | `PARTIAL` | 进入 `BACKLOG`（先 query hint，后 schema） |
| 4 | 层级继承 | `BACKLOG` | 保留（仅可选参数，不改默认行为） |
| 5 | 外部导入 | `HOLD` | 冻结（安全风险高，当前读面不适合直接放开） |
| 6 | 自动学习触发器 | `PARTIAL` | `HOLD`（先做显式触发，不做隐式自动写入） |
| 7 | MMR 去重 | `BACKLOG` | 保留（hybrid 下 feature flag） |
| 8 | 搜索权重可配 | `DONE` | 关闭该项（转入维护） |
| 9 | 自动 flush | `DONE` | 关闭该项（转入维护） |
| 10 | 嵌入缓存 | `DONE` | 关闭该项（转入维护） |
| 11 | Embedding 可插拔 | `PARTIAL` | `HOLD`（先避免变量体系冲突） |
| 12 | sqlite-vec 加速 | `HOLD` | 仅做 spike，不进短周期承诺 |
| 13 | Write Lane 优化 | `PARTIAL` | `HOLD`（先做压测与 WAL 可行性） |
| 14 | 意图分类 LLM | `BACKLOG` | 保留为实验项（默认关闭） |

### 1.3 Phase A 补齐：逐项验收定义与默认开关策略

| # | 改进项 | Phase A 验收定义 | 默认开关策略 |
|---|---|---|---|
| 1 | 索引自动生成 | `read_memory(system://index-lite)` 在“有/无数据”两种场景都返回可解释结果，不出现 500；输出包含生成时间与条目计数。 | 默认关闭自动生成；仅在显式调用或 `INDEX_LITE_ENABLED=true` 时生成/暴露引导入口。 |
| 2 | 审计 URI | `read_memory(system://audit)` 至少聚合 `index/guard/gist/vitality` 四类摘要；任一子模块缺失需给出 `degrade_reason`。 | 默认只读开启；高成本扩展字段仅在 `AUDIT_VERBOSE=true` 时返回。 |
| 3 | 作用域规则 | `search_memory` 支持 scope hint 且回显最终生效策略；未传 hint 时行为与当前版本一致。 | 默认关闭强制作用域；仅显式传 `scope_hint` 时生效，不改 schema。 |
| 4 | 层级继承 | `read_memory` 新增 `include_ancestors` 可选参数，默认不变；开启后按父链补全且无重复节点。 | 默认 `include_ancestors=false`；仅调用方显式开启。 |
| 5 | 外部导入 | 仅输出安全设计验收包（鉴权、白名单、审计、回滚）；未满足四项前不得进入开发。 | 默认关闭（`HOLD`）；无运行时入口。 |
| 6 | 自动学习触发器 | 仅允许显式触发写入；隐式自动写入路径全部禁用，并在观测中可追踪触发来源。 | 默认关闭隐式学习；显式触发由 `AUTO_LEARN_EXPLICIT_ENABLED=true` 控制。 |
| 7 | MMR 去重 | 开关开启后重复结果占比下降且召回不低于基线阈值；结果元数据包含 `mmr_applied`。 | 默认关闭；仅 `RETRIEVAL_MMR_ENABLED=true` 且 hybrid 检索时生效。 |
| 8 | 搜索权重可配 | 权重配置缺失/非法时可回退默认值，且回归不退化。 | 保持现状（已可用）；不新增开关。 |
| 9 | 自动 flush | 达阈值自动 flush，异常时可降级并保持主流程可用；观测可看到触发原因。 | 保持现状（已开启）；仅保留现有阈值参数。 |
| 10 | 嵌入缓存 | 缓存命中/降级路径在观测可追踪；缓存不可用时可回退在线请求。 | 保持现状（已开启）；仅保留 TTL/容量参数调优。 |
| 11 | Embedding 可插拔 | 先完成 provider 兼容矩阵与回滚方案评审；不得破坏现有 `RETRIEVAL_EMBEDDING_*` 契约。 | 默认 `HOLD`；不启用自动 provider 切换。 |
| 12 | sqlite-vec 加速 | 仅接受 spike 报告通过（收益、兼容、回滚三项齐备）后再进入开发。 | 默认关闭；只允许实验环境手动开启。 |
| 13 | Write Lane 优化 | 压测证明吞吐提升且一致性回归为零，并完成 rollback 演练。 | 默认关闭优化路径；保持现有 lane 行为。 |
| 14 | 意图分类 LLM | 开关开启后需满足 A/B 指标目标；失败必须自动回退规则策略并记录 `degrade_reason`。 | 默认关闭；仅 `INTENT_LLM_ENABLED=true` 开启。 |

---

## 2. 过度设计与下线清单

以下内容从“短周期实施计划”中移除：

1. **新增 `B+` 档位**：与当前 `A/B/C/D` 部署契约冲突，先下线。
2. **一次性推进 1-14 全项**：当前实现已覆盖多项，继续全量推进会造成重复建设。
3. **外部文件导入默认开启**：在当前公开读面下风险高，先冻结。
4. **自动学习隐式写入**：易引入噪声与记忆污染，先只保留显式触发方向。

---

## 3. 争议方向裁决（你关心的“是否值得做”）

下表给出“是否值得改”和“怎么改才不改过头”的明确结论：

| 改进项 | 是否值得做 | 结论 | 推进方式 |
|---|---|---|---|
| #3 作用域规则 | `值得` | 直接带来检索精度收益，且可增量实现 | 已纳入 `Phase B`，先做 query hint，不动 schema |
| #5 外部导入 | `值得，但当前不宜落地` | 能扩展知识来源，但在现有安全边界下风险偏高 | 保持 `HOLD`，先完成鉴权/路径白名单/可审计导入再开发 |
| #12 sqlite-vec | `值得评估` | 潜在性能收益大，但兼容性和收益不确定 | 纳入 `Phase D Spike`，先做 Go/No-Go |
| #13 WAL + 批写 | `值得评估` | 高并发场景可能收益明显，但一致性风险需实测 | 纳入 `Phase D Spike`，先压测与回滚演练 |
| #14 intent LLM | `值得` | 对复杂查询路由有价值，但必须实验开关化 | 已纳入 `Phase C`，默认关闭，A/B 达标才考虑推广 |

裁决原则：这些方向**不是否决**，而是“分层推进、证据驱动、默认保守”。

---

## 4. 关键缺口：短期记忆（Short Memory）

### 4.1 现状判断

项目并非“完全没有 short memory”，已存在运行时短期层：

- 会话检索缓存：`SessionSearchCache`
- 会话压缩缓冲：`SessionFlushTracker`
- search 的 session-first 合并

### 4.2 当前短板

1. 短期记忆仍偏“内部机制”，缺少明确的对外契约和观测指标。
2. 会话隔离语义不够强（当前以进程内 session id 为主）。
3. 缺少“短期 -> 长期”的显式晋升策略说明（目前主要靠 auto flush）。

### 4.3 方向（不偏离长期记忆定位）

将 short memory 定位为**运行时工作集**，而不是第二套持久化系统：

- 只增强“会话态缓存 + 晋升策略 + 观测”，不引入新数据库层。
- 长期记忆仍是唯一权威存储（SQLite）。

### 4.4 轻量短期层实施包（明确纳入计划）

定义：short memory 是 runtime 工作集，不是第二套持久化系统。

实施包 `SM-Lite`：

1. `SM-1 会话工作集契约`
   - 暴露 short memory 核心状态（命中、待 flush 事件、最近活动）。
   - 明确会话生命周期（活跃、过期、清空）。
2. `SM-2 晋升机制标准化`
   - 固化“短期 -> 长期”唯一通道：`compact_context + auto flush`。
   - 输出晋升元数据（来源、触发原因、摘要质量、降级原因）。
3. `SM-3 检索融合可观测`
   - 对 session-first 合并过程给出可审计指标，避免黑盒。
4. `SM-4 非目标（防止跑偏）`
   - 不新增短期记忆独立数据库。
   - 不引入第二套持久化索引链路。

---

## 5. 分期执行计划（重构后）

## Phase A：计划与契约收敛（P0，1-2 天）

目标：先把“文档计划”和“实际代码”对齐，避免继续错配。

任务：
1. 冻结原 14 项状态矩阵：`DONE/PARTIAL/BACKLOG/HOLD/DROP`（本文件维护唯一版本）。
2. 清理冲突项：移除 `B+`、移除重复已完成功能的“新增”表述。
3. 补齐每项的验收定义与默认开关策略（见 `1.3`）。
4. 明确本阶段执行范围与验证顺序，避免跨阶段并行。

本轮范围（明确到模块/文件）：
1. `Memory-Palace/docs/improvement/implementation_plan.md`（主改动文件）。
2. `new/verification_log.md`、`new/release_gate_log.md`（仅门禁脚本自动追加运行记录）。

本阶段执行顺序（先最小验证，再门禁）：
1. 最小验证（文档契约检查）：
   ```bash
   file=Memory-Palace/docs/improvement/implementation_plan.md
   for p in \
     "### 1\\.3 Phase A 补齐：逐项验收定义与默认开关策略" \
     "本轮范围（明确到模块/文件）" \
     "本阶段执行顺序（先最小验证，再门禁）"
   do
     rg -q "$p" "$file" || { echo "phase_a_contract_check_failed:$p"; exit 1; }
   done
   echo "phase_a_contract_check_pass"
   ```
2. 门禁验证：
   ```bash
   bash new/run_post_change_checks.sh --with-docker --docker-profile b --skip-sse
   ```

验收：
1. 计划中不再出现与现有实现冲突的默认行为。
2. 不再出现超出当前部署契约的新增档位。
3. `1.3` 的 14 项均具备“验收定义 + 默认开关策略”。

---

## Phase B：高价值低风险增强（P0/P1，3-5 天）

目标：在不改主架构的前提下，提高可用性与可观测性。

任务：
1. **#2 审计 URI**：新增 `system://audit`，聚合 index status、guard 统计、gist/vitality 摘要。
2. **#1 索引自动生成（轻量版）**：基于 `memory_gists` 生成 `system://index-lite`，并在 `system://boot` 提供可选入口。
3. **`SM-1` 短期记忆契约 v1**：暴露 session cache/flush 的关键统计到 `index_status` 或 `system://audit`。
4. **#3 作用域规则 v1**：先支持查询侧 scope hint（不改 schema）。

验收：
1. 不新增破坏性参数；旧调用兼容。
2. 后端回归通过，`search_memory` 与 `read_memory(system://*)` 行为可解释。
3. 明确 short memory 仅为 runtime 工作集，不引入持久化新层。

---

## Phase C：检索质量增强（P1，4-6 天）

目标：提高检索结果质量，但全部以开关控制。

任务：
1. **#7 MMR 去重**：仅在 hybrid 下提供 feature flag，默认关闭。
2. **#4 层级继承**：`read_memory` 增 `include_ancestors` 可选参数，默认 `false`。
3. **#14 意图 LLM（实验）**：加入 `INTENT_LLM_ENABLED` 实验开关，失败回退关键词规则。
4. **`SM-2/SM-3`**：补齐短期记忆晋升元数据与 session-first 可观测指标。
5. **C/D 联调口径（临时）**：开发测试阶段，`profile_c/profile_d` 的 benchmark 默认走 `RETRIEVAL_EMBEDDING_BACKEND=api`（不走 `router`），并显式配置 `RETRIEVAL_EMBEDDING_*` 与 `RETRIEVAL_RERANKER_*`；该约定仅用于本地联调，最终交付前需按部署模板口径回切并复验。
6. **本地联调覆盖来源（记录）**：`new/run_post_change_checks.sh` 在 `--docker-profile c|d` 时允许加载本地 runtime `.env` 覆盖（优先 `Memory-Palace/.env`，其次 `~/Desktop/clawmemo/nocturne_memory/.env`）以便本地 API 链路联调继续进行；该行为仅用于开发验证，不改变 `deploy/profiles/*` 的默认模板。

验收：
1. 默认配置下行为与当前版本一致。
2. 开启实验开关后，有明确 A/B 指标和 degrade reason。
3. 不改变“长期层是唯一权威存储”的架构边界。
4. 若本轮使用过 C/D `api` 联调口径，发布前必须执行一次“回切检查”：确认 `deploy/profiles/*/profile-c.env` 与 `profile-d.env` 仍保持 `router` 默认，并重新执行 `--docker-profile c|d` 烟测。
5. 上线前复验必须在“未加载本地 runtime 覆盖”的环境执行，避免把开发机私有 API 配置带入交付判定。

---

## Phase D：技术 Spike（P2，按需）

以下项不纳入短周期承诺，只做可行性验证：

1. **#11 Embedding 可插拔深化**：评估主备 provider 链，不打破现有 `RETRIEVAL_EMBEDDING_*` 契约。
2. **#12 sqlite-vec**：评估扩展可用性、兼容性、收益与回滚成本。
3. **#13 Write Lane + WAL**：评估高并发收益与一致性风险。
4. **#5 外部导入 / #6 自动学习隐式化**：需先过安全评审与噪声治理方案。

Go/No-Go 标准：
1. 无法证明收益显著且回滚简单时，保持 `HOLD`。
2. 不满足安全基线时，不进入开发阶段。

---

## 6. 验证门禁（每阶段执行）

最小门禁：

```bash
cd Memory-Palace/backend && .venv/bin/pytest tests -q
cd Memory-Palace/frontend && npm run test && npm run build
bash new/run_post_change_checks.sh --with-docker --docker-profile b --skip-sse
```

增强门禁（涉及检索改动时）：

```bash
cd Memory-Palace/backend && .venv/bin/pytest tests/benchmark -q
```

---

## 7. 决策记录（本次重构结果）

### 7.1 保留执行

- #1、#2、#3、#4、#7、#14（其中 #14 为实验项）
- short memory 契约增强（作为本轮新增治理项）

### 7.2 暂缓（HOLD）

- #5、#6、#11、#12、#13

### 7.3 关闭（DONE/不再作为新增）

- #8、#9、#10

---

## 8. 后续维护规则

1. 每次状态变更只更新本文件，不再维护平行版本计划。
2. 新改进项必须先写“默认行为是否变化 + 回滚策略 + 验收命令”。
3. 不允许再引入与 `A/B/C/D` 契约冲突的新档位，除非先更新部署规范并完成兼容评审。

---

## 9. 下次开工指令模板（可直接复制）

目标：让执行从第一条消息就进入“低风险、可回滚、可验收”模式。

### 模板 A（按阶段执行，推荐）

```text
请按 /Users/yangjunjie/Desktop/clawanti/Memory-Palace/docs/improvement/implementation_plan.md 执行 Phase <A|B|C|D>。
要求：
1) 仅修改该阶段必要文件，禁止无关重构。
2) 每完成一个子任务先跑最小验证，再进入下一步。
3) 若出现失败，先定位根因并给出修复，再继续推进。
4) 阶段结束后执行文档中的“验证门禁”命令并汇总结果。
5) 输出：改动文件清单、风险点、回滚点、验证结果。
暂时不要做下一阶段。
```

### 模板 B（一次执行到“可提交”）

```text
请从 implementation_plan.md 的当前待执行阶段开始，连续执行到形成一次可提交变更。
要求：
1) 严格遵守“执行边界”和“争议方向裁决”。
2) #14 必须保持默认关闭（实验开关）；#5/#12/#13 按计划仅做对应级别动作（不得越级）。
3) 前后端、docker scripts、部署与运行记录都要覆盖验证。
4) 输出最终建议 commit message（Conventional Commits）和验证摘要。
```

---

## 10. 模块级执行清单（避免漏项）

| 模块 | 何时必须覆盖 | 最低执行动作 |
|---|---|---|
| `backend/` | 检索、记忆、runtime、API 任一改动 | 跑相关 pytest；阶段末跑 `backend tests -q` |
| `frontend/` | API 契约或页面行为改动 | 跑相关前端测试；阶段末 `npm run test && npm run build` |
| `scripts/` + `deploy/` + `docker-compose.yml` | 部署/脚本/端口/档位改动 | 跑 `run_post_change_checks.sh` 的 docker 门禁 |
| `docs/` | 参数、流程、接口行为变更 | 同步本文件与相关文档，避免计划漂移 |
| `new/*.md` 运行记录 | 每次阶段验收 | 记录门禁 run 结果与失败原因/修复结论 |
| `snapshots/` | 需要文件级快照留痕时 | 当前仓库为空目录；如本轮需快照，先定义命名规范再写入 |

说明：`snapshots/` 目前不在默认门禁产物链中，现阶段以 `new/` 下验证日志为主要审计锚点。

---

## 11. Bug 预防执行规则（开工即生效）

1. 每次只推进一个阶段，不跨阶段并行改动。
2. 每个子任务“改动 -> 最小验证 -> 再改动”，避免堆叠故障。
3. 默认不开启实验特性（尤其 #14），除非该任务明确要求。
4. 任何影响部署行为的改动必须补一条 docker 门禁验证。
5. 验收失败时不跳过，必须给出“失败原因 + 修复动作 + 复验结果”。
