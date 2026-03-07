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
    async def _boom(_data: dict, **_kwargs) -> dict:
        raise RuntimeError("boom-secret-detail")

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
    assert response.json().get("detail") == {
        "error": "rollback_failed",
        "reason": "internal_error",
        "operation": "rollback_resource",
    }


def test_list_deprecated_endpoint_hides_internal_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingClient:
        async def get_deprecated_memories(self):
            raise RuntimeError("deprecated-secret-detail")

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: _FailingClient())
    monkeypatch.setenv("MCP_API_KEY", "review-test-secret")
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)

    app = FastAPI()
    app.include_router(review_api.router)

    with TestClient(app) as client:
        response = client.get(
            "/review/deprecated",
            headers={"X-MCP-API-Key": "review-test-secret"},
        )

    assert response.status_code == 500
    assert response.json().get("detail") == {
        "error": "list_deprecated_failed",
        "reason": "internal_error",
        "operation": "list_deprecated_memories",
    }


def test_compare_text_hides_internal_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_text_a: str, _text_b: str):
        raise RuntimeError("diff-secret-detail")

    monkeypatch.setattr(review_api, "get_text_diff", _boom)
    monkeypatch.setenv("MCP_API_KEY", "review-test-secret")
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)

    app = FastAPI()
    app.include_router(review_api.router)

    with TestClient(app) as client:
        response = client.post(
            "/review/diff",
            json={"text_a": "old", "text_b": "new"},
            headers={"X-MCP-API-Key": "review-test-secret"},
        )

    assert response.status_code == 500
    assert response.json().get("detail") == {
        "error": "compare_text_failed",
        "reason": "internal_error",
        "operation": "compare_text",
    }


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


@pytest.mark.asyncio
async def test_rollback_path_create_alias_routes_writes_through_write_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_calls = []

    class _AliasClient:
        async def remove_path(self, path: str, domain: str):
            return {"removed_uri": f"{domain}://{path}"}

    async def _run_write_lane_stub(operation: str, task, *, session_id=None):
        lane_calls.append({"operation": operation, "session_id": session_id})
        return await task()

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: _AliasClient())
    monkeypatch.setattr(review_api, "_run_write_lane", _run_write_lane_stub)

    payload = await review_api._rollback_path(
        {
            "operation_type": "create_alias",
            "domain": "core",
            "path": "alias-node",
            "uri": "core://alias-node",
        },
        lane_session_id="review.rollback:lane-path",
    )

    assert payload == {"deleted": True, "alias_removed": True}
    assert lane_calls == [
        {
            "operation": "rollback.remove_alias",
            "session_id": "review.rollback:lane-path",
        }
    ]


@pytest.mark.asyncio
async def test_rollback_memory_content_routes_writes_through_write_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_calls = []

    class _MemoryContentClient:
        async def get_memory_version(self, memory_id: int):
            memory_id = int(memory_id)
            if memory_id == 123:
                return {"id": 123, "migrated_to": 999}
            if memory_id == 999:
                return {"id": 999, "migrated_to": None}
            return None

        async def get_memory_by_path(
            self,
            path: str,
            domain: str,
            reinforce_access: bool = False,
        ):
            _ = path, domain, reinforce_access
            return {"id": 999, "content": "current"}

        async def rollback_to_memory(self, path: str, memory_id: int, domain: str):
            _ = path, domain
            return {"restored_memory_id": int(memory_id)}

    async def _run_write_lane_stub(operation: str, task, *, session_id=None):
        lane_calls.append({"operation": operation, "session_id": session_id})
        return await task()

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: _MemoryContentClient())
    monkeypatch.setattr(review_api, "_run_write_lane", _run_write_lane_stub)

    payload = await review_api._rollback_memory_content(
        {
            "memory_id": 123,
            "path": "agent/node",
            "domain": "core",
            "uri": "core://agent/node",
            "all_paths": [],
        },
        lane_session_id="review.rollback:lane-memory",
    )

    assert payload == {"new_version": 123}
    assert lane_calls == [
        {
            "operation": "rollback.rollback_to_memory",
            "session_id": "review.rollback:lane-memory",
        }
    ]


@pytest.mark.asyncio
async def test_rollback_legacy_modify_routes_combined_restore_through_single_write_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_calls = []

    class _LegacyModifyClient:
        async def get_memory_version(self, memory_id: int):
            memory_id = int(memory_id)
            if memory_id == 123:
                return {"id": 123, "migrated_to": 999}
            if memory_id == 999:
                return {"id": 999, "migrated_to": None}
            return None

        async def get_memory_by_path(
            self,
            path: str,
            domain: str,
            reinforce_access: bool = False,
        ):
            _ = path, domain, reinforce_access
            return {"id": 999, "priority": 5, "disclosure": "new"}

        async def rollback_to_memory(
            self,
            path: str,
            memory_id: int,
            domain: str,
            *,
            restore_path_metadata: bool = False,
            restore_priority: int | None = None,
            restore_disclosure: str | None = None,
        ):
            _ = path, domain
            assert restore_path_metadata is True
            assert restore_priority == 1
            assert restore_disclosure == "old"
            return {"restored_memory_id": int(memory_id)}

        async def update_memory(self, **_: object):
            raise AssertionError("update_memory should not run for combined legacy rollback")

    async def _run_write_lane_stub(operation: str, task, *, session_id=None):
        lane_calls.append({"operation": operation, "session_id": session_id})
        return await task()

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: _LegacyModifyClient())
    monkeypatch.setattr(review_api, "_run_write_lane", _run_write_lane_stub)

    payload = await review_api._rollback_legacy_modify(
        {
            "memory_id": 123,
            "path": "agent/node",
            "domain": "core",
            "uri": "core://agent/node",
            "priority": 1,
            "disclosure": "old",
        },
        lane_session_id="review.rollback:legacy-combined",
    )

    assert payload == {"new_version": 123}
    assert lane_calls == [
        {
            "operation": "rollback.restore_legacy_modify",
            "session_id": "review.rollback:legacy-combined",
        }
    ]


@pytest.mark.asyncio
async def test_rollback_legacy_modify_restores_content_and_metadata_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-legacy-modify-success.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="original content",
        priority=1,
        title="node",
        domain="core",
    )
    await client.update_memory(
        path="node",
        domain="core",
        content="current content",
        priority=5,
        disclosure="current disclosure",
    )

    lane_calls = []

    async def _run_write_lane_stub(operation: str, task, *, session_id=None):
        lane_calls.append({"operation": operation, "session_id": session_id})
        return await task()

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(review_api, "_run_write_lane", _run_write_lane_stub)

    payload = await review_api._rollback_legacy_modify(
        {
            "memory_id": created["id"],
            "path": "node",
            "domain": "core",
            "uri": "core://node",
            "priority": 1,
            "disclosure": None,
        },
        lane_session_id="review.rollback:legacy-success",
    )

    current = await client.get_memory_by_path("node", "core", reinforce_access=False)

    assert payload == {"new_version": created["id"]}
    assert current is not None
    assert current["id"] == created["id"]
    assert current["content"] == "original content"
    assert current["priority"] == 1
    assert current["disclosure"] is None
    assert lane_calls == [
        {
            "operation": "rollback.restore_legacy_modify",
            "session_id": "review.rollback:legacy-success",
        }
    ]

    await client.close()


@pytest.mark.asyncio
async def test_rollback_legacy_modify_failure_does_not_leave_partial_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-legacy-modify-failure.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="original content",
        priority=1,
        title="node",
        domain="core",
    )
    updated = await client.update_memory(
        path="node",
        domain="core",
        content="current content",
        priority=5,
        disclosure="current disclosure",
    )

    async def _boom_reindex(self, session, memory_id: int):
        _ = self, session, memory_id
        raise RuntimeError("reindex failed")

    async def _run_write_lane_stub(_operation: str, task, *, session_id=None):
        _ = session_id
        return await task()

    monkeypatch.setattr(SQLiteClient, "_reindex_memory", _boom_reindex)
    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(review_api, "_run_write_lane", _run_write_lane_stub)

    with pytest.raises(RuntimeError, match="reindex failed"):
        await review_api._rollback_legacy_modify(
            {
                "memory_id": created["id"],
                "path": "node",
                "domain": "core",
                "uri": "core://node",
                "priority": 1,
                "disclosure": None,
            },
            lane_session_id="review.rollback:legacy-failure",
        )

    current = await client.get_memory_by_path("node", "core", reinforce_access=False)

    assert current is not None
    assert current["id"] == updated["new_memory_id"]
    assert current["content"] == "current content"
    assert current["priority"] == 5
    assert current["disclosure"] == "current disclosure"

    await client.close()


@pytest.mark.asyncio
async def test_rollback_path_modify_meta_can_clear_disclosure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-modify-meta-clear-disclosure.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    await client.create_memory(
        parent_path="",
        content="content",
        priority=5,
        title="node",
        domain="core",
    )
    await client.update_memory(
        path="node",
        domain="core",
        priority=8,
        disclosure="current disclosure",
    )

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: client)

    payload = await review_api._rollback_path(
        {
            "operation_type": "modify_meta",
            "domain": "core",
            "path": "node",
            "uri": "core://node",
            "priority": 5,
            "disclosure": None,
        }
    )

    current = await client.get_memory_by_path("node", "core", reinforce_access=False)

    assert payload == {"metadata_restored": True}
    assert current is not None
    assert current["priority"] == 5
    assert current["disclosure"] is None

    await client.close()


@pytest.mark.asyncio
async def test_rollback_path_create_returns_409_when_snapshot_memory_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-create-mismatch.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    original = await client.create_memory(
        parent_path="",
        content="original content",
        priority=1,
        title="parent",
        domain="core",
    )
    await client.remove_path("parent", "core")
    replacement = await client.create_memory(
        parent_path="",
        content="replacement content",
        priority=1,
        title="parent",
        domain="core",
    )

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: client)

    with pytest.raises(HTTPException) as exc_info:
        await review_api._rollback_path(
            {
                "operation_type": "create",
                "domain": "core",
                "path": "parent",
                "uri": "core://parent",
                "memory_id": original["id"],
            }
        )

    assert exc_info.value.status_code == 409
    assert "does not match current memory_id" in str(exc_info.value.detail)
    current = await client.get_memory_by_path("parent", "core", reinforce_access=False)
    assert current is not None
    assert current["id"] == replacement["id"]
    await client.close()


@pytest.mark.asyncio
async def test_rollback_memory_content_returns_409_for_cross_chain_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CrossChainClient:
        async def get_memory_version(self, memory_id: int):
            versions = {
                10: {"id": 10, "migrated_to": 11},
                11: {"id": 11, "migrated_to": None},
                99: {"id": 99, "migrated_to": None},
            }
            return versions.get(int(memory_id))

        async def get_memory_by_path(
            self,
            path: str,
            domain: str,
            reinforce_access: bool = False,
        ):
            _ = path, domain, reinforce_access
            return {"id": 99, "content": "current"}

        async def rollback_to_memory(self, path: str, memory_id: int, domain: str):
            _ = path, memory_id, domain
            raise AssertionError("rollback_to_memory should not run for cross-chain rollback")

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: _CrossChainClient())

    with pytest.raises(HTTPException) as exc_info:
        await review_api._rollback_memory_content(
            {
                "memory_id": 10,
                "path": "agent/node",
                "domain": "core",
                "uri": "core://agent/node",
                "all_paths": [],
            }
        )

    assert exc_info.value.status_code == 409
    assert "not in the same version chain" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_rollback_legacy_modify_returns_409_for_cross_chain_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CrossChainLegacyClient:
        async def get_memory_version(self, memory_id: int):
            versions = {
                20: {"id": 20, "migrated_to": 21},
                21: {"id": 21, "migrated_to": None},
                88: {"id": 88, "migrated_to": None},
            }
            return versions.get(int(memory_id))

        async def get_memory_by_path(
            self,
            path: str,
            domain: str,
            reinforce_access: bool = False,
        ):
            _ = path, domain, reinforce_access
            return {"id": 88, "priority": 2, "disclosure": "internal"}

        async def rollback_to_memory(self, path: str, memory_id: int, domain: str):
            _ = path, memory_id, domain
            raise AssertionError("rollback_to_memory should not run for cross-chain rollback")

        async def update_memory(
            self,
            path: str,
            domain: str,
            priority: int | None = None,
            disclosure: str | None = None,
        ):
            _ = path, domain, priority, disclosure
            raise AssertionError("update_memory should not run for cross-chain rollback")

    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: _CrossChainLegacyClient())

    with pytest.raises(HTTPException) as exc_info:
        await review_api._rollback_legacy_modify(
            {
                "memory_id": 20,
                "path": "agent/node",
                "domain": "core",
                "uri": "core://agent/node",
                "priority": 1,
                "disclosure": "public",
            }
        )

    assert exc_info.value.status_code == 409
    assert "not in the same version chain" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_sqlite_client_rollback_to_memory_restores_path_metadata(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-restore-meta.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    original = await client.create_memory(
        parent_path="",
        content="version 1",
        priority=1,
        title="agent",
        domain="core",
        disclosure="old disclosure",
    )
    await client.update_memory(
        path="agent",
        content="version 2",
        priority=5,
        disclosure="new disclosure",
        domain="core",
    )

    payload = await client.rollback_to_memory(
        "agent",
        original["id"],
        "core",
        restore_path_metadata=True,
        restore_priority=1,
        restore_disclosure="old disclosure",
    )

    current = await client.get_memory_by_path("agent", "core", reinforce_access=False)
    assert payload["restored_memory_id"] == original["id"]
    assert current is not None
    assert current["id"] == original["id"]
    assert current["priority"] == 1
    assert current["disclosure"] == "old disclosure"

    await client.close()


@pytest.mark.asyncio
async def test_sqlite_client_rollback_to_memory_rolls_back_on_reindex_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-rollback-atomicity.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    original = await client.create_memory(
        parent_path="",
        content="version 1",
        priority=1,
        title="agent",
        domain="core",
        disclosure="old disclosure",
    )
    update_payload = await client.update_memory(
        path="agent",
        content="version 2",
        priority=5,
        disclosure="new disclosure",
        domain="core",
    )
    current_id = update_payload["new_memory_id"]

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("reindex boom")

    monkeypatch.setattr(client, "_reindex_memory", _boom)

    with pytest.raises(RuntimeError, match="reindex boom"):
        await client.rollback_to_memory(
            "agent",
            original["id"],
            "core",
            restore_path_metadata=True,
            restore_priority=1,
            restore_disclosure="old disclosure",
        )

    current = await client.get_memory_by_path("agent", "core", reinforce_access=False)
    assert current is not None
    assert current["id"] == current_id
    assert current["priority"] == 5
    assert current["disclosure"] == "new disclosure"

    await client.close()
