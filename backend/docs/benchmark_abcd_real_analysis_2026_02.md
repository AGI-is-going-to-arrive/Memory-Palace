# A/B/C/D Benchmark Analysis vs 2026-02 Reference

- generated_at_utc: 2026-03-03T10:25:44+00:00
- dataset_scope: squad_v2_dev
- sample_size_requested: 1

## Profile Means (dataset-average)

| Profile | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) |
|---|---:|---:|---:|---:|---:|
| profile_a | 0.000 | 0.000 | 0.000 | 0.000 | 2.2 |
| profile_b | 1.000 | 1.000 | 1.000 | 1.000 | 5.5 |
| profile_c | 1.000 | 1.000 | 1.000 | 1.000 | 3167.1 |
| profile_d | 1.000 | 1.000 | 1.000 | 1.000 | 1732.1 |

## 2026-02 Reference Ranges (from project docs)

| Reference | Avg NDCG@10 |
|---|---:|
| BM25 baseline | ~0.43 |
| Dense retrieval | ~0.48–0.52 |
| Hybrid (embedding+BM25) | ~0.54–0.56 |
| Hybrid + reranker | ~0.58–0.62 |
| SOTA floor | ~0.62+ |

## Positioning

- Profile D dataset-mean NDCG@10: `1.000`
- Relative to hybrid+reranker reference (`~0.58–0.62`): above-range

## Comparability Notes

- 本报告使用项目内可落盘语料（SQuAD + BEIR NFCorpus）的小样本运行，不等价于完整 BEIR 全量评测。
- query relevance 采用 `first_relevant_only=true` 策略以控制真实 API 成本，结果更适合回归比较，不适合外部 SOTA 声明。
- Profile D 的有效性仍由 phase6 gate 判定（`embedding_fallback_hash` / `embedding_request_failed` / `reranker_request_failed`）。
