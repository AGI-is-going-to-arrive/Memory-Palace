from pathlib import Path

import pytest

from db.sqlite_client import SQLiteClient


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "expected_template", "expected_multiplier"),
    [
        ("factual", "factual_high_precision", 2),
        ("exploratory", "exploratory_high_recall", 6),
        ("temporal", "temporal_time_filtered", 5),
        ("causal", "causal_wide_pool", 8),
    ],
)
async def test_search_advanced_applies_intent_strategy_metadata(
    tmp_path: Path,
    intent: str,
    expected_template: str,
    expected_multiplier: int,
) -> None:
    db_path = tmp_path / f"week3-intent-{intent}.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    payload = await client.search_advanced(
        query="index rebuild diagnostics",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=4,
        filters={},
        intent_profile={"intent": intent},
    )

    await client.close()

    metadata = payload.get("metadata", {})
    assert metadata.get("intent") == intent
    assert metadata.get("strategy_template") == expected_template
    assert metadata.get("candidate_multiplier_applied") == expected_multiplier


@pytest.mark.asyncio
async def test_search_advanced_without_intent_profile_uses_default_strategy(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week3-intent-default.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    payload = await client.search_advanced(
        query="index rebuild diagnostics",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=4,
        filters={},
    )

    await client.close()

    metadata = payload.get("metadata", {})
    assert metadata.get("intent") is None
    assert metadata.get("strategy_template") == "default"
    assert metadata.get("candidate_multiplier_applied") == 4


@pytest.mark.asyncio
async def test_search_advanced_empty_query_handles_none_candidate_multiplier(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week3-intent-empty-query.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    payload = await client.search_advanced(
        query="",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=None,  # type: ignore[arg-type]
        filters={},
        intent_profile={"intent": "factual"},
    )

    await client.close()

    assert payload["degraded"] is True
    assert payload["degrade_reason"] == "empty_query"
    assert payload["metadata"]["strategy_template"] == "factual_high_precision"
    assert payload["metadata"]["mmr_applied"] is False
    assert payload["metadata"]["mmr_candidate_count"] == 0
    assert payload["metadata"]["mmr_selected_count"] == 0


@pytest.mark.asyncio
async def test_search_advanced_no_candidates_keeps_mmr_metadata_shape(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week3-mmr-no-candidates.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    payload = await client.search_advanced(
        query="unmatched phrase",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=4,
        filters={"domain": "core"},
    )

    await client.close()

    assert payload["results"] == []
    assert payload["metadata"]["mmr_applied"] is False
    assert payload["metadata"]["mmr_candidate_count"] == 0
    assert payload["metadata"]["mmr_selected_count"] == 0


@pytest.mark.asyncio
async def test_search_advanced_applies_mmr_only_for_hybrid_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_MMR_ENABLED", "true")
    db_path = tmp_path / "week3-mmr-enabled.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.create_memory(
        parent_path="",
        content="alpha retrieval signal for mmr validation",
        priority=1,
        title="alpha",
        domain="core",
    )

    call_count = {"value": 0}

    def _fake_apply_mmr(scored_results, max_results):
        call_count["value"] += 1
        selected = scored_results[:max_results]
        return selected, {
            "mmr_applied": True,
            "mmr_candidate_count": len(scored_results),
            "mmr_selected_count": len(selected),
        }

    monkeypatch.setattr(client, "_apply_mmr_rerank", _fake_apply_mmr)

    hybrid_payload = await client.search_advanced(
        query="alpha retrieval",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    keyword_payload = await client.search_advanced(
        query="alpha retrieval",
        mode="keyword",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    await client.close()

    assert call_count["value"] == 1
    assert hybrid_payload["metadata"]["mmr_applied"] is True
    assert hybrid_payload["metadata"]["mmr_selected_count"] >= 1
    assert keyword_payload["metadata"]["mmr_applied"] is False


@pytest.mark.asyncio
async def test_search_advanced_hybrid_keeps_mmr_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RETRIEVAL_MMR_ENABLED", raising=False)
    db_path = tmp_path / "week3-mmr-default-off.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.create_memory(
        parent_path="",
        content="beta retrieval signal for mmr default off",
        priority=1,
        title="beta",
        domain="core",
    )

    def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("mmr_should_not_run_when_disabled")

    monkeypatch.setattr(client, "_apply_mmr_rerank", _raise_if_called)

    payload = await client.search_advanced(
        query="beta retrieval",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    await client.close()

    assert payload["metadata"]["mmr_applied"] is False
    assert payload["metadata"]["mmr_selected_count"] >= 1


@pytest.mark.asyncio
async def test_search_advanced_semantic_does_not_apply_mmr_even_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_MMR_ENABLED", "true")
    db_path = tmp_path / "week3-mmr-semantic.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.create_memory(
        parent_path="",
        content="gamma retrieval signal for semantic mode",
        priority=1,
        title="gamma",
        domain="core",
    )

    def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("mmr_should_not_run_for_semantic_mode")

    monkeypatch.setattr(client, "_apply_mmr_rerank", _raise_if_called)

    payload = await client.search_advanced(
        query="gamma retrieval",
        mode="semantic",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    await client.close()

    assert payload["metadata"]["mmr_applied"] is False
    assert payload["metadata"]["mmr_selected_count"] >= 1


@pytest.mark.asyncio
async def test_search_advanced_mmr_failure_degrades_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_MMR_ENABLED", "true")
    db_path = tmp_path / "week3-mmr-failure.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.create_memory(
        parent_path="",
        content="delta retrieval signal for mmr failure path",
        priority=1,
        title="delta",
        domain="core",
    )

    def _raise_runtime_error(*_args, **_kwargs):
        raise RuntimeError("forced_mmr_failure")

    monkeypatch.setattr(client, "_apply_mmr_rerank", _raise_runtime_error)

    payload = await client.search_advanced(
        query="delta retrieval",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    await client.close()

    assert payload["degraded"] is True
    assert "mmr_rerank_failed" in payload.get("degrade_reasons", [])
    assert payload["metadata"]["mmr_applied"] is False
    assert payload["metadata"]["mmr_selected_count"] >= 1


@pytest.mark.asyncio
async def test_classify_intent_uses_scoring_and_ambiguous_fallback(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week3-intent-classifier.db"
    client = SQLiteClient(_sqlite_url(db_path))

    causal = client.classify_intent("Why did rebuild fail?")
    temporal = client.classify_intent("When did rebuild happen?")
    exploratory = client.classify_intent("Explore alternatives and compare options")
    ambiguous = client.classify_intent("Why did rebuild fail after yesterday?")

    await client.close()

    assert causal["intent"] == "causal"
    assert temporal["intent"] == "temporal"
    assert exploratory["intent"] == "exploratory"
    assert ambiguous["intent"] == "unknown"
    assert ambiguous["strategy_template"] == "default"


@pytest.mark.asyncio
async def test_reranker_base_with_rerank_suffix_is_normalized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_RERANKER_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_RERANKER_API_BASE", "https://api.siliconflow.cn/v1/rerank")
    monkeypatch.setenv("RETRIEVAL_RERANKER_API_KEY", "test-key")
    monkeypatch.setenv("RETRIEVAL_RERANKER_MODEL", "Qwen/Qwen3-Reranker-8B")

    db_path = tmp_path / "week3-reranker-base.db"
    client = SQLiteClient(_sqlite_url(db_path))

    call_meta = {"base": "", "endpoint": ""}

    async def _fake_post_json(base: str, endpoint: str, payload, api_key: str = ""):
        call_meta["base"] = base
        call_meta["endpoint"] = endpoint
        _ = payload
        _ = api_key
        return {"results": [{"index": 0, "score": 0.88}]}

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    degrade_reasons: list[str] = []
    scores = await client._get_rerank_scores(
        query="release checklist",
        documents=["release checklist owner map"],
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert call_meta["base"] == "https://api.siliconflow.cn/v1"
    assert call_meta["endpoint"] == "/rerank"
    assert scores[0] == pytest.approx(0.88)
    assert degrade_reasons == []


@pytest.mark.asyncio
async def test_embedding_base_with_embeddings_suffix_is_normalized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "api")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_BASE", "https://ai.gitee.com/v1/embeddings")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_MODEL", "Qwen3-Embedding-8B")

    db_path = tmp_path / "week3-embedding-base.db"
    client = SQLiteClient(_sqlite_url(db_path))

    call_meta = {"base": "", "endpoint": ""}

    async def _fake_post_json(base: str, endpoint: str, payload, api_key: str = ""):
        call_meta["base"] = base
        call_meta["endpoint"] = endpoint
        _ = payload
        _ = api_key
        return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    degrade_reasons: list[str] = []
    embedding = await client._fetch_remote_embedding(
        "memory retrieval smoke",
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert call_meta["base"] == "https://ai.gitee.com/v1"
    assert call_meta["endpoint"] == "/embeddings"
    assert embedding == [0.1, 0.2, 0.3]
    assert degrade_reasons == []


@pytest.mark.asyncio
async def test_search_advanced_invalid_weight_env_falls_back_defaults_and_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("RETRIEVAL_RERANKER_ENABLED", "false")
    monkeypatch.setenv("RETRIEVAL_RERANKER_WEIGHT", "not-a-number")
    monkeypatch.setenv("RETRIEVAL_HYBRID_SEMANTIC_WEIGHT", "invalid")
    monkeypatch.setenv("RETRIEVAL_HYBRID_KEYWORD_WEIGHT", "invalid")
    monkeypatch.setenv("RETRIEVAL_WEIGHT_PRIORITY", "invalid")
    monkeypatch.setenv("RETRIEVAL_WEIGHT_RECENCY", "invalid")
    monkeypatch.setenv("RETRIEVAL_WEIGHT_PATH_PREFIX", "invalid")
    monkeypatch.setenv("RETRIEVAL_RECENCY_HALF_LIFE_DAYS", "invalid")

    db_path = tmp_path / "week3-invalid-weight-env.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.create_memory(
        parent_path="",
        content="weight fallback regression sample",
        priority=1,
        title="weight_fallback",
        domain="core",
    )

    payload = await client.search_advanced(
        query="weight fallback regression",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    await client.close()

    assert client._rerank_weight == pytest.approx(0.25)
    assert client._weight_vector == pytest.approx(0.7)
    assert client._weight_text == pytest.approx(0.3)
    assert client._weight_priority == pytest.approx(0.1)
    assert client._weight_recency == pytest.approx(0.06)
    assert client._weight_path_prefix == pytest.approx(0.04)
    assert client._recency_half_life_days == pytest.approx(30.0)
    assert payload["degraded"] is False
    assert payload["results"]


@pytest.mark.asyncio
async def test_search_advanced_embedding_fallback_then_cache_hit_is_observable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "hash")
    db_path = tmp_path / "week3-embedding-cache-fallback.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.create_memory(
        parent_path="",
        content="embedding cache fallback regression sample",
        priority=1,
        title="cache_fallback",
        domain="core",
    )

    client._embedding_backend = "api"
    client._embedding_api_base = "https://embedding.example/v1"
    client._embedding_api_key = "test-key"
    client._embedding_model = "test-model"
    client._reranker_enabled = False

    post_call_count = {"value": 0}

    async def _fake_post_json(*_args, **_kwargs):
        post_call_count["value"] += 1
        return None

    monkeypatch.setattr(client, "_post_json", _fake_post_json)

    first_payload = await client.search_advanced(
        query="cache fallback query",
        mode="semantic",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    second_payload = await client.search_advanced(
        query="cache fallback query",
        mode="semantic",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    status_payload = await client.get_index_status()
    await client.close()

    assert "embedding_request_failed" in first_payload.get("degrade_reasons", [])
    assert "embedding_fallback_hash" in first_payload.get("degrade_reasons", [])
    assert post_call_count["value"] == 1
    assert second_payload.get("degrade_reasons", []) == []
    assert second_payload["degraded"] is False
    assert int(status_payload["counts"]["embedding_cache"]) >= 1
