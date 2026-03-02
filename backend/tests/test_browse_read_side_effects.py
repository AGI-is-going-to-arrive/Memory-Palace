from pathlib import Path

import pytest

from api import browse as browse_api
from db.sqlite_client import Memory, SQLiteClient


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _memory_access_snapshot(client: SQLiteClient, memory_id: int) -> dict[str, object]:
    async with client.session() as session:
        memory = await session.get(Memory, memory_id)
        assert memory is not None
        return {
            "access_count": int(memory.access_count or 0),
            "vitality_score": float(memory.vitality_score or 0.0),
            "last_accessed_at": memory.last_accessed_at,
        }


@pytest.mark.asyncio
async def test_browse_get_node_read_has_no_access_reinforce_side_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "browse-read-side-effects.db"
    client = SQLiteClient(_sqlite_url(db_path))
    await client.init_db()

    created = await client.create_memory(
        parent_path="",
        content="node body",
        priority=1,
        title="side_effect_node",
        domain="core",
    )
    path = "side_effect_node"

    before_read = await _memory_access_snapshot(client, created["id"])

    monkeypatch.setattr(browse_api, "get_sqlite_client", lambda: client)
    payload = await browse_api.get_node(path=path, domain="core")

    after_browse_read = await _memory_access_snapshot(client, created["id"])
    assert payload["node"]["path"] == path
    assert after_browse_read["access_count"] == before_read["access_count"]
    assert after_browse_read["vitality_score"] == before_read["vitality_score"]
    assert after_browse_read["last_accessed_at"] == before_read["last_accessed_at"]

    await client.get_memory_by_path(path, domain="core")
    after_default_read = await _memory_access_snapshot(client, created["id"])

    await client.close()

    assert after_default_read["access_count"] == after_browse_read["access_count"] + 1
