import pytest

import mcp_server


async def _noop_ensure_started(_factory) -> None:
    return None


async def _guard_summary_ok():
    return {
        "total_events": 3,
        "blocked_events": 1,
        "degraded_events": 0,
        "last_event_at": "2026-03-01T12:00:00Z",
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
        "pending_events": 2,
        "pending_chars": 42,
        "trigger_chars": 6000,
        "min_events": 6,
        "max_events_per_session": 80,
        "top_sessions": [{"session_id": "s1", "events": 2, "chars": 42}],
    }


class _AuditClientOk:
    async def get_index_status(self):
        return {"index_available": True, "degraded": False}

    async def get_gist_stats(self):
        return {
            "degraded": False,
            "total_rows": 12,
            "active_coverage": 0.75,
        }

    async def get_vitality_stats(self):
        return {
            "degraded": False,
            "total_memories": 20,
            "low_vitality_count": 3,
        }

    async def get_recent_memories(self, limit: int = 10):
        _ = limit
        return [
            {
                "memory_id": 7,
                "uri": "core://agent/index",
                "created_at": "2026-03-01T11:00:00Z",
            }
        ]

    async def get_latest_memory_gist(self, memory_id: int):
        if memory_id != 7:
            return None
        return {
            "gist_text": "Index rebuilt with healthy queue depth.",
            "gist_method": "extractive_bullets",
            "quality_score": 0.81,
        }


class _AuditClientNoGist:
    async def get_index_status(self):
        return {"index_available": True, "degraded": False}

    async def get_vitality_stats(self):
        return {"degraded": False, "total_memories": 5, "low_vitality_count": 0}


class _AuditClientGistDegraded(_AuditClientOk):
    async def get_gist_stats(self):
        return {
            "degraded": True,
            "reason": "gist_backend_timeout",
            "total_rows": 12,
            "active_coverage": 0.75,
        }


class _IndexLiteEmptyClient:
    async def get_gist_stats(self):
        return {"degraded": False, "total_rows": 0, "active_coverage": 0.0}

    async def get_recent_memories(self, limit: int = 10):
        _ = limit
        return []

    async def get_latest_memory_gist(self, memory_id: int):
        _ = memory_id
        return None


@pytest.mark.asyncio
async def test_read_memory_system_audit_includes_required_sections(
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

    raw = await mcp_server.read_memory("system://audit")

    assert "# System Audit" in raw
    assert "## Index" in raw
    assert "## Guard" in raw
    assert "## Gist" in raw
    assert "## Vitality" in raw
    assert "## SM-Lite (Runtime Working Set)" in raw
    assert "promotion.total_promotions" in raw
    assert "# Status: ok" in raw


@pytest.mark.asyncio
async def test_read_memory_system_audit_reports_degrade_reason_when_module_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _AuditClientNoGist())
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

    raw = await mcp_server.read_memory("system://audit")

    assert "# Status: degraded" in raw
    assert "degrade_reason" in raw
    assert "gist:unavailable" in raw


@pytest.mark.asyncio
async def test_read_memory_system_index_lite_handles_empty_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _IndexLiteEmptyClient())

    raw = await mcp_server.read_memory("system://index-lite")

    assert "# Memory Index Lite" in raw
    assert "# Entry count: 0" in raw
    assert "(No gist-backed entries found.)" in raw


@pytest.mark.asyncio
async def test_read_memory_system_index_lite_lists_gist_backed_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _AuditClientOk())

    raw = await mcp_server.read_memory("system://index-lite")

    assert "# Memory Index Lite" in raw
    assert "# Entry count: 1" in raw
    assert "core://agent/index" in raw
    assert "method=extractive_bullets" in raw


@pytest.mark.asyncio
async def test_read_memory_system_audit_marks_degraded_when_gist_payload_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _AuditClientGistDegraded())
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

    raw = await mcp_server.read_memory("system://audit")

    assert "# Status: degraded" in raw
    assert "gist:gist_backend_timeout" in raw
