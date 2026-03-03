# Benchmark Results - phase_d_spike

> generated_at_utc: 2026-03-03T09:29:39+00:00

## Scope

- phase: D
- focus: embedding provider routing / sqlite-vec compatibility / write lane WAL
- sqlite_vec_readiness: ready
- wal_load_profile: business_write_peak
- wal_repeat: 3

## Probe Status

| Probe | Status |
|---|---|
| embedding_provider | ok |
| sqlite_vec | ok |
| write_lane_wal | ok |

## Embedding Cases

| Backend | API Base Source | API Base Present | Status |
|---|---|---|---|
| hash | unset | no | not_required |
| api | unset | no | missing_api_base |
| router | unset | no | missing_api_base |
| openai | unset | no | missing_api_base |
| none | unset | no | not_required |

## Write Lane Throughput

| Mode | Throughput (tx/s) | Success/Planned | Failed | Failure Rate | Retry Rate | Persistence Gap |
|---|---:|---:|---:|---:|---:|---:|
| DELETE | 2639.7 | 7920/7920 | 0 | 0.0 | 0.003662 | 0 |
| WAL | 10941.1 | 7920/7920 | 0 | 0.0 | 0.001389 | 0 |
- wal_vs_delete_throughput_ratio: 4.145
- wal_regression_gate_pass: True

## WAL Threshold Suggestion

- profile_baseline: {'min_throughput_ratio': 1.02, 'max_failure_rate': 0.005, 'max_retry_rate': 0.01, 'max_persistence_gap': 0}
- suggested_stable_thresholds: {'min_throughput_ratio': 1.02, 'max_failure_rate': 0.005, 'max_retry_rate': 0.01, 'max_persistence_gap': 0}

## HOLD Gate Snapshot (#11/#12/#13)

- source_profile_metrics: /Users/yangjunjie/Desktop/clawanti/Memory-Palace/backend/tests/benchmark/profile_ab_metrics.json
- source_profile_metrics_status: ok
- #11: embedding_success_rate=1.0, embedding_fallback_hash_rate=0.0, search_degraded_rate=0.0, overall_pass=True
- #12: extension_ready=True, latency_gate=False, quality_gate=True, no_new_500_proxy=True, overall_pass=False
- #13: wal_failed_tx=0, wal_failure_rate=0.0, retry_rate_p95=0.001818, persistence_gap=0, wal_vs_delete_tps_ratio=4.145, overall_pass=True

## Go/No-Go

- decision: GO
- summary: Spike baseline is executable. Continue with default-off feature flags.

## Risks

- No blocker detected in current spike scope; keep feature flags default-off.

## Rollback Points

- Keep RETRIEVAL_EMBEDDING_BACKEND at hash/none when provider probe is not ready.
- Do not configure sqlite-vec extension path in production startup until compatibility is validated.
- If WAL write lane degrades consistency, switch journal mode back to DELETE.
- If browse read behavior must rollback, restore browse.get_node -> get_memory_by_path(... reinforce_access=True).
