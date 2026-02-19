import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

import pytest

from db.sqlite_client import SQLiteClient


BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from helpers.common import load_thresholds_v1


WRITE_GUARD_GOLD_SET_PATH = BENCHMARK_DIR.parent / "fixtures" / "write_guard_gold_set.jsonl"
WRITE_GUARD_JSON_ARTIFACT = BENCHMARK_DIR / "write_guard_quality_metrics.json"
WRITE_GUARD_MARKDOWN_ARTIFACT = BENCHMARK_DIR / "write_guard_quality_metrics.md"
_SQLITE_MEMORY_URL = "sqlite+aiosqlite:///:memory:"
_PHASE5_DATASETS = ("beir_nq", "beir_hotpotqa", "beir_fiqa")


def _load_gold_rows() -> List[Dict[str, Any]]:
    assert WRITE_GUARD_GOLD_SET_PATH.exists(), f"missing gold set: {WRITE_GUARD_GOLD_SET_PATH}"
    rows: List[Dict[str, Any]] = []
    for idx, line in enumerate(WRITE_GUARD_GOLD_SET_PATH.read_text(encoding="utf-8").splitlines(), 1):
        raw = line.strip()
        if not raw:
            continue
        row = json.loads(raw)
        row["case_index"] = idx
        assert str(row.get("id", "")).strip()
        assert str(row.get("content", "")).strip()
        assert str(row.get("expected_action", "")).strip() in {"ADD", "UPDATE", "NOOP", "DELETE"}
        rows.append(row)
    assert rows, "write_guard gold set must not be empty"
    return rows


def _is_block_action(action: str) -> bool:
    return action in {"UPDATE", "NOOP", "DELETE"}


def _assert_phase5_dataset_manifests_ready(sample_size: int = 100) -> None:
    manifests_dir = BENCHMARK_DIR.parent / "datasets" / "manifests"
    project_root = BENCHMARK_DIR.parents[2]
    sample_key = str(sample_size)

    for dataset in _PHASE5_DATASETS:
        manifest_path = manifests_dir / f"{dataset}.json"
        assert manifest_path.exists(), f"missing phase5 dataset manifest: {manifest_path}"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload.get("status") == "ready"
        sample_files = payload.get("sample_files", {})
        sample_counts = payload.get("sample_counts", {})
        sample_rel = sample_files.get(sample_key)
        assert isinstance(sample_rel, str) and sample_rel
        sample_path = project_root / sample_rel
        assert sample_path.exists()
        assert int(sample_counts.get(sample_key) or 0) > 0


class _WriteGuardEvalClient(SQLiteClient):
    def __init__(self, sqlite_url: str, rows_by_query: Mapping[str, Dict[str, Any]]) -> None:
        super().__init__(sqlite_url)
        self._rows_by_query = rows_by_query

    async def search_advanced(
        self,
        *,
        query: str,
        mode: str,
        max_results: int,
        candidate_multiplier: int,
        filters: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = max_results
        _ = candidate_multiplier
        _ = filters

        row = self._rows_by_query[query]
        if mode == "semantic":
            score = float(row.get("semantic_vector_score") or 0.0)
            if score <= 0.0:
                return {"results": [], "degrade_reasons": []}
            return {
                "results": [
                    {
                        "memory_id": int(row["case_index"]),
                        "uri": f"core://guard/semantic/{row['id']}",
                        "snippet": f"semantic candidate {row['id']}",
                        "scores": {"vector": score, "text": 0.05, "final": score},
                    }
                ],
                "degrade_reasons": [],
            }
        if mode == "keyword":
            score = float(row.get("keyword_text_score") or 0.0)
            if score <= 0.0:
                return {"results": [], "degrade_reasons": []}
            return {
                "results": [
                    {
                        "memory_id": 1000 + int(row["case_index"]),
                        "uri": f"core://guard/keyword/{row['id']}",
                        "snippet": f"keyword candidate {row['id']}",
                        "scores": {"vector": 0.05, "text": score, "final": score},
                    }
                ],
                "degrade_reasons": [],
            }
        raise AssertionError(f"unexpected mode: {mode}")


def _compute_precision_recall(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    tp = sum(1 for row in cases if row["expected_block"] and row["predicted_block"])
    fp = sum(1 for row in cases if (not row["expected_block"]) and row["predicted_block"])
    fn = sum(1 for row in cases if row["expected_block"] and (not row["predicted_block"]))
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
    }


def _write_artifacts(payload: Dict[str, Any]) -> None:
    WRITE_GUARD_JSON_ARTIFACT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    metrics = payload["metrics"]
    gates = payload["gates"]
    lines = [
        "# Write Guard Quality Metrics",
        "",
        f"> generated_at_utc: {payload['generated_at_utc']}",
        f"> total_cases: {payload['total_cases']}",
        "",
        "| Metric | Value | Threshold | Pass |",
        "|---|---:|---:|---|",
        (
            f"| Precision | {metrics['precision']:.3f} | {metrics['precision_threshold']:.3f} | "
            f"{'PASS' if gates['precision_pass'] else 'FAIL'} |"
        ),
        (
            f"| Recall | {metrics['recall']:.3f} | {metrics['recall_threshold']:.3f} | "
            f"{'PASS' if gates['recall_pass'] else 'FAIL'} |"
        ),
        f"| Overall | - | - | {'PASS' if gates['overall_pass'] else 'FAIL'} |",
        "",
        "## Cases",
        "",
        "| Case | Expected | Predicted | ExpectedBlock | PredictedBlock |",
        "|---|---|---|---:|---:|",
    ]
    for row in payload["cases"]:
        lines.append(
            f"| {row['id']} | {row['expected_action']} | {row['predicted_action']} | "
            f"{int(row['expected_block'])} | {int(row['predicted_block'])} |"
        )
    WRITE_GUARD_MARKDOWN_ARTIFACT.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_write_guard_quality_metrics_threshold_and_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_phase5_dataset_manifests_ready(sample_size=100)
    rows = _load_gold_rows()
    rows_by_query = {str(row["content"]): row for row in rows}

    monkeypatch.setenv("WRITE_GUARD_LLM_ENABLED", "false")
    monkeypatch.delenv("WRITE_GUARD_LLM_API_BASE", raising=False)
    monkeypatch.delenv("WRITE_GUARD_LLM_MODEL", raising=False)

    client = _WriteGuardEvalClient(_SQLITE_MEMORY_URL, rows_by_query)
    case_rows: List[Dict[str, Any]] = []
    try:
        for row in rows:
            decision = await client.write_guard(content=str(row["content"]), domain="core")
            predicted_action = str(decision.get("action") or "").upper()
            expected_action = str(row["expected_action"]).upper()
            case_rows.append(
                {
                    "id": row["id"],
                    "expected_action": expected_action,
                    "predicted_action": predicted_action,
                    "expected_block": _is_block_action(expected_action),
                    "predicted_block": _is_block_action(predicted_action),
                }
            )
    finally:
        await client.close()

    metrics = _compute_precision_recall(case_rows)
    thresholds = load_thresholds_v1()["write_guard"]
    precision_threshold = float(thresholds["precision_gte"])
    recall_threshold = float(thresholds["recall_gte"])

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "total_cases": len(case_rows),
        "metrics": {
            "tp": metrics["tp"],
            "fp": metrics["fp"],
            "fn": metrics["fn"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "precision_threshold": precision_threshold,
            "recall_threshold": recall_threshold,
        },
        "gates": {
            "precision_pass": metrics["precision"] >= precision_threshold,
            "recall_pass": metrics["recall"] >= recall_threshold,
            "overall_pass": (
                metrics["precision"] >= precision_threshold
                and metrics["recall"] >= recall_threshold
            ),
        },
        "cases": case_rows,
    }
    _write_artifacts(payload)

    assert payload["gates"]["precision_pass"] is True
    assert payload["gates"]["recall_pass"] is True
    assert payload["gates"]["overall_pass"] is True
    assert WRITE_GUARD_JSON_ARTIFACT.exists()
    assert WRITE_GUARD_MARKDOWN_ARTIFACT.exists()
