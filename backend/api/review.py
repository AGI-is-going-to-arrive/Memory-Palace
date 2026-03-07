"""
Review API - Selective Rollback for Database Changes (SQLite Backend)

This module provides endpoints for the human to review and selectively rollback
the AI's database modifications.

Design Philosophy:
- Snapshots are split into two dimensions matching the DB tables:
  * PATH snapshots (resource_type="path"): track path creation/deletion/metadata changes
  * MEMORY snapshots (resource_type="memory"): track content changes
- This separation allows independent rollback of path vs content changes
- Old versions are marked deprecated for review
- The human can permanently delete deprecated memories after review
"""
from fastapi import APIRouter, Depends, HTTPException
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional
import difflib
from urllib.parse import unquote

from models import (
    DiffRequest, DiffResponse,
    SessionInfo, SnapshotInfo, SnapshotDetail, ResourceDiff,
    RollbackRequest, RollbackResponse
)
from .utils import get_text_diff
from .maintenance import require_maintenance_api_key
from db.snapshot import get_snapshot_manager
from db.sqlite_client import get_sqlite_client
from runtime_state import runtime_state

router = APIRouter(
    prefix="/review",
    tags=["review"],
    dependencies=[Depends(require_maintenance_api_key)],
)

_TRUTHY_VALUES = {"1", "true", "yes", "on", "enabled"}
_ENABLE_WRITE_LANE_QUEUE = (
    str(os.getenv("RUNTIME_WRITE_LANE_QUEUE", "true")).strip().lower()
    in _TRUTHY_VALUES
)


async def _run_write_lane(
    operation: str,
    task: Callable[[], Awaitable[Any]],
    *,
    session_id: Optional[str] = None,
) -> Any:
    if not _ENABLE_WRITE_LANE_QUEUE:
        return await task()
    resolved_session_id = str(session_id or "").strip() or f"review.{operation}"
    return await runtime_state.write_lanes.run_write(
        session_id=resolved_session_id,
        operation=f"review.{operation}",
        task=task,
    )


def _validate_session_id_or_400(session_id: str) -> str:
    value = str(session_id or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Invalid session_id: empty value.")
    if value in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid session_id path segment.")
    if "/" in value or "\\" in value or "\x00" in value:
        raise HTTPException(status_code=400, detail="Invalid session_id characters.")
    return value


def _raise_review_internal_error(*, operation: str, error: str, exc: Exception) -> None:
    raise HTTPException(
        status_code=500,
        detail={
            "error": error,
            "reason": "internal_error",
            "operation": operation,
        },
    ) from exc


# ========== Session & Snapshot Endpoints ==========

@router.get("/sessions", response_model=List[SessionInfo])
async def list_sessions():
    """
    列出所有有快照的 session
    
    每个 MCP 服务器实例运行期间算作一个 session。
    Session ID 格式: mcp_YYYYMMDD_HHMMSS_{random}
    """
    manager = get_snapshot_manager()
    sessions = manager.list_sessions()
    return [SessionInfo(**s) for s in sessions]


@router.get("/sessions/{session_id}/snapshots", response_model=List[SnapshotInfo])
async def list_session_snapshots(session_id: str):
    """
    列出指定 session 中的所有快照
    
    返回每个被修改过的资源的快照元信息。
    """
    session_id = _validate_session_id_or_400(session_id)
    manager = get_snapshot_manager()
    snapshots = manager.list_snapshots(session_id)
    
    if not snapshots:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found or has no snapshots"
        )
    
    return [SnapshotInfo(**s) for s in snapshots]


@router.get("/sessions/{session_id}/snapshots/{resource_id:path}", response_model=SnapshotDetail)
async def get_snapshot_detail(session_id: str, resource_id: str):
    """
    获取指定快照的详细数据
    
    resource_id 示例:
    - Memory path: "memory-palace", "memory-palace/salem"
    """
    session_id = _validate_session_id_or_400(session_id)
    # Ensure resource_id is decoded (handling %2F and other encoded chars)
    resource_id = unquote(resource_id)
    
    manager = get_snapshot_manager()
    snapshot = manager.get_snapshot(session_id, resource_id)
    
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot for '{resource_id}' not found in session '{session_id}'"
        )
    
    return SnapshotDetail(
        resource_id=snapshot["resource_id"],
        resource_type=snapshot["resource_type"],
        snapshot_time=snapshot["snapshot_time"],
        data=snapshot["data"]
    )


# ========== Diff Helpers ==========

def _compute_diff(old_content: str, new_content: str) -> tuple:
    """
    计算两个文本的 diff
    返回 (unified_diff, summary)
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    
    diff = difflib.unified_diff(old_lines, new_lines, fromfile='snapshot', tofile='current')
    unified = ''.join(diff)
    
    additions = sum(1 for line in unified.splitlines() if line.startswith('+') and not line.startswith('+++'))
    deletions = sum(1 for line in unified.splitlines() if line.startswith('-') and not line.startswith('---'))
    
    if additions == 0 and deletions == 0:
        summary = "No changes"
    else:
        summary = f"+{additions} / -{deletions} lines"
    
    return unified, summary


async def _get_memory_by_path_from_data(data: dict):
    """Helper: fetch current memory via path/domain stored in snapshot data."""
    client = get_sqlite_client()
    path = data.get("path")
    domain = data.get("domain", "core")
    if not path:
        return None
    return await client.get_memory_by_path(path, domain)


async def _resolve_chain_tip_memory_id(
    client: Any,
    memory_id: int,
    *,
    max_hops: int = 64,
) -> Optional[int]:
    """Follow migrated_to pointers and return the final memory id in the chain."""
    current_id = int(memory_id)
    visited: set[int] = set()
    for _ in range(max_hops):
        if current_id in visited:
            return None
        visited.add(current_id)
        version = await client.get_memory_version(current_id)
        if not version:
            return None
        migrated_to = version.get("migrated_to")
        if migrated_to is None:
            return current_id
        try:
            current_id = int(migrated_to)
        except (TypeError, ValueError):
            return None
    return None


async def _ensure_same_version_chain_or_409(
    client: Any,
    *,
    snapshot_memory_id: int,
    current_memory_id: int,
    uri: str,
) -> None:
    if snapshot_memory_id == current_memory_id:
        return

    snapshot_tip = await _resolve_chain_tip_memory_id(client, snapshot_memory_id)
    current_tip = await _resolve_chain_tip_memory_id(client, current_memory_id)
    if snapshot_tip is None or current_tip is None or snapshot_tip != current_tip:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot rollback '{uri}': snapshot memory_id={snapshot_memory_id} "
                f"and current memory_id={current_memory_id} are not in the same version chain."
            ),
        )


# ========== Diff: PATH snapshots ==========

async def _diff_path_create(snapshot: dict, resource_id: str) -> dict:
    """Diff for path creation (create_memory). Rollback = delete memory + path."""
    snapshot_data = {"content": None, "priority": None, "disclosure": None}
    current_memory = await _get_memory_by_path_from_data(snapshot["data"])
    
    if not current_memory:
        current_data = {"content": "[DELETED]", "priority": None, "disclosure": None}
        summary = "Created then deleted"
        has_changes = False
    else:
        current_data = {
            "content": current_memory.get("content", ""),
            "priority": current_memory.get("priority"),
            "disclosure": current_memory.get("disclosure")
        }
        line_count = len(current_data["content"].splitlines())
        summary = f"Created: +{line_count} lines (rollback = delete)"
        has_changes = True
    
    unified = f"--- /dev/null\n+++ {resource_id}\n"
    if current_data["content"] and current_data["content"] != "[DELETED]":
        for line in current_data["content"].splitlines():
            unified += f"+{line}\n"
    
    return {"snapshot_data": snapshot_data, "current_data": current_data,
            "unified": unified, "summary": summary, "has_changes": has_changes}


async def _diff_path_create_alias(snapshot: dict, resource_id: str) -> dict:
    """Diff for alias creation. Rollback = remove alias path only."""
    target_uri = snapshot["data"].get("target_uri", "unknown")
    snapshot_data = {"content": None, "priority": None, "disclosure": None}
    
    current_memory = await _get_memory_by_path_from_data(snapshot["data"])
    
    if not current_memory:
        current_data = {"content": "[ALIAS REMOVED]", "priority": None, "disclosure": None}
        summary = "Alias created then removed"
        has_changes = False
    else:
        current_data = {
            "content": current_memory.get("content", ""),
            "priority": current_memory.get("priority"),
            "disclosure": current_memory.get("disclosure")
        }
        summary = f"Alias created → {target_uri} (rollback = remove alias)"
        has_changes = True
    
    unified = f"--- /dev/null\n+++ {resource_id} (alias → {target_uri})\n"
    if current_data["content"] and current_data["content"] != "[ALIAS REMOVED]":
        unified += f"+[Alias pointing to: {target_uri}]\n"
    
    return {"snapshot_data": snapshot_data, "current_data": current_data,
            "unified": unified, "summary": summary, "has_changes": has_changes}


async def _get_surviving_paths(client, memory_id: int) -> list:
    """Follow the version chain from memory_id to the latest version,
    then return all living paths pointing to that memory.
    
    This lets the human see whether a deleted path was just an alias
    or the last remaining route to the memory content.
    """
    if not memory_id:
        return []
    
    # Follow migrated_to chain to find the latest version
    current_id = memory_id
    visited = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        version = await client.get_memory_version(current_id)
        if not version or not version.get("migrated_to"):
            break
        current_id = version["migrated_to"]
    
    # Now get paths from the latest version
    latest = await client.get_memory_version(current_id)
    if latest:
        return latest.get("paths", [])
    
    # Fallback: check original memory_id
    original = await client.get_memory_version(memory_id)
    return original.get("paths", []) if original else []


async def _diff_path_delete(snapshot: dict, resource_id: str) -> dict:
    """Diff for path deletion. Rollback = restore path.
    
    Old content is fetched from DB via memory_id rather than from the
    snapshot file, leveraging the version chain.
    Also includes surviving paths so the human can tell if this is just
    an alias removal or if the entire memory is being discarded.
    """
    client = get_sqlite_client()
    
    # --- Retrieve old content from DB ---
    old_memory_id = snapshot["data"].get("memory_id")
    old_version = await client.get_memory_version(old_memory_id) if old_memory_id else None
    
    if old_version:
        old_content = old_version.get("content", "")
    else:
        old_content = "[已被永久删除，无法显示旧内容]"
    
    snapshot_data = {
        "content": old_content,
        "priority": snapshot["data"].get("priority", snapshot["data"].get("importance")),
        "disclosure": snapshot["data"].get("disclosure")
    }
    
    current_memory = await _get_memory_by_path_from_data(snapshot["data"])
    
    if not current_memory:
        current_data = {"content": "[DELETED]", "priority": None, "disclosure": None}
    else:
        current_data = {
            "content": current_memory.get("content", ""),
            "priority": current_memory.get("priority"),
            "disclosure": current_memory.get("disclosure")
        }
    
    # --- Find surviving paths for this memory ---
    surviving_paths = await _get_surviving_paths(client, old_memory_id)
    # Exclude the deleted path itself from the list
    deleted_uri = snapshot["data"].get("uri") or f"{snapshot['data'].get('domain', 'core')}://{snapshot['data'].get('path')}"
    surviving_paths = [p for p in surviving_paths if p != deleted_uri]
    
    unified, summary = _compute_diff(snapshot_data["content"], current_data["content"])
    
    if current_data["content"] == "[DELETED]":
        summary = "Deleted (rollback = restore)"
    
    current_data["surviving_paths"] = surviving_paths
    
    return {"snapshot_data": snapshot_data, "current_data": current_data,
            "unified": unified, "summary": summary, "has_changes": True}


async def _diff_path_modify_meta(snapshot: dict, resource_id: str) -> dict:
    """Diff for path metadata change (priority/disclosure). Rollback = restore metadata."""
    snapshot_data = {
        "content": None,
        "priority": snapshot["data"].get("priority", snapshot["data"].get("importance")),
        "disclosure": snapshot["data"].get("disclosure")
    }
    
    current_memory = await _get_memory_by_path_from_data(snapshot["data"])
    
    if not current_memory:
        current_data = {"content": None, "priority": None, "disclosure": None}
        summary = "Path no longer exists"
        has_changes = False
    else:
        current_data = {
            "content": None,
            "priority": current_memory.get("priority"),
            "disclosure": current_memory.get("disclosure")
        }
        
        meta_changes = []
        for key in ["priority", "disclosure"]:
            if snapshot_data.get(key) != current_data.get(key):
                meta_changes.append(f"{key}: {snapshot_data.get(key)} → {current_data.get(key)}")
        
        if meta_changes:
            summary = "Metadata: " + ", ".join(meta_changes)
            has_changes = True
        else:
            summary = "No metadata changes"
            has_changes = False
    
    return {"snapshot_data": snapshot_data, "current_data": current_data,
            "unified": "", "summary": summary, "has_changes": has_changes}


# ========== Diff: MEMORY snapshots ==========

async def _diff_memory_content(snapshot: dict, resource_id: str) -> dict:
    """Diff for memory content change. Rollback = rollback_to_memory.
    
    Old content is fetched from DB via memory_id (the deprecated Memory row
    is preserved by the version chain).  If the old row was permanently
    deleted, a fallback message is shown instead.
    """
    client = get_sqlite_client()
    
    # --- Retrieve old content from DB instead of snapshot file ---
    old_memory_id = snapshot["data"].get("memory_id")
    old_version = await client.get_memory_version(old_memory_id) if old_memory_id else None
    
    if old_version:
        old_content = old_version.get("content", "")
    else:
        old_content = "[已被永久删除，无法显示旧内容]"
    
    snapshot_data = {
        "content": old_content,
        "priority": None,
        "disclosure": None
    }
    
    # --- Retrieve current content via path (with alias fallback) ---
    current_memory = await _get_memory_by_path_from_data(snapshot["data"])
    
    if not current_memory:
        for alt_uri_str in snapshot["data"].get("all_paths", []):
            if "://" in alt_uri_str:
                alt_domain, alt_path = alt_uri_str.split("://", 1)
            else:
                alt_domain, alt_path = "core", alt_uri_str
            orig_path = snapshot["data"].get("path")
            orig_domain = snapshot["data"].get("domain", "core")
            if alt_path == orig_path and alt_domain == orig_domain:
                continue
            current_memory = await client.get_memory_by_path(alt_path, alt_domain)
            if current_memory:
                break
    
    if not current_memory:
        current_data = {"content": "[PATH DELETED]", "priority": None, "disclosure": None}
    else:
        current_data = {
            "content": current_memory.get("content", ""),
            "priority": None,
            "disclosure": None
        }
    
    unified, summary = _compute_diff(snapshot_data["content"], current_data.get("content", ""))
    has_changes = snapshot_data["content"] != current_data.get("content", "")
    
    return {"snapshot_data": snapshot_data, "current_data": current_data,
            "unified": unified, "summary": summary, "has_changes": has_changes}


# ========== Diff Endpoint ==========

# Dispatch table: (resource_type, operation_type) → diff handler
_DIFF_HANDLERS = {
    ("path", "create"):       _diff_path_create,
    ("path", "create_alias"): _diff_path_create_alias,
    ("path", "delete"):       _diff_path_delete,
    ("path", "modify_meta"):  _diff_path_modify_meta,
    ("memory", "modify_content"): _diff_memory_content,
}

# Legacy compatibility: old snapshots used resource_type="memory" for everything
_LEGACY_DIFF_HANDLERS = {
    "create":       _diff_path_create,
    "create_alias": _diff_path_create_alias,
    "delete":       _diff_path_delete,
    "modify":       _diff_memory_content,  # Old "modify" = content change
}


@router.get("/sessions/{session_id}/diff/{resource_id:path}", response_model=ResourceDiff)
async def get_resource_diff(session_id: str, resource_id: str):
    """
    获取快照与当前状态的 diff

    Handles both new split snapshots (path/memory) and legacy snapshots.
    """
    session_id = _validate_session_id_or_400(session_id)
    resource_id = unquote(resource_id)
    
    manager = get_snapshot_manager()
    snapshot = manager.get_snapshot(session_id, resource_id)
    
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot for '{resource_id}' not found in session '{session_id}'"
        )
    
    resource_type = snapshot["resource_type"]
    operation_type = snapshot["data"].get("operation_type", "modify")
    
    # Try new dispatch table first, then legacy fallback
    handler = _DIFF_HANDLERS.get((resource_type, operation_type))
    if not handler:
        handler = _LEGACY_DIFF_HANDLERS.get(operation_type)
    if not handler:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown snapshot type: {resource_type}/{operation_type}"
        )
    
    result = await handler(snapshot, resource_id)
    
    return ResourceDiff(
        resource_id=resource_id,
        resource_type=resource_type,
        snapshot_time=snapshot["snapshot_time"],
        snapshot_data=result["snapshot_data"],
        current_data=result["current_data"],
        diff_unified=result["unified"],
        diff_summary=result["summary"],
        has_changes=result["has_changes"]
    )


# ========== Rollback Helpers ==========

async def _rollback_path(data: dict, *, lane_session_id: Optional[str] = None) -> dict:
    """Rollback a path-level operation."""
    client = get_sqlite_client()
    path = data.get("path")
    domain = data.get("domain", "core")
    operation_type = data.get("operation_type")
    uri = data.get("uri", f"{domain}://{path}")
    
    if operation_type == "create":
        snapshot_memory_id_raw = data.get("memory_id")
        snapshot_memory_id: Optional[int] = None
        if snapshot_memory_id_raw is not None:
            try:
                snapshot_memory_id = int(snapshot_memory_id_raw)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid snapshot memory_id for '{uri}'.",
                )
        current_for_validation = await client.get_memory_by_path(
            path, domain, reinforce_access=False
        )
        if snapshot_memory_id is not None and current_for_validation:
            current_memory_id = current_for_validation.get("id")
            if (
                isinstance(current_memory_id, int)
                and current_memory_id != snapshot_memory_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot rollback create for '{uri}': snapshot memory_id="
                        f"{snapshot_memory_id} does not match current memory_id="
                        f"{current_memory_id}."
                    ),
                )

        # Rollback of create = delete the memory/path and any later descendants.
        # Descendants must be removed first to avoid leaving dangling child paths.
        descendants_deleted = 0
        orphan_memories_deleted = 0
        descendant_memory_ids: List[int] = []

        if path:
            # Descendants may be created under any alias path that points to the same
            # memory node, so we must scan across domains before deleting the root node.
            all_paths = await client.get_all_paths(domain=None)
            current = await client.get_memory_by_path(
                path, domain, reinforce_access=False
            )
            current_memory_id = current.get("id") if isinstance(current, dict) else None

            root_aliases: set[tuple[str, str]] = set()
            if isinstance(current_memory_id, int) and current_memory_id > 0:
                for item in all_paths:
                    try:
                        item_memory_id = int(item.get("memory_id"))
                    except (TypeError, ValueError):
                        continue
                    if item_memory_id != current_memory_id:
                        continue
                    alias_domain = str(item.get("domain") or "").strip()
                    alias_path = str(item.get("path") or "").strip()
                    if alias_domain and alias_path:
                        root_aliases.add((alias_domain, alias_path))

            if not root_aliases:
                root_aliases.add((str(domain or "core"), str(path)))

            descendants_map: Dict[str, Dict[str, Any]] = {}
            for alias_domain, alias_path in root_aliases:
                descendant_prefix = f"{alias_path}/"
                for item in all_paths:
                    item_domain = str(item.get("domain") or "").strip()
                    item_path = str(item.get("path") or "").strip()
                    if item_domain != alias_domain:
                        continue
                    if not item_path.startswith(descendant_prefix):
                        continue
                    descendants_map[f"{item_domain}://{item_path}"] = item

            descendants = list(descendants_map.values())
            descendants.sort(
                key=lambda item: (
                    str(item.get("path") or "").count("/"),
                    str(item.get("domain") or ""),
                    str(item.get("path") or ""),
                ),
                reverse=True,
            )

            for item in descendants:
                child_domain = str(item.get("domain") or "").strip()
                child_path = str(item.get("path") or "").strip()
                if (
                    not child_path
                    or not child_domain
                    or (child_domain, child_path) in root_aliases
                ):
                    continue
                try:
                    async def _write_task_remove_child(
                        _child_path: str = child_path,
                        _child_domain: str = child_domain,
                    ) -> Any:
                        return await client.remove_path(_child_path, _child_domain)

                    await _run_write_lane(
                        "rollback.remove_path",
                        _write_task_remove_child,
                        session_id=lane_session_id,
                    )
                    descendants_deleted += 1
                except ValueError:
                    # Path already removed by concurrent/manual operations.
                    continue

                child_memory_id = item.get("memory_id")
                try:
                    parsed_memory_id = int(child_memory_id)
                except (TypeError, ValueError):
                    continue
                if parsed_memory_id > 0:
                    descendant_memory_ids.append(parsed_memory_id)

        current = await client.get_memory_by_path(
            path, domain, reinforce_access=False
        )

        # Best-effort cleanup of memories orphaned by descendant path deletion.
        # require_orphan=True ensures we only delete in safe conditions.
        parent_memory_id = current.get("id") if current else None
        for memory_id in list(dict.fromkeys(descendant_memory_ids)):
            if parent_memory_id is not None and memory_id == parent_memory_id:
                continue
            try:
                async def _write_task_delete_orphan(
                    _memory_id: int = memory_id,
                ) -> Any:
                    return await client.permanently_delete_memory(
                        _memory_id,
                        require_orphan=True,
                    )

                await _run_write_lane(
                    "rollback.delete_orphan_memory",
                    _write_task_delete_orphan,
                    session_id=lane_session_id,
                )
                orphan_memories_deleted += 1
            except (ValueError, PermissionError, RuntimeError):
                continue

        if not current:
            return {
                "deleted": True,
                "descendants_deleted": descendants_deleted,
                "orphan_memories_deleted": orphan_memories_deleted,
            }
        try:
            async def _write_task_delete_current() -> Any:
                return await client.permanently_delete_memory(current["id"])

            await _run_write_lane(
                "rollback.delete_memory",
                _write_task_delete_current,
                session_id=lane_session_id,
            )
            return {
                "deleted": True,
                "descendants_deleted": descendants_deleted,
                "orphan_memories_deleted": orphan_memories_deleted,
            }
        except (ValueError, PermissionError, RuntimeError) as e:
            raise HTTPException(status_code=409, detail=f"Cannot delete '{uri}': {e}")
    
    elif operation_type == "create_alias":
        # Rollback of alias creation = remove the alias path only
        try:
            async def _write_task_remove_alias() -> Any:
                return await client.remove_path(path, domain)

            await _run_write_lane(
                "rollback.remove_alias",
                _write_task_remove_alias,
                session_id=lane_session_id,
            )
        except ValueError as exc:
            existing_alias = await client.get_memory_by_path(
                path, domain, reinforce_access=False
            )
            if existing_alias is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot rollback alias '{uri}': {exc}",
                ) from exc
            return {"deleted": True, "alias_removed": False, "no_change": True}
        return {"deleted": True, "alias_removed": True}
    
    elif operation_type == "delete":
        # Rollback of delete = restore the path
        memory_id = data.get("memory_id")
        
        # Verify the target memory still exists in DB
        target_version = await client.get_memory_version(memory_id) if memory_id else None
        if not target_version:
            raise HTTPException(
                status_code=410,
                detail=f"旧版本 (memory_id={memory_id}) 已被永久删除，无法恢复 '{uri}'。"
            )
        
        try:
            async def _write_task_restore_path() -> Any:
                return await client.restore_path(
                    path=path,
                    domain=domain,
                    memory_id=memory_id,
                    priority=data.get("priority", data.get("importance", 0)),
                    disclosure=data.get("disclosure"),
                )

            await _run_write_lane(
                "rollback.restore_path",
                _write_task_restore_path,
                session_id=lane_session_id,
            )
            return {"restored": True, "new_version": memory_id}
        except ValueError as e:
            raise HTTPException(status_code=409, detail=f"Cannot restore '{uri}': {e}")
    
    elif operation_type == "modify_meta":
        # Rollback of metadata change = restore original priority/disclosure
        current = await client.get_memory_by_path(
            path, domain, reinforce_access=False
        )
        if not current:
            raise HTTPException(status_code=404, detail=f"'{uri}' no longer exists")

        async def _write_task_update_meta() -> Any:
            return await client.restore_path_metadata(
                path=path,
                domain=domain,
                priority=data.get("priority", data.get("importance", 0)),
                disclosure=data.get("disclosure"),
            )

        await _run_write_lane(
            "rollback.update_meta",
            _write_task_update_meta,
            session_id=lane_session_id,
        )
        return {"metadata_restored": True}
    
    else:
        raise HTTPException(status_code=400, detail=f"Unknown path operation: {operation_type}")


async def _rollback_memory_content(
    data: dict, *, lane_session_id: Optional[str] = None
) -> dict:
    """Rollback a memory content change."""
    client = get_sqlite_client()
    memory_id_raw = data.get("memory_id")
    try:
        memory_id = int(memory_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Snapshot missing memory_id")
    path = data.get("path")
    domain = data.get("domain", "core")
    uri = data.get("uri", f"{domain}://{path}")
    
    if memory_id <= 0:
        raise HTTPException(status_code=400, detail="Snapshot missing memory_id")
    
    # Verify the target memory still exists in DB (not permanently deleted)
    target_version = await client.get_memory_version(memory_id)
    if not target_version:
        raise HTTPException(
            status_code=410,
            detail=f"旧版本 (memory_id={memory_id}) 已被永久删除，无法回滚。"
        )
    
    current = await client.get_memory_by_path(
        path, domain, reinforce_access=False
    )
    
    # Fallback: if original path was deleted, try alternative paths from snapshot
    if not current:
        for alt_uri_str in data.get("all_paths", []):
            if "://" in alt_uri_str:
                alt_domain, alt_path = alt_uri_str.split("://", 1)
            else:
                alt_domain, alt_path = "core", alt_uri_str
            if alt_path == path and alt_domain == domain:
                continue  # Skip the one we already tried
            current = await client.get_memory_by_path(
                alt_path, alt_domain, reinforce_access=False
            )
            if current:
                path, domain = alt_path, alt_domain
                break
    
    if not current:
        raise HTTPException(
            status_code=404,
            detail=f"Path '{uri}' no longer exists and no alternative paths found. Cannot rollback content."
        )

    current_memory_id = current.get("id")
    if not isinstance(current_memory_id, int):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot rollback '{uri}': current memory_id is invalid.",
        )

    await _ensure_same_version_chain_or_409(
        client,
        snapshot_memory_id=memory_id,
        current_memory_id=current_memory_id,
        uri=uri,
    )

    if memory_id == current_memory_id:
        return {"no_change": True, "new_version": memory_id}

    async def _write_task_rollback_to_memory() -> Any:
        return await client.rollback_to_memory(path, memory_id, domain)

    result = await _run_write_lane(
        "rollback.rollback_to_memory",
        _write_task_rollback_to_memory,
        session_id=lane_session_id,
    )
    return {"new_version": result["restored_memory_id"]}


async def _rollback_legacy_modify(
    data: dict, *, lane_session_id: Optional[str] = None
) -> dict:
    """Rollback for legacy 'modify' snapshots that combined content + metadata."""
    client = get_sqlite_client()
    path = data.get("path")
    domain = data.get("domain", "core")
    uri = data.get("uri", f"{domain}://{path}")
    snapshot_memory_id_raw = data.get("memory_id")
    try:
        snapshot_memory_id = int(snapshot_memory_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Snapshot missing memory_id")
    
    if snapshot_memory_id <= 0:
        raise HTTPException(status_code=400, detail="Snapshot missing memory_id")
    
    # Verify the target memory still exists in DB
    target_version = await client.get_memory_version(snapshot_memory_id)
    if not target_version:
        raise HTTPException(
            status_code=410,
            detail=f"旧版本 (memory_id={snapshot_memory_id}) 已被永久删除，无法回滚。"
        )
    
    current = await client.get_memory_by_path(
        path, domain, reinforce_access=False
    )
    if not current:
        raise HTTPException(status_code=404, detail=f"'{uri}' no longer exists")

    current_memory_id = current.get("id")
    if not isinstance(current_memory_id, int):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot rollback '{uri}': current memory_id is invalid.",
        )

    await _ensure_same_version_chain_or_409(
        client,
        snapshot_memory_id=snapshot_memory_id,
        current_memory_id=current_memory_id,
        uri=uri,
    )
    
    snapshot_priority = data.get("priority", data.get("importance"))
    snapshot_disclosure = data.get("disclosure")
    has_version_change = snapshot_memory_id != current_memory_id
    has_meta_change = (
        snapshot_priority != current.get("priority") or
        snapshot_disclosure != current.get("disclosure")
    )
    
    if not has_version_change and not has_meta_change:
        return {"no_change": True, "new_version": current.get("id")}
    
    restored_id = current.get("id")
    
    if has_version_change and has_meta_change:
        async def _write_task_restore_legacy_modify() -> Any:
            return await client.rollback_to_memory(
                path,
                snapshot_memory_id,
                domain,
                restore_path_metadata=True,
                restore_priority=snapshot_priority,
                restore_disclosure=snapshot_disclosure,
            )

        result = await _run_write_lane(
            "rollback.restore_legacy_modify",
            _write_task_restore_legacy_modify,
            session_id=lane_session_id,
        )
        restored_id = result["restored_memory_id"]
    elif has_version_change:
        async def _write_task_rollback_to_memory() -> Any:
            return await client.rollback_to_memory(path, snapshot_memory_id, domain)

        result = await _run_write_lane(
            "rollback.rollback_to_memory",
            _write_task_rollback_to_memory,
            session_id=lane_session_id,
        )
        restored_id = result["restored_memory_id"]
    
    if has_meta_change and not has_version_change:
        async def _write_task_update_legacy_meta() -> Any:
            return await client.restore_path_metadata(
                path=path,
                domain=domain,
                priority=snapshot_priority if snapshot_priority is not None else 0,
                disclosure=snapshot_disclosure,
            )

        await _run_write_lane(
            "rollback.update_meta",
            _write_task_update_legacy_meta,
            session_id=lane_session_id,
        )
    
    return {"new_version": restored_id}


# ========== Rollback Endpoint ==========

@router.post("/sessions/{session_id}/rollback/{resource_id:path}", response_model=RollbackResponse)
async def rollback_resource(session_id: str, resource_id: str, request: RollbackRequest):
    """
    执行回滚：将资源恢复到快照状态

    路径快照 (resource_type="path"):
    - create → 删除新创建的 memory 和 path
    - create_alias → 移除别名路径
    - delete → 恢复被删除的路径
    - modify_meta → 恢复 priority/disclosure

    内容快照 (resource_type="memory"):
    - modify_content → 将 path 指回旧版本的 memory
    """
    session_id = _validate_session_id_or_400(session_id)
    resource_id = unquote(resource_id)
    
    manager = get_snapshot_manager()
    snapshot = manager.get_snapshot(session_id, resource_id)
    
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot for '{resource_id}' not found in session '{session_id}'"
        )
    
    resource_type = snapshot["resource_type"]
    data = snapshot["data"]
    operation_type = data.get("operation_type", "modify")
    lane_session_id = f"review.rollback:{session_id}"
    
    try:
        # Dispatch based on resource_type
        if resource_type == "path":
            result = await _rollback_path(data, lane_session_id=lane_session_id)
        elif resource_type == "memory":
            if operation_type == "modify_content":
                result = await _rollback_memory_content(
                    data,
                    lane_session_id=lane_session_id,
                )
            elif operation_type == "modify":
                # Legacy: old "modify" snapshots with resource_type="memory"
                result = await _rollback_legacy_modify(
                    data,
                    lane_session_id=lane_session_id,
                )
            elif operation_type in ("create", "delete", "create_alias"):
                # Legacy: old snapshots used resource_type="memory" for all operations
                result = await _rollback_path(
                    data,
                    lane_session_id=lane_session_id,
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unknown memory operation: {operation_type}")
        else:
            raise HTTPException(status_code=400, detail=f"Unknown resource type: {resource_type}")
        
        # Build response message
        message = _build_rollback_message(resource_id, operation_type, result)
        
        return RollbackResponse(
            resource_id=resource_id,
            resource_type=resource_type,
            success=True,
            message=message,
            new_version=result.get("new_version")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        _raise_review_internal_error(
            operation="rollback_resource",
            error="rollback_failed",
            exc=e,
        )


def _build_rollback_message(resource_id: str, operation_type: str, result: dict) -> str:
    """Generate a human-readable rollback result message."""
    if result.get("no_change"):
        return "No changes detected. Already matches snapshot."
    
    messages = {
        "create":         f"Deleted created resource '{resource_id}'.",
        "create_alias":   f"Removed alias '{resource_id}'.",
        "delete":         f"Restored deleted resource '{resource_id}'.",
        "modify_meta":    f"Restored metadata for '{resource_id}'.",
        "modify_content": f"Restored content to snapshot version (memory_id={result.get('new_version')}).",
        "modify":         f"Restored to snapshot version (memory_id={result.get('new_version')}).",
    }
    
    return messages.get(operation_type, f"Rollback completed for '{resource_id}'.")


@router.delete("/sessions/{session_id}/snapshots/{resource_id:path}")
async def delete_snapshot(session_id: str, resource_id: str):
    """
    删除指定的快照（确认不需要回滚后）
    """
    session_id = _validate_session_id_or_400(session_id)
    # Ensure resource_id is decoded
    resource_id = unquote(resource_id)
    
    manager = get_snapshot_manager()
    deleted = manager.delete_snapshot(session_id, resource_id)
    
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot for '{resource_id}' not found in session '{session_id}'"
        )
    
    return {"message": f"Snapshot for '{resource_id}' deleted"}


@router.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """
    清除整个 session 的所有快照
    
    当 human 确认所有修改都 OK 后调用此端点清理。
    """
    session_id = _validate_session_id_or_400(session_id)
    manager = get_snapshot_manager()
    count = manager.clear_session(session_id)
    
    if count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found or already empty"
        )
    
    return {"message": f"Session '{session_id}' cleared, {count} snapshots deleted"}


# ========== Deprecated Memory Management (Human Only) ==========

@router.get("/deprecated")
async def list_deprecated_memories():
    """
    列出所有被标记为 deprecated 的记忆
    
    这些是 AI 更新/删除后留下的旧版本，等待 human 审核后永久删除。
    """
    client = get_sqlite_client()
    
    try:
        memories = await client.get_deprecated_memories()
        return {
            "count": len(memories),
            "memories": memories
        }
    except Exception as e:
        _raise_review_internal_error(
            operation="list_deprecated_memories",
            error="list_deprecated_failed",
            exc=e,
        )


@router.delete("/memories/{memory_id}")
async def permanently_delete_memory(memory_id: int):
    """
    永久删除一条记忆（human 专用）
    
    这是真正的删除操作，不可恢复。
    AI 无法调用此接口（仅限 human 通过前端操作）。
    """
    client = get_sqlite_client()
    
    try:
        async def _write_task_delete_memory() -> Any:
            return await client.permanently_delete_memory(
                memory_id,
                require_orphan=True,
            )

        await _run_write_lane(
            "deprecated.permanently_delete_memory",
            _write_task_delete_memory,
            session_id="review.deprecated_delete",
        )
        return {"message": f"Memory {memory_id} permanently deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        _raise_review_internal_error(
            operation="permanently_delete_memory",
            error="delete_deprecated_failed",
            exc=e,
        )


# ========== Utility Endpoints ==========

@router.post("/diff", response_model=DiffResponse)
async def compare_text(request: DiffRequest):
    """
    比较两个文本并返回diff

    Args:
        request: 包含text_a和text_b

    Returns:
        DiffResponse: 包含diff_html, diff_unified, summary
    """
    try:
        diff_html, diff_unified, summary = get_text_diff(
            request.text_a,
            request.text_b
        )
        return DiffResponse(
            diff_html=diff_html,
            diff_unified=diff_unified,
            summary=summary
        )
    except Exception as e:
        _raise_review_internal_error(
            operation="compare_text",
            error="compare_text_failed",
            exc=e,
        )
