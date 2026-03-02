from pathlib import Path

import pytest

from db.sqlite_client import SQLiteClient


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


@pytest.mark.asyncio
async def test_sqlite_vec_rollout_defaults_keep_legacy_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("RETRIEVAL_SQLITE_VEC_ENABLED", "false")
    monkeypatch.setenv("RETRIEVAL_VECTOR_ENGINE", "legacy")
    monkeypatch.setenv("RETRIEVAL_SQLITE_VEC_READ_RATIO", "0")

    client = SQLiteClient(_sqlite_url(tmp_path / "sqlite-vec-defaults.db"))
    await client.init_db()
    status_payload = await client.get_index_status()
    await client.close()

    capabilities = status_payload["capabilities"]
    assert capabilities["sqlite_vec_enabled"] is False
    assert capabilities["vector_engine_requested"] == "legacy"
    assert capabilities["vector_engine_effective"] == "legacy"
    assert capabilities["sqlite_vec_status"] == "disabled"
    assert capabilities["sqlite_vec_readiness"] == "hold"
    assert capabilities["sqlite_vec_read_ratio"] == 0


@pytest.mark.asyncio
async def test_sqlite_vec_rollout_enabled_without_extension_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("RETRIEVAL_SQLITE_VEC_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_VECTOR_ENGINE", "vec")
    monkeypatch.setenv("RETRIEVAL_SQLITE_VEC_READ_RATIO", "100")
    monkeypatch.delenv("RETRIEVAL_SQLITE_VEC_EXTENSION_PATH", raising=False)

    client = SQLiteClient(_sqlite_url(tmp_path / "sqlite-vec-no-extension.db"))
    await client.init_db()
    await client.create_memory(
        parent_path="",
        content="sqlite vec fallback legacy sample",
        priority=1,
        title="sqlite_vec_fallback",
        domain="core",
    )

    status_payload = await client.get_index_status()
    search_payload = await client.search_advanced(
        query="sqlite vec fallback",
        mode="semantic",
        max_results=5,
        candidate_multiplier=2,
        filters={},
    )
    await client.close()

    capabilities = status_payload["capabilities"]
    assert capabilities["sqlite_vec_enabled"] is True
    assert capabilities["vector_engine_requested"] == "vec"
    assert capabilities["vector_engine_effective"] == "legacy"
    assert capabilities["sqlite_vec_status"] == "skipped_no_extension_path"
    assert capabilities["sqlite_vec_diag_code"] == "path_not_provided"
    assert "sqlite_vec_fallback_legacy" in search_payload.get("degrade_reasons", [])
    assert search_payload["results"]
    assert (
        search_payload["metadata"]["vector_engine_selected"] == "legacy"
    )


@pytest.mark.asyncio
async def test_sqlite_vec_rollout_invalid_extension_path_marks_hold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("RETRIEVAL_SQLITE_VEC_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_VECTOR_ENGINE", "vec")
    monkeypatch.setenv("RETRIEVAL_SQLITE_VEC_EXTENSION_PATH", str(tmp_path / "missing_vec"))

    client = SQLiteClient(_sqlite_url(tmp_path / "sqlite-vec-invalid-path.db"))
    await client.init_db()
    status_payload = await client.get_index_status()
    await client.close()

    capabilities = status_payload["capabilities"]
    assert capabilities["vector_engine_effective"] == "legacy"
    assert capabilities["sqlite_vec_status"] == "invalid_extension_path"
    assert capabilities["sqlite_vec_diag_code"] == "path_not_found"
    assert capabilities["sqlite_vec_readiness"] == "hold"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_ratio", "expected_ratio"),
    [
        ("180", 100),
        ("-9", 0),
    ],
)
async def test_sqlite_vec_rollout_read_ratio_is_clamped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_ratio: str,
    expected_ratio: int,
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("RETRIEVAL_SQLITE_VEC_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_VECTOR_ENGINE", "dual")
    monkeypatch.setenv("RETRIEVAL_SQLITE_VEC_READ_RATIO", raw_ratio)
    monkeypatch.delenv("RETRIEVAL_SQLITE_VEC_EXTENSION_PATH", raising=False)

    db_path = tmp_path / f"sqlite-vec-ratio-{raw_ratio}.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()
    status_payload = await client.get_index_status()
    await client.close()

    assert status_payload["capabilities"]["sqlite_vec_read_ratio"] == expected_ratio
