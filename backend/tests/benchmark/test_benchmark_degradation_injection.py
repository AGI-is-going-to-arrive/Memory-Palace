import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import mcp_server
from db.sqlite_client import SQLiteClient


def test_degradation_injection_skeleton_threshold_available() -> None:
    benchmark_dir = Path(__file__).resolve().parent
    thresholds_path = benchmark_dir / "thresholds_v1.json"
    payload = json.loads(thresholds_path.read_text(encoding="utf-8"))
    assert 0.0 <= payload["global"]["degrade_rate_lt"] <= 1.0


@pytest.mark.asyncio
async def test_write_guard_stays_usable_when_search_advanced_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WRITE_GUARD_LLM_ENABLED", "false")
    client = SQLiteClient("sqlite+aiosqlite:///:memory:")

    async def _raise_search_advanced(*_: Any, **kwargs: Any) -> Dict[str, Any]:
        mode = str(kwargs.get("mode") or "")
        if mode == "semantic":
            raise RuntimeError("semantic backend outage")
        if mode == "keyword":
            raise ValueError("keyword backend outage")
        raise AssertionError(f"unexpected mode: {mode}")

    monkeypatch.setattr(client, "search_advanced", _raise_search_advanced)
    try:
        decision = await client.write_guard(content="new fact candidate", domain="core")
    finally:
        await client.close()

    assert decision["action"] in {"ADD", "UPDATE", "NOOP", "DELETE"}
    assert isinstance(decision.get("reason"), str) and decision["reason"]
    assert isinstance(decision.get("method"), str) and decision["method"]
    assert decision["degraded"] is True
    reasons = decision.get("degrade_reasons")
    assert isinstance(reasons, list)
    assert any(reason.startswith("write_guard_semantic_failed:") for reason in reasons)
    assert any(reason.startswith("write_guard_keyword_failed:") for reason in reasons)


class _FailingGistClient:
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
        _ = degrade_reasons
        raise RuntimeError("upstream llm timeout")


@pytest.mark.asyncio
async def test_generate_gist_llm_exception_falls_back_and_is_observable() -> None:
    payload = await mcp_server.generate_gist(
        "Session compaction notes:\n- first signal\n- second signal",
        client=_FailingGistClient(),
    )

    assert payload["gist_method"] in {
        "extractive_bullets",
        "sentence_fallback",
        "truncate_fallback",
    }
    assert isinstance(payload.get("gist_text"), str) and payload["gist_text"]
    reasons = payload.get("degrade_reasons")
    assert isinstance(reasons, list)
    assert any(reason.startswith("compact_gist_llm_exception:") for reason in reasons)


class _FakeFlushTracker:
    def __init__(self, summary: str) -> None:
        self.summary = summary

    async def should_flush(self, *, session_id: Optional[str]) -> bool:
        _ = session_id
        return True

    async def build_summary(self, *, session_id: Optional[str], limit: int = 12) -> str:
        _ = session_id
        _ = limit
        return self.summary

    async def mark_flushed(self, *, session_id: Optional[str]) -> None:
        _ = session_id
        return None


class _FailingCompactContextClient(_FailingGistClient):
    def __init__(self) -> None:
        self.memory_id = 101
        self.created_payload: Dict[str, Any] = {}
        self.gist_payload: Dict[str, Any] = {}

    async def get_memory_by_path(
        self, path: str, domain: str = "core"
    ) -> Optional[Dict[str, Any]]:
        _ = path
        _ = domain
        return None

    async def create_memory(self, **kwargs: Any) -> Dict[str, Any]:
        self.created_payload = dict(kwargs)
        return {
            "id": self.memory_id,
            "domain": kwargs.get("domain", "notes"),
            "path": "auto_flush_1",
            "uri": "notes://auto_flush_1",
            "index_targets": [self.memory_id],
        }

    async def upsert_memory_gist(self, **kwargs: Any) -> Dict[str, Any]:
        self.gist_payload = dict(kwargs)
        return {"id": 1, **kwargs}


async def _noop_async(*_: Any, **__: Any) -> None:
    return None


async def _false_async(*_: Any, **__: Any) -> bool:
    return False


async def _run_write_inline(_operation: str, task):
    return await task()


@pytest.mark.asyncio
async def test_compact_context_surfaces_degrade_reasons_when_gist_llm_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FailingCompactContextClient()
    fake_tracker = _FakeFlushTracker(
        "Session compaction notes:\n- deterministic fallback stays available"
    )

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: fake_client)
    monkeypatch.setattr(mcp_server.runtime_state, "flush_tracker", fake_tracker)
    monkeypatch.setattr(mcp_server, "_record_session_hit", _noop_async)
    monkeypatch.setattr(mcp_server, "_should_defer_index_on_write", _false_async)
    monkeypatch.setattr(mcp_server, "_run_write_lane", _run_write_inline)
    mcp_server._AUTO_FLUSH_IN_PROGRESS.clear()

    raw = await mcp_server.compact_context(reason="benchmark_injection", force=True, max_lines=5)
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["flushed"] is True
    assert payload["gist_method"] in {
        "extractive_bullets",
        "sentence_fallback",
        "truncate_fallback",
    }
    reasons = payload.get("degrade_reasons")
    assert isinstance(reasons, list)
    assert any(reason.startswith("compact_gist_llm_exception:") for reason in reasons)
