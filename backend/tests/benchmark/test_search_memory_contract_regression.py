import json
from typing import Any, Dict

import pytest

import mcp_server
from api import maintenance as maintenance_api


async def _noop_async(*_: Any, **__: Any) -> None:
    return None


class _FakeSearchClient:
    def __init__(self) -> None:
        self.meta_store: Dict[str, str] = {}

    def preprocess_query(self, query: str) -> Dict[str, Any]:
        return {
            "original_query": query,
            "normalized_query": query.strip().lower(),
            "rewritten_query": query.strip(),
            "tokens": query.split(),
            "changed": False,
        }

    def classify_intent(self, _query: str, _rewritten_query: str) -> Dict[str, Any]:
        return {
            "intent": "factual",
            "strategy_template": "factual_high_precision",
            "method": "rule",
            "confidence": 0.9,
            "signals": ["rule_match"],
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
        _ = query
        _ = mode
        _ = max_results
        _ = candidate_multiplier
        _ = filters
        profile = dict(intent_profile or {})
        return {
            "mode": "hybrid",
            "degraded": False,
            "degrade_reasons": [],
            "results": [
                {
                    "memory_id": 1,
                    "uri": "core://agent/index",
                    "snippet": "index report",
                    "priority": 0,
                    "updated_at": "2026-02-18T00:00:00Z",
                    "metadata": {"domain": "core", "path": "agent/index", "priority": 0},
                }
            ],
            "metadata": {
                "intent": profile.get("intent", "factual"),
                "strategy_template": profile.get(
                    "strategy_template", "factual_high_precision"
                ),
                "candidate_multiplier_applied": candidate_multiplier,
            },
        }

    async def get_index_status(self) -> Dict[str, Any]:
        return {"degraded": False, "index_available": True}

    async def get_runtime_meta(self, key: str) -> str | None:
        return self.meta_store.get(key)

    async def set_runtime_meta(self, key: str, value: str) -> None:
        self.meta_store[key] = value


@pytest.mark.asyncio
async def test_search_memory_contract_regression_contains_required_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.search_memory(
        "release plan",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=3,
        include_session=False,
        filters={"domain": "core"},
    )
    payload = json.loads(raw)

    required_keys = {
        "ok",
        "query",
        "query_effective",
        "mode_requested",
        "mode_applied",
        "max_results",
        "candidate_multiplier",
        "results",
        "degraded",
        "intent",
        "intent_profile",
        "strategy_template",
        "backend_method",
    }

    assert payload["ok"] is True
    assert required_keys.issubset(payload.keys())
    assert payload["mode_requested"] == "hybrid"
    assert payload["mode_applied"] == "hybrid"
    assert payload["degraded"] is False
    assert payload["intent"] == "factual"
    assert payload["intent_applied"] == "factual"
    assert payload["strategy_template_applied"] == "factual_high_precision"
    assert payload["candidate_multiplier_applied"] == 3
    assert isinstance(payload["results"], list)
    assert payload["results"][0]["uri"] == "core://agent/index"


@pytest.mark.asyncio
async def test_observability_search_contract_regression_contains_required_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    maintenance_api._search_events_loaded = False

    payload = maintenance_api.SearchConsoleRequest(
        query="release plan",
        mode="hybrid",
        max_results=5,
        candidate_multiplier=3,
        include_session=False,
    )
    result = await maintenance_api.run_observability_search(payload)

    required_keys = {
        "ok",
        "query",
        "query_effective",
        "intent",
        "intent_profile",
        "intent_applied",
        "strategy_template",
        "strategy_template_applied",
        "mode_requested",
        "mode_applied",
        "filters",
        "max_results",
        "candidate_multiplier",
        "degraded",
        "degrade_reasons",
        "counts",
        "results",
        "backend_metadata",
        "timestamp",
    }

    assert result["ok"] is True
    assert required_keys.issubset(result.keys())
    assert result["mode_requested"] == "hybrid"
    assert result["mode_applied"] == "hybrid"
    assert result["degraded"] is False
    assert result["degrade_reasons"] == []
    assert result["counts"]["session"] == 0
    assert result["counts"]["global"] == 1
    assert result["counts"]["returned"] == 1
    assert result["results"][0]["uri"] == "core://agent/index"
    assert fake_client.meta_store.get("observability.search_events.v1")
