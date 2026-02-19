import asyncio
import hmac
import json
import math
import os
import time
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from db import get_sqlite_client
from runtime_state import runtime_state

_MCP_API_KEY_ENV = "MCP_API_KEY"
_MCP_API_KEY_HEADER = "X-MCP-API-Key"
_MCP_API_KEY_ALLOW_INSECURE_LOCAL_ENV = "MCP_API_KEY_ALLOW_INSECURE_LOCAL"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_LOOPBACK_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _get_configured_mcp_api_key() -> str:
    return str(os.getenv(_MCP_API_KEY_ENV) or "").strip()


def _allow_insecure_local_without_api_key() -> bool:
    value = str(os.getenv(_MCP_API_KEY_ALLOW_INSECURE_LOCAL_ENV) or "").strip().lower()
    return value in _TRUTHY_ENV_VALUES


def _is_loopback_request(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = str(getattr(client, "host", "") or "").strip().lower()
    return host in _LOOPBACK_CLIENT_HOSTS


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


_CLEANUP_QUERY_SLOW_MS = max(
    1.0, _env_float("OBSERVABILITY_CLEANUP_QUERY_SLOW_MS", 250.0)
)


class SearchConsoleRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: str = Field(default="hybrid")
    max_results: int = Field(default=8, ge=1, le=50)
    candidate_multiplier: int = Field(default=4, ge=1, le=20)
    include_session: bool = True
    session_id: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)


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
        "session_count": int(raw.get("session_count") or 0),
        "global_count": int(raw.get("global_count") or 0),
        "returned_count": int(raw.get("returned_count") or 0),
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

    return normalized


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
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for row in session_results + global_results:
        uri = str(row.get("uri") or "")
        if not uri or uri in seen:
            continue
        seen.add(uri)
        merged.append(row)
        if len(merged) >= max(1, limit):
            break
    return merged


def _build_search_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not events:
        return {
            "window_size": 0,
            "total_queries": 0,
            "degraded_queries": 0,
            "cache_hit_queries": 0,
            "cache_hit_ratio": 0.0,
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
        result = await client.permanently_delete_memory(
            memory_id, require_orphan=True
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
            await client.permanently_delete_memory(
                memory_id,
                require_orphan=True,
                expected_state_hash=expected_hash,
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
        status_code = 404 if "not found" in error else 409
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

    classify_fn = getattr(client, "classify_intent", None)
    if callable(classify_fn):
        try:
            classify_payload = classify_fn(query, query_effective)
            if isinstance(classify_payload, dict):
                intent_profile.update(classify_payload)
        except Exception:
            preprocess_degrade_reasons.append("intent_classification_failed")
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

    merged_results = _merge_session_global_results(
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
        "intent_profile": intent_profile,
        "strategy_template": str(intent_profile.get("strategy_template") or "default"),
        "strategy_template_applied": strategy_template_applied,
        "mode_requested": mode,
        "mode_applied": mode_applied,
        "filters": filters,
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
        "results": merged_results,
        "backend_metadata": backend_metadata,
        "timestamp": event["timestamp"],
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
            "degraded": False,
            "reason": "vitality_stats_unavailable",
            "source": "maintenance.observability.vitality_stats",
        }

    worker_status = await runtime_state.index_worker.status()
    write_lane_status = await runtime_state.write_lanes.status()
    vitality_decay_status = await runtime_state.vitality_decay.status()
    cleanup_review_status = await runtime_state.cleanup_reviews.summary()
    sleep_consolidation_status = await runtime_state.sleep_consolidation.status()

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
