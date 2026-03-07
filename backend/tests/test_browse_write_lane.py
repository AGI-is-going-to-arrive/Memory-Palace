from typing import Any, Dict, Optional

import pytest

from api import browse as browse_api


class _FakeBrowseClient:
    def __init__(self) -> None:
        self.memory = {
            "id": 7,
            "content": "origin",
            "priority": 1,
            "disclosure": None,
        }
        self.create_called = False
        self.update_called = False
        self.remove_called = False

    async def write_guard(self, **_: Any) -> Dict[str, Any]:
        return {
            "action": "ADD",
            "reason": "allow",
            "method": "keyword",
        }

    async def create_memory(self, **_: Any) -> Dict[str, Any]:
        self.create_called = True
        return {
            "id": 11,
            "path": "agent/new_note",
            "uri": "core://agent/new_note",
            "index_targets": [11],
        }

    async def get_memory_by_path(
        self, path: str, domain: str = "core", reinforce_access: bool = True
    ) -> Optional[Dict[str, Any]]:
        _ = path
        _ = domain
        _ = reinforce_access
        return dict(self.memory)

    async def update_memory(self, **_: Any) -> Dict[str, Any]:
        self.update_called = True
        return {
            "uri": "core://agent/new_note",
            "new_memory_id": 19,
            "index_targets": [19],
        }

    async def remove_path(self, path: str, domain: str = "core") -> Dict[str, Any]:
        _ = path
        _ = domain
        self.remove_called = True
        return {
            "deleted": True,
            "memory_id": 19,
            "descendants_deleted": 0,
            "orphan_memories_deleted": 0,
        }


@pytest.mark.asyncio
async def test_browse_write_endpoints_run_through_write_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeBrowseClient()
    lane_calls: list[dict[str, Any]] = []

    async def _run_write(*, session_id: Optional[str], operation: str, task):
        lane_calls.append({"session_id": session_id, "operation": operation})
        return await task()

    monkeypatch.setattr(browse_api, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(browse_api.runtime_state.write_lanes, "run_write", _run_write)
    monkeypatch.setattr(browse_api, "ENABLE_WRITE_LANE_QUEUE", True)

    create_payload = await browse_api.create_node(
        browse_api.NodeCreate(
            parent_path="agent",
            title="new_note",
            content="create payload",
            priority=1,
            domain="core",
        )
    )
    update_payload = await browse_api.update_node(
        path="agent/new_note",
        domain="core",
        body=browse_api.NodeUpdate(content="update payload"),
    )
    delete_payload = await browse_api.delete_node(path="agent/new_note", domain="core")

    assert create_payload["success"] is True
    assert create_payload["created"] is True
    assert update_payload["success"] is True
    assert update_payload["updated"] is True
    assert delete_payload["success"] is True

    assert fake_client.create_called is True
    assert fake_client.update_called is True
    assert fake_client.remove_called is True
    assert lane_calls == [
        {"session_id": None, "operation": "browse.create_node"},
        {"session_id": None, "operation": "browse.update_node"},
        {"session_id": None, "operation": "browse.delete_node"},
    ]
