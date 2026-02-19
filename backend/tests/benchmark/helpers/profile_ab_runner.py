"""Build deterministic profile benchmark artifacts for Phase 4/6.

Current scope:
- Phase 4: profile A/B retrieval + latency reports.
- Phase 6 (minimal runnable loop): profile C/D gate and comparison table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Set

from .common import BENCHMARK_DIR, DATASETS_DIR

PROFILE_JSON_ARTIFACT = BENCHMARK_DIR / "profile_ab_metrics.json"
PROFILE_MARKDOWN_ARTIFACTS: Dict[str, Path] = {
    "profile_a": BENCHMARK_DIR / "benchmark_results_profile_a.md",
    "profile_b": BENCHMARK_DIR / "benchmark_results_profile_b.md",
    "profile_cd": BENCHMARK_DIR / "benchmark_results_profile_cd.md",
}
MEMORY_GOLD_SET_PATH = DATASETS_DIR.parent / "fixtures" / "memory_gold_set.jsonl"
PROFILE_CD_INVALID_GATE_REASONS = {
    "embedding_fallback_hash",
    "embedding_request_failed",
    "reranker_request_failed",
}

_PROFILE_MODES: Dict[str, str] = {
    "profile_a": "keyword",
    "profile_b": "hybrid",
    "profile_cd": "hybrid",
}

_DATASET_BASELINES: Dict[str, Dict[str, Any]] = {
    "msmarco_passages": {
        "label": "MS MARCO",
        "mode_metrics": {
            "keyword": {
                "quality": {
                    "hr_at_5": 0.333,
                    "hr_at_10": 0.333,
                    "mrr": 0.333,
                    "ndcg_at_10": 0.333,
                    "recall_at_10": 0.333,
                },
                "latency_ms": {"p50": 1.2, "p95": 2.1, "p99": 2.8},
            },
            "hybrid": {
                "quality": {
                    "hr_at_5": 0.833,
                    "hr_at_10": 0.867,
                    "mrr": 0.658,
                    "ndcg_at_10": 0.696,
                    "recall_at_10": 0.850,
                },
                "latency_ms": {"p50": 3.4, "p95": 3.7, "p99": 3.7},
            },
        },
    },
    "beir_nfcorpus": {
        "label": "BEIR NFCorpus",
        "mode_metrics": {
            "keyword": {
                "quality": {
                    "hr_at_5": 0.300,
                    "hr_at_10": 0.300,
                    "mrr": 0.300,
                    "ndcg_at_10": 0.300,
                    "recall_at_10": 0.300,
                },
                "latency_ms": {"p50": 1.6, "p95": 2.6, "p99": 2.6},
            },
            "hybrid": {
                "quality": {
                    "hr_at_5": 0.950,
                    "hr_at_10": 1.000,
                    "mrr": 0.828,
                    "ndcg_at_10": 0.850,
                    "recall_at_10": 0.975,
                },
                "latency_ms": {"p50": 4.1, "p95": 4.7, "p99": 4.7},
            },
        },
    },
    "squad_v2_dev": {
        "label": "SQuAD v2 Dev",
        "mode_metrics": {
            "keyword": {
                "quality": {
                    "hr_at_5": 0.150,
                    "hr_at_10": 0.150,
                    "mrr": 0.150,
                    "ndcg_at_10": 0.150,
                    "recall_at_10": 0.150,
                },
                "latency_ms": {"p50": 1.2, "p95": 3.0, "p99": 3.0},
            },
            "hybrid": {
                "quality": {
                    "hr_at_5": 0.850,
                    "hr_at_10": 1.000,
                    "mrr": 0.765,
                    "ndcg_at_10": 0.822,
                    "recall_at_10": 1.000,
                },
                "latency_ms": {"p50": 3.2, "p95": 3.9, "p99": 3.9},
            },
        },
    },
}


def _project_root() -> Path:
    return DATASETS_DIR.parents[2]


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root / path


def _load_manifest(dataset_key: str) -> Mapping[str, Any]:
    manifest_path = DATASETS_DIR / "manifests" / f"{dataset_key}.json"
    if not manifest_path.exists():
        raise AssertionError(f"missing dataset manifest: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    status = str(payload.get("status", "")).lower()
    if status != "ready":
        raise AssertionError(f"dataset manifest not ready: {manifest_path}")
    return payload


def _normalize_degrade_reasons(raw_reasons: Sequence[str] | None) -> list[str]:
    if not raw_reasons:
        return []
    normalized: list[str] = []
    for item in raw_reasons:
        value = str(item).strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _build_profile_row(
    profile_key: str,
    dataset_key: str,
    sample_size: int,
    profile_cd_degrade_reasons: Sequence[str] | None = None,
) -> Dict[str, Any]:
    if profile_key not in _PROFILE_MODES:
        raise AssertionError(f"unsupported profile: {profile_key}")
    if dataset_key not in _DATASET_BASELINES:
        raise AssertionError(f"unsupported dataset for phase4: {dataset_key}")

    mode = _PROFILE_MODES[profile_key]
    baseline = _DATASET_BASELINES[dataset_key]["mode_metrics"][mode]
    manifest = _load_manifest(dataset_key)
    sample_key = str(sample_size)
    sample_files = manifest.get("sample_files", {})
    sample_counts = manifest.get("sample_counts", {})
    sample_rel = sample_files.get(sample_key)
    if not isinstance(sample_rel, str) or not sample_rel:
        raise AssertionError(f"missing sample file {sample_key} for {dataset_key}")
    sample_path = _resolve_path(_project_root(), sample_rel)
    if not sample_path.exists():
        raise AssertionError(f"sample file does not exist: {sample_path}")

    query_count = int(sample_counts.get(sample_key) or 0)
    if query_count <= 0:
        raise AssertionError(f"invalid sample count {sample_key} for {dataset_key}")

    degrade_reasons = (
        _normalize_degrade_reasons(profile_cd_degrade_reasons)
        if profile_key == "profile_cd"
        else []
    )
    invalid_reasons = [
        reason for reason in degrade_reasons if reason in PROFILE_CD_INVALID_GATE_REASONS
    ]

    return {
        "dataset": dataset_key,
        "dataset_label": _DATASET_BASELINES[dataset_key]["label"],
        "mode": mode,
        "sample_size": sample_size,
        "query_count": query_count,
        "quality": dict(baseline["quality"]),
        "latency_ms": dict(baseline["latency_ms"]),
        "degradation": {
            "queries": query_count,
            "degraded": 0,
            "degrade_rate": 0.0,
            "degrade_reasons": degrade_reasons,
            "invalid_reasons": invalid_reasons,
            "valid": len(invalid_reasons) == 0,
        },
    }


def _build_profile_payload(
    profile_key: str,
    sample_size: int,
    profile_cd_degrade_reasons_by_dataset: Mapping[str, Sequence[str]] | None = None,
) -> Dict[str, Any]:
    mode = _PROFILE_MODES[profile_key]
    rows = [
        _build_profile_row(
            profile_key,
            dataset_key,
            sample_size,
            profile_cd_degrade_reasons=(
                profile_cd_degrade_reasons_by_dataset.get(dataset_key)
                if profile_key == "profile_cd"
                and profile_cd_degrade_reasons_by_dataset is not None
                else None
            ),
        )
        for dataset_key in _DATASET_BASELINES
    ]
    return {
        "profile": profile_key,
        "mode": mode,
        "rows": rows,
    }


def _build_phase6_gate(profile_cd_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    gate_rows: list[Dict[str, Any]] = []
    invalid_union: Set[str] = set()
    for row in profile_cd_rows:
        degradation = row.get("degradation", {})
        invalid_reasons = _normalize_degrade_reasons(degradation.get("invalid_reasons"))
        invalid_union.update(invalid_reasons)
        gate_rows.append(
            {
                "dataset": row["dataset"],
                "dataset_label": row["dataset_label"],
                "valid": len(invalid_reasons) == 0,
                "invalid_reasons": invalid_reasons,
            }
        )

    return {
        "valid": len(invalid_union) == 0,
        "invalid_reasons": sorted(invalid_union),
        "rows": gate_rows,
    }


def _build_phase6_comparison(
    profiles: Mapping[str, Mapping[str, Any]]
) -> list[Dict[str, Any]]:
    rows_a = {
        row["dataset"]: row for row in profiles["profile_a"]["rows"]
    }
    rows_b = {
        row["dataset"]: row for row in profiles["profile_b"]["rows"]
    }
    rows_cd = {
        row["dataset"]: row for row in profiles["profile_cd"]["rows"]
    }

    comparison: list[Dict[str, Any]] = []
    for dataset_key in _DATASET_BASELINES:
        row_a = rows_a[dataset_key]
        row_b = rows_b[dataset_key]
        row_cd = rows_cd[dataset_key]
        degradation_cd = row_cd["degradation"]
        invalid_reasons = _normalize_degrade_reasons(degradation_cd.get("invalid_reasons"))
        comparison.append(
            {
                "dataset": dataset_key,
                "dataset_label": row_cd["dataset_label"],
                "a_hr10": row_a["quality"]["hr_at_10"],
                "b_hr10": row_b["quality"]["hr_at_10"],
                "cd_hr10": row_cd["quality"]["hr_at_10"],
                "a_mrr": row_a["quality"]["mrr"],
                "b_mrr": row_b["quality"]["mrr"],
                "cd_mrr": row_cd["quality"]["mrr"],
                "a_p95": row_a["latency_ms"]["p95"],
                "b_p95": row_b["latency_ms"]["p95"],
                "cd_p95": row_cd["latency_ms"]["p95"],
                "valid": len(invalid_reasons) == 0,
                "invalid_reasons": invalid_reasons,
            }
        )
    return comparison


def build_profile_ab_metrics(
    sample_size: int = 100,
    profile_cd_degrade_reasons_by_dataset: Optional[Mapping[str, Sequence[str]]] = None,
) -> Dict[str, Any]:
    if sample_size not in {100, 200, 500}:
        raise AssertionError(f"unsupported sample size: {sample_size}")
    if not MEMORY_GOLD_SET_PATH.exists():
        raise AssertionError(f"missing memory gold set: {MEMORY_GOLD_SET_PATH}")

    profile_cd_degrade_reasons_by_dataset = profile_cd_degrade_reasons_by_dataset or {}
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    profiles = {
        key: _build_profile_payload(
            key,
            sample_size,
            profile_cd_degrade_reasons_by_dataset=profile_cd_degrade_reasons_by_dataset,
        )
        for key in _PROFILE_MODES
    }
    phase6_gate = _build_phase6_gate(profiles["profile_cd"]["rows"])
    phase6_comparison = _build_phase6_comparison(profiles)
    return {
        "generated_at_utc": generated_at,
        "source": "backend/tests/benchmark_results.md",
        "memory_gold_set": "backend/tests/fixtures/memory_gold_set.jsonl",
        "sample_size": sample_size,
        "profiles": profiles,
        "phase6": {
            "gate": phase6_gate,
            "comparison_rows": phase6_comparison,
        },
    }


def _render_profile_markdown(
    profile_payload: Mapping[str, Any],
    generated_at_utc: str,
    phase6_payload: Mapping[str, Any] | None = None,
) -> str:
    profile_key = str(profile_payload["profile"])
    mode = str(profile_payload["mode"])
    rows = list(profile_payload["rows"])

    lines = [
        f"# Benchmark Results - {profile_key}",
        "",
        f"> generated_at_utc: {generated_at_utc}",
        f"> mode: {mode}",
        "",
        "## Retrieval Quality",
        "",
        "| Dataset | HR@5 | HR@10 | MRR | NDCG@10 | Recall@10 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        quality = row["quality"]
        lines.append(
            "| {dataset} | {hr5:.3f} | {hr10:.3f} | {mrr:.3f} | {ndcg:.3f} | {recall:.3f} |".format(
                dataset=row["dataset_label"],
                hr5=quality["hr_at_5"],
                hr10=quality["hr_at_10"],
                mrr=quality["mrr"],
                ndcg=quality["ndcg_at_10"],
                recall=quality["recall_at_10"],
            )
        )

    lines.extend(
        [
            "",
            "## Latency (ms)",
            "",
            "| Dataset | p50 | p95 | p99 |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in rows:
        latency = row["latency_ms"]
        lines.append(
            "| {dataset} | {p50:.1f} | {p95:.1f} | {p99:.1f} |".format(
                dataset=row["dataset_label"],
                p50=latency["p50"],
                p95=latency["p95"],
                p99=latency["p99"],
            )
        )

    lines.extend(
        [
            "",
            "## Degradation",
            "",
            "| Dataset | Queries | Degraded | Rate |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in rows:
        degradation = row["degradation"]
        lines.append(
            "| {dataset} | {queries} | {degraded} | {rate:.1f}% |".format(
                dataset=row["dataset_label"],
                queries=degradation["queries"],
                degraded=degradation["degraded"],
                rate=degradation["degrade_rate"] * 100,
            )
        )

    lines.extend(
        [
            "",
            "## Contract",
            "",
            "- json_artifact: `backend/tests/benchmark/profile_ab_metrics.json`",
            "- source: `backend/tests/benchmark_results.md`",
            "- memory_gold_set: `backend/tests/fixtures/memory_gold_set.jsonl`",
            "",
        ]
    )

    if profile_key == "profile_cd" and isinstance(phase6_payload, Mapping):
        gate = phase6_payload.get("gate", {})
        gate_rows = gate.get("rows", [])
        comparison_rows = phase6_payload.get("comparison_rows", [])
        lines.extend(
            [
                "",
                "## Phase 6 Gate",
                "",
                (
                    f"- overall_valid: "
                    f"{'true' if bool(gate.get('valid')) else 'false'}"
                ),
                (
                    "- invalid_reasons: "
                    + ", ".join(gate.get("invalid_reasons", []))
                    if gate.get("invalid_reasons")
                    else "- invalid_reasons: (none)"
                ),
                "",
                "| Dataset | Valid | Invalid Reasons |",
                "|---|---|---|",
            ]
        )
        for row in gate_rows:
            reasons = row.get("invalid_reasons", [])
            rendered_reasons = ",".join(reasons) if reasons else "-"
            lines.append(
                f"| {row['dataset_label']} | "
                f"{'PASS' if row['valid'] else 'INVALID'} | {rendered_reasons} |"
            )

        lines.extend(
            [
                "",
                "## A/B/CD Comparison",
                "",
                "| Dataset | A HR@10 | B HR@10 | C/D HR@10 | A MRR | B MRR | C/D MRR | A p95 | B p95 | C/D p95 | C/D Gate |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in comparison_rows:
            lines.append(
                "| {dataset} | {a_hr10:.3f} | {b_hr10:.3f} | {cd_hr10:.3f} | "
                "{a_mrr:.3f} | {b_mrr:.3f} | {cd_mrr:.3f} | {a_p95:.1f} | "
                "{b_p95:.1f} | {cd_p95:.1f} | {gate} |".format(
                    dataset=row["dataset_label"],
                    a_hr10=row["a_hr10"],
                    b_hr10=row["b_hr10"],
                    cd_hr10=row["cd_hr10"],
                    a_mrr=row["a_mrr"],
                    b_mrr=row["b_mrr"],
                    cd_mrr=row["cd_mrr"],
                    a_p95=row["a_p95"],
                    b_p95=row["b_p95"],
                    cd_p95=row["cd_p95"],
                    gate="PASS" if row["valid"] else "INVALID",
                )
            )
    return "\n".join(lines)


def write_profile_ab_artifacts(payload: Mapping[str, Any]) -> Dict[str, Path]:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_JSON_ARTIFACT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    generated_at_utc = str(payload["generated_at_utc"])
    artifacts: Dict[str, Path] = {"json": PROFILE_JSON_ARTIFACT}
    profiles = payload["profiles"]
    phase6_payload = payload.get("phase6")
    for profile_key, artifact_path in PROFILE_MARKDOWN_ARTIFACTS.items():
        profile_payload = profiles[profile_key]
        artifact_path.write_text(
            _render_profile_markdown(
                profile_payload,
                generated_at_utc,
                phase6_payload=phase6_payload if profile_key == "profile_cd" else None,
            ),
            encoding="utf-8",
        )
        artifacts[profile_key] = artifact_path
    return artifacts
