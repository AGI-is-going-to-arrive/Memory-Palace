import asyncio
from typing import Any, Dict

import pytest

from api import maintenance as maintenance_api


class _FakeIntentClient:
    def __init__(self) -> None:
        self.meta_store: Dict[str, str] = {}
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
        _ = query
        _ = mode
        _ = max_results
        _ = candidate_multiplier
        self.received_filters = dict(filters)
        profile = intent_profile or {}
        return {
            "mode": "hybrid",
            "degraded": False,
            "degrade_reasons": [],
            "results": [],
            "metadata": {
                "intent": profile.get("intent"),
                "strategy_template": profile.get("strategy_template", "default"),
            },
        }

    async def get_index_status(self) -> Dict[str, Any]:
        return {"degraded": False, "index_available": True}

    async def get_gist_stats(self) -> Dict[str, Any]:
        return {"degraded": False, "total_rows": 0, "active_coverage": 0.0}

    async def get_vitality_stats(self) -> Dict[str, Any]:
        return {"degraded": False, "total_memories": 0, "low_vitality_count": 0}

    async def get_runtime_meta(self, key: str) -> str | None:
        return self.meta_store.get(key)

    async def set_runtime_meta(self, key: str, value: str) -> None:
        self.meta_store[key] = value


class _LegacyIntentClient(_FakeIntentClient):
    async def search_advanced(
        self,
        *,
        query: str,
        mode: str,
        max_results: int,
        candidate_multiplier: int,
        filters: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = query
        _ = mode
        _ = max_results
        _ = candidate_multiplier
        _ = filters
        return {
            "mode": "hybrid",
            "degraded": False,
            "degrade_reasons": [],
            "results": [],
            "metadata": {
                "intent": None,
                "strategy_template": "default",
            },
        }


class _RacePersistIntentClient(_FakeIntentClient):
    def __init__(self, delays: list[float]) -> None:
        super().__init__()
        self._delays = list(delays)
        self._set_call_count = 0

    async def set_runtime_meta(self, key: str, value: str) -> None:
        delay = 0.0
        if self._set_call_count < len(self._delays):
            delay = self._delays[self._set_call_count]
        self._set_call_count += 1
        if delay > 0:
            await asyncio.sleep(delay)
        await super().set_runtime_meta(key, value)


@pytest.mark.asyncio
async def test_observability_summary_tracks_intent_and_strategy_breakdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeIntentClient()

    async def _ensure_started(_factory) -> None:
        return None

    async def _index_worker_status() -> Dict[str, Any]:
        return {"enabled": True, "running": False, "recent_jobs": [], "stats": {}}

    async def _write_lane_status() -> Dict[str, Any]:
        return {
            "global_concurrency": 1,
            "global_active": 0,
            "global_waiting": 0,
            "session_waiting_count": 0,
            "session_waiting_sessions": 0,
            "max_session_waiting": 0,
            "wait_warn_ms": 2000,
        }

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state.index_worker, "status", _index_worker_status
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.write_lanes, "status", _write_lane_status
    )

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    maintenance_api._search_events_loaded = False

    temporal_payload = maintenance_api.SearchConsoleRequest(
        query="When did we rebuild index?",
        mode="hybrid",
        include_session=False,
    )
    causal_payload = maintenance_api.SearchConsoleRequest(
        query="Why did rebuild fail?",
        mode="hybrid",
        include_session=False,
    )

    temporal_result = await maintenance_api.run_observability_search(temporal_payload)
    causal_result = await maintenance_api.run_observability_search(causal_payload)
    summary = await maintenance_api.get_observability_summary()

    assert temporal_result["intent"] == "temporal"
    assert temporal_result["strategy_template"] == "temporal_time_filtered"
    assert causal_result["intent"] == "causal"
    assert causal_result["strategy_template"] == "causal_wide_pool"

    stats = summary["search_stats"]
    assert stats["intent_breakdown"]["temporal"] == 1
    assert stats["intent_breakdown"]["causal"] == 1
    assert stats["strategy_hit_breakdown"]["temporal_time_filtered"] == 1
    assert stats["strategy_hit_breakdown"]["causal_wide_pool"] == 1


@pytest.mark.asyncio
async def test_observability_marks_strategy_applied_from_backend_metadata_on_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _LegacyIntentClient()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    maintenance_api._search_events_loaded = False

    payload = maintenance_api.SearchConsoleRequest(
        query="When did we rebuild index?",
        mode="hybrid",
        include_session=False,
    )
    result = await maintenance_api.run_observability_search(payload)

    assert result["intent"] == "temporal"
    assert result["strategy_template"] == "temporal_time_filtered"
    assert result["intent_applied"] == "unknown"
    assert result["strategy_template_applied"] == "default"
    assert "intent_profile_not_supported" in result["degrade_reasons"]


@pytest.mark.asyncio
async def test_observability_search_scope_hint_applies_and_echoes_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeIntentClient()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    maintenance_api._search_events_loaded = False

    payload = maintenance_api.SearchConsoleRequest(
        query="When did we rebuild index?",
        mode="hybrid",
        include_session=False,
        scope_hint="core://agent",
    )
    result = await maintenance_api.run_observability_search(payload)

    assert result["scope_hint"] == "core://agent"
    assert result["scope_hint_applied"] is True
    assert result["scope_strategy_applied"] == "uri_prefix"
    assert result["scope_effective"] == {"domain": "core", "path_prefix": "agent"}
    assert fake_client.received_filters == {"domain": "core", "path_prefix": "agent"}


@pytest.mark.asyncio
async def test_observability_summary_includes_sm_lite_runtime_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeIntentClient()

    async def _ensure_started(_factory) -> None:
        return None

    async def _index_worker_status() -> Dict[str, Any]:
        return {"enabled": True, "running": False, "recent_jobs": [], "stats": {}}

    async def _write_lane_status() -> Dict[str, Any]:
        return {
            "global_concurrency": 1,
            "global_active": 0,
            "global_waiting": 0,
            "session_waiting_count": 0,
            "session_waiting_sessions": 0,
            "max_session_waiting": 0,
            "wait_warn_ms": 2000,
        }

    async def _session_cache_summary() -> Dict[str, Any]:
        return {
            "session_count": 2,
            "total_hits": 6,
            "max_hits_in_session": 4,
            "max_hits_per_session": 200,
            "half_life_seconds": 21600.0,
            "top_sessions": [],
        }

    async def _flush_tracker_summary() -> Dict[str, Any]:
        return {
            "session_count": 1,
            "pending_events": 3,
            "pending_chars": 20,
            "trigger_chars": 6000,
            "min_events": 6,
            "max_events_per_session": 80,
            "top_sessions": [],
        }

    async def _promotion_summary() -> Dict[str, Any]:
        return {
            "total_promotions": 2,
            "degraded_promotions": 1,
            "avg_quality": 0.74,
        }

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state.index_worker, "status", _index_worker_status
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.write_lanes, "status", _write_lane_status
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.session_cache, "summary", _session_cache_summary
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.flush_tracker, "summary", _flush_tracker_summary
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.promotion_tracker, "summary", _promotion_summary
    )

    summary = await maintenance_api.get_observability_summary()

    assert summary["status"] == "ok"
    runtime = summary["health"]["runtime"]
    assert "sm_lite" in runtime
    assert runtime["sm_lite"]["session_cache"]["session_count"] == 2
    assert runtime["sm_lite"]["flush_tracker"]["pending_events"] == 3
    assert runtime["sm_lite"]["promotion"]["total_promotions"] == 2


@pytest.mark.asyncio
async def test_observability_search_events_are_persisted_across_memory_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeIntentClient()

    async def _ensure_started(_factory) -> None:
        return None

    async def _index_worker_status() -> Dict[str, Any]:
        return {"enabled": True, "running": False, "recent_jobs": [], "stats": {}}

    async def _write_lane_status() -> Dict[str, Any]:
        return {
            "global_concurrency": 1,
            "global_active": 0,
            "global_waiting": 0,
            "session_waiting_count": 0,
            "session_waiting_sessions": 0,
            "max_session_waiting": 0,
            "wait_warn_ms": 2000,
        }

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state.index_worker, "status", _index_worker_status
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.write_lanes, "status", _write_lane_status
    )

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    maintenance_api._search_events_loaded = False

    payload = maintenance_api.SearchConsoleRequest(
        query="When did we rebuild index?",
        mode="hybrid",
        include_session=False,
    )
    result = await maintenance_api.run_observability_search(payload)

    assert result["ok"] is True
    assert fake_client.meta_store.get("observability.search_events.v1")

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    maintenance_api._search_events_loaded = False

    summary = await maintenance_api.get_observability_summary()
    assert summary["search_stats"]["total_queries"] == 1
    assert summary["search_stats"]["intent_breakdown"]["temporal"] == 1


@pytest.mark.asyncio
async def test_observability_persistence_avoids_concurrent_snapshot_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _RacePersistIntentClient(delays=[0.05, 0.0])

    async def _ensure_started(_factory) -> None:
        return None

    async def _index_worker_status() -> Dict[str, Any]:
        return {"enabled": True, "running": False, "recent_jobs": [], "stats": {}}

    async def _write_lane_status() -> Dict[str, Any]:
        return {
            "global_concurrency": 1,
            "global_active": 0,
            "global_waiting": 0,
            "session_waiting_count": 0,
            "session_waiting_sessions": 0,
            "max_session_waiting": 0,
            "wait_warn_ms": 2000,
        }

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state.index_worker, "status", _index_worker_status
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.write_lanes, "status", _write_lane_status
    )

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    maintenance_api._search_events_loaded = False

    temporal_payload = maintenance_api.SearchConsoleRequest(
        query="When did we rebuild index?",
        mode="hybrid",
        include_session=False,
    )
    causal_payload = maintenance_api.SearchConsoleRequest(
        query="Why did rebuild fail?",
        mode="hybrid",
        include_session=False,
    )

    await asyncio.gather(
        maintenance_api.run_observability_search(temporal_payload),
        maintenance_api.run_observability_search(causal_payload),
    )

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    maintenance_api._search_events_loaded = False

    summary = await maintenance_api.get_observability_summary()
    assert summary["search_stats"]["total_queries"] == 2
    assert summary["search_stats"]["intent_breakdown"]["temporal"] == 1
    assert summary["search_stats"]["intent_breakdown"]["causal"] == 1
