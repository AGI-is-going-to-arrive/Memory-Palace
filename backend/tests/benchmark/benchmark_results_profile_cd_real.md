# Benchmark Results - profile_cd_real

> generated_at_utc: 2026-03-03T10:25:44+00:00
> mode: real API embedding/reranker execution

## profile_c

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 1 | 21 | 1.000 | 1.000 | 1.000 | 1.000 | 3167.1 | 100.0% | embedding_fallback_hash,embedding_request_failed |

## profile_d

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 1 | 21 | 1.000 | 1.000 | 1.000 | 1.000 | 1732.1 | 0.0% | - |

## Phase 6 Gate

- overall_valid: true
- invalid_reasons: (none)
