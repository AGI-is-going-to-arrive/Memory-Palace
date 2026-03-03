# sqlite-vec vec-native topK 改造与测试建议（#12）

## 1. 背景结论

当前 `#12` 在“同后端、只切 vec”的口径下，质量不回退但性能未达门槛（`semantic/hybrid p95` 相对 legacy 提升 `>=20%`）。

已确认的关键事实：

1. 当前检索主路径仍是应用层向量打分（读出 JSON 向量后在 Python 里算相似度并排序）。
2. `vector_engine_selected` 已存在，但尚未形成稳定的 vec-native topK 热路径。
3. 因此“扩展可用”不等于“必然有性能提升”。

## 2. 是否偏离项目初衷

不会偏离。实现真正的 vec-native topK 查询路径，属于 `#12 sqlite-vec` 的核心目标强化，而不是方向变更。

不偏离的前提边界：

1. 默认行为保持 `legacy`（不改变现网默认）。
2. `vec` 必须可灰度（`read_ratio`）与可回滚（故障可回切 legacy）。
3. 质量门禁不放松（`nDCG@10`、`Recall@10` 不回退）。
4. 降级原因必须可观测（如 `sqlite_vec_fallback_legacy`）。

## 3. 建议实现路线（最小风险）

1. 保留现有 `legacy` 路径作为稳定基线，不删除。
2. 新增 `vec-native topK` 查询分支，仅在 `vector_engine_selected=vec` 且 capability `ready` 时走该分支。
3. 将向量候选召回下推到 SQLite/vec 扩展侧，避免“大量向量回传 + Python 全量打分”。
4. `hybrid` 仍维持“关键词分 + 语义分”融合逻辑，只替换语义候选生成阶段。
5. 保持 `dual + read_ratio` 灰度读策略，便于 A/B 对照与快速回滚。

## 4. 推荐测试口径（下次沿用）

测试目标：验证“vec-native topK”是否在不降质前提下达成性能收益。

统一口径：

1. 同 embedding backend（建议 `hash` 或固定 API，不混用）。
2. 同数据集、同 sample_size、同 candidate 配置、同机器。
3. 仅切 `vec_off` vs `vec_on`。
4. 必须记录 `degrade_reasons`，并区分“真实 vec 生效”与“fallback legacy”。

推荐指标：

1. 性能：`latency_improvement_ratio = (b_p95 - c_p95) / b_p95`
2. 质量：`nDCG@10`、`Recall@10`
3. 稳定性：`fallback_legacy_rows`、`all_c_rows_valid`

判定建议：

1. 质量门：`quality_non_regression_all=true`
2. 生效门：`fallback_legacy_rows=0`
3. 性能门：`latency_improvement_ratio` 达到既定阈值（当前计划阈值 `>=0.20`）

## 5. 本地快速复验命令（当前仓库）

1. 后端全量回归：

```bash
cd Memory-Palace/backend && .venv/bin/pytest tests -q
```

2. benchmark 回归：

```bash
cd Memory-Palace/backend && .venv/bin/pytest tests/benchmark -q
```

3. 前端回归：

```bash
cd Memory-Palace/frontend && npm run test && npm run build
```

4. 门禁脚本：

```bash
cd /Users/yangjunjie/Desktop/clawanti && bash new/run_post_change_checks.sh --skip-sse
```

## 6. 本轮证据产物（可直接复查）

1. `backend/tests/benchmark/profile_vec_isolation_metrics_v2.json`
2. `backend/tests/benchmark/profile_vec_isolation_metrics.json`
3. `new/verification_log.md`
4. `new/review_log.md`
5. `new/release_gate_log.md`
