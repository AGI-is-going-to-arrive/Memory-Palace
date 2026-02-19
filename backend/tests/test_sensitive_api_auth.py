from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import browse as browse_api
from api import review as review_api


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(review_api.router)
    app.include_router(browse_api.router)
    return TestClient(app)


def test_review_requires_api_key_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "review-secret")
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)
    with _build_client() as client:
        response = client.get("/review/sessions")
    assert response.status_code == 401


def test_browse_write_requires_api_key_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "browse-secret")
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)
    with _build_client() as client:
        response = client.post(
            "/browse/node",
            json={
                "parent_path": "",
                "title": "test-node",
                "content": "test-content",
                "priority": 1,
                "domain": "core",
            },
        )
    assert response.status_code == 401


def test_review_rejects_invalid_session_id_with_api_key(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "review-secret")
    monkeypatch.delenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", raising=False)
    headers = {"X-MCP-API-Key": "review-secret"}
    with _build_client() as client:
        response = client.delete("/review/sessions/%2E%2E", headers=headers)
    assert response.status_code == 400
    assert "Invalid session_id" in str(response.json().get("detail"))
