# Memory Palace 评测结果

本文档汇总 Memory Palace 各档位（A/B/C/D）的检索质量、延迟与语义质量门禁测试结果。所有数据均来自仓库内已落盘 JSON 产物，可通过 `pytest` 命令完整复现。

---

## 1. 数据来源

| 产物文件 | 说明 |
|---|---|
| `backend/tests/benchmark/profile_ab_metrics.json` | A/B/CD 小样本门禁检索指标 |
| `backend/tests/benchmark/profile_abcd_real_metrics.json` | A/B/C/D 真实运行检索指标 |
| `backend/tests/benchmark/write_guard_quality_metrics.json` | Write Guard 准确率 |
| `backend/tests/benchmark/intent_accuracy_metrics.json` | Intent 分类准确率 |
| `backend/tests/benchmark/compact_context_gist_quality_metrics.json` | Gist 质量（ROUGE-L） |

> 数据生成时间：`2026-02-19T06:55:30+00:00`（门禁）/ `2026-02-18T21:22:48+00:00`（真实运行）

---

## 2. 检索评测（A/B/CD 小样本门禁）

**来源**：`profile_ab_metrics.json`（`sample_size=100`，每档 3 个数据集 × 100 条查询）

| 档位 | 模式 | 数据集 | HR@10 | MRR | NDCG@10 | Recall@10 | p50(ms) | p95(ms) | 降级率 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | keyword | MS MARCO | 0.333 | 0.333 | 0.333 | 0.333 | 1.2 | 2.1 | 0.0% |
| A | keyword | BEIR NFCorpus | 0.300 | 0.300 | 0.300 | 0.300 | 1.6 | 2.6 | 0.0% |
| A | keyword | SQuAD v2 Dev | 0.150 | 0.150 | 0.150 | 0.150 | 1.2 | 3.0 | 0.0% |
| B | hybrid | MS MARCO | 0.867 | 0.658 | 0.696 | 0.850 | 3.4 | 3.7 | 0.0% |
| B | hybrid | BEIR NFCorpus | 1.000 | 0.828 | 0.850 | 0.975 | 4.1 | 4.7 | 0.0% |
| B | hybrid | SQuAD v2 Dev | 1.000 | 0.765 | 0.822 | 1.000 | 3.2 | 3.9 | 0.0% |
| CD | hybrid | （与 B 同配置基线） | 同 B | 同 B | 同 B | 同 B | 同 B | 同 B | 0.0% |

> **说明**：CD 门禁使用与 B 相同的 hash embedding 基线运行，目的是验证 hybrid 检索路径的正确性。

---

## 3. 检索评测（真实 A/B/C/D 运行）

**来源**：`profile_abcd_real_metrics.json`（`sample_size_requested=8`，2 个数据集 × 8 条查询）

策略：每条查询按 `first_relevant_only=true` 仅保留首个相关文档，额外灌入 `10` 条干扰文档，随机种子 `20260219`。

| 档位 | 数据集 | HR@10 | MRR | NDCG@10 | p50(ms) | p95(ms) | Gate |
|---|---|---:|---:|---:|---:|---:|---|
| A | SQuAD v2 Dev | 0.000 | 0.000 | 0.000 | 1.056 | 1.782 | ✅ PASS |
| A | BEIR NFCorpus | 0.250 | 0.250 | 0.250 | 1.036 | 1.743 | ✅ PASS |
| B | SQuAD v2 Dev | 0.625 | 0.302 | 0.383 | 3.999 | 4.915 | ✅ PASS |
| B | BEIR NFCorpus | 0.750 | 0.478 | 0.542 | 4.709 | 5.025 | ✅ PASS |
| C | SQuAD v2 Dev | 1.000 | 1.000 | 1.000 | 385.840 | 665.143 | ✅ PASS |
| C | BEIR NFCorpus | 0.750 | 0.567 | 0.611 | 381.547 | 454.422 | ✅ PASS |
| D | SQuAD v2 Dev | 1.000 | 1.000 | 1.000 | 1599.139 | 2078.378 | ✅ PASS |
| D | BEIR NFCorpus | 0.750 | 0.650 | 0.673 | 1826.662 | 2364.969 | ✅ PASS |

> **说明**：
>
> - C/D 为真实外部 embedding + reranker 链路调用，延迟显著高于本地 keyword/hash 档位。
> - C 与 D 使用相同检索算法，差异来源于模型服务配置与网络延迟。
> - 所有 Gate 均为 PASS，表明各档位在其适用场景下工作正常。

![检索质量与延迟对比图（A:B:C:D）](images/检索质量与延迟对比图（A:B:C:D）.png)

---

## 4. 质量门禁（语义相关）

### Write Guard（写入守卫）

**来源**：`write_guard_quality_metrics.json`

| 指标 | 值 | 阈值 | 状态 |
|---|---:|---:|---|
| Precision | 1.000 | ≥ 0.90 | ✅ PASS |
| Recall | 1.000 | ≥ 0.85 | ✅ PASS |

- 总测试用例数：**6**（TP=4, FP=0, FN=0）
- 决策类型分布：`NOOP`×2, `UPDATE`×2, `ADD`×2
- 综合判定：**overall_pass = true**

### Intent 分类（查询意图识别）

**来源**：`intent_accuracy_metrics.json`

| 指标 | 值 | 阈值 | 状态 |
|---|---:|---:|---|
| Accuracy | 1.000 | ≥ 0.80 | ✅ PASS |

- 总测试用例数：**6**
- 分类方法：`keyword_scoring_v2`（纯规则，无外部模型依赖）
- 覆盖意图：`temporal`×2, `causal`×2, `exploratory`×1, `factual`×1
- 策略模板映射：
  - `temporal` → `temporal_time_filtered`
  - `causal` → `causal_wide_pool`
  - `exploratory` → `exploratory_high_recall`
  - `factual` → `factual_high_precision`

### Gist 质量（上下文压缩摘要）

**来源**：`compact_context_gist_quality_metrics.json`

| 指标 | 值 | 阈值 | 状态 |
|---|---:|---:|---|
| ROUGE-L（均值） | 0.759 | ≥ 0.40 | ✅ PASS |

- 总测试用例数：**5**
- 各 case ROUGE-L 分布：

| Case | ROUGE-L |
|---|---:|
| gist-001 | 0.824 |
| gist-002 | 0.923 |
| gist-003 | 0.667 |
| gist-004 | 0.667 |
| gist-005 | 0.714 |

---

## 5. 如何复现

### 全量基准测试

```bash
cd backend
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pytest tests/benchmark -q
```

### 定向门禁测试

```bash
# A/B/CD 小样本门禁
pytest tests/benchmark/test_benchmark_public_datasets_profiles.py -q -k small_gate

# 检索契约回归
pytest tests/benchmark/test_search_memory_contract_regression.py -q

# 真实 A/B/C/D 运行（需配置 embedding/reranker API）
pytest tests/benchmark/test_profile_abcd_real_runner.py -q

# Write Guard 质量门禁
pytest tests/benchmark/test_write_guard_quality_metrics.py -q

# Intent 分类准确率
pytest tests/benchmark/test_intent_accuracy_metrics.py -q

# Gist 质量门禁
pytest tests/benchmark/test_compact_context_gist_quality.py -q
```

---

## 6. 结果解读与档位选择建议

| 档位 | 适用场景 | 优势 | 注意事项 |
|---|---|---|---|
| A | 低配环境、先跑通验证 | 延迟极低（p95 < 3ms） | 仅关键词匹配，语义召回有限 |
| B | 单机开发、日常调试 | 质量/性能平衡好（HR@10 可达 1.0，p95 < 5ms） | 使用本地 hash embedding，精度有上限 |
| C | 本地/私有模型服务优先 | 高质量检索（SQuAD HR@10=1.0） | 需接受模型调用延迟（p95 ~500-700ms） |
| D | API-first / 远程服务优先 | 最高检索质量 | 延迟最高（p95 ~2000ms+），受网络影响 |

> **上线建议**：固定一套 profile + 模型配置，长期追踪同一指标口径，避免跨档位混合比较。
