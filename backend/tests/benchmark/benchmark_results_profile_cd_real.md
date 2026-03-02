# Benchmark Results - profile_cd_real

> generated_at_utc: 2026-03-02T01:34:44+00:00
> mode: real API embedding/reranker execution

## profile_c

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 10 | 90 | 1.000 | 0.834 | 0.872 | 1.000 | 735.8 | 0.0% | - |
| BEIR NFCorpus | 10 | 90 | 0.500 | 0.500 | 0.500 | 0.500 | 577.9 | 0.0% | - |

## profile_d

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 10 | 90 | 1.000 | 0.845 | 0.882 | 1.000 | 2071.7 | 0.0% | - |
| BEIR NFCorpus | 10 | 90 | 0.600 | 0.550 | 0.563 | 0.600 | 2037.1 | 0.0% | - |

## Phase 6 Gate

- overall_valid: true
- invalid_reasons: (none)
