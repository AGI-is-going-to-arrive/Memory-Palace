import inspect
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from api import review_router, browse_router, maintenance_router
from db import get_sqlite_client, close_sqlite_client
from runtime_state import runtime_state


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_sqlite_file_path(database_url: Optional[str]) -> Optional[Path]:
    """Extract local file path from sqlite+aiosqlite URL."""
    if not database_url:
        return None
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw_path = database_url[len(prefix):]
    if not raw_path:
        return None
    if raw_path.startswith("/") or (
        len(raw_path) >= 3 and raw_path[1] == ":" and raw_path[2] == "/"
    ):
        return Path(raw_path)
    return Path(raw_path)


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
        target_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True) as source_conn:
            with sqlite3.connect(target_path) as target_conn:
                source_conn.backup(target_conn)
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境，生产环境需要限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(review_router)
app.include_router(browse_router)
app.include_router(maintenance_router)


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

    except Exception as e:
        payload["status"] = "degraded"
        payload["index"] = {
            "index_available": False,
            "degraded": True,
            "reason": str(e),
            "source": "api.health.exception",
        }
        payload["runtime"] = {
            "write_lanes": {"degraded": True, "reason": str(e)},
            "index_worker": {"degraded": True, "reason": str(e)},
        }

    return payload


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
