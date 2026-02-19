# Benchmark Results - profile_a

> generated_at_utc: 2026-02-19T06:55:30+00:00
> mode: keyword

## Retrieval Quality

| Dataset | HR@5 | HR@10 | MRR | NDCG@10 | Recall@10 |
|---|---:|---:|---:|---:|---:|
| MS MARCO | 0.333 | 0.333 | 0.333 | 0.333 | 0.333 |
| BEIR NFCorpus | 0.300 | 0.300 | 0.300 | 0.300 | 0.300 |
| SQuAD v2 Dev | 0.150 | 0.150 | 0.150 | 0.150 | 0.150 |

## Latency (ms)

| Dataset | p50 | p95 | p99 |
|---|---:|---:|---:|
| MS MARCO | 1.2 | 2.1 | 2.8 |
| BEIR NFCorpus | 1.6 | 2.6 | 2.6 |
| SQuAD v2 Dev | 1.2 | 3.0 | 3.0 |

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
