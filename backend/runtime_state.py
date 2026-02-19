"""
Runtime state helpers for Phase-2 memory behavior.

This module provides:
1) Write-lane coordination (session lane + global lane).
2) Session-first retrieval cache (ephemeral, process-local).
3) Flush tracking for threshold-based context compaction.
4) Background index worker queue for async reindex/rebuild tasks.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import os
import re
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Set


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_session_id(session_id: Optional[str]) -> str:
    value = (session_id or "").strip()
    return value if value else "default"


def _tokenize_query(query: str) -> List[str]:
    return [token for token in re.findall(r"[a-zA-Z0-9_]+", query.lower()) if token]


@dataclass
class SessionSearchHit:
    uri: str
    memory_id: Optional[int]
    snippet: str
    updated_at: str
    priority: Optional[int]
    source: str


class WriteLaneCoordinator:
    """
    Two-layer write coordination:
    - Session lane: serial writes within the same session.
    - Global lane: bounded write concurrency across all sessions.
    """

    def __init__(self) -> None:
        self._global_concurrency = _env_int(
            "RUNTIME_WRITE_GLOBAL_CONCURRENCY", 1, minimum=1
        )
        self._wait_warn_ms = _env_int("RUNTIME_WRITE_WAIT_WARN_MS", 2000, minimum=1)
        self._global_sem = asyncio.Semaphore(self._global_concurrency)
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._session_waiting: Dict[str, int] = {}
        self._global_waiting = 0
        self._global_active = 0
        self._guard = asyncio.Lock()

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    async def run_write(
        self,
        *,
        session_id: Optional[str],
        operation: str,
        task: Callable[[], Awaitable[Any]],
    ) -> Any:
        lane = _normalize_session_id(session_id)
        session_lock = await self._get_session_lock(lane)

        session_wait_start = time.monotonic()
        async with self._guard:
            self._session_waiting[lane] = self._session_waiting.get(lane, 0) + 1

        async with session_lock:
            waited_session_ms = int((time.monotonic() - session_wait_start) * 1000)
            async with self._guard:
                self._session_waiting[lane] = max(
                    0, self._session_waiting.get(lane, 1) - 1
                )
                self._global_waiting += 1

            global_wait_start = time.monotonic()
            await self._global_sem.acquire()
            waited_global_ms = int((time.monotonic() - global_wait_start) * 1000)
            async with self._guard:
                self._global_waiting = max(0, self._global_waiting - 1)
                self._global_active += 1

            try:
                # Keep this as a metric hook, even when no logger is attached yet.
                _ = operation
                _ = waited_session_ms
                _ = waited_global_ms
                return await task()
            finally:
                async with self._guard:
                    self._global_active = max(0, self._global_active - 1)
                self._global_sem.release()

    async def status(self) -> Dict[str, Any]:
        async with self._guard:
            busy_sessions = {
                session: waiting
                for session, waiting in self._session_waiting.items()
                if waiting > 0
            }
            max_session_wait_ms = max(busy_sessions.values(), default=0)
            return {
                "global_concurrency": self._global_concurrency,
                "global_active": self._global_active,
                "global_waiting": self._global_waiting,
                "session_waiting_count": sum(busy_sessions.values()),
                "session_waiting_sessions": len(busy_sessions),
                "max_session_waiting": max_session_wait_ms,
                "wait_warn_ms": self._wait_warn_ms,
            }


class SessionSearchCache:
    """Ephemeral per-session retrieval cache used by session-first search."""

    def __init__(self) -> None:
        self._max_hits_per_session = _env_int(
            "RUNTIME_SESSION_CACHE_MAX_HITS", 200, minimum=20
        )
        self._half_life_seconds = float(
            _env_int("RUNTIME_SESSION_CACHE_HALF_LIFE_SECONDS", 6 * 3600, minimum=60)
        )
        self._hits: Dict[str, Deque[SessionSearchHit]] = {}
        self._guard = asyncio.Lock()

    async def record_hit(
        self,
        *,
        session_id: Optional[str],
        uri: str,
        memory_id: Optional[int],
        snippet: str,
        priority: Optional[int] = None,
        source: str = "runtime",
        updated_at: Optional[str] = None,
    ) -> None:
        sid = _normalize_session_id(session_id)
        clean_snippet = (snippet or "").strip()
        if not uri or not clean_snippet:
            return
        hit = SessionSearchHit(
            uri=uri,
            memory_id=memory_id,
            snippet=clean_snippet,
            updated_at=updated_at or _utc_iso_now(),
            priority=priority,
            source=source,
        )
        async with self._guard:
            queue = self._hits.get(sid)
            if queue is None:
                queue = deque(maxlen=self._max_hits_per_session)
                self._hits[sid] = queue
            queue.append(hit)

    async def search(
        self, *, session_id: Optional[str], query: str, limit: int
    ) -> List[Dict[str, Any]]:
        sid = _normalize_session_id(session_id)
        terms = _tokenize_query(query)
        if not terms:
            return []
        async with self._guard:
            snapshot = list(self._hits.get(sid, ()))
        if not snapshot:
            return []

        now = datetime.now(timezone.utc)
        by_uri: Dict[str, Dict[str, Any]] = {}

        for item in snapshot:
            text = item.snippet.lower()
            hits = sum(1 for term in terms if term in text)
            if hits <= 0:
                continue

            text_score = min(1.0, hits / max(1, len(terms)))
            updated_dt = None
            try:
                updated_dt = datetime.fromisoformat(item.updated_at.replace("Z", "+00:00"))
            except ValueError:
                updated_dt = now

            age_seconds = max(0.0, (now - updated_dt).total_seconds())
            recency_score = math.exp(-age_seconds / self._half_life_seconds)
            priority_value = item.priority if isinstance(item.priority, int) else 0
            priority_score = 1.0 / (1.0 + max(0, priority_value))
            final_score = (0.70 * text_score) + (0.20 * recency_score) + (0.10 * priority_score)

            prev = by_uri.get(item.uri)
            candidate = {
                "uri": item.uri,
                "memory_id": item.memory_id,
                "snippet": item.snippet[:300],
                "priority": priority_value,
                "score": round(final_score, 6),
                "keyword_score": round(text_score, 6),
                "semantic_score": 0.0,
                "updated_at": item.updated_at,
                "source": item.source,
                "match_type": "session_queue",
            }
            if prev is None or candidate["score"] > prev["score"]:
                by_uri[item.uri] = candidate

        ranked = sorted(by_uri.values(), key=lambda row: row["score"], reverse=True)
        return ranked[: max(1, limit)]


class SessionFlushTracker:
    """Tracks session activity and produces compact flush summaries."""

    def __init__(self) -> None:
        self._trigger_chars = _env_int("RUNTIME_FLUSH_TRIGGER_CHARS", 6000, minimum=800)
        self._min_events = _env_int("RUNTIME_FLUSH_MIN_EVENTS", 6, minimum=1)
        self._max_events = _env_int("RUNTIME_FLUSH_MAX_EVENTS", 80, minimum=10)
        self._events: Dict[str, Deque[str]] = {}
        self._guard = asyncio.Lock()

    async def record_event(self, *, session_id: Optional[str], message: str) -> None:
        text = (message or "").strip()
        if not text:
            return
        sid = _normalize_session_id(session_id)
        async with self._guard:
            queue = self._events.get(sid)
            if queue is None:
                queue = deque(maxlen=self._max_events)
                self._events[sid] = queue
            queue.append(text[:400])

    async def should_flush(self, *, session_id: Optional[str]) -> bool:
        sid = _normalize_session_id(session_id)
        async with self._guard:
            queue = self._events.get(sid)
            if not queue:
                return False
            total_chars = sum(len(item) for item in queue)
            return len(queue) >= self._min_events and total_chars >= self._trigger_chars

    async def build_summary(self, *, session_id: Optional[str], limit: int = 12) -> str:
        sid = _normalize_session_id(session_id)
        async with self._guard:
            queue = list(self._events.get(sid, ()))
        if not queue:
            return ""
        tail = queue[-max(1, limit) :]
        lines = [f"- {line}" for line in tail]
        return "Session compaction notes:\n" + "\n".join(lines)

    async def mark_flushed(self, *, session_id: Optional[str]) -> None:
        sid = _normalize_session_id(session_id)
        async with self._guard:
            self._events.pop(sid, None)


@dataclass
class GuardDecisionEvent:
    timestamp: str
    operation: str
    action: str
    method: str
    reason: str
    target_id: Optional[int]
    blocked: bool
    degraded: bool
    degrade_reasons: List[str]


class GuardDecisionTracker:
    """In-process observability tracker for write_guard decisions."""

    def __init__(self) -> None:
        self._max_events = _env_int("RUNTIME_GUARD_EVENT_LIMIT", 300, minimum=50)
        self._events: Deque[GuardDecisionEvent] = deque(maxlen=self._max_events)
        self._guard = asyncio.Lock()

    async def record_event(
        self,
        *,
        operation: str,
        action: str,
        method: str,
        reason: str,
        target_id: Optional[int] = None,
        blocked: bool = False,
        degraded: bool = False,
        degrade_reasons: Optional[List[str]] = None,
    ) -> None:
        event = GuardDecisionEvent(
            timestamp=_utc_iso_now(),
            operation=(operation or "unknown").strip() or "unknown",
            action=(action or "unknown").strip().upper() or "UNKNOWN",
            method=(method or "unknown").strip().lower() or "unknown",
            reason=(reason or "").strip(),
            target_id=target_id if isinstance(target_id, int) else None,
            blocked=bool(blocked),
            degraded=bool(degraded),
            degrade_reasons=[
                item
                for item in (degrade_reasons or [])
                if isinstance(item, str) and item.strip()
            ],
        )
        async with self._guard:
            self._events.append(event)

    async def summary(self) -> Dict[str, Any]:
        async with self._guard:
            snapshot = list(self._events)

        if not snapshot:
            return {
                "window_size": self._max_events,
                "total_events": 0,
                "blocked_events": 0,
                "degraded_events": 0,
                "action_breakdown": {},
                "method_breakdown": {},
                "operation_breakdown": {},
                "top_reasons": [],
                "last_event_at": None,
            }

        action_counter = Counter(item.action for item in snapshot)
        method_counter = Counter(item.method for item in snapshot)
        operation_counter = Counter(item.operation for item in snapshot)
        reason_counter = Counter(item.reason for item in snapshot if item.reason)
        blocked_events = sum(1 for item in snapshot if item.blocked)
        degraded_events = sum(1 for item in snapshot if item.degraded)

        return {
            "window_size": self._max_events,
            "total_events": len(snapshot),
            "blocked_events": blocked_events,
            "degraded_events": degraded_events,
            "action_breakdown": dict(action_counter),
            "method_breakdown": dict(method_counter),
            "operation_breakdown": dict(operation_counter),
            "top_reasons": [
                {"reason": reason, "count": count}
                for reason, count in reason_counter.most_common(5)
            ],
            "last_event_at": snapshot[-1].timestamp,
        }


@dataclass
class CleanupReviewRecord:
    review_id: str
    token: str
    confirmation_phrase: str
    action: str
    reviewer: str
    selections: List[Dict[str, Any]]
    created_at: float
    expires_at: float


class CleanupReviewCoordinator:
    """Ephemeral confirmation flow for risky cleanup actions."""

    def __init__(self) -> None:
        self._default_ttl_seconds = _env_int(
            "RUNTIME_CLEANUP_REVIEW_TTL_SECONDS", 900, minimum=60
        )
        self._max_pending = _env_int("RUNTIME_CLEANUP_REVIEW_MAX_PENDING", 64, minimum=8)
        self._records: Dict[str, CleanupReviewRecord] = {}
        self._guard = asyncio.Lock()

    def _cleanup_expired_locked(self, now_ts: float) -> None:
        expired = [
            key for key, record in self._records.items() if record.expires_at <= now_ts
        ]
        for key in expired:
            self._records.pop(key, None)

    async def create_review(
        self,
        *,
        action: str,
        selections: List[Dict[str, Any]],
        reviewer: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_action = (action or "delete").strip().lower() or "delete"
        normalized_reviewer = (reviewer or "").strip() or "human"
        normalized_ttl = (
            self._default_ttl_seconds
            if ttl_seconds is None
            else max(60, int(ttl_seconds))
        )
        now_ts = time.time()
        expires_at = now_ts + float(normalized_ttl)
        review_id = f"cleanup-{uuid.uuid4().hex[:10]}"
        token = uuid.uuid4().hex
        confirmation_phrase = f"CONFIRM {normalized_action.upper()} {len(selections)}"
        record = CleanupReviewRecord(
            review_id=review_id,
            token=token,
            confirmation_phrase=confirmation_phrase,
            action=normalized_action,
            reviewer=normalized_reviewer,
            selections=selections,
            created_at=now_ts,
            expires_at=expires_at,
        )

        async with self._guard:
            self._cleanup_expired_locked(now_ts)
            while len(self._records) >= self._max_pending:
                oldest_key = min(
                    self._records.items(), key=lambda item: item[1].created_at
                )[0]
                self._records.pop(oldest_key, None)
            self._records[review_id] = record

        return {
            "review_id": review_id,
            "token": token,
            "confirmation_phrase": confirmation_phrase,
            "action": normalized_action,
            "reviewer": normalized_reviewer,
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }

    async def consume_review(
        self,
        *,
        review_id: str,
        token: str,
        confirmation_phrase: str,
    ) -> Dict[str, Any]:
        review_id_value = (review_id or "").strip()
        token_value = (token or "").strip()
        phrase_value = (confirmation_phrase or "").strip()
        if not review_id_value:
            return {"ok": False, "error": "review_id is required"}
        if not token_value:
            return {"ok": False, "error": "token is required"}
        if not phrase_value:
            return {"ok": False, "error": "confirmation_phrase is required"}

        now_ts = time.time()
        async with self._guard:
            self._cleanup_expired_locked(now_ts)
            record = self._records.get(review_id_value)
            if record is None:
                return {"ok": False, "error": "review_not_found_or_expired"}
            if record.token != token_value:
                return {"ok": False, "error": "invalid_review_token"}
            if record.confirmation_phrase != phrase_value:
                return {"ok": False, "error": "confirmation_phrase_mismatch"}
            self._records.pop(review_id_value, None)

        return {
            "ok": True,
            "review": {
                "review_id": record.review_id,
                "action": record.action,
                "reviewer": record.reviewer,
                "selections": list(record.selections),
                "created_at": datetime.fromtimestamp(record.created_at, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "expires_at": datetime.fromtimestamp(record.expires_at, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            },
        }

    async def summary(self) -> Dict[str, Any]:
        now_ts = time.time()
        async with self._guard:
            self._cleanup_expired_locked(now_ts)
            pending = list(self._records.values())
        return {
            "pending_reviews": len(pending),
            "default_ttl_seconds": self._default_ttl_seconds,
            "max_pending": self._max_pending,
        }


class VitalityDecayCoordinator:
    """Single-flight wrapper around daily vitality decay."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._check_interval_seconds = _env_int(
            "RUNTIME_VITALITY_DECAY_CHECK_INTERVAL_SECONDS", 600, minimum=10
        )
        self._last_check_ts = 0.0
        self._last_result: Dict[str, Any] = {
            "applied": False,
            "reason": "not_started",
        }

    async def run_decay(
        self,
        *,
        client_factory: Callable[[], Any],
        force: bool = False,
        reason: str = "runtime",
    ) -> Dict[str, Any]:
        async with self._guard:
            now_ts = time.time()
            if (
                not force
                and self._last_check_ts > 0
                and (now_ts - self._last_check_ts) < self._check_interval_seconds
            ):
                return dict(self._last_result)

            if not callable(client_factory):
                self._last_result = {
                    "applied": False,
                    "degraded": True,
                    "reason": "client_factory_unavailable",
                }
                self._last_check_ts = now_ts
                return dict(self._last_result)

            client = client_factory()
            decay_method = getattr(client, "apply_vitality_decay", None)
            if not callable(decay_method):
                self._last_result = {
                    "applied": False,
                    "degraded": True,
                    "reason": "apply_vitality_decay_unavailable",
                }
                self._last_check_ts = now_ts
                return dict(self._last_result)

            try:
                payload = decay_method(force=bool(force), reason=(reason or "runtime"))
                if inspect.isawaitable(payload):
                    payload = await payload
                if not isinstance(payload, dict):
                    payload = {"applied": False, "raw": payload}
                payload.setdefault("degraded", False)
            except Exception as exc:
                payload = {
                    "applied": False,
                    "degraded": True,
                    "reason": str(exc),
                }

            self._last_result = payload
            self._last_check_ts = now_ts
            return dict(payload)

    async def status(self) -> Dict[str, Any]:
        async with self._guard:
            return {
                **dict(self._last_result),
                "check_interval_seconds": self._check_interval_seconds,
            }


@dataclass
class IndexTask:
    job_id: str
    task_type: str
    memory_id: Optional[int]
    reason: str
    requested_at: str


class IndexTaskWorker:
    """Background worker that executes reindex/rebuild tasks serially."""

    _FINAL_STATES: Set[str] = {"succeeded", "failed", "dropped", "cancelled"}

    def __init__(self) -> None:
        self._enabled = _env_bool("RUNTIME_INDEX_WORKER_ENABLED", True)
        self._queue_maxsize = _env_int("RUNTIME_INDEX_QUEUE_MAXSIZE", 256, minimum=8)
        self._recent_limit = _env_int("RUNTIME_INDEX_RECENT_JOBS", 30, minimum=5)

        self._queue: asyncio.Queue[IndexTask] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._client_factory: Optional[Callable[[], Any]] = None
        self._runner: Optional[asyncio.Task] = None
        self._guard = asyncio.Lock()

        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._job_events: Dict[str, asyncio.Event] = {}
        self._recent_job_ids: Deque[str] = deque()
        self._pending_memory_jobs: Dict[int, str] = {}
        self._rebuild_job_id: Optional[str] = None
        self._sleep_job_id: Optional[str] = None

        self._enqueued_total = 0
        self._succeeded_total = 0
        self._failed_total = 0
        self._dropped_total = 0
        self._cancelled_total = 0
        self._active_job_id: Optional[str] = None
        self._active_execution_task: Optional[asyncio.Task[Any]] = None
        self._last_error: Optional[str] = None
        self._last_finished_at: Optional[str] = None
        self._cancelled_job_ids: Set[str] = set()

    async def ensure_started(self, client_factory: Callable[[], Any]) -> None:
        if not self._enabled:
            return
        async with self._guard:
            self._client_factory = client_factory
            if self._runner is None or self._runner.done():
                self._runner = asyncio.create_task(
                    self._run_loop(), name="runtime-index-worker"
                )

    async def shutdown(self) -> None:
        runner: Optional[asyncio.Task] = None
        async with self._guard:
            runner = self._runner
            self._runner = None
        if runner is None:
            return
        runner.cancel()
        try:
            await runner
        except asyncio.CancelledError:
            pass

    async def enqueue_reindex_memory(
        self,
        *,
        memory_id: int,
        reason: str = "write",
    ) -> Dict[str, Any]:
        if memory_id <= 0:
            raise ValueError("memory_id must be a positive integer.")
        if not self._enabled:
            return {"queued": False, "reason": "index_worker_disabled"}

        requested_at = _utc_iso_now()
        async with self._guard:
            existing_job_id = self._pending_memory_jobs.get(memory_id)
            if existing_job_id:
                return {
                    "queued": False,
                    "deduped": True,
                    "job_id": existing_job_id,
                    "memory_id": memory_id,
                }

            job_id = f"idx-{uuid.uuid4().hex[:10]}"
            task = IndexTask(
                job_id=job_id,
                task_type="reindex_memory",
                memory_id=memory_id,
                reason=reason or "write",
                requested_at=requested_at,
            )
            event = asyncio.Event()
            self._job_events[job_id] = event
            self._jobs[job_id] = {
                "job_id": job_id,
                "task_type": task.task_type,
                "memory_id": memory_id,
                "reason": task.reason,
                "requested_at": requested_at,
                "status": "queued",
            }
            self._pending_memory_jobs[memory_id] = job_id

            try:
                self._queue.put_nowait(task)
            except asyncio.QueueFull:
                self._jobs[job_id]["status"] = "dropped"
                self._jobs[job_id]["error"] = "queue_full"
                self._jobs[job_id]["finished_at"] = _utc_iso_now()
                self._dropped_total += 1
                self._pending_memory_jobs.pop(memory_id, None)
                event.set()
                self._append_recent_job_locked(job_id)
                return {
                    "queued": False,
                    "dropped": True,
                    "job_id": job_id,
                    "memory_id": memory_id,
                    "reason": "queue_full",
                }

            self._enqueued_total += 1
            return {
                "queued": True,
                "job_id": job_id,
                "memory_id": memory_id,
            }

    async def enqueue_rebuild(self, *, reason: str = "manual") -> Dict[str, Any]:
        if not self._enabled:
            return {"queued": False, "reason": "index_worker_disabled"}

        requested_at = _utc_iso_now()
        async with self._guard:
            if self._rebuild_job_id:
                return {
                    "queued": False,
                    "deduped": True,
                    "job_id": self._rebuild_job_id,
                }

            job_id = f"idx-{uuid.uuid4().hex[:10]}"
            task = IndexTask(
                job_id=job_id,
                task_type="rebuild_index",
                memory_id=None,
                reason=reason or "manual",
                requested_at=requested_at,
            )
            event = asyncio.Event()
            self._job_events[job_id] = event
            self._jobs[job_id] = {
                "job_id": job_id,
                "task_type": task.task_type,
                "reason": task.reason,
                "requested_at": requested_at,
                "status": "queued",
            }
            self._rebuild_job_id = job_id

            try:
                self._queue.put_nowait(task)
            except asyncio.QueueFull:
                self._jobs[job_id]["status"] = "dropped"
                self._jobs[job_id]["error"] = "queue_full"
                self._jobs[job_id]["finished_at"] = _utc_iso_now()
                self._dropped_total += 1
                self._rebuild_job_id = None
                event.set()
                self._append_recent_job_locked(job_id)
                return {
                    "queued": False,
                    "dropped": True,
                    "job_id": job_id,
                    "reason": "queue_full",
                }

            self._enqueued_total += 1
            return {"queued": True, "job_id": job_id}

    async def enqueue_sleep_consolidation(
        self,
        *,
        reason: str = "sleep_cycle",
    ) -> Dict[str, Any]:
        if not self._enabled:
            return {"queued": False, "reason": "index_worker_disabled"}

        requested_at = _utc_iso_now()
        async with self._guard:
            if self._sleep_job_id:
                return {
                    "queued": False,
                    "deduped": True,
                    "job_id": self._sleep_job_id,
                }

            job_id = f"idx-{uuid.uuid4().hex[:10]}"
            task = IndexTask(
                job_id=job_id,
                task_type="sleep_consolidation",
                memory_id=None,
                reason=reason or "sleep_cycle",
                requested_at=requested_at,
            )
            event = asyncio.Event()
            self._job_events[job_id] = event
            self._jobs[job_id] = {
                "job_id": job_id,
                "task_type": task.task_type,
                "reason": task.reason,
                "requested_at": requested_at,
                "status": "queued",
            }
            self._sleep_job_id = job_id

            try:
                self._queue.put_nowait(task)
            except asyncio.QueueFull:
                self._jobs[job_id]["status"] = "dropped"
                self._jobs[job_id]["error"] = "queue_full"
                self._jobs[job_id]["finished_at"] = _utc_iso_now()
                self._dropped_total += 1
                self._sleep_job_id = None
                event.set()
                self._append_recent_job_locked(job_id)
                return {
                    "queued": False,
                    "dropped": True,
                    "job_id": job_id,
                    "reason": "queue_full",
                }

            self._enqueued_total += 1
            return {"queued": True, "job_id": job_id}

    async def wait_for_job(
        self, *, job_id: str, timeout_seconds: float = 10.0
    ) -> Dict[str, Any]:
        if not job_id:
            return {"ok": False, "error": "job_id is required."}
        async with self._guard:
            job = dict(self._jobs.get(job_id, {}))
            event = self._job_events.get(job_id)
        if not job:
            return {"ok": False, "error": f"job '{job_id}' not found."}
        if job.get("status") in self._FINAL_STATES or event is None:
            return {"ok": True, "job": job}
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.1, float(timeout_seconds)))
        except asyncio.TimeoutError:
            pass
        async with self._guard:
            current = dict(self._jobs.get(job_id, {}))
        return {"ok": True, "job": current}

    async def get_job(self, *, job_id: str) -> Dict[str, Any]:
        async with self._guard:
            job = self._jobs.get(job_id)
            if not job:
                return {"ok": False, "error": f"job '{job_id}' not found."}
            return {"ok": True, "job": dict(job)}

    async def cancel_job(
        self,
        *,
        job_id: str,
        reason: str = "manual_cancel",
    ) -> Dict[str, Any]:
        normalized_job_id = (job_id or "").strip()
        if not normalized_job_id:
            return {"ok": False, "error": "job_id is required."}

        cancel_reason = (reason or "").strip() or "manual_cancel"
        cancellation_ts = _utc_iso_now()
        execution_task: Optional[asyncio.Task[Any]] = None

        async with self._guard:
            job = self._jobs.get(normalized_job_id)
            if not job:
                return {"ok": False, "error": f"job '{normalized_job_id}' not found."}

            status = str(job.get("status") or "")
            if status in self._FINAL_STATES:
                return {
                    "ok": False,
                    "error": "job_already_finalized",
                    "job": dict(job),
                }

            if status == "queued":
                job["status"] = "cancelled"
                job["cancel_reason"] = cancel_reason
                job["cancelled_at"] = cancellation_ts
                job["finished_at"] = cancellation_ts
                self._cancelled_total += 1
                self._cancelled_job_ids.add(normalized_job_id)
                if job.get("task_type") == "rebuild_index":
                    if self._rebuild_job_id == normalized_job_id:
                        self._rebuild_job_id = None
                elif job.get("task_type") == "sleep_consolidation":
                    if self._sleep_job_id == normalized_job_id:
                        self._sleep_job_id = None
                else:
                    memory_id = job.get("memory_id")
                    if isinstance(memory_id, int):
                        self._pending_memory_jobs.pop(memory_id, None)
                event = self._job_events.get(normalized_job_id)
                if event is not None:
                    event.set()
                self._append_recent_job_locked(normalized_job_id)
                self._last_finished_at = cancellation_ts
                return {"ok": True, "cancelled": True, "job": dict(job)}

            if status in {"running", "cancelling"}:
                if status != "cancelling":
                    job["status"] = "cancelling"
                    job["cancel_requested"] = True
                    job["cancel_reason"] = cancel_reason
                    job["cancel_requested_at"] = cancellation_ts
                execution_task = (
                    self._active_execution_task
                    if self._active_job_id == normalized_job_id
                    else None
                )
                if execution_task is None:
                    return {
                        "ok": False,
                        "error": "running_job_handle_unavailable",
                        "job": dict(job),
                    }
                snapshot = dict(job)
            else:
                return {
                    "ok": False,
                    "error": f"job_not_cancellable_from_status:{status or 'unknown'}",
                    "job": dict(job),
                }

        execution_task.cancel()
        return {"ok": True, "cancel_requested": True, "job": snapshot}

    async def status(self) -> Dict[str, Any]:
        async with self._guard:
            recent_jobs = [
                dict(self._jobs[job_id])
                for job_id in self._recent_job_ids
                if job_id in self._jobs
            ]
            cancelling_count = sum(
                1 for item in self._jobs.values() if item.get("status") == "cancelling"
            )
            return {
                "enabled": self._enabled,
                "running": self._runner is not None and not self._runner.done(),
                "queue_depth": self._queue.qsize(),
                "queue_maxsize": self._queue_maxsize,
                "active_job_id": self._active_job_id,
                "cancelling_jobs": cancelling_count,
                "pending_memory_jobs": len(self._pending_memory_jobs),
                "rebuild_pending": self._rebuild_job_id is not None,
                "sleep_pending": self._sleep_job_id is not None,
                "stats": {
                    "enqueued": self._enqueued_total,
                    "succeeded": self._succeeded_total,
                    "failed": self._failed_total,
                    "dropped": self._dropped_total,
                    "cancelled": self._cancelled_total,
                },
                "last_error": self._last_error,
                "last_finished_at": self._last_finished_at,
                "recent_jobs": recent_jobs,
            }

    async def _run_loop(self) -> None:
        while True:
            task = await self._queue.get()
            should_skip = False
            async with self._guard:
                if task.job_id in self._cancelled_job_ids:
                    self._cancelled_job_ids.discard(task.job_id)
                    should_skip = True
            if should_skip:
                self._queue.task_done()
                continue

            await self._mark_running(task)
            execution_task = asyncio.create_task(
                self._execute_task(task), name=f"runtime-index-job-{task.job_id}"
            )
            async with self._guard:
                self._active_execution_task = execution_task
            try:
                payload = await execution_task
            except asyncio.CancelledError:
                async with self._guard:
                    record = self._jobs.get(task.job_id) or {}
                    is_job_cancelling = record.get("status") == "cancelling"
                if is_job_cancelling:
                    await self._mark_finished(task, status="cancelled", error="job_cancelled")
                    continue
                await self._mark_finished(task, status="failed", error="worker_cancelled")
                raise
            except Exception as exc:
                await self._mark_finished(task, status="failed", error=str(exc))
            else:
                await self._mark_finished(task, status="succeeded", result=payload)
            finally:
                async with self._guard:
                    if self._active_execution_task is execution_task:
                        self._active_execution_task = None
                self._queue.task_done()

    async def _execute_task(self, task: IndexTask) -> Dict[str, Any]:
        async with self._guard:
            factory = self._client_factory
        if not callable(factory):
            raise RuntimeError("index worker is not initialized with sqlite client factory.")
        client = factory()

        if task.task_type == "reindex_memory":
            result = client.reindex_memory(
                memory_id=int(task.memory_id or 0),
                reason=task.reason,
            )
        elif task.task_type == "rebuild_index":
            result = client.rebuild_index(reason=task.reason)
        elif task.task_type == "sleep_consolidation":
            result = self._run_sleep_consolidation(client=client, reason=task.reason)
        else:
            raise ValueError(f"Unknown index task type '{task.task_type}'.")

        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            return result
        return {"result": result}

    @staticmethod
    def _normalize_sleep_dedup_content(content: Any) -> str:
        if not isinstance(content, str):
            return ""
        return re.sub(r"\s+", " ", content).strip().lower()

    @staticmethod
    def _split_uri(uri: Any) -> Optional[tuple[str, str]]:
        if not isinstance(uri, str) or "://" not in uri:
            return None
        domain, path = uri.split("://", 1)
        domain_value = domain.strip()
        path_value = path.strip().strip("/")
        if not domain_value or not path_value:
            return None
        return domain_value, path_value

    @staticmethod
    def _parent_path(path: str) -> str:
        if "/" not in path:
            return path
        return path.rsplit("/", 1)[0]

    @staticmethod
    def _build_sleep_fragment_gist(
        *, domain: str, parent_path: str, snippets: List[str]
    ) -> str:
        lines = [f"Sleep consolidation rollup for {domain}://{parent_path}"]
        for snippet in snippets:
            text = re.sub(r"\s+", " ", snippet).strip()
            if text:
                lines.append(f"- {text[:180]}")
            if len(lines) >= 7:
                break
        return "\n".join(lines)

    @staticmethod
    def _iso_to_timestamp(value: Any) -> float:
        if not isinstance(value, str):
            return 0.0
        candidate = value.strip()
        if not candidate:
            return 0.0
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return float(datetime.fromisoformat(candidate).timestamp())
        except ValueError:
            return 0.0

    async def _run_sleep_consolidation(
        self,
        *,
        client: Any,
        reason: str,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": True,
            "task": "sleep_consolidation",
            "reason": reason or "sleep_cycle",
            "started_at": _utc_iso_now(),
            "degraded": False,
            "degrade_reasons": [],
        }
        dedup_apply_enabled = _env_bool("RUNTIME_SLEEP_DEDUP_APPLY", False)
        fragment_rollup_apply_enabled = _env_bool(
            "RUNTIME_SLEEP_FRAGMENT_ROLLUP_APPLY", False
        )
        payload["policy"] = {
            "dedup_apply_enabled": dedup_apply_enabled,
            "fragment_rollup_apply_enabled": fragment_rollup_apply_enabled,
        }

        orphans_summary: Dict[str, Any] = {
            "scanned": 0,
            "deprecated": 0,
            "orphaned": 0,
        }
        orphan_items: List[Dict[str, Any]] = []
        get_orphans = getattr(client, "get_all_orphan_memories", None)
        if callable(get_orphans):
            try:
                orphans_raw = get_orphans()
                if inspect.isawaitable(orphans_raw):
                    orphans_raw = await orphans_raw
                if isinstance(orphans_raw, list):
                    orphan_items = [item for item in orphans_raw if isinstance(item, dict)]
                    orphans_summary["scanned"] = len(orphan_items)
                    for item in orphan_items:
                        if not isinstance(item, dict):
                            continue
                        category = str(item.get("category") or "").strip().lower()
                        if category == "deprecated":
                            orphans_summary["deprecated"] += 1
                        elif category == "orphaned":
                            orphans_summary["orphaned"] += 1
                else:
                    payload["degraded"] = True
                    payload["degrade_reasons"].append("sleep_orphans_invalid_payload")
            except Exception:
                payload["degraded"] = True
                payload["degrade_reasons"].append("sleep_orphans_scan_failed")
        else:
            payload["degraded"] = True
            payload["degrade_reasons"].append("sleep_orphans_scan_unavailable")

        dedup_summary: Dict[str, Any] = {
            "scanned_orphans": 0,
            "duplicate_groups": 0,
            "deleted_duplicates": 0,
            "kept_memory_ids": [],
            "deleted_memory_ids": [],
            "preview_duplicates": [],
            "preview_only": not dedup_apply_enabled,
            "errors": [],
        }
        get_memory_version = getattr(client, "get_memory_version", None)
        delete_memory = getattr(client, "permanently_delete_memory", None)
        if orphan_items:
            if callable(get_memory_version):
                groups_by_hash: Dict[str, List[Dict[str, Any]]] = {}
                for item in orphan_items:
                    try:
                        memory_id = int(item.get("id") or 0)
                    except (TypeError, ValueError):
                        continue
                    if memory_id <= 0:
                        continue
                    try:
                        version_raw = get_memory_version(memory_id)
                        if inspect.isawaitable(version_raw):
                            version_raw = await version_raw
                    except Exception as exc:
                        dedup_summary["errors"].append(
                            {"memory_id": memory_id, "error": str(exc)}
                        )
                        continue
                    if not isinstance(version_raw, dict):
                        continue
                    normalized_content = self._normalize_sleep_dedup_content(
                        version_raw.get("content")
                    )
                    if not normalized_content:
                        continue
                    content_hash = hashlib.sha256(
                        normalized_content.encode("utf-8")
                    ).hexdigest()
                    groups_by_hash.setdefault(content_hash, []).append(
                        {
                            "memory_id": memory_id,
                            "deprecated": bool(version_raw.get("deprecated")),
                            "created_at_ts": self._iso_to_timestamp(
                                version_raw.get("created_at")
                            ),
                        }
                    )
                    dedup_summary["scanned_orphans"] += 1

                for content_hash in sorted(groups_by_hash.keys()):
                    records = groups_by_hash[content_hash]
                    deduped_records: List[Dict[str, Any]] = []
                    seen_ids: Set[int] = set()
                    for record in records:
                        memory_id = int(record.get("memory_id") or 0)
                        if memory_id <= 0 or memory_id in seen_ids:
                            continue
                        seen_ids.add(memory_id)
                        deduped_records.append(record)
                    if len(deduped_records) < 2:
                        continue
                    dedup_summary["duplicate_groups"] += 1
                    ordered = sorted(
                        deduped_records,
                        key=lambda record: (
                            1 if bool(record.get("deprecated")) else 0,
                            -float(record.get("created_at_ts") or 0.0),
                            int(record.get("memory_id") or 0),
                        ),
                    )
                    keep_id = int(ordered[0].get("memory_id") or 0)
                    if keep_id <= 0:
                        continue
                    dedup_summary["kept_memory_ids"].append(keep_id)
                    duplicate_ids = [
                        int(record.get("memory_id") or 0) for record in ordered[1:]
                    ]
                    duplicate_ids = [memory_id for memory_id in duplicate_ids if memory_id > 0]
                    dedup_summary["preview_duplicates"].append(
                        {"keep": keep_id, "duplicates": duplicate_ids}
                    )
                    if not dedup_apply_enabled:
                        continue
                    if not callable(delete_memory):
                        payload["degraded"] = True
                        payload["degrade_reasons"].append("sleep_orphan_dedup_unavailable")
                        continue

                    for duplicate_id in duplicate_ids:
                        try:
                            delete_raw = delete_memory(duplicate_id, require_orphan=True)
                            if inspect.isawaitable(delete_raw):
                                await delete_raw
                            dedup_summary["deleted_duplicates"] += 1
                            dedup_summary["deleted_memory_ids"].append(duplicate_id)
                        except Exception as exc:
                            dedup_summary["errors"].append(
                                {"memory_id": duplicate_id, "error": str(exc)}
                            )

                if dedup_summary["errors"]:
                    payload["degraded"] = True
                    payload["degrade_reasons"].append(
                        "sleep_orphan_dedup_partial_failure"
                    )
            else:
                if dedup_apply_enabled:
                    payload["degraded"] = True
                    payload["degrade_reasons"].append("sleep_orphan_dedup_unavailable")
                dedup_summary["errors"].append({"error": "memory_version_reader_unavailable"})

        cleanup_preview: Dict[str, Any] = {"candidate_count": 0}
        get_candidates = getattr(client, "get_vitality_cleanup_candidates", None)
        if callable(get_candidates):
            try:
                cleanup_raw = get_candidates(limit=50)
                if inspect.isawaitable(cleanup_raw):
                    cleanup_raw = await cleanup_raw
                if isinstance(cleanup_raw, dict):
                    items = cleanup_raw.get("items")
                    if isinstance(items, list):
                        cleanup_preview["candidate_count"] = len(items)
                    elif isinstance(cleanup_raw.get("count"), int):
                        cleanup_preview["candidate_count"] = int(cleanup_raw["count"])
                    else:
                        payload["degraded"] = True
                        payload["degrade_reasons"].append(
                            "sleep_cleanup_preview_invalid_payload"
                        )
                else:
                    payload["degraded"] = True
                    payload["degrade_reasons"].append(
                        "sleep_cleanup_preview_invalid_payload"
                    )
            except Exception:
                payload["degraded"] = True
                payload["degrade_reasons"].append("sleep_cleanup_preview_failed")
        else:
            payload["degraded"] = True
            payload["degrade_reasons"].append("sleep_cleanup_preview_unavailable")

        fragment_rollup: Dict[str, Any] = {
            "groups_scanned": 0,
            "preview_groups": 0,
            "groups_aggregated": 0,
            "gist_upserts": 0,
            "memory_coverage": 0,
            "skipped_existing_gist": 0,
            "preview_only": not fragment_rollup_apply_enabled,
            "errors": [],
        }
        get_recent_memories = getattr(client, "get_recent_memories", None)
        get_memory_by_id = getattr(client, "get_memory_by_id", None)
        upsert_memory_gist = getattr(client, "upsert_memory_gist", None)
        get_latest_memory_gist = getattr(client, "get_latest_memory_gist", None)
        if (
            callable(get_recent_memories)
            and callable(get_memory_by_id)
            and (
                not fragment_rollup_apply_enabled
                or callable(upsert_memory_gist)
            )
        ):
            try:
                recent_raw = get_recent_memories(limit=120)
                if inspect.isawaitable(recent_raw):
                    recent_raw = await recent_raw
                if isinstance(recent_raw, list):
                    groups: Dict[str, Dict[str, Any]] = {}
                    for item in recent_raw:
                        if not isinstance(item, dict):
                            continue
                        try:
                            memory_id = int(item.get("memory_id") or 0)
                        except (TypeError, ValueError):
                            continue
                        if memory_id <= 0:
                            continue
                        parts = self._split_uri(item.get("uri"))
                        if parts is None:
                            continue
                        domain, path = parts
                        parent_path = self._parent_path(path)
                        key = f"{domain}://{parent_path}"
                        group = groups.setdefault(
                            key,
                            {
                                "domain": domain,
                                "parent_path": parent_path,
                                "memory_ids": [],
                            },
                        )
                        if memory_id not in group["memory_ids"]:
                            group["memory_ids"].append(memory_id)

                    fragment_rollup["groups_scanned"] = len(groups)

                    for group_key in sorted(groups.keys()):
                        group = groups[group_key]
                        memory_ids = list(group.get("memory_ids") or [])
                        if len(memory_ids) < 3:
                            continue

                        snippets: List[str] = []
                        source_parts: List[str] = []
                        for memory_id in memory_ids[:6]:
                            try:
                                memory_raw = get_memory_by_id(int(memory_id))
                                if inspect.isawaitable(memory_raw):
                                    memory_raw = await memory_raw
                            except Exception as exc:
                                fragment_rollup["errors"].append(
                                    {
                                        "group": f"{group['domain']}://{group['parent_path']}",
                                        "memory_id": int(memory_id),
                                        "error": str(exc),
                                    }
                                )
                                continue
                            if not isinstance(memory_raw, dict):
                                continue
                            content = str(memory_raw.get("content") or "").strip()
                            if not content:
                                continue
                            snippet = re.sub(r"\s+", " ", content).strip()[:180]
                            snippets.append(snippet)
                            source_parts.append(f"{int(memory_id)}:{snippet}")

                        if len(snippets) < 3:
                            continue
                        fragment_rollup["preview_groups"] += 1
                        if not fragment_rollup_apply_enabled:
                            continue

                        gist_text = self._build_sleep_fragment_gist(
                            domain=str(group["domain"]),
                            parent_path=str(group["parent_path"]),
                            snippets=snippets,
                        )
                        source_hash = "sleep-rollup:" + hashlib.sha256(
                            "||".join(source_parts).encode("utf-8")
                        ).hexdigest()

                        try:
                            if callable(get_latest_memory_gist):
                                latest_raw = get_latest_memory_gist(int(memory_ids[0]))
                                if inspect.isawaitable(latest_raw):
                                    latest_raw = await latest_raw
                                if isinstance(latest_raw, dict):
                                    latest_method = str(
                                        latest_raw.get("gist_method") or ""
                                    ).strip()
                                    if (
                                        latest_method
                                        and latest_method != "sleep_fragment_rollup"
                                    ):
                                        fragment_rollup["skipped_existing_gist"] += 1
                                        continue
                            upsert_raw = upsert_memory_gist(
                                memory_id=int(memory_ids[0]),
                                gist_text=gist_text,
                                source_hash=source_hash,
                                gist_method="sleep_fragment_rollup",
                                quality_score=0.55,
                            )
                            if inspect.isawaitable(upsert_raw):
                                await upsert_raw
                            fragment_rollup["groups_aggregated"] += 1
                            fragment_rollup["gist_upserts"] += 1
                            fragment_rollup["memory_coverage"] += len(memory_ids)
                        except Exception as exc:
                            fragment_rollup["errors"].append(
                                {
                                    "group": f"{group['domain']}://{group['parent_path']}",
                                    "error": str(exc),
                                }
                            )
                else:
                    payload["degraded"] = True
                    payload["degrade_reasons"].append(
                        "sleep_fragment_rollup_invalid_payload"
                    )
            except Exception:
                payload["degraded"] = True
                payload["degrade_reasons"].append("sleep_fragment_rollup_failed")
        else:
            if fragment_rollup_apply_enabled:
                payload["degraded"] = True
                payload["degrade_reasons"].append("sleep_fragment_rollup_unavailable")
            else:
                fragment_rollup["errors"].append(
                    {"error": "recent_memory_reader_unavailable"}
                )

        if fragment_rollup["errors"]:
            payload["degraded"] = True
            payload["degrade_reasons"].append("sleep_fragment_rollup_partial_failure")

        rebuild = client.rebuild_index(reason=f"sleep_consolidation:{reason or 'sleep_cycle'}")
        if inspect.isawaitable(rebuild):
            rebuild = await rebuild

        payload["orphans"] = orphans_summary
        payload["dedup"] = dedup_summary
        payload["cleanup_preview"] = cleanup_preview
        payload["fragment_rollup"] = fragment_rollup
        payload["rebuild_result"] = rebuild
        payload["finished_at"] = _utc_iso_now()
        payload["degrade_reasons"] = list(dict.fromkeys(payload["degrade_reasons"]))
        return payload

    async def _mark_running(self, task: IndexTask) -> None:
        async with self._guard:
            record = self._jobs.get(task.job_id)
            if record is None:
                return
            record["status"] = "running"
            record["started_at"] = _utc_iso_now()
            self._active_job_id = task.job_id

    async def _mark_finished(
        self,
        task: IndexTask,
        *,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        finished_at = _utc_iso_now()
        async with self._guard:
            record = self._jobs.get(task.job_id)
            if record is None:
                return
            record["status"] = status
            record["finished_at"] = finished_at
            if result is not None:
                record["result"] = result
            if error:
                record["error"] = error
                if status == "failed":
                    self._last_error = error
            if task.memory_id is not None:
                self._pending_memory_jobs.pop(task.memory_id, None)
            if task.task_type == "rebuild_index" and self._rebuild_job_id == task.job_id:
                self._rebuild_job_id = None
            if (
                task.task_type == "sleep_consolidation"
                and self._sleep_job_id == task.job_id
            ):
                self._sleep_job_id = None
            if status == "succeeded":
                self._succeeded_total += 1
            elif status == "failed":
                self._failed_total += 1
            elif status == "dropped":
                self._dropped_total += 1
            elif status == "cancelled":
                self._cancelled_total += 1
            self._last_finished_at = finished_at
            if self._active_job_id == task.job_id:
                self._active_job_id = None

            event = self._job_events.get(task.job_id)
            if event is not None:
                event.set()
            self._append_recent_job_locked(task.job_id)

    def _append_recent_job_locked(self, job_id: str) -> None:
        if job_id in self._recent_job_ids:
            self._recent_job_ids.remove(job_id)
        self._recent_job_ids.appendleft(job_id)
        while len(self._recent_job_ids) > self._recent_limit:
            stale_id = self._recent_job_ids.pop()
            if stale_id in self._jobs:
                stale_status = self._jobs[stale_id].get("status")
                if stale_status in self._FINAL_STATES:
                    self._jobs.pop(stale_id, None)
                    self._job_events.pop(stale_id, None)


class SleepTimeConsolidator:
    """Periodic scheduler for low-risk sleep-time consolidation jobs."""

    _QUEUE_FULL_RETRY_SECONDS = 30.0

    def __init__(self) -> None:
        self._enabled = _env_bool("RUNTIME_SLEEP_CONSOLIDATION_ENABLED", True)
        self._check_interval_seconds = _env_int(
            "RUNTIME_SLEEP_CONSOLIDATION_INTERVAL_SECONDS", 1800, minimum=60
        )
        self._last_check_ts = 0.0
        self._last_result: Dict[str, Any] = {
            "scheduled": False,
            "reason": "not_started",
            "retry_after_seconds": self._check_interval_seconds,
        }
        self._guard = asyncio.Lock()

    def _resolve_retry_interval_seconds_locked(self) -> float:
        retry_after = self._last_result.get("retry_after_seconds")
        if isinstance(retry_after, (int, float)):
            retry_after_float = float(retry_after)
            if retry_after_float > 0:
                return min(retry_after_float, float(self._check_interval_seconds))
        return float(self._check_interval_seconds)

    async def schedule(
        self,
        *,
        index_worker: IndexTaskWorker,
        force: bool = False,
        reason: str = "runtime",
    ) -> Dict[str, Any]:
        async with self._guard:
            now_ts = time.time()
            if not self._enabled:
                self._last_result = {
                    "scheduled": False,
                    "reason": "sleep_consolidation_disabled",
                    "check_interval_seconds": self._check_interval_seconds,
                    "retry_after_seconds": self._check_interval_seconds,
                }
                self._last_check_ts = now_ts
                return dict(self._last_result)

            retry_interval_seconds = self._resolve_retry_interval_seconds_locked()
            if (
                not force
                and self._last_check_ts > 0
                and (now_ts - self._last_check_ts) < retry_interval_seconds
            ):
                return dict(self._last_result)

            payload = await index_worker.enqueue_sleep_consolidation(
                reason=reason or "runtime"
            )
            scheduled = bool(payload.get("queued")) or bool(payload.get("deduped"))
            enqueue_reason = str(payload.get("reason") or "")
            retry_after_seconds = float(self._check_interval_seconds)
            if not scheduled and enqueue_reason == "queue_full":
                retry_after_seconds = min(
                    retry_after_seconds, self._QUEUE_FULL_RETRY_SECONDS
                )
            self._last_result = {
                "scheduled": scheduled,
                "reason": reason or "runtime",
                "forced": bool(force),
                "check_interval_seconds": self._check_interval_seconds,
                "retry_after_seconds": retry_after_seconds,
                "enqueue_reason": enqueue_reason,
                "requested_at": _utc_iso_now(),
                **payload,
            }
            self._last_check_ts = now_ts
            return dict(self._last_result)

    async def status(self) -> Dict[str, Any]:
        async with self._guard:
            return {
                "enabled": self._enabled,
                "check_interval_seconds": self._check_interval_seconds,
                **dict(self._last_result),
            }


class RuntimeState:
    def __init__(self) -> None:
        self.write_lanes = WriteLaneCoordinator()
        self.session_cache = SessionSearchCache()
        self.flush_tracker = SessionFlushTracker()
        self.guard_tracker = GuardDecisionTracker()
        self.cleanup_reviews = CleanupReviewCoordinator()
        self.vitality_decay = VitalityDecayCoordinator()
        self.index_worker = IndexTaskWorker()
        self.sleep_consolidation = SleepTimeConsolidator()

    async def ensure_started(self, client_factory: Callable[[], Any]) -> None:
        await self.index_worker.ensure_started(client_factory)
        await self.vitality_decay.run_decay(
            client_factory=client_factory,
            force=False,
            reason="runtime.ensure_started",
        )
        await self.sleep_consolidation.schedule(
            index_worker=self.index_worker,
            force=False,
            reason="runtime.ensure_started",
        )

    async def shutdown(self) -> None:
        await self.index_worker.shutdown()


runtime_state = RuntimeState()
