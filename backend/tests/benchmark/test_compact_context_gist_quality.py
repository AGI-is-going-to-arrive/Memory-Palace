import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from helpers.common import load_thresholds_v1


GIST_GOLD_SET_PATH = BENCHMARK_DIR.parent / "fixtures" / "compact_context_gist_gold_set.jsonl"
GIST_JSON_ARTIFACT = BENCHMARK_DIR / "compact_context_gist_quality_metrics.json"
GIST_MARKDOWN_ARTIFACT = BENCHMARK_DIR / "compact_context_gist_quality_metrics.md"


def _load_gold_rows() -> List[Dict[str, Any]]:
    assert GIST_GOLD_SET_PATH.exists(), f"missing gold set: {GIST_GOLD_SET_PATH}"
    rows: List[Dict[str, Any]] = []
    for line in GIST_GOLD_SET_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        row = json.loads(raw)
        assert str(row.get("id", "")).strip()
        assert str(row.get("reference_gist", "")).strip()
        assert str(row.get("candidate_gist", "")).strip()
        rows.append(row)
    assert rows, "gist gold set must not be empty"
    return rows


def _tokenize(value: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", value.lower())


def _lcs_length(reference_tokens: List[str], candidate_tokens: List[str]) -> int:
    if not reference_tokens or not candidate_tokens:
        return 0
    m = len(reference_tokens)
    n = len(candidate_tokens)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        ref = reference_tokens[i - 1]
        for j in range(1, n + 1):
            if ref == candidate_tokens[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]


def _rouge_l_f1(reference: str, candidate: str) -> float:
    reference_tokens = _tokenize(reference)
    candidate_tokens = _tokenize(candidate)
    if not reference_tokens or not candidate_tokens:
        return 0.0
    lcs = _lcs_length(reference_tokens, candidate_tokens)
    precision = lcs / len(candidate_tokens)
    recall = lcs / len(reference_tokens)
    if (precision + recall) == 0:
        return 0.0
    return (2 * precision * recall) / (precision + recall)


def _write_artifacts(payload: Dict[str, Any]) -> None:
    GIST_JSON_ARTIFACT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Compact Context Gist Quality Metrics",
        "",
        f"> generated_at_utc: {payload['generated_at_utc']}",
        f"> total_cases: {payload['total_cases']}",
        "",
        "| Metric | Value | Threshold | Pass |",
        "|---|---:|---:|---|",
        (
            f"| ROUGE-L F1 | {payload['rouge_l']:.3f} | {payload['rouge_l_threshold']:.3f} | "
            f"{'PASS' if payload['gate_pass'] else 'FAIL'} |"
        ),
        "",
        "## Cases",
        "",
        "| Case | ROUGE-L F1 |",
        "|---|---:|",
    ]
    for row in payload["cases"]:
        lines.append(f"| {row['id']} | {row['rouge_l']:.3f} |")
    GIST_MARKDOWN_ARTIFACT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_compact_context_gist_quality_threshold_and_artifacts() -> None:
    rows = _load_gold_rows()
    case_rows: List[Dict[str, Any]] = []
    for row in rows:
        score = _rouge_l_f1(str(row["reference_gist"]), str(row["candidate_gist"]))
        case_rows.append(
            {
                "id": row["id"],
                "rouge_l": round(score, 6),
            }
        )

    total_cases = len(case_rows)
    aggregate = sum(float(row["rouge_l"]) for row in case_rows) / total_cases if total_cases else 0.0
    rouge_l_threshold = float(load_thresholds_v1()["gist"]["rouge_l_gte"])
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "total_cases": total_cases,
        "rouge_l": round(aggregate, 6),
        "rouge_l_threshold": rouge_l_threshold,
        "gate_pass": aggregate >= rouge_l_threshold,
        "cases": case_rows,
    }
    _write_artifacts(payload)

    assert payload["gate_pass"] is True
    assert all(0.0 <= float(row["rouge_l"]) <= 1.0 for row in case_rows)
    assert GIST_JSON_ARTIFACT.exists()
    assert GIST_MARKDOWN_ARTIFACT.exists()
