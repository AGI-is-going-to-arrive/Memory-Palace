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
