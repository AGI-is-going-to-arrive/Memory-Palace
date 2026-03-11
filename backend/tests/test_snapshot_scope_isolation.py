import json
from pathlib import Path

from db.snapshot import SnapshotManager


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


def test_snapshot_manager_filters_sessions_by_current_database_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = SnapshotManager(str(tmp_path / "snapshots"))
    db_a = tmp_path / "scope-a.db"
    db_b = tmp_path / "scope-b.db"

    monkeypatch.setenv("DATABASE_URL", _sqlite_url(db_a))
    created = manager.create_snapshot(
        "session-a",
        "notes://alpha",
        "path",
        {
            "uri": "notes://alpha",
            "operation_type": "create",
        },
    )
    assert created is True

    manifest_a = json.loads(
        (tmp_path / "snapshots" / "session-a" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest_a["database_label"] == "scope-a.db"
    assert manager.list_sessions()[0]["session_id"] == "session-a"

    monkeypatch.setenv("DATABASE_URL", _sqlite_url(db_b))
    created = manager.create_snapshot(
        "session-b",
        "notes://beta",
        "path",
        {
            "uri": "notes://beta",
            "operation_type": "create",
        },
    )
    assert created is True

    sessions = manager.list_sessions()
    assert [item["session_id"] for item in sessions] == ["session-b"]
    assert manager.list_snapshots("session-a") == []
    assert manager.get_snapshot("session-a", "notes://alpha") is None


def test_snapshot_manager_hides_legacy_unscoped_sessions_when_database_scope_is_set(
    monkeypatch,
    tmp_path: Path,
) -> None:
    snapshot_dir = tmp_path / "snapshots"
    session_dir = snapshot_dir / "legacy-session"
    resources_dir = session_dir / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)

    (resources_dir / "legacy.json").write_text(
        json.dumps(
            {
                "resource_id": "notes://legacy",
                "resource_type": "path",
                "snapshot_time": "2026-03-11T00:00:00",
                "data": {
                    "uri": "notes://legacy",
                    "operation_type": "create",
                },
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "manifest.json").write_text(
        json.dumps(
            {
                "session_id": "legacy-session",
                "created_at": "2026-03-11T00:00:00",
                "resources": {
                    "notes://legacy": {
                        "resource_type": "path",
                        "snapshot_time": "2026-03-11T00:00:00",
                        "operation_type": "create",
                        "file": "legacy.json",
                        "uri": "notes://legacy",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DATABASE_URL", _sqlite_url(tmp_path / "active.db"))
    manager = SnapshotManager(str(snapshot_dir))

    assert manager.list_sessions() == []
    assert manager.list_snapshots("legacy-session") == []
    assert manager.get_snapshot("legacy-session", "notes://legacy") is None
