import json
import os
import runpy
import sqlite3
import sys
import types
from pathlib import Path

import pytest

import main


def _sqlite_url(path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def test_load_project_dotenv_reads_repo_env_without_overriding_existing_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MAIN_BOOTSTRAP_TEST=from-dotenv\nMAIN_KEEP_EXISTING=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MAIN_BOOTSTRAP_TEST", raising=False)
    monkeypatch.setenv("MAIN_KEEP_EXISTING", "already-set")

    loaded = main._load_project_dotenv(tmp_path)

    assert loaded == env_file
    assert os.getenv("MAIN_BOOTSTRAP_TEST") == "from-dotenv"
    assert os.getenv("MAIN_KEEP_EXISTING") == "already-set"


def test_resolve_cors_uses_restricted_default_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("CORS_ALLOW_CREDENTIALS", raising=False)

    origins, allow_credentials = main._resolve_cors_config()

    assert origins == list(main._DEFAULT_CORS_ALLOW_ORIGINS)
    assert allow_credentials is True


def test_resolve_cors_disables_credentials_for_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")

    origins, allow_credentials = main._resolve_cors_config()

    assert origins == ["*"]
    assert allow_credentials is False


def test_resolve_cors_keeps_credentials_for_explicit_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:5173,https://example.com",
    )
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")

    origins, allow_credentials = main._resolve_cors_config()

    assert origins == ["http://localhost:5173", "https://example.com"]
    assert allow_credentials is True


def test_try_restore_legacy_sqlite_file_restores_valid_regular_file(tmp_path) -> None:
    legacy_path = tmp_path / "agent_memory.db"
    target_path = tmp_path / "memory_palace.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO memories(title) VALUES ('ok')")

    main._try_restore_legacy_sqlite_file(_sqlite_url(target_path))

    assert target_path.exists()
    with sqlite3.connect(target_path) as conn:
        value = conn.execute("SELECT title FROM memories").fetchone()
    assert value == ("ok",)


def test_try_restore_legacy_sqlite_file_skips_symlink_candidate(tmp_path) -> None:
    real_db_path = tmp_path / "real_source.db"
    with sqlite3.connect(real_db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    symlink_path = tmp_path / "agent_memory.db"
    try:
        symlink_path.symlink_to(real_db_path)
    except OSError as exc:
        pytest.skip(f"Symlink unsupported in current environment: {exc}")

    target_path = tmp_path / "memory_palace.db"
    main._try_restore_legacy_sqlite_file(_sqlite_url(target_path))

    assert not target_path.exists()


def test_try_restore_legacy_sqlite_file_skips_invalid_sqlite_file(tmp_path) -> None:
    legacy_path = tmp_path / "agent_memory.db"
    legacy_path.write_text("not-a-sqlite-database", encoding="utf-8")
    target_path = tmp_path / "memory_palace.db"

    main._try_restore_legacy_sqlite_file(_sqlite_url(target_path))

    assert not target_path.exists()


def test_try_restore_legacy_sqlite_file_skips_sqlite_without_expected_schema(
    tmp_path,
) -> None:
    legacy_path = tmp_path / "agent_memory.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO test_table(value) VALUES ('ok')")
    target_path = tmp_path / "memory_palace.db"

    main._try_restore_legacy_sqlite_file(_sqlite_url(target_path))

    assert not target_path.exists()


def test_extract_sqlite_file_path_skips_memory_targets_and_query_string() -> None:
    relative = main._extract_sqlite_file_path(
        "sqlite+aiosqlite:///relative.db?cache=shared"
    )
    absolute = main._extract_sqlite_file_path(
        "sqlite+aiosqlite:////tmp/demo.db?mode=rwc"
    )
    memory_target = main._extract_sqlite_file_path("sqlite+aiosqlite:///:memory:")
    shared_memory_target = main._extract_sqlite_file_path(
        "sqlite+aiosqlite:///file::memory:?cache=shared"
    )

    assert relative == Path("relative.db")
    assert absolute == Path("/tmp/demo.db")
    assert memory_target is None
    assert shared_memory_target is None


def test_try_restore_legacy_sqlite_file_skips_memory_database_urls(tmp_path) -> None:
    legacy_path = tmp_path / "agent_memory.db"
    with sqlite3.connect(legacy_path) as conn:
        conn.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO memories(title) VALUES ('ok')")

    main._try_restore_legacy_sqlite_file("sqlite+aiosqlite:///:memory:")
    main._try_restore_legacy_sqlite_file(
        "sqlite+aiosqlite:///file::memory:?cache=shared"
    )

    assert not Path(":memory:").exists()
    assert not Path("file::memory:").exists()


@pytest.mark.asyncio
async def test_health_hides_internal_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom():
        raise RuntimeError("boom-secret-detail")

    monkeypatch.setattr(main, "get_sqlite_client", _boom)

    payload = await main.health()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["status"] == "degraded"
    assert payload["index"]["reason"] == "internal_error"
    assert payload["index"]["error_type"] == "RuntimeError"
    assert payload["runtime"]["write_lanes"]["reason"] == "internal_error"
    assert payload["runtime"]["index_worker"]["reason"] == "internal_error"
    assert "boom-secret-detail" not in serialized


def test_main_script_binds_all_interfaces_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    fake_uvicorn = types.SimpleNamespace(
        run=lambda app, host, port: calls.append((host, port))
    )

    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    runpy.run_module("main", run_name="__main__")

    assert calls == [("0.0.0.0", 8000)]
