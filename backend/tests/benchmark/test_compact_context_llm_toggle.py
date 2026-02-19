import json
from typing import Any, Dict, List, Optional

import pytest

import mcp_server
from db.sqlite_client import SQLiteClient


COMPACT_GIST_ENV_KEYS = [
    "COMPACT_GIST_LLM_ENABLED",
    "COMPACT_GIST_LLM_API_BASE",
    "COMPACT_GIST_LLM_API_KEY",
    "COMPACT_GIST_LLM_MODEL",
    "WRITE_GUARD_LLM_ENABLED",
    "WRITE_GUARD_LLM_API_BASE",
    "WRITE_GUARD_LLM_API_KEY",
    "WRITE_GUARD_LLM_MODEL",
    "LLM_RESPONSES_URL",
    "LLM_API_KEY",
    "LLM_MODEL_NAME",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ROUTER_API_BASE",
    "ROUTER_API_KEY",
    "ROUTER_CHAT_MODEL",
]


def _clear_compact_gist_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in COMPACT_GIST_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _enable_compact_gist_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPACT_GIST_LLM_ENABLED", "true")
    monkeypatch.setenv("COMPACT_GIST_LLM_API_BASE", "http://fake.llm")
    monkeypatch.setenv("COMPACT_GIST_LLM_MODEL", "fake-model")


@pytest.mark.asyncio
async def test_generate_compact_gist_llm_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_compact_gist_env(monkeypatch)
    monkeypatch.setenv("COMPACT_GIST_LLM_ENABLED", "false")
    monkeypatch.setenv("WRITE_GUARD_LLM_ENABLED", "false")

    client = SQLiteClient("sqlite+aiosqlite:///:memory:")
    degrade_reasons: List[str] = []
    payload = await client.generate_compact_gist(
        summary="Session summary for compact gist.",
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert payload is None
    assert degrade_reasons == ["compact_gist_llm_disabled"]


@pytest.mark.asyncio
async def test_generate_compact_gist_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_compact_gist_env(monkeypatch)
    monkeypatch.setenv("COMPACT_GIST_LLM_ENABLED", "true")

    client = SQLiteClient("sqlite+aiosqlite:///:memory:")
    degrade_reasons: List[str] = []
    payload = await client.generate_compact_gist(
        summary="Session summary for compact gist.",
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert payload is None
    assert "compact_gist_llm_config_missing" in degrade_reasons


@pytest.mark.asyncio
async def test_generate_compact_gist_request_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_compact_gist_env(monkeypatch)
    _enable_compact_gist_llm(monkeypatch)

    client = SQLiteClient("sqlite+aiosqlite:///:memory:")

    async def _fake_post_json(
        base: str,
        endpoint: str,
        payload: Dict[str, Any],
        api_key: str = "",
    ) -> Optional[Dict[str, Any]]:
        _ = base
        _ = endpoint
        _ = payload
        _ = api_key
        return None

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    degrade_reasons: List[str] = []
    payload = await client.generate_compact_gist(
        summary="Session summary for compact gist.",
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert payload is None
    assert "compact_gist_llm_request_failed" in degrade_reasons


@pytest.mark.asyncio
async def test_generate_compact_gist_response_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_compact_gist_env(monkeypatch)
    _enable_compact_gist_llm(monkeypatch)

    client = SQLiteClient("sqlite+aiosqlite:///:memory:")

    async def _fake_post_json(
        base: str,
        endpoint: str,
        payload: Dict[str, Any],
        api_key: str = "",
    ) -> Dict[str, Any]:
        _ = base
        _ = endpoint
        _ = payload
        _ = api_key
        return {}

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    degrade_reasons: List[str] = []
    result = await client.generate_compact_gist(
        summary="Session summary for compact gist.",
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert result is None
    assert "compact_gist_llm_response_empty" in degrade_reasons


@pytest.mark.asyncio
async def test_generate_compact_gist_response_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_compact_gist_env(monkeypatch)
    _enable_compact_gist_llm(monkeypatch)

    client = SQLiteClient("sqlite+aiosqlite:///:memory:")

    async def _fake_post_json(
        base: str,
        endpoint: str,
        payload: Dict[str, Any],
        api_key: str = "",
    ) -> Dict[str, Any]:
        _ = base
        _ = endpoint
        _ = payload
        _ = api_key
        return {"choices": [{"message": {"content": "not-json"}}]}

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    degrade_reasons: List[str] = []
    result = await client.generate_compact_gist(
        summary="Session summary for compact gist.",
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert result is None
    assert "compact_gist_llm_response_invalid" in degrade_reasons


@pytest.mark.asyncio
async def test_generate_compact_gist_gist_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_compact_gist_env(monkeypatch)
    _enable_compact_gist_llm(monkeypatch)

    client = SQLiteClient("sqlite+aiosqlite:///:memory:")

    async def _fake_post_json(
        base: str,
        endpoint: str,
        payload: Dict[str, Any],
        api_key: str = "",
    ) -> Dict[str, Any]:
        _ = base
        _ = endpoint
        _ = payload
        _ = api_key
        return {"choices": [{"message": {"content": json.dumps({"quality": 0.88})}}]}

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    degrade_reasons: List[str] = []
    result = await client.generate_compact_gist(
        summary="Session summary for compact gist.",
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert result is None
    assert "compact_gist_llm_gist_missing" in degrade_reasons


@pytest.mark.asyncio
async def test_generate_compact_gist_success_returns_llm_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_compact_gist_env(monkeypatch)
    _enable_compact_gist_llm(monkeypatch)

    client = SQLiteClient("sqlite+aiosqlite:///:memory:")

    async def _fake_post_json(
        base: str,
        endpoint: str,
        payload: Dict[str, Any],
        api_key: str = "",
    ) -> Dict[str, Any]:
        _ = api_key
        assert base == "http://fake.llm"
        assert endpoint == "/chat/completions"
        assert payload["model"] == "fake-model"
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"gist_text": "Incident summary with owner and ETA.", "quality": 0.91}
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    degrade_reasons: List[str] = []
    result = await client.generate_compact_gist(
        summary="Session summary for compact gist.",
        degrade_reasons=degrade_reasons,
    )
    await client.close()

    assert result is not None
    assert result["gist_method"] == "llm_gist"
    assert result["gist_text"] == "Incident summary with owner and ETA."
    assert result["quality"] == pytest.approx(0.91)
    assert degrade_reasons == []


class _UnavailableLLMClient:
    async def generate_compact_gist(
        self,
        *,
        summary: str,
        max_points: int = 3,
        max_chars: int = 280,
        degrade_reasons: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        _ = summary
        _ = max_points
        _ = max_chars
        if isinstance(degrade_reasons, list):
            degrade_reasons.append("compact_gist_llm_disabled")
        return None


@pytest.mark.asyncio
async def test_generate_gist_fallback_to_extractive_bullets_when_llm_unavailable() -> None:
    payload = await mcp_server.generate_gist(
        "Session compaction notes:\n"
        "- rebuilt index after timeout\n"
        "- retried with fallback mode\n"
        "- marked incident resolved",
        client=_UnavailableLLMClient(),
    )

    assert payload["gist_method"] == "extractive_bullets"
    assert payload["gist_text"] == (
        "rebuilt index after timeout; retried with fallback mode; marked incident resolved"
    )
    assert payload["quality"] > 0.0
    assert "compact_gist_llm_disabled" in payload.get("degrade_reasons", [])
