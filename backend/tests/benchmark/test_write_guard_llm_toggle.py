import json
from typing import Any, Dict, Optional

import pytest

from db.sqlite_client import SQLiteClient


_SQLITE_MEMORY_URL = "sqlite+aiosqlite:///:memory:"
_NO_POST_JSON_PATCH = object()
_WRITE_GUARD_ENV_KEYS = [
    "WRITE_GUARD_LLM_ENABLED",
    "WRITE_GUARD_LLM_API_BASE",
    "WRITE_GUARD_LLM_API_KEY",
    "WRITE_GUARD_LLM_MODEL",
    "LLM_RESPONSES_URL",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "ROUTER_API_BASE",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "ROUTER_API_KEY",
    "LLM_MODEL_NAME",
    "OPENAI_MODEL",
    "ROUTER_CHAT_MODEL",
]


def _clear_write_guard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _WRITE_GUARD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _candidate_payload(memory_id: int, *, vector: float, text: float, final: float) -> Dict[str, Any]:
    return {
        "memory_id": memory_id,
        "uri": f"core://memory/{memory_id}",
        "snippet": f"candidate memory {memory_id}",
        "scores": {"vector": vector, "text": text, "final": final},
    }


def _stub_search_advanced() -> Any:
    semantic_payload = {
        "results": [_candidate_payload(12, vector=0.34, text=0.21, final=0.41)],
        "degrade_reasons": [],
    }
    keyword_payload = {
        "results": [_candidate_payload(18, vector=0.17, text=0.36, final=0.39)],
        "degrade_reasons": [],
    }

    async def _fake_search_advanced(
        *,
        query: str,
        mode: str,
        max_results: int,
        candidate_multiplier: int,
        filters: Dict[str, Any],
    ) -> Dict[str, Any]:
        assert query == "incoming content"
        assert max_results == 6
        assert candidate_multiplier == 6
        assert filters.get("domain") == "core"
        if mode == "semantic":
            return dict(semantic_payload)
        if mode == "keyword":
            return dict(keyword_payload)
        raise AssertionError(f"unexpected mode: {mode}")

    return _fake_search_advanced


def _stub_post_json(response: Optional[Dict[str, Any]]) -> Any:
    async def _fake_post_json(
        base: str,
        endpoint: str,
        payload: Dict[str, Any],
        api_key: str = "",
    ) -> Optional[Dict[str, Any]]:
        _ = api_key
        assert base == "http://fake.write-guard/v1"
        assert endpoint == "/chat/completions"
        assert payload["model"] == "fake-write-guard-model"
        return response

    return _fake_post_json


async def _run_write_guard(
    monkeypatch: pytest.MonkeyPatch,
    *,
    llm_enabled: bool,
    llm_configured: bool = False,
    llm_response: Any = _NO_POST_JSON_PATCH,
) -> Dict[str, Any]:
    _clear_write_guard_env(monkeypatch)
    monkeypatch.setenv("WRITE_GUARD_LLM_ENABLED", "true" if llm_enabled else "false")
    if llm_configured:
        monkeypatch.setenv("WRITE_GUARD_LLM_API_BASE", "http://fake.write-guard/v1")
        monkeypatch.setenv("WRITE_GUARD_LLM_MODEL", "fake-write-guard-model")

    client = SQLiteClient(_SQLITE_MEMORY_URL)
    monkeypatch.setattr(client, "search_advanced", _stub_search_advanced())
    if llm_response is not _NO_POST_JSON_PATCH:
        monkeypatch.setattr(client, "_post_json", _stub_post_json(llm_response))

    try:
        return await client.write_guard(content="incoming content", domain="core")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_write_guard_llm_disabled_records_degrade_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = await _run_write_guard(monkeypatch, llm_enabled=False)
    assert decision["action"] == "ADD"
    assert decision["degrade_reasons"] == ["write_guard_llm_disabled"]


@pytest.mark.asyncio
async def test_write_guard_llm_config_missing_records_degrade_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = await _run_write_guard(monkeypatch, llm_enabled=True, llm_configured=False)
    assert decision["action"] == "ADD"
    assert decision["degrade_reasons"] == ["write_guard_llm_config_missing"]


@pytest.mark.asyncio
async def test_write_guard_llm_request_failed_records_degrade_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = await _run_write_guard(
        monkeypatch,
        llm_enabled=True,
        llm_configured=True,
        llm_response=None,
    )
    assert decision["action"] == "ADD"
    assert decision["degrade_reasons"] == ["write_guard_llm_request_failed"]


@pytest.mark.asyncio
async def test_write_guard_llm_empty_response_records_degrade_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = await _run_write_guard(
        monkeypatch,
        llm_enabled=True,
        llm_configured=True,
        llm_response={"choices": [{"message": {"content": ""}}]},
    )
    assert decision["action"] == "ADD"
    assert decision["degrade_reasons"] == ["write_guard_llm_response_empty"]


@pytest.mark.asyncio
async def test_write_guard_llm_invalid_json_records_degrade_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = await _run_write_guard(
        monkeypatch,
        llm_enabled=True,
        llm_configured=True,
        llm_response={"choices": [{"message": {"content": "not-json"}}]},
    )
    assert decision["action"] == "ADD"
    assert decision["degrade_reasons"] == ["write_guard_llm_response_invalid"]


@pytest.mark.asyncio
async def test_write_guard_llm_invalid_action_records_degrade_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = await _run_write_guard(
        monkeypatch,
        llm_enabled=True,
        llm_configured=True,
        llm_response={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "MERGE",
                                "target_id": 12,
                                "reason": "invalid action from llm",
                                "method": "llm",
                            }
                        )
                    }
                }
            ]
        },
    )
    assert decision["action"] == "ADD"
    assert decision["degrade_reasons"] == ["write_guard_llm_action_invalid"]


@pytest.mark.asyncio
async def test_write_guard_llm_success_returns_action_and_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = await _run_write_guard(
        monkeypatch,
        llm_enabled=True,
        llm_configured=True,
        llm_response={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "UPDATE",
                                "target_id": 12,
                                "reason": "same memory with fresher detail",
                                "method": "llm",
                            }
                        )
                    }
                }
            ]
        },
    )

    assert decision["action"] == "UPDATE"
    assert decision["target_id"] == 12
    assert decision["target_uri"] == "core://memory/12"
    assert decision["degrade_reasons"] == []
