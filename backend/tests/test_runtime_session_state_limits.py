import pytest

from runtime_state import SessionFlushTracker, SessionSearchCache


@pytest.mark.asyncio
async def test_session_search_cache_evicts_oldest_session_when_max_session_limit_is_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_SESSION_CACHE_MAX_SESSIONS", "2")
    cache = SessionSearchCache()

    await cache.record_hit(
        session_id="session-a",
        uri="core://session-a",
        memory_id=1,
        snippet="release checklist",
    )
    await cache.record_hit(
        session_id="session-b",
        uri="core://session-b",
        memory_id=2,
        snippet="release checklist",
    )
    assert await cache.search(session_id="session-a", query="release", limit=5)

    await cache.record_hit(
        session_id="session-c",
        uri="core://session-c",
        memory_id=3,
        snippet="release checklist",
    )

    assert await cache.search(session_id="session-a", query="release", limit=5)
    assert await cache.search(session_id="session-c", query="release", limit=5)
    assert await cache.search(session_id="session-b", query="release", limit=5) == []

    summary = await cache.summary()
    assert summary["session_count"] == 2
    assert summary["max_sessions"] == 2


@pytest.mark.asyncio
async def test_session_flush_tracker_evicts_oldest_session_when_max_session_limit_is_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNTIME_FLUSH_MAX_SESSIONS", "2")
    tracker = SessionFlushTracker()

    await tracker.record_event(session_id="session-a", message="alpha event")
    await tracker.record_event(session_id="session-b", message="beta event")
    assert "alpha event" in await tracker.build_summary(session_id="session-a")

    await tracker.record_event(session_id="session-c", message="gamma event")

    assert "alpha event" in await tracker.build_summary(session_id="session-a")
    assert "gamma event" in await tracker.build_summary(session_id="session-c")
    assert await tracker.build_summary(session_id="session-b") == ""

    summary = await tracker.summary()
    assert summary["session_count"] == 2
    assert summary["max_sessions"] == 2
