import inspect
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager


def _load_project_dotenv(project_root: Optional[Path] = None) -> Optional[Path]:
    root = project_root or Path(__file__).resolve().parents[1]
    dotenv_path = root / ".env"
    if not dotenv_path.exists():
        return None
    load_dotenv(dotenv_path, override=False)
    return dotenv_path


_load_project_dotenv()

from api import review_router, browse_router, maintenance_router, setup_router
from db import get_sqlite_client, close_sqlite_client
from runtime_state import runtime_state


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


_DEFAULT_CORS_ALLOW_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)
_LEGACY_REQUIRED_TABLE_NAMES: tuple[str, ...] = ("memories",)


def _resolve_cors_config() -> tuple[list[str], bool]:
    raw_origins = str(os.getenv("CORS_ALLOW_ORIGINS", "") or "")
    origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    if not origins:
        origins = list(_DEFAULT_CORS_ALLOW_ORIGINS)

    allow_credentials = _env_bool("CORS_ALLOW_CREDENTIALS", True)
    if "*" in origins and allow_credentials:
        # Browsers reject '*' + credentials. Fall back to credential-less CORS.
        allow_credentials = False
    return origins, allow_credentials


def _extract_sqlite_file_path(database_url: Optional[str]) -> Optional[Path]:
    """Extract local file path from sqlite+aiosqlite URL."""
    if not database_url:
        return None
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw_path = database_url[len(prefix):]
    raw_path = raw_path.split("?", 1)[0].split("#", 1)[0]
    raw_path = unquote(raw_path)
    if not raw_path:
        return None
    if raw_path == ":memory:" or raw_path.startswith("file::memory:"):
        return None
    if raw_path.startswith("/") or (
        len(raw_path) >= 3 and raw_path[1] == ":" and raw_path[2] == "/"
    ):
        return Path(raw_path)
    return Path(raw_path)


def _is_regular_file_no_symlink(path: Path) -> bool:
    try:
        file_mode = path.stat(follow_symlinks=False).st_mode
    except OSError:
        return False
    return stat.S_ISREG(file_mode)


def _sqlite_quick_check_ok(conn: sqlite3.Connection) -> bool:
    try:
        rows = conn.execute("PRAGMA quick_check(1)").fetchall()
    except sqlite3.Error:
        return False
    if len(rows) != 1 or not rows[0]:
        return False
    return str(rows[0][0]).strip().lower() == "ok"


def _sqlite_has_required_legacy_tables(conn: sqlite3.Connection) -> bool:
    placeholders = ",".join("?" for _ in _LEGACY_REQUIRED_TABLE_NAMES)
    if not placeholders:
        return True
    try:
        rows = conn.execute(
            f"""
            SELECT name
            FROM sqlite_master
            WHERE type='table'
              AND name IN ({placeholders})
            LIMIT 1
            """,
            tuple(_LEGACY_REQUIRED_TABLE_NAMES),
        ).fetchall()
    except sqlite3.Error:
        return False
    return bool(rows)


def _try_restore_legacy_sqlite_file(database_url: Optional[str]) -> None:
    """
    Compatibility helper:
    if the new DB file does not exist but a legacy filename exists in the same
    directory, copy it to the new path so upgrades keep old data.
    """
    target_path = _extract_sqlite_file_path(database_url)
    if not target_path or target_path.exists():
        return
    target_dir = target_path.parent
    if not target_dir.exists():
        return

    legacy_candidates = (
        "agent_memory.db",
        "nocturne_memory.db",
        "nocturne.db",
    )
    for legacy_name in legacy_candidates:
        legacy_path = target_dir / legacy_name
        if not legacy_path.exists():
            continue

        if not _is_regular_file_no_symlink(legacy_path):
            print(
                f"[compat] Skipped legacy database file {legacy_path}: "
                "not a regular file"
            )
            continue

        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True) as source_conn:
                if not _sqlite_quick_check_ok(source_conn):
                    print(
                        f"[compat] Skipped legacy database file {legacy_path}: "
                        "sqlite quick_check failed"
                    )
                    continue
                if not _sqlite_has_required_legacy_tables(source_conn):
                    print(
                        f"[compat] Skipped legacy database file {legacy_path}: "
                        "missing expected legacy tables"
                    )
                    continue
                with sqlite3.connect(target_path) as target_conn:
                    source_conn.backup(target_conn)
        except sqlite3.Error as exc:
            print(
                f"[compat] Skipped legacy database file {legacy_path}: "
                f"sqlite error: {exc}"
            )
            if target_path.exists():
                try:
                    target_path.unlink()
                except OSError:
                    pass
            continue

        print(
            f"[compat] Restored legacy database file from {legacy_path} "
            f"to {target_path}"
        )
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    print("Memory API starting...")
    
    # Initialize SQLite
    try:
        _try_restore_legacy_sqlite_file(os.getenv("DATABASE_URL"))
        sqlite_client = get_sqlite_client()
        await sqlite_client.init_db()
        await runtime_state.ensure_started(get_sqlite_client)
        print("SQLite database initialized.")
    except Exception as e:
        print(f"Failed to initialize SQLite: {e}")
        raise RuntimeError("Failed to initialize SQLite during startup") from e
    
    yield
    
    # 关闭时
    print("Closing database connections...")
    await runtime_state.shutdown()
    await close_sqlite_client()


app = FastAPI(
    title="Memory Palace API",
    description="AI Agent 长期记忆系统后端",
    version="1.0.1",
    lifespan=lifespan
)

# CORS设置
_cors_origins, _cors_allow_credentials = _resolve_cors_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(review_router)
app.include_router(browse_router)
app.include_router(maintenance_router)
app.include_router(setup_router)


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "Memory Palace API",
        "version": "1.0.1",
        "docs": "/docs"
    }


@app.get("/health")
async def health():
    """健康检查"""
    payload: Dict[str, Any] = {
        "status": "ok",
        "timestamp": _utc_iso_now(),
    }

    try:
        sqlite_client = get_sqlite_client()
        index_payload: Optional[Dict[str, Any]] = None

        for method_name in (
            "get_index_status",
            "index_status",
            "get_retrieval_status",
            "get_search_index_status",
        ):
            method = getattr(sqlite_client, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    result = await result
            except TypeError as exc:
                message = str(exc)
                if (
                    "unexpected keyword argument" in message
                    or "required positional argument" in message
                ):
                    continue
                raise

            index_payload = result if isinstance(result, dict) else {"raw_status": result}
            index_payload.setdefault("index_available", True)
            index_payload.setdefault("degraded", False)
            index_payload["source"] = f"sqlite_client.{method_name}"
            break

        if index_payload is None:
            paths = await sqlite_client.get_all_paths()
            domain_counts: Dict[str, int] = {}
            for item in paths:
                domain = item.get("domain", "core")
                domain_counts[domain] = domain_counts.get(domain, 0) + 1
            index_payload = {
                "index_available": False,
                "degraded": True,
                "reason": "sqlite_client index status API unavailable; fallback stats only.",
                "source": "api.health.fallback",
                "stats": {
                    "total_paths": len(paths),
                    "domain_counts": domain_counts,
                },
            }

        payload["index"] = index_payload
        payload["runtime"] = {
            "write_lanes": await runtime_state.write_lanes.status(),
            "index_worker": await runtime_state.index_worker.status(),
        }
        if index_payload.get("degraded"):
            payload["status"] = "degraded"

    except Exception as exc:
        error_type = type(exc).__name__
        payload["status"] = "degraded"
        payload["index"] = {
            "index_available": False,
            "degraded": True,
            "reason": "internal_error",
            "error_type": error_type,
            "source": "api.health.exception",
        }
        payload["runtime"] = {
            "write_lanes": {"degraded": True, "reason": "internal_error"},
            "index_worker": {"degraded": True, "reason": "internal_error"},
        }

    return payload


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
