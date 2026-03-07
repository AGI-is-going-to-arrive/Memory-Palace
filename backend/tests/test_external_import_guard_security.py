import json
from pathlib import Path

from security.import_guard import ExternalImportGuard, ExternalImportGuardConfig


class _FixedClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _build_guard(
    tmp_path: Path,
    *,
    max_total_bytes: int = 1024,
    max_files: int = 10,
    rate_limit_window_seconds: int = 60,
    rate_limit_max_requests: int = 10,
    rate_limit_state_file: Path | None = None,
    require_shared_rate_limit: bool = False,
    clock=None,
) -> tuple[ExternalImportGuard, Path]:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(exist_ok=True)
    config = ExternalImportGuardConfig(
        enabled=True,
        allowed_roots=(allowed_root.resolve(),),
        allowed_exts=(".md", ".txt", ".json"),
        max_total_bytes=max_total_bytes,
        max_files=max_files,
        rate_limit_window_seconds=rate_limit_window_seconds,
        rate_limit_max_requests=rate_limit_max_requests,
        rate_limit_state_file=rate_limit_state_file,
        require_shared_rate_limit=require_shared_rate_limit,
    )
    return ExternalImportGuard(config=config, clock=clock), allowed_root


def test_external_import_guard_allows_safe_batch(tmp_path: Path) -> None:
    guard, allowed_root = _build_guard(tmp_path)
    file_path = allowed_root / "safe.md"
    file_path.write_text("hello-safe", encoding="utf-8")

    result = guard.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-1",
    )

    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert result["file_count"] == 1
    assert result["total_bytes"] == file_path.stat().st_size
    assert len(result["allowed_files"]) == 1
    assert result["rejected_files"] == []


def test_external_import_guard_rejects_path_traversal_outside_allowed_roots(
    tmp_path: Path,
) -> None:
    guard, allowed_root = _build_guard(tmp_path)
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    traversal_path = allowed_root / ".." / "outside.txt"

    result = guard.validate_batch(
        file_paths=[traversal_path],
        actor_id="actor-a",
        session_id="session-1",
    )

    assert result["ok"] is False
    assert result["reason"] == "file_validation_failed"
    assert result["file_count"] == 0
    assert result["rejected_files"][0]["reason"] == "path_not_allowed"


def test_external_import_guard_rejects_extension_not_in_whitelist(tmp_path: Path) -> None:
    guard, allowed_root = _build_guard(tmp_path)
    bad_file = allowed_root / "payload.exe"
    bad_file.write_text("binary", encoding="utf-8")

    result = guard.validate_batch(
        file_paths=[bad_file],
        actor_id="actor-a",
        session_id="session-1",
    )

    assert result["ok"] is False
    assert result["reason"] == "file_validation_failed"
    assert result["rejected_files"][0]["reason"] == "extension_not_allowed"


def test_external_import_guard_rejects_when_total_size_exceeds_limit(
    tmp_path: Path,
) -> None:
    guard, allowed_root = _build_guard(tmp_path, max_total_bytes=8, max_files=5)
    first = allowed_root / "a.txt"
    second = allowed_root / "b.txt"
    first.write_text("12345", encoding="utf-8")
    second.write_text("67890", encoding="utf-8")

    result = guard.validate_batch(
        file_paths=[first, second],
        actor_id="actor-a",
        session_id="session-1",
    )

    assert result["ok"] is False
    assert result["reason"] == "max_total_bytes_exceeded"
    assert result["file_count"] == 2
    assert result["total_bytes"] == 10
    assert result["rejected_files"][0]["reason"] == "max_total_bytes_exceeded"


def test_external_import_guard_rejects_when_file_count_exceeds_limit(
    tmp_path: Path,
) -> None:
    guard, allowed_root = _build_guard(tmp_path, max_files=1)
    first = allowed_root / "a.txt"
    second = allowed_root / "b.txt"
    first.write_text("1", encoding="utf-8")
    second.write_text("2", encoding="utf-8")

    result = guard.validate_batch(
        file_paths=[first, second],
        actor_id="actor-a",
        session_id="session-1",
    )

    assert result["ok"] is False
    assert result["reason"] == "max_files_exceeded"
    assert result["allowed_files"] == []
    assert len(result["rejected_files"]) == 2
    assert all(item["reason"] == "max_files_exceeded" for item in result["rejected_files"])


def test_external_import_guard_hits_rate_limit_and_returns_retry_after_seconds(
    tmp_path: Path,
) -> None:
    fixed_clock = _FixedClock(now=1000.0)
    guard, allowed_root = _build_guard(
        tmp_path,
        rate_limit_window_seconds=30,
        rate_limit_max_requests=1,
        clock=fixed_clock,
    )
    file_path = allowed_root / "safe.txt"
    file_path.write_text("ok", encoding="utf-8")

    first = guard.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-rate",
    )
    second = guard.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-rate",
    )

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["reason"] == "rate_limited"
    assert second["retry_after_seconds"] == 30


def test_external_import_guard_rate_limit_blocks_actor_across_session_rotation(
    tmp_path: Path,
) -> None:
    fixed_clock = _FixedClock(now=1500.0)
    guard, allowed_root = _build_guard(
        tmp_path,
        rate_limit_window_seconds=30,
        rate_limit_max_requests=1,
        clock=fixed_clock,
    )
    file_path = allowed_root / "safe.txt"
    file_path.write_text("ok", encoding="utf-8")

    first = guard.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-a",
    )
    second = guard.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-b",
    )

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["reason"] == "rate_limited"
    assert second["rate_limit"]["scope"] == "actor"
    assert second["rate_limit"]["key"] == "actor-a::*"


def test_external_import_guard_rejects_when_shared_rate_limit_required_without_state_file(
    tmp_path: Path,
) -> None:
    fixed_clock = _FixedClock(now=1750.0)
    guard, allowed_root = _build_guard(
        tmp_path,
        rate_limit_window_seconds=30,
        rate_limit_max_requests=1,
        require_shared_rate_limit=True,
        clock=fixed_clock,
    )
    file_path = allowed_root / "safe.txt"
    file_path.write_text("ok", encoding="utf-8")

    result = guard.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-a",
    )

    assert result["ok"] is False
    assert result["reason"] == "rate_limit_shared_state_required"
    assert result["rate_limit_storage"] == "process_memory"
    assert isinstance(result.get("config_errors"), list)


def test_external_import_guard_rate_limit_state_file_blocks_across_instances(
    tmp_path: Path,
) -> None:
    fixed_clock = _FixedClock(now=2000.0)
    state_file = tmp_path / "rate_limit_state.json"
    guard_a, allowed_root = _build_guard(
        tmp_path,
        rate_limit_window_seconds=30,
        rate_limit_max_requests=1,
        rate_limit_state_file=state_file,
        clock=fixed_clock,
    )
    guard_b, _ = _build_guard(
        tmp_path,
        rate_limit_window_seconds=30,
        rate_limit_max_requests=1,
        rate_limit_state_file=state_file,
        clock=fixed_clock,
    )
    file_path = allowed_root / "safe.txt"
    file_path.write_text("ok", encoding="utf-8")

    first = guard_a.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-shared",
    )
    second = guard_b.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-shared",
    )

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["reason"] == "rate_limited"
    assert second["rate_limit_storage"] == "state_file"


def test_external_import_guard_state_file_prunes_stale_session_buckets(
    tmp_path: Path,
) -> None:
    fixed_clock = _FixedClock(now=5000.0)
    state_file = tmp_path / "rate_limit_state.json"
    guard, allowed_root = _build_guard(
        tmp_path,
        rate_limit_window_seconds=1,
        rate_limit_max_requests=100,
        rate_limit_state_file=state_file,
        clock=fixed_clock,
    )
    file_path = allowed_root / "safe.txt"
    file_path.write_text("ok", encoding="utf-8")

    for index in range(5):
        result = guard.validate_batch(
            file_paths=[file_path],
            actor_id="actor-a",
            session_id=f"session-{index}",
        )
        assert result["ok"] is True
        fixed_clock.now += 2.0

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    actor_bucket = payload.get("actor-a::*")
    session_keys = sorted(
        key for key in payload.keys() if key.startswith("actor-a::session-")
    )

    assert isinstance(actor_bucket, list)
    assert len(actor_bucket) == 1
    assert session_keys == ["actor-a::session-4"]


def test_external_import_guard_fails_closed_when_state_file_is_not_regular_file(
    tmp_path: Path,
) -> None:
    fixed_clock = _FixedClock(now=3000.0)
    state_dir = tmp_path / "rate_limit_state_dir"
    state_dir.mkdir()
    guard, allowed_root = _build_guard(
        tmp_path,
        rate_limit_window_seconds=30,
        rate_limit_max_requests=1,
        rate_limit_state_file=state_dir,
        clock=fixed_clock,
    )
    file_path = allowed_root / "safe.txt"
    file_path.write_text("ok", encoding="utf-8")

    result = guard.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-state-file-dir",
    )

    assert result["ok"] is False
    assert result["reason"] == "rate_limit_state_unavailable"
    assert result["rate_limit_state_error"] == "state_file_not_regular_file"


def test_external_import_guard_fails_closed_when_state_bucket_has_nan_timestamp(
    tmp_path: Path,
) -> None:
    fixed_clock = _FixedClock(now=4000.0)
    state_file = tmp_path / "rate_limit_state.json"
    state_file.write_text(
        '{"actor-a::session-nan": ["nan"]}',
        encoding="utf-8",
    )
    guard, allowed_root = _build_guard(
        tmp_path,
        rate_limit_window_seconds=30,
        rate_limit_max_requests=1,
        rate_limit_state_file=state_file,
        clock=fixed_clock,
    )
    file_path = allowed_root / "safe.txt"
    file_path.write_text("ok", encoding="utf-8")

    result = guard.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-nan",
    )

    assert result["ok"] is False
    assert result["reason"] == "rate_limit_state_unavailable"
    assert result["rate_limit_state_error"] == "state_bucket_invalid_timestamp"


def test_external_import_guard_is_fail_closed_when_disabled_or_no_allowed_roots(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "note.txt"
    file_path.write_text("x", encoding="utf-8")

    disabled = ExternalImportGuard(
        config=ExternalImportGuardConfig(
            enabled=False,
            allowed_roots=(tmp_path.resolve(),),
            allowed_exts=(".txt",),
        )
    )
    no_roots = ExternalImportGuard(
        config=ExternalImportGuardConfig(
            enabled=True,
            allowed_roots=(),
            allowed_exts=(".txt",),
        )
    )

    disabled_result = disabled.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-1",
    )
    no_roots_result = no_roots.validate_batch(
        file_paths=[file_path],
        actor_id="actor-a",
        session_id="session-1",
    )

    assert disabled_result["ok"] is False
    assert disabled_result["reason"] == "external_import_disabled"
    assert no_roots_result["ok"] is False
    assert no_roots_result["reason"] == "allowed_roots_not_configured"
