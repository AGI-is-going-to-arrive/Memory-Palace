import json

import pytest

import mcp_server


class _MissingMemoryClient:
    async def get_memory_by_path(self, _path: str, _domain: str):
        return None


@pytest.mark.asyncio
async def test_read_memory_partial_validation_errors_return_json() -> None:
    raw = await mcp_server.read_memory("core://agent/index", chunk_id=-1)
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert "chunk_id must be >= 0" in payload["error"]


@pytest.mark.asyncio
async def test_read_memory_partial_not_found_returns_json(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _MissingMemoryClient())

    raw = await mcp_server.read_memory("core://agent/missing", chunk_id=0)
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert "not found" in payload["error"]


@pytest.mark.asyncio
async def test_update_memory_identical_patch_returns_tool_response_json() -> None:
    raw = await mcp_server.update_memory(
        uri="core://agent/index",
        old_string="same-content",
        new_string="same-content",
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["updated"] is False
    assert "identical" in payload["message"]


@pytest.mark.asyncio
async def test_search_memory_rejects_non_string_query() -> None:
    raw = await mcp_server.search_memory(123)  # type: ignore[arg-type]
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"] == "query must be a string."

