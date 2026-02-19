import json
from pathlib import Path
from typing import Any, Dict

import pytest

import mcp_server
from api import maintenance as maintenance_api
from db.sqlite_client import SQLiteClient


async def _noop_async(*_: Any, **__: Any) -> None:
    return None


class _FakeSearchClient:
    def __init__(self) -> None:
        self.search_query: str = ""
        self.intent_profile: Dict[str, Any] = {}
        self.received_filters: Dict[str, Any] = {}

    def preprocess_query(self, query: str) -> Dict[str, Any]:
        rewritten = " ".join(query.lower().replace("?", "").split())
        return {
            "original_query": query,
            "normalized_query": rewritten,
            "rewritten_query": rewritten,
            "tokens": rewritten.split(),
            "changed": rewritten != query,
        }

    def classify_intent(self, _query: str, rewritten_query: str) -> Dict[str, Any]:
        if "when" in rewritten_query:
            return {
                "intent": "temporal",
                "strategy_template": "temporal_time_filtered",
                "method": "keyword_heuristic",
                "confidence": 0.86,
                "signals": ["temporal_keywords"],
            }
        if "why" in rewritten_query:
            return {
                "intent": "causal",
                "strategy_template": "causal_wide_pool",
                "method": "keyword_heuristic",
                "confidence": 0.82,
                "signals": ["causal_keywords"],
            }
        return {
            "intent": "factual",
            "strategy_template": "factual_high_precision",
            "method": "keyword_heuristic",
            "confidence": 0.72,
            "signals": ["default_factual"],
        }

    async def search_advanced(
        self,
        *,
        query: str,
        mode: str,
        max_results: int,
        candidate_multiplier: int,
        filters: Dict[str, Any],
        intent_profile: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        self.search_query = query
        self.intent_profile = dict(intent_profile or {})
        self.received_filters = dict(filters)
        _ = mode
        _ = max_results
        _ = candidate_multiplier
        return {
            "mode": "hybrid",
            "degraded": False,
            "degrade_reasons": [],
            "metadata": {
                "intent": self.intent_profile.get("intent"),
                "strategy_template": self.intent_profile.get("strategy_template"),
                "candidate_multiplier_applied": candidate_multiplier,
            },
            "results": [
                {
                    "uri": "core://agent/index",
                    "memory_id": 11,
                    "snippet": "Index rebuilt last night.",
                    "priority": 1,
                    "updated_at": "2026-02-16T12:00:00Z",
                    "scores": {"final": 0.9, "text": 0.6, "vector": 0.7},
                    "metadata": {
                        "domain": "core",
                        "path": "agent/index",
                        "priority": 1,
                        "updated_at": "2026-02-16T12:00:00Z",
                    },
                }
            ],
        }


class _NoIntentClient:
    def __init__(self) -> None:
        self.search_query: str = ""
        self.intent_profile: Dict[str, Any] = {}

    def preprocess_query(self, query: str) -> Dict[str, Any]:
        return {
            "original_query": query,
            "normalized_query": query,
            "rewritten_query": query,
            "tokens": [],
            "changed": False,
        }

    async def search_advanced(
        self,
        *,
        query: str,
        mode: str,
        max_results: int,
        candidate_multiplier: int,
        filters: Dict[str, Any],
        intent_profile: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        self.search_query = query
        self.intent_profile = dict(intent_profile or {})
        _ = mode
        _ = max_results
        _ = candidate_multiplier
        _ = filters
        return {"mode": "hybrid", "degraded": False, "degrade_reasons": [], "results": []}


class _LegacySearchClient:
    def __init__(self) -> None:
        self.search_query: str = ""
        self.received_filters: Dict[str, Any] = {}

    def preprocess_query(self, query: str) -> Dict[str, Any]:
        return {
            "original_query": query,
            "normalized_query": query,
            "rewritten_query": query,
            "tokens": [],
            "changed": False,
        }

    def classify_intent(self, _query: str, _rewritten_query: str) -> Dict[str, Any]:
        return {
            "intent": "factual",
            "strategy_template": "factual_high_precision",
            "method": "keyword_heuristic",
            "confidence": 0.72,
            "signals": ["default_factual"],
        }

    async def search_advanced(
        self,
        *,
        query: str,
        mode: str,
        max_results: int,
        candidate_multiplier: int,
        filters: Dict[str, Any],
    ) -> Dict[str, Any]:
        self.search_query = query
        self.received_filters = dict(filters)
        _ = mode
        _ = max_results
        _ = candidate_multiplier
        return {"mode": "hybrid", "degraded": False, "degrade_reasons": [], "results": []}


@pytest.mark.asyncio
async def test_search_memory_uses_preprocessed_query_and_returns_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.search_memory(
        query="When did we rebuild index?",
        mode="hybrid",
        include_session=False,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["query"] == "When did we rebuild index?"
    assert payload["query_effective"] == "when did we rebuild index"
    assert payload["intent"] == "temporal"
    assert payload["intent_profile"]["strategy_template"] == "temporal_time_filtered"
    assert fake_client.search_query == "when did we rebuild index"
    assert fake_client.intent_profile["intent"] == "temporal"
    assert payload["intent_applied"] == "temporal"
    assert payload["strategy_template_applied"] == "temporal_time_filtered"
    assert payload["candidate_multiplier_applied"] == 4


@pytest.mark.asyncio
async def test_search_memory_degrades_to_unknown_when_classifier_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _NoIntentClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.search_memory(
        query="index diagnostics",
        mode="hybrid",
        include_session=False,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["intent"] == "unknown"
    assert payload["strategy_template"] == "default"
    assert "intent_classification_unavailable" in payload.get("degrade_reasons", [])
    assert fake_client.intent_profile == {}


@pytest.mark.asyncio
async def test_search_memory_supports_legacy_search_advanced_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _LegacySearchClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.search_memory(
        query="legacy compatibility",
        mode="hybrid",
        include_session=False,
        filters={"domain": "core"},
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert fake_client.search_query == "legacy compatibility"
    assert fake_client.received_filters == {"domain": "core"}


@pytest.mark.asyncio
async def test_observability_search_returns_intent_and_query_effective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()

    payload = maintenance_api.SearchConsoleRequest(
        query="Why did index rebuild fail?",
        mode="hybrid",
        include_session=False,
    )
    response = await maintenance_api.run_observability_search(payload)

    assert response["ok"] is True
    assert response["query"] == "Why did index rebuild fail?"
    assert response["query_effective"] == "why did index rebuild fail"
    assert response["intent"] == "causal"
    assert response["intent_profile"]["strategy_template"] == "causal_wide_pool"
    assert fake_client.search_query == "why did index rebuild fail"
    assert fake_client.intent_profile["intent"] == "causal"
    assert fake_client.received_filters == {}


@pytest.mark.asyncio
async def test_observability_search_accepts_integer_max_priority_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()

    payload = maintenance_api.SearchConsoleRequest(
        query="index diagnostics",
        mode="hybrid",
        include_session=False,
        filters={"max_priority": "2"},
    )
    response = await maintenance_api.run_observability_search(payload)

    assert response["ok"] is True
    assert fake_client.received_filters == {"max_priority": 2}


@pytest.mark.asyncio
async def test_observability_search_rejects_non_integer_max_priority_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)

    payload = maintenance_api.SearchConsoleRequest(
        query="index diagnostics",
        mode="hybrid",
        include_session=False,
        filters={"max_priority": "1.9"},
    )
    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.run_observability_search(payload)

    assert exc_info.value.status_code == 422
    assert "filters.max_priority must be an integer" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_preprocess_query_preserves_uri_and_multilingual_content(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week3-preprocess.db"
    client = SQLiteClient(f"sqlite+aiosqlite:///{db_path}")

    uri_result = client.preprocess_query("core://agent/index")
    mixed_lang_result = client.preprocess_query("昨天 index 为什么失败")

    await client.close()

    assert uri_result["rewritten_query"] == "core://agent/index"
    assert mixed_lang_result["rewritten_query"] == "昨天 index 为什么失败"


@pytest.mark.asyncio
async def test_observability_session_cache_uses_original_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient()
    captured: Dict[str, Any] = {}

    async def _ensure_started(_factory) -> None:
        return None

    async def _session_search(*, session_id: str, query: str, limit: int):
        captured["session_id"] = session_id
        captured["query"] = query
        captured["limit"] = limit
        return []

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state.session_cache, "search", _session_search)

    payload = maintenance_api.SearchConsoleRequest(
        query="Why did index rebuild fail?",
        mode="hybrid",
        include_session=True,
        session_id="api-observability",
    )
    response = await maintenance_api.run_observability_search(payload)

    assert response["ok"] is True
    assert captured["query"] == "Why did index rebuild fail?"
    assert captured["session_id"] == "api-observability"
