from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_sse_auth_allows_when_explicit_insecure_local_override_is_enabled(monkeypatch) -> None:
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
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
