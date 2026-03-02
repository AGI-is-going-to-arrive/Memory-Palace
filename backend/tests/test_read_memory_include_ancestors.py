import json
from typing import Any, Dict, List

import pytest

import mcp_server


class _AncestorTreeClient:
    def __init__(self) -> None:
        self._nodes: Dict[str, Dict[str, Any]] = {
            "agent": {
                "id": 1,
                "domain": "core",
                "path": "agent",
                "priority": 1,
                "disclosure": "global profile",
                "content": "Root memory",
            },
            "agent/profile": {
                "id": 2,
                "domain": "core",
                "path": "agent/profile",
                "priority": 2,
                "disclosure": "profile context",
                "content": "Profile memory",
            },
            "agent/profile/preferences": {
                "id": 3,
                "domain": "core",
                "path": "agent/profile/preferences",
                "priority": 3,
                "disclosure": "preferences context",
                "content": "Preferences memory content",
            },
        }

    async def get_memory_by_path(self, path: str, domain: str = "core"):
        _ = domain
        node = self._nodes.get(path)
        return dict(node) if node else None

    async def get_children(self, _memory_id: int) -> List[Dict[str, Any]]:
        return []


class _BrokenAncestorTreeClient(_AncestorTreeClient):
    async def get_memory_by_path(self, path: str, domain: str = "core"):
        if path in {"agent/profile", "agent"}:
            raise RuntimeError("ancestor_lookup_failed")
        return await super().get_memory_by_path(path, domain)


class _SystemRecentClient:
    async def get_recent_memories(self, limit: int = 10):
        _ = limit
        return []


class _NoneSegmentClient(_AncestorTreeClient):
    async def read_memory_segment(self, **_kwargs: Any):
        return None


async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_read_memory_default_does_not_include_ancestors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _AncestorTreeClient())
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.read_memory("core://agent/profile/preferences")

    assert "MEMORY: core://agent/profile/preferences" in raw
    assert "ANCESTOR MEMORIES" not in raw


@pytest.mark.asyncio
async def test_read_memory_include_ancestors_renders_parent_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _AncestorTreeClient())
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.read_memory(
        "core://agent/profile/preferences",
        include_ancestors=True,
    )

    assert "ANCESTOR MEMORIES (Nearest Parent -> Root)" in raw
    assert "- URI: core://agent/profile [#2]" in raw
    assert "- URI: core://agent [#1]" in raw


@pytest.mark.asyncio
async def test_read_memory_partial_include_ancestors_returns_structured_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _AncestorTreeClient())
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.read_memory(
        "core://agent/profile/preferences",
        chunk_id=0,
        include_ancestors=True,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["include_ancestors"] is True
    ancestors = payload.get("ancestors", [])
    assert [item["uri"] for item in ancestors] == [
        "core://agent/profile",
        "core://agent",
    ]


@pytest.mark.asyncio
async def test_read_memory_partial_include_ancestors_reports_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_server, "get_sqlite_client", lambda: _BrokenAncestorTreeClient()
    )
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.read_memory(
        "core://agent/profile/preferences",
        chunk_id=0,
        include_ancestors=True,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["degraded"] is True
    assert "include_ancestors_lookup_failed" in payload.get("degrade_reasons", [])


@pytest.mark.asyncio
async def test_read_memory_partial_falls_back_when_segment_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _NoneSegmentClient())
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.read_memory(
        "core://agent/profile/preferences",
        chunk_id=0,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    reasons = payload.get("degrade_reasons", [])
    assert "sqlite_client partial-read API returned unsupported payload shape." not in reasons


@pytest.mark.asyncio
async def test_read_memory_legacy_include_ancestors_degrades_without_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_server, "get_sqlite_client", lambda: _BrokenAncestorTreeClient()
    )
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)

    raw = await mcp_server.read_memory(
        "core://agent/profile/preferences",
        include_ancestors=True,
    )

    assert not raw.startswith("Error:")
    assert "MEMORY: core://agent/profile/preferences" in raw
    assert "include_ancestors_lookup_failed" in raw


@pytest.mark.asyncio
async def test_read_memory_system_uri_forces_include_ancestors_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _SystemRecentClient())

    raw = await mcp_server.read_memory(
        "system://recent/1",
        chunk_id=0,
        include_ancestors=True,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["source"] == "system_uri"
    assert payload["include_ancestors"] is False
    assert "ancestors" not in payload
