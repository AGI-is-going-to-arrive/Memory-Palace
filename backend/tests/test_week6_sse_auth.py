from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import run_sse
from run_sse import apply_mcp_api_key_middleware


def _build_client(*, client=("testclient", 50000)) -> TestClient:
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    apply_mcp_api_key_middleware(app)
    return TestClient(app, client=client)


def test_sse_auth_rejects_when_api_key_not_configured_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)
    with _build_client() as client:
        response = client.get("/ping")
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "api_key_not_configured"


@pytest.mark.parametrize("override_value", ["true", "enabled"])
def test_sse_auth_allows_when_explicit_insecure_local_override_is_enabled(
    monkeypatch, override_value: str
) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", override_value)
    with _build_client(client=("127.0.0.1", 50000)) as client:
        response = client.get("/ping")
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_sse_auth_rejects_insecure_local_override_for_non_loopback_client(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    with _build_client(client=("203.0.113.10", 50000)) as client:
        response = client.get("/ping")
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "insecure_local_override_requires_loopback"


def test_sse_auth_rejects_insecure_local_override_when_forwarded_headers_present(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    headers = {"X-Forwarded-For": "198.51.100.8"}
    with _build_client(client=("127.0.0.1", 50000)) as client:
        response = client.get("/ping", headers=headers)
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "insecure_local_override_requires_loopback"


def test_sse_auth_rejects_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-sse-secret")
    with _build_client() as client:
        response = client.get("/ping")
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("error") == "mcp_sse_auth_failed"
    assert payload.get("reason") == "invalid_or_missing_api_key"


def test_sse_auth_accepts_x_mcp_api_key_header(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-sse-secret")
    headers = {"X-MCP-API-Key": "week6-sse-secret"}
    with _build_client() as client:
        response = client.get("/ping", headers=headers)
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_sse_auth_accepts_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "week6-sse-secret")
    headers = {"Authorization": "Bearer week6-sse-secret"}
    with _build_client() as client:
        response = client.get("/ping", headers=headers)
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_sse_main_runs_mcp_startup_before_uvicorn(monkeypatch) -> None:
    call_order = []

    async def _fake_startup() -> None:
        call_order.append("startup")

    def _fake_create_sse_app():
        call_order.append("create_sse_app")
        return {"app": "fake"}

    def _fake_uvicorn_run(app, host, port):
        call_order.append(("uvicorn", host, port, app))

    monkeypatch.setattr(run_sse, "mcp_startup", _fake_startup)
    monkeypatch.setattr(run_sse, "create_sse_app", _fake_create_sse_app)
    monkeypatch.setattr(run_sse.uvicorn, "run", _fake_uvicorn_run)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8010")

    run_sse.main()

    assert call_order[0] == "startup"
    assert call_order[1] == "create_sse_app"
    assert call_order[2][0] == "uvicorn"
    assert call_order[2][1] == "127.0.0.1"
    assert call_order[2][2] == 8010
