from pathlib import Path

from scripts.phase_d_spike_runner import (
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

    suggestion = payload.get("threshold_suggestion", {})
    assert isinstance(suggestion, dict)
    suggested = suggestion.get("suggested_stable_thresholds", {})
    assert isinstance(suggested, dict)
    assert "min_throughput_ratio" in suggested
    assert "max_failure_rate" in suggested
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
