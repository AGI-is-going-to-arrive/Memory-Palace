import importlib
import json

import pytest

import main as main_module


def _reload_main_module():
    return importlib.reload(main_module)


def _read_cors_kwargs(module):
    for middleware in module.app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            return dict(middleware.kwargs)
    raise AssertionError("CORS middleware not configured")


def test_cors_defaults_use_restricted_local_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("CORS_ALLOW_CREDENTIALS", raising=False)

    module = _reload_main_module()
    cors_kwargs = _read_cors_kwargs(module)

    assert cors_kwargs["allow_origins"] == list(module._DEFAULT_CORS_ALLOW_ORIGINS)
    assert cors_kwargs["allow_credentials"] is True


def test_cors_allows_credentials_with_explicit_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")

    module = _reload_main_module()
    cors_kwargs = _read_cors_kwargs(module)

    assert cors_kwargs["allow_origins"] == ["https://a.example", "https://b.example"]
    assert cors_kwargs["allow_credentials"] is True


def test_cors_disables_credentials_for_explicit_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")

    module = _reload_main_module()
    cors_kwargs = _read_cors_kwargs(module)

    assert cors_kwargs["allow_origins"] == ["*"]
    assert cors_kwargs["allow_credentials"] is False


@pytest.mark.asyncio
async def test_health_hides_internal_exception_details(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_main_module()

    def _raise_client_error():
        raise RuntimeError("secret_token_should_not_leak")

    monkeypatch.setattr(module, "get_sqlite_client", _raise_client_error)
    payload = await module.health()

    assert payload["status"] == "degraded"
    assert payload["index"]["reason"] == "internal_error"
    assert payload["runtime"]["write_lanes"]["reason"] == "internal_error"
    assert "secret_token_should_not_leak" not in json.dumps(payload)
