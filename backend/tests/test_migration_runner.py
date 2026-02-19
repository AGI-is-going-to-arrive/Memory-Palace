import asyncio
import sqlite3
from pathlib import Path

import pytest
from filelock import FileLock

from db.migration_runner import MigrationRunner
from db.sqlite_client import SQLiteClient


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


def _create_legacy_memories_table(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                deprecated BOOLEAN DEFAULT 0,
                created_at DATETIME
            )
            """
        )
        conn.execute(
            "INSERT INTO memories (content, deprecated, created_at) VALUES (?, ?, datetime('now'))",
            ("legacy row", 0),
        )
        conn.commit()


def _create_legacy_support_tables_with_old_indexes(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_gists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                gist_text TEXT NOT NULL,
                source_content_hash TEXT,
                gist_method TEXT,
                quality_score REAL,
                created_at DATETIME
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                tag_type TEXT NOT NULL,
                tag_value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_memory_gists_memory_id ON memory_gists(memory_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_memory_tags_memory_id ON memory_tags(memory_id)"
        )
        conn.commit()


@pytest.mark.asyncio
async def test_migration_runner_applies_and_tracks_versions(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    _create_legacy_memories_table(db_path)

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    migration_file = migrations_dir / "0001_test.sql"
    migration_file.write_text(
        "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    runner = MigrationRunner(_sqlite_url(db_path), migrations_dir=migrations_dir)
    first_applied = await runner.apply_pending()
    second_applied = await runner.apply_pending()

    assert first_applied == ["0001"]
    assert second_applied == []

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert count == 1
        version = conn.execute("SELECT version FROM schema_migrations").fetchone()[0]
        assert version == "0001"


@pytest.mark.asyncio
async def test_migration_runner_detects_checksum_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    _create_legacy_memories_table(db_path)

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    migration_file = migrations_dir / "0001_test.sql"
    migration_file.write_text(
        "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    runner = MigrationRunner(_sqlite_url(db_path), migrations_dir=migrations_dir)
    await runner.apply_pending()

    migration_file.write_text(
        "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, x TEXT);",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        await runner.apply_pending()


@pytest.mark.asyncio
async def test_sqlite_client_init_db_applies_project_migrations(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_memories_table(db_path)

    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.close()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT version FROM schema_migrations WHERE version = '0001'"
        ).fetchone()
        assert row is not None
        row_v2 = conn.execute(
            "SELECT version FROM schema_migrations WHERE version = '0002'"
        ).fetchone()
        assert row_v2 is not None
        row_v3 = conn.execute(
            "SELECT version FROM schema_migrations WHERE version = '0003'"
        ).fetchone()
        assert row_v3 is not None

        columns = {
            col["name"]: col
            for col in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        assert "vitality_score" in columns
        assert "last_accessed_at" in columns
        assert "access_count" in columns

        legacy = conn.execute(
            "SELECT vitality_score, access_count FROM memories ORDER BY id LIMIT 1"
        ).fetchone()
        assert legacy is not None
        assert float(legacy["vitality_score"]) == pytest.approx(1.0)
        assert int(legacy["access_count"]) == 0

        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "memory_gists" in table_names
        assert "memory_tags" in table_names

        index_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_tags_value" in index_names
        assert "idx_memory_gists_memory_id" in index_names
        assert "idx_memory_gists_memory_source_hash_unique" in index_names
        assert "idx_memory_tags_memory_id" in index_names
        assert "idx_memories_cleanup_last_accessed" in index_names
        assert "idx_memories_cleanup_created" in index_names
        assert "idx_paths_memory_domain_path" in index_names
        assert "ix_memory_gists_memory_id" not in index_names
        assert "ix_memory_tags_memory_id" not in index_names


@pytest.mark.asyncio
async def test_migration_runner_handles_database_url_query_params(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory-with-query.db"
    _create_legacy_memories_table(db_path)

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "0001_test.sql").write_text(
        "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    database_url = f"{_sqlite_url(db_path)}?cache=shared"
    runner = MigrationRunner(database_url, migrations_dir=migrations_dir)
    applied = await runner.apply_pending()

    assert applied == ["0001"]
    assert db_path.exists()


@pytest.mark.asyncio
async def test_sqlite_client_init_db_on_fresh_database_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.init_db()
    await client.close()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        version_count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version='0001'"
        ).fetchone()[0]
        assert version_count == 1
        version_count_v2 = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version='0002'"
        ).fetchone()[0]
        assert version_count_v2 == 1
        version_count_v3 = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version='0003'"
        ).fetchone()[0]
        assert version_count_v3 == 1

        columns = {
            col["name"]: col
            for col in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        assert columns["vitality_score"]["dflt_value"] in {"1.0", "1"}
        assert columns["access_count"]["dflt_value"] == "0"


@pytest.mark.asyncio
async def test_sqlite_client_init_db_replaces_legacy_auto_index_names(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-indexes.db"
    _create_legacy_memories_table(db_path)
    _create_legacy_support_tables_with_old_indexes(db_path)

    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    await client.close()

    with sqlite3.connect(db_path) as conn:
        index_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_memory_gists_memory_id" in index_names
        assert "idx_memory_gists_memory_source_hash_unique" in index_names
        assert "idx_memory_tags_memory_id" in index_names
        assert "ix_memory_gists_memory_id" not in index_names
        assert "ix_memory_tags_memory_id" not in index_names


@pytest.mark.asyncio
async def test_migration_runner_serializes_concurrent_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.db"
    _create_legacy_memories_table(db_path)

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "0001_test.sql").write_text(
        "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    runner_a = MigrationRunner(_sqlite_url(db_path), migrations_dir=migrations_dir)
    runner_b = MigrationRunner(_sqlite_url(db_path), migrations_dir=migrations_dir)
    results = await asyncio.gather(runner_a.apply_pending(), runner_b.apply_pending())

    flattened = [version for batch in results for version in batch]
    assert flattened.count("0001") == 1

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert count == 1


@pytest.mark.asyncio
async def test_migration_runner_times_out_when_lock_is_held(tmp_path: Path) -> None:
    db_path = tmp_path / "timeout.db"
    _create_legacy_memories_table(db_path)

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "0001_test.sql").write_text(
        "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    lock_path = tmp_path / "migration.lock"
    with FileLock(str(lock_path), timeout=1):
        runner = MigrationRunner(
            _sqlite_url(db_path),
            migrations_dir=migrations_dir,
            lock_file_path=lock_path,
            lock_timeout_seconds=0.01,
        )
        with pytest.raises(RuntimeError, match="Timed out waiting for migration lock"):
            await runner.apply_pending()


def test_migration_runner_normalizes_relative_env_lock_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "dbdir" / "memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _create_legacy_memories_table(db_path)

    monkeypatch.setenv("DB_MIGRATION_LOCK_FILE", "locks/migrate.lock")
    runner = MigrationRunner(_sqlite_url(db_path), migrations_dir=tmp_path / "migrations")

    expected = (db_path.parent / "locks/migrate.lock").resolve()
    assert runner.lock_file_path == expected


@pytest.mark.asyncio
async def test_migration_runner_skips_in_memory_database(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "0001_test.sql").write_text(
        "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    runner = MigrationRunner(
        "sqlite+aiosqlite:///:memory:", migrations_dir=migrations_dir
    )
    applied = await runner.apply_pending()
    assert applied == []
