from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import maintenance as maintenance_api


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
        "reason": "test prepare flow",
        "domain": "notes",
        "parent_path": "",
        "priority": 2,
    }


@pytest.fixture(autouse=True)
def _reset_import_job_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maintenance_api, "_IMPORT_JOBS", {})
    monkeypatch.setattr(maintenance_api, "_LEARN_JOBS", {})
    monkeypatch.setattr(maintenance_api, "_EXTERNAL_IMPORT_GUARD", None)
    monkeypatch.setattr(maintenance_api, "_EXTERNAL_IMPORT_GUARD_FINGERPRINT", None)


def test_import_prepare_requires_api_key_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    file_path = tmp_path / "safe.md"
    file_path.write_text("safe", encoding="utf-8")

    with _build_client() as client:
        response = client.post(
            "/maintenance/import/prepare",
            json=_prepare_payload(file_path),
        )

    assert response.status_code == 401
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "maintenance_auth_failed"


def test_import_prepare_rejects_when_external_import_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "false")
    headers = {"X-MCP-API-Key": "import-secret"}
    file_path = tmp_path / "safe.md"
    file_path.write_text("safe", encoding="utf-8")

    with _build_client() as client:
        response = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )

    assert response.status_code == 409
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "external_import_prepare_rejected"
    assert detail.get("reason") == "external_import_disabled"


def test_import_prepare_rejects_path_outside_allowlist_with_403(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path / "allowed"))
    headers = {"X-MCP-API-Key": "import-secret"}
    (tmp_path / "allowed").mkdir(parents=True, exist_ok=True)
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("outside", encoding="utf-8")

    with _build_client() as client:
        response = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(outside_file),
        )

    assert response.status_code == 403
    detail = response.json().get("detail") or {}
    assert detail.get("reason") == "file_validation_failed"
    rejected = detail.get("rejected_files") or []
    assert rejected and rejected[0].get("reason") == "path_not_allowed"


def test_import_prepare_returns_429_when_rate_limited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("EXTERNAL_IMPORT_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("EXTERNAL_IMPORT_RATE_LIMIT_MAX_REQUESTS", "1")
    headers = {"X-MCP-API-Key": "import-secret"}
    file_path = tmp_path / "safe.md"
    file_path.write_text("safe", encoding="utf-8")

    with _build_client() as client:
        first = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )
        second = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )

    assert first.status_code == 200
    assert second.status_code == 429
    detail = second.json().get("detail") or {}
    assert detail.get("reason") == "rate_limited"
    assert int(detail.get("retry_after_seconds") or 0) > 0


def test_import_prepare_rejects_when_shared_rate_limit_required_without_state_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("EXTERNAL_IMPORT_REQUIRE_SHARED_RATE_LIMIT", "true")
    monkeypatch.delenv("EXTERNAL_IMPORT_RATE_LIMIT_STATE_FILE", raising=False)
    headers = {"X-MCP-API-Key": "import-secret"}
    file_path = tmp_path / "safe.md"
    file_path.write_text("safe", encoding="utf-8")

    with _build_client() as client:
        response = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )

    assert response.status_code == 409
    detail = response.json().get("detail") or {}
    assert detail.get("reason") == "rate_limit_shared_state_required"
    assert isinstance(detail.get("config_errors"), list)


def test_import_prepare_rejects_domain_outside_external_import_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_DOMAINS", "notes")
    headers = {"X-MCP-API-Key": "import-secret"}
    file_path = tmp_path / "safe.md"
    file_path.write_text("safe", encoding="utf-8")
    payload = _prepare_payload(file_path)
    payload["domain"] = "core"

    with _build_client() as client:
        response = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=payload,
        )

    assert response.status_code == 422
    detail = response.json().get("detail") or {}
    assert detail.get("reason") == "domain_not_allowed_for_external_import"
    assert detail.get("allowed_domains") == ["notes"]


def test_import_prepare_returns_prepared_job_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "import-secret")
    monkeypatch.setenv("EXTERNAL_IMPORT_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("EXTERNAL_IMPORT_ALLOWED_EXTS", ".md,.txt,.json")
    headers = {"X-MCP-API-Key": "import-secret"}
    file_path = tmp_path / "guide.md"
    file_path.write_text("Memory Palace import test", encoding="utf-8")

    with _build_client() as client:
        response = client.post(
            "/maintenance/import/prepare",
            headers=headers,
            json=_prepare_payload(file_path),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("ok") is True
    assert payload.get("status") == "prepared"
    assert payload.get("dry_run") is True
    job_id = str(payload.get("job_id") or "")
    assert job_id.startswith("import-")
    job = payload.get("job") or {}
    assert job.get("status") == "prepared"
    assert job.get("file_count") == 1
    guard = job.get("guard") or {}
    policy = guard.get("policy") or {}
    assert guard.get("rate_limit_storage") in {"process_memory", "state_file"}
    assert isinstance(policy.get("policy_hash"), str) and policy.get("policy_hash")
    assert policy.get("allowed_domains") == ["notes"]
    files = job.get("files") or []
    assert len(files) == 1
    assert files[0].get("target_uri", "").startswith("notes://")
    assert "content" not in files[0]
    assert job_id in maintenance_api._IMPORT_JOBS
    assert job_id not in maintenance_api._LEARN_JOBS
