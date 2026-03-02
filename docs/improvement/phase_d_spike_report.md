# Phase D Spike Report

## 1. 执行范围（仅 Phase D）

本轮仅覆盖以下模块/文件：

1. `Memory-Palace/backend/scripts/phase_d_spike_runner.py`
2. `Memory-Palace/backend/tests/test_phase_d_spike_runner.py`
3. `Memory-Palace/backend/tests/benchmark/phase_d_spike_metrics.json`
4. `Memory-Palace/backend/tests/benchmark/benchmark_results_phase_d_spike.md`
5. `Memory-Palace/backend/db/sqlite_client.py`
6. `Memory-Palace/backend/api/browse.py`
7. `Memory-Palace/backend/tests/test_browse_read_side_effects.py`

未做跨阶段功能开发；未引入新部署档位；未改变默认开关策略。

## 2. Phase D 子项执行结果

### 2.1 #11 Embedding 可插拔深化（Spike）

执行内容：

1. 新增 provider 契约探针（环境变量来源、backend/base/key 可观测）。
2. 输出结构化报告供 Go/No-Go 复核。

结论：

1. **GO（仅 Spike 层）**：当前探针链路可运行。
2. **保持默认保守**：不改变现有 `RETRIEVAL_EMBEDDING_*` 契约，不启用自动 provider 切换。

### 2.2 #12 sqlite-vec（Spike）

执行内容：

1. 新增 sqlite runtime + 扩展加载可用性探针。
2. 在未提供扩展路径时输出 `skipped_no_extension_path`，避免假阳性。

结论：

1. **保持 HOLD（生产层）**。
2. 仅确认“探针可运行”，尚未完成扩展加载兼容验证（无扩展路径输入）。

### 2.3 #13 Write Lane + WAL（Spike）

执行内容：

1. 新增 WAL/DELETE 小负载并发写入对比探针。
2. 产出吞吐、失败事务、锁重试统计。

结果（本轮样本）：

1. `DELETE throughput_tps=2463.279`
2. `WAL throughput_tps=6199.118`
3. `wal_vs_delete_throughput_ratio=2.517`
4. 两组 `failed_tx=0`

结论：

1. **GO（仅 Spike 层）**：存在可测收益信号。
2. **保持 HOLD（生产层）**：尚未完成业务高并发实压与回滚演练，不进入默认路径。

### 2.4 #5 外部导入 / #6 自动学习隐式化（安全评审）

执行内容：

1. 完成安全审查并识别“未鉴权读取触发隐式写入”风险。
2. 本轮修复该风险：`GET /browse/node` 改为无副作用读取（不再强化 access/vitality）。
3. 保持默认策略：不引入外部导入入口，不启用隐式自动学习写入。

结论：

1. **安全基线提升**：关闭一个高优先级隐式写入面。
2. **保持 HOLD（#5/#6）**：未越级实现外部导入或隐式学习。

## 3. Phase D 历史锚点（本轮门禁与测试结果）

已执行命令与结果：

1. `cd Memory-Palace/backend && .venv/bin/pytest tests/test_phase_d_spike_runner.py tests/test_browse_read_side_effects.py tests/test_sensitive_api_auth.py -q` -> `7 passed`
2. `cd Memory-Palace/backend && .venv/bin/pytest tests -q` -> `222 passed`
3. `cd Memory-Palace/backend && .venv/bin/pytest tests/benchmark -q` -> `46 passed`
4. `cd Memory-Palace/frontend && npm run test` -> `39 passed`
5. `cd Memory-Palace/frontend && npm run build` -> `pass`
6. `bash new/run_post_change_checks.sh --with-docker --docker-profile b --skip-sse` -> `PASS=10, FAIL=0, SKIP=1`

说明：本节为 Phase D Spike 历史锚点。H1 二次风险修复最新锚点为 targeted regression `33 passed`、full gate backend `257 passed`、security gate（`import/learn/wal/vec`）`34 passed`（详见 `docs/improvement/hold_items_next_step_plan.md` 与 `llmdoc/reference/phase-status.md`）。

## 4. 风险与回滚点

风险：

1. sqlite-vec 仍未完成真实扩展加载兼容验证（本轮无扩展路径输入）。
2. WAL 收益来自小负载 Spike，尚未覆盖真实业务混合写路径与长压测。
3. Embedding 多 provider 仍是“探针与评估”阶段，非生产级主备切换能力。

回滚点：

1. 如读路径行为需回滚，可恢复 `browse.get_node -> get_memory_by_path(... reinforce_access=True)`。
2. Phase D 探针均为旁路脚本/测试，必要时可整组删除：
   - `backend/scripts/phase_d_spike_runner.py`
   - `backend/tests/test_phase_d_spike_runner.py`
   - `backend/tests/benchmark/phase_d_spike_metrics.json`
   - `backend/tests/benchmark/benchmark_results_phase_d_spike.md`
3. WAL 生产试验前保持 `journal_mode` 默认策略；若后续试验异常，回切 `DELETE`。

## 5. 阶段停止点

本轮已完成 Phase D 既定 Spike 与评审动作，当前停止在 Phase D，不自动进入下一阶段。
