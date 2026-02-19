"""
Browse API - Clean URI-based memory navigation

This replaces the old Entity/Relation/Chapter conceptual split with a simple
hierarchical browser. Every path is just a node with content and children.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Any
from db import get_sqlite_client
from db.sqlite_client import Path as PathModel
from runtime_state import runtime_state
from .maintenance import require_maintenance_api_key
from sqlalchemy import select

router = APIRouter(prefix="/browse", tags=["browse"])


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


def _normalize_guard_decision(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    action = str(payload.get("action") or "ADD").strip().upper()
    if action not in {"ADD", "UPDATE", "NOOP", "DELETE", "BYPASS"}:
        action = "ADD"
    method = str(payload.get("method") or "none").strip().lower() or "none"
    reason = str(payload.get("reason") or "").strip()
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


@router.get("/node")
async def get_node(
    path: str = Query("", description="URI path like 'memory-palace' or 'memory-palace/salem'"),
    domain: str = Query("core")
):
    """
    Get a node's content and its direct children.
    
    This is the only read endpoint you need - it gives you:
    - The current node's full content (or virtual root)
    - Preview of all children (next level)
    - Breadcrumb trail for navigation
    """
    client = get_sqlite_client()
    
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
        memory = await client.get_memory_by_path(path, domain=domain)
        
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
    domain = body.domain.strip() or "core"
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
                "action": "ADD",
                "reason": f"write_guard_unavailable: {exc}",
                "method": "fallback",
            }
        )

    guard_action = str(guard_decision.get("action") or "ADD").upper()
    blocked = guard_action in {"NOOP", "UPDATE", "DELETE"}
    await _record_guard_event("browse.create_node", guard_decision, blocked=blocked)
    if blocked:
        return {
            "success": True,
            "created": False,
            "message": (
                "Skipped: write_guard blocked create_node "
                f"(action={guard_action}, method={guard_decision.get('method')})."
            ),
            **_guard_fields(guard_decision),
        }

    try:
        result = await client.create_memory(
            parent_path=parent_path,
            content=body.content,
            priority=body.priority,
            title=title,
            disclosure=body.disclosure,
            domain=domain,
        )
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
    
    # Check exists
    memory = await client.get_memory_by_path(path, domain=domain)
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
                    "action": "ADD",
                    "reason": f"write_guard_unavailable: {exc}",
                    "method": "fallback",
                }
            )
    else:
        guard_decision = _normalize_guard_decision(
            {"action": "BYPASS", "reason": "metadata_only_update", "method": "none"}
        )

    guard_action = str(guard_decision.get("action") or "BYPASS").upper()
    blocked = False
    if body.content is not None:
        if guard_action in {"NOOP", "DELETE"}:
            blocked = True
        elif guard_action == "UPDATE":
            target_id = guard_decision.get("target_id")
            if isinstance(target_id, int) and target_id != memory.get("id"):
                blocked = True
    await _record_guard_event("browse.update_node", guard_decision, blocked=blocked)
    if blocked:
        return {
            "success": True,
            "updated": False,
            "message": (
                "Skipped: write_guard blocked update_node "
                f"(action={guard_action}, method={guard_decision.get('method')})."
            ),
            **_guard_fields(guard_decision),
        }
    
    # Update (creates new version if content changed, updates path metadata otherwise)
    try:
        result = await client.update_memory(
            path=path,
            domain=domain,
            content=body.content,
            priority=body.priority,
            disclosure=body.disclosure,
        )
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

    try:
        result = await client.remove_path(path=path, domain=domain)
    except ValueError as e:
        message = str(e)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=409, detail=message)

    return {"success": True, **result}
