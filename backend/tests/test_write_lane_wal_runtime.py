from pathlib import Path

import pytest
from sqlalchemy import text

from db.sqlite_client import SQLiteClient


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


@pytest.mark.asyncio
async def test_runtime_write_wal_defaults_keep_delete_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RUNTIME_WRITE_WAL_ENABLED", "false")
    monkeypatch.setenv("RUNTIME_WRITE_JOURNAL_MODE", "delete")
    monkeypatch.setenv("RUNTIME_WRITE_WAL_SYNCHRONOUS", "normal")
    monkeypatch.setenv("RUNTIME_WRITE_BUSY_TIMEOUT_MS", "120")
    monkeypatch.setenv("RUNTIME_WRITE_WAL_AUTOCHECKPOINT", "1000")

    client = SQLiteClient(_sqlite_url(tmp_path / "runtime-write-wal-defaults.db"))
    await client.init_db()
    status = await client.get_index_status()
    await client.close()

    capabilities = status["capabilities"]
    assert capabilities["runtime_write_wal_enabled"] is False
    assert capabilities["runtime_write_journal_mode_requested"] == "delete"
    assert capabilities["runtime_write_journal_mode_effective"] == "delete"
    assert capabilities["runtime_write_wal_synchronous_effective"] == "default"
    assert capabilities["runtime_write_busy_timeout_ms"] == 120
    assert capabilities["runtime_write_wal_autocheckpoint"] == 1000
    assert capabilities["runtime_write_pragma_status"] == "disabled"
    assert capabilities["runtime_write_pragma_error"] == ""


@pytest.mark.asyncio
async def test_runtime_write_wal_enabled_applies_wal_pragmas_when_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RUNTIME_WRITE_WAL_ENABLED", "true")
    monkeypatch.setenv("RUNTIME_WRITE_JOURNAL_MODE", "wal")
    monkeypatch.setenv("RUNTIME_WRITE_WAL_SYNCHRONOUS", "normal")
    monkeypatch.setenv("RUNTIME_WRITE_BUSY_TIMEOUT_MS", "333")
    monkeypatch.setenv("RUNTIME_WRITE_WAL_AUTOCHECKPOINT", "97")

    client = SQLiteClient(_sqlite_url(tmp_path / "runtime-write-wal-enabled.db"))
    await client.init_db()

    async with client.session() as session:
        journal_mode = str(
            (await session.execute(text("PRAGMA journal_mode"))).scalar() or ""
        ).strip().lower()
        busy_timeout_ms = int(
            (await session.execute(text("PRAGMA busy_timeout"))).scalar() or 0
        )
        synchronous_raw = str(
            (await session.execute(text("PRAGMA synchronous"))).scalar() or ""
        ).strip().lower()
        wal_autocheckpoint = int(
            (await session.execute(text("PRAGMA wal_autocheckpoint"))).scalar() or 0
        )

    status = await client.get_index_status()
    await client.close()

    capabilities = status["capabilities"]
    assert capabilities["runtime_write_wal_enabled"] is True
    assert capabilities["runtime_write_journal_mode_requested"] == "wal"
    assert capabilities["runtime_write_busy_timeout_ms"] == 333
    assert busy_timeout_ms == 333

    if journal_mode == "wal":
        assert capabilities["runtime_write_journal_mode_effective"] == "wal"
        assert capabilities["runtime_write_pragma_status"] == "enabled"
        assert capabilities["runtime_write_wal_synchronous_effective"] == "normal"
        assert synchronous_raw in {"1", "normal"}
        assert capabilities["runtime_write_wal_autocheckpoint"] == 97
        assert wal_autocheckpoint == 97
    else:
        # Some runtimes can reject WAL mode; contract requires fail-closed fallback.
        assert capabilities["runtime_write_journal_mode_effective"] == "delete"
        assert capabilities["runtime_write_pragma_status"] in {"fallback_delete", "error"}
        assert capabilities["runtime_write_wal_synchronous_effective"] == "default"
        assert capabilities["runtime_write_pragma_error"]


@pytest.mark.asyncio
async def test_runtime_write_wal_mode_is_forced_to_delete_when_wal_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RUNTIME_WRITE_WAL_ENABLED", "false")
    monkeypatch.setenv("RUNTIME_WRITE_JOURNAL_MODE", "wal")

    client = SQLiteClient(
        _sqlite_url(tmp_path / "runtime-write-wal-disabled-force-delete.db")
    )
    await client.init_db()
    status = await client.get_index_status()
    await client.close()

    capabilities = status["capabilities"]
    assert capabilities["runtime_write_wal_enabled"] is False
    assert capabilities["runtime_write_journal_mode_requested"] == "delete"
    assert capabilities["runtime_write_journal_mode_effective"] == "delete"
    assert capabilities["runtime_write_pragma_status"] == "disabled"
