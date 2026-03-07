import sys
from pathlib import Path

import pytest

BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from helpers.profile_abcd_real_runner import (  # noqa: E402
    DatasetBundle,
    QueryCase,
    REAL_PROFILE_WORKDIR,
    _evaluate_dataset,
    build_phase6_gate,
    compute_percentile,
    compute_retrieval_metrics,
    resolve_real_profile_workdir,
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


def test_build_phase6_gate_api_tolerant_allows_small_invalid_rate() -> None:
    gate = build_phase6_gate(
        [
            {
                "dataset": "beir_nfcorpus",
                "dataset_label": "BEIR NFCorpus",
                "degradation": {
                    "queries": 500,
                    "invalid_reasons": ["reranker_request_failed"],
                    "invalid_count": 2,
                    "invalid_rate": 0.004,
                    "request_failed_count": 2,
                    "request_failed_rate": 0.004,
                    "invalid_reason_counts": {"reranker_request_failed": 2},
                    "request_failed_reason_counts": {"reranker_request_failed": 2},
                },
            }
        ],
        mode="api_tolerant",
        invalid_rate_threshold=0.05,
    )
    assert gate["mode"] == "api_tolerant"
    assert gate["valid"] is True
    assert gate["invalid_count"] == 2
    assert gate["request_failed_count"] == 2
    assert gate["request_failed_reason_counts"] == {"reranker_request_failed": 2}
    assert gate["rows"][0]["valid"] is True


def test_build_phase6_gate_api_tolerant_marks_invalid_when_rate_exceeds_threshold() -> None:
    gate = build_phase6_gate(
        [
            {
                "dataset": "beir_nfcorpus",
                "dataset_label": "BEIR NFCorpus",
                "degradation": {
                    "queries": 500,
                    "invalid_reasons": ["reranker_request_failed"],
                    "invalid_count": 30,
                    "invalid_rate": 0.06,
                    "request_failed_count": 30,
                    "request_failed_rate": 0.06,
                },
            }
        ],
        mode="api_tolerant",
        invalid_rate_threshold=0.05,
    )
    assert gate["valid"] is False
    assert gate["rows"][0]["valid"] is False


def test_resolve_real_profile_workdir_respects_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = tmp_path / "custom-real-cache"
    monkeypatch.setenv("BENCHMARK_REAL_PROFILE_WORKDIR", str(expected))
    assert resolve_real_profile_workdir() == expected


def test_resolve_real_profile_workdir_prefers_explicit_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "BENCHMARK_REAL_PROFILE_WORKDIR",
        str(tmp_path / "env-cache"),
    )
    explicit = tmp_path / "explicit-cache"
    assert resolve_real_profile_workdir(explicit) == explicit


def test_resolve_real_profile_workdir_allocates_unique_run_dir_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BENCHMARK_REAL_PROFILE_WORKDIR", raising=False)

    first = resolve_real_profile_workdir()
    second = resolve_real_profile_workdir()

    assert first != second
    assert first.parent == REAL_PROFILE_WORKDIR
    assert second.parent == REAL_PROFILE_WORKDIR
    assert first.name.startswith("run-")
    assert second.name.startswith("run-")


class _ProbeSearchClient:
    def __init__(self, result_rows):
        self._result_rows = list(result_rows)
        self.calls = []

    async def search_advanced(
        self,
        *,
        query: str,
        mode: str,
        max_results: int,
        candidate_multiplier: int,
        filters,
    ):
        self.calls.append(
            {
                "query": query,
                "mode": mode,
                "max_results": max_results,
                "candidate_multiplier": candidate_multiplier,
                "filters": dict(filters),
            }
        )
        return {
            "results": list(self._result_rows),
            "degraded": False,
            "degrade_reasons": [],
        }


@pytest.mark.asyncio
async def test_evaluate_dataset_passes_depth_params_and_keeps_top10_metrics() -> None:
    result_rows = [{"memory_id": memory_id} for memory_id in range(1, 13)]
    client = _ProbeSearchClient(result_rows)
    bundle = DatasetBundle(
        key="squad_v2_dev",
        label="SQuAD v2 Dev",
        domain="bench_squad_v2_dev",
        queries=[QueryCase(query_id="q-1", query="alpha", relevant_doc_ids={"doc_12"})],
        docs=[("doc_1", "doc one")],
        sample_bucket_size=100,
        query_count_raw=1,
    )
    memory_to_doc = {memory_id: f"doc_{memory_id}" for memory_id in range(1, 13)}

    row = await _evaluate_dataset(
        client=client,  # type: ignore[arg-type]
        bundle=bundle,
        profile_mode="hybrid",
        memory_to_doc=memory_to_doc,
        max_results=12,
        candidate_multiplier=9,
    )

    assert client.calls == [
        {
            "query": "alpha",
            "mode": "hybrid",
            "max_results": 12,
            "candidate_multiplier": 9,
            "filters": {"domain": "bench_squad_v2_dev"},
        }
    ]
    # Relevant doc is rank 12 in returned list; Top10 metrics must remain 0.
    assert row["quality"]["hr_at_10"] == pytest.approx(0.0)
    assert row["quality"]["mrr"] == pytest.approx(0.0)
    assert row["quality"]["ndcg_at_10"] == pytest.approx(0.0)
    assert row["quality"]["recall_at_10"] == pytest.approx(0.0)
    assert row["retrieval_depth"] == {
        "max_results": 12,
        "candidate_multiplier": 9,
        "metric_top_k": 10,
    }
