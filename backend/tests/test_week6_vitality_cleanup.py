from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import select

from api import maintenance as maintenance_api
from db import sqlite_client as sqlite_client_module
from db.sqlite_client import Memory, SQLiteClient
from runtime_state import CleanupReviewCoordinator, VitalityDecayCoordinator


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


@pytest.mark.asyncio
async def test_search_advanced_reinforces_memory_access(tmp_path: Path) -> None:
    db_path = tmp_path / "week6-reinforce.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Alpha payload for vitality reinforcement",
        priority=1,
        title="alpha",
        domain="core",
    )
    payload = await client.search_advanced(
        query="alpha payload",
        mode="keyword",
        max_results=3,
        candidate_multiplier=2,
    )

    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        assert int(memory.access_count or 0) >= 1
        assert memory.last_accessed_at is not None
        assert float(memory.vitality_score or 0.0) > 1.0

    await client.close()
    assert payload["results"]


@pytest.mark.asyncio
async def test_apply_vitality_decay_is_daily_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "week6-decay.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Decay target",
        priority=1,
        title="decay",
        domain="core",
    )
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 1.6
        memory.access_count = 0
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        )
        session.add(memory)

    first = await client.apply_vitality_decay(force=False, reason="test")
    second = await client.apply_vitality_decay(force=False, reason="test")
    third = await client.apply_vitality_decay(force=True, reason="test.force")

    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        assert float(memory.vitality_score or 0.0) < 1.6

    await client.close()
    assert first["applied"] is True
    assert second["applied"] is False
    assert second["reason"] == "already_applied_today"
    assert third["applied"] is True


@pytest.mark.asyncio
async def test_get_vitality_cleanup_candidates_returns_orphan_candidate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-candidates.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Orphan candidate content",
        priority=1,
        title="candidate",
        domain="core",
    )
    await client.remove_path(path="candidate", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.12
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=45)
        )
        memory.access_count = 0
        session.add(memory)

    payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=20,
    )
    await client.close()

    items = payload["items"]
    assert len(items) == 1
    item = items[0]
    assert item["memory_id"] == created["id"]
    assert item["can_delete"] is True
    assert "orphaned" in item["reason_codes"]
    assert len(item["state_hash"]) == 64


@pytest.mark.asyncio
async def test_get_vitality_cleanup_candidates_respects_domain_and_path_prefix(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-candidates-domain-prefix.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    await client.create_memory(
        parent_path="",
        content="notes scope root",
        priority=1,
        title="scope",
        domain="notes",
    )
    await client.create_memory(
        parent_path="",
        content="notes other root",
        priority=1,
        title="other",
        domain="notes",
    )
    await client.create_memory(
        parent_path="",
        content="core scope root",
        priority=1,
        title="scope",
        domain="core",
    )

    keep = await client.create_memory(
        parent_path="scope",
        content="Scoped notes candidate",
        priority=1,
        title="keep_me",
        domain="notes",
    )
    skip_prefix = await client.create_memory(
        parent_path="other",
        content="Notes but out of prefix",
        priority=1,
        title="skip_prefix",
        domain="notes",
    )
    skip_domain = await client.create_memory(
        parent_path="scope",
        content="Core domain candidate",
        priority=1,
        title="skip_domain",
        domain="core",
    )

    async with client.session() as session:
        for memory_id in (keep["id"], skip_prefix["id"], skip_domain["id"]):
            memory = await session.get(Memory, memory_id)
            assert memory is not None
            memory.vitality_score = 0.1
            memory.last_accessed_at = (
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=45)
            )
            memory.access_count = 0
            session.add(memory)

    payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=20,
        domain="notes",
        path_prefix="scope/",
    )
    await client.close()

    items = payload["items"]
    assert len(items) == 1
    assert items[0]["memory_id"] == keep["id"]
    assert items[0]["uri"] == "notes://scope/keep_me"


@pytest.mark.asyncio
async def test_get_vitality_cleanup_candidates_uses_sql_scope_for_path_loading(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-candidates-sql-scope.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    await client.create_memory(
        parent_path="",
        content="scope root",
        priority=1,
        title="scope",
        domain="core",
    )
    await client.create_memory(
        parent_path="",
        content="other root",
        priority=1,
        title="other",
        domain="core",
    )

    candidate = await client.create_memory(
        parent_path="scope",
        content="Scoped cleanup candidate",
        priority=1,
        title="candidate",
        domain="core",
    )

    for idx in range(80):
        await client.add_path(
            new_path=f"other/alias_{idx}",
            target_path="scope/candidate",
            new_domain="core",
            target_domain="core",
        )

    async with client.session() as session:
        memory = await session.get(Memory, candidate["id"])
        assert memory is not None
        memory.vitality_score = 0.06
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)
        )
        memory.access_count = 0
        session.add(memory)

    payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
        domain="core",
        path_prefix="scope/",
    )
    await client.close()

    items = payload["items"]
    assert len(items) == 1
    assert items[0]["memory_id"] == candidate["id"]
    assert items[0]["uri"] == "core://scope/candidate"
    assert items[0]["path_count"] == 81

    query_profile = payload["summary"].get("query_profile") or {}
    assert int(query_profile.get("path_rows_loaded") or 0) <= 3


@pytest.mark.asyncio
async def test_get_vitality_cleanup_candidates_limit_keeps_week6_sort_order(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-candidates-limit-order.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    oldest = await client.create_memory(
        parent_path="",
        content="Oldest low vitality",
        priority=1,
        title="oldest",
        domain="core",
    )
    newer = await client.create_memory(
        parent_path="",
        content="Newer low vitality",
        priority=1,
        title="newer",
        domain="core",
    )
    higher_score = await client.create_memory(
        parent_path="",
        content="Higher vitality score",
        priority=1,
        title="higher_score",
        domain="core",
    )

    async with client.session() as session:
        oldest_row = await session.get(Memory, oldest["id"])
        newer_row = await session.get(Memory, newer["id"])
        higher_score_row = await session.get(Memory, higher_score["id"])
        assert oldest_row is not None
        assert newer_row is not None
        assert higher_score_row is not None

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        oldest_row.vitality_score = 0.05
        oldest_row.last_accessed_at = now_naive - timedelta(days=120)
        newer_row.vitality_score = 0.05
        newer_row.last_accessed_at = now_naive - timedelta(days=30)
        higher_score_row.vitality_score = 0.08
        higher_score_row.last_accessed_at = now_naive - timedelta(days=90)

        oldest_row.access_count = 0
        newer_row.access_count = 0
        higher_score_row.access_count = 0
        session.add(oldest_row)
        session.add(newer_row)
        session.add(higher_score_row)

    payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=2,
    )
    await client.close()

    items = payload["items"]
    assert [item["memory_id"] for item in items] == [oldest["id"], newer["id"]]
    assert payload["summary"]["total_candidates"] == 2
    query_profile = payload["summary"].get("query_profile") or {}
    index_usage = query_profile.get("index_usage") or {}
    assert float(query_profile.get("query_ms") or 0.0) >= 0.0
    assert "memory_cleanup_index" in index_usage
    assert "path_scope_index" in index_usage


@pytest.mark.asyncio
async def test_vitality_cleanup_state_hash_stays_stable_when_only_time_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-state-hash-stable.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Stable hash candidate",
        priority=1,
        title="stable_hash",
        domain="core",
    )
    await client.remove_path(path="stable_hash", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.12
        memory.last_accessed_at = datetime(2026, 1, 1, 0, 0, 0)
        memory.access_count = 2
        session.add(memory)

    first_now = datetime(2026, 2, 1, 0, 0, 0)
    second_now = first_now + timedelta(minutes=30)

    monkeypatch.setattr(sqlite_client_module, "_utc_now_naive", lambda: first_now)
    first_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=20,
    )

    monkeypatch.setattr(sqlite_client_module, "_utc_now_naive", lambda: second_now)
    second_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=20,
    )

    await client.close()

    first_item = first_payload["items"][0]
    second_item = second_payload["items"][0]
    assert second_item["inactive_days"] > first_item["inactive_days"]
    assert first_item["state_hash"] == second_item["state_hash"]


@pytest.mark.asyncio
async def test_vitality_cleanup_prepare_and_confirm_delete_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-flow.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Cleanup confirm flow",
        priority=1,
        title="cleanup_note",
        domain="core",
    )
    await client.remove_path(path="cleanup_note", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.08
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)
        )
        memory.access_count = 0
        session.add(memory)

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state, "cleanup_reviews", CleanupReviewCoordinator()
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state, "vitality_decay", VitalityDecayCoordinator()
    )

    query_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
    )
    item = query_payload["items"][0]

    prepare_req = maintenance_api.VitalityCleanupPrepareRequest(
        action="delete",
        selections=[
            maintenance_api.CleanupSelectionItem(
                memory_id=item["memory_id"],
                state_hash=item["state_hash"],
            )
        ],
        reviewer="test-user",
    )
    prepare_result = await maintenance_api.prepare_vitality_cleanup(prepare_req)
    review = prepare_result["review"]

    confirm_req = maintenance_api.VitalityCleanupConfirmRequest(
        review_id=review["review_id"],
        token=review["token"],
        confirmation_phrase=review["confirmation_phrase"],
    )
    confirm_result = await maintenance_api.confirm_vitality_cleanup(confirm_req)

    async with client.session() as session:
        still_exists = await session.execute(
            select(Memory.id).where(Memory.id == created["id"])
        )
        assert still_exists.scalar_one_or_none() is None

    await client.close()
    assert prepare_result["status"] == "pending_confirmation"
    assert confirm_result["ok"] is True
    assert confirm_result["deleted_count"] == 1


@pytest.mark.asyncio
async def test_vitality_cleanup_prepare_and_confirm_keep_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-keep-flow.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Cleanup keep flow",
        priority=1,
        title="keep_note",
        domain="core",
    )
    await client.remove_path(path="keep_note", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.06
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)
        )
        memory.access_count = 1
        session.add(memory)

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state, "cleanup_reviews", CleanupReviewCoordinator()
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state, "vitality_decay", VitalityDecayCoordinator()
    )

    query_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
    )
    item = query_payload["items"][0]

    prepare_req = maintenance_api.VitalityCleanupPrepareRequest(
        action="keep",
        selections=[
            maintenance_api.CleanupSelectionItem(
                memory_id=item["memory_id"],
                state_hash=item["state_hash"],
            )
        ],
        reviewer="test-user",
    )
    prepare_result = await maintenance_api.prepare_vitality_cleanup(prepare_req)
    review = prepare_result["review"]

    confirm_req = maintenance_api.VitalityCleanupConfirmRequest(
        review_id=review["review_id"],
        token=review["token"],
        confirmation_phrase=review["confirmation_phrase"],
    )
    confirm_result = await maintenance_api.confirm_vitality_cleanup(confirm_req)

    async with client.session() as session:
        still_exists = await session.execute(
            select(Memory.id).where(Memory.id == created["id"])
        )
        assert still_exists.scalar_one_or_none() == created["id"]

    await client.close()
    assert prepare_result["status"] == "pending_confirmation"
    assert confirm_result["ok"] is True
    assert confirm_result["kept_count"] == 1
    assert confirm_result["deleted_count"] == 0


@pytest.mark.asyncio
async def test_vitality_cleanup_confirm_skips_active_path_memory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-active-path.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Active path memory",
        priority=1,
        title="active_path_note",
        domain="core",
    )
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.05
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=60)
        )
        memory.access_count = 0
        session.add(memory)

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state, "cleanup_reviews", CleanupReviewCoordinator()
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state, "vitality_decay", VitalityDecayCoordinator()
    )

    query_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
    )
    item = query_payload["items"][0]
    assert item["can_delete"] is False

    prepare_req = maintenance_api.VitalityCleanupPrepareRequest(
        action="delete",
        selections=[
            maintenance_api.CleanupSelectionItem(
                memory_id=item["memory_id"],
                state_hash=item["state_hash"],
            )
        ],
        reviewer="test-user",
    )
    prepare_result = await maintenance_api.prepare_vitality_cleanup(prepare_req)
    review = prepare_result["review"]

    confirm_req = maintenance_api.VitalityCleanupConfirmRequest(
        review_id=review["review_id"],
        token=review["token"],
        confirmation_phrase=review["confirmation_phrase"],
    )
    confirm_result = await maintenance_api.confirm_vitality_cleanup(confirm_req)

    async with client.session() as session:
        still_exists = await session.execute(
            select(Memory.id).where(Memory.id == created["id"])
        )
        assert still_exists.scalar_one_or_none() == created["id"]

    await client.close()
    assert confirm_result["deleted_count"] == 0
    assert confirm_result["skipped_count"] == 1
    assert confirm_result["skipped"][0]["reason"] == "active_paths"


@pytest.mark.asyncio
async def test_vitality_cleanup_confirm_detects_stale_state_after_prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-stale-confirm.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Stale confirm target",
        priority=1,
        title="stale_confirm_note",
        domain="core",
    )
    await client.remove_path(path="stale_confirm_note", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.07
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=75)
        )
        memory.access_count = 0
        session.add(memory)

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state, "cleanup_reviews", CleanupReviewCoordinator()
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state, "vitality_decay", VitalityDecayCoordinator()
    )

    query_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
    )
    item = query_payload["items"][0]

    prepare_result = await maintenance_api.prepare_vitality_cleanup(
        maintenance_api.VitalityCleanupPrepareRequest(
            action="delete",
            selections=[
                maintenance_api.CleanupSelectionItem(
                    memory_id=item["memory_id"],
                    state_hash=item["state_hash"],
                )
            ],
            reviewer="test-user",
        )
    )
    review = prepare_result["review"]

    # Simulate candidate state mutation between prepare and confirm.
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.access_count = int(memory.access_count or 0) + 1
        session.add(memory)

    confirm_result = await maintenance_api.confirm_vitality_cleanup(
        maintenance_api.VitalityCleanupConfirmRequest(
            review_id=review["review_id"],
            token=review["token"],
            confirmation_phrase=review["confirmation_phrase"],
        )
    )

    async with client.session() as session:
        still_exists = await session.execute(
            select(Memory.id).where(Memory.id == created["id"])
        )
        assert still_exists.scalar_one_or_none() == created["id"]

    await client.close()
    assert confirm_result["deleted_count"] == 0
    assert confirm_result["skipped_count"] == 1
    assert confirm_result["skipped"][0]["reason"] == "stale_state"


@pytest.mark.asyncio
async def test_vitality_cleanup_keep_skips_when_state_changes_after_prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-keep-stale-confirm.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Stale keep target",
        priority=1,
        title="stale_keep_note",
        domain="core",
    )
    await client.remove_path(path="stale_keep_note", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.09
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=80)
        )
        memory.access_count = 1
        session.add(memory)

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state, "cleanup_reviews", CleanupReviewCoordinator()
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state, "vitality_decay", VitalityDecayCoordinator()
    )

    query_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
    )
    item = query_payload["items"][0]

    prepare_result = await maintenance_api.prepare_vitality_cleanup(
        maintenance_api.VitalityCleanupPrepareRequest(
            action="keep",
            selections=[
                maintenance_api.CleanupSelectionItem(
                    memory_id=item["memory_id"],
                    state_hash=item["state_hash"],
                )
            ],
            reviewer="test-user",
        )
    )
    review = prepare_result["review"]

    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.access_count = int(memory.access_count or 0) + 1
        session.add(memory)

    confirm_result = await maintenance_api.confirm_vitality_cleanup(
        maintenance_api.VitalityCleanupConfirmRequest(
            review_id=review["review_id"],
            token=review["token"],
            confirmation_phrase=review["confirmation_phrase"],
        )
    )

    async with client.session() as session:
        still_exists = await session.execute(
            select(Memory.id).where(Memory.id == created["id"])
        )
        assert still_exists.scalar_one_or_none() == created["id"]

    await client.close()
    assert confirm_result["kept_count"] == 0
    assert confirm_result["skipped_count"] == 1
    assert confirm_result["skipped"][0]["reason"] == "stale_state"


@pytest.mark.asyncio
async def test_vitality_cleanup_confirm_skips_memory_missing_after_prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-missing-confirm.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Missing confirm target",
        priority=1,
        title="missing_confirm_note",
        domain="core",
    )
    await client.remove_path(path="missing_confirm_note", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.05
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=100)
        )
        memory.access_count = 0
        session.add(memory)

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state, "cleanup_reviews", CleanupReviewCoordinator()
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state, "vitality_decay", VitalityDecayCoordinator()
    )

    query_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
    )
    item = query_payload["items"][0]

    prepare_result = await maintenance_api.prepare_vitality_cleanup(
        maintenance_api.VitalityCleanupPrepareRequest(
            action="delete",
            selections=[
                maintenance_api.CleanupSelectionItem(
                    memory_id=item["memory_id"],
                    state_hash=item["state_hash"],
                )
            ],
            reviewer="test-user",
        )
    )
    review = prepare_result["review"]

    await client.permanently_delete_memory(created["id"], require_orphan=True)

    confirm_result = await maintenance_api.confirm_vitality_cleanup(
        maintenance_api.VitalityCleanupConfirmRequest(
            review_id=review["review_id"],
            token=review["token"],
            confirmation_phrase=review["confirmation_phrase"],
        )
    )

    await client.close()
    assert confirm_result["ok"] is True
    assert confirm_result["deleted_count"] == 0
    assert confirm_result["skipped_count"] == 1
    assert confirm_result["skipped"][0]["reason"] == "memory_missing"


@pytest.mark.asyncio
async def test_vitality_cleanup_confirm_collects_unexpected_delete_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-delete-error.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Delete error target",
        priority=1,
        title="delete_error_note",
        domain="core",
    )
    await client.remove_path(path="delete_error_note", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.04
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)
        )
        memory.access_count = 0
        session.add(memory)

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state, "cleanup_reviews", CleanupReviewCoordinator()
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state, "vitality_decay", VitalityDecayCoordinator()
    )

    query_payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
    )
    item = query_payload["items"][0]

    prepare_result = await maintenance_api.prepare_vitality_cleanup(
        maintenance_api.VitalityCleanupPrepareRequest(
            action="delete",
            selections=[
                maintenance_api.CleanupSelectionItem(
                    memory_id=item["memory_id"],
                    state_hash=item["state_hash"],
                )
            ],
            reviewer="test-user",
        )
    )
    review = prepare_result["review"]

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("delete_boom")

    monkeypatch.setattr(client, "permanently_delete_memory", _boom)

    confirm_result = await maintenance_api.confirm_vitality_cleanup(
        maintenance_api.VitalityCleanupConfirmRequest(
            review_id=review["review_id"],
            token=review["token"],
            confirmation_phrase=review["confirmation_phrase"],
        )
    )

    async with client.session() as session:
        still_exists = await session.execute(
            select(Memory.id).where(Memory.id == created["id"])
        )
        assert still_exists.scalar_one_or_none() == created["id"]

    await client.close()
    assert confirm_result["ok"] is False
    assert confirm_result["status"] == "partially_failed"
    assert confirm_result["deleted_count"] == 0
    assert confirm_result["error_count"] == 1
    assert confirm_result["errors"][0]["memory_id"] == created["id"]
    assert "delete_boom" in confirm_result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_vitality_cleanup_prepare_rejects_stale_state_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-stale.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Stale state hash",
        priority=1,
        title="stale_note",
        domain="core",
    )
    await client.remove_path(path="stale_note", domain="core")

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)

    with pytest.raises(maintenance_api.HTTPException) as exc_info:
        await maintenance_api.prepare_vitality_cleanup(
            maintenance_api.VitalityCleanupPrepareRequest(
                action="delete",
                selections=[
                    maintenance_api.CleanupSelectionItem(
                        memory_id=created["id"],
                        state_hash="0" * 64,
                    )
                ],
            )
        )

    await client.close()
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_observability_summary_includes_vitality_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyClient:
        async def get_index_status(self) -> Dict[str, Any]:
            return {"degraded": False, "index_available": True}

        async def get_gist_stats(self) -> Dict[str, Any]:
            return {"degraded": False, "total_rows": 0}

        async def get_vitality_stats(self) -> Dict[str, Any]:
            return {"degraded": False, "total_memories": 3, "low_vitality_count": 1}

    async def _ensure_started(_factory) -> None:
        return None

    async def _index_worker_status() -> Dict[str, Any]:
        return {"enabled": True, "running": False, "recent_jobs": [], "stats": {}}

    async def _write_lane_status() -> Dict[str, Any]:
        return {
            "global_concurrency": 1,
            "global_active": 0,
            "global_waiting": 0,
            "session_waiting_count": 0,
            "session_waiting_sessions": 0,
            "max_session_waiting": 0,
            "wait_warn_ms": 2000,
        }

    async def _decay_status() -> Dict[str, Any]:
        return {"applied": True, "degraded": False}

    async def _cleanup_summary() -> Dict[str, Any]:
        return {"pending_reviews": 0}

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: _DummyClient())
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state.index_worker, "status", _index_worker_status
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.write_lanes, "status", _write_lane_status
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.vitality_decay, "status", _decay_status
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state.cleanup_reviews, "summary", _cleanup_summary
    )

    async with maintenance_api._search_events_guard:
        maintenance_api._search_events.clear()
    async with maintenance_api._cleanup_query_events_guard:
        maintenance_api._cleanup_query_events.clear()
    monkeypatch.setattr(maintenance_api, "_search_events_loaded", True)

    payload = await maintenance_api.get_observability_summary()

    assert payload["status"] == "ok"
    assert payload["vitality_stats"]["degraded"] is False
    assert payload["vitality_decay"]["degraded"] is False
    assert payload["cleanup_reviews"]["pending_reviews"] == 0
    assert payload["cleanup_query_stats"]["total_queries"] == 0
    assert payload["cleanup_query_stats"]["index_hit_ratio"] == 0.0


@pytest.mark.asyncio
async def test_vitality_cleanup_query_stats_are_exposed_in_observability_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-cleanup-observability.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Cleanup observability target",
        priority=1,
        title="cleanup_obs",
        domain="core",
    )
    await client.remove_path(path="cleanup_obs", domain="core")
    async with client.session() as session:
        memory = await session.get(Memory, created["id"])
        assert memory is not None
        memory.vitality_score = 0.07
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=80)
        )
        memory.access_count = 0
        session.add(memory)

    async def _ensure_started(_factory) -> None:
        return None

    monkeypatch.setattr(maintenance_api, "get_sqlite_client", lambda: client)
    monkeypatch.setattr(maintenance_api.runtime_state, "ensure_started", _ensure_started)
    monkeypatch.setattr(
        maintenance_api.runtime_state, "cleanup_reviews", CleanupReviewCoordinator()
    )
    monkeypatch.setattr(
        maintenance_api.runtime_state, "vitality_decay", VitalityDecayCoordinator()
    )
    async with maintenance_api._cleanup_query_events_guard:
        maintenance_api._cleanup_query_events.clear()

    query_result = await maintenance_api.query_vitality_cleanup_candidates(
        maintenance_api.VitalityCleanupQueryRequest(
            threshold=0.2,
            inactive_days=14,
            limit=10,
        )
    )
    assert query_result["ok"] is True
    summary_profile = query_result["summary"].get("query_profile") or {}
    assert float(summary_profile.get("query_ms") or 0.0) >= 0.0

    summary_payload = await maintenance_api.get_observability_summary()
    cleanup_stats = summary_payload["cleanup_query_stats"]

    await client.close()
    assert cleanup_stats["total_queries"] >= 1
    assert cleanup_stats["memory_index_hit_queries"] >= 0
    assert cleanup_stats["path_index_hit_queries"] >= 0
    assert cleanup_stats["slow_queries"] >= 0


@pytest.mark.asyncio
async def test_get_vitality_cleanup_candidates_ignores_invalid_memory_ids(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-candidates-invalid-memory-ids.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    target = await client.create_memory(
        parent_path="",
        content="Target memory for memory_ids filter",
        priority=1,
        title="target",
        domain="core",
    )
    await client.create_memory(
        parent_path="",
        content="Other memory",
        priority=1,
        title="other",
        domain="core",
    )

    async with client.session() as session:
        memory = await session.get(Memory, target["id"])
        assert memory is not None
        memory.vitality_score = 0.1
        memory.last_accessed_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=45)
        )
        memory.access_count = 0
        session.add(memory)

    payload = await client.get_vitality_cleanup_candidates(
        threshold=0.2,
        inactive_days=14,
        limit=10,
        memory_ids=[target["id"], "bad-id", None, "", "3.14", -1, 0, f"{target['id']}"],
    )

    await client.close()

    items = payload["items"]
    assert len(items) == 1
    assert items[0]["memory_id"] == target["id"]


@pytest.mark.asyncio
async def test_reinforce_memory_access_ignores_invalid_memory_ids(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "week6-reinforce-invalid-memory-ids.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="Reinforce target",
        priority=1,
        title="reinforce_target",
        domain="core",
    )

    async with client.session() as session:
        reinforced = await client._reinforce_memory_access(
            session,
            [created["id"], "invalid", None, "1.5", -9, 0, f"{created['id']}"],
        )

    async with client.session() as session:
        row = await session.get(Memory, created["id"])
        assert row is not None
        assert int(row.access_count or 0) >= 1

    await client.close()
    assert reinforced == 1
