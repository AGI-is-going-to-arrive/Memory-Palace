"""
Browse API - Clean URI-based memory navigation

This replaces the old Entity/Relation/Chapter conceptual split with a simple
hierarchical browser. Every path is just a node with content and children.
"""

import os
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Any
from db import get_sqlite_client
from db.sqlite_client import Path as PathModel
from runtime_state import runtime_state
from .maintenance import require_maintenance_api_key
from sqlalchemy import select

router = APIRouter(prefix="/browse", tags=["browse"])
_READ_ONLY_DOMAINS = {"system"}
_VALID_DOMAINS = list(
    dict.fromkeys(
        [
            d.strip().lower()
            for d in str(os.getenv("VALID_DOMAINS", "core,writer,game,notes,system")).split(",")
            if d.strip()
        ]
        + sorted(_READ_ONLY_DOMAINS)
    )
)


class NodeUpdate(BaseModel):
    content: str | None = None
    priority: int | None = None
    disclosure: str | None = None


class NodeCreate(BaseModel):
    parent_path: str = ""
    title: str | None = None
    content: str
    priority: int = 0
    disclosure: str | None = None
    domain: str = "core"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


ENABLE_WRITE_LANE_QUEUE = _env_bool("RUNTIME_WRITE_LANE_QUEUE", True)


def _normalize_domain_or_422(domain: str) -> str:
    normalized = str(domain or "").strip().lower()
    if not normalized:
        normalized = "core"
    if normalized not in _VALID_DOMAINS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown domain '{normalized}'. Valid domains: {', '.join(_VALID_DOMAINS)}",
        )
    return normalized


def _ensure_writable_domain_or_422(domain: str, *, operation: str) -> str:
    normalized = _normalize_domain_or_422(domain)
    if normalized in _READ_ONLY_DOMAINS:
        raise HTTPException(
            status_code=422,
            detail=f"{operation} does not allow writes to '{normalized}://'. system:// is read-only.",
        )
    return normalized


def _normalize_guard_decision(payload: Any, *, allow_bypass: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    reason = str(payload.get("reason") or "").strip()
    has_action = "action" in payload
    raw_action = str(payload.get("action") or "").strip().upper() if has_action else ""
    action = raw_action
    valid_actions = {"ADD", "UPDATE", "NOOP", "DELETE"}
    if allow_bypass:
        valid_actions.add("BYPASS")
    if action not in valid_actions:
        action = "NOOP"
        marker_value = raw_action or ("EMPTY" if has_action else "MISSING")
        marker = f"invalid_guard_action:{marker_value}"
        reason = marker if not reason else f"{marker}; {reason}"
    method = str(payload.get("method") or "none").strip().lower() or "none"
    target_id = payload.get("target_id")
    if not isinstance(target_id, int) or target_id <= 0:
        target_id = None
    target_uri = payload.get("target_uri")
    if not isinstance(target_uri, str) or not target_uri.strip():
        target_uri = None
    return {
        "action": action,
        "reason": reason,
        "method": method,
        "target_id": target_id,
        "target_uri": target_uri,
    }


def _guard_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "guard_action": payload.get("action"),
        "guard_reason": payload.get("reason"),
        "guard_method": payload.get("method"),
        "guard_target_id": payload.get("target_id"),
        "guard_target_uri": payload.get("target_uri"),
    }


async def _record_guard_event(operation: str, decision: dict[str, Any], blocked: bool) -> None:
    try:
        await runtime_state.guard_tracker.record_event(
            operation=operation,
            action=str(decision.get("action") or "UNKNOWN"),
            method=str(decision.get("method") or "unknown"),
            reason=str(decision.get("reason") or ""),
            target_id=decision.get("target_id"),
            blocked=blocked,
        )
    except Exception:
        # Observability should not block write paths.
        return


async def _run_write_lane(operation: str, task):
    if not ENABLE_WRITE_LANE_QUEUE:
        return await task()
    return await runtime_state.write_lanes.run_write(
        session_id=None,
        operation=operation,
        task=task,
    )


@router.get("/node")
async def get_node(
    path: str = Query("", description="URI path like 'memory-palace' or 'memory-palace/salem'"),
    domain: str = Query("core"),
    _auth: None = Depends(require_maintenance_api_key),
):
    """
    Get a node's content and its direct children.
    
    This is the only read endpoint you need - it gives you:
    - The current node's full content (or virtual root)
    - Preview of all children (next level)
    - Breadcrumb trail for navigation
    """
    client = get_sqlite_client()
    domain = _normalize_domain_or_422(domain)
    
    if not path:
        # Virtual Root Node
        memory = {
            "content": "",
            "priority": 0,
            "disclosure": None,
            "created_at": None
        }
        # Get roots as children (no memory_id = virtual root)
        children_raw = await client.get_children(None, domain=domain)
        breadcrumbs = [{"path": "", "label": "root"}]
    else:
        # Get the node itself
        memory = await client.get_memory_by_path(
            path, domain=domain, reinforce_access=False
        )
        
        if not memory:
            raise HTTPException(status_code=404, detail=f"Path not found: {domain}://{path}")
        
        # Get children across all aliases of this memory
        children_raw = await client.get_children(memory["id"])
        
        # Build breadcrumbs
        segments = path.split("/")
        breadcrumbs = [{"path": "", "label": "root"}]
        accumulated = ""
        for seg in segments:
            accumulated = f"{accumulated}/{seg}" if accumulated else seg
            breadcrumbs.append({"path": accumulated, "label": seg})
    
    children = [
        {
            "domain": c["domain"],
            "path": c["path"],
            "uri": f"{c['domain']}://{c['path']}",
            "name": c["path"].split("/")[-1],  # Last segment
            "priority": c["priority"],
            "disclosure": c.get("disclosure"),
            "content_snippet": c["content_snippet"],
            "gist_text": c.get("gist_text"),
            "gist_method": c.get("gist_method"),
            "gist_quality": c.get("gist_quality"),
            "source_hash": c.get("gist_source_hash"),
        }
        for c in children_raw
    ]
    children.sort(key=lambda x: (x["priority"] if x["priority"] is not None else 999, x["path"]))
    
    # Get all aliases (other paths pointing to the same memory)
    aliases = []
    if path and memory.get("id"):
        async with client.session() as session:
            result = await session.execute(
                select(PathModel.domain, PathModel.path)
                .where(PathModel.memory_id == memory["id"])
            )
            aliases = [
                f"{row[0]}://{row[1]}"
                for row in result.all()
                if not (row[0] == domain and row[1] == path)  # exclude current
            ]
    
    return {
        "node": {
            "path": path,
            "domain": domain,
            "uri": f"{domain}://{path}",
            "name": path.split("/")[-1] if path else "root",
            "content": memory["content"],
            "priority": memory["priority"],
            "disclosure": memory["disclosure"],
            "created_at": memory["created_at"],
            "aliases": aliases,
            "gist_text": memory.get("gist_text"),
            "gist_method": memory.get("gist_method"),
            "gist_quality": memory.get("gist_quality"),
            "source_hash": memory.get("gist_source_hash"),
        },
        "children": children,
        "breadcrumbs": breadcrumbs
    }


@router.post("/node")
async def create_node(
    body: NodeCreate,
    _auth: None = Depends(require_maintenance_api_key),
):
    """
    Create a new node under a parent path.
    """
    client = get_sqlite_client()
    parent_path = body.parent_path.strip().strip("/")
    domain = _ensure_writable_domain_or_422(body.domain, operation="create_node")
    title = (body.title or "").strip() or None
    try:
        guard_decision = _normalize_guard_decision(
            await client.write_guard(
                content=body.content,
                domain=domain,
                path_prefix=parent_path if parent_path else None,
            )
        )
    except Exception as exc:
        guard_decision = _normalize_guard_decision(
            {
                "action": "NOOP",
                "reason": f"write_guard_unavailable: {exc}",
                "method": "exception",
            }
        )

    guard_action = str(guard_decision.get("action") or "NOOP").upper()
    blocked = guard_action != "ADD"
    await _record_guard_event("browse.create_node", guard_decision, blocked=blocked)
    if blocked:
        return {
            "success": False,
            "created": False,
            "reason": "write_guard_blocked",
            "message": (
                "Skipped: write_guard blocked create_node "
                f"(action={guard_action}, method={guard_decision.get('method')})."
            ),
            **_guard_fields(guard_decision),
        }

    async def _write_task():
        return await client.create_memory(
            parent_path=parent_path,
            content=body.content,
            priority=body.priority,
            title=title,
            disclosure=body.disclosure,
            domain=domain,
        )

    try:
        result = await _run_write_lane("browse.create_node", _write_task)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "success": True,
        "created": True,
        **result,
        **_guard_fields(guard_decision),
    }


@router.put("/node")
async def update_node(
    path: str = Query(...),
    domain: str = Query("core"),
    body: NodeUpdate = ...,
    _auth: None = Depends(require_maintenance_api_key),
):
    """
    Update a node's content.
    """
    client = get_sqlite_client()
    domain = _ensure_writable_domain_or_422(domain, operation="update_node")
    
    # Check exists
    memory = await client.get_memory_by_path(
        path, domain=domain, reinforce_access=False
    )
    if not memory:
        raise HTTPException(status_code=404, detail=f"Path not found: {domain}://{path}")

    if body.content is not None:
        try:
            guard_decision = _normalize_guard_decision(
                await client.write_guard(
                    content=body.content,
                    domain=domain,
                    path_prefix=path.rsplit("/", 1)[0] if "/" in path else None,
                    exclude_memory_id=memory.get("id"),
                )
            )
        except Exception as exc:
            guard_decision = _normalize_guard_decision(
                {
                    "action": "NOOP",
                    "reason": f"write_guard_unavailable: {exc}",
                    "method": "exception",
                }
            )
    else:
        guard_decision = _normalize_guard_decision(
            {"action": "BYPASS", "reason": "metadata_only_update", "method": "none"},
            allow_bypass=True,
        )

    guard_action = str(guard_decision.get("action") or "NOOP").upper()
    blocked = False
    if body.content is not None:
        if guard_action == "ADD":
            blocked = False
        elif guard_action == "UPDATE":
            target_id = guard_decision.get("target_id")
            current_memory_id = memory.get("id")
            if (
                not isinstance(target_id, int)
                or not isinstance(current_memory_id, int)
                or target_id != current_memory_id
            ):
                blocked = True
        else:
            blocked = True
    await _record_guard_event("browse.update_node", guard_decision, blocked=blocked)
    if blocked:
        return {
            "success": False,
            "updated": False,
            "reason": "write_guard_blocked",
            "message": (
                "Skipped: write_guard blocked update_node "
                f"(action={guard_action}, method={guard_decision.get('method')})."
            ),
            **_guard_fields(guard_decision),
        }

    async def _write_task():
        return await client.update_memory(
            path=path,
            domain=domain,
            content=body.content,
            priority=body.priority,
            disclosure=body.disclosure,
        )

    # Update (creates new version if content changed, updates path metadata otherwise)
    try:
        result = await _run_write_lane("browse.update_node", _write_task)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "success": True,
        "updated": True,
        "memory_id": result["new_memory_id"],
        **_guard_fields(guard_decision),
    }


@router.delete("/node")
async def delete_node(
    path: str = Query(...),
    domain: str = Query("core"),
    _auth: None = Depends(require_maintenance_api_key),
):
    """
    Delete a single path. If the path has children, this operation is rejected.
    """
    client = get_sqlite_client()
    domain = _ensure_writable_domain_or_422(domain, operation="delete_node")

    async def _write_task():
        return await client.remove_path(path=path, domain=domain)

    try:
        result = await _run_write_lane("browse.delete_node", _write_task)
    except ValueError as e:
        message = str(e)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=409, detail=message)

    return {"success": True, **result}
