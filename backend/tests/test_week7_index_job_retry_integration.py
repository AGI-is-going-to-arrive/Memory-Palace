from typing import Any, Dict

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import maintenance as maintenance_api


class _BaseIndexWorker:
    def __init__(self, *, jobs: Dict[str, Dict[str, Any]]) -> None:
        self._jobs = {job_id: dict(job) for job_id, job in jobs.items()}

    async def status(self) -> Dict[str, Any]:
        return {"enabled": True, "queue_depth": 0}

    async def get_job(self, *, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": f"job '{job_id}' not found."}
        return {"ok": True, "job": dict(job)}

    async def enqueue_reindex_memory(self, *, memory_id: int, reason: str = "api") -> Dict[str, Any]:
        _ = memory_id
        _ = reason
        raise AssertionError("enqueue_reindex_memory should not be called in this test setup")

    async def enqueue_rebuild(self, *, reason: str = "api") -> Dict[str, Any]:
        _ = reason
        raise AssertionError("enqueue_rebuild should not be called in this test setup")


class _RetrySuccessWorker(_BaseIndexWorker):
    def __init__(self) -> None:
        super().__init__(
            jobs={
                "idx-failed-reindex": {
                    "job_id": "idx-failed-reindex",
                    "task_type": "reindex_memory",
                    "status": "failed",
                    "memory_id": 42,
                }
            }
        )
        self.reindex_enqueue_calls: list[tuple[int, str]] = []

    async def enqueue_reindex_memory(self, *, memory_id: int, reason: str = "api") -> Dict[str, Any]:
        self.reindex_enqueue_calls.append((memory_id, reason))
        return {
            "queued": True,
            "dropped": False,
            "job_id": "idx-retry-queued",
            "memory_id": memory_id,
            "reason": reason,
        }


class _QueueFullRetryWorker(_BaseIndexWorker):
    def __init__(self) -> None:
        super().__init__(
            jobs={
                "idx-failed-rebuild": {
                    "job_id": "idx-failed-rebuild",
                    "task_type": "rebuild_index",
                    "status": "failed",
                }
            }
        )

    async def enqueue_rebuild(self, *, reason: str = "api") -> Dict[str, Any]:
        _ = reason
        return {
            "queued": False,
            "dropped": True,
            "job_id": "idx-drop-rebuild",
            "reason": "queue_full",
        }


class _NotScheduledSleepCoordinator:
    async def schedule(
        self,
        *,
        index_worker: Any,
        force: bool = False,
        reason: str = "runtime",
    ) -> Dict[str, Any]:
        _ = index_worker
        _ = force
        _ = reason
        return {
            "scheduled": False,
            "queued": False,
            "reason": "sleep_disabled",
        }

    async def status(self) -> Dict[str, Any]:
        return {"enabled": True, "scheduled": False, "reason": "sleep_disabled"}


def _build_client(
    monkeypatch,
    *,
    index_worker: Any,
    sleep_consolidation: Any | None = None,
) -> TestClient:
    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_API_KEY_ALLOW_INSECURE_LOCAL", "true")
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(maintenance_api.runtime_state, "index_worker", index_worker)
    if sleep_consolidation is not None:
        monkeypatch.setattr(
            maintenance_api.runtime_state,
            "sleep_consolidation",
            sleep_consolidation,
        )

    app = FastAPI()
    app.include_router(maintenance_api.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def test_index_job_retry_endpoint_retries_failed_job(monkeypatch) -> None:
    worker = _RetrySuccessWorker()

    with _build_client(monkeypatch, index_worker=worker) as client:
        response = client.post(
            "/maintenance/index/job/idx-failed-reindex/retry",
            json={"reason": "retry-via-testclient"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["retry_of_job_id"] == "idx-failed-reindex"
    assert payload["task_type"] == "reindex_memory"
    assert payload["reason"] == "retry-via-testclient"
    assert payload["queued"] is True
    assert payload["job_id"] == "idx-retry-queued"
    assert payload["runtime_worker"]["enabled"] is True
    assert worker.reindex_enqueue_calls == [(42, "retry-via-testclient")]


def test_index_job_retry_endpoint_returns_404_when_job_not_found(monkeypatch) -> None:
    worker = _BaseIndexWorker(jobs={})

    with _build_client(monkeypatch, index_worker=worker) as client:
        response = client.post("/maintenance/index/job/idx-missing/retry")

    assert response.status_code == 404
    assert response.json().get("detail")


def test_index_job_retry_endpoint_returns_409_when_status_not_allowed(monkeypatch) -> None:
    worker = _BaseIndexWorker(
        jobs={
            "idx-running": {
                "job_id": "idx-running",
                "task_type": "reindex_memory",
                "status": "running",
                "memory_id": 7,
            }
        }
    )

    with _build_client(monkeypatch, index_worker=worker) as client:
        response = client.post("/maintenance/index/job/idx-running/retry")

    assert response.status_code == 409
    detail = response.json().get("detail") or {}
    assert detail["error"] == "job_retry_not_allowed"
    assert detail["reason"] == "status:running"
    assert detail["task_type"] == "reindex_memory"


def test_index_job_retry_endpoint_returns_503_when_queue_full(monkeypatch) -> None:
    worker = _QueueFullRetryWorker()

    with _build_client(monkeypatch, index_worker=worker) as client:
        response = client.post(
            "/maintenance/index/job/idx-failed-rebuild/retry",
            json={"reason": "retry-week7-queue-full"},
        )

    assert response.status_code == 503
    detail = response.json().get("detail") or {}
    assert detail["error"] == "index_job_enqueue_failed"
    assert detail["reason"] == "queue_full"
    assert detail["operation"] == "retry_rebuild_index"
    assert detail["job_id"] == "idx-drop-rebuild"


def test_index_job_retry_endpoint_returns_409_when_sleep_not_scheduled(monkeypatch) -> None:
    worker = _BaseIndexWorker(
        jobs={
            "idx-sleep-failed": {
                "job_id": "idx-sleep-failed",
                "task_type": "sleep_consolidation",
                "status": "failed",
            }
        }
    )
    sleep_consolidation = _NotScheduledSleepCoordinator()

    with _build_client(
        monkeypatch,
        index_worker=worker,
        sleep_consolidation=sleep_consolidation,
    ) as client:
        response = client.post("/maintenance/index/job/idx-sleep-failed/retry")

    assert response.status_code == 409
    detail = response.json().get("detail") or {}
    assert detail["error"] == "job_retry_not_scheduled"
    assert detail["reason"] == "sleep_disabled"
    assert detail["task_type"] == "sleep_consolidation"
