import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from db.sqlite_client import SQLiteClient


BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from helpers.common import load_thresholds_v1


INTENT_GOLD_SET_PATH = BENCHMARK_DIR.parent / "fixtures" / "intent_gold_set.jsonl"
INTENT_JSON_ARTIFACT = BENCHMARK_DIR / "intent_accuracy_metrics.json"
INTENT_MARKDOWN_ARTIFACT = BENCHMARK_DIR / "intent_accuracy_metrics.md"
_SQLITE_MEMORY_URL = "sqlite+aiosqlite:///:memory:"


def _load_gold_rows() -> List[Dict[str, Any]]:
    assert INTENT_GOLD_SET_PATH.exists(), f"missing gold set: {INTENT_GOLD_SET_PATH}"
    rows: List[Dict[str, Any]] = []
    for line in INTENT_GOLD_SET_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        row = json.loads(raw)
        assert str(row.get("id", "")).strip()
        assert str(row.get("query", "")).strip()
        assert str(row.get("expected_intent", "")).strip()
        rows.append(row)
    assert rows, "intent gold set must not be empty"
    return rows


def _write_artifacts(payload: Dict[str, Any]) -> None:
    INTENT_JSON_ARTIFACT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Intent Accuracy Metrics",
        "",
        f"> generated_at_utc: {payload['generated_at_utc']}",
        f"> total_cases: {payload['total_cases']}",
        "",
        "| Metric | Value | Threshold | Pass |",
        "|---|---:|---:|---|",
        (
            f"| Accuracy | {payload['accuracy']:.3f} | {payload['accuracy_threshold']:.3f} | "
            f"{'PASS' if payload['gate_pass'] else 'FAIL'} |"
        ),
        "",
        "## Cases",
        "",
        "| Case | Expected | Predicted | Method | Strategy |",
        "|---|---|---|---|---|",
    ]
    for row in payload["cases"]:
        lines.append(
            f"| {row['id']} | {row['expected_intent']} | {row['predicted_intent']} | "
            f"{row['method']} | {row['strategy_template']} |"
        )
    INTENT_MARKDOWN_ARTIFACT.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_intent_accuracy_metrics_threshold_and_artifacts() -> None:
    rows = _load_gold_rows()
    client = SQLiteClient(_SQLITE_MEMORY_URL)
    case_rows: List[Dict[str, Any]] = []
    try:
        for row in rows:
            query = str(row["query"])
            expected_intent = str(row["expected_intent"])
            normalized = client.preprocess_query(query)
            predicted = client.classify_intent(query, normalized.get("rewritten_query"))
            predicted_intent = str(predicted.get("intent") or "")
            case_rows.append(
                {
                    "id": row["id"],
                    "expected_intent": expected_intent,
                    "predicted_intent": predicted_intent,
                    "method": str(predicted.get("method") or ""),
                    "strategy_template": str(predicted.get("strategy_template") or ""),
                    "correct": predicted_intent == expected_intent,
                }
            )
    finally:
        await client.close()

    total_cases = len(case_rows)
    correct_count = sum(1 for row in case_rows if row["correct"])
    accuracy = correct_count / total_cases if total_cases > 0 else 0.0
    accuracy_threshold = float(load_thresholds_v1()["intent"]["accuracy_gte"])

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "total_cases": total_cases,
        "correct_cases": correct_count,
        "accuracy": round(accuracy, 6),
        "accuracy_threshold": accuracy_threshold,
        "gate_pass": accuracy >= accuracy_threshold,
        "cases": case_rows,
    }
    _write_artifacts(payload)

    assert payload["gate_pass"] is True
    assert INTENT_JSON_ARTIFACT.exists()
    assert INTENT_MARKDOWN_ARTIFACT.exists()
