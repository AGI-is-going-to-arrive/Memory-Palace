import asyncio
import json

import pytest

import mcp_server
from runtime_state import ImportLearnAuditTracker


class _GuardOnlyClient:
    def __init__(self, decision):
        self._decision = dict(decision)
        self.last_write_guard_kwargs = None
        self.last_create_kwargs = None
        self.create_called = False
        self._next_id = 1
        self._paths = {}

    async def write_guard(self, **kwargs):
        self.last_write_guard_kwargs = dict(kwargs)
        return dict(self._decision)

    async def get_memory_by_path(
        self,
        path: str,
        domain: str,
        reinforce_access: bool = False,
    ):
        _ = reinforce_access
        key = (str(domain or "core"), str(path or ""))
        memory = self._paths.get(key)
        if not isinstance(memory, dict):
            return None
        return dict(memory)

    async def create_memory(self, **kwargs):
        self.create_called = True
        self.last_create_kwargs = dict(kwargs)
        domain = str(kwargs.get("domain") or "notes")
        parent_path = str(kwargs.get("parent_path") or "").strip().strip("/")
        title = str(kwargs.get("title") or "").strip()
        path = f"{parent_path}/{title}".strip("/")
        memory_id = self._next_id
        self._next_id += 1
        payload = {
            "id": memory_id,
            "domain": domain,
            "path": path,
            "uri": f"{domain}://{path}",
            "content": str(kwargs.get("content") or ""),
        }
        self._paths[(domain, path)] = payload
        return {
            "id": memory_id,
            "domain": domain,
            "path": path,
            "uri": f"{domain}://{path}",
        }


class _MetaClient:
    def __init__(self) -> None:
        self.values = {}

    async def set_runtime_meta(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get_runtime_meta(self, key: str):
        return self.values.get(key)


class _BrokenGuardClient:
    async def write_guard(self, **kwargs):
        _ = kwargs
        raise RuntimeError("write-guard-down")


class _CreateFailureClient(_GuardOnlyClient):
    async def create_memory(self, **kwargs):
        _ = kwargs
        raise RuntimeError("write-failed")


class _CreateLeafFailureClient(_GuardOnlyClient):
    async def create_memory(self, **kwargs):
        disclosure = str(kwargs.get("disclosure") or "")
        if disclosure == "Explicit learn trigger":
            raise RuntimeError("write-failed")
        return await super().create_memory(**kwargs)


class _SlowMetaClient(_MetaClient):
    async def set_runtime_meta(self, key: str, value: str) -> None:
        payload = json.loads(value)
        # Artificially delay stale writes so race conditions are easier to expose.
        if int(payload.get("total_events", 0)) == 1:
            await asyncio.sleep(0.05)
        await super().set_runtime_meta(key, value)


@pytest.mark.asyncio
async def test_explicit_learn_service_rejects_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "false")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient({"action": "ADD", "method": "keyword", "reason": "ok"})

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction note",
        source="manual_review",
        reason="fix factual drift",
        session_id="s-disabled",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "auto_learn_explicit_disabled"


@pytest.mark.asyncio
async def test_explicit_learn_service_disabled_does_not_persist_audit_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "false")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    meta_client = _MetaClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: meta_client)
    client = _GuardOnlyClient({"action": "ADD", "method": "keyword", "reason": "ok"})

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction note",
        source="manual_review",
        reason="fix factual drift",
        session_id="s-disabled-meta",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "auto_learn_explicit_disabled"
    persisted = await meta_client.get_runtime_meta(
        mcp_server.IMPORT_LEARN_AUDIT_META_KEY
    )
    assert persisted is None


@pytest.mark.asyncio
async def test_explicit_learn_service_requires_reason_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient({"action": "ADD", "method": "keyword", "reason": "ok"})

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction note",
        source="manual_review",
        reason="",
        session_id="s-reason",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "reason_required"


@pytest.mark.asyncio
async def test_explicit_learn_service_requires_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient({"action": "ADD", "method": "keyword", "reason": "ok"})

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction note",
        source="",
        reason="fix factual drift",
        session_id="s-source",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "source_required"


@pytest.mark.asyncio
async def test_explicit_learn_service_requires_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient({"action": "ADD", "method": "keyword", "reason": "ok"})

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction note",
        source="manual_review",
        reason="fix factual drift",
        session_id="",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "session_id_required"


@pytest.mark.asyncio
async def test_explicit_learn_service_requires_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient({"action": "ADD", "method": "keyword", "reason": "ok"})

    payload = await mcp_server.run_explicit_learn_service(
        content="",
        source="manual_review",
        reason="fix factual drift",
        session_id="s-content",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "content_required"


@pytest.mark.asyncio
async def test_explicit_learn_service_rejects_domain_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient({"action": "ADD", "method": "keyword", "reason": "ok"})

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction note",
        source="manual_review",
        reason="need new memory",
        session_id="s-domain",
        domain="core",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "domain_not_allowed"


@pytest.mark.asyncio
async def test_explicit_learn_service_rejects_when_write_guard_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient(
        {
            "action": "NOOP",
            "method": "embedding",
            "reason": "duplicate content",
            "target_id": 7,
        }
    )

    payload = await mcp_server.run_explicit_learn_service(
        content="duplicate correction",
        source="manual_review",
        reason="reason-text",
        session_id="s-blocked",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "write_guard_blocked:noop"
    assert payload["guard_action"] == "NOOP"


@pytest.mark.asyncio
async def test_explicit_learn_service_rejects_when_write_guard_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction",
        source="manual_review",
        reason="reason-text",
        session_id="s-guard-error",
        client=_BrokenGuardClient(),
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "write_guard_unavailable"
    assert payload["guard_action"] == "ERROR"
    assert payload["guard_method"] == "exception"
    assert payload["degraded"] is True
    assert payload["degrade_reasons"] == ["write_guard_exception"]


@pytest.mark.asyncio
async def test_explicit_learn_service_prepares_payload_when_guard_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient(
        {
            "action": "ADD",
            "method": "keyword",
            "reason": "no strong duplicate signal",
        }
    )

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction",
        source="manual_review",
        reason="reason-text",
        session_id="s-ready",
        actor_id="actor-1",
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is True
    assert payload["reason"] == "prepared"
    assert payload["guard_action"] == "ADD"
    assert payload["source_hash"]
    assert payload["target_parent_uri"] == "notes://corrections/s-ready"
    assert client.last_write_guard_kwargs is not None
    assert client.last_write_guard_kwargs["domain"] == "notes"


@pytest.mark.asyncio
async def test_explicit_learn_service_execute_mode_creates_memory_in_h3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _GuardOnlyClient(
        {"action": "ADD", "method": "keyword", "reason": "no strong duplicate signal"}
    )

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction",
        source="manual_review",
        reason="reason-text",
        session_id="s-exec",
        execute=True,
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is True
    assert payload["reason"] == "executed"
    assert payload["executed"] is True
    assert payload["created_memory"]["id"] > 0
    assert payload["created_memory"]["uri"].startswith("notes://corrections/s-exec/")
    created_namespace = payload.get("created_namespace_memories") or []
    assert isinstance(created_namespace, list)
    assert len(created_namespace) >= 1
    rollback = payload.get("rollback") or {}
    assert rollback.get("enabled") is True
    assert rollback.get("memory_id") == payload["created_memory"]["id"]
    assert rollback.get("mode") == "delete_memory_id"
    assert len(rollback.get("namespace_memory_ids") or []) >= 1
    assert isinstance(payload.get("batch_id"), str) and payload["batch_id"].startswith(
        "learn-s-exec-"
    )
    assert client.create_called is True
    assert client.last_create_kwargs is not None
    assert client.last_create_kwargs.get("parent_path") == "corrections/s-exec"


@pytest.mark.asyncio
async def test_explicit_learn_service_execute_mode_fail_closed_on_create_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _CreateFailureClient(
        {"action": "ADD", "method": "keyword", "reason": "no strong duplicate signal"}
    )

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction",
        source="manual_review",
        reason="reason-text",
        session_id="s-create-error",
        execute=True,
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "create_memory_failed"
    assert payload["error"] == "write-failed"


@pytest.mark.asyncio
async def test_explicit_learn_service_execute_failure_exposes_namespace_cleanup_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    client = _CreateLeafFailureClient(
        {"action": "ADD", "method": "keyword", "reason": "no strong duplicate signal"}
    )

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction",
        source="manual_review",
        reason="reason-text",
        session_id="s-leaf-error",
        execute=True,
        client=client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is False
    assert payload["reason"] == "create_memory_failed"
    created_namespace = payload.get("created_namespace_memories") or []
    assert len(created_namespace) >= 1
    rollback = payload.get("rollback") or {}
    assert rollback.get("mode") == "namespace_cleanup_only"
    assert len(rollback.get("namespace_memory_ids") or []) >= 1


@pytest.mark.asyncio
async def test_explicit_learn_service_persists_audit_summary_to_runtime_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_LEARN_EXPLICIT_ENABLED", "true")
    monkeypatch.setenv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    monkeypatch.setenv("AUTO_LEARN_REQUIRE_REASON", "true")
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    meta_client = _MetaClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: meta_client)
    guard_client = _GuardOnlyClient(
        {"action": "ADD", "method": "keyword", "reason": "no strong duplicate signal"}
    )

    payload = await mcp_server.run_explicit_learn_service(
        content="new correction",
        source="manual_review",
        reason="reason-text",
        session_id="s-persist",
        client=guard_client,
    )

    assert payload["ok"] is True
    assert payload["accepted"] is True
    persisted = await meta_client.get_runtime_meta(mcp_server.IMPORT_LEARN_AUDIT_META_KEY)
    assert isinstance(persisted, str)
    assert '"total_events": 1' in persisted


@pytest.mark.asyncio
async def test_record_import_learn_event_persistence_is_monotonic_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_server.runtime_state, "import_learn_tracker", ImportLearnAuditTracker()
    )
    meta_client = _SlowMetaClient()
    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: meta_client)

    await asyncio.gather(
        mcp_server._record_import_learn_event(
            event_type="learn",
            operation="learn_explicit",
            decision="accepted",
            reason="prepared-a",
            source="manual-review",
            session_id="s-race",
            actor_id="actor-a",
        ),
        mcp_server._record_import_learn_event(
            event_type="learn",
            operation="learn_explicit",
            decision="accepted",
            reason="prepared-b",
            source="manual-review",
            session_id="s-race",
            actor_id="actor-a",
        ),
    )

    persisted = await meta_client.get_runtime_meta(mcp_server.IMPORT_LEARN_AUDIT_META_KEY)
    assert isinstance(persisted, str)
    payload = json.loads(persisted)
    assert payload["total_events"] == 2
