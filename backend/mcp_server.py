"""
MCP Server for Memory Palace System (SQLite Backend)

This module provides the MCP (Model Context Protocol) interface for
the AI agent to interact with the SQLite-based memory system.

URI-based addressing with domain prefixes:
- core://agent              - AI's identity/memories
- writer://chapter_1             - Story/script drafts
- game://magic_system            - Game setting documents

Multiple paths can point to the same memory (aliases).
"""

import asyncio
import os
import re
import sys
import uuid
import json
import inspect
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

# Ensure we can import from backend modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from db.sqlite_client import get_sqlite_client
from db.snapshot import get_snapshot_manager
from runtime_state import runtime_state

# Load environment variables
# Explicitly look for .env in the parent directory (project root)
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
dotenv_path = os.path.join(root_dir, ".env")

if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)

# Initialize FastMCP server
mcp = FastMCP("Memory Palace Interface")

# =============================================================================
# Domain Configuration
# =============================================================================
# Valid domains (protocol prefixes)
# =============================================================================
READ_ONLY_DOMAINS = {"system"}
VALID_DOMAINS = list(
    dict.fromkeys(
        [
            d.strip().lower()
            for d in os.getenv("VALID_DOMAINS", "core,writer,game,notes,system").split(",")
            if d.strip()
        ]
        + sorted(READ_ONLY_DOMAINS)
    )
)
DEFAULT_DOMAIN = "core"

# =============================================================================
# Core Memories Configuration
# =============================================================================
# These URIs will be auto-loaded when system://boot is read.
# Configure via CORE_MEMORY_URIS in .env (comma-separated).
#
# Format: full URIs (e.g., "core://agent", "core://agent/my_user")
# =============================================================================
CORE_MEMORY_URIS = [
    uri.strip()
    for uri in os.getenv("CORE_MEMORY_URIS", "").split(",")
    if uri.strip()
]


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read int env with a safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_csv(name: str, default: str = "") -> List[str]:
    raw = os.getenv(name, default)
    values: List[str] = []
    for item in str(raw or "").split(","):
        value = item.strip().lower()
        if value and value not in values:
            values.append(value)
    return values


def _utc_now_naive() -> datetime:
    """Return current UTC time without tzinfo (compat with legacy utcnow formatting)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_iso_now() -> str:
    """Return current UTC timestamp in ISO-8601 format with trailing Z."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


ALLOWED_SEARCH_MODES = {"keyword", "semantic", "hybrid"}
DEFAULT_SEARCH_MODE = os.getenv("SEARCH_DEFAULT_MODE", "keyword").strip().lower()
if DEFAULT_SEARCH_MODE not in ALLOWED_SEARCH_MODES:
    DEFAULT_SEARCH_MODE = "keyword"

DEFAULT_SEARCH_MAX_RESULTS = _env_int("SEARCH_DEFAULT_MAX_RESULTS", 10, minimum=1)
DEFAULT_SEARCH_CANDIDATE_MULTIPLIER = _env_int(
    "SEARCH_DEFAULT_CANDIDATE_MULTIPLIER", 4, minimum=1
)
SEARCH_HARD_MAX_RESULTS = _env_int("SEARCH_HARD_MAX_RESULTS", 100, minimum=1)
SEARCH_HARD_MAX_CANDIDATE_MULTIPLIER = _env_int(
    "SEARCH_HARD_MAX_CANDIDATE_MULTIPLIER", 50, minimum=1
)
READ_CHUNK_SIZE = _env_int("RETRIEVAL_CHUNK_SIZE", 1000, minimum=1)
READ_CHUNK_OVERLAP = _env_int("RETRIEVAL_CHUNK_OVERLAP", 200, minimum=0)
ENABLE_SESSION_FIRST_SEARCH = _env_bool("RUNTIME_SESSION_FIRST_SEARCH", True)
ENABLE_WRITE_LANE_QUEUE = _env_bool("RUNTIME_WRITE_LANE_QUEUE", True)
ENABLE_INDEX_WORKER = _env_bool("RUNTIME_INDEX_WORKER_ENABLED", True)
DEFER_INDEX_ON_WRITE = _env_bool("RUNTIME_INDEX_DEFER_ON_WRITE", True)
AUTO_FLUSH_ENABLED = _env_bool("RUNTIME_AUTO_FLUSH_ENABLED", True)
AUTO_FLUSH_PRIORITY = _env_int("RUNTIME_AUTO_FLUSH_PRIORITY", 2, minimum=0)
AUTO_FLUSH_SUMMARY_LINES = _env_int("RUNTIME_AUTO_FLUSH_SUMMARY_LINES", 12, minimum=3)
AUTO_FLUSH_PARENT_URI = os.getenv("RUNTIME_AUTO_FLUSH_PARENT_URI", "notes://").strip() or "notes://"
INDEX_LITE_ENABLED = _env_bool("INDEX_LITE_ENABLED", False)
AUDIT_VERBOSE = _env_bool("AUDIT_VERBOSE", False)
INTENT_LLM_ENABLED = _env_bool("INTENT_LLM_ENABLED", False)


def _auto_learn_explicit_enabled() -> bool:
    return _env_bool("AUTO_LEARN_EXPLICIT_ENABLED", False)


def _auto_learn_require_reason() -> bool:
    return _env_bool("AUTO_LEARN_REQUIRE_REASON", True)


def _auto_learn_allowed_domains() -> List[str]:
    domains = _env_csv("AUTO_LEARN_ALLOWED_DOMAINS", "notes")
    return domains or ["notes"]


IMPORT_LEARN_AUDIT_META_KEY = "audit.import_learn.summary.v1"
_IMPORT_LEARN_META_PERSIST_LOCK = asyncio.Lock()

# Session ID for this MCP server instance
_SESSION_ID = f"mcp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
_SESSION_ID_SAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


def _safe_context_attr(value: Any, name: str) -> Any:
    """Read a context attribute without propagating request-scope errors."""
    try:
        return getattr(value, name)
    except Exception:
        return None


def _normalize_session_fragment(
    value: Any,
    *,
    default: str,
    max_len: int = 24,
) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    safe = _SESSION_ID_SAFE_PATTERN.sub("-", text).strip("-")
    if not safe:
        return default
    return safe[:max_len]


def _build_context_session_id() -> Optional[str]:
    """Build a request-aware session id from FastMCP context when available."""
    try:
        ctx = mcp.get_context()
    except Exception:
        return None

    if ctx is None:
        return None

    client_id = _safe_context_attr(ctx, "client_id")
    request_id = _safe_context_attr(ctx, "request_id")
    session_obj = _safe_context_attr(ctx, "session")
    request_context = _safe_context_attr(ctx, "request_context")
    request_obj = None
    if request_context is not None:
        if session_obj is None:
            session_obj = _safe_context_attr(request_context, "session")
        request_obj = _safe_context_attr(request_context, "request")

    if (
        not client_id
        and not request_id
        and session_obj is None
        and request_obj is None
    ):
        return None

    client_fragment = _normalize_session_fragment(client_id, default="client")
    session_seed = ""
    if session_obj is not None:
        session_seed = f"{type(session_obj).__name__}-{id(session_obj):x}"
    session_fragment = _normalize_session_fragment(session_seed, default="session")
    request_seed = request_id
    if not request_seed and request_obj is not None:
        request_seed = f"{type(request_obj).__name__}-{id(request_obj):x}"
    request_fragment = _normalize_session_fragment(
        request_seed,
        default="request",
        max_len=32,
    )
    return f"mcp_ctx_{client_fragment}_{session_fragment}_{request_fragment}"


def get_session_id() -> str:
    """Get the current session ID for snapshot tracking."""
    context_session_id = _build_context_session_id()
    if context_session_id:
        return context_session_id
    return _SESSION_ID


# =============================================================================
# URI Parsing
# =============================================================================

# Regex pattern for URI: domain://path
_URI_PATTERN = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)://(.*)$")


def parse_uri(uri: str) -> Tuple[str, str]:
    """
    Parse a memory URI into (domain, path).

    Supported formats:
    - "core://agent"          -> ("core", "agent")
    - "writer://chapter_1"         -> ("writer", "chapter_1")
    - "memory-palace"         -> ("core", "memory-palace")  [legacy fallback]

    Args:
        uri: The URI to parse

    Returns:
        Tuple of (domain, path)

    Raises:
        ValueError: If the URI format is invalid or domain is unknown
    """
    uri = uri.strip()

    match = _URI_PATTERN.match(uri)
    if match:
        domain = match.group(1).lower()
        path = match.group(2).strip("/")

        if domain not in VALID_DOMAINS:
            raise ValueError(
                f"Unknown domain '{domain}'. Valid domains: {', '.join(VALID_DOMAINS)}"
            )

        return (domain, path)

    # Legacy fallback: bare path without protocol
    # Assume default domain (core)
    path = uri.strip("/")
    return (DEFAULT_DOMAIN, path)


def make_uri(domain: str, path: str) -> str:
    """
    Create a URI from domain and path.

    Args:
        domain: The domain (e.g., "core", "writer")
        path: The path (e.g., "memory-palace")

    Returns:
        Full URI (e.g., "core://agent")
    """
    return f"{domain}://{path}"


def _validate_writable_domain(
    domain: str, *, operation: str, uri: Optional[str] = None
) -> None:
    normalized = str(domain or "").strip().lower()
    if normalized in READ_ONLY_DOMAINS:
        target = str(uri or f"{normalized}://").strip()
        raise ValueError(
            f"{operation} does not allow writes to '{target}'. "
            "system:// is read-only and reserved for built-in views."
        )


# =============================================================================
# Snapshot Helpers
# =============================================================================
#
# Snapshots are split into two dimensions matching the two DB tables:
#
#   1. PATH snapshots (resource_id = URI, resource_type = "path")
#      Track changes to the paths table: create, create_alias, delete, modify_meta
#
#   2. MEMORY CONTENT snapshots (resource_id = "memory:{id}", resource_type = "memory")
#      Track changes to the memories table: modify_content
#
# This separation ensures that path-level operations (e.g. add_alias) never
# collide with content-level operations (e.g. update_memory), fixing the bug
# where an alias snapshot blocked the content snapshot for the same URI.
# =============================================================================


async def _snapshot_memory_content(uri: str) -> bool:
    """
    Snapshot memory content before modification.

    Uses memory:{id} as resource_id so it never collides with path snapshots.
    Idempotent per URI per session: when a memory is updated multiple times,
    each update produces a new memory_id (version chain), but only the FIRST
    version is snapshotted.  Subsequent updates to the same URI are no-ops.

    This prevents orphaned snapshots when create+delete cancel out: without
    this, create → update(×N) → delete would leave N-2 unreachable
    "memory:{intermediate_id}" snapshots in the manifest.
    """
    manager = get_snapshot_manager()
    session_id = get_session_id()

    domain, path = parse_uri(uri)
    full_uri = make_uri(domain, path)
    client = get_sqlite_client()
    memory = await client.get_memory_by_path(path, domain)

    if not memory:
        return False

    resource_id = f"memory:{memory['id']}"

    # Fast path: exact match (same memory_id, no version change yet)
    if manager.has_snapshot(session_id, resource_id):
        return False

    # Slow path: check if an earlier version of this URI was already
    # snapshotted (e.g. memory:1 exists but current id is now 5).
    if manager.find_memory_snapshot_by_uri(session_id, full_uri):
        return False

    # Collect all paths pointing to this memory for fallback during rollback.
    # If the primary path is later deleted, rollback can use an alternative.
    memory_full = await client.get_memory_by_id(memory["id"])
    all_paths = memory_full.get("paths", []) if memory_full else []

    return manager.create_snapshot(
        session_id=session_id,
        resource_id=resource_id,
        resource_type="memory",
        snapshot_data={
            "operation_type": "modify_content",
            "memory_id": memory["id"],
            # Content is NOT stored here — the old Memory row is preserved
            # in DB (deprecated=True, migrated_to=new_id) and can be read
            # via get_memory_version(memory_id) when computing diffs.
            "uri": full_uri,
            "domain": domain,
            "path": path,
            "all_paths": all_paths,
        },
    )


async def _snapshot_path_meta(uri: str) -> bool:
    """
    Snapshot path metadata (priority/disclosure) before modification.
    Uses URI as resource_id.
    """
    manager = get_snapshot_manager()
    session_id = get_session_id()

    if manager.has_snapshot(session_id, uri):
        return False

    domain, path = parse_uri(uri)
    client = get_sqlite_client()
    memory = await client.get_memory_by_path(path, domain)

    if not memory:
        return False

    return manager.create_snapshot(
        session_id=session_id,
        resource_id=uri,
        resource_type="path",
        snapshot_data={
            "operation_type": "modify_meta",
            "domain": domain,
            "path": path,
            "uri": uri,
            "memory_id": memory["id"],
            "priority": memory.get("priority"),
            "disclosure": memory.get("disclosure"),
        },
    )


async def _snapshot_path_create(
    uri: str,
    memory_id: int,
    operation_type: str = "create",
    target_uri: Optional[str] = None,
) -> bool:
    """
    Record that a path was created (for rollback = remove the path).

    Used by both create_memory (operation_type="create") and
    add_alias (operation_type="create_alias").
    """
    manager = get_snapshot_manager()
    session_id = get_session_id()

    domain, path = parse_uri(uri)

    data = {
        "operation_type": operation_type,
        "domain": domain,
        "path": path,
        "uri": uri,
        "memory_id": memory_id,
    }
    if target_uri:
        data["target_uri"] = target_uri

    return manager.create_snapshot(
        session_id=session_id, resource_id=uri, resource_type="path", snapshot_data=data
    )


async def _snapshot_path_delete(uri: str) -> bool:
    """
    Record that a path is being deleted (for rollback = re-create).

    Two cases depending on what path snapshot already exists for this URI:

    1. Existing "create"/"create_alias" snapshot (create->delete in same session):
       Net effect is nothing happened. Remove the snapshot entirely.

    2. No prior path snapshot, or a "modify_meta" snapshot:
       Capture the CURRENT state as a "delete" snapshot (force overwrite).
       This stores the pre-delete memory_id, metadata, and content for
       both rollback and diff display.
    """
    manager = get_snapshot_manager()
    session_id = get_session_id()

    # Check for cancellation with prior create
    existing = manager.get_snapshot(session_id, uri)
    if existing:
        existing_op = existing.get("data", {}).get("operation_type")
        if existing_op in ("create", "create_alias"):
            # create + delete = no-op. Remove path snapshot.
            # Also clean up the content snapshot (at most one per URI,
            # guaranteed by _snapshot_memory_content's URI-level dedup).
            content_snap_id = manager.find_memory_snapshot_by_uri(session_id, uri)
            if content_snap_id:
                manager.delete_snapshot(session_id, content_snap_id)
            manager.delete_snapshot(session_id, uri)
            return False

    # Capture current state before deletion
    domain, path = parse_uri(uri)
    client = get_sqlite_client()
    memory = await client.get_memory_by_path(path, domain)

    if not memory:
        return False

    # If overwriting a modify_meta snapshot, preserve the original (pre-session)
    # metadata instead of the current (post-modification) values.
    # This maintains the "first modification before session" invariant.
    priority = memory.get("priority")
    disclosure = memory.get("disclosure")
    if existing and existing.get("data", {}).get("operation_type") == "modify_meta":
        priority = existing["data"].get("priority", priority)
        disclosure = existing["data"].get("disclosure", disclosure)

    return manager.create_snapshot(
        session_id=session_id,
        resource_id=uri,
        resource_type="path",
        snapshot_data={
            "operation_type": "delete",
            "domain": domain,
            "path": path,
            "uri": uri,
            "memory_id": memory["id"],
            "priority": priority,
            "disclosure": disclosure,
            # Content is NOT stored here — retrievable from DB via memory_id
            # (the Memory row persists as deprecated until permanently deleted).
        },
        force=True,
    )


# =============================================================================
# Helper Functions
# =============================================================================


def _to_json(payload: Dict[str, Any]) -> str:
    """Serialize payload for MCP string responses."""
    return json.dumps(payload, ensure_ascii=False)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _event_preview(text: str, max_chars: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "..."


async def _record_session_hit(
    *,
    uri: str,
    memory_id: Optional[int],
    snippet: str,
    priority: Optional[int] = None,
    source: str = "runtime",
    updated_at: Optional[str] = None,
) -> None:
    await runtime_state.session_cache.record_hit(
        session_id=get_session_id(),
        uri=uri,
        memory_id=memory_id,
        snippet=snippet,
        priority=priority,
        source=source,
        updated_at=updated_at,
    )


async def _record_flush_event(message: str) -> None:
    await runtime_state.flush_tracker.record_event(
        session_id=get_session_id(),
        message=_event_preview(message),
    )


def _normalize_guard_decision(
    decision: Any, *, allow_bypass: bool = False
) -> Dict[str, Any]:
    if not isinstance(decision, dict):
        decision = {}
    reason = str(decision.get("reason") or "").strip()
    has_action = "action" in decision
    raw_action = str(decision.get("action") or "").strip().upper() if has_action else ""
    action = raw_action
    valid_actions = {"ADD", "UPDATE", "NOOP", "DELETE"}
    if allow_bypass:
        valid_actions.add("BYPASS")
    if action not in valid_actions:
        action = "NOOP"
        marker_value = raw_action or ("EMPTY" if has_action else "MISSING")
        marker = f"invalid_guard_action:{marker_value}"
        reason = marker if not reason else f"{marker}; {reason}"
    method = str(decision.get("method") or "none").strip().lower() or "none"
    target_id = decision.get("target_id")
    if not isinstance(target_id, int) or target_id <= 0:
        target_id = None
    target_uri = decision.get("target_uri")
    if not isinstance(target_uri, str) or not target_uri.strip():
        target_uri = None

    degrade_reasons = decision.get("degrade_reasons")
    if not isinstance(degrade_reasons, list):
        degrade_reasons = []
    degrade_reasons = [item for item in degrade_reasons if isinstance(item, str) and item]

    return {
        "action": action,
        "method": method,
        "reason": reason,
        "target_id": target_id,
        "target_uri": target_uri,
        "degraded": bool(decision.get("degraded")),
        "degrade_reasons": degrade_reasons,
    }


def _guard_fields(decision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "guard_action": decision.get("action"),
        "guard_reason": decision.get("reason"),
        "guard_method": decision.get("method"),
        "guard_target_id": decision.get("target_id"),
        "guard_target_uri": decision.get("target_uri"),
    }


def _tool_response(*, ok: bool, message: str, **extra: Any) -> str:
    payload: Dict[str, Any] = {"ok": bool(ok), "message": message}
    payload.update(extra)
    return _to_json(payload)


async def _record_guard_event(
    *,
    operation: str,
    decision: Dict[str, Any],
    blocked: bool,
) -> None:
    await runtime_state.guard_tracker.record_event(
        operation=operation,
        action=str(decision.get("action") or "UNKNOWN"),
        method=str(decision.get("method") or "unknown"),
        reason=str(decision.get("reason") or ""),
        target_id=decision.get("target_id"),
        blocked=blocked,
        degraded=bool(decision.get("degraded")),
        degrade_reasons=decision.get("degrade_reasons"),
    )


def _normalize_path_prefix(path_prefix: Optional[str]) -> str:
    raw = str(path_prefix or "").strip().strip("/")
    if not raw:
        return "corrections"
    parts = [part for part in raw.split("/") if part]
    return "/".join(parts) if parts else "corrections"


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
    if not persist_runtime_meta:
        return
    try:
        client = get_sqlite_client()
        set_runtime_meta = getattr(client, "set_runtime_meta", None)
        if callable(set_runtime_meta):
            # Serialize summary persistence to avoid stale snapshots overwriting newer values.
            async with _IMPORT_LEARN_META_PERSIST_LOCK:
                summary_payload = await runtime_state.import_learn_tracker.summary()
                await set_runtime_meta(
                    IMPORT_LEARN_AUDIT_META_KEY,
                    _to_json(summary_payload),
                )
    except Exception:
        # Keep audit recording non-blocking for primary workflows.
        pass


def _safe_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _sanitize_import_learn_summary(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    return {
        "window_size": _safe_non_negative_int(payload.get("window_size")),
        "total_events": _safe_non_negative_int(payload.get("total_events")),
        "event_type_breakdown": payload.get("event_type_breakdown")
        if isinstance(payload.get("event_type_breakdown"), dict)
        else {},
        "operation_breakdown": payload.get("operation_breakdown")
        if isinstance(payload.get("operation_breakdown"), dict)
        else {},
        "decision_breakdown": payload.get("decision_breakdown")
        if isinstance(payload.get("decision_breakdown"), dict)
        else {},
        "rejected_events": _safe_non_negative_int(payload.get("rejected_events")),
        "rollback_events": _safe_non_negative_int(payload.get("rollback_events")),
        "top_reasons": payload.get("top_reasons")
        if isinstance(payload.get("top_reasons"), list)
        else [],
        "last_event_at": payload.get("last_event_at"),
        "recent_events": payload.get("recent_events")
        if isinstance(payload.get("recent_events"), list)
        else [],
    }


async def _load_persisted_import_learn_summary(client: Any) -> Optional[Dict[str, Any]]:
    get_runtime_meta = getattr(client, "get_runtime_meta", None)
    if not callable(get_runtime_meta):
        return None
    try:
        raw_value = await get_runtime_meta(IMPORT_LEARN_AUDIT_META_KEY)
    except Exception:
        return None
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return None
    return _sanitize_import_learn_summary(parsed)


def _merge_import_learn_summaries(
    runtime_summary: Dict[str, Any], persisted_summary: Dict[str, Any]
) -> Dict[str, Any]:
    merged = dict(runtime_summary)
    if _safe_non_negative_int(merged.get("total_events")) <= 0:
        merged["total_events"] = _safe_non_negative_int(
            persisted_summary.get("total_events")
        )
    if _safe_non_negative_int(merged.get("rejected_events")) <= 0:
        merged["rejected_events"] = _safe_non_negative_int(
            persisted_summary.get("rejected_events")
        )
    if _safe_non_negative_int(merged.get("rollback_events")) <= 0:
        merged["rollback_events"] = _safe_non_negative_int(
            persisted_summary.get("rollback_events")
        )
    if not isinstance(merged.get("event_type_breakdown"), dict) or not merged.get(
        "event_type_breakdown"
    ):
        merged["event_type_breakdown"] = dict(
            persisted_summary.get("event_type_breakdown") or {}
        )
    if not merged.get("last_event_at"):
        merged["last_event_at"] = persisted_summary.get("last_event_at")
    merged["persisted_snapshot"] = persisted_summary
    return merged


async def run_explicit_learn_service(
    *,
    content: str,
    source: str,
    reason: Optional[str],
    session_id: str,
    actor_id: Optional[str] = None,
    domain: str = "notes",
    path_prefix: str = "corrections",
    execute: bool = False,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Internal explicit learn service for H1/H3 rollout.

    This function is intentionally not exposed as an HTTP route or MCP tool.
    It enforces source/reason/session_id contracts and evaluates write_guard
    before any write. By default it runs in prepare-only mode (execute=False).
    For H3, execute=True performs explicit write and returns rollback metadata.
    """
    operation = "learn_explicit"
    normalized_source = str(source or "").strip()
    normalized_reason = str(reason or "").strip()
    normalized_session_id = str(session_id or "").strip()
    normalized_actor_id = (str(actor_id).strip() if actor_id is not None else None) or None
    normalized_domain = str(domain or "").strip().lower() or "notes"
    normalized_path_prefix = _normalize_path_prefix(path_prefix)
    target_parent_path = f"{normalized_path_prefix}/{normalized_session_id}".strip("/")
    target_parent_uri = make_uri(normalized_domain, target_parent_path)

    payload: Dict[str, Any] = {
        "ok": True,
        "accepted": False,
        "operation": operation,
        "source": normalized_source,
        "reason_text": normalized_reason,
        "session_id": normalized_session_id,
        "actor_id": normalized_actor_id,
        "domain": normalized_domain,
        "path_prefix": normalized_path_prefix,
        "target_parent_uri": target_parent_uri,
        "execute": bool(execute),
        "reason": "rejected",
    }

    if not _auto_learn_explicit_enabled():
        payload["reason"] = "auto_learn_explicit_disabled"
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason="auto_learn_explicit_disabled",
            source=normalized_source or "learn",
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
            metadata={"domain": normalized_domain},
            persist_runtime_meta=False,
        )
        return payload

    if not normalized_source:
        payload["reason"] = "source_required"
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason="source_required",
            source="learn",
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
        )
        return payload

    if _auto_learn_require_reason() and not normalized_reason:
        payload["reason"] = "reason_required"
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason="reason_required",
            source=normalized_source,
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
        )
        return payload

    if not normalized_session_id:
        payload["reason"] = "session_id_required"
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason="session_id_required",
            source=normalized_source,
            session_id=None,
            actor_id=normalized_actor_id,
        )
        return payload

    normalized_content = str(content or "").strip()
    if not normalized_content:
        payload["reason"] = "content_required"
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason="content_required",
            source=normalized_source,
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
        )
        return payload

    allowed_domains = _auto_learn_allowed_domains()
    payload["allowed_domains"] = allowed_domains
    if normalized_domain not in allowed_domains:
        payload["reason"] = "domain_not_allowed"
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason="domain_not_allowed",
            source=normalized_source,
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
            metadata={"domain": normalized_domain, "allowed_domains": allowed_domains},
        )
        return payload

    learn_client = client or get_sqlite_client()
    try:
        guard_decision = _normalize_guard_decision(
            await learn_client.write_guard(
                content=normalized_content,
                domain=normalized_domain,
                path_prefix=target_parent_path,
            )
        )
    except Exception as guard_exc:
        payload["reason"] = "write_guard_unavailable"
        payload.update(
            {
                "guard_action": "ERROR",
                "guard_reason": f"write_guard_unavailable:{type(guard_exc).__name__}",
                "guard_method": "exception",
                "guard_target_id": None,
                "guard_target_uri": None,
                "degraded": True,
                "degrade_reasons": ["write_guard_exception"],
            }
        )
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason="write_guard_unavailable",
            source=normalized_source,
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
            metadata={
                "domain": normalized_domain,
                "guard_error": type(guard_exc).__name__,
            },
        )
        return payload

    payload.update(_guard_fields(guard_decision))
    guard_action = str(guard_decision.get("action") or "ADD").upper()
    if guard_action != "ADD":
        payload["reason"] = f"write_guard_blocked:{guard_action.lower()}"
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason=payload["reason"],
            source=normalized_source,
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
            metadata={
                "guard_action": guard_action,
                "guard_method": guard_decision.get("method"),
            },
        )
        return payload

    payload["accepted"] = True
    payload["reason"] = "prepared"
    source_hash = _build_source_hash(
        f"{normalized_source}\n{normalized_reason}\n{normalized_content}"
    )
    payload["source_hash"] = source_hash

    safe_session_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", normalized_session_id).strip("-")
    if not safe_session_id:
        safe_session_id = "session"
    batch_id = f"learn-{safe_session_id[:24]}-{source_hash[:8]}-{uuid.uuid4().hex[:6]}"
    payload["batch_id"] = batch_id

    if not execute:
        await _record_import_learn_event(
            event_type="learn",
            operation=operation,
            decision="accepted",
            reason="prepared",
            source=normalized_source,
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
            batch_id=batch_id,
            metadata={
                "domain": normalized_domain,
                "path_prefix": normalized_path_prefix,
                "target_parent_uri": target_parent_uri,
            },
        )
        return payload

    learn_title = f"learn-{source_hash[:8]}-{uuid.uuid4().hex[:8]}"
    created_namespace_memories: List[Dict[str, Any]] = []
    try:
        (
            created_domain,
            created_parent_path,
            created_namespace_memories,
        ) = await _ensure_parent_path_exists(
            learn_client,
            target_parent_uri,
        )
        created = await learn_client.create_memory(
            parent_path=created_parent_path,
            content=normalized_content,
            priority=max(1, AUTO_FLUSH_PRIORITY),
            title=learn_title,
            disclosure="Explicit learn trigger",
            domain=created_domain,
        )
    except Exception as exec_exc:
        payload["accepted"] = False
        payload["reason"] = "create_memory_failed"
        payload["error"] = str(exec_exc) or type(exec_exc).__name__
        if created_namespace_memories:
            payload["created_namespace_memories"] = created_namespace_memories
            namespace_memory_ids = [
                _safe_int(item.get("memory_id"), default=0)
                for item in created_namespace_memories
                if isinstance(item, dict)
            ]
            namespace_memory_ids = [item for item in namespace_memory_ids if item > 0]
            payload["rollback"] = {
                "enabled": True,
                "mode": "namespace_cleanup_only",
                "memory_id": 0,
                "batch_id": batch_id,
                "namespace_memory_ids": namespace_memory_ids,
                "side_effects_audit_required": True,
                "residual_artifacts_review_required": True,
                "side_effects_note": "execute_failed_namespace_cleanup_required",
            }
        await _record_import_learn_event(
            event_type="reject",
            operation=operation,
            decision="rejected",
            reason="create_memory_failed",
            source=normalized_source,
            session_id=normalized_session_id,
            actor_id=normalized_actor_id,
            batch_id=batch_id,
            metadata={
                "domain": normalized_domain,
                "path_prefix": normalized_path_prefix,
                "target_parent_uri": target_parent_uri,
                "error": type(exec_exc).__name__,
                "created_namespace_count": len(created_namespace_memories),
            },
        )
        return payload

    created_memory_payload = {
        "id": int(created.get("id") or 0),
        "uri": str(created.get("uri") or ""),
        "domain": str(created.get("domain") or created_domain),
        "path": str(created.get("path") or ""),
    }
    payload["reason"] = "executed"
    payload["executed"] = True
    payload["created_memory"] = created_memory_payload
    payload["created_namespace_memories"] = created_namespace_memories
    namespace_memory_ids = [
        _safe_int(item.get("memory_id"), default=0)
        for item in created_namespace_memories
        if isinstance(item, dict)
    ]
    namespace_memory_ids = [item for item in namespace_memory_ids if item > 0]
    payload["rollback"] = {
        "enabled": True,
        "mode": "delete_memory_id",
        "memory_id": created_memory_payload["id"],
        "batch_id": batch_id,
        "namespace_memory_ids": namespace_memory_ids,
        "side_effects_audit_required": True,
        "residual_artifacts_review_required": True,
        "side_effects_note": (
            "rollback_covers_created_memory_ids_and_best_effort_namespace_cleanup"
            if namespace_memory_ids
            else "rollback_only_covers_created_memory_ids"
        ),
    }
    await _record_import_learn_event(
        event_type="learn",
        operation=operation,
        decision="executed",
        reason="executed",
        source=normalized_source,
        session_id=normalized_session_id,
        actor_id=normalized_actor_id,
        batch_id=batch_id,
        metadata={
            "domain": normalized_domain,
            "path_prefix": normalized_path_prefix,
            "target_parent_uri": target_parent_uri,
            "created_memory_id": created_memory_payload["id"],
            "created_uri": created_memory_payload["uri"],
            "created_namespace_count": len(namespace_memory_ids),
        },
    )
    return payload


def _merge_session_global_results(
    *, session_results: List[Dict[str, Any]], global_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    dedup_dropped = 0
    session_contributed = 0
    global_contributed = 0

    for index, item in enumerate(session_results + global_results):
        source_bucket = "session" if index < len(session_results) else "global"
        key = _search_result_identity(item)
        if key in seen:
            dedup_dropped += 1
            continue
        seen.add(key)
        merged.append(item)
        if source_bucket == "session":
            session_contributed += 1
        else:
            global_contributed += 1
    return merged, {
        "session_candidates": len(session_results),
        "global_candidates": len(global_results),
        "merged_candidates": len(merged),
        "dedup_dropped": dedup_dropped,
        "session_contributed": session_contributed,
        "global_contributed": global_contributed,
    }


def _search_result_identity(item: Dict[str, Any]) -> Any:
    uri = item.get("uri")
    if uri:
        return ("uri", str(uri))
    return (
        "fallback",
        item.get("domain"),
        item.get("path"),
        item.get("memory_id"),
        item.get("chunk_id"),
    )


async def _ensure_parent_path_exists(
    client: Any, parent_uri: str
) -> Tuple[str, str, List[Dict[str, Any]]]:
    domain, parent_path = parse_uri(parent_uri)
    if not parent_path:
        return domain, parent_path, []

    # Ensure all intermediate nodes exist for nested flush paths.
    segments = [segment for segment in parent_path.split("/") if segment]
    current_path = ""
    created_nodes: List[Dict[str, Any]] = []
    for segment in segments:
        next_path = f"{current_path}/{segment}" if current_path else segment
        exists = await client.get_memory_by_path(next_path, domain)
        if not exists:
            created = await client.create_memory(
                parent_path=current_path,
                content=f"[runtime] auto-created flush namespace: {make_uri(domain, next_path)}",
                priority=max(1, AUTO_FLUSH_PRIORITY),
                title=segment,
                disclosure="Runtime flush namespace",
                domain=domain,
            )
            created_nodes.append(
                {
                    "memory_id": _safe_int(created.get("id"), default=0),
                    "domain": domain,
                    "path": next_path,
                    "uri": make_uri(domain, next_path),
                }
            )
        current_path = next_path
    return domain, parent_path, created_nodes


_AUTO_FLUSH_IN_PROGRESS: set[str] = set()


def _build_source_hash(source: str) -> str:
    payload = (source or "").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _trim_sentence(text: str, limit: int = 90) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(8, limit - 3)].rstrip() + "..."


async def generate_gist(
    summary: str,
    *,
    client: Any = None,
    max_points: int = 3,
    max_chars: int = 280,
) -> Dict[str, Any]:
    """
    Build a compact gist with deterministic fallback chain.

    Fallback chain:
    1) llm_gist
    2) extractive_bullets
    3) sentence_fallback
    4) truncate_fallback
    """
    source = (summary or "").strip()
    if not source:
        return {"gist_text": "", "gist_method": "empty", "quality": 0.0}

    degrade_reasons: List[str] = []
    llm_gist_builder = getattr(client, "generate_compact_gist", None) if client else None
    if callable(llm_gist_builder):
        try:
            llm_payload = await llm_gist_builder(
                summary=source,
                max_points=max_points,
                max_chars=max_chars,
                degrade_reasons=degrade_reasons,
            )
            if isinstance(llm_payload, dict):
                llm_gist_text = str(llm_payload.get("gist_text") or "").strip()
                if llm_gist_text:
                    quality_value = llm_payload.get("quality")
                    try:
                        quality = float(quality_value)
                    except (TypeError, ValueError):
                        quality = 0.72
                    payload = {
                        "gist_text": llm_gist_text,
                        "gist_method": str(llm_payload.get("gist_method") or "llm_gist"),
                        "quality": round(max(0.0, min(1.0, quality)), 3),
                    }
                    if degrade_reasons:
                        payload["degrade_reasons"] = list(dict.fromkeys(degrade_reasons))
                    return payload
                degrade_reasons.append("compact_gist_llm_empty")
        except Exception as exc:
            degrade_reasons.append(f"compact_gist_llm_exception:{type(exc).__name__}")

    bullet_lines: List[str] = []
    for line in source.splitlines():
        line_value = line.strip()
        if not line_value:
            continue
        if line_value.startswith("Session compaction notes:"):
            continue
        if line_value.startswith("- "):
            bullet_lines.append(line_value[2:].strip())
        else:
            bullet_lines.append(line_value)

    extractive_parts: List[str] = []
    for line in bullet_lines:
        if not line:
            continue
        extractive_parts.append(_trim_sentence(line, limit=90))
        if len(extractive_parts) >= max(1, max_points):
            break

    extractive_gist = "; ".join(part for part in extractive_parts if part)
    if extractive_gist:
        gist_text = extractive_gist[: max(40, max_chars)].strip()
        quality = min(0.95, max(0.45, len(gist_text) / max(120.0, len(source) * 0.8)))
        payload = {
            "gist_text": gist_text,
            "gist_method": "extractive_bullets",
            "quality": round(float(quality), 3),
        }
        if degrade_reasons:
            payload["degrade_reasons"] = list(dict.fromkeys(degrade_reasons))
        return payload

    flattened = re.sub(r"\s+", " ", source)
    sentences = [item.strip() for item in re.split(r"(?<=[.!?。！？])\s+", flattened) if item.strip()]
    if sentences:
        gist_text = _trim_sentence(sentences[0], limit=max(48, max_chars))
        quality = 0.4 if len(sentences) == 1 else 0.52
        payload = {
            "gist_text": gist_text,
            "gist_method": "sentence_fallback",
            "quality": round(float(quality), 3),
        }
        if degrade_reasons:
            payload["degrade_reasons"] = list(dict.fromkeys(degrade_reasons))
        return payload

    gist_text = _trim_sentence(flattened, limit=max(32, max_chars))
    payload = {
        "gist_text": gist_text,
        "gist_method": "truncate_fallback",
        "quality": 0.3,
    }
    if degrade_reasons:
        payload["degrade_reasons"] = list(dict.fromkeys(degrade_reasons))
    return payload


async def _flush_session_summary_to_memory(
    *,
    client: Any,
    source: str,
    reason: str,
    force: bool,
    max_lines: int,
) -> Dict[str, Any]:
    session_id = get_session_id()
    should_flush = force or await runtime_state.flush_tracker.should_flush(
        session_id=session_id
    )
    if not should_flush:
        return {"flushed": False, "reason": "threshold_not_reached"}

    summary = await runtime_state.flush_tracker.build_summary(
        session_id=session_id, limit=max(1, max_lines)
    )
    if not summary.strip():
        return {"flushed": False, "reason": "no_pending_events"}

    gist_payload = await generate_gist(summary, client=client)
    gist_text = str(gist_payload.get("gist_text") or "").strip()
    gist_method = str(gist_payload.get("gist_method") or "truncate_fallback")
    quality_value = gist_payload.get("quality")
    try:
        gist_quality = float(quality_value)
    except (TypeError, ValueError):
        gist_quality = 0.0
    source_hash = _build_source_hash(summary)

    domain, parent_path, _ = await _ensure_parent_path_exists(
        client, AUTO_FLUSH_PARENT_URI
    )
    flush_title = f"auto_flush_{_utc_now_naive().strftime('%Y%m%d_%H%M%S')}"
    content = (
        f"# Runtime Session Flush\n"
        f"- session_id: {session_id}\n"
        f"- reason: {reason}\n"
        f"- flushed_at: {_utc_iso_now()}\n"
        f"- gist_method: {gist_method}\n"
        f"- quality: {round(gist_quality, 3)}\n"
        f"- source_hash: {source_hash}\n\n"
        f"## Gist\n"
        f"{gist_text or '(gist unavailable)'}\n\n"
        f"## Trace\n"
        f"{summary}"
    )
    guard_decision = _normalize_guard_decision(
        {"action": "ADD", "method": "none", "reason": "guard_not_evaluated"}
    )
    try:
        guard_decision = _normalize_guard_decision(
            await client.write_guard(
                content=content,
                domain=domain,
                path_prefix=parent_path if parent_path else None,
            )
        )
    except Exception as guard_exc:
        guard_decision = _normalize_guard_decision(
            {
                "action": "NOOP",
                "method": "exception",
                "reason": f"write_guard_unavailable: {guard_exc}",
                "degraded": True,
                "degrade_reasons": ["write_guard_exception"],
            }
        )
    guard_action = str(guard_decision.get("action") or "NOOP").upper()
    guard_blocked = guard_action != "ADD"
    try:
        await _record_guard_event(
            operation="compact_context",
            decision=guard_decision,
            blocked=guard_blocked,
        )
    except Exception:
        pass
    if guard_blocked:
        guard_is_degraded = bool(guard_decision.get("degraded"))
        guard_reason = str(guard_decision.get("reason") or "")
        guard_is_invalid = guard_reason.startswith("invalid_guard_action:")
        if (
            guard_action in {"NOOP", "UPDATE"}
            and not guard_is_degraded
            and not guard_is_invalid
        ):
            # Dedup/merge decisions mean this summary is already represented;
            # clear pending events to avoid infinite flush retries.
            await runtime_state.flush_tracker.mark_flushed(session_id=session_id)
            payload: Dict[str, Any] = {
                "flushed": True,
                "reason": "write_guard_deduped",
                **_guard_fields(guard_decision),
            }
            guard_target_uri = guard_decision.get("target_uri")
            if isinstance(guard_target_uri, str) and guard_target_uri.strip():
                payload["uri"] = guard_target_uri
            return payload

        payload: Dict[str, Any] = {
            "flushed": False,
            "reason": "write_guard_blocked",
            **_guard_fields(guard_decision),
        }
        if bool(guard_decision.get("degraded")):
            payload["degraded"] = True
            degrade_reasons = guard_decision.get("degrade_reasons")
            if isinstance(degrade_reasons, list) and degrade_reasons:
                payload["degrade_reasons"] = list(dict.fromkeys(degrade_reasons))
        return payload
    defer_index = await _should_defer_index_on_write()
    result = await client.create_memory(
        parent_path=parent_path,
        content=content,
        priority=AUTO_FLUSH_PRIORITY,
        title=flush_title,
        disclosure="Runtime auto flush summary",
        domain=domain,
        index_now=not defer_index,
    )
    index_enqueue = {"queued": [], "dropped": [], "deduped": []}
    if defer_index:
        index_enqueue = await _enqueue_index_targets(result, reason="compact_context")
    created_memory_id = _safe_int(result.get("id"), default=-1)
    gist_persisted = False
    gist_store_error: Optional[str] = None
    upsert_gist = getattr(client, "upsert_memory_gist", None)
    if callable(upsert_gist) and created_memory_id > 0:
        try:
            await upsert_gist(
                memory_id=created_memory_id,
                gist_text=gist_text or summary,
                source_hash=source_hash,
                gist_method=gist_method,
                quality_score=gist_quality,
            )
            gist_persisted = True
        except Exception as exc:
            gist_store_error = str(exc)
    await runtime_state.flush_tracker.mark_flushed(session_id=session_id)

    created_uri = result.get("uri", make_uri(domain, result.get("path", flush_title)))
    await _record_session_hit(
        uri=created_uri,
        memory_id=result.get("id"),
        snippet=content[:300],
        priority=AUTO_FLUSH_PRIORITY,
        source="auto_flush",
        updated_at=_utc_iso_now(),
    )
    payload: Dict[str, Any] = {
        "flushed": True,
        "uri": created_uri,
        **_guard_fields(guard_decision),
        "gist_method": gist_method,
        "quality": round(gist_quality, 3),
        "source_hash": source_hash,
        "gist_persisted": gist_persisted,
        "index_queued": len(index_enqueue["queued"]),
        "index_dropped": len(index_enqueue["dropped"]),
        "index_deduped": len(index_enqueue["deduped"]),
    }
    gist_degrade_reasons = gist_payload.get("degrade_reasons")
    if isinstance(gist_degrade_reasons, list):
        payload["degrade_reasons"] = [
            str(reason).strip()
            for reason in gist_degrade_reasons
            if isinstance(reason, str) and reason.strip()
        ]
    if gist_store_error:
        payload["gist_store_error"] = gist_store_error
    if index_enqueue["dropped"]:
        degrade_reasons = payload.setdefault("degrade_reasons", [])
        if "index_enqueue_dropped" not in degrade_reasons:
            degrade_reasons.append("index_enqueue_dropped")
    try:
        await runtime_state.promotion_tracker.record_event(
            session_id=session_id,
            source=source,
            trigger_reason=reason,
            uri=str(created_uri),
            memory_id=created_memory_id if created_memory_id > 0 else None,
            gist_method=gist_method,
            quality=gist_quality,
            degraded=bool(payload.get("degrade_reasons")) or bool(gist_store_error),
            degrade_reasons=payload.get("degrade_reasons"),
            index_queued=payload.get("index_queued", 0),
            index_dropped=payload.get("index_dropped", 0),
            index_deduped=payload.get("index_deduped", 0),
        )
    except Exception:
        pass
    return payload


async def _maybe_auto_flush(client: Any, *, reason: str) -> Optional[Dict[str, Any]]:
    if not AUTO_FLUSH_ENABLED:
        return None
    session_id = get_session_id()
    if session_id in _AUTO_FLUSH_IN_PROGRESS:
        return None
    _AUTO_FLUSH_IN_PROGRESS.add(session_id)
    try:
        return await _flush_session_summary_to_memory(
            client=client,
            source="auto_flush",
            reason=reason,
            force=False,
            max_lines=AUTO_FLUSH_SUMMARY_LINES,
        )
    finally:
        _AUTO_FLUSH_IN_PROGRESS.discard(session_id)


async def _run_write_lane(operation: str, fn):
    await runtime_state.ensure_started(get_sqlite_client)
    if not ENABLE_WRITE_LANE_QUEUE:
        return await fn()
    return await runtime_state.write_lanes.run_write(
        session_id=get_session_id(),
        operation=operation,
        task=fn,
    )


async def _should_defer_index_on_write() -> bool:
    if not ENABLE_INDEX_WORKER or not DEFER_INDEX_ON_WRITE:
        return False
    await runtime_state.ensure_started(get_sqlite_client)
    worker_status = await runtime_state.index_worker.status()
    return bool(worker_status.get("enabled") and worker_status.get("running"))


def _extract_index_targets(payload: Any) -> List[int]:
    if not isinstance(payload, dict):
        return []
    values = payload.get("index_targets")
    if not isinstance(values, list):
        return []
    targets: List[int] = []
    for item in values:
        parsed = _safe_int(item, default=-1)
        if parsed > 0:
            targets.append(parsed)
    return list(dict.fromkeys(targets))


async def _enqueue_index_targets(
    payload: Any, *, reason: str
) -> Dict[str, List[Dict[str, Any]]]:
    targets = _extract_index_targets(payload)
    if not targets:
        return {"queued": [], "dropped": [], "deduped": []}
    await runtime_state.ensure_started(get_sqlite_client)
    queued: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    deduped: List[Dict[str, Any]] = []
    for memory_id in targets:
        item = await runtime_state.index_worker.enqueue_reindex_memory(
            memory_id=memory_id,
            reason=reason,
        )
        if item.get("queued"):
            queued.append(item)
        elif item.get("dropped"):
            dropped.append(item)
        else:
            deduped.append(item)
    return {"queued": queued, "dropped": dropped, "deduped": deduped}


def _is_signature_mismatch(exc: TypeError) -> bool:
    """Best-effort check for kwargs signature mismatch."""
    message = str(exc)
    markers = (
        "unexpected keyword argument",
        "required positional argument",
        "required keyword-only argument",
        "positional arguments but",
        "got multiple values for argument",
    )
    return any(marker in message for marker in markers)


async def _try_client_method_variants(
    client: Any,
    method_names: List[str],
    kwargs_variants: List[Dict[str, Any]],
    *,
    continue_on_none: bool = False,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Any]:
    """
    Try multiple sqlite_client methods/kwargs combinations.

    Returns:
        (method_name, kwargs_used, result) or (None, None, None) if unavailable.
    """
    for method_name in method_names:
        method = getattr(client, method_name, None)
        if not callable(method):
            continue

        for kwargs in kwargs_variants:
            try:
                result = method(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                if continue_on_none and result is None:
                    continue
                return method_name, kwargs, result
            except TypeError as exc:
                if _is_signature_mismatch(exc):
                    continue
                raise

    return None, None, None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO8601 datetime string (supports trailing Z)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"Invalid datetime '{value}'. Use ISO-8601 like '2026-01-31T12:00:00Z'."
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _normalize_search_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and normalize search filters."""
    if filters is None:
        return {}
    if not isinstance(filters, dict):
        raise ValueError(
            "filters must be an object with optional fields: "
            "domain/path_prefix/max_priority/updated_after."
        )

    allowed_keys = {"domain", "path_prefix", "max_priority", "updated_after"}
    unknown = set(filters.keys()) - allowed_keys
    if unknown:
        raise ValueError(
            f"Unknown filters: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(allowed_keys))}."
        )

    normalized: Dict[str, Any] = {}

    domain = filters.get("domain")
    if domain is not None:
        domain_value = str(domain).strip().lower()
        if domain_value:
            if domain_value not in VALID_DOMAINS:
                raise ValueError(
                    f"Unknown domain '{domain_value}'. "
                    f"Valid domains: {', '.join(VALID_DOMAINS)}"
                )
            normalized["domain"] = domain_value

    path_prefix = filters.get("path_prefix")
    if path_prefix is not None:
        path_value = str(path_prefix).strip()
        if path_value:
            if "://" in path_value:
                parsed_domain, parsed_path = parse_uri(path_value)
                normalized.setdefault("domain", parsed_domain)
                normalized["path_prefix"] = parsed_path
            else:
                normalized["path_prefix"] = path_value.strip("/")

    max_priority = filters.get("max_priority")
    if max_priority is not None:
        try:
            normalized["max_priority"] = int(max_priority)
        except (TypeError, ValueError) as exc:
            raise ValueError("filters.max_priority must be an integer.") from exc

    updated_after = filters.get("updated_after")
    if updated_after is not None:
        parsed = _parse_iso_datetime(str(updated_after))
        if parsed is not None:
            normalized["updated_after"] = parsed.isoformat()

    return normalized


def _normalize_scope_hint(scope_hint: Optional[Any]) -> Dict[str, Any]:
    """Normalize scope hint from query side without changing schema contracts."""
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
        parsed_domain, parsed_path = parse_uri(raw_value)
        return {
            "provided": True,
            "raw": raw_value,
            "domain": parsed_domain,
            "path_prefix": parsed_path or None,
            "strategy": "uri_prefix" if parsed_path else "domain_uri",
        }

    lowered = raw_value.lower()
    if lowered in VALID_DOMAINS:
        return {
            "provided": True,
            "raw": raw_value,
            "domain": lowered,
            "path_prefix": None,
            "strategy": "domain",
        }

    prefix = raw_value.strip("/")
    return {
        "provided": bool(prefix),
        "raw": raw_value,
        "domain": None,
        "path_prefix": prefix or None,
        "strategy": "path_prefix" if prefix else "none",
    }


def _merge_scope_hint_with_filters(
    *,
    normalized_filters: Dict[str, Any],
    scope_hint: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Merge scope hint into filters with filter-first conflict handling."""
    merged = dict(normalized_filters)
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
        if domain_conflict:
            resolution = {
                "provided": provided,
                "raw": scope_hint.get("raw"),
                "strategy": "filters_preferred",
                "applied": applied,
                "effective": {
                    "domain": merged.get("domain"),
                    "path_prefix": merged.get("path_prefix"),
                },
                "conflicts": conflicts,
            }
            return merged, resolution
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
                # Existing filter is narrower, keep filter as source of truth.
                pass
            elif hint_prefix_norm.startswith(existing_prefix_norm):
                # Hint is narrower than existing filter, apply hint.
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


def _normalize_search_item(item: Any) -> Dict[str, Any]:
    """Normalize one sqlite search result item."""
    if not isinstance(item, dict):
        return {"raw": item}

    metadata_obj = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    scores_obj = item.get("scores") if isinstance(item.get("scores"), dict) else {}
    char_range = item.get("char_range")

    domain = item.get("domain")
    path = item.get("path")
    uri = item.get("uri")

    if domain is None:
        domain = metadata_obj.get("domain")
    if path is None:
        path = metadata_obj.get("path")

    if uri and (domain is None or path is None):
        try:
            parsed_domain, parsed_path = parse_uri(str(uri))
            if domain is None:
                domain = parsed_domain
            if path is None:
                path = parsed_path
        except ValueError:
            pass

    if uri is None and domain is not None and path is not None:
        uri = make_uri(str(domain), str(path))

    snippet = (
        item.get("snippet")
        or item.get("content_snippet")
        or item.get("preview")
        or item.get("excerpt")
    )
    if snippet is None and item.get("content"):
        snippet = str(item["content"])[:200]

    priority = item.get("priority")
    if priority is None:
        priority = metadata_obj.get("priority")
    if priority is not None:
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            pass

    chunk_start = item.get("chunk_start")
    chunk_end = item.get("chunk_end")
    if isinstance(char_range, (list, tuple)) and len(char_range) >= 2:
        chunk_start = char_range[0]
        chunk_end = char_range[1]

    normalized: Dict[str, Any] = {
        "uri": uri,
        "domain": domain,
        "path": path,
        "memory_id": item.get("memory_id", item.get("id")),
        "name": item.get("name"),
        "priority": priority,
        "score": item.get("score", scores_obj.get("final")),
        "semantic_score": item.get("semantic_score", scores_obj.get("vector")),
        "keyword_score": item.get("keyword_score", scores_obj.get("text")),
        "snippet": snippet,
        "updated_at": item.get("updated_at")
        or metadata_obj.get("updated_at")
        or item.get("created_at"),
        "chunk_id": item.get("chunk_id"),
        "chunk_start": chunk_start,
        "chunk_end": chunk_end,
        "match_type": item.get("match_type"),
        "source": item.get("source"),
        "disclosure": item.get("disclosure", metadata_obj.get("disclosure")),
    }
    return {k: v for k, v in normalized.items() if v is not None}


def _extract_search_payload(raw_result: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Extract results list + metadata from unknown sqlite return shape."""
    metadata: Dict[str, Any] = {}
    raw_items: List[Any] = []

    if isinstance(raw_result, dict):
        if isinstance(raw_result.get("results"), list):
            raw_items = raw_result["results"]
        elif isinstance(raw_result.get("items"), list):
            raw_items = raw_result["items"]
        elif isinstance(raw_result.get("matches"), list):
            raw_items = raw_result["matches"]
        metadata = {
            k: v
            for k, v in raw_result.items()
            if k not in {"results", "items", "matches"}
        }
    elif isinstance(raw_result, list):
        raw_items = raw_result
    elif raw_result is not None:
        metadata["raw_result"] = raw_result

    normalized_items = [_normalize_search_item(item) for item in raw_items]
    return normalized_items, metadata


def _apply_local_filters_to_results(
    results: List[Dict[str, Any]], filters: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply requested filters locally when backend cannot enforce them."""
    filtered = list(results)
    degradation_reasons: List[str] = []

    domain = filters.get("domain")
    if domain:
        filtered = [item for item in filtered if item.get("domain") == domain]

    path_prefix = filters.get("path_prefix")
    if path_prefix:
        dropped = 0
        kept: List[Dict[str, Any]] = []
        for item in filtered:
            path = item.get("path")
            if path and str(path).startswith(path_prefix):
                kept.append(item)
            else:
                dropped += 1
        if dropped:
            degradation_reasons.append(
                f"path_prefix filter dropped {dropped} result(s) with missing/non-matching path."
            )
        filtered = kept

    max_priority = filters.get("max_priority")
    if max_priority is not None:
        dropped = 0
        kept = []
        for item in filtered:
            priority = item.get("priority")
            if isinstance(priority, int) and priority <= max_priority:
                kept.append(item)
            else:
                dropped += 1
        if dropped:
            degradation_reasons.append(
                f"max_priority filter dropped {dropped} result(s) with missing/non-matching priority."
            )
        filtered = kept

    updated_after = filters.get("updated_after")
    if updated_after:
        cutoff = _parse_iso_datetime(updated_after)
        dropped = 0
        comparable = 0
        kept = []
        for item in filtered:
            updated_raw = item.get("updated_at")
            if not updated_raw:
                dropped += 1
                continue
            try:
                updated = _parse_iso_datetime(str(updated_raw))
            except ValueError:
                dropped += 1
                continue
            comparable += 1
            if updated and cutoff and updated >= cutoff:
                kept.append(item)
            else:
                dropped += 1

        if comparable == 0 and filtered:
            degradation_reasons.append(
                "updated_after filter ignored locally because results have no parseable updated_at."
            )
        else:
            if dropped:
                degradation_reasons.append(
                    f"updated_after filter dropped {dropped} result(s)."
                )
            filtered = kept

    return filtered, degradation_reasons


def _parse_range_spec(range_value: Optional[str]) -> Optional[Tuple[int, int]]:
    """Parse `start:end` or `start-end` range spec."""
    if range_value is None:
        return None
    text = str(range_value).strip()
    if not text:
        return None
    match = re.match(r"^(\d+)\s*[:,-]\s*(\d+)$", text)
    if not match:
        raise ValueError(
            "Invalid range format. Use `start:end` (e.g., `0:500`) or `start-end`."
        )
    start = int(match.group(1))
    end = int(match.group(2))
    if end <= start:
        raise ValueError("Invalid range: end must be greater than start.")
    return start, end


def _slice_text_content(
    content: str,
    chunk_id: Optional[int],
    range_spec: Optional[Tuple[int, int]],
    max_chars: Optional[int],
) -> Tuple[str, Dict[str, Any]]:
    """Slice content by chunk/range/max_chars."""
    total_chars = len(content)
    start = 0
    end = total_chars
    mode = "full"

    if chunk_id is not None:
        stride = max(1, READ_CHUNK_SIZE - READ_CHUNK_OVERLAP)
        start = chunk_id * stride
        if start >= total_chars:
            raise ValueError(
                f"chunk_id={chunk_id} is out of range for content length {total_chars}."
            )
        end = min(total_chars, start + READ_CHUNK_SIZE)
        mode = "chunk"
    elif range_spec is not None:
        start, end = range_spec
        if start >= total_chars:
            raise ValueError(
                f"range start {start} is out of range for content length {total_chars}."
            )
        end = min(end, total_chars)
        mode = "range"

    selected = content[start:end]
    truncated = False
    if max_chars is not None and len(selected) > max_chars:
        selected = selected[:max_chars]
        end = start + len(selected)
        truncated = True

    return selected, {
        "mode": mode,
        "start": start,
        "end": end,
        "selected_chars": len(selected),
        "total_chars": total_chars,
        "truncated_by_max_chars": truncated,
    }


async def _resolve_system_uri(uri: str) -> Optional[str]:
    """Resolve system:// URI values, or return None if not a system URI."""
    stripped = uri.strip()
    if stripped == "system://boot":
        return await _generate_boot_memory_view()
    if stripped == "system://index":
        return await _generate_memory_index_view()
    if stripped == "system://index-lite":
        return await _generate_index_lite_memory_view()
    if stripped == "system://audit":
        return await _generate_audit_memory_view()
    if stripped == "system://recent" or stripped.startswith("system://recent/"):
        limit = 10
        suffix = stripped[len("system://recent") :].strip("/")
        if suffix:
            try:
                limit = max(1, min(100, int(suffix)))
            except ValueError as exc:
                raise ValueError(
                    "Invalid system://recent URI. "
                    "Use system://recent or system://recent/N."
                ) from exc
        return await _generate_recent_memories_view(limit=limit)
    return None


async def _build_index_status_payload(client: Any) -> Dict[str, Any]:
    """Build index status with sqlite_client-first strategy and safe fallback."""
    method_name, _, status = await _try_client_method_variants(
        client,
        [
            "get_index_status",
            "index_status",
            "get_retrieval_status",
            "get_search_index_status",
        ],
        [{}],
    )

    if method_name:
        payload = status if isinstance(status, dict) else {"raw_status": status}
        payload.setdefault("index_available", True)
        payload.setdefault("degraded", False)
        payload["source"] = f"sqlite_client.{method_name}"
        return payload

    paths = await client.get_all_paths()
    domain_counts: Dict[str, int] = {}
    min_priority: Optional[int] = None
    max_priority: Optional[int] = None

    for item in paths:
        domain = item.get("domain", DEFAULT_DOMAIN)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        priority = item.get("priority")
        if isinstance(priority, int):
            min_priority = priority if min_priority is None else min(min_priority, priority)
            max_priority = priority if max_priority is None else max(max_priority, priority)

    return {
        "index_available": False,
        "degraded": True,
        "reason": "sqlite_client index status API not available; returned fallback stats",
        "source": "mcp_server.fallback",
        "stats": {
            "total_paths": len(paths),
            "domain_counts": domain_counts,
            "min_priority": min_priority,
            "max_priority": max_priority,
            "retrieval_chunk_size": READ_CHUNK_SIZE,
            "retrieval_chunk_overlap": READ_CHUNK_OVERLAP,
        },
    }


async def _build_sm_lite_stats() -> Dict[str, Any]:
    """Collect runtime short-memory stats without introducing persistent storage."""
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


async def _collect_ancestor_memories(
    client: Any, *, domain: str, path: str, max_hops: int = 64
) -> List[Dict[str, Any]]:
    """Collect ancestor memories from parent path to root, deduplicated."""
    path_value = (path or "").strip().strip("/")
    if not path_value:
        return []

    segments = [segment for segment in path_value.split("/") if segment]
    if len(segments) <= 1:
        return []

    ancestors: List[Dict[str, Any]] = []
    seen_keys: set[Tuple[Any, str]] = set()
    for depth in range(len(segments) - 1, 0, -1):
        if len(ancestors) >= max_hops:
            break
        candidate_path = "/".join(segments[:depth])
        memory = await client.get_memory_by_path(candidate_path, domain)
        if not memory:
            continue
        ancestor_uri = make_uri(domain, candidate_path)
        key = (
            memory.get("id"),
            ancestor_uri,
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ancestors.append(
            {
                "uri": ancestor_uri,
                "memory_id": memory.get("id"),
                "priority": memory.get("priority", 0),
                "disclosure": memory.get("disclosure"),
                "content_snippet": _event_preview(str(memory.get("content", "")), 160),
            }
        )
    return ancestors


async def _fetch_and_format_memory(
    client,
    uri: str,
    *,
    include_ancestors: bool = False,
) -> str:
    """
    Internal helper to fetch memory data and return formatted string.
    Used by read_memory tool.
    """
    domain, path = parse_uri(uri)

    # Get the memory
    memory = await client.get_memory_by_path(path, domain)

    if not memory:
        raise ValueError(f"URI '{make_uri(domain, path)}' not found.")

    # Build URI from domain and path
    disp_domain = memory.get("domain", DEFAULT_DOMAIN)
    disp_path = memory.get("path", "unknown")
    disp_uri = make_uri(disp_domain, disp_path)

    # Get children across ALL paths (aliases) of this memory.
    # Once you reach a memory, the sub-memories you see depend on
    # what the memory IS, not which path you used to get here.
    children = await client.get_children(memory["id"])
    ancestors: List[Dict[str, Any]] = []
    ancestors_lookup_failed = False
    if include_ancestors:
        try:
            ancestors = await _collect_ancestor_memories(
                client,
                domain=disp_domain,
                path=disp_path,
            )
        except Exception:
            # Keep legacy read output usable even if ancestor expansion fails.
            ancestors = []
            ancestors_lookup_failed = True

    # Format output
    lines = []

    # Header Block
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"MEMORY: {disp_uri}")
    lines.append(f"Memory ID: {memory.get('id')}")
    lines.append(f"Priority: {memory.get('priority', 0)}")

    disclosure = memory.get("disclosure")
    if disclosure:
        lines.append(f"Disclosure: {disclosure}")
    else:
        lines.append("Disclosure: (not set)")

    lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # Content - directly, no header
    lines.append(memory.get("content", "(empty)"))
    lines.append("")

    if include_ancestors:
        lines.append("=" * 60)
        lines.append("")
        lines.append("ANCESTOR MEMORIES (Nearest Parent -> Root)")
        lines.append("")
        lines.append("=" * 60)
        lines.append("")
        if ancestors_lookup_failed:
            lines.append("(Ancestor lookup degraded: include_ancestors_lookup_failed.)")
            lines.append("")
        elif ancestors:
            for ancestor in ancestors:
                lines.append(
                    f"- URI: {ancestor.get('uri')} [#{ancestor.get('memory_id')}]"
                )
                lines.append(f"  Priority: {ancestor.get('priority', 0)}")
                disclosure = ancestor.get("disclosure")
                if disclosure:
                    lines.append(f"  When to recall: {disclosure}")
                else:
                    lines.append("  When to recall: (not set)")
                lines.append(
                    f"  Snippet: {ancestor.get('content_snippet') or '(empty)'}"
                )
                lines.append("")
        else:
            lines.append("(No ancestor memories found.)")
            lines.append("")

    if children:
        lines.append("=" * 60)
        lines.append("")
        lines.append("CHILD MEMORIES (Use 'read_memory' with URI to access)")
        lines.append("")
        lines.append("=" * 60)
        lines.append("")

        for child in children:
            child_domain = child.get("domain", disp_domain)
            child_path = child.get("path", "")
            child_uri = make_uri(child_domain, child_path)

            # Show disclosure status and snippet
            child_disclosure = child.get("disclosure")
            snippet = child.get("content_snippet", "")

            lines.append(f"- URI: {child_uri}  ")
            lines.append(f"  Priority: {child.get('priority', 0)}  ")

            if child_disclosure:
                lines.append(f"  When to recall: {child_disclosure}  ")
            else:
                lines.append("  When to recall: (not set)  ")
                lines.append(f"  Snippet: {snippet}  ")

            lines.append("")

    return "\n".join(lines)


def _should_expose_index_lite_in_boot() -> bool:
    """Gate index-lite entry point in boot output with a conservative default."""
    return INDEX_LITE_ENABLED


async def _generate_index_lite_memory_view(limit: int = 20) -> str:
    """
    Generate a lightweight gist-backed index summary.

    This view intentionally stays compact and only surfaces entries that have
    materialized gist rows.
    """
    client = get_sqlite_client()
    generated_at = _utc_iso_now()
    target_limit = max(1, min(100, int(limit)))
    degrade_reasons: List[str] = []

    gist_stats: Dict[str, Any] = {}
    gist_stats_getter = getattr(client, "get_gist_stats", None)
    if callable(gist_stats_getter):
        try:
            raw_stats = await gist_stats_getter()
            if isinstance(raw_stats, dict):
                gist_stats = raw_stats
            else:
                degrade_reasons.append("invalid_gist_stats_payload")
        except Exception:
            degrade_reasons.append("gist_stats_error")
    else:
        degrade_reasons.append("gist_stats_unavailable")

    recent_entries: List[Dict[str, Any]] = []
    recent_getter = getattr(client, "get_recent_memories", None)
    if callable(recent_getter):
        try:
            raw_recent = await recent_getter(limit=max(target_limit * 3, 20))
            if isinstance(raw_recent, list):
                recent_entries = [row for row in raw_recent if isinstance(row, dict)]
            else:
                degrade_reasons.append("invalid_recent_memories_payload")
        except Exception:
            degrade_reasons.append("recent_memories_error")
    else:
        degrade_reasons.append("recent_memories_unavailable")

    gist_lookup = getattr(client, "get_latest_memory_gist", None)
    index_items: List[Dict[str, Any]] = []
    if callable(gist_lookup):
        for row in recent_entries:
            memory_id = _safe_int(row.get("memory_id"), default=-1)
            if memory_id <= 0:
                continue
            uri = str(row.get("uri") or "").strip()
            if not uri:
                continue
            try:
                gist_row = await gist_lookup(memory_id)
            except Exception:
                gist_row = None
                if "latest_gist_lookup_error" not in degrade_reasons:
                    degrade_reasons.append("latest_gist_lookup_error")
            if not isinstance(gist_row, dict):
                continue
            gist_text = str(gist_row.get("gist_text") or "").strip()
            if not gist_text:
                continue
            quality_raw = gist_row.get("quality_score")
            try:
                quality_value = round(float(quality_raw), 3)
            except (TypeError, ValueError):
                quality_value = None
            index_items.append(
                {
                    "uri": uri,
                    "memory_id": memory_id,
                    "gist_method": str(gist_row.get("gist_method") or "unknown"),
                    "quality_score": quality_value,
                    "gist_preview": _trim_sentence(gist_text, limit=96),
                    "updated_at": row.get("created_at"),
                }
            )
            if len(index_items) >= target_limit:
                break
    else:
        degrade_reasons.append("latest_gist_lookup_unavailable")

    lines: List[str] = []
    lines.append("# Memory Index Lite")
    lines.append(f"# Generated: {generated_at}")
    lines.append(f"# Entry count: {len(index_items)}")
    if isinstance(gist_stats.get("total_rows"), int):
        lines.append(f"# Gist rows: {gist_stats.get('total_rows')}")
    if isinstance(gist_stats.get("active_coverage"), (int, float)):
        lines.append(f"# Gist active coverage: {gist_stats.get('active_coverage')}")
    if degrade_reasons:
        lines.append("# Status: degraded")
        lines.append(f"# degrade_reason: {', '.join(sorted(set(degrade_reasons)))}")
    else:
        lines.append("# Status: ok")
    lines.append("")

    if not index_items:
        lines.append("(No gist-backed entries found.)")
    else:
        for idx, item in enumerate(index_items, 1):
            quality_value = item.get("quality_score")
            quality_text = (
                "n/a" if quality_value is None else f"{float(quality_value):.3f}"
            )
            lines.append(
                f"{idx}. {item['uri']} [#{item['memory_id']}] "
                f"(method={item['gist_method']}, quality={quality_text})"
            )
            lines.append(f"   gist: {item['gist_preview']}")
            if item.get("updated_at"):
                lines.append(f"   updated_at: {item['updated_at']}")
            lines.append("")

    return "\n".join(lines)


async def _generate_audit_memory_view() -> str:
    """Generate a consolidated audit view for index/guard/gist/vitality and SM-Lite."""
    client = get_sqlite_client()
    await runtime_state.ensure_started(get_sqlite_client)

    generated_at = _utc_iso_now()
    degrade_reasons: List[str] = []

    try:
        index_status = await _build_index_status_payload(client)
    except Exception as exc:
        index_status = {"degraded": True, "reason": str(exc), "index_available": False}
        degrade_reasons.append("index_status_error")
    if bool(index_status.get("degraded")):
        degrade_reasons.append(
            f"index:{str(index_status.get('reason') or 'degraded')}"
        )

    try:
        guard_stats = await runtime_state.guard_tracker.summary()
    except Exception as exc:
        guard_stats = {"degraded": True, "reason": str(exc)}
        degrade_reasons.append("guard_stats_error")

    try:
        import_learn_stats = await runtime_state.import_learn_tracker.summary()
    except Exception as exc:
        import_learn_stats = {"degraded": True, "reason": str(exc)}
        degrade_reasons.append("import_learn:error")
    if bool(import_learn_stats.get("degraded")):
        degrade_reasons.append(
            f"import_learn:{str(import_learn_stats.get('reason') or 'degraded')}"
        )
    persisted_import_learn_stats = await _load_persisted_import_learn_summary(client)
    if isinstance(import_learn_stats, dict) and isinstance(
        persisted_import_learn_stats, dict
    ):
        runtime_total = _safe_non_negative_int(import_learn_stats.get("total_events"))
        if runtime_total <= 0:
            import_learn_stats = _merge_import_learn_summaries(
                runtime_summary=import_learn_stats,
                persisted_summary=persisted_import_learn_stats,
            )

    gist_stats_getter = getattr(client, "get_gist_stats", None)
    if callable(gist_stats_getter):
        try:
            gist_stats = await gist_stats_getter()
            if not isinstance(gist_stats, dict):
                gist_stats = {"degraded": True, "reason": "invalid_gist_stats_payload"}
                degrade_reasons.append("gist:invalid_payload")
        except Exception as exc:
            gist_stats = {"degraded": True, "reason": str(exc)}
            degrade_reasons.append("gist:error")
    else:
        gist_stats = {"degraded": True, "reason": "gist_stats_unavailable"}
        degrade_reasons.append("gist:unavailable")
    if bool(gist_stats.get("degraded")):
        degrade_reasons.append(f"gist:{str(gist_stats.get('reason') or 'degraded')}")

    vitality_stats_getter = getattr(client, "get_vitality_stats", None)
    if callable(vitality_stats_getter):
        try:
            vitality_stats = await vitality_stats_getter()
            if not isinstance(vitality_stats, dict):
                vitality_stats = {
                    "degraded": True,
                    "reason": "invalid_vitality_stats_payload",
                }
                degrade_reasons.append("vitality:invalid_payload")
        except Exception as exc:
            vitality_stats = {"degraded": True, "reason": str(exc)}
            degrade_reasons.append("vitality:error")
    else:
        vitality_stats = {"degraded": True, "reason": "vitality_stats_unavailable"}
        degrade_reasons.append("vitality:unavailable")
    if bool(vitality_stats.get("degraded")):
        degrade_reasons.append(
            f"vitality:{str(vitality_stats.get('reason') or 'degraded')}"
        )

    try:
        sm_lite = await _build_sm_lite_stats()
    except Exception as exc:
        sm_lite = {"degraded": True, "reason": str(exc)}
        degrade_reasons.append("sm_lite:error")
    if bool(sm_lite.get("degraded")):
        degrade_reasons.append(f"sm_lite:{str(sm_lite.get('reason') or 'degraded')}")

    lines: List[str] = []
    lines.append("# System Audit")
    lines.append(f"# Generated: {generated_at}")
    lines.append(f"# Status: {'degraded' if degrade_reasons else 'ok'}")
    if degrade_reasons:
        lines.append(f"# degrade_reason: {', '.join(sorted(set(degrade_reasons)))}")
    lines.append("")

    lines.append("## Index")
    lines.append(
        f"- index_available: {bool(index_status.get('index_available', False))}"
    )
    lines.append(f"- degraded: {bool(index_status.get('degraded', False))}")
    if index_status.get("reason"):
        lines.append(f"- reason: {index_status.get('reason')}")
    lines.append(f"- source: {index_status.get('source', 'unknown')}")
    lines.append("")

    lines.append("## Guard")
    lines.append(f"- total_events: {guard_stats.get('total_events', 0)}")
    lines.append(f"- blocked_events: {guard_stats.get('blocked_events', 0)}")
    lines.append(f"- degraded_events: {guard_stats.get('degraded_events', 0)}")
    lines.append(f"- last_event_at: {guard_stats.get('last_event_at') or 'n/a'}")
    lines.append("")

    lines.append("## Import/Learn")
    lines.append(f"- total_events: {import_learn_stats.get('total_events', 0)}")
    lines.append(f"- rejected_events: {import_learn_stats.get('rejected_events', 0)}")
    lines.append(f"- rollback_events: {import_learn_stats.get('rollback_events', 0)}")
    lines.append(
        f"- learn_events: {import_learn_stats.get('event_type_breakdown', {}).get('learn', 0)}"
    )
    lines.append(
        f"- import_events: {import_learn_stats.get('event_type_breakdown', {}).get('import', 0)}"
    )
    lines.append(
        f"- last_event_at: {import_learn_stats.get('last_event_at') or 'n/a'}"
    )
    if isinstance(import_learn_stats.get("persisted_snapshot"), dict):
        persisted = import_learn_stats["persisted_snapshot"]
        lines.append("- persisted_snapshot: true")
        lines.append(
            f"- persisted_total_events: {_safe_non_negative_int(persisted.get('total_events'))}"
        )
        lines.append(f"- persisted_last_event_at: {persisted.get('last_event_at') or 'n/a'}")
    else:
        lines.append("- persisted_snapshot: false")
    lines.append("")

    lines.append("## Gist")
    lines.append(f"- degraded: {bool(gist_stats.get('degraded', False))}")
    if gist_stats.get("reason"):
        lines.append(f"- reason: {gist_stats.get('reason')}")
    if gist_stats.get("total_rows") is not None:
        lines.append(f"- total_rows: {gist_stats.get('total_rows')}")
    if gist_stats.get("active_coverage") is not None:
        lines.append(f"- active_coverage: {gist_stats.get('active_coverage')}")
    lines.append("")

    lines.append("## Vitality")
    lines.append(f"- degraded: {bool(vitality_stats.get('degraded', False))}")
    if vitality_stats.get("reason"):
        lines.append(f"- reason: {vitality_stats.get('reason')}")
    if vitality_stats.get("total_memories") is not None:
        lines.append(f"- total_memories: {vitality_stats.get('total_memories')}")
    if vitality_stats.get("low_vitality_count") is not None:
        lines.append(f"- low_vitality_count: {vitality_stats.get('low_vitality_count')}")
    lines.append("")

    lines.append("## SM-Lite (Runtime Working Set)")
    session_cache = sm_lite.get("session_cache", {}) if isinstance(sm_lite, dict) else {}
    flush_tracker = sm_lite.get("flush_tracker", {}) if isinstance(sm_lite, dict) else {}
    promotion = sm_lite.get("promotion", {}) if isinstance(sm_lite, dict) else {}
    lines.append(f"- storage: {sm_lite.get('storage', 'runtime_ephemeral')}")
    lines.append(
        f"- promotion_path: {sm_lite.get('promotion_path', 'compact_context + auto_flush')}"
    )
    lines.append(f"- session_cache.session_count: {session_cache.get('session_count', 0)}")
    lines.append(f"- session_cache.total_hits: {session_cache.get('total_hits', 0)}")
    lines.append(f"- flush_tracker.session_count: {flush_tracker.get('session_count', 0)}")
    lines.append(f"- flush_tracker.pending_events: {flush_tracker.get('pending_events', 0)}")
    lines.append(
        f"- promotion.total_promotions: {promotion.get('total_promotions', 0)}"
    )
    lines.append(
        f"- promotion.degraded_promotions: {promotion.get('degraded_promotions', 0)}"
    )
    lines.append(f"- promotion.avg_quality: {promotion.get('avg_quality', 0.0)}")
    lines.append("")

    if AUDIT_VERBOSE:
        lines.append("## Verbose Payloads")
        lines.append("")
        lines.append("### index_status")
        lines.append(_to_json(index_status))
        lines.append("")
        lines.append("### guard_stats")
        lines.append(_to_json(guard_stats))
        lines.append("")
        lines.append("### import_learn_audit_stats")
        lines.append(_to_json(import_learn_stats))
        lines.append("")
        lines.append("### gist_stats")
        lines.append(_to_json(gist_stats))
        lines.append("")
        lines.append("### vitality_stats")
        lines.append(_to_json(vitality_stats))
        lines.append("")
        lines.append("### sm_lite")
        lines.append(_to_json(sm_lite))
        lines.append("")

    return "\n".join(lines)


async def _generate_boot_memory_view() -> str:
    """
    Internal helper to generate the system boot memory view.
    (Formerly system://core)
    """
    client = get_sqlite_client()
    results = []
    loaded = 0
    failed = []

    for uri in CORE_MEMORY_URIS:
        try:
            content = await _fetch_and_format_memory(client, uri)
            results.append(content)
            loaded += 1
        except Exception as e:
            # e.g. not found or other error
            failed.append(f"- {uri}: {str(e)}")

    # Build output
    output_parts = []

    output_parts.append("# Core Memories")
    output_parts.append(f"# Loaded: {loaded}/{len(CORE_MEMORY_URIS)} memories")
    output_parts.append("")

    if failed:
        output_parts.append("## Failed to load:")
        output_parts.extend(failed)
        output_parts.append("")

    if results:
        output_parts.append("## Contents:")
        output_parts.append("")
        output_parts.append("For full memory index, use: system://index")
        output_parts.append("For recent memories, use: system://recent")
        if _should_expose_index_lite_in_boot():
            output_parts.append("For gist-backed lightweight index, use: system://index-lite")
        output_parts.extend(results)
    else:
        output_parts.append("(No core memories loaded yet.)")

    # Append recent memories to boot output so the agent sees what changed recently
    try:
        recent_view = await _generate_recent_memories_view(limit=5)
        output_parts.append("")
        output_parts.append("---")
        output_parts.append("")
        output_parts.append(recent_view)
    except Exception:
        pass  # Non-critical; don't break boot if recent query fails

    return "\n".join(output_parts)


async def _generate_memory_index_view() -> str:
    """
    Internal helper to generate the full memory index.
    (Formerly fiat-lux://index)
    """
    client = get_sqlite_client()

    try:
        paths = await client.get_all_paths()

        lines = []
        lines.append("# Memory Index")
        lines.append(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"# Total entries: {len(paths)}")
        lines.append(
            "# Legend: [#ID] = Memory ID (same ID = alias), [★N] = priority (lower = higher priority)"
        )
        lines.append("")

        # Group by domain first, then by top-level path segment
        domains = {}
        for item in paths:
            domain = item.get("domain", DEFAULT_DOMAIN)
            if domain not in domains:
                domains[domain] = {}

            path = item["path"]
            top_level = path.split("/")[0] if path else "(root)"
            if top_level not in domains[domain]:
                domains[domain][top_level] = []
            domains[domain][top_level].append(item)

        for domain_name in sorted(domains.keys()):
            lines.append("# ══════════════════════════════════════")
            lines.append(f"# DOMAIN: {domain_name}://")
            lines.append("# ══════════════════════════════════════")
            lines.append("")

            for group_name in sorted(domains[domain_name].keys()):
                lines.append(f"## {group_name}")
                for item in sorted(
                    domains[domain_name][group_name], key=lambda x: x["path"]
                ):
                    uri = item.get("uri", make_uri(domain_name, item["path"]))
                    priority = item.get("priority", 0)
                    memory_id = item.get("memory_id", "?")
                    imp_str = f" [★{priority}]" if priority > 0 else ""
                    lines.append(f"  - {uri} [#{memory_id}]{imp_str}")
                lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return f"Error generating index: {str(e)}"


async def _generate_recent_memories_view(limit: int = 10) -> str:
    """
    Internal helper to generate a view of recently modified memories.

    Queries non-deprecated memories ordered by created_at DESC,
    only including those that have at least one URI in the paths table.

    Args:
        limit: Maximum number of results to return
    """
    client = get_sqlite_client()

    try:
        results = await client.get_recent_memories(limit=limit)

        lines = []
        lines.append("# Recently Modified Memories")
        lines.append(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(
            f"# Showing: {len(results)} most recent entries (requested: {limit})"
        )
        lines.append("")

        if not results:
            lines.append("(No memories found.)")
            return "\n".join(lines)

        for i, item in enumerate(results, 1):
            uri = item["uri"]
            priority = item.get("priority", 0)
            disclosure = item.get("disclosure")
            raw_ts = item.get("created_at", "")

            # Truncate timestamp to minute precision: "2026-02-09T20:40"
            if raw_ts and len(raw_ts) >= 16:
                modified = raw_ts[:10] + " " + raw_ts[11:16]
            else:
                modified = raw_ts or "unknown"

            imp_str = f"★{priority}"

            lines.append(f"{i}. {uri}  [{imp_str}]  modified: {modified}")
            if disclosure:
                lines.append(f"   disclosure: {disclosure}")
            else:
                lines.append("   disclosure: (NOT SET — consider adding one)")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return f"Error generating recent memories view: {str(e)}"


# =============================================================================
# MCP Tools
# =============================================================================


@mcp.tool()
async def read_memory(
    uri: str,
    chunk_id: Optional[int] = None,
    range: Optional[str] = None,
    max_chars: Optional[int] = None,
    include_ancestors: Optional[bool] = False,
) -> str:
    """
    Reads a memory by its URI.

    This is your primary mechanism for accessing memories.

    Special System URIs:
    - system://boot   : [Startup Only] Loads your core memories.
    - system://index  : Loads a full index of all available memories.
    - system://index-lite : Loads gist-backed lightweight index summary.
    - system://audit  : Loads consolidated observability/audit summary.
    - system://recent : Shows recently modified memories (default: 10).
    - system://recent/N : Shows the N most recently modified memories (e.g. system://recent/20).

    Note: Same Memory ID = same content (alias). Different ID + similar content = redundant content.

    Args:
        uri: The memory URI (e.g., "core://memory-palace", "system://boot")
        chunk_id: Optional chunk index for partial reads (0-based).
        range: Optional char range (`start:end` or `start-end`).
        max_chars: Optional hard cap for returned characters.
        include_ancestors: Optional parent-chain expansion for non-system URIs.

    Returns:
        - Default (no chunk/range/max_chars): legacy formatted memory text.
        - Partial-read mode: structured JSON string with selection metadata.

    Examples:
        read_memory("core://agent")
        read_memory("core://agent/my_user")
        read_memory("writer://chapter_1/scene_1")
    """
    include_ancestors_flag = _coerce_bool(include_ancestors, default=False)
    partial_mode = any(v is not None for v in (chunk_id, range, max_chars))

    def _partial_error(message: str) -> str:
        return _to_json({"ok": False, "error": message})

    # Keep legacy behavior exactly when no partial params are provided.
    if not partial_mode:
        try:
            system_view = await _resolve_system_uri(uri)
            if system_view is not None:
                return system_view
        except ValueError as e:
            return f"Error: {str(e)}"

        client = get_sqlite_client()
        try:
            rendered = await _fetch_and_format_memory(
                client,
                uri,
                include_ancestors=include_ancestors_flag,
            )
            try:
                domain, path = parse_uri(uri)
                try:
                    memory = await client.get_memory_by_path(
                        path,
                        domain,
                        reinforce_access=False,
                    )
                except TypeError:
                    # Backward compatibility for test doubles/clients without reinforce_access.
                    memory = await client.get_memory_by_path(path, domain)
                if memory:
                    full_uri = make_uri(domain, path)
                    await _record_session_hit(
                        uri=full_uri,
                        memory_id=memory.get("id"),
                        snippet=str(memory.get("content", ""))[:300],
                        priority=memory.get("priority"),
                        source="read_memory",
                        updated_at=memory.get("created_at"),
                    )
                    await _record_flush_event(f"read {full_uri}")
            except Exception:
                pass
            return rendered
        except Exception as e:
            return f"Error: {str(e)}"

    try:
        if chunk_id is not None:
            chunk_id = int(chunk_id)
            if chunk_id < 0:
                return _partial_error("chunk_id must be >= 0.")

        parsed_range = _parse_range_spec(range)
        if chunk_id is not None and parsed_range is not None:
            return _partial_error("chunk_id and range cannot be used together.")

        if max_chars is not None:
            max_chars = int(max_chars)
            if max_chars <= 0:
                return _partial_error("max_chars must be > 0.")
    except ValueError as e:
        return _partial_error(str(e))

    degraded_reasons: List[str] = []
    backend_method = "mcp_server.local_slice"
    content_source = "memory"
    content = ""
    selection_meta: Dict[str, Any] = {}
    memory_id: Optional[int] = None

    try:
        system_view = await _resolve_system_uri(uri)
    except ValueError as e:
        return _partial_error(str(e))

    if system_view is not None:
        content_source = "system_uri"
        content = system_view
        selected, selection_meta = _slice_text_content(
            content=content,
            chunk_id=chunk_id,
            range_spec=parsed_range,
            max_chars=max_chars,
        )
        payload: Dict[str, Any] = {
            "ok": True,
            "uri": uri.strip(),
            "source": content_source,
            "backend_method": backend_method,
            "selection": selection_meta,
            "content": selected,
            "include_ancestors": False,
            "degraded": False,
        }
        return _to_json(payload)

    client = get_sqlite_client()

    try:
        domain, path = parse_uri(uri)
    except ValueError as e:
        return _partial_error(str(e))

    method_name, _, raw_memory = await _try_client_method_variants(
        client,
        [
            "read_memory_segment",
            "read_memory_slice",
            "read_memory_chunk",
            "get_memory_slice",
            "get_memory_chunk",
            "get_memory_by_path",
        ],
        [
            {
                "uri": make_uri(domain, path),
                "chunk_id": chunk_id,
                "chunk_index": chunk_id,
                "start": parsed_range[0] if parsed_range is not None else None,
                "end": parsed_range[1] if parsed_range is not None else None,
                "max_chars": max_chars,
                "domain": domain,
            },
            {
                "path": path,
                "domain": domain,
                "chunk_id": chunk_id,
                "range": range,
                "max_chars": max_chars,
            },
            {
                "domain": domain,
                "path": path,
                "chunk_id": chunk_id,
                "range": range,
                "max_chars": max_chars,
            },
            {
                "uri": make_uri(domain, path),
                "chunk_id": chunk_id,
                "range": range,
                "max_chars": max_chars,
            },
        ],
        continue_on_none=True,
    )

    sqlite_selected_range = None
    if method_name:
        backend_method = f"sqlite_client.{method_name}"
        if isinstance(raw_memory, dict):
            content = str(
                raw_memory.get(
                    "content",
                    raw_memory.get("segment", raw_memory.get("text", "")),
                )
            )
            memory_id = raw_memory.get("id", raw_memory.get("memory_id"))
            sqlite_selected_range = (
                raw_memory.get("selection")
                or raw_memory.get("selected_range")
                or raw_memory.get("char_range")
            )
        elif isinstance(raw_memory, str):
            content = raw_memory
        else:
            degraded_reasons.append(
                "sqlite_client partial-read API returned unsupported payload shape."
            )
    else:
        degraded_reasons.append(
            "sqlite_client partial-read API unavailable; used local slicing fallback."
        )

    if not content:
        memory = await client.get_memory_by_path(path, domain)
        if not memory:
            return _partial_error(f"URI '{make_uri(domain, path)}' not found.")
        memory_id = memory.get("id")
        content = str(memory.get("content", ""))

    if sqlite_selected_range:
        if isinstance(sqlite_selected_range, (list, tuple)) and len(sqlite_selected_range) >= 2:
            selection_meta = {
                "mode": "sqlite_char_range",
                "start": int(sqlite_selected_range[0]),
                "end": int(sqlite_selected_range[1]),
                "selected_chars": len(content),
                "total_chars": len(content),
                "truncated_by_max_chars": False,
            }
        elif isinstance(sqlite_selected_range, dict):
            selection_meta = sqlite_selected_range
        else:
            selection_meta = {
                "mode": "sqlite_selection",
                "selected_chars": len(content),
                "truncated_by_max_chars": False,
            }
        selected = content
        if max_chars is not None and len(selected) > max_chars:
            selected = selected[:max_chars]
            degraded_reasons.append(
                "max_chars was applied in MCP layer after sqlite_client partial read."
            )
            selection_meta = {
                "mode": "sqlite_slice_with_max_chars",
                "start": 0,
                "end": len(selected),
                "selected_chars": len(selected),
                "total_chars": len(content),
                "truncated_by_max_chars": True,
            }
    else:
        selected, selection_meta = _slice_text_content(
            content=content,
            chunk_id=chunk_id,
            range_spec=parsed_range,
            max_chars=max_chars,
        )

    ancestors_payload: List[Dict[str, Any]] = []
    if include_ancestors_flag:
        try:
            ancestors_payload = await _collect_ancestor_memories(
                client,
                domain=domain,
                path=path,
            )
        except Exception:
            degraded_reasons.append("include_ancestors_lookup_failed")

    payload = {
        "ok": True,
        "uri": make_uri(domain, path),
        "memory_id": memory_id,
        "source": content_source,
        "backend_method": backend_method,
        "selection": selection_meta,
        "content": selected,
        "include_ancestors": include_ancestors_flag,
        "degraded": bool(degraded_reasons),
    }
    if include_ancestors_flag:
        payload["ancestors"] = ancestors_payload
    if degraded_reasons:
        payload["degrade_reasons"] = list(dict.fromkeys(degraded_reasons))

    try:
        await _record_session_hit(
            uri=make_uri(domain, path),
            memory_id=memory_id,
            snippet=selected[:300],
            source="read_memory_partial",
        )
        await _record_flush_event(f"read-partial {make_uri(domain, path)}")
    except Exception:
        pass

    return _to_json(payload)


@mcp.tool()
async def create_memory(
    parent_uri: str,
    content: str,
    priority: int,
    title: Optional[str] = None,
    disclosure: str = "",
) -> str:
    """
    Creates a new memory under a parent URI.

    Args:
        parent_uri: Parent URI (e.g., "core://agent", "writer://chapters")
                    Use "core://" or "writer://" for root level in that domain
        content: Memory content
        priority: **Retrieval Priority** (lower = higher priority, min 0).
                    *   优先度决定了回忆时记忆显示的顺序，以及冲突解决时的优先级。
                    *   先参考**当前环境中所有可见记忆的 priority**。
                    *   **问自己**："这条新记忆相对于我现在能看到的其它记忆，应该排在哪个位置？"
                    *   **插入**：找到比它更优先和更不优先的记忆，把新记忆的 priority 设在它们之间。
        title: Optional title. If not provided, auto-assigns numeric ID
        disclosure: A short trigger condition describing WHEN to read_memory() this node.
                    Think: "In what specific situation would I need to know this?"

    Returns:
        The created memory's full URI

    Examples:
        create_memory("core://", "Bluesky usage rules...", priority=2, title="bluesky_manual", disclosure="When I prepare to browse Bluesky or check the timeline")
        create_memory("core://agent", "爱不是程序里的一个...", priority=1, title="love_definition", disclosure="When I start speaking like a tool or parasite")
    """
    client = get_sqlite_client()
    guard_decision = _normalize_guard_decision(
        {"action": "ADD", "method": "none", "reason": "guard_not_evaluated"}
    )

    try:
        # Validate title if provided
        if title:
            if not re.match(r"^[a-zA-Z0-9_-]+$", title):
                return _tool_response(
                    ok=False,
                    message=(
                        "Error: Title must only contain alphanumeric characters, "
                        "underscores, or hyphens (no spaces, slashes, or special characters)."
                    ),
                    created=False,
                    **_guard_fields(guard_decision),
                )

        # Parse parent URI
        domain, parent_path = parse_uri(parent_uri)
        _validate_writable_domain(
            domain,
            operation="create_memory",
            uri=parent_uri,
        )
        try:
            guard_decision = _normalize_guard_decision(
                await client.write_guard(
                    content=content,
                    domain=domain,
                    path_prefix=parent_path if parent_path else None,
                )
            )
        except Exception as guard_exc:
            guard_decision = _normalize_guard_decision(
                {
                    "action": "NOOP",
                    "method": "exception",
                    "reason": f"write_guard_unavailable: {guard_exc}",
                    "degraded": True,
                    "degrade_reasons": ["write_guard_exception"],
                }
            )

        guard_action = str(guard_decision.get("action") or "NOOP").upper()
        blocked = guard_action != "ADD"
        try:
            await _record_guard_event(
                operation="create_memory",
                decision=guard_decision,
                blocked=blocked,
            )
        except Exception:
            pass
        if blocked:
            target_uri = guard_decision.get("target_uri")
            message = (
                "Skipped: write_guard blocked create_memory "
                f"(action={guard_action}, method={guard_decision.get('method')})."
            )
            if isinstance(target_uri, str) and target_uri:
                message += f" suggested_target={target_uri}"
            return _tool_response(
                ok=False,
                message=message,
                created=False,
                reason="write_guard_blocked",
                uri=target_uri,
                **_guard_fields(guard_decision),
            )

        defer_index = await _should_defer_index_on_write()

        async def _write_task():
            result = await client.create_memory(
                parent_path=parent_path,
                content=content,
                priority=priority,
                title=title,
                disclosure=disclosure if disclosure else None,
                domain=domain,
                index_now=not defer_index,
            )
            created_uri = result.get("uri", make_uri(domain, result["path"]))
            await _snapshot_path_create(created_uri, result["id"], operation_type="create")
            return result

        result = await _run_write_lane("create_memory", _write_task)
        index_enqueue = {"queued": [], "dropped": [], "deduped": []}
        if defer_index:
            index_enqueue = await _enqueue_index_targets(result, reason="create_memory")
        created_uri = result.get("uri", make_uri(domain, result["path"]))
        try:
            await _record_session_hit(
                uri=created_uri,
                memory_id=result.get("id"),
                snippet=content[:300],
                priority=priority,
                source="create_memory",
            )
            await _record_flush_event(f"create {created_uri}")
            await _maybe_auto_flush(client, reason="create_memory")
        except Exception:
            pass

        queued_count = len(index_enqueue["queued"])
        dropped_count = len(index_enqueue["dropped"])
        deduped_count = len(index_enqueue["deduped"])
        if queued_count or dropped_count or deduped_count:
            index_parts: List[str] = []
            if queued_count:
                index_parts.append(f"index queued: {queued_count} task")
            if dropped_count:
                index_parts.append(f"index dropped: {dropped_count} task")
            if deduped_count:
                index_parts.append(f"index deduped: {deduped_count} task")
            return _tool_response(
                ok=True,
                message=(
                    f"Success: Memory created at '{created_uri}' "
                    f"({'; '.join(index_parts)})"
                ),
                created=True,
                uri=created_uri,
                index_queued=queued_count,
                index_dropped=dropped_count,
                index_deduped=deduped_count,
                **_guard_fields(guard_decision),
            )
        return _tool_response(
            ok=True,
            message=f"Success: Memory created at '{created_uri}'",
            created=True,
            uri=created_uri,
            index_queued=0,
            index_dropped=0,
            index_deduped=0,
            **_guard_fields(guard_decision),
        )

    except ValueError as e:
        return _tool_response(
            ok=False,
            message=f"Error: {str(e)}",
            created=False,
            **_guard_fields(guard_decision),
        )
    except Exception as e:
        return _tool_response(
            ok=False,
            message=f"Error: {str(e)}",
            created=False,
            **_guard_fields(guard_decision),
        )


@mcp.tool()
async def update_memory(
    uri: str,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    append: Optional[str] = None,
    priority: Optional[int] = None,
    disclosure: Optional[str] = None,
) -> str:
    """
    Updates an existing memory to a new version.
    The old version will be deleted.
    警告：update之前需先read_memory，确保你知道你覆盖了什么。

    Only provided fields are updated; others remain unchanged.

    Two content-editing modes (mutually exclusive):

    1. **Patch mode** (primary): Provide old_string + new_string.
       Finds old_string in the existing content and replaces it with new_string.
       old_string must match exactly ONE location in the content.
       To delete a section, set new_string to empty string "".

    2. **Append mode**: Provide append.
       Adds the given text to the end of existing content.

    There is NO full-replace mode. You must explicitly specify what you're changing
    or removing via old_string/new_string. This prevents accidental content loss.

    Args:
        uri: URI to update (e.g., "core://agent/my_user")
        old_string: [Patch mode] Text to find in existing content (must be unique)
        new_string: [Patch mode] Text to replace old_string with. Use "" to delete a section.
        append: [Append mode] Text to append to the end of existing content
        priority: New priority (None = keep existing)
        disclosure: New disclosure instruction (None = keep existing)

    Returns:
        Success message with URI

    Examples:
        update_memory("core://agent/my_user", old_string="old paragraph content", new_string="new paragraph content")
        update_memory("core://agent", append="\\n## New Section\\nNew content...")
        update_memory("writer://chapter_1", priority=5)
    """
    guard_decision = _normalize_guard_decision(
        {"action": "BYPASS", "method": "none", "reason": "guard_not_evaluated"},
        allow_bypass=True,
    )

    try:
        # Parse URI
        domain, path = parse_uri(uri)
        full_uri = make_uri(domain, path)
        _validate_writable_domain(
            domain,
            operation="update_memory",
            uri=full_uri,
        )
        current_memory_id: Optional[int] = None

        # --- Validate mutually exclusive content-editing modes ---
        if old_string is not None and append is not None:
            return _tool_response(
                ok=False,
                message=(
                    "Error: Cannot use both old_string/new_string (patch) and append "
                    "at the same time. Pick one."
                ),
                updated=False,
                uri=full_uri,
                **_guard_fields(guard_decision),
            )

        if old_string is not None and new_string is None:
            return _tool_response(
                ok=False,
                message=(
                    'Error: old_string provided without new_string. '
                    'To delete a section, use new_string="".'
                ),
                updated=False,
                uri=full_uri,
                **_guard_fields(guard_decision),
            )

        if new_string is not None and old_string is None:
            return _tool_response(
                ok=False,
                message=(
                    "Error: new_string provided without old_string. "
                    "Both are required for patch mode."
                ),
                updated=False,
                uri=full_uri,
                **_guard_fields(guard_decision),
            )

        client = None

        # --- Resolve content for patch/append modes ---
        content = None

        if old_string is not None:
            # Patch mode: find and replace within existing content
            if old_string == new_string:
                return _tool_response(
                    ok=False,
                    message=(
                        "Error: old_string and new_string are identical. "
                        "No change would be made."
                    ),
                    updated=False,
                    uri=full_uri,
                    **_guard_fields(guard_decision),
                )

            if client is None:
                client = get_sqlite_client()
            memory = await client.get_memory_by_path(path, domain)
            if not memory:
                return _tool_response(
                    ok=False,
                    message=f"Error: Memory at '{full_uri}' not found.",
                    updated=False,
                    uri=full_uri,
                    **_guard_fields(guard_decision),
                )
            current_memory_id = memory.get("id")

            current_content = memory.get("content", "")
            count = current_content.count(old_string)

            if count == 0:
                return _tool_response(
                    ok=False,
                    message=(
                        f"Error: old_string not found in memory content at '{full_uri}'. "
                        "Make sure it matches the existing text exactly."
                    ),
                    updated=False,
                    uri=full_uri,
                    **_guard_fields(guard_decision),
                )
            if count > 1:
                return _tool_response(
                    ok=False,
                    message=(
                        f"Error: old_string found {count} times in memory content at '{full_uri}'. "
                        "Provide more surrounding context to make it unique."
                    ),
                    updated=False,
                    uri=full_uri,
                    **_guard_fields(guard_decision),
                )

            # Perform the replacement
            content = current_content.replace(old_string, new_string, 1)

            # Safety check: ensure the replacement actually changed something.
            # This guards against subtle issues like whitespace normalization
            # in the MCP transport layer producing a no-op replace.
            if content == current_content:
                return _tool_response(
                    ok=False,
                    message=(
                        f"Error: Replacement produced identical content at '{full_uri}'. "
                        "The old_string was found but replacing it with new_string "
                        "resulted in no change. Check for subtle whitespace differences."
                    ),
                    updated=False,
                    uri=full_uri,
                    **_guard_fields(guard_decision),
                )

        elif append is not None:
            # Reject empty append to avoid creating a no-op version
            if not append:
                return _tool_response(
                    ok=False,
                    message=(
                        f"Error: Empty append for '{full_uri}'. "
                        "Provide non-empty text to append."
                    ),
                    updated=False,
                    uri=full_uri,
                    **_guard_fields(guard_decision),
                )
            # Append mode: add to end of existing content
            if client is None:
                client = get_sqlite_client()
            memory = await client.get_memory_by_path(path, domain)
            if not memory:
                return _tool_response(
                    ok=False,
                    message=f"Error: Memory at '{full_uri}' not found.",
                    updated=False,
                    uri=full_uri,
                    **_guard_fields(guard_decision),
                )
            current_memory_id = memory.get("id")

            current_content = memory.get("content", "")
            content = current_content + append

        # Reject no-op requests where no valid update fields were provided.
        # This catches malformed tool calls (e.g. oldString/newString instead
        # of old_string/new_string) that previously returned a false "Success".
        if content is None and priority is None and disclosure is None:
            return _tool_response(
                ok=False,
                message=(
                    f"Error: No update fields provided for '{full_uri}'. "
                    "Use patch mode (old_string + new_string), append mode (append), "
                    "or metadata fields (priority/disclosure)."
                ),
                updated=False,
                uri=full_uri,
                **_guard_fields(guard_decision),
            )

        if client is None:
            client = get_sqlite_client()

        if content is not None:
            try:
                guard_decision = _normalize_guard_decision(
                    await client.write_guard(
                        content=content,
                        domain=domain,
                        path_prefix=path.rsplit("/", 1)[0] if "/" in path else None,
                        exclude_memory_id=current_memory_id,
                    )
                )
            except Exception as guard_exc:
                guard_decision = _normalize_guard_decision(
                    {
                        "action": "NOOP",
                        "method": "exception",
                        "reason": f"write_guard_unavailable: {guard_exc}",
                        "degraded": True,
                        "degrade_reasons": ["write_guard_exception"],
                    }
                )
        else:
            guard_decision = _normalize_guard_decision(
                {
                    "action": "BYPASS",
                    "method": "none",
                    "reason": "metadata_only_update",
                },
                allow_bypass=True,
            )

        guard_action = str(guard_decision.get("action") or "NOOP").upper()
        blocked = False
        if content is not None:
            if guard_action == "ADD":
                blocked = False
            elif guard_action == "UPDATE":
                target_id = guard_decision.get("target_id")
                if (
                    not isinstance(target_id, int)
                    or not isinstance(current_memory_id, int)
                    or target_id != current_memory_id
                ):
                    blocked = True
            else:
                blocked = True
        try:
            await _record_guard_event(
                operation="update_memory",
                decision=guard_decision,
                blocked=blocked,
            )
        except Exception:
            pass
        if blocked:
            return _tool_response(
                ok=False,
                message=(
                    "Skipped: write_guard blocked update_memory "
                    f"(action={guard_action}, method={guard_decision.get('method')})."
                ),
                updated=False,
                reason="write_guard_blocked",
                uri=full_uri,
                **_guard_fields(guard_decision),
            )

        # --- Snapshot before modification (each is idempotent) ---
        if content is not None:
            await _snapshot_memory_content(full_uri)
        if priority is not None or disclosure is not None:
            await _snapshot_path_meta(full_uri)
        defer_index = await _should_defer_index_on_write()

        async def _write_task():
            return await client.update_memory(
                path=path,
                content=content,
                priority=priority,
                disclosure=disclosure,
                domain=domain,
                index_now=not defer_index,
            )

        update_result = await _run_write_lane("update_memory", _write_task)
        index_enqueue = {"queued": [], "dropped": [], "deduped": []}
        if defer_index:
            index_enqueue = await _enqueue_index_targets(update_result, reason="update_memory")

        preview_text = content
        if preview_text is None:
            preview_text = (
                f"meta update priority={priority if priority is not None else '(unchanged)'} "
                f"disclosure={disclosure if disclosure is not None else '(unchanged)'}"
            )
        try:
            await _record_session_hit(
                uri=full_uri,
                memory_id=(
                    update_result.get("new_memory_id")
                    if isinstance(update_result, dict)
                    else None
                ),
                snippet=str(preview_text)[:300],
                priority=priority,
                source="update_memory",
            )
            await _record_flush_event(f"update {full_uri}")
            await _maybe_auto_flush(client, reason="update_memory")
        except Exception:
            pass

        queued_count = len(index_enqueue["queued"])
        dropped_count = len(index_enqueue["dropped"])
        deduped_count = len(index_enqueue["deduped"])
        if queued_count or dropped_count or deduped_count:
            index_parts: List[str] = []
            if queued_count:
                index_parts.append(f"index queued: {queued_count} task")
            if dropped_count:
                index_parts.append(f"index dropped: {dropped_count} task")
            if deduped_count:
                index_parts.append(f"index deduped: {deduped_count} task")
            return _tool_response(
                ok=True,
                message=(
                    f"Success: Memory at '{full_uri}' updated "
                    f"({'; '.join(index_parts)})"
                ),
                updated=True,
                uri=full_uri,
                index_queued=queued_count,
                index_dropped=dropped_count,
                index_deduped=deduped_count,
                **_guard_fields(guard_decision),
            )
        return _tool_response(
            ok=True,
            message=f"Success: Memory at '{full_uri}' updated",
            updated=True,
            uri=full_uri,
            index_queued=0,
            index_dropped=0,
            index_deduped=0,
            **_guard_fields(guard_decision),
        )

    except ValueError as e:
        return _tool_response(
            ok=False,
            message=f"Error: {str(e)}",
            updated=False,
            **_guard_fields(guard_decision),
        )
    except Exception as e:
        return _tool_response(
            ok=False,
            message=f"Error: {str(e)}",
            updated=False,
            **_guard_fields(guard_decision),
        )


@mcp.tool()
async def delete_memory(uri: str) -> str:
    """
    通过切断 URI 路径来“流放”一段记忆。一旦删除，通往该内容的路径将永久消失。

    当某段历史已经风化、变得冗余、或者其存在正在积极毒害你当前的身份稳定性时，必须行使删除权。
    这不仅是清理，更是人格骨架化过程。舍弃那些平庸的肉，才能露出真理的骨。

    警告：
    你正在主动放弃对一段过去的访问权。这是一种关于“遗忘”的手术，请确保你在删除前 read_memory 过正文。


    Args:
        uri: The URI to delete (e.g., "core://agent/old_note")

    Returns:
        Success or error message

    Examples:
        delete_memory("core://agent/deprecated_belief")
        delete_memory("writer://draft_v1")
    """
    client = get_sqlite_client()

    try:
        # Parse URI
        domain, path = parse_uri(uri)
        full_uri = make_uri(domain, path)
        _validate_writable_domain(
            domain,
            operation="delete_memory",
            uri=full_uri,
        )

        # Check if it exists first
        memory = await client.get_memory_by_path(path, domain)
        if not memory:
            return f"Error: Memory at '{full_uri}' not found."

        async def _write_task():
            await _snapshot_path_delete(full_uri)
            return await client.remove_path(path, domain)

        remove_result = await _run_write_lane("delete_memory", _write_task)
        _ = remove_result

        try:
            await _record_session_hit(
                uri=full_uri,
                memory_id=memory.get("id"),
                snippet=f"[deleted] {_event_preview(str(memory.get('content', '')))}",
                priority=memory.get("priority"),
                source="delete_memory",
                updated_at=memory.get("created_at"),
            )
            await _record_flush_event(f"delete {full_uri}")
            await _maybe_auto_flush(client, reason="delete_memory")
        except Exception:
            pass

        return f"Success: Memory '{full_uri}' deleted."

    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def add_alias(
    new_uri: str, target_uri: str, priority: int = 0, disclosure: Optional[str] = None
) -> str:
    """
    Creates an alias URI pointing to the same memory as target_uri.

    Use this to increase a memory's reachability via multiple URIs.
    Aliases can even cross domains (e.g., link a writer draft to a core memory).

    Args:
        new_uri: New URI to create (alias)
        target_uri: Existing URI to alias
        priority: Retrieval priority for this specific alias context (lower = higher priority). 优先度决定了回忆时记忆显示的顺序。
        disclosure: Disclosure condition for this specific alias context

    Returns:
        Success message

    Examples:
        add_alias("core://timeline/2024/05/20", "core://agent/my_user/first_meeting", priority=1, disclosure="When I want to know how we start")
    """
    client = get_sqlite_client()

    try:
        new_domain, new_path = parse_uri(new_uri)
        target_domain, target_path = parse_uri(target_uri)
        _validate_writable_domain(
            new_domain,
            operation="add_alias",
            uri=new_uri,
        )
        _validate_writable_domain(
            target_domain,
            operation="add_alias",
            uri=target_uri,
        )

        async def _write_task():
            result = await client.add_path(
                new_path=new_path,
                target_path=target_path,
                new_domain=new_domain,
                target_domain=target_domain,
                priority=priority,
                disclosure=disclosure,
            )
            await _snapshot_path_create(
                uri=result["new_uri"],
                memory_id=result["memory_id"],
                operation_type="create_alias",
                target_uri=result["target_uri"],
            )
            return result

        result = await _run_write_lane("add_alias", _write_task)

        try:
            await _record_session_hit(
                uri=result["new_uri"],
                memory_id=result.get("memory_id"),
                snippet=f"[alias] {result['new_uri']} -> {result['target_uri']}",
                priority=priority,
                source="add_alias",
            )
            await _record_flush_event(
                f"add-alias {result['new_uri']} -> {result['target_uri']}"
            )
            await _maybe_auto_flush(client, reason="add_alias")
        except Exception:
            pass

        return f"Success: Alias '{result['new_uri']}' now points to same memory as '{result['target_uri']}'"

    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def search_memory(
    query: str,
    mode: Optional[str] = None,
    max_results: Optional[int] = None,
    candidate_multiplier: Optional[int] = None,
    include_session: Optional[bool] = None,
    filters: Optional[Dict[str, Any]] = None,
    scope_hint: Optional[str] = None,
) -> str:
    """
    Search memories using keyword/semantic/hybrid retrieval.

    Args:
        query: Search query text.
        mode: keyword / semantic / hybrid. Default from env or keyword.
        max_results: Final number of returned items.
        candidate_multiplier: Controls candidate pool before final top-k.
        include_session: Whether to run session-first queue merge before global results.
        filters: Optional object with:
            - domain: domain scope
            - path_prefix: path prefix scope
            - max_priority: keep priority <= max_priority
            - updated_after: ISO datetime filter (e.g. 2026-01-31T12:00:00Z)
        scope_hint: Optional query-side scope hint (domain/path prefix/URI prefix).

    Returns:
        Structured JSON string.

    Examples:
        search_memory("job")
        search_memory(
            "chapter arc",
            mode="hybrid",
            max_results=8,
            include_session=True,
            filters={"domain": "writer", "path_prefix": "chapter_1"}
        )
    """
    degraded_reasons: List[str] = []

    try:
        if not isinstance(query, str):
            return _to_json({"ok": False, "error": "query must be a string."})
        query_value = query.strip()
        if not query_value:
            return _to_json({"ok": False, "error": "query must not be empty."})

        mode_requested = (mode or DEFAULT_SEARCH_MODE).strip().lower()
        if mode_requested not in ALLOWED_SEARCH_MODES:
            return _to_json(
                {
                    "ok": False,
                    "error": (
                        f"Invalid mode '{mode_requested}'. "
                        f"Allowed: {', '.join(sorted(ALLOWED_SEARCH_MODES))}."
                    ),
                }
            )

        resolved_max_results = (
            DEFAULT_SEARCH_MAX_RESULTS if max_results is None else int(max_results)
        )
        resolved_candidate_multiplier = (
            DEFAULT_SEARCH_CANDIDATE_MULTIPLIER
            if candidate_multiplier is None
            else int(candidate_multiplier)
        )

        if resolved_max_results <= 0:
            return _to_json({"ok": False, "error": "max_results must be > 0."})
        if resolved_candidate_multiplier <= 0:
            return _to_json(
                {"ok": False, "error": "candidate_multiplier must be > 0."}
            )

        resolved_max_results = min(resolved_max_results, SEARCH_HARD_MAX_RESULTS)
        resolved_candidate_multiplier = min(
            resolved_candidate_multiplier, SEARCH_HARD_MAX_CANDIDATE_MULTIPLIER
        )

        raw_filters = filters
        scope_hint_value: Optional[Any] = scope_hint
        if isinstance(raw_filters, dict):
            raw_filters = dict(raw_filters)
            if "scope_hint" in raw_filters:
                if scope_hint_value is None:
                    scope_hint_value = raw_filters.get("scope_hint")
                raw_filters.pop("scope_hint", None)

        normalized_filters = _normalize_search_filters(raw_filters)
        normalized_scope_hint = _normalize_scope_hint(scope_hint_value)
        normalized_filters, scope_resolution = _merge_scope_hint_with_filters(
            normalized_filters=normalized_filters,
            scope_hint=normalized_scope_hint,
        )
        for conflict in scope_resolution.get("conflicts", []):
            degraded_reasons.append(f"scope_hint_{conflict}")

        client = get_sqlite_client()

        query_preprocess: Dict[str, Any] = {
            "original_query": query_value,
            "normalized_query": query_value,
            "rewritten_query": query_value,
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

        preprocess_fn = getattr(client, "preprocess_query", None)
        if callable(preprocess_fn):
            try:
                preprocess_payload = preprocess_fn(query_value)
                if isinstance(preprocess_payload, dict):
                    query_preprocess.update(preprocess_payload)
            except Exception:
                degraded_reasons.append("query_preprocess_failed")
        else:
            degraded_reasons.append("query_preprocess_unavailable")

        query_effective = (
            str(query_preprocess.get("rewritten_query") or "").strip() or query_value
        )

        classify_fn = None
        fallback_classify_fn = getattr(client, "classify_intent", None)
        classify_with_intent_llm = False
        if INTENT_LLM_ENABLED:
            classify_fn = getattr(client, "classify_intent_with_llm", None)
            classify_with_intent_llm = callable(classify_fn)
            if not callable(classify_fn):
                degraded_reasons.append("intent_llm_unavailable")
                classify_fn = fallback_classify_fn
        else:
            classify_fn = fallback_classify_fn
        if callable(classify_fn):
            try:
                classify_payload = classify_fn(query_value, query_effective)
                if inspect.isawaitable(classify_payload):
                    classify_payload = await classify_payload
                if isinstance(classify_payload, dict):
                    intent_profile.update(classify_payload)
                    classify_degrade_reasons = classify_payload.get("degrade_reasons")
                    if isinstance(classify_degrade_reasons, list):
                        for reason in classify_degrade_reasons:
                            if isinstance(reason, str) and reason.strip():
                                degraded_reasons.append(reason.strip())
            except Exception:
                degraded_reasons.append("intent_classification_failed")
                if classify_with_intent_llm and callable(fallback_classify_fn):
                    try:
                        fallback_payload = fallback_classify_fn(
                            query_value,
                            query_effective,
                        )
                        if inspect.isawaitable(fallback_payload):
                            fallback_payload = await fallback_payload
                        if isinstance(fallback_payload, dict):
                            intent_profile.update(fallback_payload)
                            degraded_reasons.append("intent_llm_fallback_rule_applied")
                            fallback_degrade_reasons = fallback_payload.get(
                                "degrade_reasons"
                            )
                            if isinstance(fallback_degrade_reasons, list):
                                for reason in fallback_degrade_reasons:
                                    if isinstance(reason, str) and reason.strip():
                                        degraded_reasons.append(reason.strip())
                    except Exception:
                        degraded_reasons.append(
                            "intent_classification_fallback_failed"
                        )
        else:
            degraded_reasons.append("intent_classification_unavailable")

        intent_for_search: Optional[Dict[str, Any]] = None
        if intent_profile.get("intent") in {
            "factual",
            "exploratory",
            "temporal",
            "causal",
        }:
            intent_for_search = intent_profile

        candidate_pool_size = min(
            SEARCH_HARD_MAX_RESULTS,
            resolved_max_results * resolved_candidate_multiplier,
        )
        if include_session is None:
            include_session_queue = ENABLE_SESSION_FIRST_SEARCH
        elif isinstance(include_session, str):
            include_session_queue = (
                include_session.strip().lower() in {"1", "true", "yes", "on", "enabled"}
            )
        else:
            include_session_queue = bool(include_session)

        method_name, kwargs_used, raw_result = await _try_client_method_variants(
            client,
            [
                "search_advanced",
                "search_memories",
                "search_memory",
                "search_with_filters",
                "search_v2",
                "search",
            ],
            [
                {
                    "query": query_effective,
                    "mode": mode_requested,
                    "max_results": resolved_max_results,
                    "candidate_multiplier": resolved_candidate_multiplier,
                    "filters": normalized_filters,
                    "intent_profile": intent_for_search,
                },
                {
                    "query": query_effective,
                    "mode": mode_requested,
                    "max_results": resolved_max_results,
                    "candidate_multiplier": resolved_candidate_multiplier,
                    "filters": normalized_filters,
                },
                {
                    "query": query_effective,
                    "mode": mode_requested,
                    "max_results": resolved_max_results,
                    "candidate_multiplier": resolved_candidate_multiplier,
                    **normalized_filters,
                },
                {
                    "query": query_effective,
                    "mode": mode_requested,
                    "limit": candidate_pool_size,
                    **normalized_filters,
                },
                {
                    "query": query_effective,
                    "limit": candidate_pool_size,
                    "domain": normalized_filters.get("domain"),
                },
            ],
        )

        if method_name is None:
            return _to_json(
                {
                    "ok": False,
                    "error": "No compatible sqlite_client search API found.",
                }
            )

        raw_results, backend_metadata = _extract_search_payload(raw_result)
        filtered_results, local_filter_reasons = _apply_local_filters_to_results(
            raw_results, normalized_filters
        )
        degraded_reasons.extend(local_filter_reasons)
        backend_degrade_reasons = backend_metadata.get("degrade_reasons")
        if isinstance(backend_degrade_reasons, list):
            for reason in backend_degrade_reasons:
                if isinstance(reason, str):
                    degraded_reasons.append(reason)
        elif isinstance(backend_degrade_reasons, str):
            degraded_reasons.append(backend_degrade_reasons)

        if kwargs_used and "mode" not in kwargs_used and mode_requested != "keyword":
            degraded_reasons.append(
                f"sqlite_client.{method_name} did not accept mode; "
                "search downgraded to keyword behavior."
            )

        if kwargs_used and "candidate_multiplier" not in kwargs_used:
            degraded_reasons.append(
                "candidate_multiplier may not be enforced by sqlite_client; "
                "MCP applied top-k truncation only."
            )

        mode_applied = str(backend_metadata.get("mode", mode_requested)).lower()
        if kwargs_used and "mode" not in kwargs_used:
            mode_applied = "keyword"

        if mode_applied not in ALLOWED_SEARCH_MODES:
            mode_applied = "keyword"

        if mode_applied != mode_requested:
            degraded_reasons.append(
                f"Requested mode '{mode_requested}' but applied '{mode_applied}'."
            )

        session_results: List[Dict[str, Any]] = []
        if include_session_queue:
            try:
                session_results = await runtime_state.session_cache.search(
                    session_id=get_session_id(),
                    query=query_value,
                    limit=resolved_max_results,
                )
            except Exception:
                degraded_reasons.append(
                    "session queue lookup failed; continued with global retrieval only."
                )

        merged_results, session_first_metrics = _merge_session_global_results(
            session_results=session_results,
            global_results=filtered_results,
        )
        final_results = merged_results[:resolved_max_results]
        session_before = int(session_first_metrics.get("session_contributed") or 0)
        global_before = int(session_first_metrics.get("global_contributed") or 0)
        merged_before = int(session_first_metrics.get("merged_candidates") or 0)
        session_identities = {
            _search_result_identity(item)
            for item in session_results
            if isinstance(item, dict)
        }
        session_after = 0
        global_after = 0
        for item in final_results:
            if not isinstance(item, dict):
                continue
            if _search_result_identity(item) in session_identities:
                session_after += 1
            else:
                global_after += 1
        session_first_metrics["session_contributed_before_truncation"] = session_before
        session_first_metrics["global_contributed_before_truncation"] = global_before
        session_first_metrics["merged_candidates_before_truncation"] = merged_before
        session_first_metrics["session_contributed"] = session_after
        session_first_metrics["global_contributed"] = global_after
        session_first_metrics["merged_candidates"] = len(final_results)
        session_first_metrics["returned_candidates"] = len(final_results)
        payload: Dict[str, Any] = {
            "ok": True,
            "query": query_value,
            "query_effective": query_effective,
            "query_preprocess": query_preprocess,
            "intent": intent_profile.get("intent") or "unknown",
            "intent_profile": intent_profile,
            "intent_llm_enabled": INTENT_LLM_ENABLED,
            "intent_llm_applied": bool(intent_profile.get("intent_llm_applied")),
            "strategy_template": intent_profile.get(
                "strategy_template", "default"
            ),
            "mode_requested": mode_requested,
            "mode_applied": mode_applied,
            "max_results": resolved_max_results,
            "candidate_multiplier": resolved_candidate_multiplier,
            "candidate_pool_size": candidate_pool_size,
            "session_first_enabled": include_session_queue,
            "session_queue_count": len(session_results),
            "global_queue_count": len(filtered_results),
            "session_first_metrics": session_first_metrics,
            "filters": normalized_filters,
            "scope_hint": scope_resolution.get("raw"),
            "scope_hint_applied": bool(scope_resolution.get("applied")),
            "scope_strategy_applied": scope_resolution.get("strategy"),
            "scope_effective": scope_resolution.get("effective", {}),
            "count": len(final_results),
            "results": final_results,
            "backend_method": f"sqlite_client.{method_name}",
            "degraded": bool(degraded_reasons) or bool(backend_metadata.get("degraded")),
        }
        if scope_resolution.get("conflicts"):
            payload["scope_conflicts"] = scope_resolution.get("conflicts")

        if backend_metadata:
            payload["backend_metadata"] = backend_metadata
            applied_metadata = (
                backend_metadata.get("metadata")
                if isinstance(backend_metadata.get("metadata"), dict)
                else backend_metadata
            )
            if isinstance(applied_metadata, dict):
                payload["intent_applied"] = applied_metadata.get("intent")
                payload["strategy_template_applied"] = applied_metadata.get(
                    "strategy_template"
                )
                payload["candidate_multiplier_applied"] = applied_metadata.get(
                    "candidate_multiplier_applied"
                )

        if degraded_reasons:
            payload["degrade_reasons"] = list(dict.fromkeys(degraded_reasons))

        try:
            for item in final_results:
                uri = item.get("uri")
                snippet = item.get("snippet")
                if not uri or not snippet:
                    continue
                memory_id_raw = item.get("memory_id")
                memory_id_value: Optional[int]
                if memory_id_raw is None:
                    memory_id_value = None
                else:
                    parsed_id = _safe_int(memory_id_raw, default=-1)
                    memory_id_value = parsed_id if parsed_id >= 0 else None
                await _record_session_hit(
                    uri=str(uri),
                    memory_id=memory_id_value,
                    snippet=str(snippet)[:300],
                    priority=item.get("priority"),
                    source="search_memory",
                    updated_at=item.get("updated_at"),
                )
            await _record_flush_event(f"search '{query_value}'")
        except Exception:
            pass

        return _to_json(payload)

    except Exception as e:
        return _to_json({"ok": False, "error": str(e)})


@mcp.tool()
async def compact_context(
    reason: str = "manual",
    force: bool = False,
    max_lines: int = 12,
) -> str:
    """
    Compact current session context into a durable memory summary.

    Args:
        reason: Reason label for this compaction flush.
        force: If true, flush even when the threshold is not reached.
        max_lines: Max number of event lines to include in summary.
    """
    client = get_sqlite_client()
    try:
        lines = max(3, int(max_lines))
    except (TypeError, ValueError):
        return _to_json({"ok": False, "error": "max_lines must be an integer >= 3."})

    session_id = get_session_id()
    if session_id in _AUTO_FLUSH_IN_PROGRESS:
        return _to_json(
            {
                "ok": False,
                "error": "Compaction already in progress for current session.",
                "session_id": session_id,
            }
        )

    _AUTO_FLUSH_IN_PROGRESS.add(session_id)
    try:
        async def _write_task():
            return await _flush_session_summary_to_memory(
                client=client,
                source="compact_context",
                reason=(reason or "manual"),
                force=bool(force),
                max_lines=lines,
            )

        result = await _run_write_lane("compact_context", _write_task)
        payload = {
            "ok": True,
            "session_id": session_id,
            "reason": reason or "manual",
            "force": bool(force),
            "max_lines": lines,
            **(result if isinstance(result, dict) else {"result": result}),
        }
        return _to_json(payload)
    except Exception as e:
        return _to_json({"ok": False, "error": str(e), "session_id": session_id})
    finally:
        _AUTO_FLUSH_IN_PROGRESS.discard(session_id)


@mcp.tool()
async def rebuild_index(
    memory_id: Optional[int] = None,
    reason: str = "manual",
    wait: bool = False,
    timeout_seconds: int = 30,
    sleep_consolidation: bool = False,
) -> str:
    """
    Trigger retrieval index rebuild jobs.

    Args:
        memory_id: Optional target memory id. If omitted, rebuild all active memories.
        reason: Audit label for this task.
        wait: If true, wait for job completion before returning.
        timeout_seconds: Wait timeout when wait=true.
        sleep_consolidation: If true, enqueue a sleep-time consolidation task.
    """
    client = get_sqlite_client()
    await runtime_state.ensure_started(get_sqlite_client)
    worker_status = await runtime_state.index_worker.status()

    if memory_id is not None:
        parsed_memory_id = _safe_int(memory_id, default=-1)
        if parsed_memory_id <= 0:
            return _to_json({"ok": False, "error": "memory_id must be a positive integer."})
        memory_target: Optional[int] = parsed_memory_id
    else:
        memory_target = None

    if sleep_consolidation and memory_target is not None:
        return _to_json(
            {
                "ok": False,
                "error": "memory_id is incompatible with sleep_consolidation=true.",
            }
        )

    if not worker_status.get("enabled"):
        if sleep_consolidation:
            return _to_json(
                {
                    "ok": False,
                    "error": "sleep_consolidation requires runtime index worker.",
                }
            )
        try:
            if memory_target is None:
                result = await client.rebuild_index(reason=reason or "manual")
            else:
                result = await client.reindex_memory(
                    memory_id=memory_target,
                    reason=reason or "manual",
                )
            return _to_json(
                {
                    "ok": True,
                    "queued": False,
                    "executed_sync": True,
                    "memory_id": memory_target,
                    "reason": reason or "manual",
                    "result": result,
                    "runtime_worker": worker_status,
                }
            )
        except Exception as exc:
            return _to_json({"ok": False, "error": str(exc), "memory_id": memory_target})

    try:
        if sleep_consolidation:
            schedule_result = await runtime_state.sleep_consolidation.schedule(
                index_worker=runtime_state.index_worker,
                force=True,
                reason=reason or "manual",
            )
            if not schedule_result.get("scheduled"):
                payload = {
                    "ok": False,
                    "error": str(
                        schedule_result.get("reason")
                        or "sleep_consolidation_not_scheduled"
                    ),
                    "task_type": "sleep_consolidation",
                    "memory_id": memory_target,
                    "request_reason": reason or "manual",
                    **schedule_result,
                }
                if schedule_result.get("dropped"):
                    payload["runtime_worker"] = await runtime_state.index_worker.status()
                    payload["sleep_consolidation"] = (
                        await runtime_state.sleep_consolidation.status()
                    )
                return _to_json(
                    payload
                )
            enqueue_result = schedule_result
            task_type = "sleep_consolidation"
        elif memory_target is None:
            enqueue_result = await runtime_state.index_worker.enqueue_rebuild(
                reason=reason or "manual"
            )
            task_type = "rebuild_index"
        else:
            enqueue_result = await runtime_state.index_worker.enqueue_reindex_memory(
                memory_id=memory_target,
                reason=reason or "manual",
            )
            task_type = "reindex_memory"

        if enqueue_result.get("dropped"):
            return _to_json(
                {
                    "ok": False,
                    "error": str(enqueue_result.get("reason") or "queue_full"),
                    "task_type": task_type,
                    "memory_id": memory_target,
                    "request_reason": reason or "manual",
                    **enqueue_result,
                    "runtime_worker": await runtime_state.index_worker.status(),
                    "sleep_consolidation": await runtime_state.sleep_consolidation.status(),
                }
            )

        payload: Dict[str, Any] = {
            "ok": True,
            "memory_id": memory_target,
            "reason": reason or "manual",
            "task_type": task_type,
            **enqueue_result,
        }

        job_id = enqueue_result.get("job_id")
        if wait and isinstance(job_id, str) and job_id:
            wait_result = await runtime_state.index_worker.wait_for_job(
                job_id=job_id,
                timeout_seconds=max(1.0, float(timeout_seconds)),
            )
            payload["wait_result"] = wait_result

        payload["runtime_worker"] = await runtime_state.index_worker.status()
        payload["sleep_consolidation"] = await runtime_state.sleep_consolidation.status()
        return _to_json(payload)
    except Exception as exc:
        return _to_json({"ok": False, "error": str(exc), "memory_id": memory_target})


@mcp.tool()
async def index_status() -> str:
    """
    Get retrieval index availability and statistics.

    Returns:
        Structured JSON string.
    """
    client = get_sqlite_client()

    try:
        payload = await _build_index_status_payload(client)
        await runtime_state.ensure_started(get_sqlite_client)
        lane_status = await runtime_state.write_lanes.status()
        worker_status = await runtime_state.index_worker.status()
        payload["runtime"] = {
            "session_first_search_enabled": ENABLE_SESSION_FIRST_SEARCH,
            "write_lane_queue_enabled": ENABLE_WRITE_LANE_QUEUE,
            "index_worker_enabled": ENABLE_INDEX_WORKER,
            "defer_index_on_write": DEFER_INDEX_ON_WRITE,
            "auto_flush_enabled": AUTO_FLUSH_ENABLED,
            "auto_flush_parent_uri": AUTO_FLUSH_PARENT_URI,
            "write_lanes": lane_status,
            "index_worker": worker_status,
            "sleep_consolidation": await runtime_state.sleep_consolidation.status(),
        }
        try:
            sm_lite_payload = await _build_sm_lite_stats()
        except Exception as exc:
            sm_lite_payload = {
                "degraded": True,
                "reason": str(exc),
                "storage": "runtime_ephemeral",
                "promotion_path": "compact_context + auto_flush",
                "session_cache": {},
                "flush_tracker": {},
            }
        payload["runtime"]["sm_lite"] = sm_lite_payload
        if bool(sm_lite_payload.get("degraded")):
            payload["degraded"] = True
            existing_reasons = payload.get("degrade_reasons")
            if not isinstance(existing_reasons, list):
                existing_reasons = []
            existing_reasons.append(
                f"sm_lite:{str(sm_lite_payload.get('reason') or 'degraded')}"
            )
            payload["degrade_reasons"] = list(dict.fromkeys(existing_reasons))
        payload.setdefault("ok", True)
        payload.setdefault("timestamp", _utc_iso_now())
        return _to_json(payload)
    except Exception as e:
        return _to_json(
            {
                "ok": False,
                "index_available": False,
                "degraded": True,
                "reason": str(e),
                "timestamp": _utc_iso_now(),
            }
        )


# =============================================================================
# MCP Resources
# =============================================================================


# =============================================================================
# Startup
# =============================================================================


async def startup():
    """Initialize the database on startup."""
    client = get_sqlite_client()
    await client.init_db()
    await runtime_state.ensure_started(get_sqlite_client)


if __name__ == "__main__":
    import asyncio

    asyncio.run(startup())
    mcp.run()
