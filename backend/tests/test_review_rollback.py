from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import review as review_api
from db.snapshot import SnapshotManager
from db.sqlite_client import SQLiteClient


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


@pytest.mark.asyncio
async def test_rollback_path_create_cascades_descendants_and_cleans_orphans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-create-cascade.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    root = await client.create_memory(
        parent_path="",
        content="root content",
        priority=1,
        title="parent",
        domain="core",
    )
    child = await client.create_memory(
        parent_path="parent",
        content="child content",
        priority=1,
        title="child",
        domain="core",
    )
    grandchild = await client.create_memory(
        parent_path="parent/child",
        content="grandchild content",
        priority=1,
        title="grand",
        domain="core",
    )

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: client)

    payload = await review_api._rollback_path(
        {
            "operation_type": "create",
            "domain": "core",
            "path": "parent",
            "uri": "core://parent",
            "memory_id": root["id"],
        }
    )

    assert payload["deleted"] is True
    assert payload["descendants_deleted"] == 2
    assert payload["orphan_memories_deleted"] >= 2

    assert await client.get_memory_by_path("parent", "core") is None
    assert await client.get_memory_by_path("parent/child", "core") is None
    assert await client.get_memory_by_path("parent/child/grand", "core") is None

    assert await client.get_memory_by_id(root["id"]) is None
    assert await client.get_memory_by_id(child["id"]) is None
    assert await client.get_memory_by_id(grandchild["id"]) is None

    await client.close()


class _StubSnapshotManager:
    def get_snapshot(self, _session_id: str, resource_id: str):
        return {
            "resource_id": resource_id,
            "resource_type": "path",
            "snapshot_time": "2026-02-19T00:00:00",
            "data": {
                "operation_type": "create",
                "domain": "core",
                "path": resource_id,
                "uri": f"core://{resource_id}",
            },
        }


def test_rollback_endpoint_returns_5xx_when_internal_error_occurs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(_data: dict) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(review_api, "get_snapshot_manager", lambda: _StubSnapshotManager())
    monkeypatch.setattr(review_api, "_rollback_path", _boom)
    monkeypatch.setenv("MCP_API_KEY", "review-test-secret")
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)

    app = FastAPI()
    app.include_router(review_api.router)

    with TestClient(app) as client:
        response = client.post(
            "/review/sessions/s1/rollback/parent",
            json={},
            headers={"X-MCP-API-Key": "review-test-secret"},
        )

    assert response.status_code == 500
    assert "Rollback failed: boom" in str(response.json().get("detail"))


def test_snapshot_manager_rejects_traversal_session_id(tmp_path: Path) -> None:
    manager = SnapshotManager(str(tmp_path / "snapshots"))
    with pytest.raises(ValueError):
        manager.clear_session("..")
