"""
SQLite migration runner for Memory Palace.

Migrations are SQL files under backend/db/migrations with names like:
    0001_description.sql

Applied versions are tracked in `schema_migrations`.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union
from urllib.parse import unquote
from filelock import FileLock, Timeout


_SQLITE_FILE_PREFIXES = ("sqlite+aiosqlite:///", "sqlite:///")
_MIGRATION_FILE_PATTERN = re.compile(r"^(?P<version>\d{4,})_.*\.sql$")
_ROLLBACK_SUFFIX = ".rollback.sql"
_ADD_COLUMN_PATTERN = re.compile(
    r"^ALTER\s+TABLE\s+.+\s+ADD\s+COLUMN\s+.+$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class MigrationFile:
    """A normalized migration file descriptor."""

    version: str
    path: Path
    checksum: str


def _extract_sqlite_file_path(database_url: str) -> Optional[Path]:
    """
    Extract a local file path from a sqlite SQLAlchemy URL.

    Supports:
    - sqlite+aiosqlite:///absolute/path.db
    - sqlite:///absolute/path.db
    """
    for prefix in _SQLITE_FILE_PREFIXES:
        if not database_url.startswith(prefix):
            continue
        raw_path = database_url[len(prefix) :]
        raw_path = raw_path.split("?", 1)[0].split("#", 1)[0]
        raw_path = unquote(raw_path)
        if not raw_path or raw_path == ":memory:":
            return None
        return Path(raw_path)
    raise ValueError(
        "Unsupported DATABASE_URL for migration runner. "
        "Expected sqlite+aiosqlite:///... or sqlite:///..."
    )


class MigrationRunner:
    """Discover and apply SQL migrations with version tracking."""

    def __init__(
        self,
        database_url: str,
        migrations_dir: Optional[Path] = None,
        lock_file_path: Optional[Path] = None,
        lock_timeout_seconds: float = 10.0,
    ) -> None:
        self.database_url = database_url
        self.database_file = _extract_sqlite_file_path(database_url)
        self.migrations_dir = (
            Path(migrations_dir)
            if migrations_dir is not None
            else Path(__file__).resolve().parent / "migrations"
        )
        default_lock_file: Optional[Path]
        if self.database_file is not None:
            default_lock_file = (
                self.database_file.with_suffix(
                    self.database_file.suffix + ".migrate.lock"
                )
                if self.database_file.suffix
                else Path(f"{self.database_file}.migrate.lock")
            )
        else:
            default_lock_file = None
        configured_env_lock_raw = os.getenv("DB_MIGRATION_LOCK_FILE", "").strip()
        configured_env_lock = self._normalize_lock_path(configured_env_lock_raw)
        explicit_lock_path = self._normalize_lock_path(lock_file_path)
        self.lock_file_path = (
            explicit_lock_path
            if explicit_lock_path is not None
            else configured_env_lock
            if configured_env_lock is not None
            else default_lock_file
        )
        env_timeout = os.getenv("DB_MIGRATION_LOCK_TIMEOUT_SEC")
        if env_timeout is not None:
            try:
                lock_timeout_seconds = float(env_timeout)
            except ValueError:
                pass
        self.lock_timeout_seconds = max(0.0, lock_timeout_seconds)

    def _normalize_lock_path(
        self, raw_path: Optional[Union[Path, str]]
    ) -> Optional[Path]:
        if raw_path is None:
            return None
        text_value = str(raw_path).strip()
        if not text_value:
            return None
        candidate = Path(text_value).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        if self.database_file is not None:
            return (self.database_file.parent / candidate).resolve()
        return candidate.resolve()

    async def apply_pending(self) -> List[str]:
        """Apply all pending migrations and return applied versions."""
        return await asyncio.to_thread(self._apply_pending_sync)

    def _apply_pending_sync(self) -> List[str]:
        migration_files = self._discover_migrations()
        if not migration_files:
            return []
        if self.database_file is None:
            # In-memory DBs are already created from current metadata every boot.
            return []

        if self.lock_file_path is not None:
            self.lock_file_path.parent.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(self.lock_file_path), timeout=self.lock_timeout_seconds)
            try:
                with lock:
                    return self._apply_pending_unlocked(migration_files)
            except Timeout as exc:
                raise RuntimeError(
                    "Timed out waiting for migration lock: "
                    f"{self.lock_file_path} ({self.lock_timeout_seconds}s)"
                ) from exc
        return self._apply_pending_unlocked(migration_files)

    def _apply_pending_unlocked(self, migration_files: List[MigrationFile]) -> List[str]:
        if self.database_file is None:
            return []
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_file) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            self._ensure_schema_table(conn)

            applied_map = self._load_applied_checksums(conn)
            applied_versions: List[str] = []

            for migration in migration_files:
                recorded_checksum = applied_map.get(migration.version)
                if recorded_checksum is not None:
                    if recorded_checksum != migration.checksum:
                        raise RuntimeError(
                            "Checksum mismatch for migration "
                            f"{migration.version}: recorded={recorded_checksum} "
                            f"current={migration.checksum}"
                        )
                    continue

                self._execute_sql_script(conn, migration.path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at, checksum) "
                    "VALUES (?, ?, ?)",
                    (
                        migration.version,
                        datetime.now(timezone.utc).isoformat(),
                        migration.checksum,
                    ),
                )
                conn.commit()
                applied_versions.append(migration.version)

            return applied_versions

    def _discover_migrations(self) -> List[MigrationFile]:
        if not self.migrations_dir.exists():
            return []

        discovered: List[MigrationFile] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            if path.name.endswith(_ROLLBACK_SUFFIX):
                continue
            match = _MIGRATION_FILE_PATTERN.match(path.name)
            if not match:
                continue

            content = path.read_bytes()
            checksum = self._normalized_checksum(content)
            discovered.append(
                MigrationFile(
                    version=match.group("version"),
                    path=path,
                    checksum=checksum,
                )
            )
        return discovered

    @staticmethod
    def _normalized_checksum(content: bytes) -> str:
        """
        Cross-platform checksum:
        normalize line endings so CRLF/LF checkout differences do not break boot.
        """
        try:
            normalized = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            payload = normalized.encode("utf-8")
        except UnicodeDecodeError:
            payload = content
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _ensure_schema_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL
            )
            """
        )
        conn.commit()

    @staticmethod
    def _load_applied_checksums(conn: sqlite3.Connection) -> Dict[str, str]:
        cursor = conn.execute("SELECT version, checksum FROM schema_migrations")
        return {str(row["version"]): str(row["checksum"]) for row in cursor.fetchall()}

    @staticmethod
    def _execute_sql_script(conn: sqlite3.Connection, script: str) -> None:
        for statement in MigrationRunner._iter_sql_statements(script):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if MigrationRunner._is_ignorable_add_column_error(statement, exc):
                    continue
                raise

    @staticmethod
    def _iter_sql_statements(script: str) -> List[str]:
        statements: List[str] = []
        buffer: List[str] = []
        in_single_quote = False
        in_double_quote = False
        previous = ""

        for char in script:
            if char == "'" and not in_double_quote and previous != "\\":
                in_single_quote = not in_single_quote
            elif char == '"' and not in_single_quote and previous != "\\":
                in_double_quote = not in_double_quote

            if char == ";" and not in_single_quote and not in_double_quote:
                candidate = "".join(buffer).strip()
                if candidate and not MigrationRunner._is_comment_only(candidate):
                    statements.append(candidate)
                buffer = []
            else:
                buffer.append(char)
            previous = char

        tail = "".join(buffer).strip()
        if tail and not MigrationRunner._is_comment_only(tail):
            statements.append(tail)
        return statements

    @staticmethod
    def _is_comment_only(statement: str) -> bool:
        lines = [line.strip() for line in statement.splitlines() if line.strip()]
        if not lines:
            return True
        return all(line.startswith("--") for line in lines)

    @staticmethod
    def _is_ignorable_add_column_error(
        statement: str, exc: sqlite3.OperationalError
    ) -> bool:
        if not _ADD_COLUMN_PATTERN.match(statement):
            return False
        return "duplicate column name" in str(exc).lower()


async def apply_pending_migrations(
    database_url: str, migrations_dir: Optional[Path] = None
) -> List[str]:
    """Convenience wrapper used by SQLite client startup."""
    runner = MigrationRunner(database_url=database_url, migrations_dir=migrations_dir)
    return await runner.apply_pending()
