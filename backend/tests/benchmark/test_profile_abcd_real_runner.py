import sys
from pathlib import Path

import pytest

BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from helpers.profile_abcd_real_runner import (  # noqa: E402
    build_phase6_gate,
    compute_percentile,
    compute_retrieval_metrics,
)


def test_compute_retrieval_metrics_binary_relevance_contract() -> None:
    metrics = compute_retrieval_metrics(
        retrieved_doc_ids=["d2", "d1", "d3", "d4"],
        relevant_doc_ids={"d1", "d4"},
        k=10,
    )
    assert metrics["hr_at_5"] == pytest.approx(1.0)
    assert metrics["hr_at_10"] == pytest.approx(1.0)
    assert metrics["mrr"] == pytest.approx(0.5)
    assert metrics["recall_at_10"] == pytest.approx(1.0)
    assert metrics["ndcg_at_10"] == pytest.approx(0.6509209, abs=1e-6)


def test_compute_percentile_linear_interpolation() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert compute_percentile(values, 0.50) == pytest.approx(2.5)
    assert compute_percentile(values, 0.95) == pytest.approx(3.85)
    assert compute_percentile([9.0], 0.95) == pytest.approx(9.0)


def test_build_phase6_gate_marks_invalid_when_profile_d_has_invalid_reasons() -> None:
    gate = build_phase6_gate(
        [
            {
                "dataset": "squad_v2_dev",
                "dataset_label": "SQuAD v2 Dev",
                "degradation": {"invalid_reasons": []},
            },
            {
                "dataset": "beir_nfcorpus",
                "dataset_label": "BEIR NFCorpus",
                "degradation": {"invalid_reasons": ["embedding_request_failed"]},
            },
        ]
    )
    assert gate["valid"] is False
    assert gate["invalid_reasons"] == ["embedding_request_failed"]
    assert gate["rows"][0]["valid"] is True
    assert gate["rows"][1]["valid"] is False
