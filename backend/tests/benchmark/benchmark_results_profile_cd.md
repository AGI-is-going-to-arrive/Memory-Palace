# Benchmark Results - profile_cd

> generated_at_utc: 2026-02-19T06:55:30+00:00
> mode: hybrid

## Retrieval Quality

| Dataset | HR@5 | HR@10 | MRR | NDCG@10 | Recall@10 |
|---|---:|---:|---:|---:|---:|
| MS MARCO | 0.833 | 0.867 | 0.658 | 0.696 | 0.850 |
| BEIR NFCorpus | 0.950 | 1.000 | 0.828 | 0.850 | 0.975 |
| SQuAD v2 Dev | 0.850 | 1.000 | 0.765 | 0.822 | 1.000 |

## Latency (ms)

| Dataset | p50 | p95 | p99 |
|---|---:|---:|---:|
| MS MARCO | 3.4 | 3.7 | 3.7 |
| BEIR NFCorpus | 4.1 | 4.7 | 4.7 |
| SQuAD v2 Dev | 3.2 | 3.9 | 3.9 |

## Degradation

| Dataset | Queries | Degraded | Rate |
|---|---:|---:|---:|
| MS MARCO | 100 | 0 | 0.0% |
| BEIR NFCorpus | 100 | 0 | 0.0% |
| SQuAD v2 Dev | 100 | 0 | 0.0% |

## Contract

- json_artifact: `backend/tests/benchmark/profile_ab_metrics.json`
- source: `backend/tests/benchmark_results.md`
- memory_gold_set: `backend/tests/fixtures/memory_gold_set.jsonl`


## Phase 6 Gate

- overall_valid: true
- invalid_reasons: (none)

| Dataset | Valid | Invalid Reasons |
|---|---|---|
| MS MARCO | PASS | - |
| BEIR NFCorpus | PASS | - |
| SQuAD v2 Dev | PASS | - |

## A/B/CD Comparison

| Dataset | A HR@10 | B HR@10 | C/D HR@10 | A MRR | B MRR | C/D MRR | A p95 | B p95 | C/D p95 | C/D Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| MS MARCO | 0.333 | 0.867 | 0.867 | 0.333 | 0.658 | 0.658 | 2.1 | 3.7 | 3.7 | PASS |
| BEIR NFCorpus | 0.300 | 1.000 | 1.000 | 0.300 | 0.828 | 0.828 | 2.6 | 4.7 | 4.7 | PASS |
| SQuAD v2 Dev | 0.150 | 1.000 | 1.000 | 0.150 | 0.765 | 0.765 | 3.0 | 3.9 | 3.9 | PASS |