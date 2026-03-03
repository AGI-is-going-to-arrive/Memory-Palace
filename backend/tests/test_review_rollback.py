from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
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


@pytest.mark.asyncio
async def test_rollback_path_create_cascades_descendants_under_alias_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-create-alias-cascade.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    root = await client.create_memory(
        parent_path="",
        content="root content",
        priority=1,
        title="parent",
        domain="core",
    )
    await client.add_path(
        new_path="aliasparent",
        target_path="parent",
        new_domain="writer",
        target_domain="core",
    )
    alias_child = await client.create_memory(
        parent_path="aliasparent",
        content="alias child content",
        priority=1,
        title="child",
        domain="writer",
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
    assert payload["descendants_deleted"] >= 1
    assert await client.get_memory_by_path("parent", "core", reinforce_access=False) is None
    assert await client.get_memory_by_path(
        "aliasparent", "writer", reinforce_access=False
    ) is None
    assert await client.get_memory_by_path(
        "aliasparent/child", "writer", reinforce_access=False
    ) is None
    assert await client.get_memory_by_id(alias_child["id"]) is None

    await client.close()


@pytest.mark.asyncio
async def test_rollback_path_delete_rejects_restore_when_parent_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-delete-missing-parent.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    await client.create_memory(
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

    await client.remove_path("parent/child", "core")
    await client.remove_path("parent", "core")

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: client)

    with pytest.raises(HTTPException) as exc_info:
        await review_api._rollback_path(
            {
                "operation_type": "delete",
                "domain": "core",
                "path": "parent/child",
                "uri": "core://parent/child",
                "memory_id": child["id"],
                "priority": 1,
                "disclosure": None,
            }
        )

    assert exc_info.value.status_code == 409
    assert "Parent path 'core://parent' not found" in str(exc_info.value.detail)
    assert (
        await client.get_memory_by_path("parent/child", "core", reinforce_access=False)
        is None
    )
    await client.close()


@pytest.mark.asyncio
async def test_rollback_path_create_alias_returns_409_when_alias_still_exists_after_remove_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AliasStillExistsClient:
        async def remove_path(self, path: str, domain: str) -> None:
            _ = path
            _ = domain
            raise ValueError("alias_remove_failed")

        async def get_memory_by_path(
            self,
            path: str,
            domain: str,
            reinforce_access: bool = False,
        ):
            _ = path
            _ = domain
            _ = reinforce_access
            return {"id": 42}

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: _AliasStillExistsClient())

    with pytest.raises(HTTPException) as exc_info:
        await review_api._rollback_path(
            {
                "operation_type": "create_alias",
                "domain": "core",
                "path": "parent-alias",
                "uri": "core://parent-alias",
            }
        )

    assert exc_info.value.status_code == 409
    assert "Cannot rollback alias 'core://parent-alias'" in str(exc_info.value.detail)


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


def test_diff_endpoint_rejects_invalid_session_id_with_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "review-test-secret")
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)

    app = FastAPI()
    app.include_router(review_api.router)

    with TestClient(app) as client:
        response = client.get(
            "/review/sessions/abc%5Cdef/diff/core%3A%2F%2Fmemory-palace",
            headers={"X-MCP-API-Key": "review-test-secret"},
        )

    assert response.status_code == 400
    assert "Invalid session_id" in str(response.json().get("detail"))


def test_snapshot_manager_rejects_traversal_session_id(tmp_path: Path) -> None:
    manager = SnapshotManager(str(tmp_path / "snapshots"))
    with pytest.raises(ValueError):
        manager.clear_session("..")
