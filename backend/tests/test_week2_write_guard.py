import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

import mcp_server
from api import browse as browse_api
from api import maintenance as maintenance_api
from db.sqlite_client import SQLiteClient
from runtime_state import GuardDecisionTracker


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


class _FakeClient:
    def __init__(
        self,
        *,
        guard_decision: Dict[str, Any],
        memory: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.guard_decision = guard_decision
        self.memory = memory or {
            "id": 7,
            "content": "hello world",
            "priority": 1,
            "disclosure": None,
        }
        self.create_called = False
        self.update_called = False
        self.update_payload: Dict[str, Any] = {}

    async def write_guard(self, **_: Any) -> Dict[str, Any]:
        return dict(self.guard_decision)

    async def create_memory(self, **_: Any) -> Dict[str, Any]:
        self.create_called = True
        return {
            "id": 11,
            "path": "agent/new_note",
            "uri": "core://agent/new_note",
            "index_targets": [11],
        }

    async def get_memory_by_path(self, path: str, domain: str = "core") -> Optional[Dict[str, Any]]:
        _ = path
        _ = domain
        return dict(self.memory)

    async def update_memory(self, **kwargs: Any) -> Dict[str, Any]:
        self.update_called = True
        self.update_payload = dict(kwargs)
        return {
            "uri": f"{kwargs.get('domain', 'core')}://{kwargs.get('path', '')}",
            "new_memory_id": 19,
            "index_targets": [19],
        }


async def _noop_async(*_: Any, **__: Any) -> None:
    return None


async def _false_async(*_: Any, **__: Any) -> bool:
    return False


async def _empty_list_async(*_: Any, **__: Any) -> list[Any]:
    return []


async def _run_write_inline(_operation: str, task):
    return await task()


def _patch_mcp_dependencies(monkeypatch: pytest.MonkeyPatch, fake_client: _FakeClient) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server, "_record_guard_event", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_record_flush_event", _noop_async)
    monkeypatch.setattr(mcp_server, "_maybe_auto_flush", _noop_async)
    monkeypatch.setattr(mcp_server, "_snapshot_path_create", _noop_async)
    monkeypatch.setattr(mcp_server, "_snapshot_memory_content", _noop_async)
    monkeypatch.setattr(mcp_server, "_snapshot_path_meta", _noop_async)
    monkeypatch.setattr(mcp_server, "_should_defer_index_on_write", _false_async)
    monkeypatch.setattr(mcp_server, "_enqueue_index_targets", _empty_list_async)
    monkeypatch.setattr(mcp_server, "_run_write_lane", _run_write_inline)


@pytest.mark.asyncio
async def test_write_guard_identical_content_hits_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "guard-identical.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    created = await client.create_memory(
        parent_path="",
        content="alpha beta gamma",
        priority=1,
        title="note_a",
        domain="core",
    )
    decision = await client.write_guard(content="alpha beta gamma", domain="core")
    await client.close()

    assert decision["action"] == "NOOP"
    assert decision["target_id"] == created["id"]
    assert decision["method"] in {"embedding", "keyword"}


@pytest.mark.asyncio
async def test_write_guard_exclude_memory_id_allows_add(tmp_path: Path) -> None:
    db_path = tmp_path / "guard-exclude.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    created = await client.create_memory(
        parent_path="",
        content="exclusive payload",
        priority=1,
        title="note_b",
        domain="core",
    )
    decision = await client.write_guard(
        content="exclusive payload",
        domain="core",
        exclude_memory_id=created["id"],
    )
    await client.close()

    assert decision["action"] == "ADD"
    assert decision["target_id"] is None


@pytest.mark.asyncio
async def test_create_memory_is_blocked_when_guard_returns_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        guard_decision={
            "action": "NOOP",
            "reason": "duplicate content",
            "method": "embedding",
            "target_id": 7,
            "target_uri": "core://agent/existing",
        }
    )
    _patch_mcp_dependencies(monkeypatch, fake_client)

    raw = await mcp_server.create_memory(
        parent_uri="core://agent",
        content="duplicate content",
        priority=1,
        title="new_note",
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["created"] is False
    assert payload["guard_action"] == "NOOP"
    assert fake_client.create_called is False


@pytest.mark.asyncio
async def test_create_memory_returns_guard_fields_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        guard_decision={
            "action": "ADD",
            "reason": "no strong duplicate signal",
            "method": "keyword",
        }
    )
    _patch_mcp_dependencies(monkeypatch, fake_client)

    raw = await mcp_server.create_memory(
        parent_uri="core://agent",
        content="new information",
        priority=2,
        title="fresh_note",
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["created"] is True
    assert payload["guard_action"] == "ADD"
    assert fake_client.create_called is True


@pytest.mark.asyncio
async def test_update_memory_is_blocked_when_guard_returns_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        guard_decision={
            "action": "NOOP",
            "reason": "no effective change",
            "method": "embedding",
            "target_id": 7,
            "target_uri": "core://agent/current",
        }
    )
    _patch_mcp_dependencies(monkeypatch, fake_client)

    raw = await mcp_server.update_memory(
        uri="core://agent/current",
        old_string="world",
        new_string="planet",
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["updated"] is False
    assert payload["guard_action"] == "NOOP"
    assert fake_client.update_called is False


@pytest.mark.asyncio
async def test_update_memory_metadata_only_marks_guard_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        guard_decision={
            "action": "ADD",
            "reason": "unused",
            "method": "keyword",
        }
    )
    _patch_mcp_dependencies(monkeypatch, fake_client)

    raw = await mcp_server.update_memory(
        uri="core://agent/current",
        priority=5,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["updated"] is True
    assert payload["guard_action"] == "BYPASS"
    assert fake_client.update_called is True


@pytest.mark.asyncio
async def test_browse_create_node_is_blocked_by_write_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        guard_decision={
            "action": "NOOP",
            "reason": "duplicate",
            "method": "embedding",
        }
    )
    monkeypatch.setattr(browse_api, "get_sqlite_client", lambda: fake_client)

    payload = await browse_api.create_node(
        browse_api.NodeCreate(
            parent_path="agent",
            title="new_note",
            content="duplicate",
            priority=1,
            domain="core",
        )
    )

    assert payload["success"] is True
    assert payload["created"] is False
    assert payload["guard_action"] == "NOOP"
    assert fake_client.create_called is False


@pytest.mark.asyncio
async def test_browse_create_node_records_guard_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        guard_decision={
            "action": "NOOP",
            "reason": "duplicate",
            "method": "embedding",
        }
    )
    tracker = GuardDecisionTracker()
    monkeypatch.setattr(browse_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(browse_api.runtime_state, "guard_tracker", tracker)

    payload = await browse_api.create_node(
        browse_api.NodeCreate(
            parent_path="agent",
            title="new_note",
            content="duplicate",
            priority=1,
            domain="core",
        )
    )
    stats = await tracker.summary()

    assert payload["created"] is False
    assert stats["total_events"] == 1
    assert stats["blocked_events"] == 1
    assert stats["operation_breakdown"]["browse.create_node"] == 1


@pytest.mark.asyncio
async def test_browse_update_node_metadata_only_marks_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        guard_decision={
            "action": "ADD",
            "reason": "unused",
            "method": "keyword",
        }
    )
    monkeypatch.setattr(browse_api, "get_sqlite_client", lambda: fake_client)

    payload = await browse_api.update_node(
        path="agent/current",
        domain="core",
        body=browse_api.NodeUpdate(priority=9),
    )

    assert payload["success"] is True
    assert payload["updated"] is True
    assert payload["guard_action"] == "BYPASS"
    assert fake_client.update_called is True


@pytest.mark.asyncio
async def test_browse_update_node_blocks_guard_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        guard_decision={
            "action": "NOOP",
            "reason": "duplicate",
            "method": "keyword",
        }
    )
    monkeypatch.setattr(browse_api, "get_sqlite_client", lambda: fake_client)

    payload = await browse_api.update_node(
        path="agent/current",
        domain="core",
        body=browse_api.NodeUpdate(content="replace payload"),
    )

    assert payload["success"] is True
    assert payload["updated"] is False
    assert payload["guard_action"] == "NOOP"
    assert fake_client.update_called is False


@pytest.mark.asyncio
async def test_observability_summary_includes_guard_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyClient:
        async def get_index_status(self) -> Dict[str, Any]:
            return {"degraded": False, "index_available": True}

        async def get_gist_stats(self) -> Dict[str, Any]:
            return {
                "total_rows": 0,
                "distinct_memory_count": 0,
                "total_distinct_memory_count": 0,
                "active_memory_count": 0,
                "coverage_ratio": 0.0,
                "quality_coverage_ratio": 0.0,
                "avg_quality_score": 0.0,
                "method_breakdown": {},
                "latest_created_at": None,
            }

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

    tracker = GuardDecisionTracker()
    await tracker.record_event(
        operation="create_memory",
        action="NOOP",
        method="embedding",
        reason="duplicate",
        blocked=True,
    )

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: _DummyClient())
    monkeypatch.setattr(maintenance_api.runtime_state, "guard_tracker", tracker)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state.index_worker, "status", _index_worker_status)
    monkeypatch.setattr(maintenance_api.runtime_state.write_lanes, "status", _write_lane_status)

    payload = await maintenance_api.get_observability_summary()

    assert payload["status"] == "ok"
    assert "guard_stats" in payload
    assert payload["guard_stats"]["total_events"] == 1
    assert payload["guard_stats"]["blocked_events"] == 1
