import pytest

from runtime_state import SessionPromotionTracker


@pytest.mark.asyncio
async def test_session_promotion_tracker_summary_aggregates_metrics() -> None:
    tracker = SessionPromotionTracker()

    await tracker.record_event(
        session_id="session-a",
        source="compact_context",
        trigger_reason="manual",
        uri="core://agent/profile",
        memory_id=1,
        gist_method="llm_gist",
        quality=0.8,
        degraded=False,
        index_queued=1,
        index_dropped=0,
        index_deduped=1,
    )
    await tracker.record_event(
        session_id="session-a",
        source="auto_flush",
        trigger_reason="threshold",
        uri="core://agent/profile/flush",
        memory_id=2,
        gist_method="extractive_bullets",
        quality=1.2,
        degraded=True,
        degrade_reasons=["index_enqueue_dropped"],
        index_queued="2",
        index_dropped="3",
        index_deduped=-1,
    )

    summary = await tracker.summary()

    assert summary["total_promotions"] == 2
    assert summary["degraded_promotions"] == 1
    assert summary["source_breakdown"]["compact_context"] == 1
    assert summary["source_breakdown"]["auto_flush"] == 1
    assert summary["reason_breakdown"]["manual"] == 1
    assert summary["reason_breakdown"]["threshold"] == 1
    assert summary["gist_method_breakdown"]["llm_gist"] == 1
    assert summary["gist_method_breakdown"]["extractive_bullets"] == 1
    assert summary["avg_quality"] == pytest.approx(0.9)
    assert summary["index_queue"] == {"queued": 3, "dropped": 3, "deduped": 1}
    assert summary["top_sessions"][0]["session_id"] == "session-a"
    assert summary["top_sessions"][0]["count"] == 2
    assert summary["last_promotion_at"] is not None
