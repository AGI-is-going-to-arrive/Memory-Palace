import json

import pytest

import mcp_server


class _IndexStatusClient:
    async def get_index_status(self):
        return {
            "index_available": True,
            "degraded": False,
            "counts": {"active_memories": 3},
        }


async def _noop_ensure_started(_factory) -> None:
    return None


@pytest.mark.asyncio
async def test_index_status_includes_sm_lite_runtime_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _IndexStatusClient())
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _noop_ensure_started)

    raw = await mcp_server.index_status()
    payload = json.loads(raw)

    assert payload["ok"] is True
    runtime_payload = payload["runtime"]
    assert "sm_lite" in runtime_payload
    sm_lite = runtime_payload["sm_lite"]

    assert sm_lite["storage"] == "runtime_ephemeral"
    assert sm_lite["promotion_path"] == "compact_context + auto_flush"
    assert "session_cache" in sm_lite
    assert "flush_tracker" in sm_lite
    assert "promotion" in sm_lite
    assert "session_count" in sm_lite["session_cache"]
    assert "pending_events" in sm_lite["flush_tracker"]
    assert "total_promotions" in sm_lite["promotion"]


@pytest.mark.asyncio
async def test_index_status_keeps_index_payload_when_sm_lite_summary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _IndexStatusClient())
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _noop_ensure_started)

    async def _raise_summary() -> dict:
        raise RuntimeError("session_cache_summary_error")

    monkeypatch.setattr(mcp_server.runtime_state.session_cache, "summary", _raise_summary)

    raw = await mcp_server.index_status()
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["index_available"] is True
    assert payload["degraded"] is True
    assert "sm_lite:session_cache_summary_error" in payload["degrade_reasons"]
    sm_lite = payload["runtime"]["sm_lite"]
    assert sm_lite["degraded"] is True
    assert sm_lite["reason"] == "session_cache_summary_error"
