from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api import maintenance as maintenance_api


def _build_client(monkeypatch, *, client=("testclient", 50000)) -> TestClient:
    async def _ensure_started(_factory) -> None:
        return None

    async def _run_decay(*, client_factory, force: bool, reason: str):
        return {"degraded": False, "applied": True, "reason": reason, "force": force}

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state.vitality_decay, "run_decay", _run_decay)

    app = FastAPI()
    app.include_router(maintenance_api.router)
    return TestClient(app, client=client)


def test_maintenance_auth_rejects_when_api_key_not_configured_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)
    with _build_client(monkeypatch) as client:
        response = client.post("/maintenance/vitality/decay")
    assert response.status_code == 401
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "maintenance_auth_failed"
    assert detail.get("reason") == "api_key_not_configured"


@pytest.mark.parametrize("override_value", ["true", "enabled"])
def test_maintenance_auth_allows_when_explicit_insecure_local_override_is_enabled(
    monkeypatch, override_value: str
) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", override_value)
    with _build_client(monkeypatch, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/maintenance/vitality/decay",
            headers={"Host": "127.0.0.1"},
        )
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_maintenance_auth_rejects_insecure_local_override_for_non_loopback_client(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    with _build_client(monkeypatch, client=("203.0.113.10", 50000)) as client:
        response = client.post("/maintenance/vitality/decay")
    assert response.status_code == 401
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "maintenance_auth_failed"
    assert detail.get("reason") == "insecure_local_override_requires_loopback"


def test_maintenance_auth_rejects_insecure_local_override_when_host_is_not_loopback(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    with _build_client(monkeypatch, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/maintenance/vitality/decay",
            headers={"Host": "memory-palace.example"},
        )
    assert response.status_code == 401
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "maintenance_auth_failed"
    assert detail.get("reason") == "insecure_local_override_requires_loopback"


@pytest.mark.parametrize(
    "forwarded_headers",
    [
        {"X-Forwarded-For": "203.0.113.10"},
        {"Forwarded": "for=203.0.113.10;proto=https"},
    ],
)
def test_maintenance_auth_rejects_insecure_local_override_when_forwarding_headers_present(
    monkeypatch, forwarded_headers: dict[str, str]
) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    with _build_client(monkeypatch, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/maintenance/vitality/decay",
            headers=forwarded_headers,
        )
    assert response.status_code == 401
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "maintenance_auth_failed"
    assert detail.get("reason") == "insecure_local_override_requires_loopback"


def test_maintenance_auth_rejects_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-secret")
    with _build_client(monkeypatch) as client:
        response = client.post("/maintenance/vitality/decay")
    assert response.status_code == 401
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "maintenance_auth_failed"


def test_maintenance_auth_accepts_x_mcp_api_key_header(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-secret")
    headers = {"X-MCP-API-Key": "week6-secret"}
    with _build_client(monkeypatch) as client:
        response = client.post("/maintenance/vitality/decay", headers=headers)
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_maintenance_auth_accepts_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-secret")
    headers = {"Authorization": "Bearer week6-secret"}
    with _build_client(monkeypatch) as client:
        response = client.post("/maintenance/vitality/decay", headers=headers)
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_maintenance_auth_rejects_index_job_endpoint_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-secret")
    with _build_client(monkeypatch) as client:
        response = client.get("/maintenance/index/job/idx-test")
    assert response.status_code == 401
    detail = response.json().get("detail") or {}
    assert detail.get("error") == "maintenance_auth_failed"


def test_maintenance_auth_accepts_index_job_endpoint_with_api_key(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-secret")
    headers = {"X-MCP-API-Key": "week6-secret"}
    with _build_client(monkeypatch) as client:
        response = client.get("/maintenance/index/job/idx-test", headers=headers)
    assert response.status_code in {200, 404, 409}
