from pathlib import Path

from scripts.phase_d_spike_runner import (
    _lock_retry_delay_sec,
    _build_hold_gate_11_from_profile_metrics,
    _build_hold_gate_12_from_vec_isolation_metrics,
    _build_hold_gate_13_from_wal_probe,
    _build_wal_regression_gate,
    _build_go_no_go,
    build_phase_d_report,
    run_embedding_provider_probe,
    run_sqlite_vec_probe,
    run_write_lane_wal_probe,
)


def test_embedding_provider_probe_returns_cases_with_backend_and_api_base_source() -> None:
    payload = run_embedding_provider_probe()
    cases = payload.get("cases")
    assert isinstance(cases, list)
    assert cases

    for case in cases:
        assert "backend" in case
        assert "api_base_source" in case
        assert "backend/api_base_source" in case


def test_sqlite_vec_probe_without_extension_path_returns_conservative_hold() -> None:
    payload = run_sqlite_vec_probe(sqlite_vec_extension_path=None)
    assert payload["status"] == "skipped_no_extension_path"
    assert payload["extension_loaded"] is False
    assert payload["extension_load_attempted"] is False
    assert payload["sqlite_vec_readiness"] == "hold"
    assert payload["verification_level"] == "runtime_only"
    assert "sqlite_version" in payload
    assert isinstance(payload.get("checks"), list)
    assert any(
        item.get("name") == "extension_load"
        and item.get("reason") == "path_not_provided"
        for item in payload.get("checks", [])
        if isinstance(item, dict)
    )


def test_sqlite_vec_probe_invalid_extension_path_returns_actionable_diagnostics(
    tmp_path: Path,
) -> None:
    invalid_extension_path = tmp_path / "sqlite_vec_missing_extension.dylib"
    payload = run_sqlite_vec_probe(sqlite_vec_extension_path=str(invalid_extension_path))

    assert payload["status"] == "invalid_extension_path"
    assert payload["diag_code"] == "path_not_found"
    assert payload["extension_load_attempted"] is False
    assert payload["extension_path_exists"] is False
    assert payload["sqlite_vec_readiness"] == "hold"
    assert payload["errors"]
    assert str(payload["errors"][0]).startswith("invalid_extension_path:path_not_found")


def test_sqlite_vec_probe_resolves_platform_suffix_and_attempts_load(
    tmp_path: Path,
) -> None:
    fake_extension_file = tmp_path / "fake_sqlite_vec.dylib"
    fake_extension_file.write_bytes(b"not-a-real-extension")
    payload = run_sqlite_vec_probe(
        sqlite_vec_extension_path=str(tmp_path / "fake_sqlite_vec")
    )

    assert payload["status"] in {"extension_load_failed", "ok"}
    assert payload["extension_load_attempted"] is True
    assert payload["extension_path"].endswith(".dylib")
    assert payload["extension_path_exists"] is True


def test_sqlite_vec_probe_prefers_extension_file_over_same_name_directory(
    tmp_path: Path,
) -> None:
    base = tmp_path / "sqlite_vec"
    base.mkdir()
    fake_extension_file = tmp_path / "sqlite_vec.dylib"
    fake_extension_file.write_bytes(b"not-a-real-extension")

    payload = run_sqlite_vec_probe(sqlite_vec_extension_path=str(base))

    assert payload["diag_code"] != "path_not_file"
    assert payload["status"] in {
        "extension_load_failed",
        "extension_loading_unavailable",
        "sqlite_runtime_error",
        "ok",
    }
    assert str(payload.get("extension_path", "")).endswith(".dylib")


def test_write_lane_wal_probe_returns_regression_metrics_and_gate() -> None:
    payload = run_write_lane_wal_probe(
        workers=2,
        tx_per_worker=5,
        timeout_sec=0.05,
        load_profile="small",
        repeat=2,
        min_throughput_ratio=0.0,
        max_failure_rate=1.0,
        max_persistence_gap=1000,
    )
    assert payload["status"] in {"ok", "degraded"}
    assert payload["load_profile"] == "small"
    assert payload["repeat"] == 2
    assert "regression_gate" in payload

    results = payload.get("results")
    assert isinstance(results, dict)
    assert "delete" in results
    assert "wal" in results

    for mode in ("delete", "wal"):
        metrics = results[mode]
        assert "throughput_tps" in metrics
        assert float(metrics["throughput_tps"]) >= 0.0
        assert "failure_rate" in metrics
        assert 0.0 <= float(metrics["failure_rate"]) <= 1.0
        assert "retry_rate" in metrics
        assert float(metrics["retry_rate"]) >= 0.0
        assert "persistence_gap" in metrics
        assert "sample_count" in metrics
        assert int(metrics["sample_count"]) == 2

    summary = payload.get("summary")
    assert isinstance(summary, dict)
    for mode in ("delete", "wal"):
        mode_summary = summary.get(mode, {})
        assert "throughput_tps_p50" in mode_summary
        assert "throughput_tps_p95" in mode_summary

    threshold_suggestion = payload.get("threshold_suggestion")
    assert isinstance(threshold_suggestion, dict)
    assert "profile_baseline" in threshold_suggestion
    assert "suggested_stable_thresholds" in threshold_suggestion


def test_write_lane_wal_probe_medium_profile_uses_profile_defaults() -> None:
    payload = run_write_lane_wal_probe(
        load_profile="medium",
        repeat=1,
        min_throughput_ratio=0.0,
        max_failure_rate=1.0,
        max_persistence_gap=1000,
    )
    assert payload["load_profile"] == "medium"
    assert int(payload["workers"]) == 6
    assert int(payload["tx_per_worker"]) == 120


def test_write_lane_wal_probe_business_write_profile_exposes_threshold_suggestion() -> None:
    payload = run_write_lane_wal_probe(load_profile="business_write_burst", repeat=1)
    assert payload["load_profile"] == "business_write_burst"
    assert int(payload["workers"]) == 8
    assert int(payload["tx_per_worker"]) == 160

    thresholds = payload.get("regression_thresholds", {})
    assert isinstance(thresholds, dict)
    assert float(thresholds.get("min_throughput_ratio", 0.0)) >= 1.0
    assert float(thresholds.get("max_failure_rate", -1.0)) >= 0.0
    assert float(thresholds.get("max_retry_rate", -1.0)) >= 0.0

    suggestion = payload.get("threshold_suggestion", {})
    assert isinstance(suggestion, dict)
    suggested = suggestion.get("suggested_stable_thresholds", {})
    assert isinstance(suggested, dict)
    assert "min_throughput_ratio" in suggested
    assert "max_failure_rate" in suggested
    assert "max_retry_rate" in suggested
    assert "max_persistence_gap" in suggested


def test_phase_d_report_marks_sqlite_vec_not_verified_as_hold() -> None:
    report = build_phase_d_report(
        sqlite_vec_extension_path=None,
        workers=1,
        tx_per_worker=1,
        timeout_sec=0.01,
        wal_repeat=1,
        wal_min_throughput_ratio=0.0,
        wal_max_failure_rate=1.0,
        wal_max_persistence_gap=10,
        write_artifacts=False,
    )
    sqlite_probe = report.get("probes", {}).get("sqlite_vec", {})
    assert sqlite_probe.get("sqlite_vec_readiness") == "hold"
    risks = report.get("risks", [])
    assert isinstance(risks, list)
    assert any("sqlite-vec" in str(item) for item in risks)


def test_go_no_go_blocks_when_wal_regression_gate_fails() -> None:
    decision = _build_go_no_go(
        embedding_probe={
            "configured_backend": "hash",
            "cases": [{"backend": "hash", "status": "ok"}],
        },
        sqlite_vec_probe={"status": "ok"},
        wal_probe={
            "status": "degraded",
            "results": {"wal": {"failed_tx": 0}},
            "regression_gate": {
                "pass": False,
                "reasons": ["wal_throughput_ratio_below_threshold:0.900<1.020"],
            },
        },
    )

    assert decision["decision"] == "NO_GO"
    assert "wal_regression_gate_failed" in decision["blockers"]


def test_go_no_go_allows_when_gate_passes_and_no_wal_failed_transactions() -> None:
    decision = _build_go_no_go(
        embedding_probe={
            "configured_backend": "hash",
            "cases": [{"backend": "hash", "status": "ok"}],
        },
        sqlite_vec_probe={"status": "ok"},
        wal_probe={
            "status": "degraded",
            "results": {
                "delete": {"failed_tx": 0},
                "wal": {"failed_tx": 0, "effective_journal_mode": "wal"},
            },
            "regression_gate": {"pass": True, "reasons": []},
        },
    )

    assert decision["decision"] == "GO"
    assert decision["blockers"] == []


def test_go_no_go_blocks_when_hold_gate_12_fails() -> None:
    decision = _build_go_no_go(
        embedding_probe={
            "configured_backend": "hash",
            "cases": [{"backend": "hash", "status": "ok"}],
        },
        sqlite_vec_probe={"status": "ok"},
        wal_probe={
            "status": "degraded",
            "results": {
                "delete": {"failed_tx": 0},
                "wal": {"failed_tx": 0, "effective_journal_mode": "wal"},
            },
            "regression_gate": {"pass": True, "reasons": []},
        },
        hold_gate={
            "gate_11": {"overall_pass": True},
            "gate_12": {"overall_pass": False},
            "gate_13": {"overall_pass": True},
        },
    )

    assert decision["decision"] == "NO_GO"
    assert "gate_12_failed" in decision["blockers"]


def test_go_no_go_blocks_when_wal_mode_is_not_effective() -> None:
    decision = _build_go_no_go(
        embedding_probe={
            "configured_backend": "hash",
            "cases": [{"backend": "hash", "status": "ok"}],
        },
        sqlite_vec_probe={"status": "ok"},
        wal_probe={
            "status": "degraded",
            "results": {
                "delete": {"failed_tx": 0},
                "wal": {"failed_tx": 0, "effective_journal_mode": "delete"},
            },
            "regression_gate": {"pass": True, "reasons": []},
        },
    )

    assert decision["decision"] == "NO_GO"
    assert "wal_not_effective" in decision["blockers"]


def test_go_no_go_allows_when_only_delete_has_failed_transactions() -> None:
    decision = _build_go_no_go(
        embedding_probe={
            "configured_backend": "hash",
            "cases": [{"backend": "hash", "status": "ok"}],
        },
        sqlite_vec_probe={"status": "ok"},
        wal_probe={
            "status": "degraded",
            "results": {
                "delete": {"failed_tx": 3},
                "wal": {"failed_tx": 0, "effective_journal_mode": "wal"},
            },
            "regression_gate": {"pass": True, "reasons": []},
        },
    )

    assert decision["decision"] == "GO"
    assert decision["blockers"] == []


def test_go_no_go_blocks_when_wal_has_failures_even_if_gate_passes() -> None:
    decision = _build_go_no_go(
        embedding_probe={
            "configured_backend": "hash",
            "cases": [{"backend": "hash", "status": "ok"}],
        },
        sqlite_vec_probe={"status": "ok"},
        wal_probe={
            "status": "degraded",
            "results": {
                "delete": {"failed_tx": 4},
                "wal": {"failed_tx": 2, "effective_journal_mode": "wal"},
            },
            "regression_gate": {"pass": True, "reasons": []},
        },
    )

    assert decision["decision"] == "NO_GO"
    assert "wal_failed_transactions" in decision["blockers"]


def test_go_no_go_blocks_when_wal_fails_and_gate_payload_missing() -> None:
    decision = _build_go_no_go(
        embedding_probe={
            "configured_backend": "hash",
            "cases": [{"backend": "hash", "status": "ok"}],
        },
        sqlite_vec_probe={"status": "ok"},
        wal_probe={
            "status": "degraded",
            "results": {
                "delete": {"failed_tx": 0},
                "wal": {"failed_tx": 1, "effective_journal_mode": "wal"},
            },
        },
    )

    assert decision["decision"] == "NO_GO"
    assert "wal_failed_transactions" in decision["blockers"]


def test_wal_regression_gate_blocks_when_retry_rate_exceeds_threshold() -> None:
    gate = _build_wal_regression_gate(
        delete_metrics={"persistence_gap": 0},
        wal_metrics={"failure_rate": 0.0, "retry_rate": 0.02, "persistence_gap": 0},
        wal_gain=1.2,
        min_throughput_ratio=1.0,
        max_failure_rate=0.001,
        max_retry_rate=0.01,
        max_persistence_gap=0,
    )

    assert gate["pass"] is False
    assert any("wal_retry_rate_exceeded" in reason for reason in gate["reasons"])


def test_hold_gate_11_collects_embedding_success_and_fallback_rates() -> None:
    payload = _build_hold_gate_11_from_profile_metrics(
        {
            "profiles": {
                "profile_cd": {
                    "rows": [
                        {
                            "dataset": "alpha",
                            "degradation": {
                                "queries": 100,
                                "degraded": 2,
                                "degrade_reasons": [
                                    "embedding_request_failed",
                                    "embedding_fallback_hash",
                                ],
                                "degrade_reason_counts": {
                                    "embedding_request_failed": 2,
                                    "embedding_fallback_hash": 1,
                                },
                            },
                        },
                        {
                            "dataset": "beta",
                            "degradation": {
                                "queries": 100,
                                "degraded": 0,
                                "degrade_reasons": [],
                                "degrade_reason_counts": {},
                            },
                        },
                    ]
                }
            }
        }
    )
    assert payload["status"] == "ok"
    assert payload["query_count"] == 200
    assert payload["embedding_request_failed_queries"] == 2
    assert payload["embedding_fallback_hash_queries"] == 1
    assert payload["embedding_success_rate"] == 0.99
    assert payload["embedding_fallback_hash_rate"] == 0.005
    assert payload["search_degraded_rate"] == 0.01
    assert payload["checks"]["embedding_success_rate"] is False
    assert payload["checks"]["embedding_fallback_hash_rate"] is True
    assert payload["checks"]["search_degraded_rate"] is True
    assert payload["overall_pass"] is False


def test_hold_gate_12_builds_latency_and_quality_comparison() -> None:
    payload = _build_hold_gate_12_from_vec_isolation_metrics(
        {
            "status": "ok",
            "path": "/tmp/profile_vec_isolation_metrics_v2.json",
            "source": "primary",
            "payload": {
                "runs": [
                    {
                        "repeats": [
                            {
                                "rows": [
                                    {
                                        "dataset": "alpha",
                                        "dataset_label": "Alpha",
                                        "b_p95": 10.0,
                                        "c_p95": 7.0,
                                        "latency_improvement_ratio": 0.3,
                                        "ndcg_delta": 0.0,
                                        "recall_delta": 0.0,
                                        "c_degrade_reasons": [],
                                        "c_valid": True,
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
        },
        sqlite_vec_probe={"status": "ok", "extension_loaded": True},
    )
    assert payload["status"] == "ok"
    assert payload["row_count"] == 1
    assert payload["rows"][0]["b_p95"] == 10.0
    assert payload["rows"][0]["c_p95"] == 7.0
    assert payload["rows"][0]["latency_improvement_ratio"] == 0.3
    assert payload["rows"][0]["latency_pass"] is True
    assert payload["rows"][0]["quality_pass"] is True
    assert payload["checks"]["extension_ready"] is True
    assert payload["checks"]["latency_improvement_gate"] is True
    assert payload["checks"]["quality_non_regression_gate"] is True
    assert payload["checks"]["rollback_ready"] is True
    assert payload["overall_pass"] is True


def test_hold_gate_12_fails_closed_when_vec_isolation_artifact_missing() -> None:
    payload = _build_hold_gate_12_from_vec_isolation_metrics(
        {
            "status": "missing",
            "path": "/tmp/missing_profile_vec_isolation_metrics_v2.json",
            "payload": {},
        },
        sqlite_vec_probe={"status": "ok", "extension_loaded": True},
    )
    assert payload["status"] == "vec_isolation_artifact_missing"
    assert payload["vec_isolation_status"] == "missing"
    assert payload["checks"]["extension_ready"] is True
    assert payload["checks"]["no_new_500_proxy"] is False
    assert payload["checks"]["latency_improvement_gate"] is False
    assert payload["checks"]["quality_non_regression_gate"] is False
    assert payload["overall_pass"] is False


def test_hold_gate_13_uses_wal_thresholds() -> None:
    payload = _build_hold_gate_13_from_wal_probe(
        {
            "wal_vs_delete_throughput_ratio": 1.6,
            "results": {
                "wal": {
                    "failed_tx": 0,
                    "failure_rate": 0.0,
                    "persistence_gap": 0,
                    "retry_rate": 0.008,
                }
            },
            "summary": {"wal": {"retry_rate_p95": 0.009}},
        }
    )
    assert payload["status"] == "ok"
    assert payload["wal_failed_tx"] == 0
    assert payload["wal_failure_rate"] == 0.0
    assert payload["retry_rate_p95"] == 0.009
    assert payload["wal_vs_delete_tps_ratio"] == 1.6
    assert payload["overall_pass"] is True


def test_lock_retry_delay_is_deterministic_and_capped() -> None:
    first = _lock_retry_delay_sec(worker_id=2, seq=5, attempt=3)
    second = _lock_retry_delay_sec(worker_id=2, seq=5, attempt=3)
    assert first == second
    assert first >= 0.0
    assert first <= 0.035

    early = _lock_retry_delay_sec(worker_id=0, seq=0, attempt=0)
    later = _lock_retry_delay_sec(worker_id=0, seq=0, attempt=2)
    assert later >= early
