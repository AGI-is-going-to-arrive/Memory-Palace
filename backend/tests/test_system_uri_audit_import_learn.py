import json

import pytest

import mcp_server


async def _noop_ensure_started(_factory) -> None:
    return None


async def _guard_summary_ok():
    return {
        "total_events": 2,
        "blocked_events": 0,
        "degraded_events": 0,
        "last_event_at": "2026-03-02T08:00:00Z",
    }


async def _session_cache_summary_ok():
    return {
        "session_count": 1,
        "total_hits": 2,
        "max_hits_in_session": 2,
        "max_hits_per_session": 200,
        "half_life_seconds": 21600.0,
        "top_sessions": [{"session_id": "s1", "hits": 2}],
    }


async def _flush_tracker_summary_ok():
    return {
        "session_count": 1,
        "pending_events": 0,
        "pending_chars": 0,
        "trigger_chars": 6000,
        "min_events": 6,
        "max_events_per_session": 80,
        "top_sessions": [],
    }


async def _promotion_summary_ok():
    return {
        "window_size": 200,
        "total_promotions": 0,
        "degraded_promotions": 0,
        "source_breakdown": {},
        "reason_breakdown": {},
        "gist_method_breakdown": {},
        "avg_quality": 0.0,
        "index_queue": {"queued": 0, "dropped": 0, "deduped": 0},
        "top_sessions": [],
        "last_promotion_at": None,
    }


async def _import_learn_summary_ok():
    return {
        "window_size": 300,
        "total_events": 4,
        "event_type_breakdown": {"learn": 2, "reject": 1, "rollback": 1},
        "operation_breakdown": {"learn_explicit": 4},
        "decision_breakdown": {"accepted": 1, "rejected": 2, "rolled_back": 1},
        "rejected_events": 2,
        "rollback_events": 1,
        "top_reasons": [{"reason": "prepared", "count": 1}],
        "last_event_at": "2026-03-02T08:05:00Z",
        "recent_events": [],
    }


class _AuditClientOk:
    def __init__(self, persisted_summary=None):
        self._persisted_summary = persisted_summary

    async def get_index_status(self):
        return {"index_available": True, "degraded": False}

    async def get_gist_stats(self):
        return {"degraded": False, "total_rows": 12, "active_coverage": 0.75}

    async def get_vitality_stats(self):
        return {"degraded": False, "total_memories": 20, "low_vitality_count": 3}

    async def get_runtime_meta(self, key: str):
        if key != mcp_server.IMPORT_LEARN_AUDIT_META_KEY:
            return None
        if self._persisted_summary is None:
            return None
        return json.dumps(self._persisted_summary, ensure_ascii=False)


@pytest.mark.asyncio
async def test_system_audit_includes_import_learn_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _AuditClientOk())
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _noop_ensure_started)
    monkeypatch.setattr(
        mcp_server.runtime_state.guard_tracker, "summary", _guard_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.session_cache, "summary", _session_cache_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.flush_tracker, "summary", _flush_tracker_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.promotion_tracker, "summary", _promotion_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.import_learn_tracker, "summary", _import_learn_summary_ok
    )

    raw = await mcp_server.read_memory("system://audit")

    assert "# System Audit" in raw
    assert "## Import/Learn" in raw
    assert "- total_events: 4" in raw
    assert "- rejected_events: 2" in raw
    assert "- rollback_events: 1" in raw
    assert "# Status: ok" in raw


@pytest.mark.asyncio
async def test_system_audit_marks_degraded_when_import_learn_summary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _broken_import_learn_summary():
        raise RuntimeError("import_learn_tracker_failed")

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _AuditClientOk())
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _noop_ensure_started)
    monkeypatch.setattr(
        mcp_server.runtime_state.guard_tracker, "summary", _guard_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.session_cache, "summary", _session_cache_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.flush_tracker, "summary", _flush_tracker_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.promotion_tracker, "summary", _promotion_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.import_learn_tracker,
        "summary",
        _broken_import_learn_summary,
    )

    raw = await mcp_server.read_memory("system://audit")

    assert "# Status: degraded" in raw
    assert "import_learn:error" in raw
    assert "## Import/Learn" in raw


@pytest.mark.asyncio
async def test_system_audit_uses_persisted_import_learn_snapshot_when_runtime_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _import_learn_summary_empty():
        return {
            "window_size": 300,
            "total_events": 0,
            "event_type_breakdown": {},
            "operation_breakdown": {},
            "decision_breakdown": {},
            "rejected_events": 0,
            "rollback_events": 0,
            "top_reasons": [],
            "last_event_at": None,
            "recent_events": [],
        }

    persisted_summary = {
        "window_size": 300,
        "total_events": 9,
        "event_type_breakdown": {"learn": 5, "reject": 3, "rollback": 1},
        "operation_breakdown": {"learn_explicit": 9},
        "decision_breakdown": {"accepted": 5, "rejected": 3, "rolled_back": 1},
        "rejected_events": 3,
        "rollback_events": 1,
        "top_reasons": [{"reason": "prepared", "count": 5}],
        "last_event_at": "2026-03-02T09:00:00Z",
        "recent_events": [],
    }
    monkeypatch.setattr(
        mcp_server, "get_sqlite_client", lambda: _AuditClientOk(persisted_summary)
    )
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _noop_ensure_started)
    monkeypatch.setattr(
        mcp_server.runtime_state.guard_tracker, "summary", _guard_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.session_cache, "summary", _session_cache_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.flush_tracker, "summary", _flush_tracker_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.promotion_tracker, "summary", _promotion_summary_ok
    )
    monkeypatch.setattr(
        mcp_server.runtime_state.import_learn_tracker,
        "summary",
        _import_learn_summary_empty,
    )

    raw = await mcp_server.read_memory("system://audit")

    assert "## Import/Learn" in raw
    assert "- total_events: 9" in raw
    assert "- rejected_events: 3" in raw
    assert "- rollback_events: 1" in raw
    assert "- persisted_snapshot: true" in raw
