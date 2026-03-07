from pathlib import Path
from typing import Dict, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import maintenance as maintenance_api


class _ImportClientStub:
    def __init__(self) -> None:
        self._next_id = 1
        self.memories: Dict[int, Dict[str, str]] = {}
        self.paths: Dict[Tuple[str, str], int] = {}
        self.meta: Dict[str, str] = {}
        self.guard_action = "ADD"

    def counts(self) -> Tuple[int, int]:
        return len(self.memories), len(self.paths)

    async def set_runtime_meta(self, key: str, value: str) -> None:
        self.meta[key] = value

    async def get_runtime_meta(self, key: str):
        return self.meta.get(key)

    async def get_memory_by_path(self, path: str, domain: str, reinforce_access: bool = False):
        _ = reinforce_access
        memory_id = self.paths.get((domain, path))
        if memory_id is None:
            return None
        memory = self.memories.get(memory_id)
        if not isinstance(memory, dict):
            return None
        return {
            "id": memory_id,
            "domain": domain,
            "path": path,
            "content": memory.get("content") or "",
        }

    async def write_guard(self, **kwargs):
        _ = kwargs
        return {
            "action": self.guard_action,
            "method": "stub",
            "reason": "stubbed",
        }

    async def create_memory(
        self,
        *,
        parent_path: str,
        content: str,
        priority: int,
        title: str,
        domain: str,
        disclosure: str | None = None,
    ):
        _ = priority, disclosure
        normalized_parent = str(parent_path or "").strip().strip("/")
        normalized_title = str(title or "").strip()
        path = (
            f"{normalized_parent}/{normalized_title}"
            if normalized_parent
            else normalized_title
        )
        if not path:
            raise ValueError("path is required")
        key = (domain, path)
        if key in self.paths:
            raise ValueError("path already exists")
        memory_id = self._next_id
        self._next_id += 1
        self.memories[memory_id] = {"content": content, "domain": domain, "path": path}
        self.paths[key] = memory_id
        return {
            "id": memory_id,
            "domain": domain,
            "path": path,
            "uri": f"{domain}://{path}",
        }

    async def permanently_delete_memory(
        self,
        memory_id: int,
        *,
        require_orphan: bool = False,
    ):
        if memory_id not in self.memories:
            raise ValueError("memory not found")
        if require_orphan and any(value == memory_id for value in self.paths.values()):
            raise PermissionError("memory still has active paths")
        del self.memories[memory_id]
        for key, value in list(self.paths.items()):
            if value == memory_id:
                self.paths.pop(key, None)
        return {"deleted_memory_id": memory_id}

    async def remove_path(self, path: str, domain: str = "core"):
        key = (domain, path)
        memory_id = self.paths.get(key)
        if memory_id is None:
            raise ValueError("path not found")
        prefix = f"{path}/"
        for item_domain, item_path in self.paths.keys():
            if item_domain != domain:
                continue
            if item_path.startswith(prefix):
                raise ValueError("path still has child path(s)")
        self.paths.pop(key, None)
        return {"removed_uri": f"{domain}://{path}", "memory_id": memory_id}


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(maintenance_api.router)
    return TestClient(app)


def _prepare_payload(file_path: Path) -> dict:
    return {
        "file_paths": [str(file_path)],
        "actor_id": "actor-a",
        "session_id": "session-1",
        "source": "manual_import",
        "reason": "execute and rollback",
        "domain": "notes",
        "parent_path": "",
        "priority": 2,
    }


def _learn_payload(**overrides) -> dict:
    payload = {
        "content": "explicit correction from reviewer",
        "source": "manual_review",
        "reason": "fix factual drift",
        "session_id": "learn-session-1",
        "actor_id": "actor-a",
        "domain": "notes",
        "path_prefix": "corrections",
        "execute": True,
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _reset_import_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maintenance_api, "_IMPORT_JOBS", {})
    monkeypatch.setattr(maintenance_api, "_LEARN_JOBS", {})
    monkeypatch.setattr(maintenance_api, "_EXTERNAL_IMPORT_GUARD", None)
    monkeypatch.setattr(maintenance_api, "_EXTERNAL_IMPORT_GUARD_FINGERPRINT", None)
    monkeypatch.setattr(maintenance_api, "_EXPLICIT_LEARN_SERVICE", None)


def test_external_import_execute_and_rollback_restores_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    write_lane_calls: list[Dict[str, str]] = []

    async def _run_write_lane_stub(*, session_id, operation, task):
        write_lane_calls.append(
            {
                "session_id": str(session_id or ""),
                "operation": str(operation or ""),
            }
        )
        return await task()

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setattr(maintenance_api, "ENABLE_WRITE_LANE_QUEUE", True)
    monkeypatch.setattr(
        maintenance_api.runtime_state.write_lanes,
        "run_write",
        _run_write_lane_stub,
    )
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    headers = {"X-MCP-API-Key": "import-secret"}

    file_path = tmp_path / "memo.md"
    file_path.write_text("Import content v1", encoding="utf-8")
    before_counts = client_stub.counts()

    with _build_client() as client:
        prepare = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        assert prepare.status_code == 200
        job_id = prepare.json().get("job_id")
        assert isinstance(job_id, str) and job_id

        execute = client.post(
            "/maintenance/import/execute",
            headers=headers,
            json={"job_id": job_id},
        )
        assert execute.status_code == 200
        assert execute.json().get("status") == "executed"
        after_execute_counts = client_stub.counts()
        assert after_execute_counts[0] == before_counts[0] + 1

        status = client.get(f"/maintenance/import/jobs/{job_id}", headers=headers)
        assert status.status_code == 200
        assert status.json().get("status") == "executed"

        rollback = client.post(
            f"/maintenance/import/jobs/{job_id}/rollback",
            headers=headers,
            json={"reason": "manual_rollback"},
        )
        assert rollback.status_code == 200
        assert rollback.json().get("status") == "rolled_back"
        rollback_summary = rollback.json().get("rollback") or {}
        assert rollback_summary.get("attempted_memory_ids") == [1]
        assert rollback_summary.get("side_effects_audit_required") is True
        assert rollback_summary.get("residual_artifacts_review_required") is True

    after_rollback_counts = client_stub.counts()
    assert after_rollback_counts == before_counts
    assert [item["operation"] for item in write_lane_calls] == [
        "maintenance.import.execute.create_memory",
        "maintenance.import.rollback.remove_path",
        "maintenance.import.rollback.delete_memory",
    ]
    assert all(item["session_id"] == "session-1" for item in write_lane_calls)


def test_external_import_rollback_keeps_reused_memory_aliases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    headers = {"X-MCP-API-Key": "import-secret"}

    file_path = tmp_path / "memo.md"
    file_path.write_text("Import content v1", encoding="utf-8")

    with _build_client() as client:
        prepare = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        assert prepare.status_code == 200
        job_id = str(prepare.json().get("job_id") or "")
        assert job_id

        execute = client.post(
            "/maintenance/import/execute",
            headers=headers,
            json={"job_id": job_id},
        )
        assert execute.status_code == 200

        # Simulate another active alias binding to the same memory_id.
        client_stub.paths[("notes", "reused-alias")] = 1

        rollback = client.post(
            f"/maintenance/import/jobs/{job_id}/rollback",
            headers=headers,
            json={"reason": "rollback_keep_reused_alias"},
        )
        assert rollback.status_code == 200
        payload = rollback.json()
        assert payload.get("status") == "rolled_back"
        summary = payload.get("rollback") or {}
        assert summary.get("rolled_back_count") == 0
        assert summary.get("error_count") == 0
        assert summary.get("skipped_count") == 1

    assert 1 in client_stub.memories
    assert ("notes", "memo") not in client_stub.paths
    assert client_stub.paths.get(("notes", "reused-alias")) == 1


def test_external_import_job_status_recovers_from_runtime_meta_after_memory_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    headers = {"X-MCP-API-Key": "import-secret"}

    file_path = tmp_path / "memo.md"
    file_path.write_text("Import content v1", encoding="utf-8")

    with _build_client() as client:
        prepare = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        assert prepare.status_code == 200
        job_id = str(prepare.json().get("job_id") or "")
        assert job_id

        monkeypatch.setattr(maintenance_api, "_IMPORT_JOBS", {})
        monkeypatch.setattr(maintenance_api, "_LEARN_JOBS", {})
        status = client.get(f"/maintenance/import/jobs/{job_id}", headers=headers)
        assert status.status_code == 200
        assert status.json().get("status") == "prepared"


def test_external_import_execute_and_rollback_recover_from_runtime_meta_after_memory_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    headers = {"X-MCP-API-Key": "import-secret"}

    file_path = tmp_path / "memo.md"
    file_path.write_text("Import content v1", encoding="utf-8")

    with _build_client() as client:
        prepare = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        assert prepare.status_code == 200
        job_id = str(prepare.json().get("job_id") or "")
        assert job_id

        monkeypatch.setattr(maintenance_api, "_IMPORT_JOBS", {})
        monkeypatch.setattr(maintenance_api, "_LEARN_JOBS", {})
        execute = client.post(
            "/maintenance/import/execute",
            headers=headers,
            json={"job_id": job_id},
        )
        assert execute.status_code == 200
        assert execute.json().get("status") == "executed"

        monkeypatch.setattr(maintenance_api, "_IMPORT_JOBS", {})
        monkeypatch.setattr(maintenance_api, "_LEARN_JOBS", {})
        rollback = client.post(
            f"/maintenance/import/jobs/{job_id}/rollback",
            headers=headers,
            json={"reason": "restart_rollback"},
        )
        assert rollback.status_code == 200
        assert rollback.json().get("status") == "rolled_back"


def test_external_import_execute_uses_prepared_snapshot_even_when_source_file_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    headers = {"X-MCP-API-Key": "import-secret"}

    file_path = tmp_path / "memo.md"
    file_path.write_text("Import content v1", encoding="utf-8")

    with _build_client() as client:
        prepare = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        assert prepare.status_code == 200
        job_id = str(prepare.json().get("job_id") or "")
        assert job_id

        file_path.write_text("Import content changed", encoding="utf-8")
        execute = client.post(
            "/maintenance/import/execute",
            headers=headers,
            json={"job_id": job_id},
        )
        assert execute.status_code == 200
        assert execute.json().get("status") == "executed"
        assert client_stub.memories[1]["content"] == "Import content v1"

        status = client.get(f"/maintenance/import/jobs/{job_id}", headers=headers)
        assert status.status_code == 200
        assert status.json().get("status") == "executed"


def test_external_import_execute_rejects_when_prepared_snapshot_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    headers = {"X-MCP-API-Key": "import-secret"}

    file_path = tmp_path / "memo.md"
    file_path.write_text("Import content v1", encoding="utf-8")

    with _build_client() as client:
        prepare = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        assert prepare.status_code == 200
        job_id = str(prepare.json().get("job_id") or "")
        assert job_id

        maintenance_api._IMPORT_JOBS[job_id]["files"][0].pop("content", None)
        execute = client.post(
            "/maintenance/import/execute",
            headers=headers,
            json={"job_id": job_id},
        )
        assert execute.status_code == 409
        detail = execute.json().get("detail") or {}
        assert detail.get("reason") == "prepared_snapshot_invalid"
        source_mismatch = detail.get("source_mismatch") or []
        assert source_mismatch
        assert source_mismatch[0].get("reason") == "prepared_snapshot_incomplete"

        status = client.get(f"/maintenance/import/jobs/{job_id}", headers=headers)
        assert status.status_code == 200
        assert status.json().get("status") == "failed"


def test_external_import_execute_fail_closed_when_write_guard_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    client_stub.guard_action = "NOOP"
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    headers = {"X-MCP-API-Key": "import-secret"}

    file_path = tmp_path / "memo.md"
    file_path.write_text("Import content v1", encoding="utf-8")

    with _build_client() as client:
        prepare = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        assert prepare.status_code == 200
        job_id = str(prepare.json().get("job_id") or "")
        assert job_id

        execute = client.post(
            "/maintenance/import/execute",
            headers=headers,
            json={"job_id": job_id},
        )
        assert execute.status_code == 409
        detail = execute.json().get("detail") or {}
        assert detail.get("reason") == "write_guard_blocked"
        assert client_stub.counts() == (0, 0)


def test_explicit_learn_trigger_rejects_when_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "false")
    headers = {"X-MCP-API-Key": "import-secret"}

    with _build_client() as client:
        response = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(),
        )

    assert response.status_code == 409
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "explicit_learn_trigger_rejected"
    assert detail.get("reason") == "auto_learn_explicit_disabled"
    job = detail.get("job") or {}
    assert job.get("job_type") == "learn"
    assert job.get("status") == "failed"


def test_explicit_learn_trigger_disabled_handles_unavailable_db_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _db_not_ready():
        raise RuntimeError("db not ready")

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", _db_not_ready)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "false")
    headers = {"X-MCP-API-Key": "import-secret"}

    with _build_client() as client:
        response = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(),
        )

    assert response.status_code == 409
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "explicit_learn_trigger_rejected"
    assert detail.get("reason") == "auto_learn_explicit_disabled"


def test_explicit_learn_trigger_execute_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    headers = {"X-MCP-API-Key": "import-secret"}

    before_counts = client_stub.counts()
    with _build_client() as client:
        trigger = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(execute=True),
        )
        assert trigger.status_code == 200
        payload = trigger.json()
        assert payload.get("ok") is True
        assert payload.get("status") == "executed"
        job_id = str(payload.get("job_id") or "")
        assert job_id.startswith("learn-")
        assert payload.get("job_type") == "learn"
        assert payload.get("rollback_endpoint") == f"/maintenance/import/jobs/{job_id}/rollback"
        assert payload.get("rollback_endpoint_aliases") == [
            f"/maintenance/import/jobs/{job_id}/rollback",
            f"/maintenance/learn/jobs/{job_id}/rollback",
        ]
        result = payload.get("result") or {}
        assert result.get("reason") == "executed"

        status = client.get(f"/maintenance/import/jobs/{job_id}", headers=headers)
        assert status.status_code == 200
        status_payload = status.json()
        assert status_payload.get("status") == "executed"
        assert status_payload.get("job_type") == "learn"
        job = status_payload.get("job") or {}
        assert job.get("job_type") == "learn"

        learn_status = client.get(f"/maintenance/learn/jobs/{job_id}", headers=headers)
        assert learn_status.status_code == 200
        assert learn_status.json().get("job_type") == "learn"

        rollback = client.post(
            f"/maintenance/learn/jobs/{job_id}/rollback",
            headers=headers,
            json={"reason": "learn_rollback"},
        )
        assert rollback.status_code == 200
        rollback_payload = rollback.json()
        assert rollback_payload.get("status") == "rolled_back"
        assert rollback_payload.get("job_type") == "learn"
        summary = rollback_payload.get("rollback") or {}
        assert summary.get("rolled_back_count") == 1
        assert summary.get("side_effects_audit_required") is True
        assert summary.get("residual_artifacts_review_required") is True
        namespace_cleanup = summary.get("namespace_cleanup") or {}
        assert len(namespace_cleanup.get("removed_paths") or []) >= 1

    after_counts = client_stub.counts()
    assert after_counts == before_counts


def test_explicit_learn_job_uses_dedicated_learn_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    headers = {"X-MCP-API-Key": "import-secret"}

    with _build_client() as client:
        trigger = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(execute=False, session_id="learn-pool-check"),
        )
        assert trigger.status_code == 200
        payload = trigger.json()
        assert payload.get("status") == "prepared"
        job_id = str(payload.get("job_id") or "")
        assert job_id.startswith("learn-")

        assert job_id in maintenance_api._LEARN_JOBS
        assert job_id not in maintenance_api._IMPORT_JOBS

        # Backward-compatible alias is still served from import endpoint.
        status_via_import = client.get(
            f"/maintenance/import/jobs/{job_id}",
            headers=headers,
        )
        assert status_via_import.status_code == 200
        assert status_via_import.json().get("job_type") == "learn"


def test_explicit_learn_job_status_recovers_from_learn_runtime_meta_after_memory_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    headers = {"X-MCP-API-Key": "import-secret"}

    with _build_client() as client:
        trigger = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(execute=False, session_id="learn-recover"),
        )
        assert trigger.status_code == 200
        job_id = str(trigger.json().get("job_id") or "")
        assert job_id

        monkeypatch.setattr(maintenance_api, "_LEARN_JOBS", {})
        status = client.get(f"/maintenance/learn/jobs/{job_id}", headers=headers)
        assert status.status_code == 200
        assert status.json().get("status") == "prepared"

        # Compatibility path keeps working after learn cache reset.
        status_via_import = client.get(
            f"/maintenance/import/jobs/{job_id}",
            headers=headers,
        )
        assert status_via_import.status_code == 200
        assert status_via_import.json().get("job_type") == "learn"


def test_explicit_learn_rollback_keeps_shared_prefix_when_other_session_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    headers = {"X-MCP-API-Key": "import-secret"}

    with _build_client() as client:
        first = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(session_id="learn-session-a", execute=True),
        )
        assert first.status_code == 200
        first_job_id = str(first.json().get("job_id") or "")
        assert first_job_id

        second = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(session_id="learn-session-b", execute=True),
        )
        assert second.status_code == 200
        second_job_id = str(second.json().get("job_id") or "")
        assert second_job_id

        rollback_first = client.post(
            f"/maintenance/learn/jobs/{first_job_id}/rollback",
            headers=headers,
            json={"reason": "cleanup-first-session"},
        )
        assert rollback_first.status_code == 200
        summary = (rollback_first.json().get("rollback") or {}).get("namespace_cleanup") or {}
        assert summary.get("skipped_count", 0) >= 1

        assert ("notes", "corrections") in client_stub.paths
        assert ("notes", "corrections/learn-session-a") not in client_stub.paths
        assert ("notes", "corrections/learn-session-b") in client_stub.paths

        second_status = client.get(
            f"/maintenance/learn/jobs/{second_job_id}",
            headers=headers,
        )
        assert second_status.status_code == 200
        assert second_status.json().get("status") == "executed"


def test_import_job_not_evicted_by_learn_job_burst(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setattr(maintenance_api, "_IMPORT_JOB_MAX_PENDING", 4)
    headers = {"X-MCP-API-Key": "import-secret"}

    file_path = tmp_path / "memo.md"
    file_path.write_text("Import content v1", encoding="utf-8")

    async def _fake_service(**kwargs):
        session_id = str(kwargs.get("session_id") or "s")
        return {
            "ok": True,
            "accepted": True,
            "reason": "prepared",
            "batch_id": f"learn-{session_id}",
            "source_hash": f"hash-{session_id}",
            "target_parent_uri": f"notes://corrections/{session_id}",
        }

    monkeypatch.setattr(maintenance_api, "_EXPLICIT_LEARN_SERVICE", _fake_service)

    with _build_client() as client:
        prepare = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        assert prepare.status_code == 200
        import_job_id = str(prepare.json().get("job_id") or "")
        assert import_job_id

        for index in range(8):
            response = client.post(
                "/maintenance/learn/trigger",
                headers=headers,
                json=_learn_payload(session_id=f"burst-{index}", execute=False),
            )
            assert response.status_code == 200

        import_status = client.get(
            f"/maintenance/import/jobs/{import_job_id}",
            headers=headers,
        )
        assert import_status.status_code == 200
        assert import_status.json().get("job_type") == "import"


def test_import_prepare_prefers_evicting_unprotected_jobs_when_pool_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setattr(maintenance_api, "_IMPORT_JOB_MAX_PENDING", 2)
    headers = {"X-MCP-API-Key": "import-secret"}

    file_a = tmp_path / "import-a.md"
    file_b = tmp_path / "import-b.md"
    file_c = tmp_path / "import-c.md"
    file_a.write_text("import A", encoding="utf-8")
    file_b.write_text("import B", encoding="utf-8")
    file_c.write_text("import C", encoding="utf-8")

    with _build_client() as client:
        prepare_a = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_a),
        )
        assert prepare_a.status_code == 200
        import_job_a = str(prepare_a.json().get("job_id") or "")
        assert import_job_a
        maintenance_api._IMPORT_JOBS[import_job_a]["status"] = "executed"
        maintenance_api._IMPORT_JOBS[import_job_a]["created_memories"] = [
            {"memory_id": 101, "uri": "notes://imports/a", "path": "imports/a"}
        ]

        prepare_b = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_b),
        )
        assert prepare_b.status_code == 200
        import_job_b = str(prepare_b.json().get("job_id") or "")
        assert import_job_b

        prepare_c = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_c),
        )
        assert prepare_c.status_code == 200
        import_job_c = str(prepare_c.json().get("job_id") or "")
        assert import_job_c

        protected_status = client.get(
            f"/maintenance/import/jobs/{import_job_a}",
            headers=headers,
        )
        assert protected_status.status_code == 200

        evicted_status = client.get(
            f"/maintenance/import/jobs/{import_job_b}",
            headers=headers,
        )
        assert evicted_status.status_code == 404

        latest_status = client.get(
            f"/maintenance/import/jobs/{import_job_c}",
            headers=headers,
        )
        assert latest_status.status_code == 200


def test_explicit_learn_failed_job_can_rollback_namespace_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_stub = _ImportClientStub()
    write_lane_calls: list[Dict[str, str]] = []

    async def _run_write_lane_stub(*, session_id, operation, task):
        write_lane_calls.append(
            {
                "session_id": str(session_id or ""),
                "operation": str(operation or ""),
            }
        )
        return await task()

    # Seed namespace nodes that simulate a failed execute after namespace creation.
    client_stub.memories[1] = {"content": "ns-root", "domain": "notes", "path": "corrections"}
    client_stub.paths[("notes", "corrections")] = 1
    client_stub.memories[2] = {
        "content": "ns-session",
        "domain": "notes",
        "path": "corrections/failed-session",
    }
    client_stub.paths[("notes", "corrections/failed-session")] = 2
    client_stub._next_id = 3

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setattr(maintenance_api, "ENABLE_WRITE_LANE_QUEUE", True)
    monkeypatch.setattr(
        maintenance_api.runtime_state.write_lanes,
        "run_write",
        _run_write_lane_stub,
    )
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    headers = {"X-MCP-API-Key": "import-secret"}

    async def _failed_service(**kwargs):
        _ = kwargs
        return {
            "ok": True,
            "accepted": False,
            "reason": "create_memory_failed",
            "batch_id": "learn-failed-ns-1",
            "source_hash": "deadbeef",
            "target_parent_uri": "notes://corrections/failed-session",
            "created_namespace_memories": [
                {
                    "memory_id": 1,
                    "domain": "notes",
                    "path": "corrections",
                    "uri": "notes://corrections",
                },
                {
                    "memory_id": 2,
                    "domain": "notes",
                    "path": "corrections/failed-session",
                    "uri": "notes://corrections/failed-session",
                },
            ],
        }

    monkeypatch.setattr(maintenance_api, "_EXPLICIT_LEARN_SERVICE", _failed_service)

    with _build_client() as client:
        trigger = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(session_id="failed-session", execute=True),
        )
        assert trigger.status_code == 409
        detail = trigger.json().get("detail") or {}
        assert detail.get("reason") == "create_memory_failed"
        assert detail.get("job_id") == "learn-failed-ns-1"

        rollback = client.post(
            "/maintenance/learn/jobs/learn-failed-ns-1/rollback",
            headers=headers,
            json={"reason": "cleanup_failed_namespace"},
        )
        assert rollback.status_code == 200
        payload = rollback.json()
        assert payload.get("status") == "rolled_back"
        summary = payload.get("rollback") or {}
        assert summary.get("rolled_back_count") == 0
        namespace_cleanup = summary.get("namespace_cleanup") or {}
        assert namespace_cleanup.get("removed_paths") == [
            "notes://corrections/failed-session",
            "notes://corrections",
        ]

    assert ("notes", "corrections") not in client_stub.paths
    assert ("notes", "corrections/failed-session") not in client_stub.paths
    assert 1 not in client_stub.memories
    assert 2 not in client_stub.memories
    assert [item["operation"] for item in write_lane_calls] == [
        "maintenance.learn.rollback.remove_path",
        "maintenance.learn.rollback.delete_memory",
        "maintenance.learn.rollback.remove_path",
        "maintenance.learn.rollback.delete_memory",
    ]
    assert all(item["session_id"] == "failed-session" for item in write_lane_calls)


def test_learn_trigger_does_not_evict_import_jobs_after_pool_split(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setattr(maintenance_api, "_IMPORT_JOB_MAX_PENDING", 2)
    headers = {"X-MCP-API-Key": "import-secret"}

    file_a = tmp_path / "import-a.md"
    file_b = tmp_path / "import-b.md"
    file_a.write_text("import A", encoding="utf-8")
    file_b.write_text("import B", encoding="utf-8")

    async def _fake_service(**kwargs):
        session_id = str(kwargs.get("session_id") or "s")
        return {
            "ok": True,
            "accepted": True,
            "reason": "prepared",
            "batch_id": f"learn-{session_id}",
            "source_hash": f"hash-{session_id}",
            "target_parent_uri": f"notes://corrections/{session_id}",
        }

    monkeypatch.setattr(maintenance_api, "_EXPLICIT_LEARN_SERVICE", _fake_service)

    with _build_client() as client:
        prepare_a = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_a),
        )
        assert prepare_a.status_code == 200
        import_job_a = str(prepare_a.json().get("job_id") or "")
        assert import_job_a
        # Mark the first import job as rollback-protected (executed with created memories).
        maintenance_api._IMPORT_JOBS[import_job_a]["status"] = "executed"
        maintenance_api._IMPORT_JOBS[import_job_a]["created_memories"] = [
            {"memory_id": 101, "uri": "notes://imports/a", "path": "imports/a"}
        ]

        prepare_b = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_b),
        )
        assert prepare_b.status_code == 200
        import_job_b = str(prepare_b.json().get("job_id") or "")
        assert import_job_b

        trigger = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(session_id="evict-1", execute=False),
        )
        assert trigger.status_code == 200

        protected_status = client.get(
            f"/maintenance/import/jobs/{import_job_a}",
            headers=headers,
        )
        assert protected_status.status_code == 200

        evicted_status = client.get(
            f"/maintenance/import/jobs/{import_job_b}",
            headers=headers,
        )
        assert evicted_status.status_code == 200
        assert evicted_status.json().get("job_type") == "import"


def test_explicit_learn_trigger_uses_injected_service_without_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    headers = {"X-MCP-API-Key": "import-secret"}
    captured: Dict[str, object] = {}

    async def _fake_service(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "accepted": True,
            "reason": "executed",
            "batch_id": "learn-injected-1",
            "source_hash": "abc123",
            "target_parent_uri": "notes://corrections/learn-session-1",
            "created_memory": {
                "id": 11,
                "uri": "notes://corrections/learn-session-1/learn-abc123",
                "path": "corrections/learn-session-1/learn-abc123",
            },
        }

    monkeypatch.setattr(maintenance_api, "_EXPLICIT_LEARN_SERVICE", _fake_service)

    def _unexpected_import(name: str):
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(maintenance_api.importlib, "import_module", _unexpected_import)

    with _build_client() as client:
        response = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(execute=True),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("status") == "executed"
    assert payload.get("job_id") == "learn-injected-1"
    assert captured.get("domain") == "notes"
    assert captured.get("execute") is True


def test_explicit_learn_trigger_returns_503_when_service_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_stub = _ImportClientStub()
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client_stub)
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    headers = {"X-MCP-API-Key": "import-secret"}

    def _raise_import_error(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(maintenance_api.importlib, "import_module", _raise_import_error)

    with _build_client() as client:
        response = client.post(
            "/maintenance/learn/trigger",
            headers=headers,
            json=_learn_payload(execute=True),
        )

    assert response.status_code == 503
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "explicit_learn_service_unavailable"
    assert detail.get("reason") == "import_failed"
