from pathlib import Path

import pytest

from db.sqlite_client import SQLiteClient


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


@pytest.mark.asyncio
async def test_embedding_provider_chain_disabled_keeps_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "api")
    monkeypatch.setenv("EMBEDDING_PROVIDER_CHAIN_ENABLED", "false")
    monkeypatch.delenv("RETRIEVAL_EMBEDDING_API_BASE", raising=False)
    monkeypatch.delenv("ROUTER_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    client = SQLiteClient(_sqlite_url(tmp_path / "provider-chain-disabled.db"))
    await client.init_db()

    degrade_reasons: list[str] = []
    async with client.session() as session:
        embedding = await client._get_embedding(
            session,
            "provider chain disabled fallback sample",
            degrade_reasons=degrade_reasons,
        )
    await client.close()

    assert len(embedding) == client._embedding_dim
    assert "embedding_config_missing" in degrade_reasons
    assert "embedding_fallback_hash" in degrade_reasons


@pytest.mark.asyncio
async def test_embedding_provider_chain_uses_configured_fallback_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "router")
    monkeypatch.setenv("EMBEDDING_PROVIDER_CHAIN_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER_FAIL_OPEN", "false")
    monkeypatch.setenv("EMBEDDING_PROVIDER_FALLBACK", "api")
    monkeypatch.delenv("ROUTER_API_BASE", raising=False)
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_BASE", "https://embedding.example/v1")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_MODEL", "chain-model")

    client = SQLiteClient(_sqlite_url(tmp_path / "provider-chain-fallback-api.db"))
    await client.init_db()

    call_meta: dict[str, str] = {"base": "", "endpoint": "", "api_key": ""}

    async def _fake_post_json(base: str, endpoint: str, payload, api_key: str = ""):
        call_meta["base"] = base
        call_meta["endpoint"] = endpoint
        call_meta["api_key"] = api_key
        assert payload["model"] == "chain-model"
        return {"data": [{"embedding": [0.11, 0.22, 0.33]}]}

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    degrade_reasons: list[str] = []
    async with client.session() as session:
        embedding = await client._get_embedding(
            session,
            "provider chain fallback provider sample",
            degrade_reasons=degrade_reasons,
        )
    await client.close()

    assert embedding == [0.11, 0.22, 0.33]
    assert call_meta["base"] == "https://embedding.example/v1"
    assert call_meta["endpoint"] == "/embeddings"
    assert call_meta["api_key"] == "test-key"
    assert "embedding_fallback_hash" not in degrade_reasons


@pytest.mark.asyncio
async def test_embedding_provider_chain_fail_closed_when_fallback_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "api")
    monkeypatch.setenv("EMBEDDING_PROVIDER_CHAIN_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER_FAIL_OPEN", "false")
    monkeypatch.setenv("EMBEDDING_PROVIDER_FALLBACK", "none")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_BASE", "https://embedding.example/v1")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_MODEL", "chain-model")

    client = SQLiteClient(_sqlite_url(tmp_path / "provider-chain-fail-closed.db"))
    await client.init_db()

    async def _always_fail(*_args, **_kwargs):
        return None

    monkeypatch.setattr(client, "_post_json", _always_fail)
    degrade_reasons: list[str] = []
    async with client.session() as session:
        with pytest.raises(RuntimeError, match="embedding_provider_chain_blocked"):
            await client._get_embedding(
                session,
                "provider chain fail closed sample",
                degrade_reasons=degrade_reasons,
            )
    await client.close()

    assert "embedding_provider_chain_blocked" in degrade_reasons


@pytest.mark.asyncio
async def test_embedding_provider_chain_fail_open_still_hash_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "api")
    monkeypatch.setenv("EMBEDDING_PROVIDER_CHAIN_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER_FAIL_OPEN", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER_FALLBACK", "none")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_BASE", "https://embedding.example/v1")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_MODEL", "chain-model")

    client = SQLiteClient(_sqlite_url(tmp_path / "provider-chain-fail-open.db"))
    await client.init_db()

    async def _always_fail(*_args, **_kwargs):
        return None

    monkeypatch.setattr(client, "_post_json", _always_fail)
    degrade_reasons: list[str] = []
    async with client.session() as session:
        embedding = await client._get_embedding(
            session,
            "provider chain fail open hash fallback sample",
            degrade_reasons=degrade_reasons,
        )
    await client.close()

    assert len(embedding) == client._embedding_dim
    assert "embedding_fallback_hash" in degrade_reasons


@pytest.mark.asyncio
async def test_embedding_provider_chain_cache_hit_avoids_second_remote_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "api")
    monkeypatch.setenv("EMBEDDING_PROVIDER_CHAIN_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER_FAIL_OPEN", "false")
    monkeypatch.setenv("EMBEDDING_PROVIDER_FALLBACK", "hash")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_BASE", "https://embedding.example/v1")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_MODEL", "cache-model")

    client = SQLiteClient(_sqlite_url(tmp_path / "provider-chain-cache.db"))
    await client.init_db()

    call_counter = {"value": 0}

    async def _fake_post_json(*_args, **_kwargs):
        call_counter["value"] += 1
        return {"data": [{"embedding": [0.5, 0.4, 0.3]}]}

    monkeypatch.setattr(client, "_post_json", _fake_post_json)
    async with client.session() as session:
        first = await client._get_embedding(session, "provider chain cache sample")
        await session.flush()
        second = await client._get_embedding(session, "provider chain cache sample")
    await client.close()

    assert first == [0.5, 0.4, 0.3]
    assert second == [0.5, 0.4, 0.3]
    assert call_counter["value"] == 1
