import asyncio
import json
import time
from typing import Any, Dict

import pytest

from api import maintenance as maintenance_api
import mcp_server
from runtime_state import IndexTaskWorker, SleepTimeConsolidator


class _FakeIndexClient:
    def __init__(self) -> None:
        self.reindex_calls: list[tuple[int, str]] = []
        self.rebuild_calls: list[str] = []
        self.deleted_memory_ids: list[int] = []
        self.gist_upserts: list[Dict[str, Any]] = []
        self._slow_memory_ids: set[int] = {1, 99}
        self._orphan_items: list[Dict[str, Any]] = [
            {"id": 1, "category": "deprecated"},
            {"id": 2, "category": "orphaned"},
            {"id": 3, "category": "deprecated"},
        ]
        self._memory_versions: Dict[int, Dict[str, Any]] = {
            1: {"memory_id": 1, "content": "Legacy duplicate payload", "deprecated": True},
            2: {"memory_id": 2, "content": "Independent orphan payload", "deprecated": False},
            3: {"memory_id": 3, "content": "Legacy duplicate payload", "deprecated": True},
            201: {"memory_id": 201, "content": "Alpha cluster fact one"},
            202: {"memory_id": 202, "content": "Alpha cluster fact two"},
            203: {"memory_id": 203, "content": "Alpha cluster fact three"},
            301: {"memory_id": 301, "content": "Beta standalone memory"},
        }

    async def reindex_memory(self, *, memory_id: int, reason: str = "write") -> Dict[str, Any]:
        self.reindex_calls.append((memory_id, reason))
        if memory_id in self._slow_memory_ids:
            await asyncio.sleep(0.25)
        return {"ok": True, "memory_id": memory_id, "reason": reason}

    async def rebuild_index(self, *, reason: str = "manual") -> Dict[str, Any]:
        self.rebuild_calls.append(reason)
        await asyncio.sleep(0.05)
        return {"ok": True, "reason": reason}

    async def get_all_orphan_memories(self) -> list[Dict[str, Any]]:
        return list(self._orphan_items)

    async def get_vitality_cleanup_candidates(self, **_kwargs: Any) -> Dict[str, Any]:
        return {
            "count": 2,
            "items": [
                {"memory_id": 101, "state_hash": "a" * 64},
                {"memory_id": 102, "state_hash": "b" * 64},
            ],
        }

    async def get_memory_version(self, memory_id: int) -> Dict[str, Any]:
        payload = self._memory_versions.get(int(memory_id))
        if payload is None:
            raise ValueError(f"memory_id={memory_id} not found")
        return dict(payload)

    async def permanently_delete_memory(
        self,
        memory_id: int,
        *,
        require_orphan: bool = False,
        expected_state_hash: str | None = None,
    ) -> Dict[str, Any]:
        _ = require_orphan
        _ = expected_state_hash
        parsed_id = int(memory_id)
        if parsed_id not in self._memory_versions:
            raise ValueError(f"memory_id={parsed_id} not found")
        self.deleted_memory_ids.append(parsed_id)
        self._memory_versions.pop(parsed_id, None)
        return {"deleted": True, "memory_id": parsed_id}

    async def get_recent_memories(self, limit: int = 10) -> list[Dict[str, Any]]:
        items = [
            {"memory_id": 201, "uri": "core://projects/alpha/one"},
            {"memory_id": 202, "uri": "core://projects/alpha/two"},
            {"memory_id": 203, "uri": "core://projects/alpha/three"},
            {"memory_id": 301, "uri": "core://projects/beta/one"},
        ]
        return items[: max(1, int(limit))]

    async def get_memory_by_id(self, memory_id: int) -> Dict[str, Any]:
        payload = self._memory_versions.get(int(memory_id))
        if payload is None:
            raise ValueError(f"memory_id={memory_id} not found")
        return {"id": int(memory_id), "content": str(payload.get("content") or "")}

    async def upsert_memory_gist(self, **kwargs: Any) -> Dict[str, Any]:
        self.gist_upserts.append(dict(kwargs))
        return {
            "ok": True,
            "memory_id": int(kwargs.get("memory_id") or 0),
            "source_hash": str(kwargs.get("source_hash") or ""),
            "gist_method": str(kwargs.get("gist_method") or ""),
        }

    async def get_latest_memory_gist(self, memory_id: int) -> Dict[str, Any] | None:
        _ = memory_id
        return None


class _SleepPreviewOnlyClient:
    def __init__(self) -> None:
        self.rebuild_calls: list[str] = []

    async def rebuild_index(self, *, reason: str = "manual") -> Dict[str, Any]:
        self.rebuild_calls.append(reason)
        return {"ok": True, "reason": reason}

    async def get_all_orphan_memories(self) -> list[Dict[str, Any]]:
        return [{"id": 7, "category": "deprecated"}]

    async def get_vitality_cleanup_candidates(self, **_kwargs: Any) -> Dict[str, Any]:
        return {"count": 0, "items": []}


class _FailingSleepRebuildClient(_FakeIndexClient):
    async def rebuild_index(self, *, reason: str = "manual") -> Dict[str, Any]:
        _ = reason
        raise RuntimeError("sleep_rebuild_failed")


class _QueueFullIndexWorker:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {
            "idx-failed-rebuild": {
                "job_id": "idx-failed-rebuild",
                "task_type": "rebuild_index",
                "status": "failed",
                "reason": "queue_full_prev",
            }
        }

    async def status(self) -> Dict[str, Any]:
        return {"enabled": True, "queue_depth": 0}

    async def get_job(self, *, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": f"job '{job_id}' not found."}
        return {"ok": True, "job": dict(job)}

    async def enqueue_reindex_memory(self, *, memory_id: int, reason: str = "api") -> Dict[str, Any]:
        return {
            "queued": False,
            "dropped": True,
            "job_id": "idx-drop-reindex",
            "memory_id": memory_id,
            "reason": "queue_full",
        }

    async def enqueue_rebuild(self, *, reason: str = "api") -> Dict[str, Any]:
        return {
            "queued": False,
            "dropped": True,
            "job_id": "idx-drop-rebuild",
            "reason": "queue_full",
        }


class _QueueFullSleepCoordinator:
    async def schedule(self, *, index_worker, force: bool = False, reason: str = "runtime") -> Dict[str, Any]:
        _ = index_worker
        _ = force
        _ = reason
        return {
            "scheduled": False,
            "queued": False,
            "dropped": True,
            "job_id": "idx-drop-sleep",
            "reason": "queue_full",
        }

    async def status(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "scheduled": False,
            "reason": "queue_full",
        }


async def _wait_for_job_status(
    worker: IndexTaskWorker,
    *,
    job_id: str,
    expected_status: str,
    timeout_seconds: float = 2.0,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = await worker.get_job(job_id=job_id)
        if payload.get("ok") and (payload.get("job") or {}).get("status") == expected_status:
            return payload["job"]
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach status '{expected_status}' in time")


@pytest.mark.asyncio
async def test_index_job_detail_returns_404_when_job_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.get_index_job("idx-missing")

    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_index_job_cancel_cancels_queued_job_and_job_detail_reflects_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    await worker.ensure_started(lambda: client)

    async def _ensure_started(_factory) -> None:
        await worker.ensure_started(lambda: client)

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    first = await worker.enqueue_reindex_memory(memory_id=1, reason="week7-first")
    second = await worker.enqueue_reindex_memory(memory_id=2, reason="week7-second")
    await _wait_for_job_status(worker, job_id=first["job_id"], expected_status="running")
    await _wait_for_job_status(worker, job_id=second["job_id"], expected_status="queued")

    cancel_result = await maintenance_api.cancel_index_job(
        second["job_id"],
        maintenance_api.IndexJobCancelRequest(reason="unit_cancel"),
    )
    detail_result = await maintenance_api.get_index_job(second["job_id"])

    await worker.wait_for_job(job_id=first["job_id"], timeout_seconds=2.0)
    await worker.shutdown()

    assert cancel_result["ok"] is True
    assert cancel_result.get("cancelled") is True
    assert cancel_result["job"]["status"] == "cancelled"
    assert cancel_result["job"]["cancel_reason"] == "unit_cancel"

    assert detail_result["ok"] is True
    assert detail_result["job"]["status"] == "cancelled"
    assert detail_result["runtime_worker"]["stats"]["cancelled"] >= 1


@pytest.mark.asyncio
async def test_index_job_cancel_requests_running_job_and_finishes_as_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    await worker.ensure_started(lambda: client)

    async def _ensure_started(_factory) -> None:
        await worker.ensure_started(lambda: client)

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    enqueue_result = await worker.enqueue_reindex_memory(memory_id=99, reason="week7-running")
    await _wait_for_job_status(worker, job_id=enqueue_result["job_id"], expected_status="running")

    cancel_result = await maintenance_api.cancel_index_job(
        enqueue_result["job_id"],
        maintenance_api.IndexJobCancelRequest(reason="manual_abort"),
    )
    wait_result = await worker.wait_for_job(
        job_id=enqueue_result["job_id"],
        timeout_seconds=2.0,
    )
    detail_result = await maintenance_api.get_index_job(enqueue_result["job_id"])

    await worker.shutdown()

    assert cancel_result["ok"] is True
    assert cancel_result.get("cancel_requested") is True
    assert cancel_result["job"]["status"] == "cancelling"
    assert wait_result["ok"] is True
    assert wait_result["job"]["status"] == "cancelled"
    assert wait_result["job"]["cancel_reason"] == "manual_abort"
    assert detail_result["job"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_index_job_cancel_returns_404_when_job_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.cancel_index_job(
            "idx-missing",
            maintenance_api.IndexJobCancelRequest(reason="missing"),
        )

    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_index_job_cancel_returns_409_when_job_already_finalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    await worker.ensure_started(lambda: client)

    async def _ensure_started(_factory) -> None:
        await worker.ensure_started(lambda: client)

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    first = await worker.enqueue_reindex_memory(memory_id=1, reason="week7-finalized-first")
    second = await worker.enqueue_reindex_memory(memory_id=2, reason="week7-finalized-second")
    await _wait_for_job_status(worker, job_id=first["job_id"], expected_status="running")
    await _wait_for_job_status(worker, job_id=second["job_id"], expected_status="queued")
    await maintenance_api.cancel_index_job(
        second["job_id"],
        maintenance_api.IndexJobCancelRequest(reason="first-cancel"),
    )

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.cancel_index_job(
            second["job_id"],
            maintenance_api.IndexJobCancelRequest(reason="second-cancel"),
        )

    await worker.wait_for_job(job_id=first["job_id"], timeout_seconds=2.0)
    await worker.shutdown()

    assert exc_info.value.status_code == 409
    assert str(exc_info.value.detail) == "job_already_finalized"


@pytest.mark.asyncio
async def test_index_job_retry_requeues_cancelled_reindex_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    await worker.ensure_started(lambda: client)

    async def _ensure_started(_factory) -> None:
        await worker.ensure_started(lambda: client)

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    first = await worker.enqueue_reindex_memory(memory_id=1, reason="week7-retry-first")
    second = await worker.enqueue_reindex_memory(memory_id=2, reason="week7-retry-second")
    await _wait_for_job_status(worker, job_id=first["job_id"], expected_status="running")
    await _wait_for_job_status(worker, job_id=second["job_id"], expected_status="queued")

    await maintenance_api.cancel_index_job(
        second["job_id"],
        maintenance_api.IndexJobCancelRequest(reason="retry-prep"),
    )
    retry_payload = await maintenance_api.retry_index_job(
        second["job_id"],
        maintenance_api.IndexJobRetryRequest(reason="retry-via-api"),
    )

    retry_job_id = str(retry_payload.get("job_id") or "")
    assert retry_payload["ok"] is True
    assert retry_payload["retry_of_job_id"] == second["job_id"]
    assert retry_payload["task_type"] == "reindex_memory"
    assert retry_payload["reason"] == "retry-via-api"
    assert retry_payload["queued"] is True
    assert retry_job_id

    wait_retry = await worker.wait_for_job(job_id=retry_job_id, timeout_seconds=2.0)
    wait_first = await worker.wait_for_job(job_id=first["job_id"], timeout_seconds=2.0)
    await worker.shutdown()

    assert wait_retry["ok"] is True
    assert wait_retry["job"]["status"] == "succeeded"
    assert wait_first["ok"] is True
    assert client.reindex_calls == [(1, "week7-retry-first"), (2, "retry-via-api")]


@pytest.mark.asyncio
async def test_index_job_retry_returns_404_when_job_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.retry_index_job("idx-missing")

    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_index_job_retry_returns_409_when_job_status_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    await worker.ensure_started(lambda: client)

    async def _ensure_started(_factory) -> None:
        await worker.ensure_started(lambda: client)

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    enqueue_result = await worker.enqueue_reindex_memory(memory_id=1, reason="week7-retry-running")
    await _wait_for_job_status(worker, job_id=enqueue_result["job_id"], expected_status="running")

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.retry_index_job(enqueue_result["job_id"])

    await worker.wait_for_job(job_id=enqueue_result["job_id"], timeout_seconds=2.0)
    await worker.shutdown()

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail or {}
    assert detail["error"] == "job_retry_not_allowed"
    assert detail["reason"] == "status:running"


@pytest.mark.asyncio
async def test_index_job_retry_returns_503_when_enqueue_is_queue_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.retry_index_job(
            "idx-failed-rebuild",
            maintenance_api.IndexJobRetryRequest(reason="retry-week7-queue-full"),
        )

    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail or {}
    assert detail["error"] == "index_job_enqueue_failed"
    assert detail["reason"] == "queue_full"
    assert detail["operation"] == "retry_rebuild_index"


@pytest.mark.asyncio
async def test_index_job_retry_returns_409_when_sleep_consolidation_not_scheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    async def _ensure_started(_factory) -> None:
        return None

    async def _get_job(*, job_id: str) -> Dict[str, Any]:
        _ = job_id
        return {
            "ok": True,
            "job": {
                "job_id": "idx-sleep-failed",
                "task_type": "sleep_consolidation",
                "status": "failed",
            },
        }

    class _NotScheduledSleepCoordinator:
        async def schedule(self, *, index_worker, force: bool = False, reason: str = "runtime") -> Dict[str, Any]:
            _ = index_worker
            _ = force
            _ = reason
            return {"scheduled": False, "reason": "sleep_disabled"}

    monkeypatch.setattr(worker, "get_job", _get_job)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)
    monkeypatch.setattr(
        maintenance_api.runtime_state,
        "sleep_consolidation",
        _NotScheduledSleepCoordinator(),
    )

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.retry_index_job("idx-sleep-failed")

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail or {}
    assert detail["error"] == "job_retry_not_scheduled"
    assert detail["reason"] == "sleep_disabled"


@pytest.mark.asyncio
async def test_index_job_retry_returns_409_for_unsupported_task_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    async def _ensure_started(_factory) -> None:
        return None

    async def _get_job(*, job_id: str) -> Dict[str, Any]:
        _ = job_id
        return {
            "ok": True,
            "job": {
                "job_id": "idx-unsupported",
                "task_type": "unknown_task",
                "status": "failed",
            },
        }

    monkeypatch.setattr(worker, "get_job", _get_job)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.retry_index_job("idx-unsupported")

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail or {}
    assert detail["error"] == "job_retry_unsupported_task_type"
    assert detail["task_type"] == "unknown_task"


@pytest.mark.asyncio
async def test_index_job_retry_returns_409_when_memory_id_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    async def _ensure_started(_factory) -> None:
        return None

    async def _get_job(*, job_id: str) -> Dict[str, Any]:
        _ = job_id
        return {
            "ok": True,
            "job": {
                "job_id": "idx-reindex-invalid",
                "task_type": "reindex_memory",
                "status": "failed",
                "memory_id": 0,
            },
        }

    monkeypatch.setattr(worker, "get_job", _get_job)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.retry_index_job("idx-reindex-invalid")

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail or {}
    assert detail["error"] == "job_retry_invalid_memory_id"
    assert detail["task_type"] == "reindex_memory"


@pytest.mark.asyncio
async def test_index_job_retry_sleep_consolidation_returns_503_when_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()
    coordinator = _QueueFullSleepCoordinator()

    async def _ensure_started(_factory) -> None:
        return None

    async def _get_job(*, job_id: str) -> Dict[str, Any]:
        _ = job_id
        return {
            "ok": True,
            "job": {
                "job_id": "idx-retry-sleep",
                "task_type": "sleep_consolidation",
                "status": "failed",
            },
        }

    monkeypatch.setattr(worker, "get_job", _get_job)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)
    monkeypatch.setattr(maintenance_api.runtime_state, "sleep_consolidation", coordinator)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.retry_index_job("idx-retry-sleep")

    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail or {}
    assert detail["error"] == "index_job_enqueue_failed"
    assert detail["reason"] == "queue_full"
    assert detail["operation"] == "retry_sleep_consolidation"


@pytest.mark.asyncio
async def test_reindex_job_forwards_reason_to_client() -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    await worker.ensure_started(lambda: client)

    enqueue_result = await worker.enqueue_reindex_memory(
        memory_id=2,
        reason="week7-reason-forward",
    )
    wait_result = await worker.wait_for_job(
        job_id=enqueue_result["job_id"],
        timeout_seconds=2.0,
    )
    await worker.shutdown()

    assert wait_result["ok"] is True
    assert wait_result["job"]["status"] == "succeeded"
    assert wait_result["job"]["result"]["reason"] == "week7-reason-forward"
    assert client.reindex_calls == [(2, "week7-reason-forward")]


@pytest.mark.asyncio
async def test_sleep_consolidation_job_executes_preview_and_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_SLEEP_DEDUP_APPLY", "1")
    monkeypatch.setenv("RUNTIME_SLEEP_FRAGMENT_ROLLUP_APPLY", "1")

    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    await worker.ensure_started(lambda: client)

    enqueue_result = await worker.enqueue_sleep_consolidation(reason="nightly")
    wait_result = await worker.wait_for_job(
        job_id=enqueue_result["job_id"],
        timeout_seconds=2.0,
    )
    await worker.shutdown()

    assert enqueue_result["queued"] is True
    assert wait_result["ok"] is True
    assert wait_result["job"]["status"] == "succeeded"
    result = wait_result["job"]["result"]
    assert result["task"] == "sleep_consolidation"
    assert result["policy"]["dedup_apply_enabled"] is True
    assert result["policy"]["fragment_rollup_apply_enabled"] is True
    assert result["orphans"]["deprecated"] == 2
    assert result["orphans"]["orphaned"] == 1
    assert result["dedup"]["duplicate_groups"] == 1
    assert result["dedup"]["preview_only"] is False
    assert result["dedup"]["deleted_duplicates"] == 1
    assert result["dedup"]["deleted_memory_ids"] == [3]
    assert client.deleted_memory_ids == [3]
    assert result["cleanup_preview"]["candidate_count"] == 2
    assert result["fragment_rollup"]["preview_only"] is False
    assert result["fragment_rollup"]["preview_groups"] == 1
    assert result["fragment_rollup"]["groups_aggregated"] == 1
    assert result["fragment_rollup"]["gist_upserts"] == 1
    assert result["fragment_rollup"]["memory_coverage"] == 3
    assert client.gist_upserts
    assert client.gist_upserts[0]["gist_method"] == "sleep_fragment_rollup"
    assert result["rebuild_result"]["ok"] is True
    assert any(call.startswith("sleep_consolidation:") for call in client.rebuild_calls)


@pytest.mark.asyncio
async def test_sleep_consolidation_defaults_to_preview_only_when_apply_flags_disabled() -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    await worker.ensure_started(lambda: client)

    enqueue_result = await worker.enqueue_sleep_consolidation(reason="preview-default")
    wait_result = await worker.wait_for_job(
        job_id=enqueue_result["job_id"],
        timeout_seconds=2.0,
    )
    await worker.shutdown()

    assert wait_result["ok"] is True
    assert wait_result["job"]["status"] == "succeeded"
    result = wait_result["job"]["result"]
    assert result["policy"]["dedup_apply_enabled"] is False
    assert result["policy"]["fragment_rollup_apply_enabled"] is False
    assert result["dedup"]["preview_only"] is True
    assert result["dedup"]["duplicate_groups"] == 1
    assert result["dedup"]["deleted_duplicates"] == 0
    assert client.deleted_memory_ids == []
    assert result["fragment_rollup"]["preview_only"] is True
    assert result["fragment_rollup"]["preview_groups"] == 1
    assert result["fragment_rollup"]["groups_aggregated"] == 0
    assert result["fragment_rollup"]["gist_upserts"] == 0
    assert client.gist_upserts == []
    assert result["rebuild_result"]["ok"] is True


@pytest.mark.asyncio
async def test_sleep_consolidation_uses_short_retry_after_queue_full() -> None:
    class _QueueFullThenRecoverWorker:
        def __init__(self) -> None:
            self.calls = 0

        async def enqueue_sleep_consolidation(self, *, reason: str = "runtime") -> Dict[str, Any]:
            _ = reason
            self.calls += 1
            if self.calls == 1:
                return {
                    "queued": False,
                    "dropped": True,
                    "job_id": "idx-drop-sleep",
                    "reason": "queue_full",
                }
            return {
                "queued": True,
                "job_id": "idx-retry-sleep",
                "reason": "runtime",
            }

    worker = _QueueFullThenRecoverWorker()
    coordinator = SleepTimeConsolidator()

    first_result = await coordinator.schedule(
        index_worker=worker,
        force=False,
        reason="runtime.ensure_started",
    )
    second_result = await coordinator.schedule(
        index_worker=worker,
        force=False,
        reason="runtime.ensure_started",
    )

    coordinator._last_check_ts -= SleepTimeConsolidator._QUEUE_FULL_RETRY_SECONDS + 0.1
    third_result = await coordinator.schedule(
        index_worker=worker,
        force=False,
        reason="runtime.ensure_started",
    )

    assert first_result["scheduled"] is False
    assert first_result["enqueue_reason"] == "queue_full"
    assert first_result["retry_after_seconds"] == min(
        float(coordinator._check_interval_seconds),
        SleepTimeConsolidator._QUEUE_FULL_RETRY_SECONDS,
    )
    assert worker.calls == 2
    assert second_result["job_id"] == "idx-drop-sleep"
    assert second_result["scheduled"] is False
    assert third_result["job_id"] == "idx-retry-sleep"
    assert third_result["scheduled"] is True
    assert third_result["retry_after_seconds"] == float(coordinator._check_interval_seconds)


@pytest.mark.asyncio
async def test_sleep_consolidation_degrades_when_dedup_or_rollup_methods_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_SLEEP_DEDUP_APPLY", "1")
    monkeypatch.setenv("RUNTIME_SLEEP_FRAGMENT_ROLLUP_APPLY", "1")

    worker = IndexTaskWorker()
    client = _SleepPreviewOnlyClient()
    await worker.ensure_started(lambda: client)

    enqueue_result = await worker.enqueue_sleep_consolidation(reason="preview-only")
    wait_result = await worker.wait_for_job(
        job_id=enqueue_result["job_id"],
        timeout_seconds=2.0,
    )
    await worker.shutdown()

    assert wait_result["ok"] is True
    assert wait_result["job"]["status"] == "succeeded"
    result = wait_result["job"]["result"]
    assert result["degraded"] is True
    assert "sleep_orphan_dedup_unavailable" in result["degrade_reasons"]
    assert "sleep_fragment_rollup_unavailable" in result["degrade_reasons"]
    assert result["rebuild_result"]["ok"] is True
    assert client.rebuild_calls


@pytest.mark.asyncio
async def test_sleep_consolidation_does_not_overwrite_existing_non_rollup_gist_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("RUNTIME_SLEEP_FRAGMENT_ROLLUP_APPLY", "1")
    monkeypatch.setenv("RUNTIME_SLEEP_DEDUP_APPLY", "0")

    from db.sqlite_client import SQLiteClient

    db_path = tmp_path / "week7-sleep-rollup.db"
    client = SQLiteClient(f"sqlite+aiosqlite:///{db_path}")
    await client.init_db()

    await client.create_memory(
        parent_path="",
        content="Projects root node",
        priority=1,
        title="projects",
        domain="core",
    )
    await client.create_memory(
        parent_path="projects",
        content="Alpha shard one",
        priority=1,
        title="a1",
        domain="core",
    )
    await client.create_memory(
        parent_path="projects",
        content="Alpha shard two",
        priority=1,
        title="a2",
        domain="core",
    )
    await client.create_memory(
        parent_path="projects",
        content="Alpha shard three",
        priority=1,
        title="a3",
        domain="core",
    )

    recent = await client.get_recent_memories(limit=20)
    project_children = [
        item
        for item in recent
        if isinstance(item, dict)
        and str(item.get("uri") or "").startswith("core://projects/")
    ]
    assert len(project_children) >= 3
    anchor_memory_id = int(project_children[0]["memory_id"])

    existing_gist = await client.upsert_memory_gist(
        memory_id=anchor_memory_id,
        gist_text="existing canonical gist",
        source_hash="existing-canonical-hash",
        gist_method="extractive_bullets",
        quality_score=0.77,
    )

    worker = IndexTaskWorker()
    await worker.ensure_started(lambda: client)
    try:
        enqueue_result = await worker.enqueue_sleep_consolidation(reason="sqlite-guard")
        wait_result = await worker.wait_for_job(
            job_id=enqueue_result["job_id"],
            timeout_seconds=3.0,
        )
        assert wait_result["ok"] is True
        assert wait_result["job"]["status"] == "succeeded"
        result = wait_result["job"]["result"]
        assert result["policy"]["fragment_rollup_apply_enabled"] is True
        assert result["fragment_rollup"]["preview_groups"] >= 1
        assert result["fragment_rollup"]["skipped_existing_gist"] >= 1
        assert result["fragment_rollup"]["groups_aggregated"] == 0
        assert result["fragment_rollup"]["gist_upserts"] == 0
        assert result["rebuild_result"]["reason"].startswith("sleep_consolidation:")
    finally:
        await worker.shutdown()

    latest_gist = await client.get_latest_memory_gist(anchor_memory_id)
    await client.close()

    assert latest_gist is not None
    assert latest_gist["gist_method"] == "extractive_bullets"
    assert latest_gist["gist_text"] == "existing canonical gist"
    assert latest_gist["source_hash"] == "existing-canonical-hash"
    assert latest_gist["id"] == existing_gist["id"]


@pytest.mark.asyncio
async def test_sleep_consolidation_job_fails_when_rebuild_index_raises() -> None:
    worker = IndexTaskWorker()
    client = _FailingSleepRebuildClient()
    await worker.ensure_started(lambda: client)

    enqueue_result = await worker.enqueue_sleep_consolidation(reason="sleep-fail")
    wait_result = await worker.wait_for_job(
        job_id=enqueue_result["job_id"],
        timeout_seconds=2.0,
    )
    await worker.shutdown()

    assert wait_result["ok"] is True
    assert wait_result["job"]["status"] == "failed"
    assert wait_result["job"]["error"] == "sleep_rebuild_failed"


@pytest.mark.asyncio
async def test_maintenance_trigger_sleep_consolidation_returns_wait_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    coordinator = SleepTimeConsolidator()
    await worker.ensure_started(lambda: client)

    async def _ensure_started(_factory) -> None:
        await worker.ensure_started(lambda: client)

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)
    monkeypatch.setattr(maintenance_api.runtime_state, "sleep_consolidation", coordinator)

    payload = await maintenance_api.trigger_sleep_consolidation(
        reason="ops_console",
        wait=True,
        timeout_seconds=2,
    )
    await worker.shutdown()

    assert payload["ok"] is True
    assert payload["reason"] == "ops_console"
    assert payload["wait_result"]["ok"] is True
    assert payload["wait_result"]["job"]["status"] == "succeeded"
    assert payload["runtime_worker"]["stats"]["succeeded"] >= 1
    assert payload["sleep_consolidation"]["enabled"] in {True, False}


@pytest.mark.asyncio
async def test_maintenance_reindex_returns_503_when_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: _FakeIndexClient())

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.reindex_memory(memory_id=7, reason="week7-queue-full")

    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail or {}
    assert detail["error"] == "index_job_enqueue_failed"
    assert detail["reason"] == "queue_full"
    assert detail["operation"] == "reindex_memory"


@pytest.mark.asyncio
async def test_maintenance_rebuild_returns_503_when_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)
    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: _FakeIndexClient())

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.rebuild_index(reason="week7-queue-full")

    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail or {}
    assert detail["error"] == "index_job_enqueue_failed"
    assert detail["reason"] == "queue_full"
    assert detail["operation"] == "rebuild_index"


@pytest.mark.asyncio
async def test_maintenance_sleep_consolidation_returns_503_when_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()
    coordinator = _QueueFullSleepCoordinator()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", worker)
    monkeypatch.setattr(maintenance_api.runtime_state, "sleep_consolidation", coordinator)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.trigger_sleep_consolidation(reason="week7-sleep-queue-full")

    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail or {}
    assert detail["error"] == "index_job_enqueue_failed"
    assert detail["reason"] == "queue_full"
    assert detail["operation"] == "sleep_consolidation"


@pytest.mark.asyncio
async def test_mcp_rebuild_index_supports_sleep_consolidation_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    coordinator = SleepTimeConsolidator()
    await worker.ensure_started(lambda: client)

    async def _ensure_started(_factory) -> None:
        await worker.ensure_started(lambda: client)

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(mcp_server.runtime_state, "index_worker", worker)
    monkeypatch.setattr(mcp_server.runtime_state, "sleep_consolidation", coordinator)

    raw = await mcp_server.rebuild_index(
        reason="mcp_week7",
        wait=True,
        timeout_seconds=2,
        sleep_consolidation=True,
    )
    payload = json.loads(raw)
    await worker.shutdown()

    assert payload["ok"] is True
    assert payload["task_type"] == "sleep_consolidation"
    assert payload["wait_result"]["ok"] is True
    assert payload["wait_result"]["job"]["status"] == "succeeded"
    assert payload["sleep_consolidation"]["enabled"] in {True, False}


@pytest.mark.asyncio
async def test_mcp_rebuild_index_rejects_sleep_consolidation_with_memory_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = IndexTaskWorker()
    client = _FakeIndexClient()
    coordinator = SleepTimeConsolidator()
    await worker.ensure_started(lambda: client)

    async def _ensure_started(_factory) -> None:
        await worker.ensure_started(lambda: client)

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(mcp_server.runtime_state, "index_worker", worker)
    monkeypatch.setattr(mcp_server.runtime_state, "sleep_consolidation", coordinator)

    raw = await mcp_server.rebuild_index(memory_id=7, sleep_consolidation=True)
    payload = json.loads(raw)
    await worker.shutdown()

    assert payload["ok"] is False
    assert "incompatible" in payload["error"]


@pytest.mark.asyncio
async def test_mcp_rebuild_index_returns_error_when_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _FakeIndexClient())
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(mcp_server.runtime_state, "index_worker", worker)

    raw = await mcp_server.rebuild_index(memory_id=7, reason="week7-queue-full")
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"] == "queue_full"
    assert payload["task_type"] == "reindex_memory"
    assert payload["dropped"] is True
    assert payload["request_reason"] == "week7-queue-full"


@pytest.mark.asyncio
async def test_mcp_rebuild_index_returns_error_when_rebuild_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _FakeIndexClient())
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(mcp_server.runtime_state, "index_worker", worker)

    raw = await mcp_server.rebuild_index(reason="week7-rebuild-queue-full")
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"] == "queue_full"
    assert payload["task_type"] == "rebuild_index"
    assert payload["dropped"] is True
    assert payload["request_reason"] == "week7-rebuild-queue-full"


@pytest.mark.asyncio
async def test_mcp_rebuild_index_sleep_consolidation_returns_error_when_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()
    coordinator = _QueueFullSleepCoordinator()

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _FakeIndexClient())
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(mcp_server.runtime_state, "index_worker", worker)
    monkeypatch.setattr(mcp_server.runtime_state, "sleep_consolidation", coordinator)

    raw = await mcp_server.rebuild_index(
        reason="week7-sleep-queue-full",
        sleep_consolidation=True,
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"] == "queue_full"
    assert payload["task_type"] == "sleep_consolidation"
    assert payload["dropped"] is True
    assert payload["request_reason"] == "week7-sleep-queue-full"


@pytest.mark.asyncio
async def test_mcp_rebuild_index_sleep_consolidation_returns_error_when_not_scheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _QueueFullIndexWorker()

    class _NotScheduledSleepCoordinator:
        async def schedule(self, *, index_worker, force: bool = False, reason: str = "runtime") -> Dict[str, Any]:
            _ = index_worker
            _ = force
            _ = reason
            return {"scheduled": False, "reason": "sleep_disabled"}

        async def status(self) -> Dict[str, Any]:
            return {"enabled": False, "scheduled": False, "reason": "sleep_disabled"}

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: _FakeIndexClient())
    monkeypatch.setattr(mcp_server.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(mcp_server.runtime_state, "index_worker", worker)
    monkeypatch.setattr(
        mcp_server.runtime_state,
        "sleep_consolidation",
        _NotScheduledSleepCoordinator(),
    )

    raw = await mcp_server.rebuild_index(
        reason="week7-sleep-not-scheduled",
        sleep_consolidation=True,
    )
    payload = json.loads(raw)

    assert payload["ok"] is False
    assert payload["error"] == "sleep_disabled"
    assert payload["task_type"] == "sleep_consolidation"
    assert payload["request_reason"] == "week7-sleep-not-scheduled"
