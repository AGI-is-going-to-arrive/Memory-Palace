# Benchmark Results - phase_d_spike

> generated_at_utc: 2026-03-02T05:27:49+00:00

## Scope

- phase: D
- focus: embedding provider routing / sqlite-vec compatibility / write lane WAL
- sqlite_vec_readiness: ready
- wal_load_profile: business_write_peak
- wal_repeat: 2

## Probe Status

| Probe | Status |
|---|---|
| embedding_provider | ok |
| sqlite_vec | ok |
| write_lane_wal | degraded |

## Embedding Cases

| Backend | API Base Source | API Base Present | Status |
|---|---|---|---|
| hash | unset | no | not_required |
| api | unset | no | missing_api_base |
| router | unset | no | missing_api_base |
| openai | unset | no | missing_api_base |
| none | unset | no | not_required |

## Write Lane Throughput

| Mode | Throughput (tx/s) | Success/Planned | Failed | Failure Rate | Persistence Gap |
|---|---:|---:|---:|---:|---:|
| DELETE | 1417.239 | 5252/5280 | 28 | 0.005303 | 0 |
| WAL | 2126.207 | 5260/5280 | 20 | 0.003788 | 0 |
- wal_vs_delete_throughput_ratio: 1.5
- wal_regression_gate_pass: True

## WAL Threshold Suggestion

- profile_baseline: {'min_throughput_ratio': 1.02, 'max_failure_rate': 0.005, 'max_persistence_gap': 0}
- suggested_stable_thresholds: {'min_throughput_ratio': 1.02, 'max_failure_rate': 0.005, 'max_persistence_gap': 0}

## Go/No-Go

- decision: NO_GO
- summary: Spike produced blockers; keep HOLD and do not promote to default path.

## Risks

- Write lane probe in DELETE mode observed failed transactions.
- Write lane probe in WAL mode observed failed transactions.

## Rollback Points

- Keep RETRIEVAL_EMBEDDING_BACKEND at hash/none when provider probe is not ready.
- Do not configure sqlite-vec extension path in production startup until compatibility is validated.
- If WAL write lane degrades consistency, switch journal mode back to DELETE.
- If browse read behavior must rollback, restore browse.get_node -> get_memory_by_path(... reinforce_access=True).
