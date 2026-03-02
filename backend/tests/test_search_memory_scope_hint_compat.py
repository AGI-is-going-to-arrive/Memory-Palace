import json
from typing import Any, Dict

import pytest

import mcp_server


async def _noop_async(*_: Any, **__: Any) -> None:
    return None


class _ScopeSearchClient:
    def __init__(self) -> None:
        self.received_filters: Dict[str, Any] = {}

    def preprocess_query(self, query: str) -> Dict[str, Any]:
        normalized = " ".join(query.strip().split())
        return {
            "original_query": query,
            "normalized_query": normalized,
            "rewritten_query": normalized,
            "tokens": normalized.lower().split(),
            "changed": normalized != query,
        }

    def classify_intent(self, _query: str, _rewritten_query: str) -> Dict[str, Any]:
        return {
            "intent": "factual",
            "strategy_template": "factual_high_precision",
            "method": "rule",
            "confidence": 0.8,
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
        _ = query
        _ = mode
        _ = max_results
        _ = candidate_multiplier
        _ = intent_profile
        self.received_filters = dict(filters)
        return {
            "mode": "hybrid",
            "degraded": False,
            "degrade_reasons": [],
            "results": [
                {
                    "uri": "core://agent/index",
                    "memory_id": 1,
                    "snippet": "index diagnostics",
                    "priority": 0,
                    "updated_at": "2026-03-01T12:00:00Z",
                    "metadata": {
                        "domain": "core",
                        "path": "agent/index",
                        "priority": 0,
                    },
                }
            ],
        }


@pytest.mark.asyncio
async def test_search_memory_scope_hint_applies_uri_prefix_and_echoes_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _ScopeSearchClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.search_memory(
        query="index diagnostics",
        mode="hybrid",
        include_session=False,
        scope_hint="core://agent",
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["scope_hint"] == "core://agent"
    assert payload["scope_hint_applied"] is True
    assert payload["scope_strategy_applied"] == "uri_prefix"
    assert payload["scope_effective"] == {"domain": "core", "path_prefix": "agent"}
    assert fake_client.received_filters == {"domain": "core", "path_prefix": "agent"}


@pytest.mark.asyncio
async def test_search_memory_without_scope_hint_keeps_legacy_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _ScopeSearchClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.search_memory(
        query="index diagnostics",
        mode="hybrid",
        include_session=False,
        filters={"domain": "core"},
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["scope_hint"] is None
    assert payload["scope_hint_applied"] is False
    assert payload["scope_strategy_applied"] == "none"
    assert payload["scope_effective"] == {"domain": "core", "path_prefix": None}
    assert fake_client.received_filters == {"domain": "core"}


@pytest.mark.asyncio
async def test_search_memory_accepts_scope_hint_inside_filters_for_compat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _ScopeSearchClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.search_memory(
        query="chapter arc",
        mode="hybrid",
        include_session=False,
        filters={"scope_hint": "writer://chapter_1"},
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["scope_hint"] == "writer://chapter_1"
    assert payload["scope_hint_applied"] is True
    assert payload["scope_effective"] == {
        "domain": "writer",
        "path_prefix": "chapter_1",
    }
    assert fake_client.received_filters == {
        "domain": "writer",
        "path_prefix": "chapter_1",
    }


@pytest.mark.asyncio
async def test_search_memory_scope_hint_domain_conflict_keeps_filters_as_source_of_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _ScopeSearchClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.search_memory(
        query="chapter arc",
        mode="hybrid",
        include_session=False,
        filters={"domain": "core"},
        scope_hint="writer://chapter_1",
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["scope_hint"] == "writer://chapter_1"
    assert payload["scope_hint_applied"] is False
    assert payload["scope_strategy_applied"] == "filters_preferred"
    assert payload["scope_effective"] == {"domain": "core", "path_prefix": None}
    assert payload["scope_conflicts"] == ["domain_conflict"]
    assert fake_client.received_filters == {"domain": "core"}
