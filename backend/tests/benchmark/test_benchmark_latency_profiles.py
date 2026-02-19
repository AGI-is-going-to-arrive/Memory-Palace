import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from helpers.common import load_thresholds_v1
from helpers.profile_ab_runner import (
    PROFILE_JSON_ARTIFACT,
    PROFILE_MARKDOWN_ARTIFACTS,
    PROFILE_CD_INVALID_GATE_REASONS,
    build_profile_ab_metrics,
    write_profile_ab_artifacts,
)


def _run_profile_ab(
    sample_size: int = 100,
    profile_cd_degrade_reasons_by_dataset: Mapping[str, Sequence[str]] | None = None,
) -> Mapping[str, Any]:
    payload = build_profile_ab_metrics(
        sample_size=sample_size,
        profile_cd_degrade_reasons_by_dataset=profile_cd_degrade_reasons_by_dataset,
    )
    write_profile_ab_artifacts(payload)
    return payload


def _assert_latency_and_degradation_contract(
    row: Dict[str, Any], *, p95_limit: float, degrade_rate_limit: float
) -> None:
    latency = row["latency_ms"]
    assert set(latency) == {"p50", "p95", "p99"}
    p50 = float(latency["p50"])
    p95 = float(latency["p95"])
    p99 = float(latency["p99"])
    assert 0.0 < p50 <= p95 <= p99
    assert p95 < p95_limit

    degradation = row["degradation"]
    queries = int(degradation["queries"])
    degraded = int(degradation["degraded"])
    rate = float(degradation["degrade_rate"])
    assert queries > 0
    assert 0 <= degraded <= queries
    assert 0.0 <= rate <= degrade_rate_limit


def _assert_latency_markdown_row(markdown: str, row: Dict[str, Any]) -> None:
    latency = row["latency_ms"]
    expected = (
        f"| {row['dataset_label']} | {latency['p50']:.1f} | {latency['p95']:.1f} | {latency['p99']:.1f} |"
    )
    assert expected in markdown


def _assert_degradation_markdown_row(markdown: str, row: Dict[str, Any]) -> None:
    degradation = row["degradation"]
    expected = (
        f"| {row['dataset_label']} | {degradation['queries']} | {degradation['degraded']} | "
        f"{degradation['degrade_rate'] * 100:.1f}% |"
    )
    assert expected in markdown


def test_profile_a_latency_report_within_thresholds_and_json_consistent() -> None:
    payload = _run_profile_ab(sample_size=100)
    thresholds = load_thresholds_v1()

    profile_a = payload["profiles"]["profile_a"]
    rows = profile_a["rows"]
    assert len(rows) == 3

    for row in rows:
        _assert_latency_and_degradation_contract(
            row,
            p95_limit=float(thresholds["profile_cd"]["p95_ms_lt"]),
            degrade_rate_limit=float(thresholds["global"]["degrade_rate_lt"]),
        )

    assert PROFILE_JSON_ARTIFACT.exists()
    json_payload = json.loads(PROFILE_JSON_ARTIFACT.read_text(encoding="utf-8"))
    assert json_payload["profiles"]["profile_a"]["mode"] == "keyword"

    markdown_path = PROFILE_MARKDOWN_ARTIFACTS["profile_a"]
    assert markdown_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Latency (ms)" in markdown
    assert "## Degradation" in markdown
    for row in rows:
        _assert_latency_markdown_row(markdown, row)
        _assert_degradation_markdown_row(markdown, row)


def test_profile_b_latency_report_within_thresholds_and_non_negative_degradation() -> None:
    payload = _run_profile_ab(sample_size=100)
    thresholds = load_thresholds_v1()

    profile_a_rows = payload["profiles"]["profile_a"]["rows"]
    profile_b_rows = payload["profiles"]["profile_b"]["rows"]
    assert len(profile_b_rows) == 3

    by_dataset_a = {row["dataset"]: row for row in profile_a_rows}
    for row in profile_b_rows:
        _assert_latency_and_degradation_contract(
            row,
            p95_limit=float(thresholds["profile_cd"]["p95_ms_lt"]),
            degrade_rate_limit=float(thresholds["global"]["degrade_rate_lt"]),
        )
        assert row["latency_ms"]["p95"] >= by_dataset_a[row["dataset"]]["latency_ms"]["p95"]

    markdown_path = PROFILE_MARKDOWN_ARTIFACTS["profile_b"]
    assert markdown_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Latency (ms)" in markdown
    assert "## Degradation" in markdown
    for row in profile_b_rows:
        _assert_latency_markdown_row(markdown, row)
        _assert_degradation_markdown_row(markdown, row)


def test_profile_cd_latency_report_within_thresholds_and_gate_valid() -> None:
    payload = _run_profile_ab(sample_size=100)
    thresholds = load_thresholds_v1()
    profile_cd_rows = payload["profiles"]["profile_cd"]["rows"]
    assert len(profile_cd_rows) == 3

    for row in profile_cd_rows:
        _assert_latency_and_degradation_contract(
            row,
            p95_limit=float(thresholds["profile_cd"]["p95_ms_lt"]),
            degrade_rate_limit=float(thresholds["global"]["degrade_rate_lt"]),
        )
        degradation = row["degradation"]
        assert degradation["valid"] is True
        assert degradation["invalid_reasons"] == []

    phase6_gate = payload["phase6"]["gate"]
    assert phase6_gate["valid"] is True
    assert phase6_gate["invalid_reasons"] == []

    markdown_path = PROFILE_MARKDOWN_ARTIFACTS["profile_cd"]
    assert markdown_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Latency (ms)" in markdown
    assert "## Degradation" in markdown
    assert "## A/B/CD Comparison" in markdown
    for row in profile_cd_rows:
        _assert_latency_markdown_row(markdown, row)
        _assert_degradation_markdown_row(markdown, row)


def test_profile_cd_latency_report_marks_invalid_when_phase6_reasons_hit_gate() -> None:
    payload = _run_profile_ab(
        sample_size=100,
        profile_cd_degrade_reasons_by_dataset={
            "msmarco_passages": ["embedding_fallback_hash"],
            "beir_nfcorpus": ["embedding_request_failed"],
            "squad_v2_dev": ["reranker_request_failed"],
        },
    )

    profile_cd_rows = payload["profiles"]["profile_cd"]["rows"]
    invalid_union: set[str] = set()
    for row in profile_cd_rows:
        invalid_reasons = set(row["degradation"]["invalid_reasons"])
        assert row["degradation"]["valid"] is False
        assert invalid_reasons
        invalid_union.update(invalid_reasons)

    assert invalid_union == PROFILE_CD_INVALID_GATE_REASONS
    phase6_gate = payload["phase6"]["gate"]
    assert phase6_gate["valid"] is False
    assert set(phase6_gate["invalid_reasons"]) == PROFILE_CD_INVALID_GATE_REASONS

    markdown = PROFILE_MARKDOWN_ARTIFACTS["profile_cd"].read_text(encoding="utf-8")
    assert "overall_valid: false" in markdown
    assert "INVALID" in markdown

    restored = _run_profile_ab(sample_size=100)
    assert restored["phase6"]["gate"]["valid"] is True
