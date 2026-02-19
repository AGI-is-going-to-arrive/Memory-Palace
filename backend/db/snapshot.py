"""
Snapshot Manager for Selective Rollback

This module implements a snapshot system that allows the human to review and
selectively roll back the AI's database operations.

Design Principles:
1. Snapshots are taken BEFORE the first modification to a resource in a session
2. Multiple modifications to the same resource in one session share ONE snapshot
3. Rollback creates a NEW version with snapshot content (preserves version chain)
4. Session-based organization for easy cleanup

    Storage Structure:
    snapshots/
    └── {session_id}/
        ├── manifest.json          # Session metadata and resource index
        └── resources/
            └── {safe_resource_id}.json
"""

import os
import json
import hashlib
import shutil
import stat
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path


# Default snapshot directory (relative to workspace root)
DEFAULT_SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "snapshots"
)


def _handle_remove_readonly(func, path, exc_info):
    """Make read-only files writable before retrying removal."""
    exc_type, exc_value, _ = exc_info
    if issubclass(exc_type, PermissionError):
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass
        func(path)
    else:
        raise exc_value


def _force_remove(path: str):
    """Delete files or directories regardless of read-only attributes."""
    if not os.path.exists(path):
        return
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=_handle_remove_readonly)
    else:
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
        except FileNotFoundError:
            pass


class SnapshotManager:
    """
    Manages snapshots for selective rollback functionality.
    
    Each session (typically one agent task/conversation) has its own snapshot space.
    Within a session, each resource gets at most ONE snapshot - the state before
    the first modification.
    """
    
    def __init__(self, snapshot_dir: Optional[str] = None):
        self.snapshot_dir = snapshot_dir or DEFAULT_SNAPSHOT_DIR
        self._ensure_dir_exists(self.snapshot_dir)

    @staticmethod
    def _validate_session_id(session_id: str) -> str:
        """Validate session_id to prevent path traversal and invalid paths."""
        value = str(session_id or "").strip()
        if not value:
            raise ValueError("session_id must not be empty")
        if value in {".", ".."}:
            raise ValueError("session_id contains invalid path segment")
        if "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("session_id contains invalid characters")
        return value
    
    @staticmethod
    def _ensure_dir_exists(path: str):
        """Create directory if it doesn't exist."""
        Path(path).mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def _sanitize_resource_id(resource_id: str) -> str:
        """
        Convert a resource_id to a safe filename.
        
        Resource IDs like URIs "core://path/to/memory" need sanitization.
        We use a deterministic hash suffix for uniqueness to prevent collisions
        (e.g. "core://a/b" vs "core://a_b") while keeping readability.
        """
        # Calculate hash of the ORIGINAL resource_id for uniqueness
        # This prevents "core://a/b" and "core://a_b" from colliding regardless of sanitization
        id_hash = hashlib.md5(resource_id.encode()).hexdigest()[:8]

        # Replace problematic characters
        # 1. Handle protocol separator specifically for better readability
        safe_id = resource_id.replace("://", "__")
        
        # 2. Replace remaining colons, slashes, and backslashes
        safe_id = safe_id.replace(":", "_").replace("/", "_").replace("\\", "_")
        
        # 3. Replace relation arrow
        safe_id = safe_id.replace(">", "_to_")
        
        # Truncate if too long (keeping enough distinct chars + hash)
        # Windows max path is ~260 chars. We leave plenty of buffer.
        if len(safe_id) > 100:
            safe_id = safe_id[:100]
        
        # Always append hash to guarantee uniqueness
        return f"{safe_id}_{id_hash}"
    
    def _get_session_dir(self, session_id: str) -> str:
        """Get the directory path for a session."""
        safe_session_id = self._validate_session_id(session_id)
        return os.path.join(self.snapshot_dir, safe_session_id)
    
    def _get_resources_dir(self, session_id: str) -> str:
        """Get the resources subdirectory for a session."""
        return os.path.join(self._get_session_dir(session_id), "resources")
    
    def _get_manifest_path(self, session_id: str) -> str:
        """Get the manifest file path for a session."""
        return os.path.join(self._get_session_dir(session_id), "manifest.json")
    
    def _get_snapshot_path(self, session_id: str, resource_id: str) -> str:
        """Get the snapshot file path for a specific resource."""
        safe_id = self._sanitize_resource_id(resource_id)
        return os.path.join(self._get_resources_dir(session_id), f"{safe_id}.json")
    
    def _load_manifest(self, session_id: str) -> Dict[str, Any]:
        """Load or create session manifest."""
        manifest_path = self._get_manifest_path(session_id)
        
        if os.path.exists(manifest_path):
            with open(manifest_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        return {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "resources": {}  # resource_id -> metadata
        }
    
    def _save_manifest(self, session_id: str, manifest: Dict[str, Any]):
        """Save session manifest."""
        self._ensure_dir_exists(self._get_session_dir(session_id))
        manifest_path = self._get_manifest_path(session_id)
        
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    
    def has_snapshot(self, session_id: str, resource_id: str) -> bool:
        """Check if a snapshot exists for this resource in this session."""
        # Check manifest first (handles legacy snapshots with different filename formats)
        manifest = self._load_manifest(session_id)
        if resource_id in manifest.get("resources", {}):
            return True
        
        # Fallback to file existence check
        snapshot_path = self._get_snapshot_path(session_id, resource_id)
        return os.path.exists(snapshot_path)
    
    def find_memory_snapshot_by_uri(self, session_id: str, uri: str) -> Optional[str]:
        """
        Find an existing memory content snapshot for a given URI.
        
        When a memory is updated multiple times in one session, each update
        creates a new memory_id (version chain: id=1 → id=5 → id=12 → ...).
        The snapshot resource_id is "memory:{id}", so a naive has_snapshot()
        check on the new id misses the existing snapshot for the old id.
        
        This method scans the manifest for any "memory" type snapshot whose
        stored URI matches the given one, ensuring only ONE content snapshot
        per URI per session regardless of how many updates occur.
        
        Args:
            session_id: Session identifier
            uri: The memory URI (e.g. "core://foo/bar")
            
        Returns:
            The resource_id of the existing snapshot (e.g. "memory:1"),
            or None if no matching snapshot exists.
        """
        manifest = self._load_manifest(session_id)
        for resource_id, meta in manifest.get("resources", {}).items():
            if (meta.get("resource_type") == "memory"
                    and meta.get("uri") == uri):
                return resource_id
        return None
    
    def create_snapshot(
        self,
        session_id: str,
        resource_id: str,
        resource_type: str,
        snapshot_data: Dict[str, Any],
        force: bool = False
    ) -> bool:
        """
        Create a snapshot for a resource.
        
        IMPORTANT: This should be called BEFORE any modification.
        If a snapshot already exists for this resource in this session,
        this call is a no-op (returns False) unless force=True.
        
        Args:
            session_id: Unique session identifier
            resource_id: Resource identifier (e.g., memory URI)
            resource_type: Resource type (e.g., 'memory')
            snapshot_data: The complete resource state to snapshot
            force: If True, overwrite any existing snapshot for this resource.
                   Used by delete operations to ensure the final snapshot
                   reflects the delete rather than an earlier modify.
            
        Returns:
            True if snapshot was created, False if it already existed (and force=False)
        """
        # Check if snapshot already exists
        if not force and self.has_snapshot(session_id, resource_id):
            return False
        
        # Ensure directories exist
        self._ensure_dir_exists(self._get_resources_dir(session_id))
        
        # Create snapshot file
        snapshot = {
            "resource_id": resource_id,
            "resource_type": resource_type,
            "snapshot_time": datetime.now().isoformat(),
            "data": snapshot_data
        }
        
        snapshot_path = self._get_snapshot_path(session_id, resource_id)
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        
        # Update manifest
        manifest = self._load_manifest(session_id)
        manifest["resources"][resource_id] = {
            "resource_type": resource_type,
            "snapshot_time": snapshot["snapshot_time"],
            "operation_type": snapshot_data.get("operation_type", "modify"),
            "file": os.path.basename(snapshot_path),
            "uri": snapshot_data.get("uri")  # For display (path snapshots use URI as resource_id; memory snapshots store it in data)
        }
        self._save_manifest(session_id, manifest)
        
        return True
    
    def get_snapshot(self, session_id: str, resource_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a snapshot for a resource.
        
        Returns:
            The snapshot data, or None if not found
        """
        # First, check manifest for the actual filename (handles legacy snapshots)
        manifest = self._load_manifest(session_id)
        resource_meta = manifest.get("resources", {}).get(resource_id)
        
        if resource_meta and resource_meta.get("file"):
            # Use the filename recorded in manifest
            snapshot_path = os.path.join(
                self._get_resources_dir(session_id), 
                resource_meta["file"]
            )
        else:
            # Fallback to computed path (for forward compatibility)
            snapshot_path = self._get_snapshot_path(session_id, resource_id)
        
        if not os.path.exists(snapshot_path):
            return None
        
        with open(snapshot_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def list_sessions(self) -> List[Dict[str, Any]]:
        """
        List all sessions with snapshots.
        
        Returns:
            List of session metadata (id, created_at, resource_count)
        """
        sessions = []
        
        if not os.path.exists(self.snapshot_dir):
            return sessions
        
        for session_id in os.listdir(self.snapshot_dir):
            session_dir = self._get_session_dir(session_id)
            if os.path.isdir(session_dir):
                manifest = self._load_manifest(session_id)
                resource_count = len(manifest.get("resources", {}))
                
                # Auto-cleanup empty sessions
                if resource_count == 0:
                    self.clear_session(session_id)
                    continue

                sessions.append({
                    "session_id": session_id,
                    "created_at": manifest.get("created_at"),
                    "resource_count": resource_count
                })
        
        # Sort by creation time (newest first)
        sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return sessions
    
    def list_snapshots(self, session_id: str) -> List[Dict[str, Any]]:
        """
        List all snapshots in a session.
        
        Returns:
            List of snapshot metadata (resource_id, resource_type, snapshot_time, operation_type)
        """
        manifest = self._load_manifest(session_id)
        snapshots = []
        
        for resource_id, meta in manifest.get("resources", {}).items():
            snapshots.append({
                "resource_id": resource_id,
                "resource_type": meta.get("resource_type"),
                "snapshot_time": meta.get("snapshot_time"),
                "operation_type": meta.get("operation_type", "modify"),
                "uri": meta.get("uri")  # Display URI
            })
        
        return snapshots
    
    def delete_snapshot(self, session_id: str, resource_id: str) -> bool:
        """
        Delete a specific snapshot.
        
        Returns:
            True if deleted, False if not found
        """
        # First, check manifest for the actual filename (handles legacy snapshots)
        manifest = self._load_manifest(session_id)
        resource_meta = manifest.get("resources", {}).get(resource_id)
        
        if resource_meta and resource_meta.get("file"):
            # Use the filename recorded in manifest
            snapshot_path = os.path.join(
                self._get_resources_dir(session_id), 
                resource_meta["file"]
            )
        else:
            # Fallback to computed path
            snapshot_path = self._get_snapshot_path(session_id, resource_id)
        
        if not os.path.exists(snapshot_path):
            return False
        
        _force_remove(snapshot_path)
        
        # Update manifest
        if resource_id in manifest.get("resources", {}):
            del manifest["resources"][resource_id]
            
            # If no resources left, remove the entire session to prevent clutter
            if not manifest["resources"]:
                self.clear_session(session_id)
            else:
                self._save_manifest(session_id, manifest)
        
        return True
    
    def clear_session(self, session_id: str) -> int:
        """
        Delete all snapshots in a session.
        
        Returns:
            Number of snapshots deleted
        """
        session_dir = self._get_session_dir(session_id)
        
        if not os.path.exists(session_dir):
            return 0
        
        # Count resources before deletion
        manifest = self._load_manifest(session_id)
        count = len(manifest.get("resources", {}))
        
        # Remove the entire session directory tree (manifest + resources)
        _force_remove(session_dir)
        
        return count


# Global singleton
_snapshot_manager: Optional[SnapshotManager] = None


def get_snapshot_manager() -> SnapshotManager:
    """Get the global SnapshotManager instance."""
    global _snapshot_manager
    if _snapshot_manager is None:
        _snapshot_manager = SnapshotManager()
    return _snapshot_manager
