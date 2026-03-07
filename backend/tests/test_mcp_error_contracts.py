import json

import pytest

import mcp_server


class _MissingMemoryClient:
    async def get_memory_by_path(self, _path: str, _domain: str):
        return None


class _NoWriteClient:
    async def write_guard(self, **_kwargs):  # pragma: no cover - should never be called
        raise AssertionError("write_guard should not be called for system:// writes")

    async def get_memory_by_path(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("get_memory_by_path should not be called for system:// writes")

    async def remove_path(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("remove_path should not be called for system:// writes")

    async def add_path(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("add_path should not be called for system:// writes")


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


@pytest.mark.asyncio
async def test_search_memory_invalid_mode_validated_before_db_init(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("should_not_init_db_for_invalid_mode")

    monkeypatch.setattr(mcp_server, "get_sqlite_client", _boom)

    raw = await mcp_server.search_memory("memory queue", mode="invalid-mode")
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert "Invalid mode" in payload["error"]


@pytest.mark.asyncio
async def test_create_memory_rejects_system_domain_writes(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _NoWriteClient())

    raw = await mcp_server.create_memory(
        parent_uri="system://",
        content="blocked",
        priority=1,
        title="blocked",
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert "read-only" in payload["message"]


@pytest.mark.asyncio
async def test_update_memory_rejects_system_domain_writes() -> None:
    raw = await mcp_server.update_memory(
        uri="system://boot",
        append="\nblocked",
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert "read-only" in payload["message"]


@pytest.mark.asyncio
async def test_delete_memory_rejects_system_domain_writes(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _NoWriteClient())
    raw = await mcp_server.delete_memory("system://boot")
    assert raw.startswith("Error:")
    assert "read-only" in raw


@pytest.mark.asyncio
async def test_add_alias_rejects_system_domain_writes(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _NoWriteClient())
    raw = await mcp_server.add_alias("core://alias-node", "system://boot")
    assert raw.startswith("Error:")
    assert "read-only" in raw


def test_get_session_id_uses_request_aware_context(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeRequestContext:
        def __init__(self, session_obj, request_obj) -> None:
            self.session = session_obj
            self.request = request_obj

    class _FakeContext:
        def __init__(
            self,
            *,
            client_id: str,
            request_id: str,
            session_obj,
            request_obj,
        ) -> None:
            self._client_id = client_id
            self._request_id = request_id
            self._session_obj = session_obj
            self._request_context = _FakeRequestContext(session_obj, request_obj)

        @property
        def client_id(self):
            return self._client_id

        @property
        def request_id(self):
            return self._request_id

        @property
        def session(self):
            return self._session_obj

        @property
        def request_context(self):
            return self._request_context

    shared_session = object()
    ctx_a = _FakeContext(
        client_id="client-A",
        request_id="req-001",
        session_obj=shared_session,
        request_obj=object(),
    )
    ctx_b = _FakeContext(
        client_id="client-A",
        request_id="req-002",
        session_obj=shared_session,
        request_obj=object(),
    )

    monkeypatch.setattr(mcp_server.mcp, "get_context", lambda: ctx_a)
    session_a = mcp_server.get_session_id()
    monkeypatch.setattr(mcp_server.mcp, "get_context", lambda: ctx_b)
    session_b = mcp_server.get_session_id()

    assert session_a.startswith("mcp_ctx_")
    assert session_b.startswith("mcp_ctx_")
    assert session_a != session_b


def test_get_session_id_falls_back_when_context_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_context_error():
        raise RuntimeError("context unavailable")

    monkeypatch.setattr(mcp_server.mcp, "get_context", _raise_context_error)
    assert mcp_server.get_session_id() == mcp_server._SESSION_ID
