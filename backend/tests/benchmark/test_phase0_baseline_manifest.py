import json
from datetime import datetime
from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parent
BASELINE_MANIFEST = BENCHMARK_DIR / "baseline_manifest.md"
THRESHOLDS_FILE = BENCHMARK_DIR / "thresholds_v1.json"

EXPECTED_THRESHOLD_TOP_LEVEL_KEYS = {
    "version",
    "frozen_source",
    "frozen_at_utc",
    "profile_a",
    "profile_b",
    "profile_cd",
    "global",
    "write_guard",
    "intent",
    "gist",
}

SEARCH_MEMORY_CONTRACT_KEYS = {
    "ok",
    "query",
    "query_effective",
    "mode_requested",
    "mode_applied",
    "results",
    "degraded",
}

COMPACT_CONTEXT_CONTRACT_KEYS = {
    "ok",
    "session_id",
    "reason",
    "flushed",
    "gist_method",
    "quality",
    "source_hash",
}

WRITE_GUARD_CONTRACT_KEYS = {
    "action",
    "reason",
    "method",
    "degraded",
    "degrade_reasons",
}


def _parse_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_baseline_manifest_exists_and_references_sources() -> None:
    assert BASELINE_MANIFEST.exists()
    text = BASELINE_MANIFEST.read_text(encoding="utf-8")

    assert "backend/tests/benchmark_results.md" in text
    assert "backend/mcp_server.py" in text
    assert "Threshold Contract v1" in text
    assert "MCP/API Contract Lock" in text
    assert "Freeze Rule" in text
    assert "do not change both implementation code and benchmark gold set" in text.lower()

    assert "### `search_memory` response must contain" in text
    assert "### `compact_context` response must contain" in text
    assert "### `write_guard` decision must contain" in text
    for key in sorted(SEARCH_MEMORY_CONTRACT_KEYS):
        assert f"- `{key}`" in text
    for key in sorted(COMPACT_CONTEXT_CONTRACT_KEYS):
        assert f"- `{key}`" in text
    for key in sorted(WRITE_GUARD_CONTRACT_KEYS):
        assert f"- `{key}`" in text


def test_thresholds_v1_contract_shape() -> None:
    assert THRESHOLDS_FILE.exists()
    payload = json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8"))
    assert set(payload) == EXPECTED_THRESHOLD_TOP_LEVEL_KEYS

    assert payload["version"] == "v1"
    assert payload["frozen_source"] == "backend/tests/benchmark_results.md"
    assert _parse_utc_iso(payload["frozen_at_utc"]).tzinfo is not None

    assert payload["profile_a"] == {"enabled": True}
    assert payload["profile_b"] == {"enabled": True}
    assert payload["profile_cd"]["enabled"] is True
    assert payload["profile_cd"]["p95_ms_lt"] == 2000
    assert 0.0 <= payload["global"]["degrade_rate_lt"] <= 1.0
    assert payload["global"]["degrade_rate_lt"] == 0.05
    assert payload["write_guard"]["precision_gte"] >= 0.9
    assert payload["write_guard"]["recall_gte"] >= 0.85
    assert payload["write_guard"]["precision_gte"] >= payload["write_guard"]["recall_gte"]
    assert payload["intent"]["accuracy_gte"] >= 0.8
    assert payload["gist"]["rouge_l_gte"] >= 0.4
