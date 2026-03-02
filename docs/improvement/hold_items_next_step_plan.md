# HOLD 项下一步开发计划（#5 / #6 / #11 / #12 / #13）

> 适用范围：`Memory-Palace`  
> 基线来源：`docs/improvement/implementation_plan.md`、`docs/improvement/phase_d_spike_report.md`  
> 目标：在不偏离项目初衷的前提下，把 `HOLD` 项推进到“可灰度、可回滚、可审计”的可发布状态。

---

## 0. 不偏离项目初衷的强制约束（Gate 0）

以下约束为硬门，任一不满足即停止推进：

1. 项目定位不变：`Memory Palace` 仍是 **AI Agent 长期记忆操作系统**，不是新知识库产品，不引入新数据库路线。
2. 存储权威不变：长期层仍由现有 SQLite 主链路承载，不新增第二套权威持久化。
3. 部署契约不变：不新增 `A/B/C/D` 之外的档位，不破坏现有 profile 默认行为。
4. 默认行为稳定：`#5/#6/#11/#12/#13` 相关能力全部默认关闭（`opt-in`）。
5. 安全优先：导入、学习、写路径必须 fail-closed，禁止“未鉴权读取触发隐式写入”。
6. 增量演进：仅在现有 `write_guard + search + runtime + observability` 主干上扩展，不重写主干。
7. 证据先行：先完成测试与评审证据，再解除 `HOLD`；未达标时保持 `HOLD`。

---

## 1. HOLD 项完成定义（Definition of Done）

| 项目 | 当前状态 | 解除 HOLD 的必要条件（全部满足） |
|---|---|---|
| #5 外部导入 | HOLD | 有鉴权、有白名单、有审计、有回滚；默认关闭；安全测试通过；profile gate 全绿 |
| #6 自动学习触发器 | HOLD | 仅显式触发；隐式写入仍禁用；有写入来源审计与回滚；默认关闭；回归全绿 |
| #11 Embedding 可插拔 | HOLD | provider 链路默认关闭；兼容旧 env 契约；失败可回退；性能/降级率达标 |
| #12 sqlite-vec | HOLD | 扩展可用性通过；质量不回退；可一键回切 legacy；默认仍 legacy |
| #13 Write Lane + WAL | HOLD | 一致性指标先达标（`failed_tx=0` 等）再谈吞吐；WAL 可开可关可回滚 |

---

## 2. 分阶段实施路线图

## 2.1 Phase H0：设计冻结与契约补齐（1-2 天）

交付物：

1. 本计划文档评审通过。
2. 新增配置矩阵（`.env.example` + `deploy/profiles/*` 默认关闭）。
3. 新增 HOLD 契约回归（默认无入口、无隐式写入）并接入门禁。

退出条件：

1. `Gate 0` 全部满足。
2. `pytest` 与 docker profile smoke 无新增失败。

## 2.2 Phase H1：#5 / #6 安全底座（2-4 天）

交付物：

1. 导入安全护栏模块（路径白名单、后缀白名单、大小/数量上限、速率限制）。
2. 显式学习触发器服务层（必须携带 `source/reason/session_id`）。
3. 审计事件模型（导入/学习/拒绝/回滚）并接入 `system://audit`。

退出条件：

1. 安全测试（路径穿越、鉴权绕过、限流）全部通过。
2. 默认关闭下行为与当前完全一致。

## 2.3 Phase H2：#5 外部导入 MVP（3-5 天）

交付物：

1. `POST /maintenance/import/prepare`（仅分析，`dry_run`）。
2. `POST /maintenance/import/execute`（显式执行）。
3. `GET /maintenance/import/jobs/{id}`（状态查询）。
4. `POST /maintenance/import/jobs/{id}/rollback`（批次回滚）。

退出条件：

1. 无 key 必须 `401`，越权路径必须拒绝，超限必须 `429`。
2. rollback 后数据与快照一致。

## 2.4 Phase H3：#6 显式学习触发器 MVP（2-4 天）

交付物：

1. `POST /maintenance/learn/trigger`（或 MCP 显式工具，默认关闭）。
2. 学习写入域隔离（建议固定到 `notes://corrections/*`）。
3. 与 `write_guard` 联动的决策与审计字段输出。

退出条件：

1. 未显式触发时，读/搜路径不产生任何新增记忆。
2. 显式触发可审计、可回滚。

## 2.5 Phase H4：#11 可插拔 + #12 vec + #13 WAL 受控灰度（5-8 天）

交付物：

1. #11：provider 接口与 orchestrator（默认关闭，不改旧契约）。
2. #12：vec capability + 双轨读写（默认 legacy，不强切）。
3. #13：WAL 可控 PRAGMA 与 lane 指标（默认 DELETE）。

退出条件：

1. 质量与稳定性门槛达标（见第 6 节）。
2. 任一子项不达标则仅该子项保持 HOLD，不阻塞其他子项。

---

## 3. 分项开发方案（最小侵入）

## 3.1 #5 外部导入（显式、可审计、可回滚）

设计要点：

1. 入口限制在 `maintenance` 域，不进入公开读面。
2. 仅允许白名单目录 + 白名单文件类型。
3. 导入过程拆成 `prepare -> execute -> rollback`，避免一步到位。
4. 限流采用 `actor_id + session_id` 双桶并行判定，任一桶超限即返回 `429`，防止通过轮换 `session_id` 绕过。
5. `rate_limit_state_file` 必须执行过期桶清理（TTL + 桶数量上限）；清理失败或状态损坏时按 fail-closed 拒绝导入并记录审计。

建议新增配置（默认关闭）：

1. `EXTERNAL_IMPORT_ENABLED=false`
2. `EXTERNAL_IMPORT_ALLOWED_ROOTS=`
3. `EXTERNAL_IMPORT_ALLOWED_EXTS=.md,.txt,.json`
4. `EXTERNAL_IMPORT_MAX_TOTAL_BYTES=5242880`
5. `EXTERNAL_IMPORT_MAX_FILES=200`

建议实现落点：

1. `backend/api/maintenance.py`：导入 API 与鉴权接入。
2. `backend/security/import_guard.py`：路径与内容安全校验。
3. `backend/db/sqlite_client.py`：导入写入与审计落盘。
4. `backend/tests/test_external_import_*.py`：单测/集成/回滚测试。

## 3.2 #6 自动学习触发器（仅显式）

设计要点：

1. 仅允许显式触发，不允许隐式自动学习。
2. 写入前必须经过 `write_guard` 与去重策略。
3. `write_guard` 任一异常必须 fail-closed：直接拒绝并审计，不得返回 `prepared/accepted`。
4. 每次触发必须可追踪来源与回滚句柄。

建议新增配置（默认关闭）：

1. `AUTO_LEARN_EXPLICIT_ENABLED=false`
2. `AUTO_LEARN_ALLOWED_DOMAINS=notes`
3. `AUTO_LEARN_REQUIRE_REASON=true`

建议实现落点：

1. `backend/api/maintenance.py` 或 `backend/mcp_server.py`：显式触发入口（任选其一先做）。
2. `backend/db/sqlite_client.py`：学习写入事务 + 审计关联。
3. `backend/tests/test_auto_learn_explicit_*.py`：显式触发与无隐式写入回归。

## 3.3 #11 Embedding 可插拔（保持旧契约）

设计要点：

1. 引入 `EmbeddingProvider` 接口，但外部配置仍兼容现有 `RETRIEVAL_EMBEDDING_*`。
2. provider chain 总开关默认关闭。
3. 失败回退优先保持当前 `hash fallback` 语义。

建议新增配置（默认关闭）：

1. `EMBEDDING_PROVIDER_CHAIN_ENABLED=false`
2. `EMBEDDING_PROVIDER_FAIL_OPEN=false`
3. `EMBEDDING_PROVIDER_FALLBACK=hash`

建议实现落点：

1. `backend/db/sqlite_client.py`：提取 provider 编排层与缓存复用。
2. `backend/tests/test_embedding_provider_chain_*.py`：优先级/降级/缓存一致性测试。

## 3.4 #12 sqlite-vec（双轨演进，默认 legacy）

设计要点：

1. 先 capability 探测，再双写，再按比例灰度读切换。
2. 默认仍走 legacy 引擎；vec 仅受控开启。
3. 必须具备一键回切 legacy 的路径。

建议新增配置（默认关闭）：

1. `RETRIEVAL_SQLITE_VEC_ENABLED=false`
2. `RETRIEVAL_SQLITE_VEC_EXTENSION_PATH=`
3. `RETRIEVAL_VECTOR_ENGINE=legacy`
4. `RETRIEVAL_SQLITE_VEC_READ_RATIO=0`

建议实现落点：

1. `backend/db/sqlite_client.py`：engine selector + capability 记录。
2. `backend/scripts/phase_d_spike_runner.py`：继续提供可比较探针。
3. `backend/tests/test_sqlite_vec_*.py`：扩展加载、读写一致、回切测试。

## 3.5 #13 Write Lane + WAL（一致性优先）

设计要点：

1. 先统一写路径进入 lane，再做 WAL 开关化。
2. WAL 默认关闭；只在灰度环境开启。
3. 一致性指标不达标时强制回切 DELETE。

建议新增配置（默认关闭）：

1. `RUNTIME_WRITE_WAL_ENABLED=false`
2. `RUNTIME_WRITE_JOURNAL_MODE=delete`
3. `RUNTIME_WRITE_WAL_SYNCHRONOUS=normal`
4. `RUNTIME_WRITE_BUSY_TIMEOUT_MS=120`
5. `RUNTIME_WRITE_WAL_AUTOCHECKPOINT=1000`

建议实现落点：

1. `backend/runtime_state.py`：lane 指标补齐。
2. `backend/db/sqlite_client.py`：写路径统一入口。
3. `backend/tests/test_write_lane_wal_*.py`：并发一致性与回滚测试。

---

## 4. 全链路测试矩阵（开发 + 回归 + 安全 + 性能）

| 维度 | 最低覆盖 | 阻断条件 |
|---|---|---|
| backend | 单测 + 集成 + 回归 | 任一关键用例失败 |
| frontend | `npm run test` + `npm run build` | 任一失败 |
| scripts/deploy/docker | `run_post_change_checks` + profile smoke | compose/smoke 任一失败 |
| mcp | 9 工具契约 + 错误契约 + 默认无入口契约 | 契约字段不兼容 |
| skills | `docs/skills` 规则与现有 MCP 能力一致性检查 | 规则与实现漂移 |
| snapshots | 导入/学习批次留痕与回滚可追踪 | 无留痕或不可回滚 |
| 安全 | 鉴权/路径穿越/限流/fuzz | 任一绕过成功 |
| benchmark | `tests/benchmark` + profile gate | 指标退化超阈值 |

---

## 5. 执行顺序与命令（强制）

## 5.1 Small Gate（快速阻断）

```bash
cd Memory-Palace/backend && .venv/bin/pytest tests/test_phase_d_hold_contracts.py -q
cd Memory-Palace/backend && .venv/bin/pytest tests/test_mcp_error_contracts.py -q
bash new/run_post_change_checks.sh --skip-sse
```

## 5.2 Profile Gate（a/b/c/d）

```bash
bash new/run_post_change_checks.sh --with-docker --docker-profile a
bash new/run_post_change_checks.sh --with-docker --docker-profile b --skip-sse
bash new/run_post_change_checks.sh --with-docker --docker-profile c --skip-sse
bash new/run_post_change_checks.sh --with-docker --docker-profile d --skip-sse
```

## 5.3 Full Gate（发布前）

```bash
cd Memory-Palace/backend && .venv/bin/pytest tests -q
cd Memory-Palace/backend && .venv/bin/pytest tests/benchmark -q
cd Memory-Palace/frontend && npm run test && npm run build
```

## 5.4 安全与鲁棒性补充门禁

```bash
cd Memory-Palace/backend && .venv/bin/pytest tests -q -k "import or learn or wal or vec"
cd Memory-Palace/backend && .venv/bin/pytest tests/benchmark -q -k "profile_a or profile_b or profile_cd"
```

说明：

1. 任一阻断项失败必须先修复再继续。
2. `C/D` 发布前必须回切模板默认（`router`）并复验。

---

## 6. 解除 HOLD 的量化门槛（Go/No-Go）

## 6.1 #11

1. `embedding_success_rate >= 99.5%`
2. `embedding_fallback_hash_rate <= 1%`（灰度期可放宽到 5%）
3. `search degraded_rate <= 1%`
4. 默认关闭下行为与当前版本一致

## 6.2 #12

1. 扩展加载成功率 `100%`（目标环境样本）
2. vec 开启后无新增 500
3. `semantic/hybrid p95` 相对 legacy 提升 `>= 20%`
4. 质量回归不低于阈值（`nDCG@10`、`Recall@10`）
5. 关闭 vec 后可稳定回切 legacy

## 6.3 #13

1. `wal_failed_tx == 0`
2. `wal_failure_rate <= 0.001`
3. `persistence_gap == 0`
4. `retry_rate_p95 <= 0.01`
5. `wal_vs_delete_tps_ratio >= 1.10`（peak）

---

## 7. Review 机制（防显性/隐性 bug）

## 7.1 评审层级

1. 设计评审：边界、默认开关、回滚路径是否完备。
2. 安全评审：鉴权、白名单、审计、限流是否可绕过。
3. 代码评审：事务边界、幂等、并发一致性、日志脱敏。
4. 测试评审：是否覆盖正常/异常/降级/回滚路径。
5. 发布评审：门禁证据是否齐全、是否满足 Go/No-Go。

## 7.2 显性 bug 清单

1. 未鉴权入口。
2. 默认开关误开。
3. 错误码语义错乱（401/403/422/429/500）。
4. 回滚不可用或不完整。

## 7.3 隐性 bug 清单

1. 路径校验 TOCTOU 漏洞。
2. 并发重复写入（非幂等）。
3. 审计漏记或字段不完整。
4. 降级原因缺失导致不可观测。
5. profile 模板与运行时覆盖混淆导致发布配置漂移。

---

## 8. 证据留痕与 snapshots 规范

每轮必须输出：

1. `new/verification_log.md`
2. `new/review_log.md`
3. `new/release_gate_log.md`

建议新增快照目录规范（如本轮启用 snapshots）：

1. `snapshots/<run_id>/scope.json`
2. `snapshots/<run_id>/changed_files.txt`
3. `snapshots/<run_id>/api_samples.json`
4. `snapshots/<run_id>/rollback_proof.md`

要求：

1. 全部日志与快照脱敏（key/token/本机路径）。
2. 每个 run 必须可追溯到命令、结果、修复与复验。

---

## 9. 发布与回滚策略

发布顺序：

1. `staging` 开启 `#5`，`#6` 继续关闭观察。
2. `staging` 验证通过后灰度开启 `#6`。
3. `#11/#12/#13` 仅在对应 Go/No-Go 单项通过后灰度，不打包硬开。

回滚策略：

1. 配置回滚：关闭对应开关并重启服务。
2. 数据回滚：按导入/学习批次回滚 + 快照核对。
3. 引擎回滚：`vec -> legacy`，`WAL -> DELETE`。

---

## 10. 执行组织建议（PR 拆分）

1. PR-1：`#5/#6` 安全底座 + 测试骨架。
2. PR-2：`#5` 导入 MVP + 回滚。
3. PR-3：`#6` 显式学习 MVP + 审计。
4. PR-4：`#11` provider 链（默认关闭）。
5. PR-5：`#12` vec 双轨（默认 legacy）。
6. PR-6：`#13` WAL 开关与一致性门。
7. PR-7：全链路门禁补齐与文档收敛。

每个 PR 都必须满足：

1. 默认关闭不改变现有行为。
2. 对应测试通过并有回滚说明。
3. 通过 `small gate -> profile gate -> full gate`。
