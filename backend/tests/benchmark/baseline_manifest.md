# Benchmark Baseline Manifest (Phase 0)

> Scope: `nocturne_memory/backend/tests`
> Frozen by: benchmark phase-0 bootstrap
> Source metrics: `backend/tests/benchmark_results.md`
> Source contracts: `backend/mcp_server.py`, `backend/db/sqlite_client.py`

## Frozen Baseline Snapshot (2026-02-17)

### Retrieval quality baseline (from `benchmark_results.md`)

| Dataset | Metric | keyword | semantic | hybrid |
|---|---|---:|---:|---:|
| MS MARCO | HR@10 | 0.333 | 0.867 | 0.867 |
| MS MARCO | MRR | 0.333 | 0.624 | 0.658 |
| MS MARCO | NDCG@10 | 0.333 | 0.671 | 0.696 |
| BEIR NFCorpus | HR@10 | 0.300 | 1.000 | 1.000 |
| BEIR NFCorpus | MRR | 0.300 | 0.770 | 0.828 |
| BEIR NFCorpus | NDCG@10 | 0.300 | 0.807 | 0.850 |
| SQuAD v2 Dev | HR@10 | 0.150 | 1.000 | 1.000 |
| SQuAD v2 Dev | MRR | 0.150 | 0.757 | 0.765 |
| SQuAD v2 Dev | NDCG@10 | 0.150 | 0.815 | 0.822 |

### Latency baseline p95(ms) (from `benchmark_results.md`)

| Dataset | keyword | semantic | hybrid |
|---|---:|---:|---:|
| MS MARCO | 2.1 | 3.1 | 3.7 |
| BEIR NFCorpus | 2.6 | 6.0 | 4.7 |
| SQuAD v2 Dev | 3.0 | 3.9 | 3.9 |

### Stability baseline (from `benchmark_results.md`)

| Dataset | keyword degrade rate | semantic degrade rate | hybrid degrade rate |
|---|---:|---:|---:|
| MS MARCO | 0.0% | 0.0% | 0.0% |
| BEIR NFCorpus | 0.0% | 0.0% | 0.0% |
| SQuAD v2 Dev | 0.0% | 0.0% | 0.0% |

## Threshold Contract v1

Threshold definition file: `backend/tests/benchmark/thresholds_v1.json`

- `profile_cd.p95_ms_lt`: 2000
- `global.degrade_rate_lt`: 0.05
- `write_guard.precision_gte`: 0.90
- `write_guard.recall_gte`: 0.85
- `intent.accuracy_gte`: 0.80
- `gist.rouge_l_gte`: 0.40

## MCP/API Contract Lock

### `search_memory` response must contain

- `ok`
- `query`
- `query_effective`
- `mode_requested`
- `mode_applied`
- `results`
- `degraded`

### `compact_context` response must contain

- `ok`
- `session_id`
- `reason`
- `flushed`
- `gist_method`
- `quality`
- `source_hash`

### `write_guard` decision must contain

- `action`
- `reason`
- `method`
- `degraded`
- `degrade_reasons`

## Freeze Rule

During the same phase, do not change both implementation code and benchmark gold set at once.
