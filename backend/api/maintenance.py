import asyncio
import hashlib
import hmac
import importlib
import inspect
import json
import math
import os
import re
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from db import get_sqlite_client
from runtime_state import runtime_state
from security.import_guard import ExternalImportGuard, ExternalImportGuardConfig

_MCP_API_KEY_ENV = "MCP_API_KEY"
_MCP_API_KEY_HEADER = "X-MCP-API-Key"
_MCP_API_KEY_ALLOW_INSECURE_LOCAL_ENV = "MCP_API_KEY_ALLOW_INSECURE_LOCAL"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on", "enabled"}
_LOOPBACK_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost"}
_FORWARDED_HEADER_NAMES = {
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-real-ip",
    "x-client-ip",
    "true-client-ip",
    "cf-connecting-ip",
}


def _get_configured_mcp_api_key() -> str:
    return str(os.getenv(_MCP_API_KEY_ENV) or "").strip()


def _allow_insecure_local_without_api_key() -> bool:
    value = str(os.getenv(_MCP_API_KEY_ALLOW_INSECURE_LOCAL_ENV) or "").strip().lower()
    return value in _TRUTHY_ENV_VALUES


def _is_loopback_request(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = str(getattr(client, "host", "") or "").strip().lower()
    if host not in _LOOPBACK_CLIENT_HOSTS:
        return False
    headers = getattr(request, "headers", None)
    if headers is None:
        return True
    for header_name in _FORWARDED_HEADER_NAMES:
        header_value = headers.get(header_name)
        if isinstance(header_value, str) and header_value.strip():
            return False
    return True


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not isinstance(authorization, str):
        return None
    value = authorization.strip()
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token if token else None


async def require_maintenance_api_key(
    request: Request,
    x_mcp_api_key: Optional[str] = Header(default=None, alias=_MCP_API_KEY_HEADER),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> None:
    configured = _get_configured_mcp_api_key()
    if not configured:
        if _allow_insecure_local_without_api_key() and _is_loopback_request(request):
            return
        reason = (
            "insecure_local_override_requires_loopback"
            if _allow_insecure_local_without_api_key()
            else "api_key_not_configured"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "maintenance_auth_failed",
                "reason": reason,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided = str(x_mcp_api_key or "").strip() or _extract_bearer_token(authorization)
    if not provided or not hmac.compare_digest(provided, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "maintenance_auth_failed",
                "reason": "invalid_or_missing_api_key",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(
    prefix="/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(require_maintenance_api_key)],
)
_ALLOWED_SEARCH_MODES = {"keyword", "semantic", "hybrid"}
_VALID_DOMAINS = [
    d.strip().lower()
    for d in str(os.getenv("VALID_DOMAINS", "core,writer,game,notes,system")).split(",")
    if d.strip()
]
_SCOPE_URI_PATTERN = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)://(.*)$")
_SEARCH_EVENT_LIMIT = 200
_SEARCH_EVENTS_META_KEY = "observability.search_events.v1"
_search_events: Deque[Dict[str, Any]] = deque(maxlen=_SEARCH_EVENT_LIMIT)
_search_events_guard = asyncio.Lock()
_search_events_loaded = False
_CLEANUP_QUERY_EVENT_LIMIT = 200
_cleanup_query_events: Deque[Dict[str, Any]] = deque(maxlen=_CLEANUP_QUERY_EVENT_LIMIT)
_cleanup_query_events_guard = asyncio.Lock()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


_CLEANUP_QUERY_SLOW_MS = max(
    1.0, _env_float("OBSERVABILITY_CLEANUP_QUERY_SLOW_MS", 250.0)
)
_INTENT_LLM_ENABLED = str(os.getenv("INTENT_LLM_ENABLED") or "").strip().lower() in _TRUTHY_ENV_VALUES
ENABLE_WRITE_LANE_QUEUE = _env_bool("RUNTIME_WRITE_LANE_QUEUE", True)


class SearchConsoleRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: str = Field(default="hybrid")
    max_results: int = Field(default=8, ge=1, le=50)
    candidate_multiplier: int = Field(default=4, ge=1, le=20)
    include_session: bool = True
    session_id: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    scope_hint: Optional[str] = None


class VitalityCleanupQueryRequest(BaseModel):
    threshold: float = Field(default=0.35, ge=0.0)
    inactive_days: float = Field(default=14.0, ge=0.0)
    limit: int = Field(default=50, ge=1, le=500)
    domain: Optional[str] = None
    path_prefix: Optional[str] = None


class CleanupSelectionItem(BaseModel):
    memory_id: int = Field(ge=1)
    state_hash: str = Field(min_length=16, max_length=128)


class VitalityCleanupPrepareRequest(BaseModel):
    action: str = Field(default="delete")
    selections: List[CleanupSelectionItem] = Field(min_length=1, max_length=100)
    reviewer: Optional[str] = None
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class VitalityCleanupConfirmRequest(BaseModel):
    review_id: str = Field(min_length=8)
    token: str = Field(min_length=16)
    confirmation_phrase: str = Field(min_length=8)


class IndexJobCancelRequest(BaseModel):
    reason: str = Field(default="api_cancel", min_length=1, max_length=120)


class IndexJobRetryRequest(BaseModel):
    reason: str = Field(default="", max_length=120)


class ImportPrepareRequest(BaseModel):
    file_paths: List[str] = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    source: str = Field(default="external_import", min_length=1, max_length=128)
    reason: str = Field(default="manual_import", min_length=1, max_length=240)
    domain: str = Field(default="notes", min_length=1, max_length=32)
    parent_path: str = Field(default="", max_length=512)
    priority: int = Field(default=2, ge=0, le=9)


class ImportExecuteRequest(BaseModel):
    job_id: str = Field(min_length=8, max_length=64)


class ImportRollbackRequest(BaseModel):
    reason: str = Field(default="manual_rollback", min_length=1, max_length=240)


class LearnTriggerRequest(BaseModel):
    content: str = Field(min_length=1)
    source: str = Field(default="manual_review", min_length=1, max_length=128)
    reason: str = Field(default="", max_length=240)
    session_id: str = Field(min_length=1, max_length=128)
    actor_id: Optional[str] = Field(default=None, max_length=128)
    domain: str = Field(default="notes", min_length=1, max_length=32)
    path_prefix: str = Field(default="corrections", min_length=1, max_length=256)
    execute: bool = True


IMPORT_LEARN_AUDIT_META_KEY = "audit.import_learn.summary.v1"
_IMPORT_LEARN_META_PERSIST_LOCK = asyncio.Lock()
_IMPORT_JOB_MAX_PENDING = 64
_IMPORT_JOBS: Dict[str, Dict[str, Any]] = {}
_IMPORT_JOBS_GUARD = asyncio.Lock()
_IMPORT_JOBS_META_KEY = "maintenance.import.jobs.v1"
_IMPORT_JOBS_META_PERSIST_LOCK = asyncio.Lock()
_LEARN_JOB_MAX_PENDING = 64
_LEARN_JOBS: Dict[str, Dict[str, Any]] = {}
_LEARN_JOBS_GUARD = asyncio.Lock()
_LEARN_JOBS_META_KEY = "maintenance.learn.jobs.v1"
_LEARN_JOBS_META_PERSIST_LOCK = asyncio.Lock()
_IMPORT_TITLE_SEGMENT_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")
_EXTERNAL_IMPORT_GUARD: Optional[ExternalImportGuard] = None
_EXTERNAL_IMPORT_GUARD_FINGERPRINT: Optional[Tuple[Any, ...]] = None
_EXTERNAL_IMPORT_GUARD_LOCK = asyncio.Lock()
_EXTERNAL_IMPORT_ALLOWED_DOMAINS_ENV = "EXTERNAL_IMPORT_ALLOWED_DOMAINS"
_EXPLICIT_LEARN_SERVICE: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None
_EXPLICIT_LEARN_SERVICE_LOCK = asyncio.Lock()


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_ts(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1))
    return float(ordered[index])


def _safe_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


async def _run_write_lane(
    operation: str,
    task: Callable[[], Awaitable[Any]],
    *,
    session_id: Optional[str] = None,
) -> Any:
    if not ENABLE_WRITE_LANE_QUEUE:
        return await task()
    return await runtime_state.write_lanes.run_write(
        session_id=session_id,
        operation=operation,
        task=task,
    )


class _LazySQLiteClientProxy:
    """Defer sqlite client resolution until an attribute is actually used."""

    def __init__(self, client_factory: Callable[[], Any]) -> None:
        self._client_factory = client_factory
        self._resolved_client: Optional[Any] = None

    def _resolve(self) -> Any:
        if self._resolved_client is None:
            self._resolved_client = self._client_factory()
        return self._resolved_client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


def _normalize_import_job_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return "learn" if normalized == "learn" else "import"


def _normalize_created_namespace_memories(entries: Any) -> List[Dict[str, Any]]:
    if not isinstance(entries, list):
        return []
    normalized_entries: List[Dict[str, Any]] = []
    seen_uris: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "notes").strip().lower() or "notes"
        path = _normalize_import_parent_path(str(item.get("path") or ""))
        uri = str(item.get("uri") or "").strip()
        if not path and uri:
            match = _SCOPE_URI_PATTERN.match(uri)
            if match:
                domain = str(match.group(1) or domain).strip().lower() or domain
                path = _normalize_import_parent_path(match.group(2))
        if not path:
            continue
        normalized_uri = f"{domain}://{path}"
        if normalized_uri in seen_uris:
            continue
        seen_uris.add(normalized_uri)
        normalized_entries.append(
            {
                "domain": domain,
                "path": path,
                "uri": normalized_uri,
                "memory_id": _safe_non_negative_int(item.get("memory_id")),
            }
        )
    return normalized_entries


def _has_created_memory_ids(created_memories: Any) -> bool:
    if not isinstance(created_memories, list):
        return False
    for item in created_memories:
        if not isinstance(item, dict):
            continue
        if _safe_non_negative_int(item.get("memory_id")) > 0:
            return True
    return False


def _is_rollback_protected_import_job(payload: Dict[str, Any]) -> bool:
    if _normalize_import_job_type(payload.get("job_type")) != "import":
        return False
    if str(payload.get("status") or "").strip().lower() == "rolled_back":
        return False
    return _has_created_memory_ids(payload.get("created_memories"))


async def _resolve_explicit_learn_service() -> Callable[..., Awaitable[Dict[str, Any]]]:
    global _EXPLICIT_LEARN_SERVICE

    if callable(_EXPLICIT_LEARN_SERVICE):
        return _EXPLICIT_LEARN_SERVICE

    async with _EXPLICIT_LEARN_SERVICE_LOCK:
        if callable(_EXPLICIT_LEARN_SERVICE):
            return _EXPLICIT_LEARN_SERVICE
        try:
            module = importlib.import_module("mcp_server")
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "explicit_learn_service_unavailable",
                    "reason": "import_failed",
                    "message": type(exc).__name__,
                },
            ) from exc

        service = getattr(module, "run_explicit_learn_service", None)
        if not callable(service):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "explicit_learn_service_unavailable",
                    "reason": "missing_entrypoint",
                },
            )

        _EXPLICIT_LEARN_SERVICE = cast(
            Callable[..., Awaitable[Dict[str, Any]]],
            service,
        )
        return _EXPLICIT_LEARN_SERVICE


def _normalize_import_parent_path(parent_path: Optional[str]) -> str:
    raw = str(parent_path or "").strip().strip("/")
    if not raw:
        return ""
    segments = [segment for segment in raw.split("/") if segment]
    return "/".join(segments)


def _sanitize_import_title(path_value: str, source_hash: str, *, suffix: str = "") -> str:
    stem = Path(path_value).stem.strip()
    stem = _IMPORT_TITLE_SEGMENT_PATTERN.sub("-", stem).strip("-._")
    if not stem:
        stem = "imported"
    normalized_suffix = _IMPORT_TITLE_SEGMENT_PATTERN.sub("-", str(suffix or "")).strip(
        "-._"
    )
    if normalized_suffix:
        return f"{stem}-{source_hash[:8]}-{normalized_suffix[:10]}"
    return f"{stem}-{source_hash[:8]}"


def _build_import_source_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8", errors="ignore")).hexdigest()


def _build_import_target_uri(*, domain: str, parent_path: str, title: str) -> tuple[str, str]:
    normalized_parent = _normalize_import_parent_path(parent_path)
    target_path = f"{normalized_parent}/{title}" if normalized_parent else title
    return target_path, f"{domain}://{target_path}"


def _trim_import_preview(content: str, limit: int = 160) -> str:
    snippet = (content or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if len(snippet) <= max(1, limit):
        return snippet
    return f"{snippet[:max(1, limit)]}..."


def _clone_import_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _clone_import_payload_for_persistence(payload: Dict[str, Any]) -> Dict[str, Any]:
    persisted_payload = _clone_import_payload(payload)
    status = str(persisted_payload.get("status") or "").strip().lower()
    keep_snapshot_content = status in {"prepared", "executing"}
    files = persisted_payload.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict):
                if not keep_snapshot_content:
                    item.pop("content", None)
    return persisted_payload


def _trim_jobs_with_limit(
    jobs: Dict[str, Dict[str, Any]], max_pending: int
) -> Dict[str, Dict[str, Any]]:
    ordered = sorted(
        (
            (job_id, payload)
            for job_id, payload in jobs.items()
            if isinstance(job_id, str) and job_id and isinstance(payload, dict)
        ),
        key=lambda item: (str(item[1].get("created_at") or ""), item[0]),
    )
    if len(ordered) > max_pending:
        ordered = ordered[-max_pending:]
    return {
        job_id: _clone_import_payload(payload)
        for job_id, payload in ordered
    }


def _trim_import_jobs(jobs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    max_pending = _IMPORT_JOB_MAX_PENDING
    if max_pending <= 0:
        return {}

    ordered = sorted(
        (
            (job_id, payload)
            for job_id, payload in jobs.items()
            if isinstance(job_id, str) and job_id and isinstance(payload, dict)
        ),
        key=lambda item: (str(item[1].get("created_at") or ""), item[0]),
    )
    if len(ordered) <= max_pending:
        return {
            job_id: _clone_import_payload(payload)
            for job_id, payload in ordered
        }

    protected_items = [
        (job_id, payload)
        for job_id, payload in ordered
        if _is_rollback_protected_import_job(payload)
    ]
    unprotected_items = [
        (job_id, payload)
        for job_id, payload in ordered
        if not _is_rollback_protected_import_job(payload)
    ]

    if len(protected_items) >= max_pending:
        selected = protected_items[-max_pending:]
    else:
        slots = max_pending - len(protected_items)
        selected = protected_items + unprotected_items[-slots:]

    selected.sort(key=lambda item: (str(item[1].get("created_at") or ""), item[0]))
    return {
        job_id: _clone_import_payload(payload)
        for job_id, payload in selected
    }


def _trim_learn_jobs(jobs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return _trim_jobs_with_limit(jobs, _LEARN_JOB_MAX_PENDING)


def _serialize_import_jobs_for_runtime_meta(
    jobs: Dict[str, Dict[str, Any]],
) -> str:
    trimmed_jobs = _trim_import_jobs(jobs)
    payload = {
        "version": 1,
        "updated_at": _utc_iso_now(),
        "jobs": {
            job_id: _clone_import_payload_for_persistence(job_payload)
            for job_id, job_payload in trimmed_jobs.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _parse_import_jobs_from_runtime_meta(raw: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not raw or not isinstance(raw, str):
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    parsed: Dict[str, Dict[str, Any]] = {}
    for job_id, job_payload in jobs.items():
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id or not isinstance(job_payload, dict):
            continue
        normalized_payload = _clone_import_payload_for_persistence(job_payload)
        normalized_payload["job_type"] = _normalize_import_job_type(
            normalized_payload.get("job_type")
        )
        if "reason_text" not in normalized_payload and isinstance(
            normalized_payload.get("reason"), str
        ):
            normalized_payload["reason_text"] = str(normalized_payload.get("reason"))
        if "reason" not in normalized_payload and isinstance(
            normalized_payload.get("reason_text"), str
        ):
            normalized_payload["reason"] = str(normalized_payload.get("reason_text"))
        parsed[normalized_job_id] = normalized_payload
    return _trim_import_jobs(parsed)


def _serialize_learn_jobs_for_runtime_meta(
    jobs: Dict[str, Dict[str, Any]],
) -> str:
    trimmed_jobs = _trim_learn_jobs(jobs)
    payload = {
        "version": 1,
        "updated_at": _utc_iso_now(),
        "jobs": {
            job_id: _clone_import_payload_for_persistence(job_payload)
            for job_id, job_payload in trimmed_jobs.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _parse_learn_jobs_from_runtime_meta(raw: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not raw or not isinstance(raw, str):
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    parsed: Dict[str, Dict[str, Any]] = {}
    for job_id, job_payload in jobs.items():
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id or not isinstance(job_payload, dict):
            continue
        normalized_payload = _clone_import_payload_for_persistence(job_payload)
        normalized_payload["job_type"] = "learn"
        if "reason_text" not in normalized_payload and isinstance(
            normalized_payload.get("reason"), str
        ):
            normalized_payload["reason_text"] = str(normalized_payload.get("reason"))
        if "reason" not in normalized_payload and isinstance(
            normalized_payload.get("reason_text"), str
        ):
            normalized_payload["reason"] = str(normalized_payload.get("reason_text"))
        parsed[normalized_job_id] = normalized_payload
    return _trim_learn_jobs(parsed)


async def _persist_import_jobs_runtime_meta(
    jobs: Dict[str, Dict[str, Any]],
) -> None:
    try:
        client = get_sqlite_client()
        set_runtime_meta = getattr(client, "set_runtime_meta", None)
        if not callable(set_runtime_meta):
            return
        payload = _serialize_import_jobs_for_runtime_meta(jobs)
        async with _IMPORT_JOBS_META_PERSIST_LOCK:
            await set_runtime_meta(_IMPORT_JOBS_META_KEY, payload)
    except Exception:
        return


async def _persist_learn_jobs_runtime_meta(
    jobs: Dict[str, Dict[str, Any]],
) -> None:
    try:
        client = get_sqlite_client()
        set_runtime_meta = getattr(client, "set_runtime_meta", None)
        if not callable(set_runtime_meta):
            return
        payload = _serialize_learn_jobs_for_runtime_meta(jobs)
        async with _LEARN_JOBS_META_PERSIST_LOCK:
            await set_runtime_meta(_LEARN_JOBS_META_KEY, payload)
    except Exception:
        return


async def _load_import_jobs_from_runtime_meta() -> Dict[str, Dict[str, Any]]:
    try:
        client = get_sqlite_client()
        get_runtime_meta = getattr(client, "get_runtime_meta", None)
        if not callable(get_runtime_meta):
            return {}
        raw = await get_runtime_meta(_IMPORT_JOBS_META_KEY)
    except Exception:
        return {}
    return _parse_import_jobs_from_runtime_meta(raw)


async def _load_learn_jobs_from_runtime_meta() -> Dict[str, Dict[str, Any]]:
    try:
        client = get_sqlite_client()
        get_runtime_meta = getattr(client, "get_runtime_meta", None)
        if not callable(get_runtime_meta):
            return {}
        raw = await get_runtime_meta(_LEARN_JOBS_META_KEY)
    except Exception:
        return {}
    return _parse_learn_jobs_from_runtime_meta(raw)


async def _hydrate_import_jobs_cache(job_id: Optional[str] = None) -> None:
    normalized_job_id = str(job_id or "").strip()
    async with _IMPORT_JOBS_GUARD:
        if normalized_job_id and normalized_job_id in _IMPORT_JOBS:
            return
    persisted_jobs = await _load_import_jobs_from_runtime_meta()
    if not persisted_jobs:
        return
    async with _IMPORT_JOBS_GUARD:
        if normalized_job_id and normalized_job_id in _IMPORT_JOBS:
            return
        for persisted_job_id, persisted_payload in persisted_jobs.items():
            if persisted_job_id not in _IMPORT_JOBS:
                _IMPORT_JOBS[persisted_job_id] = _clone_import_payload(persisted_payload)
        trimmed = _trim_import_jobs(_IMPORT_JOBS)
        _IMPORT_JOBS.clear()
        _IMPORT_JOBS.update(trimmed)


async def _hydrate_learn_jobs_cache(job_id: Optional[str] = None) -> None:
    normalized_job_id = str(job_id or "").strip()
    async with _LEARN_JOBS_GUARD:
        if normalized_job_id and normalized_job_id in _LEARN_JOBS:
            return
    persisted_jobs = await _load_learn_jobs_from_runtime_meta()
    if not persisted_jobs:
        return
    async with _LEARN_JOBS_GUARD:
        if normalized_job_id and normalized_job_id in _LEARN_JOBS:
            return
        for persisted_job_id, persisted_payload in persisted_jobs.items():
            if persisted_job_id not in _LEARN_JOBS:
                _LEARN_JOBS[persisted_job_id] = _clone_import_payload(persisted_payload)
        trimmed = _trim_learn_jobs(_LEARN_JOBS)
        _LEARN_JOBS.clear()
        _LEARN_JOBS.update(trimmed)


def _external_import_allowed_domains() -> Tuple[str, ...]:
    raw = str(os.getenv(_EXTERNAL_IMPORT_ALLOWED_DOMAINS_ENV, "notes") or "")
    allowed_domains: List[str] = []
    for item in raw.split(","):
        value = str(item or "").strip().lower()
        if value and value not in allowed_domains:
            allowed_domains.append(value)
    return tuple(allowed_domains)


def _public_import_job_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    public_payload = _clone_import_payload(payload)
    public_payload["job_type"] = _normalize_import_job_type(public_payload.get("job_type"))
    if "reason_text" not in public_payload and isinstance(public_payload.get("reason"), str):
        public_payload["reason_text"] = str(public_payload.get("reason"))
    if "reason" not in public_payload and isinstance(public_payload.get("reason_text"), str):
        public_payload["reason"] = str(public_payload.get("reason_text"))
    files = public_payload.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict):
                item.pop("content", None)
                item.pop("resolved_path", None)
    return public_payload


def _http_error_for_import_guard(result: Dict[str, Any]) -> HTTPException:
    reason = str(result.get("reason") or "rejected")
    detail: Dict[str, Any] = {
        "error": "external_import_prepare_rejected",
        "reason": reason,
        "requested_file_count": _safe_non_negative_int(result.get("requested_file_count")),
        "rejected_files": result.get("rejected_files") if isinstance(result.get("rejected_files"), list) else [],
    }
    config_errors = result.get("config_errors")
    if isinstance(config_errors, list) and config_errors:
        detail["config_errors"] = [str(item) for item in config_errors]
    storage = str(result.get("rate_limit_storage") or "").strip()
    if storage:
        detail["rate_limit_storage"] = storage
    retry_after = _safe_non_negative_int(result.get("retry_after_seconds"))
    if retry_after > 0:
        detail["retry_after_seconds"] = retry_after
    if reason in {
        "external_import_disabled",
        "allowed_roots_not_configured",
        "allowed_exts_not_configured",
        "rate_limit_shared_state_required",
    }:
        return HTTPException(status_code=409, detail=detail)
    if reason in {
        "rate_limited",
        "rate_limit_state_unavailable",
        "max_files_exceeded",
        "max_total_bytes_exceeded",
    }:
        return HTTPException(status_code=429, detail=detail)
    if reason == "file_validation_failed":
        rejected_files = detail.get("rejected_files") or []
        if any(
            isinstance(item, dict) and str(item.get("reason") or "") == "path_not_allowed"
            for item in rejected_files
        ):
            return HTTPException(status_code=403, detail=detail)
        return HTTPException(status_code=422, detail=detail)
    return HTTPException(status_code=422, detail=detail)


def _http_error_for_learn_trigger(
    *,
    result: Dict[str, Any],
    job_id: str,
    job_payload: Dict[str, Any],
) -> HTTPException:
    reason = str(result.get("reason") or "rejected")
    detail: Dict[str, Any] = {
        "error": "explicit_learn_trigger_rejected",
        "reason": reason,
        "job_id": job_id,
        "job": _public_import_job_payload(job_payload),
        "result": result,
    }
    if reason == "write_guard_unavailable":
        return HTTPException(status_code=503, detail=detail)
    if reason in {
        "source_required",
        "reason_required",
        "session_id_required",
        "content_required",
        "domain_not_allowed",
    }:
        return HTTPException(status_code=422, detail=detail)
    return HTTPException(status_code=409, detail=detail)


def _validate_import_domain(domain: str) -> str:
    normalized = str(domain or "").strip().lower()
    if normalized not in _VALID_DOMAINS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "external_import_invalid_domain",
                "reason": f"unknown_domain:{normalized or 'empty'}",
                "valid_domains": list(_VALID_DOMAINS),
            },
        )
    allowed_domains = _external_import_allowed_domains()
    if not allowed_domains:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "external_import_invalid_policy",
                "reason": "allowed_domains_not_configured",
                "env": _EXTERNAL_IMPORT_ALLOWED_DOMAINS_ENV,
                "valid_domains": list(_VALID_DOMAINS),
            },
        )
    invalid_allowed_domains = [
        item for item in allowed_domains if item not in _VALID_DOMAINS
    ]
    if invalid_allowed_domains:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "external_import_invalid_policy",
                "reason": "allowed_domains_invalid",
                "invalid_allowed_domains": invalid_allowed_domains,
                "valid_domains": list(_VALID_DOMAINS),
            },
        )
    if normalized not in allowed_domains:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "external_import_invalid_domain",
                "reason": "domain_not_allowed_for_external_import",
                "domain": normalized,
                "allowed_domains": list(allowed_domains),
            },
        )
    return normalized


def _external_import_guard_fingerprint(config: ExternalImportGuardConfig) -> Tuple[Any, ...]:
    return (
        bool(config.enabled),
        tuple(str(item) for item in config.allowed_roots),
        tuple(str(item) for item in config.allowed_exts),
        int(config.max_total_bytes),
        int(config.max_files),
        int(config.rate_limit_window_seconds),
        int(config.rate_limit_max_requests),
        str(config.rate_limit_state_file) if config.rate_limit_state_file else "",
        bool(config.require_shared_rate_limit),
    )


def _build_external_import_policy_snapshot(guard: ExternalImportGuard) -> Dict[str, Any]:
    allowed_domains = list(_external_import_allowed_domains())
    policy = {
        **guard.policy_snapshot(),
        "allowed_domains": allowed_domains,
    }
    fingerprint_payload = json.dumps(policy, ensure_ascii=False, sort_keys=True)
    policy["policy_hash"] = hashlib.sha256(
        fingerprint_payload.encode("utf-8", errors="ignore")
    ).hexdigest()
    return policy


async def _get_external_import_guard() -> ExternalImportGuard:
    global _EXTERNAL_IMPORT_GUARD, _EXTERNAL_IMPORT_GUARD_FINGERPRINT
    config = ExternalImportGuardConfig.from_env()
    fingerprint = _external_import_guard_fingerprint(config)
    async with _EXTERNAL_IMPORT_GUARD_LOCK:
        if (
            _EXTERNAL_IMPORT_GUARD is None
            or _EXTERNAL_IMPORT_GUARD_FINGERPRINT != fingerprint
        ):
            _EXTERNAL_IMPORT_GUARD = ExternalImportGuard(config=config)
            _EXTERNAL_IMPORT_GUARD_FINGERPRINT = fingerprint
        return _EXTERNAL_IMPORT_GUARD


async def _record_import_learn_event(
    *,
    event_type: str,
    operation: str,
    decision: str,
    reason: str,
    source: str,
    session_id: Optional[str],
    actor_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    persist_runtime_meta: bool = True,
) -> None:
    try:
        await runtime_state.import_learn_tracker.record_event(
            event_type=event_type,
            operation=operation,
            decision=decision,
            reason=reason,
            source=source,
            session_id=session_id,
            actor_id=actor_id,
            batch_id=batch_id,
            metadata=metadata,
        )
    except Exception:
        return

    if not persist_runtime_meta:
        return

    try:
        client = get_sqlite_client()
        set_runtime_meta = getattr(client, "set_runtime_meta", None)
        if callable(set_runtime_meta):
            async with _IMPORT_LEARN_META_PERSIST_LOCK:
                summary_payload = await runtime_state.import_learn_tracker.summary()
                await set_runtime_meta(
                    IMPORT_LEARN_AUDIT_META_KEY,
                    json.dumps(summary_payload, ensure_ascii=False, separators=(",", ":")),
                )
    except Exception:
        return


async def _put_import_job(payload: Dict[str, Any]) -> None:
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        return
    incoming_job_type = _normalize_import_job_type(payload.get("job_type"))
    normalized_payload = _clone_import_payload(payload)
    normalized_payload["job_type"] = incoming_job_type
    if "reason_text" not in normalized_payload and isinstance(
        normalized_payload.get("reason"), str
    ):
        normalized_payload["reason_text"] = str(normalized_payload.get("reason"))
    if "reason" not in normalized_payload and isinstance(
        normalized_payload.get("reason_text"), str
    ):
        normalized_payload["reason"] = str(normalized_payload.get("reason_text"))
    snapshot: Dict[str, Dict[str, Any]] = {}
    async with _IMPORT_JOBS_GUARD:
        while len(_IMPORT_JOBS) >= _IMPORT_JOB_MAX_PENDING:
            same_type_items = [
                (item_job_id, item_payload)
                for item_job_id, item_payload in _IMPORT_JOBS.items()
                if _normalize_import_job_type(item_payload.get("job_type"))
                == incoming_job_type
            ]
            same_type_unprotected_items = [
                (item_job_id, item_payload)
                for item_job_id, item_payload in same_type_items
                if not _is_rollback_protected_import_job(item_payload)
            ]
            cross_type_items = [
                (item_job_id, item_payload)
                for item_job_id, item_payload in _IMPORT_JOBS.items()
                if _normalize_import_job_type(item_payload.get("job_type"))
                != incoming_job_type
            ]
            cross_type_unprotected_items = [
                (item_job_id, item_payload)
                for item_job_id, item_payload in cross_type_items
                if not _is_rollback_protected_import_job(item_payload)
            ]
            if same_type_unprotected_items:
                eviction_pool = same_type_unprotected_items
            elif incoming_job_type == "import" and cross_type_items:
                eviction_pool = (
                    cross_type_unprotected_items
                    if cross_type_unprotected_items
                    else cross_type_items
                )
            elif same_type_items:
                eviction_pool = same_type_items
            elif incoming_job_type == "learn" and cross_type_items:
                eviction_pool = (
                    cross_type_unprotected_items
                    if cross_type_unprotected_items
                    else cross_type_items
                )
            else:
                eviction_pool = list(_IMPORT_JOBS.items())
            oldest_key = min(
                eviction_pool,
                key=lambda item: (str(item[1].get("created_at") or ""), item[0]),
            )[0]
            _IMPORT_JOBS.pop(oldest_key, None)
        _IMPORT_JOBS[job_id] = _clone_import_payload(normalized_payload)
        trimmed = _trim_import_jobs(_IMPORT_JOBS)
        _IMPORT_JOBS.clear()
        _IMPORT_JOBS.update(trimmed)
        snapshot = {
            item_job_id: _clone_import_payload(item_payload)
            for item_job_id, item_payload in _IMPORT_JOBS.items()
        }
    await _persist_import_jobs_runtime_meta(snapshot)


async def _put_learn_job(payload: Dict[str, Any]) -> None:
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        return
    normalized_payload = _clone_import_payload(payload)
    normalized_payload["job_type"] = "learn"
    if "reason_text" not in normalized_payload and isinstance(
        normalized_payload.get("reason"), str
    ):
        normalized_payload["reason_text"] = str(normalized_payload.get("reason"))
    if "reason" not in normalized_payload and isinstance(
        normalized_payload.get("reason_text"), str
    ):
        normalized_payload["reason"] = str(normalized_payload.get("reason_text"))
    snapshot: Dict[str, Dict[str, Any]] = {}
    async with _LEARN_JOBS_GUARD:
        while len(_LEARN_JOBS) >= _LEARN_JOB_MAX_PENDING:
            oldest_key = min(
                _LEARN_JOBS.items(),
                key=lambda item: (str(item[1].get("created_at") or ""), item[0]),
            )[0]
            _LEARN_JOBS.pop(oldest_key, None)
        _LEARN_JOBS[job_id] = _clone_import_payload(normalized_payload)
        trimmed = _trim_learn_jobs(_LEARN_JOBS)
        _LEARN_JOBS.clear()
        _LEARN_JOBS.update(trimmed)
        snapshot = {
            item_job_id: _clone_import_payload(item_payload)
            for item_job_id, item_payload in _LEARN_JOBS.items()
        }
    await _persist_learn_jobs_runtime_meta(snapshot)


async def _get_import_job(job_id: str) -> Optional[Dict[str, Any]]:
    normalized = str(job_id or "").strip()
    if not normalized:
        return None
    async with _IMPORT_JOBS_GUARD:
        payload = _IMPORT_JOBS.get(normalized)
    if isinstance(payload, dict):
        return _clone_import_payload(payload)

    await _hydrate_import_jobs_cache(job_id=normalized)
    async with _IMPORT_JOBS_GUARD:
        persisted_payload = _IMPORT_JOBS.get(normalized)
        if not isinstance(persisted_payload, dict):
            return None
        return _clone_import_payload(persisted_payload)


async def _get_learn_job(job_id: str) -> Optional[Dict[str, Any]]:
    normalized = str(job_id or "").strip()
    if not normalized:
        return None
    async with _LEARN_JOBS_GUARD:
        payload = _LEARN_JOBS.get(normalized)
    if isinstance(payload, dict):
        return _clone_import_payload(payload)

    await _hydrate_learn_jobs_cache(job_id=normalized)
    async with _LEARN_JOBS_GUARD:
        persisted_payload = _LEARN_JOBS.get(normalized)
        if not isinstance(persisted_payload, dict):
            return None
        return _clone_import_payload(persisted_payload)


async def _update_import_job(job_id: str, payload: Dict[str, Any]) -> None:
    normalized = str(job_id or "").strip()
    if not normalized:
        return
    cloned = _clone_import_payload(payload)
    cloned["job_type"] = _normalize_import_job_type(cloned.get("job_type"))
    if "reason_text" not in cloned and isinstance(cloned.get("reason"), str):
        cloned["reason_text"] = str(cloned.get("reason"))
    if "reason" not in cloned and isinstance(cloned.get("reason_text"), str):
        cloned["reason"] = str(cloned.get("reason_text"))
    cloned["updated_at"] = _utc_iso_now()
    snapshot: Dict[str, Dict[str, Any]] = {}
    async with _IMPORT_JOBS_GUARD:
        _IMPORT_JOBS[normalized] = cloned
        trimmed = _trim_import_jobs(_IMPORT_JOBS)
        _IMPORT_JOBS.clear()
        _IMPORT_JOBS.update(trimmed)
        snapshot = {
            item_job_id: _clone_import_payload(item_payload)
            for item_job_id, item_payload in _IMPORT_JOBS.items()
        }
    await _persist_import_jobs_runtime_meta(snapshot)


async def _update_learn_job(job_id: str, payload: Dict[str, Any]) -> None:
    normalized = str(job_id or "").strip()
    if not normalized:
        return
    cloned = _clone_import_payload(payload)
    cloned["job_type"] = "learn"
    if "reason_text" not in cloned and isinstance(cloned.get("reason"), str):
        cloned["reason_text"] = str(cloned.get("reason"))
    if "reason" not in cloned and isinstance(cloned.get("reason_text"), str):
        cloned["reason"] = str(cloned.get("reason_text"))
    cloned["updated_at"] = _utc_iso_now()
    snapshot: Dict[str, Dict[str, Any]] = {}
    async with _LEARN_JOBS_GUARD:
        _LEARN_JOBS[normalized] = cloned
        trimmed = _trim_learn_jobs(_LEARN_JOBS)
        _LEARN_JOBS.clear()
        _LEARN_JOBS.update(trimmed)
        snapshot = {
            item_job_id: _clone_import_payload(item_payload)
            for item_job_id, item_payload in _LEARN_JOBS.items()
        }
    await _persist_learn_jobs_runtime_meta(snapshot)


async def _transition_import_job_status(
    job_id: str,
    *,
    allowed_from: set[str],
    next_status: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    normalized = str(job_id or "").strip()
    if not normalized:
        return None, "job_id_required"
    await _hydrate_import_jobs_cache(job_id=normalized)
    snapshot: Dict[str, Dict[str, Any]] = {}
    async with _IMPORT_JOBS_GUARD:
        payload = _IMPORT_JOBS.get(normalized)
        if not isinstance(payload, dict):
            return None, "job_not_found"
        current_status = str(payload.get("status") or "unknown")
        if current_status not in allowed_from:
            return _clone_import_payload(payload), f"invalid_status:{current_status}"
        updated = _clone_import_payload(payload)
        updated["status"] = next_status
        updated["updated_at"] = _utc_iso_now()
        _IMPORT_JOBS[normalized] = _clone_import_payload(updated)
        trimmed = _trim_import_jobs(_IMPORT_JOBS)
        _IMPORT_JOBS.clear()
        _IMPORT_JOBS.update(trimmed)
        snapshot = {
            item_job_id: _clone_import_payload(item_payload)
            for item_job_id, item_payload in _IMPORT_JOBS.items()
        }
    await _persist_import_jobs_runtime_meta(snapshot)
    return updated, None


async def _transition_learn_job_status(
    job_id: str,
    *,
    allowed_from: set[str],
    next_status: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    normalized = str(job_id or "").strip()
    if not normalized:
        return None, "job_id_required"
    await _hydrate_learn_jobs_cache(job_id=normalized)
    snapshot: Dict[str, Dict[str, Any]] = {}
    async with _LEARN_JOBS_GUARD:
        payload = _LEARN_JOBS.get(normalized)
        if not isinstance(payload, dict):
            return None, "job_not_found"
        current_status = str(payload.get("status") or "unknown")
        if current_status not in allowed_from:
            return _clone_import_payload(payload), f"invalid_status:{current_status}"
        updated = _clone_import_payload(payload)
        updated["job_type"] = "learn"
        updated["status"] = next_status
        updated["updated_at"] = _utc_iso_now()
        _LEARN_JOBS[normalized] = _clone_import_payload(updated)
        trimmed = _trim_learn_jobs(_LEARN_JOBS)
        _LEARN_JOBS.clear()
        _LEARN_JOBS.update(trimmed)
        snapshot = {
            item_job_id: _clone_import_payload(item_payload)
            for item_job_id, item_payload in _LEARN_JOBS.items()
        }
    await _persist_learn_jobs_runtime_meta(snapshot)
    return updated, None


async def _rollback_import_created_memories(
    *,
    client: Any,
    created_memories: List[Dict[str, Any]],
    created_namespace_memories: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    write_lane_session_id = (
        str(session_id or "").strip()
        or (f"maintenance.rollback:{job_id}" if job_id else "maintenance.rollback")
    )
    attempted_memory_ids: List[int] = []
    removed_paths: List[str] = []
    rolled_back: List[int] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    remove_path = getattr(client, "remove_path", None)
    for item in reversed(created_memories):
        if not isinstance(item, dict):
            continue
        memory_id = _safe_non_negative_int(item.get("memory_id"))
        if memory_id <= 0:
            continue
        attempted_memory_ids.append(memory_id)
        item_uri = str(item.get("uri") or "").strip()
        item_domain = str(item.get("domain") or "").strip().lower()
        item_path = _normalize_import_parent_path(str(item.get("path") or ""))
        if item_uri:
            match = _SCOPE_URI_PATTERN.match(item_uri)
            if match:
                if not item_domain:
                    item_domain = str(match.group(1) or "").strip().lower()
                if not item_path:
                    item_path = _normalize_import_parent_path(match.group(2))

        if callable(remove_path) and item_domain and item_path:
            try:
                async def _write_task_remove(
                    _path: str = item_path,
                    _domain: str = item_domain,
                ) -> Dict[str, Any]:
                    return await remove_path(_path, domain=_domain)

                await _run_write_lane(
                    "maintenance.import.rollback.remove_path",
                    _write_task_remove,
                    session_id=write_lane_session_id,
                )
                removed_uri = item_uri or f"{item_domain}://{item_path}"
                removed_paths.append(removed_uri)
            except ValueError as exc:
                skipped.append(
                    {
                        "memory_id": memory_id,
                        "uri": item_uri or f"{item_domain}://{item_path}",
                        "reason": str(exc) or "path_not_found",
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "memory_id": memory_id,
                        "uri": item_uri or f"{item_domain}://{item_path}",
                        "error": str(exc) or type(exc).__name__,
                    }
                )

        try:
            async def _write_task(_memory_id: int = memory_id) -> Dict[str, Any]:
                return await client.permanently_delete_memory(
                    _memory_id,
                    require_orphan=True,
                )

            await _run_write_lane(
                "maintenance.import.rollback.delete_memory",
                _write_task,
                session_id=write_lane_session_id,
            )
            rolled_back.append(memory_id)
        except (PermissionError, ValueError) as exc:
            skipped.append(
                {
                    "memory_id": memory_id,
                    "uri": item_uri or None,
                    "reason": str(exc) or type(exc).__name__,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "memory_id": memory_id,
                    "error": str(exc) or type(exc).__name__,
                }
            )

    namespace_cleanup = await _best_effort_cleanup_learn_namespace(
        client=client,
        created_namespace_memories=created_namespace_memories or [],
        session_id=write_lane_session_id,
        job_id=job_id,
    )
    namespace_attempted = bool(namespace_cleanup.get("attempted_paths"))
    namespace_has_skipped = bool(namespace_cleanup.get("skipped"))

    return {
        "attempted_memory_ids": attempted_memory_ids,
        "removed_paths": removed_paths,
        "rolled_back_memory_ids": rolled_back,
        "rolled_back_count": len(rolled_back),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "errors": errors,
        "namespace_cleanup": namespace_cleanup,
        "side_effects_audit_required": bool(attempted_memory_ids) or namespace_attempted,
        "residual_artifacts_review_required": bool(attempted_memory_ids)
        or namespace_attempted
        or namespace_has_skipped,
        "side_effects_note": (
            "rollback_covers_created_memory_ids_and_best_effort_namespace_cleanup"
            if namespace_attempted
            else "rollback_only_covers_created_memory_ids"
        ),
        "completed_at": _utc_iso_now(),
    }


async def _best_effort_cleanup_learn_namespace(
    *,
    client: Any,
    created_namespace_memories: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    write_lane_session_id = (
        str(session_id or "").strip()
        or (f"maintenance.learn.rollback:{job_id}" if job_id else "maintenance.learn.rollback")
    )
    normalized_entries = _normalize_created_namespace_memories(created_namespace_memories)
    if not normalized_entries:
        return {
            "attempted_paths": [],
            "removed_paths": [],
            "deleted_memory_ids": [],
            "skipped": [],
            "skipped_count": 0,
        }

    remove_path = getattr(client, "remove_path", None)
    permanently_delete_memory = getattr(client, "permanently_delete_memory", None)
    if not callable(remove_path) or not callable(permanently_delete_memory):
        attempted_paths = [str(item.get("uri") or "") for item in normalized_entries]
        return {
            "attempted_paths": attempted_paths,
            "removed_paths": [],
            "deleted_memory_ids": [],
            "skipped": [
                {
                    "uri": str(item.get("uri") or ""),
                    "reason": "namespace_cleanup_methods_unavailable",
                }
                for item in normalized_entries
            ],
            "skipped_count": len(normalized_entries),
        }

    attempted_paths: List[str] = []
    removed_paths: List[str] = []
    deleted_memory_ids: List[int] = []
    skipped: List[Dict[str, Any]] = []

    for entry in reversed(normalized_entries):
        domain = str(entry.get("domain") or "notes").strip().lower() or "notes"
        path = _normalize_import_parent_path(str(entry.get("path") or ""))
        uri = str(entry.get("uri") or f"{domain}://{path}")
        if not path:
            continue
        attempted_paths.append(uri)
        try:
            async def _write_task_remove(
                _path: str = path,
                _domain: str = domain,
            ) -> Dict[str, Any]:
                return await remove_path(_path, domain=_domain)

            remove_result = await _run_write_lane(
                "maintenance.learn.rollback.remove_path",
                _write_task_remove,
                session_id=write_lane_session_id,
            )
            removed_paths.append(uri)
        except Exception as exc:
            skipped.append({"uri": uri, "reason": str(exc) or type(exc).__name__})
            continue

        removed_memory_id = 0
        if isinstance(remove_result, dict):
            removed_memory_id = _safe_non_negative_int(remove_result.get("memory_id"))
        target_memory_id = removed_memory_id or _safe_non_negative_int(entry.get("memory_id"))
        if target_memory_id <= 0:
            continue
        try:
            async def _write_task_delete(_memory_id: int = target_memory_id) -> Dict[str, Any]:
                return await permanently_delete_memory(_memory_id, require_orphan=True)

            await _run_write_lane(
                "maintenance.learn.rollback.delete_memory",
                _write_task_delete,
                session_id=write_lane_session_id,
            )
            deleted_memory_ids.append(target_memory_id)
        except Exception as exc:
            skipped.append(
                {
                    "uri": uri,
                    "memory_id": target_memory_id,
                    "reason": str(exc) or type(exc).__name__,
                }
            )

    return {
        "attempted_paths": attempted_paths,
        "removed_paths": removed_paths,
        "deleted_memory_ids": deleted_memory_ids,
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


def _raise_on_enqueue_drop(
    enqueue_result: Dict[str, Any], *, operation: str
) -> None:
    if not isinstance(enqueue_result, dict):
        return
    if not enqueue_result.get("dropped"):
        return

    reason = str(enqueue_result.get("reason") or "queue_full")
    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if reason == "queue_full"
        else status.HTTP_409_CONFLICT
    )
    detail: Dict[str, Any] = {
        "error": "index_job_enqueue_failed",
        "reason": reason,
        "operation": operation,
    }
    job_id = enqueue_result.get("job_id")
    if isinstance(job_id, str) and job_id:
        detail["job_id"] = job_id
    raise HTTPException(status_code=status_code, detail=detail)


def _sanitize_search_event(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    degrade_reasons_raw = raw.get("degrade_reasons")
    degrade_reasons = (
        [str(item) for item in degrade_reasons_raw if isinstance(item, str) and item.strip()]
        if isinstance(degrade_reasons_raw, list)
        else []
    )

    return {
        "timestamp": str(raw.get("timestamp") or _utc_iso_now()),
        "mode_requested": str(raw.get("mode_requested") or "hybrid"),
        "mode_applied": str(raw.get("mode_applied") or "hybrid"),
        "latency_ms": round(float(raw.get("latency_ms") or 0.0), 3),
        "degraded": bool(raw.get("degraded")),
        "degrade_reasons": degrade_reasons,
        "session_count": _safe_int(raw.get("session_count") or 0),
        "global_count": _safe_int(raw.get("global_count") or 0),
        "returned_count": _safe_int(raw.get("returned_count") or 0),
        "dedup_dropped": _safe_int(raw.get("dedup_dropped") or 0),
        "session_contributed": _safe_int(raw.get("session_contributed") or 0),
        "global_contributed": _safe_int(raw.get("global_contributed") or 0),
        "intent": str(raw.get("intent") or "unknown"),
        "intent_applied": str(raw.get("intent_applied") or "unknown"),
        "strategy_template": str(raw.get("strategy_template") or "default"),
        "strategy_template_applied": str(
            raw.get("strategy_template_applied") or "default"
        ),
    }


def _serialize_search_events(events: List[Dict[str, Any]]) -> str:
    payload = [_sanitize_search_event(item) for item in events]
    compact = [item for item in payload if isinstance(item, dict)]
    return json.dumps(compact[-_SEARCH_EVENT_LIMIT:], ensure_ascii=False, separators=(",", ":"))


def _deserialize_search_events(raw: Optional[str]) -> List[Dict[str, Any]]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    loaded: List[Dict[str, Any]] = []
    for item in payload[-_SEARCH_EVENT_LIMIT:]:
        normalized = _sanitize_search_event(item)
        if normalized is not None:
            loaded.append(normalized)
    return loaded


async def _ensure_search_events_loaded(client: Any) -> None:
    global _search_events_loaded
    async with _search_events_guard:
        if _search_events_loaded:
            return

    getter = getattr(client, "get_runtime_meta", None)
    loaded_events: List[Dict[str, Any]] = []
    if callable(getter):
        try:
            raw_payload = await getter(_SEARCH_EVENTS_META_KEY)
            loaded_events = _deserialize_search_events(raw_payload)
        except Exception:
            loaded_events = []

    async with _search_events_guard:
        if _search_events_loaded:
            return
        _search_events.clear()
        _search_events.extend(loaded_events[-_SEARCH_EVENT_LIMIT:])
        _search_events_loaded = True


async def _persist_search_events_locked(
    client: Any, events: List[Dict[str, Any]]
) -> None:
    setter = getattr(client, "set_runtime_meta", None)
    if not callable(setter):
        return
    try:
        await setter(_SEARCH_EVENTS_META_KEY, _serialize_search_events(events))
    except Exception:
        # Observability persistence must never block online requests.
        return


def _normalize_search_filters(raw_filters: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    if not isinstance(raw_filters, dict):
        return normalized

    domain = raw_filters.get("domain")
    if isinstance(domain, str) and domain.strip():
        normalized["domain"] = domain.strip()

    path_prefix = raw_filters.get("path_prefix")
    if isinstance(path_prefix, str) and path_prefix.strip():
        normalized["path_prefix"] = path_prefix.strip()

    max_priority = raw_filters.get("max_priority", raw_filters.get("priority"))
    if max_priority is not None:
        parsed_priority: Optional[int] = None
        if isinstance(max_priority, bool):
            parsed_priority = None
        elif isinstance(max_priority, int):
            parsed_priority = max_priority
        elif isinstance(max_priority, float):
            if max_priority.is_integer():
                parsed_priority = int(max_priority)
        elif isinstance(max_priority, str):
            priority_raw = max_priority.strip()
            if priority_raw and priority_raw.lstrip("+-").isdigit():
                parsed_priority = int(priority_raw)
        if parsed_priority is None:
            raise ValueError("filters.max_priority must be an integer")
        normalized["max_priority"] = parsed_priority

    updated_after = raw_filters.get("updated_after")
    if isinstance(updated_after, str) and updated_after.strip():
        normalized["updated_after"] = updated_after.strip()

    scope_hint = raw_filters.get("scope_hint")
    if scope_hint is not None:
        if not isinstance(scope_hint, str):
            raise ValueError("filters.scope_hint must be a string")
        normalized_scope_hint = scope_hint.strip()
        if normalized_scope_hint:
            normalized["scope_hint"] = normalized_scope_hint

    return normalized


def _normalize_scope_hint(scope_hint: Optional[Any]) -> Dict[str, Any]:
    if scope_hint is None:
        return {
            "provided": False,
            "raw": None,
            "domain": None,
            "path_prefix": None,
            "strategy": "none",
        }

    raw_value = str(scope_hint).strip()
    if not raw_value:
        return {
            "provided": False,
            "raw": raw_value,
            "domain": None,
            "path_prefix": None,
            "strategy": "none",
        }

    if "://" in raw_value:
        match = _SCOPE_URI_PATTERN.match(raw_value)
        if not match:
            raise ValueError("scope_hint must be a valid URI/domain/path prefix string")
        domain = str(match.group(1) or "").strip().lower()
        path_prefix = str(match.group(2) or "").strip("/")
        if domain not in _VALID_DOMAINS:
            raise ValueError(
                f"Unknown scope_hint domain '{domain}'. "
                f"Valid domains: {', '.join(_VALID_DOMAINS)}"
            )
        return {
            "provided": True,
            "raw": raw_value,
            "domain": domain,
            "path_prefix": path_prefix or None,
            "strategy": "uri_prefix" if path_prefix else "domain_uri",
        }

    lowered = raw_value.lower()
    if lowered in _VALID_DOMAINS:
        return {
            "provided": True,
            "raw": raw_value,
            "domain": lowered,
            "path_prefix": None,
            "strategy": "domain",
        }

    path_prefix = raw_value.strip("/")
    return {
        "provided": bool(path_prefix),
        "raw": raw_value,
        "domain": None,
        "path_prefix": path_prefix or None,
        "strategy": "path_prefix" if path_prefix else "none",
    }


def _merge_scope_hint_with_filters(
    *,
    normalized_filters: Dict[str, Any],
    scope_hint: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    merged = dict(normalized_filters)
    merged.pop("scope_hint", None)

    provided = bool(scope_hint.get("provided"))
    hint_domain = scope_hint.get("domain")
    hint_path_prefix = scope_hint.get("path_prefix")
    conflicts: List[str] = []
    applied = False
    domain_conflict = False

    if provided and isinstance(hint_domain, str) and hint_domain:
        existing_domain = merged.get("domain")
        if existing_domain is None:
            merged["domain"] = hint_domain
            applied = True
        elif str(existing_domain) != hint_domain:
            conflicts.append("domain_conflict")
            domain_conflict = True

    if provided and isinstance(hint_path_prefix, str) and hint_path_prefix:
        if not domain_conflict:
            existing_prefix = merged.get("path_prefix")
            hint_prefix_norm = hint_path_prefix.strip("/")
            if existing_prefix is None:
                merged["path_prefix"] = hint_prefix_norm
                applied = True
            else:
                existing_prefix_norm = str(existing_prefix).strip("/")
                if not existing_prefix_norm:
                    merged["path_prefix"] = hint_prefix_norm
                    applied = True
                elif existing_prefix_norm == hint_prefix_norm:
                    pass
                elif existing_prefix_norm.startswith(hint_prefix_norm):
                    pass
                elif hint_prefix_norm.startswith(existing_prefix_norm):
                    merged["path_prefix"] = hint_prefix_norm
                    applied = True
                else:
                    conflicts.append("path_prefix_conflict")

    resolution = {
        "provided": provided,
        "raw": scope_hint.get("raw"),
        "strategy": (
            str(scope_hint.get("strategy") or "none")
            if applied
            else ("filters_preferred" if provided else "none")
        ),
        "applied": applied,
        "effective": {
            "domain": merged.get("domain"),
            "path_prefix": merged.get("path_prefix"),
        },
        "conflicts": conflicts,
    }
    return merged, resolution


async def _build_sm_lite_stats() -> Dict[str, Any]:
    session_cache_stats = await runtime_state.session_cache.summary()
    flush_tracker_stats = await runtime_state.flush_tracker.summary()
    promotion_stats = await runtime_state.promotion_tracker.summary()
    return {
        "storage": "runtime_ephemeral",
        "promotion_path": "compact_context + auto_flush",
        "session_cache": session_cache_stats,
        "flush_tracker": flush_tracker_stats,
        "promotion": promotion_stats,
    }


def _session_row_to_result(row: Dict[str, Any]) -> Dict[str, Any]:
    uri = str(row.get("uri") or "")
    domain = "core"
    path = uri
    if "://" in uri:
        domain, path = uri.split("://", 1)
    final_score = float(row.get("score") or 0.0)
    keyword_score = float(row.get("keyword_score") or 0.0)
    priority_value = row.get("priority")
    updated_at = row.get("updated_at")

    return {
        "uri": uri,
        "memory_id": row.get("memory_id"),
        "chunk_id": None,
        "snippet": str(row.get("snippet") or ""),
        "char_range": None,
        "scores": {
            "vector": 0.0,
            "text": round(keyword_score, 6),
            "priority": 0.0,
            "recency": 0.0,
            "path_prefix": 0.0,
            "rerank": 0.0,
            "final": round(final_score, 6),
        },
        "metadata": {
            "domain": domain,
            "path": path,
            "priority": priority_value,
            "disclosure": None,
            "updated_at": updated_at,
            "source": row.get("source", "session_queue"),
            "match_type": row.get("match_type", "session_queue"),
        },
    }


def _merge_session_global_results(
    *,
    session_results: List[Dict[str, Any]],
    global_results: List[Dict[str, Any]],
    limit: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    dedup_dropped = 0
    session_contributed = 0
    global_contributed = 0

    for index, row in enumerate(session_results + global_results):
        source_bucket = "session" if index < len(session_results) else "global"
        uri = str(row.get("uri") or "")
        if not uri or uri in seen:
            dedup_dropped += 1
            continue
        seen.add(uri)
        merged.append(row)
        if source_bucket == "session":
            session_contributed += 1
        else:
            global_contributed += 1
        if len(merged) >= max(1, limit):
            break
    return merged, {
        "session_candidates": len(session_results),
        "global_candidates": len(global_results),
        "merged_candidates": len(merged),
        "returned_candidates": len(merged),
        "dedup_dropped": dedup_dropped,
        "session_contributed": session_contributed,
        "global_contributed": global_contributed,
    }


def _build_search_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not events:
        return {
            "window_size": 0,
            "total_queries": 0,
            "degraded_queries": 0,
            "cache_hit_queries": 0,
            "cache_hit_ratio": 0.0,
            "dedup_dropped_total": 0,
            "avg_dedup_dropped": 0.0,
            "session_contributed_total": 0,
            "global_contributed_total": 0,
            "latency_ms": {"avg": 0.0, "p95": 0.0, "max": 0.0},
            "mode_breakdown": {},
            "intent_breakdown": {},
            "strategy_hit_breakdown": {},
            "top_degrade_reasons": [],
            "last_query_at": None,
        }

    latencies = [float(item.get("latency_ms") or 0.0) for item in events]
    degraded_queries = sum(1 for item in events if bool(item.get("degraded")))
    cache_hit_queries = sum(1 for item in events if int(item.get("session_count") or 0) > 0)
    dedup_dropped_total = sum(max(0, int(item.get("dedup_dropped") or 0)) for item in events)
    session_contributed_total = sum(
        max(0, int(item.get("session_contributed") or 0)) for item in events
    )
    global_contributed_total = sum(
        max(0, int(item.get("global_contributed") or 0)) for item in events
    )

    mode_counts = Counter(str(item.get("mode_applied") or "unknown") for item in events)
    intent_counts = Counter(
        str(item.get("intent_applied") or item.get("intent") or "unknown")
        for item in events
    )
    strategy_counts = Counter(
        str(
            item.get("strategy_template_applied")
            or item.get("strategy_template")
            or "default"
        )
        for item in events
    )
    degrade_reason_counts = Counter()
    for item in events:
        reasons = item.get("degrade_reasons") or []
        if isinstance(reasons, list):
            for reason in reasons:
                if isinstance(reason, str) and reason:
                    degrade_reason_counts[reason] += 1

    return {
        "window_size": _SEARCH_EVENT_LIMIT,
        "total_queries": len(events),
        "degraded_queries": degraded_queries,
        "cache_hit_queries": cache_hit_queries,
        "cache_hit_ratio": round(cache_hit_queries / max(1, len(events)), 6),
        "dedup_dropped_total": dedup_dropped_total,
        "avg_dedup_dropped": round(dedup_dropped_total / max(1, len(events)), 6),
        "session_contributed_total": session_contributed_total,
        "global_contributed_total": global_contributed_total,
        "latency_ms": {
            "avg": round(sum(latencies) / max(1, len(latencies)), 3),
            "p95": round(_safe_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "mode_breakdown": dict(mode_counts),
        "intent_breakdown": dict(intent_counts),
        "strategy_hit_breakdown": dict(strategy_counts),
        "top_degrade_reasons": [
            {"reason": reason, "count": count}
            for reason, count in degrade_reason_counts.most_common(5)
        ],
        "last_query_at": events[-1].get("timestamp"),
    }


def _build_index_latency_summary(worker_status: Dict[str, Any]) -> Dict[str, Any]:
    recent_jobs = worker_status.get("recent_jobs")
    if not isinstance(recent_jobs, list):
        return {"samples": 0, "avg_ms": 0.0, "p95_ms": 0.0, "last_ms": 0.0}

    durations: List[float] = []
    for job in recent_jobs:
        if not isinstance(job, dict):
            continue
        started_at = _parse_iso_ts(job.get("started_at"))
        finished_at = _parse_iso_ts(job.get("finished_at"))
        if started_at is None or finished_at is None:
            continue
        duration_ms = max(0.0, (finished_at - started_at).total_seconds() * 1000.0)
        durations.append(duration_ms)

    if not durations:
        return {"samples": 0, "avg_ms": 0.0, "p95_ms": 0.0, "last_ms": 0.0}

    return {
        "samples": len(durations),
        "avg_ms": round(sum(durations) / len(durations), 3),
        "p95_ms": round(_safe_percentile(durations, 0.95), 3),
        "last_ms": round(durations[0], 3),
    }


def _sanitize_cleanup_query_event(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    try:
        query_ms = round(float(raw.get("query_ms") or 0.0), 3)
    except (TypeError, ValueError):
        query_ms = 0.0
    try:
        candidate_count = int(raw.get("candidate_count") or 0)
    except (TypeError, ValueError):
        candidate_count = 0

    return {
        "timestamp": str(raw.get("timestamp") or _utc_iso_now()),
        "query_ms": query_ms,
        "slow": bool(raw.get("slow")),
        "candidate_count": candidate_count,
        "memory_index_hit": bool(raw.get("memory_index_hit")),
        "path_index_hit": bool(raw.get("path_index_hit")),
        "full_scan": bool(raw.get("full_scan")),
        "degraded": bool(raw.get("degraded")),
    }


def _build_cleanup_query_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not events:
        return {
            "window_size": 0,
            "slow_threshold_ms": round(_CLEANUP_QUERY_SLOW_MS, 3),
            "total_queries": 0,
            "slow_queries": 0,
            "slow_query_ratio": 0.0,
            "degraded_queries": 0,
            "index_hit_queries": 0,
            "index_hit_ratio": 0.0,
            "memory_index_hit_queries": 0,
            "path_index_hit_queries": 0,
            "full_scan_queries": 0,
            "latency_ms": {"avg": 0.0, "p95": 0.0, "max": 0.0},
            "last_query_at": None,
        }

    latencies = [float(item.get("query_ms") or 0.0) for item in events]
    slow_queries = sum(1 for item in events if bool(item.get("slow")))
    degraded_queries = sum(1 for item in events if bool(item.get("degraded")))
    memory_index_hits = sum(1 for item in events if bool(item.get("memory_index_hit")))
    path_index_hits = sum(1 for item in events if bool(item.get("path_index_hit")))
    full_scan_queries = sum(1 for item in events if bool(item.get("full_scan")))
    any_index_hits = sum(
        1
        for item in events
        if bool(item.get("memory_index_hit")) or bool(item.get("path_index_hit"))
    )

    return {
        "window_size": _CLEANUP_QUERY_EVENT_LIMIT,
        "slow_threshold_ms": round(_CLEANUP_QUERY_SLOW_MS, 3),
        "total_queries": len(events),
        "slow_queries": slow_queries,
        "slow_query_ratio": round(slow_queries / max(1, len(events)), 6),
        "degraded_queries": degraded_queries,
        "index_hit_queries": any_index_hits,
        "index_hit_ratio": round(any_index_hits / max(1, len(events)), 6),
        "memory_index_hit_queries": memory_index_hits,
        "path_index_hit_queries": path_index_hits,
        "full_scan_queries": full_scan_queries,
        "latency_ms": {
            "avg": round(sum(latencies) / max(1, len(latencies)), 3),
            "p95": round(_safe_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "last_query_at": events[-1].get("timestamp"),
    }


@router.post("/import/prepare")
async def prepare_external_import(payload: ImportPrepareRequest):
    actor_id = str(payload.actor_id or "").strip()
    session_id = str(payload.session_id or "").strip()
    source = str(payload.source or "external_import").strip() or "external_import"
    reason_text = str(payload.reason or "manual_import").strip() or "manual_import"
    domain = _validate_import_domain(payload.domain)
    parent_path = _normalize_import_parent_path(payload.parent_path)

    if parent_path:
        client = get_sqlite_client()
        parent = None
        try:
            parent = await client.get_memory_by_path(
                parent_path,
                domain,
                reinforce_access=False,
            )
        except TypeError:
            parent = await client.get_memory_by_path(parent_path, domain)
        if parent is None:
            await _record_import_learn_event(
                event_type="reject",
                operation="import_prepare",
                decision="rejected",
                reason="parent_path_not_found",
                source=source,
                session_id=session_id,
                actor_id=actor_id,
                metadata={"domain": domain, "parent_path": parent_path},
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "external_import_prepare_rejected",
                    "reason": "parent_path_not_found",
                    "domain": domain,
                    "parent_path": parent_path,
                },
            )

    guard = await _get_external_import_guard()
    guard_result = guard.validate_batch(
        file_paths=payload.file_paths,
        actor_id=actor_id,
        session_id=session_id,
    )
    if not bool(guard_result.get("ok")):
        await _record_import_learn_event(
            event_type="reject",
            operation="import_prepare",
            decision="rejected",
            reason=str(guard_result.get("reason") or "guard_rejected"),
            source=source,
            session_id=session_id,
            actor_id=actor_id,
            metadata={
                "domain": domain,
                "parent_path": parent_path,
                "requested_file_count": _safe_non_negative_int(
                    guard_result.get("requested_file_count")
                ),
            },
        )
        raise _http_error_for_import_guard(guard_result)

    job_id = f"import-{uuid.uuid4().hex[:12]}"
    policy_snapshot = _build_external_import_policy_snapshot(guard)
    title_suffix = job_id.rsplit("-", 1)[-1]
    allowed_files = (
        guard_result.get("allowed_files")
        if isinstance(guard_result.get("allowed_files"), list)
        else []
    )
    prepared_files: List[Dict[str, Any]] = []
    read_failures: List[Dict[str, Any]] = []
    total_bytes = 0

    for index, file_info in enumerate(allowed_files):
        if not isinstance(file_info, dict):
            continue
        source_path = str(file_info.get("path") or "").strip()
        resolved_path = str(file_info.get("resolved_path") or "").strip()
        extension = str(file_info.get("extension") or "").strip().lower()
        size_bytes = _safe_non_negative_int(file_info.get("size_bytes"))
        if not resolved_path:
            read_failures.append(
                {
                    "path": source_path,
                    "reason": "resolved_path_missing",
                }
            )
            continue
        try:
            content = Path(resolved_path).read_text(encoding="utf-8")
        except Exception as exc:
            read_failures.append(
                {
                    "path": source_path,
                    "reason": "file_read_failed",
                    "detail": type(exc).__name__,
                }
            )
            continue

        source_hash = _build_import_source_hash(content)
        title = _sanitize_import_title(
            source_path or resolved_path,
            source_hash,
            suffix=title_suffix,
        )
        target_path, target_uri = _build_import_target_uri(
            domain=domain,
            parent_path=parent_path,
            title=title,
        )
        prepared_files.append(
            {
                "file_index": index,
                "source_path": source_path,
                "resolved_path": resolved_path,
                "extension": extension,
                "size_bytes": size_bytes,
                "source_hash": source_hash,
                "title": title,
                "target_path": target_path,
                "target_uri": target_uri,
                "preview": _trim_import_preview(content),
                "content": content,
            }
        )
        total_bytes += size_bytes

    if read_failures:
        await _record_import_learn_event(
            event_type="reject",
            operation="import_prepare",
            decision="rejected",
            reason="file_read_failed",
            source=source,
            session_id=session_id,
            actor_id=actor_id,
            metadata={
                "domain": domain,
                "parent_path": parent_path,
                "read_failure_count": len(read_failures),
            },
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "external_import_prepare_rejected",
                "reason": "file_read_failed",
                "read_failures": read_failures,
            },
        )

    now_iso = _utc_iso_now()
    job_payload: Dict[str, Any] = {
        "job_id": job_id,
        "job_type": "import",
        "status": "prepared",
        "created_at": now_iso,
        "updated_at": now_iso,
        "operation": "import_prepare",
        "dry_run": True,
        "actor_id": actor_id,
        "session_id": session_id,
        "source": source,
        "reason": reason_text,
        "reason_text": reason_text,
        "domain": domain,
        "parent_path": parent_path,
        "priority": int(payload.priority),
        "file_count": len(prepared_files),
        "total_bytes": total_bytes,
        "guard": {
            "reason": str(guard_result.get("reason") or "ok"),
            "rate_limit": guard_result.get("rate_limit")
            if isinstance(guard_result.get("rate_limit"), dict)
            else None,
            "rate_limit_storage": str(
                guard_result.get("rate_limit_storage") or "process_memory"
            ),
            "require_shared_rate_limit": bool(
                guard_result.get("require_shared_rate_limit")
            ),
            "max_files": _safe_non_negative_int(guard_result.get("max_files")),
            "max_total_bytes": _safe_non_negative_int(
                guard_result.get("max_total_bytes")
            ),
            "policy": policy_snapshot,
        },
        "files": prepared_files,
        "created_memories": [],
        "side_effects": {
            "audit_required": True,
            "scope": [
                "created_memory_ids",
                "index_chunks",
                "runtime_audit_events",
            ],
            "note": "rollback_only_covers_created_memory_ids",
        },
        "rollback": {
            "status": "not_started",
            "rolled_back_count": 0,
            "error_count": 0,
            "errors": [],
            "completed_at": None,
        },
    }
    await _put_import_job(job_payload)
    await _record_import_learn_event(
        event_type="import",
        operation="import_prepare",
        decision="accepted",
        reason="prepared",
        source=source,
        session_id=session_id,
        actor_id=actor_id,
        batch_id=job_id,
        metadata={
            "domain": domain,
            "parent_path": parent_path,
            "file_count": len(prepared_files),
            "total_bytes": total_bytes,
            "policy_hash": str(policy_snapshot.get("policy_hash") or ""),
        },
    )

    return {
        "ok": True,
        "status": "prepared",
        "job_id": job_id,
        "job_type": "import",
        "dry_run": True,
        "file_count": len(prepared_files),
        "total_bytes": total_bytes,
        "job": _public_import_job_payload(job_payload),
    }


@router.post("/import/execute")
async def execute_external_import(payload: ImportExecuteRequest):
    job_id = str(payload.job_id or "").strip()
    job, transition_error = await _transition_import_job_status(
        job_id,
        allowed_from={"prepared"},
        next_status="executing",
    )
    if transition_error == "job_not_found":
        raise HTTPException(status_code=404, detail={"error": "import_job_not_found"})
    if transition_error:
        current_status = (
            str(job.get("status") or "unknown")
            if isinstance(job, dict)
            else "unknown"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "import_job_invalid_status",
                "reason": transition_error,
                "status": current_status,
                "job_id": job_id,
            },
        )
    if not isinstance(job, dict):
        raise HTTPException(status_code=404, detail={"error": "import_job_not_found"})

    files = job.get("files") if isinstance(job.get("files"), list) else []
    if not files:
        job["status"] = "failed"
        job["failure"] = {
            "reason": "prepared_files_missing",
            "updated_at": _utc_iso_now(),
        }
        await _update_import_job(job_id, job)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "external_import_execute_rejected",
                "reason": "prepared_files_missing",
                "job_id": job_id,
            },
        )

    client = get_sqlite_client()
    domain = _validate_import_domain(str(job.get("domain") or "notes"))
    parent_path = _normalize_import_parent_path(str(job.get("parent_path") or ""))
    priority = _safe_non_negative_int(job.get("priority"))
    actor_id = str(job.get("actor_id") or "").strip() or None
    session_id = str(job.get("session_id") or "").strip() or None
    source = str(job.get("source") or "external_import").strip() or "external_import"
    write_lane_session_id = (
        str(session_id or "").strip() or f"maintenance.import:{job_id}"
    )

    validated_entries: List[Dict[str, Any]] = []
    source_mismatch: List[Dict[str, Any]] = []
    guard_blocked: List[Dict[str, Any]] = []
    guard_errors: List[Dict[str, Any]] = []

    for item in files:
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("source_path") or "").strip()
        expected_hash = str(item.get("source_hash") or "").strip()
        title = str(item.get("title") or "").strip()
        snapshot_content = item.get("content")
        if not expected_hash or not title or not isinstance(snapshot_content, str):
            source_mismatch.append(
                {
                    "path": source_path,
                    "reason": "prepared_snapshot_incomplete",
                }
            )
            continue
        current_hash = _build_import_source_hash(snapshot_content)
        if not hmac.compare_digest(current_hash, expected_hash):
            source_mismatch.append(
                {
                    "path": source_path,
                    "reason": "prepared_snapshot_hash_mismatch",
                }
            )
            continue

        try:
            guard_decision = await client.write_guard(
                content=snapshot_content,
                domain=domain,
                path_prefix=parent_path or None,
            )
        except Exception as exc:
            guard_errors.append(
                {
                    "path": source_path,
                    "reason": "write_guard_unavailable",
                    "detail": type(exc).__name__,
                }
            )
            continue

        action = str(guard_decision.get("action") or "UNKNOWN").upper()
        if action != "ADD":
            guard_blocked.append(
                {
                    "path": source_path,
                    "reason": f"write_guard_blocked:{action.lower()}",
                    "guard_action": action,
                    "guard_method": str(guard_decision.get("method") or "unknown"),
                }
            )
            continue

        validated_entries.append(
            {
                "source_path": source_path,
                "title": title,
                "content": snapshot_content,
                "target_path": str(item.get("target_path") or ""),
                "target_uri": str(item.get("target_uri") or ""),
                "source_hash": expected_hash,
            }
        )

    if source_mismatch or guard_errors or guard_blocked:
        reason = (
            "prepared_snapshot_invalid"
            if source_mismatch
            else ("write_guard_unavailable" if guard_errors else "write_guard_blocked")
        )
        job["status"] = "failed"
        job["failure"] = {
            "reason": reason,
            "source_mismatch": source_mismatch,
            "guard_errors": guard_errors,
            "guard_blocked": guard_blocked,
            "updated_at": _utc_iso_now(),
        }
        await _update_import_job(job_id, job)
        await _record_import_learn_event(
            event_type="reject",
            operation="import_execute",
            decision="rejected",
            reason=reason,
            source=source,
            session_id=session_id,
            actor_id=actor_id,
            batch_id=job_id,
            metadata={
                "domain": domain,
                "source_mismatch_count": len(source_mismatch),
                "guard_error_count": len(guard_errors),
                "guard_blocked_count": len(guard_blocked),
            },
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "external_import_execute_rejected",
                "reason": reason,
                "job_id": job_id,
                "source_mismatch": source_mismatch,
                "guard_errors": guard_errors,
                "guard_blocked": guard_blocked,
            },
        )

    created_memories: List[Dict[str, Any]] = []
    for entry in validated_entries:
        try:
            async def _write_task_create(
                _entry: Dict[str, Any] = entry,
            ) -> Dict[str, Any]:
                return await client.create_memory(
                    parent_path=parent_path,
                    content=str(_entry.get("content") or ""),
                    priority=priority,
                    title=str(_entry.get("title") or ""),
                    domain=domain,
                )

            created = await _run_write_lane(
                "maintenance.import.execute.create_memory",
                _write_task_create,
                session_id=write_lane_session_id,
            )
        except Exception as exc:
            rollback_summary = await _rollback_import_created_memories(
                client=client,
                created_memories=created_memories,
                session_id=write_lane_session_id,
                job_id=job_id,
            )
            job["status"] = "failed"
            job["created_memories"] = created_memories
            job["rollback"] = rollback_summary
            job["failure"] = {
                "reason": "create_memory_failed",
                "detail": str(exc) or type(exc).__name__,
                "updated_at": _utc_iso_now(),
            }
            await _update_import_job(job_id, job)
            await _record_import_learn_event(
                event_type="reject",
                operation="import_execute",
                decision="rejected",
                reason="create_memory_failed",
                source=source,
                session_id=session_id,
                actor_id=actor_id,
                batch_id=job_id,
                metadata={
                    "domain": domain,
                    "created_count": len(created_memories),
                    "rollback_count": rollback_summary.get("rolled_back_count"),
                },
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "external_import_execute_failed",
                    "reason": "create_memory_failed",
                    "job_id": job_id,
                    "created_count": len(created_memories),
                    "rollback": rollback_summary,
                },
            )

        created_memories.append(
            {
                "memory_id": _safe_non_negative_int(created.get("id")),
                "uri": str(created.get("uri") or ""),
                "path": str(created.get("path") or ""),
                "source_path": str(entry.get("source_path") or ""),
                "source_hash": str(entry.get("source_hash") or ""),
            }
        )

    job["status"] = "executed"
    job["created_memories"] = created_memories
    job["failure"] = None
    job["rollback"] = {
        "status": "not_started",
        "rolled_back_count": 0,
        "error_count": 0,
        "errors": [],
        "completed_at": None,
    }
    await _update_import_job(job_id, job)
    await _record_import_learn_event(
        event_type="import",
        operation="import_execute",
        decision="executed",
        reason="executed",
        source=source,
        session_id=session_id,
        actor_id=actor_id,
        batch_id=job_id,
        metadata={
            "domain": domain,
            "created_count": len(created_memories),
            "file_count": len(validated_entries),
        },
    )

    return {
        "ok": True,
        "status": "executed",
        "job_id": job_id,
        "job_type": _normalize_import_job_type(job.get("job_type")),
        "created_count": len(created_memories),
        "created_memories": created_memories,
        "job": _public_import_job_payload(job),
    }


async def _load_job_from_pool(
    job_id: str, *, prefer_learn: bool, allow_fallback: bool
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if prefer_learn:
        primary_fetcher = _get_learn_job
        primary_pool = "learn"
        fallback_fetcher = _get_import_job
        fallback_pool = "import"
    else:
        primary_fetcher = _get_import_job
        primary_pool = "import"
        fallback_fetcher = _get_learn_job
        fallback_pool = "learn"

    payload = await primary_fetcher(job_id)
    if isinstance(payload, dict):
        return payload, primary_pool
    if not allow_fallback:
        return None, None
    fallback_payload = await fallback_fetcher(job_id)
    if isinstance(fallback_payload, dict):
        return fallback_payload, fallback_pool
    return None, None


def _transition_and_update_by_pool(
    pool: str,
) -> Tuple[
    Callable[..., Awaitable[Tuple[Optional[Dict[str, Any]], Optional[str]]]],
    Callable[[str, Dict[str, Any]], Awaitable[None]],
]:
    if pool == "learn":
        return _transition_learn_job_status, _update_learn_job
    return _transition_import_job_status, _update_import_job


async def _rollback_job(
    *,
    job_id: str,
    payload: ImportRollbackRequest,
    prefer_learn: bool,
    allow_fallback: bool,
    not_found_error: str,
) -> Dict[str, Any]:
    current_job, job_pool = await _load_job_from_pool(
        job_id,
        prefer_learn=prefer_learn,
        allow_fallback=allow_fallback,
    )
    if not isinstance(current_job, dict) or not isinstance(job_pool, str):
        raise HTTPException(status_code=404, detail={"error": not_found_error})

    current_status = str(current_job.get("status") or "unknown")
    current_job_type = _normalize_import_job_type(current_job.get("job_type"))
    created_memories = (
        current_job.get("created_memories")
        if isinstance(current_job.get("created_memories"), list)
        else []
    )
    current_created_namespace_memories = (
        _normalize_created_namespace_memories(current_job.get("created_namespace_memories"))
        if current_job_type == "learn"
        else []
    )

    if current_status == "rolled_back":
        return {
            "ok": True,
            "status": "rolled_back",
            "job_id": str(current_job.get("job_id") or job_id),
            "job_type": current_job_type,
            "job": _public_import_job_payload(current_job),
        }

    if current_status not in {"executed", "failed", "rollback_failed"}:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "import_job_invalid_status",
                "reason": f"invalid_status:{current_status}",
                "job_id": job_id,
            },
        )
    has_rollback_targets = bool(created_memories) or bool(current_created_namespace_memories)
    if not has_rollback_targets:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "import_job_no_rollback_targets",
                "job_id": job_id,
            },
        )

    transition_job_status, update_job = _transition_and_update_by_pool(job_pool)
    transitioned_job, transition_error = await transition_job_status(
        job_id,
        allowed_from={"executed", "failed", "rollback_failed"},
        next_status="rolling_back",
    )
    if transition_error:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "import_job_invalid_status",
                "reason": transition_error,
                "job_id": job_id,
            },
        )
    if not isinstance(transitioned_job, dict):
        raise HTTPException(status_code=404, detail={"error": not_found_error})

    client = get_sqlite_client()
    job_type = _normalize_import_job_type(transitioned_job.get("job_type"))
    created_namespace_memories = []
    if job_type == "learn":
        created_namespace_memories = _normalize_created_namespace_memories(
            transitioned_job.get("created_namespace_memories")
        )

    rollback_summary = await _rollback_import_created_memories(
        client=client,
        created_memories=created_memories,
        created_namespace_memories=created_namespace_memories,
        session_id=str(transitioned_job.get("session_id") or "").strip(),
        job_id=str(transitioned_job.get("job_id") or job_id),
    )
    has_errors = bool(rollback_summary.get("error_count"))
    final_status = "rollback_failed" if has_errors else "rolled_back"
    transitioned_job["status"] = final_status
    transitioned_job["rollback"] = rollback_summary
    await update_job(job_id, transitioned_job)

    rollback_operation = "learn_rollback" if job_type == "learn" else "import_rollback"
    await _record_import_learn_event(
        event_type="rollback",
        operation=rollback_operation,
        decision="rejected" if has_errors else "rolled_back",
        reason=(
            "rollback_failed"
            if has_errors
            else str(payload.reason or "manual_rollback").strip()
        ),
        source=str(transitioned_job.get("source") or "external_import"),
        session_id=str(transitioned_job.get("session_id") or ""),
        actor_id=str(transitioned_job.get("actor_id") or "") or None,
        batch_id=str(transitioned_job.get("job_id") or job_id),
        metadata={
            "rolled_back_count": _safe_non_negative_int(
                rollback_summary.get("rolled_back_count")
            ),
            "error_count": _safe_non_negative_int(rollback_summary.get("error_count")),
            "side_effects_audit_required": bool(
                rollback_summary.get("side_effects_audit_required")
            ),
            "residual_artifacts_review_required": bool(
                rollback_summary.get("residual_artifacts_review_required")
            ),
            "namespace_cleanup_skipped_count": _safe_non_negative_int(
                (
                    rollback_summary.get("namespace_cleanup") or {}
                ).get("skipped_count")
                if isinstance(rollback_summary.get("namespace_cleanup"), dict)
                else 0
            ),
        },
    )

    return {
        "ok": not has_errors,
        "status": final_status,
        "job_id": str(transitioned_job.get("job_id") or job_id),
        "job_type": job_type,
        "rollback": rollback_summary,
        "job": _public_import_job_payload(transitioned_job),
    }


@router.get("/import/jobs/{job_id}")
async def get_external_import_job(job_id: str):
    payload, _ = await _load_job_from_pool(
        job_id,
        prefer_learn=False,
        allow_fallback=True,
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=404, detail={"error": "import_job_not_found"})
    job_type = _normalize_import_job_type(payload.get("job_type"))
    return {
        "ok": True,
        "job_id": str(payload.get("job_id") or job_id),
        "job_type": job_type,
        "status": str(payload.get("status") or "unknown"),
        "job": _public_import_job_payload(payload),
    }


@router.get("/learn/jobs/{job_id}")
async def get_explicit_learn_job(job_id: str):
    payload = await _get_learn_job(job_id)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=404, detail={"error": "learn_job_not_found"})
    return {
        "ok": True,
        "job_id": str(payload.get("job_id") or job_id),
        "job_type": "learn",
        "status": str(payload.get("status") or "unknown"),
        "job": _public_import_job_payload(payload),
    }


@router.post("/learn/jobs/{job_id}/rollback")
async def rollback_explicit_learn_job(job_id: str, payload: ImportRollbackRequest):
    return await _rollback_job(
        job_id=job_id,
        payload=payload,
        prefer_learn=True,
        allow_fallback=False,
        not_found_error="learn_job_not_found",
    )


@router.post("/import/jobs/{job_id}/rollback")
async def rollback_external_import_job(job_id: str, payload: ImportRollbackRequest):
    return await _rollback_job(
        job_id=job_id,
        payload=payload,
        prefer_learn=False,
        allow_fallback=True,
        not_found_error="import_job_not_found",
    )


@router.post("/learn/trigger")
async def trigger_explicit_learn(payload: LearnTriggerRequest):
    domain = _validate_import_domain(payload.domain)
    normalized_source = str(payload.source or "manual_review").strip() or "manual_review"
    normalized_session_id = str(payload.session_id or "").strip()
    normalized_reason = str(payload.reason or "").strip()
    normalized_actor_id = (str(payload.actor_id).strip() if payload.actor_id is not None else "") or None
    normalized_path_prefix = str(payload.path_prefix or "corrections").strip() or "corrections"
    execute = bool(payload.execute)
    explicit_learn_service = await _resolve_explicit_learn_service()

    result = await explicit_learn_service(
        content=str(payload.content or ""),
        source=normalized_source,
        reason=normalized_reason,
        session_id=normalized_session_id,
        actor_id=normalized_actor_id,
        domain=domain,
        path_prefix=normalized_path_prefix,
        execute=execute,
        client=_LazySQLiteClientProxy(get_sqlite_client),
    )

    created_memories: List[Dict[str, Any]] = []
    created_memory = result.get("created_memory")
    if isinstance(created_memory, dict):
        memory_id = _safe_non_negative_int(created_memory.get("id"))
        if memory_id > 0:
            created_memories.append(
                {
                    "memory_id": memory_id,
                    "uri": str(created_memory.get("uri") or ""),
                    "path": str(created_memory.get("path") or ""),
                    "source_hash": str(result.get("source_hash") or ""),
                }
            )

    created_namespace_memories = _normalize_created_namespace_memories(
        result.get("created_namespace_memories")
    )

    accepted = bool(result.get("accepted"))
    reason = str(result.get("reason") or "")
    job_status = "failed"
    if accepted and reason == "executed":
        job_status = "executed"
    elif accepted:
        job_status = "prepared"

    batch_id = str(result.get("batch_id") or "").strip()
    if not batch_id:
        batch_id = f"learn-{uuid.uuid4().hex[:16]}"
        result["batch_id"] = batch_id

    now = _utc_iso_now()
    job_payload: Dict[str, Any] = {
        "job_id": batch_id,
        "job_type": "learn",
        "status": job_status,
        "created_at": now,
        "updated_at": now,
        "source": normalized_source,
        "reason": normalized_reason,
        "reason_text": normalized_reason,
        "actor_id": normalized_actor_id,
        "session_id": normalized_session_id,
        "domain": domain,
        "path_prefix": normalized_path_prefix,
        "execute": execute,
        "source_hash": str(result.get("source_hash") or ""),
        "target_parent_uri": str(result.get("target_parent_uri") or ""),
        "created_memories": created_memories,
        "created_namespace_memories": created_namespace_memories,
        "result": result,
    }
    if job_status == "executed":
        job_payload["rollback"] = {
            "status": "not_started",
            "rolled_back_count": 0,
            "error_count": 0,
            "errors": [],
            "completed_at": None,
            "side_effects_audit_required": True,
            "residual_artifacts_review_required": True,
        }
    elif job_status == "failed":
        job_payload["failure"] = {
            "reason": reason or "rejected",
            "updated_at": now,
        }

    await _put_learn_job(job_payload)

    response_payload = {
        "ok": accepted,
        "status": job_status,
        "job_id": batch_id,
        "job_type": "learn",
        "result": result,
        "job": _public_import_job_payload(job_payload),
        "rollback_endpoint": f"/maintenance/import/jobs/{batch_id}/rollback",
        "rollback_endpoint_aliases": [
            f"/maintenance/import/jobs/{batch_id}/rollback",
            f"/maintenance/learn/jobs/{batch_id}/rollback",
        ],
    }
    if not accepted:
        raise _http_error_for_learn_trigger(
            result=result,
            job_id=batch_id,
            job_payload=job_payload,
        )
    return response_payload


@router.get("/orphans")
async def get_orphans():
    """
    Get all orphan memories (both deprecated and truly orphaned).
    
    - deprecated: old versions created by update_memory (has migrated_to)
    - orphaned: non-deprecated memories with no paths pointing to them
    
    Includes migration target paths for deprecated memories so the human can see
    where the memory used to live without clicking into each one.
    """
    client = get_sqlite_client()
    return await client.get_all_orphan_memories()


@router.get("/orphans/{memory_id}")
async def get_orphan_detail(memory_id: int):
    """
    Get full detail of an orphan memory, including migration target's
    full content for diff comparison.
    """
    client = get_sqlite_client()
    detail = await client.get_orphan_detail(memory_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")
    return detail


@router.delete("/orphans/{memory_id}")
async def delete_orphan(memory_id: int):
    """
    Permanently delete an orphan memory.
    This action is irreversible. Repairs the version chain if applicable.
    
    Safety: The orphan check (deprecated or path-less) and the deletion
    run inside the same DB transaction, eliminating TOCTOU races.
    """
    client = get_sqlite_client()
    try:
        async def _write_task() -> Dict[str, Any]:
            return await client.permanently_delete_memory(memory_id, require_orphan=True)

        result = await _run_write_lane(
            "maintenance.delete_orphan",
            _write_task,
            session_id=f"maintenance.orphan:{memory_id}",
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/vitality/decay")
async def trigger_vitality_decay(force: bool = False, reason: str = "api"):
    await runtime_state.ensure_started(get_sqlite_client)
    result = await runtime_state.vitality_decay.run_decay(
        client_factory=get_sqlite_client,
        force=force,
        reason=reason or "api",
    )
    degraded = bool(result.get("degraded"))
    return {
        "ok": not degraded,
        "status": "degraded" if degraded else "ok",
        "result": result,
    }


@router.post("/vitality/candidates/query")
async def query_vitality_cleanup_candidates(payload: VitalityCleanupQueryRequest):
    client = get_sqlite_client()
    await runtime_state.ensure_started(get_sqlite_client)
    query_started = time.perf_counter()
    decay_result = await runtime_state.vitality_decay.run_decay(
        client_factory=get_sqlite_client,
        force=False,
        reason="maintenance.vitality_candidates",
    )
    candidates = await client.get_vitality_cleanup_candidates(
        threshold=payload.threshold,
        inactive_days=payload.inactive_days,
        limit=payload.limit,
        domain=payload.domain,
        path_prefix=payload.path_prefix,
    )
    summary = candidates.get("summary") if isinstance(candidates, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    query_profile = summary.get("query_profile")
    if not isinstance(query_profile, dict):
        query_profile = {}
    index_usage = query_profile.get("index_usage")
    if not isinstance(index_usage, dict):
        index_usage = {}
    query_ms = float(query_profile.get("query_ms") or 0.0)
    if query_ms <= 0.0:
        query_ms = (time.perf_counter() - query_started) * 1000.0
    candidate_items = candidates.get("items") if isinstance(candidates, dict) else []
    if not isinstance(candidate_items, list):
        candidate_items = []
    event = _sanitize_cleanup_query_event(
        {
            "timestamp": _utc_iso_now(),
            "query_ms": query_ms,
            "slow": query_ms >= _CLEANUP_QUERY_SLOW_MS,
            "candidate_count": len(candidate_items),
            "memory_index_hit": bool(index_usage.get("memory_cleanup_index")),
            "path_index_hit": bool(index_usage.get("path_scope_index")),
            "full_scan": bool(query_profile.get("full_scan")),
            "degraded": bool(query_profile.get("degraded")),
        }
    )
    if event is not None:
        async with _cleanup_query_events_guard:
            _cleanup_query_events.append(event)
    return {
        "ok": True,
        "status": "degraded" if bool(decay_result.get("degraded")) else "ok",
        "decay": decay_result,
        **candidates,
    }


@router.post("/vitality/cleanup/prepare")
async def prepare_vitality_cleanup(payload: VitalityCleanupPrepareRequest):
    action = (payload.action or "delete").strip().lower()
    if action not in {"delete", "keep"}:
        raise HTTPException(status_code=422, detail="action must be one of: delete, keep")

    client = get_sqlite_client()
    await runtime_state.ensure_started(get_sqlite_client)

    selected_by_id: Dict[int, str] = {}
    for item in payload.selections:
        selected_by_id[int(item.memory_id)] = str(item.state_hash)
    selected_ids = sorted(selected_by_id.keys())

    query_payload = await client.get_vitality_cleanup_candidates(
        threshold=9999.0,
        inactive_days=0.0,
        limit=max(1, len(selected_ids)),
        memory_ids=selected_ids,
    )
    current_items = query_payload.get("items") if isinstance(query_payload, dict) else []
    if not isinstance(current_items, list):
        current_items = []

    current_by_id = {
        int(item.get("memory_id")): item
        for item in current_items
        if isinstance(item, dict) and item.get("memory_id") is not None
    }

    missing_ids: List[int] = []
    stale_ids: List[int] = []
    prepared_selections: List[Dict[str, Any]] = []
    for memory_id in selected_ids:
        current = current_by_id.get(memory_id)
        if current is None:
            missing_ids.append(memory_id)
            continue
        expected_hash = selected_by_id[memory_id]
        current_hash = str(current.get("state_hash") or "")
        if current_hash != expected_hash:
            stale_ids.append(memory_id)
            continue
        prepared_selections.append(
            {
                "memory_id": memory_id,
                "state_hash": current_hash,
                "can_delete": bool(current.get("can_delete")),
                "uri": current.get("uri"),
                "vitality_score": current.get("vitality_score"),
                "inactive_days": current.get("inactive_days"),
            }
        )

    if missing_ids or stale_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "cleanup_candidates_changed",
                "missing_ids": missing_ids,
                "stale_ids": stale_ids,
            },
        )

    review = await runtime_state.cleanup_reviews.create_review(
        action=action,
        selections=prepared_selections,
        reviewer=payload.reviewer,
        ttl_seconds=payload.ttl_seconds,
    )

    return {
        "ok": True,
        "status": "pending_confirmation",
        "action": action,
        "selected_count": len(prepared_selections),
        "review": review,
        "preview": prepared_selections,
    }


@router.post("/vitality/cleanup/confirm")
async def confirm_vitality_cleanup(payload: VitalityCleanupConfirmRequest):
    consume_result = await runtime_state.cleanup_reviews.consume_review(
        review_id=payload.review_id,
        token=payload.token,
        confirmation_phrase=payload.confirmation_phrase,
    )
    if not consume_result.get("ok"):
        raise HTTPException(status_code=409, detail=str(consume_result.get("error")))

    review = consume_result.get("review") or {}
    action = str(review.get("action") or "delete")
    selections = review.get("selections") if isinstance(review.get("selections"), list) else []
    selected_ids = [
        int(item.get("memory_id"))
        for item in selections
        if isinstance(item, dict) and item.get("memory_id") is not None
    ]
    expected_hash_by_id = {
        int(item.get("memory_id")): str(item.get("state_hash") or "")
        for item in selections
        if isinstance(item, dict) and item.get("memory_id") is not None
    }

    client = get_sqlite_client()
    latest_payload = await client.get_vitality_cleanup_candidates(
        threshold=9999.0,
        inactive_days=0.0,
        limit=max(1, len(selected_ids)),
        memory_ids=selected_ids,
    )
    latest_items = latest_payload.get("items") if isinstance(latest_payload, dict) else []
    if not isinstance(latest_items, list):
        latest_items = []
    latest_by_id = {
        int(item.get("memory_id")): item
        for item in latest_items
        if isinstance(item, dict) and item.get("memory_id") is not None
    }
    write_lane_session_id = f"maintenance.cleanup:{str(review.get('review_id') or 'default')}"

    deleted: List[int] = []
    kept: List[int] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for memory_id in selected_ids:
        latest_item = latest_by_id.get(memory_id)
        if latest_item is None:
            skipped.append({"memory_id": memory_id, "reason": "memory_missing"})
            continue
        if str(latest_item.get("state_hash") or "") != expected_hash_by_id.get(memory_id, ""):
            skipped.append({"memory_id": memory_id, "reason": "stale_state"})
            continue

        if action == "keep":
            kept.append(memory_id)
            continue

        expected_hash = expected_hash_by_id.get(memory_id, "")
        if not expected_hash:
            skipped.append({"memory_id": memory_id, "reason": "stale_state"})
            continue

        try:
            async def _write_task(
                _memory_id: int = memory_id,
                _expected_hash: str = expected_hash,
            ) -> Dict[str, Any]:
                return await client.permanently_delete_memory(
                    _memory_id,
                    require_orphan=True,
                    expected_state_hash=_expected_hash,
                )

            await _run_write_lane(
                "maintenance.vitality.cleanup.confirm.delete",
                _write_task,
                session_id=write_lane_session_id,
            )
            deleted.append(memory_id)
        except RuntimeError as exc:
            if str(exc) == "stale_state":
                skipped.append({"memory_id": memory_id, "reason": "stale_state"})
                continue
            errors.append({"memory_id": memory_id, "error": str(exc)})
        except PermissionError:
            skipped.append({"memory_id": memory_id, "reason": "active_paths"})
        except ValueError:
            skipped.append({"memory_id": memory_id, "reason": "memory_missing"})
        except Exception as exc:
            errors.append({"memory_id": memory_id, "error": str(exc)})

    status = "executed" if not errors else "partially_failed"
    return {
        "ok": len(errors) == 0,
        "status": status,
        "action": action,
        "review_id": review.get("review_id"),
        "reviewer": review.get("reviewer"),
        "selected_count": len(selected_ids),
        "deleted_count": len(deleted),
        "kept_count": len(kept),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "deleted": deleted,
        "kept": kept,
        "skipped": skipped,
        "errors": errors,
    }


@router.get("/index/worker")
async def get_index_worker_status():
    await runtime_state.ensure_started(get_sqlite_client)
    return await runtime_state.index_worker.status()


@router.get("/index/job/{job_id}")
async def get_index_job(job_id: str):
    await runtime_state.ensure_started(get_sqlite_client)
    result = await runtime_state.index_worker.get_job(job_id=job_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=str(result.get("error") or "job not found"))
    result["runtime_worker"] = await runtime_state.index_worker.status()
    return result


@router.post("/index/job/{job_id}/cancel")
async def cancel_index_job(job_id: str, payload: Optional[IndexJobCancelRequest] = None):
    await runtime_state.ensure_started(get_sqlite_client)
    reason = "api_cancel"
    if isinstance(payload, IndexJobCancelRequest):
        reason = payload.reason

    result = await runtime_state.index_worker.cancel_job(job_id=job_id, reason=reason)
    if not result.get("ok"):
        error = str(result.get("error") or "job cancellation failed")
        normalized_error = error.lower()
        status_code = (
            404
            if "not found" in normalized_error or "job_not_found" in normalized_error
            else 409
        )
        raise HTTPException(status_code=status_code, detail=error)
    result["runtime_worker"] = await runtime_state.index_worker.status()
    return result


@router.post("/index/job/{job_id}/retry")
async def retry_index_job(job_id: str, payload: Optional[IndexJobRetryRequest] = None):
    await runtime_state.ensure_started(get_sqlite_client)
    original_result = await runtime_state.index_worker.get_job(job_id=job_id)
    if not original_result.get("ok"):
        raise HTTPException(status_code=404, detail=str(original_result.get("error") or "job not found"))

    original_job = original_result.get("job") or {}
    task_type = str(original_job.get("task_type") or "").strip()
    current_status = str(original_job.get("status") or "").strip().lower()
    if current_status not in {"failed", "dropped", "cancelled"}:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "job_retry_not_allowed",
                "reason": f"status:{current_status or 'unknown'}",
                "job_id": job_id,
                "task_type": task_type or "unknown",
            },
        )

    retry_reason = f"retry:{job_id}"
    if isinstance(payload, IndexJobRetryRequest) and payload.reason.strip():
        retry_reason = payload.reason.strip()

    enqueue_result: Dict[str, Any]
    if task_type == "reindex_memory":
        try:
            memory_id = int(original_job.get("memory_id") or 0)
        except (TypeError, ValueError):
            memory_id = 0
        if memory_id <= 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "job_retry_invalid_memory_id",
                    "job_id": job_id,
                    "task_type": task_type,
                },
            )
        enqueue_result = await runtime_state.index_worker.enqueue_reindex_memory(
            memory_id=memory_id,
            reason=retry_reason,
        )
        _raise_on_enqueue_drop(enqueue_result, operation="retry_reindex_memory")
    elif task_type == "rebuild_index":
        enqueue_result = await runtime_state.index_worker.enqueue_rebuild(reason=retry_reason)
        _raise_on_enqueue_drop(enqueue_result, operation="retry_rebuild_index")
    elif task_type == "sleep_consolidation":
        enqueue_result = await runtime_state.sleep_consolidation.schedule(
            index_worker=runtime_state.index_worker,
            force=True,
            reason=retry_reason,
        )
        _raise_on_enqueue_drop(enqueue_result, operation="retry_sleep_consolidation")
        if not enqueue_result.get("scheduled"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "job_retry_not_scheduled",
                    "reason": str(
                        enqueue_result.get("reason") or "sleep_consolidation_not_scheduled"
                    ),
                    "job_id": job_id,
                    "task_type": task_type,
                },
            )
    else:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "job_retry_unsupported_task_type",
                "job_id": job_id,
                "task_type": task_type or "unknown",
            },
        )

    response: Dict[str, Any] = {
        "ok": True,
        "retry_of_job_id": job_id,
        "task_type": task_type,
        "reason": retry_reason,
        **enqueue_result,
    }
    response["runtime_worker"] = await runtime_state.index_worker.status()
    if task_type == "sleep_consolidation":
        response["sleep_consolidation"] = await runtime_state.sleep_consolidation.status()
    return response


@router.post("/index/sleep-consolidation")
async def trigger_sleep_consolidation(
    reason: str = "api",
    wait: bool = False,
    timeout_seconds: int = 30,
):
    await runtime_state.ensure_started(get_sqlite_client)
    worker_status = await runtime_state.index_worker.status()
    if not worker_status.get("enabled"):
        raise HTTPException(status_code=409, detail="index_worker_disabled")

    schedule_result = await runtime_state.sleep_consolidation.schedule(
        index_worker=runtime_state.index_worker,
        force=True,
        reason=reason or "api",
    )
    _raise_on_enqueue_drop(schedule_result, operation="sleep_consolidation")
    if not schedule_result.get("scheduled"):
        raise HTTPException(
            status_code=409,
            detail=str(schedule_result.get("reason") or "sleep_consolidation_not_scheduled"),
        )

    payload = {"ok": True, "reason": reason or "api", **schedule_result}
    job_id = schedule_result.get("job_id")
    if wait and isinstance(job_id, str) and job_id:
        payload["wait_result"] = await runtime_state.index_worker.wait_for_job(
            job_id=job_id,
            timeout_seconds=max(1.0, float(timeout_seconds)),
        )
    payload["runtime_worker"] = await runtime_state.index_worker.status()
    payload["sleep_consolidation"] = await runtime_state.sleep_consolidation.status()
    return payload


@router.post("/index/rebuild")
async def rebuild_index(reason: str = "api", wait: bool = False, timeout_seconds: int = 30):
    client = get_sqlite_client()
    await runtime_state.ensure_started(get_sqlite_client)
    worker_status = await runtime_state.index_worker.status()

    if not worker_status.get("enabled"):
        try:
            result = await client.rebuild_index(reason=reason or "api")
            return {
                "ok": True,
                "queued": False,
                "executed_sync": True,
                "reason": reason or "api",
                "result": result,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    enqueue_result = await runtime_state.index_worker.enqueue_rebuild(
        reason=reason or "api"
    )
    _raise_on_enqueue_drop(enqueue_result, operation="rebuild_index")
    payload = {"ok": True, "reason": reason or "api", **enqueue_result}
    job_id = enqueue_result.get("job_id")
    if wait and isinstance(job_id, str) and job_id:
        payload["wait_result"] = await runtime_state.index_worker.wait_for_job(
            job_id=job_id,
            timeout_seconds=max(1.0, float(timeout_seconds)),
        )
    payload["runtime_worker"] = await runtime_state.index_worker.status()
    return payload


@router.post("/index/reindex/{memory_id}")
async def reindex_memory(
    memory_id: int, reason: str = "api", wait: bool = False, timeout_seconds: int = 30
):
    if memory_id <= 0:
        raise HTTPException(status_code=400, detail="memory_id must be a positive integer")

    client = get_sqlite_client()
    await runtime_state.ensure_started(get_sqlite_client)
    worker_status = await runtime_state.index_worker.status()

    if not worker_status.get("enabled"):
        try:
            result = await client.reindex_memory(
                memory_id=memory_id,
                reason=reason or "api",
            )
            return {
                "ok": True,
                "queued": False,
                "executed_sync": True,
                "memory_id": memory_id,
                "reason": reason or "api",
                "result": result,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    enqueue_result = await runtime_state.index_worker.enqueue_reindex_memory(
        memory_id=memory_id,
        reason=reason or "api",
    )
    _raise_on_enqueue_drop(enqueue_result, operation="reindex_memory")
    payload = {
        "ok": True,
        "memory_id": memory_id,
        "reason": reason or "api",
        **enqueue_result,
    }
    job_id = enqueue_result.get("job_id")
    if wait and isinstance(job_id, str) and job_id:
        payload["wait_result"] = await runtime_state.index_worker.wait_for_job(
            job_id=job_id,
            timeout_seconds=max(1.0, float(timeout_seconds)),
        )
    payload["runtime_worker"] = await runtime_state.index_worker.status()
    return payload


@router.post("/observability/search")
async def run_observability_search(payload: SearchConsoleRequest):
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="query must not be empty")

    mode = payload.mode.strip().lower()
    if mode not in _ALLOWED_SEARCH_MODES:
        raise HTTPException(
            status_code=422,
            detail="mode must be one of: keyword, semantic, hybrid",
        )

    try:
        filters = _normalize_search_filters(payload.filters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    scope_hint_raw: Optional[Any] = payload.scope_hint
    if scope_hint_raw is None and isinstance(payload.filters, dict):
        scope_hint_raw = payload.filters.get("scope_hint")
    try:
        normalized_scope_hint = _normalize_scope_hint(scope_hint_raw)
        filters, scope_resolution = _merge_scope_hint_with_filters(
            normalized_filters=filters,
            scope_hint=normalized_scope_hint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    client = get_sqlite_client()
    await runtime_state.ensure_started(get_sqlite_client)
    await _ensure_search_events_loaded(client)

    query_preprocess: Dict[str, Any] = {
        "original_query": query,
        "normalized_query": query,
        "rewritten_query": query,
        "tokens": [],
        "changed": False,
    }
    intent_profile: Dict[str, Any] = {
        "intent": None,
        "strategy_template": "default",
        "method": "fallback",
        "confidence": 0.0,
        "signals": ["fallback_default"],
    }
    preprocess_degrade_reasons: List[str] = []
    for conflict in scope_resolution.get("conflicts", []):
        preprocess_degrade_reasons.append(f"scope_hint_{conflict}")

    preprocess_fn = getattr(client, "preprocess_query", None)
    if callable(preprocess_fn):
        try:
            preprocess_payload = preprocess_fn(query)
            if isinstance(preprocess_payload, dict):
                query_preprocess.update(preprocess_payload)
        except Exception:
            preprocess_degrade_reasons.append("query_preprocess_failed")
    else:
        preprocess_degrade_reasons.append("query_preprocess_unavailable")

    query_effective = (
        str(query_preprocess.get("rewritten_query") or "").strip() or query
    )

    classify_fn = None
    fallback_classify_fn = getattr(client, "classify_intent", None)
    classify_with_intent_llm = False
    if _INTENT_LLM_ENABLED:
        classify_fn = getattr(client, "classify_intent_with_llm", None)
        classify_with_intent_llm = callable(classify_fn)
        if not callable(classify_fn):
            preprocess_degrade_reasons.append("intent_llm_unavailable")
            classify_fn = fallback_classify_fn
    else:
        classify_fn = fallback_classify_fn
    if callable(classify_fn):
        try:
            classify_payload = classify_fn(query, query_effective)
            if inspect.isawaitable(classify_payload):
                classify_payload = await classify_payload
            if isinstance(classify_payload, dict):
                intent_profile.update(classify_payload)
                classify_degrade_reasons = classify_payload.get("degrade_reasons")
                if isinstance(classify_degrade_reasons, list):
                    for reason in classify_degrade_reasons:
                        if isinstance(reason, str) and reason.strip():
                            preprocess_degrade_reasons.append(reason.strip())
        except Exception:
            preprocess_degrade_reasons.append("intent_classification_failed")
            if classify_with_intent_llm and callable(fallback_classify_fn):
                try:
                    fallback_payload = fallback_classify_fn(query, query_effective)
                    if inspect.isawaitable(fallback_payload):
                        fallback_payload = await fallback_payload
                    if isinstance(fallback_payload, dict):
                        intent_profile.update(fallback_payload)
                        preprocess_degrade_reasons.append(
                            "intent_llm_fallback_rule_applied"
                        )
                        fallback_degrade_reasons = fallback_payload.get(
                            "degrade_reasons"
                        )
                        if isinstance(fallback_degrade_reasons, list):
                            for reason in fallback_degrade_reasons:
                                if isinstance(reason, str) and reason.strip():
                                    preprocess_degrade_reasons.append(reason.strip())
                except Exception:
                    preprocess_degrade_reasons.append(
                        "intent_classification_fallback_failed"
                    )
    else:
        preprocess_degrade_reasons.append("intent_classification_unavailable")

    intent_for_search: Optional[Dict[str, Any]] = None
    if intent_profile.get("intent") in {"factual", "exploratory", "temporal", "causal"}:
        intent_for_search = intent_profile

    started = time.perf_counter()
    try:
        try:
            backend_payload = await client.search_advanced(
                query=query_effective,
                mode=mode,
                max_results=payload.max_results,
                candidate_multiplier=payload.candidate_multiplier,
                filters=filters,
                intent_profile=intent_for_search,
            )
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument 'intent_profile'" not in message:
                raise
            preprocess_degrade_reasons.append("intent_profile_not_supported")
            backend_payload = await client.search_advanced(
                query=query_effective,
                mode=mode,
                max_results=payload.max_results,
                candidate_multiplier=payload.candidate_multiplier,
                filters=filters,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    latency_ms = (time.perf_counter() - started) * 1000.0

    if not isinstance(backend_payload, dict):
        backend_payload = {}
    backend_metadata = (
        backend_payload.get("metadata")
        if isinstance(backend_payload.get("metadata"), dict)
        else {}
    )

    global_results_raw = backend_payload.get("results")
    global_results = global_results_raw if isinstance(global_results_raw, list) else []

    session_results: List[Dict[str, Any]] = []
    if payload.include_session:
        try:
            session_rows = await runtime_state.session_cache.search(
                session_id=payload.session_id or "api-observability",
                query=query,
                limit=payload.max_results,
            )
            session_results = [
                _session_row_to_result(row)
                for row in session_rows
                if isinstance(row, dict)
            ]
        except Exception:
            session_results = []
            preprocess_degrade_reasons.append("session_cache_lookup_failed")

    merged_results, session_first_metrics = _merge_session_global_results(
        session_results=session_results,
        global_results=global_results,
        limit=payload.max_results,
    )

    degrade_reasons = backend_payload.get("degrade_reasons")
    if not isinstance(degrade_reasons, list):
        degrade_reasons = []
    degrade_reasons = [
        item for item in degrade_reasons if isinstance(item, str) and item.strip()
    ]
    for reason in preprocess_degrade_reasons:
        if reason not in degrade_reasons:
            degrade_reasons.append(reason)

    mode_applied = str(backend_payload.get("mode") or mode)
    degraded = bool(backend_payload.get("degraded")) or bool(degrade_reasons)
    intent_profile_supported = "intent_profile_not_supported" not in degrade_reasons
    intent_applied = str(
        backend_metadata.get("intent")
        or (
            intent_profile.get("intent")
            if intent_profile_supported
            else "unknown"
        )
        or "unknown"
    )
    strategy_template_applied = str(
        backend_metadata.get("strategy_template")
        or (
            intent_profile.get("strategy_template")
            if intent_profile_supported
            else "default"
        )
        or "default"
    )

    event = {
        "timestamp": _utc_iso_now(),
        "mode_requested": mode,
        "mode_applied": mode_applied,
        "latency_ms": round(latency_ms, 3),
        "degraded": degraded,
        "degrade_reasons": degrade_reasons,
        "session_count": len(session_results),
        "global_count": len(global_results),
        "returned_count": len(merged_results),
        "dedup_dropped": int(session_first_metrics.get("dedup_dropped") or 0),
        "session_contributed": int(
            session_first_metrics.get("session_contributed") or 0
        ),
        "global_contributed": int(session_first_metrics.get("global_contributed") or 0),
        "intent": str(intent_profile.get("intent") or "unknown"),
        "intent_applied": intent_applied,
        "strategy_template": str(
            intent_profile.get("strategy_template") or "default"
        ),
        "strategy_template_applied": strategy_template_applied,
    }
    async with _search_events_guard:
        _search_events.append(event)
        await _persist_search_events_locked(client, list(_search_events))

    return {
        "ok": True,
        "query": query,
        "query_effective": query_effective,
        "query_preprocess": query_preprocess,
        "intent": str(intent_profile.get("intent") or "unknown"),
        "intent_applied": intent_applied,
        "intent_llm_enabled": _INTENT_LLM_ENABLED,
        "intent_llm_applied": bool(intent_profile.get("intent_llm_applied")),
        "intent_profile": intent_profile,
        "strategy_template": str(intent_profile.get("strategy_template") or "default"),
        "strategy_template_applied": strategy_template_applied,
        "mode_requested": mode,
        "mode_applied": mode_applied,
        "filters": filters,
        "scope_hint": scope_resolution.get("raw"),
        "scope_hint_applied": bool(scope_resolution.get("applied")),
        "scope_strategy_applied": scope_resolution.get("strategy"),
        "scope_effective": scope_resolution.get("effective", {}),
        "max_results": payload.max_results,
        "candidate_multiplier": payload.candidate_multiplier,
        "include_session": payload.include_session,
        "latency_ms": round(latency_ms, 3),
        "degraded": degraded,
        "degrade_reasons": degrade_reasons,
        "counts": {
            "session": len(session_results),
            "global": len(global_results),
            "returned": len(merged_results),
        },
        "session_first_metrics": session_first_metrics,
        "results": merged_results,
        "backend_metadata": backend_metadata,
        "timestamp": event["timestamp"],
        **(
            {"scope_conflicts": scope_resolution.get("conflicts")}
            if scope_resolution.get("conflicts")
            else {}
        ),
    }


@router.get("/observability/summary")
async def get_observability_summary():
    client = get_sqlite_client()
    await runtime_state.ensure_started(get_sqlite_client)
    await _ensure_search_events_loaded(client)

    try:
        index_status = await client.get_index_status()
        index_status.setdefault("degraded", False)
    except Exception as exc:
        index_status = {
            "degraded": True,
            "reason": str(exc),
            "source": "maintenance.observability.index_status",
        }

    gist_stats_getter = getattr(client, "get_gist_stats", None)
    if callable(gist_stats_getter):
        try:
            gist_stats = await gist_stats_getter()
            if isinstance(gist_stats, dict):
                gist_stats.setdefault("degraded", False)
            else:
                gist_stats = {"degraded": True, "reason": "invalid_gist_stats_payload"}
        except Exception as exc:
            gist_stats = {
                "degraded": True,
                "reason": str(exc),
                "source": "maintenance.observability.gist_stats",
            }
    else:
        gist_stats = {
            "degraded": True,
            "reason": "gist_stats_unavailable",
            "source": "maintenance.observability.gist_stats",
        }

    vitality_stats_getter = getattr(client, "get_vitality_stats", None)
    if callable(vitality_stats_getter):
        try:
            vitality_stats = await vitality_stats_getter()
            if isinstance(vitality_stats, dict):
                vitality_stats.setdefault("degraded", False)
            else:
                vitality_stats = {
                    "degraded": True,
                    "reason": "invalid_vitality_stats_payload",
                }
        except Exception as exc:
            vitality_stats = {
                "degraded": True,
                "reason": str(exc),
                "source": "maintenance.observability.vitality_stats",
            }
    else:
        vitality_stats = {
            "degraded": True,
            "reason": "vitality_stats_unavailable",
            "source": "maintenance.observability.vitality_stats",
        }

    worker_status = await runtime_state.index_worker.status()
    write_lane_status = await runtime_state.write_lanes.status()
    vitality_decay_status = await runtime_state.vitality_decay.status()
    cleanup_review_status = await runtime_state.cleanup_reviews.summary()
    sleep_consolidation_status = await runtime_state.sleep_consolidation.status()
    try:
        sm_lite_stats = await _build_sm_lite_stats()
    except Exception as exc:
        sm_lite_stats = {
            "degraded": True,
            "reason": str(exc),
            "storage": "runtime_ephemeral",
            "promotion_path": "compact_context + auto_flush",
            "session_cache": {},
            "flush_tracker": {},
        }

    async with _search_events_guard:
        events = list(_search_events)
    async with _cleanup_query_events_guard:
        cleanup_query_events = list(_cleanup_query_events)

    search_summary = _build_search_summary(events)
    cleanup_query_summary = _build_cleanup_query_summary(cleanup_query_events)
    guard_summary = await runtime_state.guard_tracker.summary()
    index_latency = _build_index_latency_summary(worker_status)

    status = (
        "degraded"
        if bool(index_status.get("degraded"))
        or bool(gist_stats.get("degraded"))
        or bool(vitality_stats.get("degraded"))
        or bool(sm_lite_stats.get("degraded"))
        else "ok"
    )

    return {
        "status": status,
        "timestamp": _utc_iso_now(),
        "health": {
            "index": index_status,
            "runtime": {
                "write_lanes": write_lane_status,
                "index_worker": worker_status,
                "sleep_consolidation": sleep_consolidation_status,
                "sm_lite": sm_lite_stats,
            },
        },
        "search_stats": search_summary,
        "cleanup_query_stats": cleanup_query_summary,
        "guard_stats": guard_summary,
        "index_latency": index_latency,
        "gist_stats": gist_stats,
        "vitality_stats": vitality_stats,
        "vitality_decay": vitality_decay_status,
        "cleanup_reviews": cleanup_review_status,
        "sleep_consolidation": sleep_consolidation_status,
    }
