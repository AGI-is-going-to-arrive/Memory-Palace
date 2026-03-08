import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

import main
import mcp_server
from api import browse as browse_api
from api import review as review_api
from db.sqlite_client import Memory, Path as PathModel, SQLiteClient


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _active_counts(client: SQLiteClient) -> tuple[int, int]:
    async with client.session() as session:
        memory_count_result = await session.execute(
            select(func.count()).select_from(Memory).where(Memory.deprecated == False)
        )
        path_count_result = await session.execute(
            select(func.count()).select_from(PathModel)
        )
        return (
            int(memory_count_result.scalar() or 0),
            int(path_count_result.scalar() or 0),
        )


async def _memory_access_snapshot(
    client: SQLiteClient, memory_id: int
) -> dict[str, object]:
    async with client.session() as session:
        memory = await session.get(Memory, memory_id)
        assert memory is not None
        return {
            "access_count": int(memory.access_count or 0),
            "vitality_score": float(memory.vitality_score or 0.0),
            "last_accessed_at": memory.last_accessed_at,
        }


def test_phase_d_hold_http_and_mcp_contracts() -> None:
    business_paths = [
        route.path
        for route in main.app.routes
        if route.path.startswith(("/browse", "/review", "/maintenance"))
    ]
    allowed_business_paths = {
        "/browse/node",
        "/review/deprecated",
        "/review/diff",
        "/review/memories/{memory_id}",
        "/review/sessions",
        "/review/sessions/{session_id}",
        "/review/sessions/{session_id}/diff/{resource_id:path}",
        "/review/sessions/{session_id}/rollback/{resource_id:path}",
        "/review/sessions/{session_id}/snapshots",
        "/review/sessions/{session_id}/snapshots/{resource_id:path}",
        "/maintenance/index/job/{job_id}",
        "/maintenance/index/job/{job_id}/cancel",
        "/maintenance/index/job/{job_id}/retry",
        "/maintenance/index/rebuild",
        "/maintenance/index/reindex/{memory_id}",
        "/maintenance/index/sleep-consolidation",
        "/maintenance/index/worker",
        "/maintenance/import/execute",
        "/maintenance/import/jobs/{job_id}",
        "/maintenance/import/jobs/{job_id}/rollback",
        "/maintenance/import/prepare",
        "/maintenance/learn/jobs/{job_id}",
        "/maintenance/learn/jobs/{job_id}/rollback",
        "/maintenance/learn/trigger",
        "/maintenance/observability/search",
        "/maintenance/observability/summary",
        "/maintenance/orphans",
        "/maintenance/orphans/{memory_id}",
        "/maintenance/vitality/candidates/query",
        "/maintenance/vitality/cleanup/confirm",
        "/maintenance/vitality/cleanup/prepare",
        "/maintenance/vitality/decay",
    }
    assert set(business_paths) == allowed_business_paths

    tools = mcp_server.mcp._tool_manager.list_tools()
    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "read_memory",
        "create_memory",
        "update_memory",
        "delete_memory",
        "add_alias",
        "search_memory",
        "compact_context",
        "rebuild_index",
        "index_status",
    }


def _load_env_pairs(file_path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key.strip()] = value.strip()
    return pairs


def test_phase_d_hold_default_flags_off_in_env_and_profiles() -> None:
    expected_defaults = {
        "EXTERNAL_IMPORT_ENABLED": "false",
        "EXTERNAL_IMPORT_ALLOWED_ROOTS": "",
        "EXTERNAL_IMPORT_ALLOWED_EXTS": ".md,.txt,.json",
        "EXTERNAL_IMPORT_MAX_TOTAL_BYTES": "5242880",
        "EXTERNAL_IMPORT_MAX_FILES": "200",
        "AUTO_LEARN_EXPLICIT_ENABLED": "false",
        "AUTO_LEARN_ALLOWED_DOMAINS": "notes",
        "AUTO_LEARN_REQUIRE_REASON": "true",
        "EMBEDDING_PROVIDER_CHAIN_ENABLED": "false",
        "EMBEDDING_PROVIDER_FAIL_OPEN": "false",
        "EMBEDDING_PROVIDER_FALLBACK": "hash",
        "RETRIEVAL_SQLITE_VEC_ENABLED": "false",
        "RETRIEVAL_SQLITE_VEC_EXTENSION_PATH": "",
        "RETRIEVAL_VECTOR_ENGINE": "legacy",
        "RETRIEVAL_SQLITE_VEC_READ_RATIO": "0",
        "RUNTIME_WRITE_WAL_ENABLED": "false",
        "RUNTIME_WRITE_JOURNAL_MODE": "delete",
        "RUNTIME_WRITE_WAL_SYNCHRONOUS": "normal",
        "RUNTIME_WRITE_BUSY_TIMEOUT_MS": "120",
        "RUNTIME_WRITE_WAL_AUTOCHECKPOINT": "1000",
    }

    project_root = Path(__file__).resolve().parents[2]
    expected_profile_files = [
        project_root / "deploy" / "profiles" / platform / f"profile-{label}.env"
        for platform in ("docker", "macos", "windows")
        for label in ("a", "b", "c", "d")
    ]
    profile_files = sorted(
        (project_root / "deploy" / "profiles").glob("*/profile-*.env")
    )
    expected_profiles_set = set(expected_profile_files)
    found_profiles_set = set(profile_files)
    missing_profiles = sorted(expected_profiles_set - found_profiles_set)
    unexpected_profiles = sorted(found_profiles_set - expected_profiles_set)
    assert not missing_profiles and not unexpected_profiles, (
        f"profile matrix mismatch; missing={missing_profiles}, "
        f"unexpected={unexpected_profiles}"
    )

    contract_files = [project_root / ".env.example", *expected_profile_files]

    assert contract_files, "expected env contracts to exist"
    for contract_file in contract_files:
        pairs = _load_env_pairs(contract_file)
        missing = [key for key in expected_defaults if key not in pairs]
        assert not missing, f"{contract_file} missing defaults: {missing}"
        for key, expected in expected_defaults.items():
            assert pairs[key] == expected, (
                f"{contract_file} expects {key}={expected}, got {pairs[key]!r}"
            )


def test_docker_env_file_is_parameterized_for_isolated_runs() -> None:
    project_root = Path(__file__).resolve().parents[2]

    compose_text = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${MEMORY_PALACE_DOCKER_ENV_FILE:-./.env.docker}" in compose_text
    assert "memory_palace_snapshots:/app/snapshots" in compose_text
    assert "${MEMORY_PALACE_SNAPSHOTS_VOLUME:-${NOCTURNE_SNAPSHOTS_VOLUME:-memory_palace_snapshots}}" in compose_text

    workspace_post_check = project_root.parent / "new" / "run_post_change_checks.sh"
    if workspace_post_check.exists():
        bash_smoke_text = workspace_post_check.read_text(encoding="utf-8")
        assert "MEMORY_PALACE_DOCKER_ENV_FILE" in bash_smoke_text

    bash_deploy_text = (project_root / "scripts" / "docker_one_click.sh").read_text(
        encoding="utf-8"
    )
    assert "MEMORY_PALACE_DOCKER_ENV_FILE" in bash_deploy_text
    assert "MEMORY_PALACE_SNAPSHOTS_VOLUME" in bash_deploy_text
    assert "resolve_snapshots_volume" in bash_deploy_text

    ps_deploy_text = (project_root / "scripts" / "docker_one_click.ps1").read_text(
        encoding="utf-8"
    )
    assert "MEMORY_PALACE_DOCKER_ENV_FILE" in ps_deploy_text
    assert "MEMORY_PALACE_SNAPSHOTS_VOLUME" in ps_deploy_text
    assert "Resolve-SnapshotsVolume" in ps_deploy_text


@pytest.mark.asyncio
async def test_phase_d_hold_search_and_read_do_not_create_new_memory_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "phase-d-hold-no-implicit-write.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    created = await client.create_memory(
        parent_path="",
        content="phase d hold boundary sample",
        priority=1,
        title="hold_boundary_node",
        domain="core",
    )
    uri = f"{created['domain']}://{created['path']}"

    before_counts = await _active_counts(client)
    before_access = await _memory_access_snapshot(client, int(created["id"]))

    flush_tracker = mcp_server.runtime_state.flush_tracker
    original_trigger_chars = flush_tracker._trigger_chars
    original_min_events = flush_tracker._min_events
    original_max_events = flush_tracker._max_events
    session_id = "phase_d_hold_no_implicit_write"

    monkeypatch.setattr(mcp_server, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(mcp_server, "get_session_id", lambda: session_id)
    monkeypatch.setattr(mcp_server, "AUTO_FLUSH_ENABLED", True)

    flush_tracker._trigger_chars = 1
    flush_tracker._min_events = 1
    flush_tracker._max_events = max(10, original_max_events)

    try:
        await flush_tracker.mark_flushed(session_id=session_id)

        search_raw = await mcp_server.search_memory(
            query="phase d hold boundary sample",
            mode="hybrid",
            include_session=False,
        )
        search_payload = json.loads(search_raw)
        assert search_payload["ok"] is True
        assert search_payload["count"] >= 1
        assert any(
            int(item.get("memory_id") or -1) == int(created["id"])
            for item in search_payload.get("results", [])
        )

        after_search_access = await _memory_access_snapshot(client, int(created["id"]))
        assert after_search_access["access_count"] >= (
            int(before_access["access_count"]) + 1
        )
        assert float(after_search_access["vitality_score"]) >= float(
            before_access["vitality_score"]
        )
        if before_access["last_accessed_at"] is None:
            assert after_search_access["last_accessed_at"] is not None
        else:
            assert after_search_access["last_accessed_at"] >= before_access["last_accessed_at"]

        read_raw = await mcp_server.read_memory(uri)
        assert "hold_boundary_node" in read_raw

        after_read_access = await _memory_access_snapshot(client, int(created["id"]))
        assert after_read_access["access_count"] == int(
            after_search_access["access_count"]
        ) + 1
        assert float(after_read_access["vitality_score"]) >= float(
            after_search_access["vitality_score"]
        )
        assert after_read_access["last_accessed_at"] is not None
        assert after_read_access["last_accessed_at"] >= after_search_access["last_accessed_at"]

        after_counts = await _active_counts(client)
        assert after_counts == before_counts
    finally:
        flush_tracker._trigger_chars = original_trigger_chars
        flush_tracker._min_events = original_min_events
        flush_tracker._max_events = original_max_events
        await flush_tracker.mark_flushed(session_id=session_id)
        await client.close()


@pytest.mark.asyncio
async def test_phase_d_hold_browse_blocked_update_has_no_access_side_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "phase-d-hold-browse-no-side-effect.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    created = await client.create_memory(
        parent_path="",
        content="phase d hold browse side effect sample",
        priority=1,
        title="hold_browse_node",
        domain="core",
    )

    before_access = await _memory_access_snapshot(client, int(created["id"]))

    async def _guard_noop(**_: object) -> dict[str, object]:
        return {"action": "NOOP", "reason": "duplicate", "method": "keyword"}

    monkeypatch.setattr(browse_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(client, "write_guard", _guard_noop)

    payload = await browse_api.update_node(
        path=str(created["path"]),
        domain="core",
        body=browse_api.NodeUpdate(content="replacement content"),
    )
    assert payload["success"] is False
    assert payload["updated"] is False
    assert str(payload.get("guard_action")) == "NOOP"

    after_access = await _memory_access_snapshot(client, int(created["id"]))
    assert after_access["access_count"] == before_access["access_count"]
    assert after_access["vitality_score"] == before_access["vitality_score"]
    assert after_access["last_accessed_at"] == before_access["last_accessed_at"]
    await client.close()


@pytest.mark.asyncio
async def test_phase_d_hold_review_no_change_rollback_has_no_access_side_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "phase-d-hold-review-no-side-effect.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    created = await client.create_memory(
        parent_path="",
        content="phase d hold review side effect sample",
        priority=3,
        title="hold_review_node",
        domain="core",
    )

    before_access = await _memory_access_snapshot(client, int(created["id"]))
    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: client)

    rollback_payload = await review_api._rollback_legacy_modify(
        {
            "memory_id": created["id"],
            "path": created["path"],
            "domain": created["domain"],
            "uri": f"{created['domain']}://{created['path']}",
            "priority": 3,
            "disclosure": None,
        }
    )
    assert rollback_payload.get("no_change") is True

    after_access = await _memory_access_snapshot(client, int(created["id"]))
    assert after_access["access_count"] == before_access["access_count"]
    assert after_access["vitality_score"] == before_access["vitality_score"]
    assert after_access["last_accessed_at"] == before_access["last_accessed_at"]
    await client.close()


@pytest.mark.asyncio
async def test_phase_d_hold_review_modify_meta_rollback_has_no_access_side_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "phase-d-hold-review-modify-meta-no-side-effect.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    created = await client.create_memory(
        parent_path="",
        content="phase d hold review modify_meta side effect sample",
        priority=2,
        title="hold_review_modify_meta_node",
        domain="core",
    )

    before_access = await _memory_access_snapshot(client, int(created["id"]))
    monkeypatch.setattr(review_api, "get_sqlite_client", lambda: client)

    rollback_payload = await review_api._rollback_path(
        {
            "operation_type": "modify_meta",
            "path": created["path"],
            "domain": created["domain"],
            "uri": f"{created['domain']}://{created['path']}",
            "priority": 7,
            "disclosure": "internal",
        }
    )
    assert rollback_payload.get("metadata_restored") is True

    after_access = await _memory_access_snapshot(client, int(created["id"]))
    assert after_access["access_count"] == before_access["access_count"]
    assert after_access["vitality_score"] == before_access["vitality_score"]
    assert after_access["last_accessed_at"] == before_access["last_accessed_at"]

    updated = await client.get_memory_by_path(
        str(created["path"]), str(created["domain"]), reinforce_access=False
    )
    assert updated is not None
    assert int(updated["priority"]) == 7
    assert str(updated.get("disclosure") or "") == "internal"
    await client.close()
