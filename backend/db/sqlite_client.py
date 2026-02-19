"""
SQLite Client for Memory Palace System

This module implements the SQLite-based memory storage with:
- Path-based addressing (mem://path/to/memory)
- Version control via deprecated flag
- Multiple paths (aliases) pointing to same memory
"""

import os
import re
import json
import math
import hashlib
import sqlite3
import time
import httpx
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from contextlib import asynccontextmanager

from sqlalchemy import (
    Column,
    Integer,
    Float,
    Index,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    create_engine,
    select,
    update,
    delete,
    func,
    and_,
    or_,
    text,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from dotenv import load_dotenv, find_dotenv
from .migration_runner import apply_pending_migrations

# Load environment variables
_dotenv_path = find_dotenv(usecwd=True)
if _dotenv_path:
    load_dotenv(_dotenv_path)

Base = declarative_base()

_SQLITE_ADAPTERS_REGISTERED = False


def _register_sqlite_adapters() -> None:
    """
    Register explicit sqlite adapters for Python datetime objects.

    Python 3.12+ deprecates sqlite3's implicit default datetime adapter.
    Registering our own adapter removes deprecation noise and keeps behavior stable.
    """
    global _SQLITE_ADAPTERS_REGISTERED
    if _SQLITE_ADAPTERS_REGISTERED:
        return
    sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" "))
    _SQLITE_ADAPTERS_REGISTERED = True


_register_sqlite_adapters()


def _utc_now() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def _utc_now_naive() -> datetime:
    """Naive UTC datetime for existing DB schema compatibility."""
    return _utc_now().replace(tzinfo=None)


# =============================================================================
# ORM Models
# =============================================================================


class Memory(Base):
    """A single memory unit with content and metadata.

    Note: The 'title' column was removed. A memory's display name is now
    derived from the last segment of its path(s) in the paths table.
    Existing DB columns named 'title' are simply ignored by SQLAlchemy.

    Version chain: When a memory is updated, the old version's `migrated_to`
    field points to the new version's ID, forming a singly-linked list:
        Memory(id=1, migrated_to=5) → Memory(id=5, migrated_to=12) → Memory(id=12, migrated_to=NULL)
    When a middle node is permanently deleted, the chain is repaired by
    skipping over it (A→B→C, delete B → A→C).
    """

    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False)
    deprecated = Column(Boolean, default=False)  # Marked for review/deletion
    migrated_to = Column(
        Integer, nullable=True
    )  # Points to successor memory ID (version chain)
    created_at = Column(DateTime, default=_utc_now_naive)
    vitality_score = Column(
        Float, default=1.0, server_default=text("1.0"), nullable=False
    )
    last_accessed_at = Column(DateTime, nullable=True)
    access_count = Column(
        Integer, default=0, server_default=text("0"), nullable=False
    )

    # Relationship to paths
    paths = relationship("Path", back_populates="memory")
    gists = relationship("MemoryGist", back_populates="memory")
    tags = relationship("MemoryTag", back_populates="memory")


class Path(Base):
    """A path pointing to a memory. Multiple paths can point to the same memory."""

    __tablename__ = "paths"

    # Composite primary key: (domain, path)
    # domain examples: "core", "writer", "game"
    # path examples: "memory-palace", "memory-palace/salem"
    domain = Column(String(64), primary_key=True, default="core")
    path = Column(String(512), primary_key=True)
    memory_id = Column(Integer, ForeignKey("memories.id"), nullable=False)
    created_at = Column(DateTime, default=_utc_now_naive)

    # Context metadata (moved from Memory to Path)
    priority = Column(Integer, default=0)  # Relative priority for ranking
    disclosure = Column(Text, nullable=True)  # When to expand this memory

    # Relationship to memory
    memory = relationship("Memory", back_populates="paths")


class MemoryChunk(Base):
    """Chunked text slices for memory-level retrieval."""

    __tablename__ = "memory_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(Integer, ForeignKey("memories.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    char_start = Column(Integer, nullable=False, default=0)
    char_end = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utc_now_naive)


class MemoryChunkVec(Base):
    """Persisted vectors for memory chunks (fallback pure-SQLite storage)."""

    __tablename__ = "memory_chunks_vec"

    chunk_id = Column(Integer, ForeignKey("memory_chunks.id"), primary_key=True)
    memory_id = Column(Integer, ForeignKey("memories.id"), nullable=False, index=True)
    vector = Column(Text, nullable=False)
    model = Column(String(64), nullable=False, default="hash-v1")
    dim = Column(Integer, nullable=False, default=64)
    created_at = Column(DateTime, default=_utc_now_naive)


class EmbeddingCache(Base):
    """Cache embeddings by deterministic text hash."""

    __tablename__ = "embedding_cache"

    cache_key = Column(String(128), primary_key=True)
    text_hash = Column(String(128), nullable=False, index=True)
    model = Column(String(64), nullable=False, default="hash-v1")
    embedding = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=_utc_now_naive, onupdate=_utc_now_naive)


class IndexMeta(Base):
    """Index runtime metadata and capability flags."""

    __tablename__ = "index_meta"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=_utc_now_naive, onupdate=_utc_now_naive)


class SchemaMigration(Base):
    """Applied schema migration records."""

    __tablename__ = "schema_migrations"

    version = Column(String(32), primary_key=True)
    applied_at = Column(DateTime, default=_utc_now_naive, nullable=False)
    checksum = Column(String(128), nullable=False)


class MemoryGist(Base):
    """Compact gist materialized from a memory body."""

    __tablename__ = "memory_gists"
    __table_args__ = (
        Index("idx_memory_gists_memory_id", "memory_id"),
        Index(
            "idx_memory_gists_memory_source_hash_unique",
            "memory_id",
            "source_content_hash",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(Integer, ForeignKey("memories.id"), nullable=False)
    gist_text = Column(Text, nullable=False)
    source_content_hash = Column(String(128), nullable=False)
    gist_method = Column(String(64), nullable=False, default="fallback")
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utc_now_naive)

    memory = relationship("Memory", back_populates="gists")


class MemoryTag(Base):
    """Structured tag extraction output for memories."""

    __tablename__ = "memory_tags"
    __table_args__ = (
        Index("idx_tags_value", "tag_value"),
        Index("idx_memory_tags_memory_id", "memory_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(Integer, ForeignKey("memories.id"), nullable=False)
    tag_type = Column(String(64), nullable=False)
    tag_value = Column(String(255), nullable=False)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utc_now_naive)

    memory = relationship("Memory", back_populates="tags")


# =============================================================================
# SQLite Client
# =============================================================================


class SQLiteClient:
    """
    Async SQLite client for memory operations.

    Core operations:
    - read: Get memory by path
    - create: New memory with auto-generated or specified path segment
    - update: Create new version, deprecate old, repoint path
    - add_path: Create alias to existing memory
    - search: Substring search on path and content
    """

    def __init__(self, database_url: str):
        """
        Initialize the SQLite client.

        Args:
            database_url: SQLAlchemy async URL, e.g.
                         "sqlite+aiosqlite:///memory_palace.db"
        """
        self.database_url = database_url
        self.engine = create_async_engine(database_url, echo=False)
        self.async_session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self._embedding_backend = (
            os.getenv("RETRIEVAL_EMBEDDING_BACKEND", "hash").strip().lower() or "hash"
        )
        self._embedding_model = (
            self._first_env(
                [
                    "RETRIEVAL_EMBEDDING_MODEL",
                    "ROUTER_EMBEDDING_MODEL",
                    "OPENAI_EMBEDDING_MODEL",
                ],
                default="hash-v1",
            )
            or "hash-v1"
        )
        self._embedding_api_base = self._resolve_embedding_api_base(
            self._embedding_backend
        )
        self._embedding_api_key = self._resolve_embedding_api_key(
            self._embedding_backend
        )
        self._embedding_dim = max(16, self._env_int("RETRIEVAL_EMBEDDING_DIM", 64))
        self._remote_http_timeout_sec = max(
            1.0, self._env_float("RETRIEVAL_REMOTE_TIMEOUT_SEC", 8.0)
        )
        self._reranker_enabled = self._env_bool("RETRIEVAL_RERANKER_ENABLED", False)
        self._reranker_api_base = self._normalize_reranker_api_base(
            self._first_env(
                [
                    "RETRIEVAL_RERANKER_API_BASE",
                    "RETRIEVAL_RERANKER_BASE",
                    "ROUTER_API_BASE",
                    "OPENAI_BASE_URL",
                    "OPENAI_API_BASE",
                ]
            )
        )
        self._reranker_api_key = self._first_env(
            [
                "RETRIEVAL_RERANKER_API_KEY",
                "RETRIEVAL_RERANKER_KEY",
                "ROUTER_API_KEY",
                "OPENAI_API_KEY",
            ]
        )
        self._reranker_model = self._first_env(
            ["RETRIEVAL_RERANKER_MODEL", "ROUTER_RERANKER_MODEL"]
        )
        self._rerank_weight = min(
            1.0, max(0.0, self._env_float("RETRIEVAL_RERANKER_WEIGHT", 0.25))
        )
        self._chunk_size = max(128, self._env_int("RETRIEVAL_CHUNK_SIZE", 500))
        self._chunk_overlap = max(
            0, min(self._chunk_size - 1, self._env_int("RETRIEVAL_CHUNK_OVERLAP", 80))
        )
        self._weight_vector = self._env_float("RETRIEVAL_HYBRID_SEMANTIC_WEIGHT", 0.7)
        self._weight_text = self._env_float("RETRIEVAL_HYBRID_KEYWORD_WEIGHT", 0.3)
        self._weight_priority = self._env_float("RETRIEVAL_WEIGHT_PRIORITY", 0.1)
        self._weight_recency = self._env_float("RETRIEVAL_WEIGHT_RECENCY", 0.06)
        self._weight_path_prefix = self._env_float("RETRIEVAL_WEIGHT_PATH_PREFIX", 0.04)
        self._recency_half_life_days = max(
            1.0, self._env_float("RETRIEVAL_RECENCY_HALF_LIFE_DAYS", 30.0)
        )
        self._vitality_max_score = max(
            0.1, self._env_float("VITALITY_MAX_SCORE", 3.0)
        )
        self._vitality_reinforce_delta = max(
            0.0, self._env_float("VITALITY_REINFORCE_DELTA", 0.08)
        )
        self._vitality_decay_half_life_days = max(
            1.0, self._env_float("VITALITY_DECAY_HALF_LIFE_DAYS", 30.0)
        )
        self._vitality_decay_min_score = max(
            0.0, self._env_float("VITALITY_DECAY_MIN_SCORE", 0.05)
        )
        self._vitality_cleanup_threshold = max(
            0.0, self._env_float("VITALITY_CLEANUP_THRESHOLD", 0.35)
        )
        self._vitality_cleanup_inactive_days = max(
            0.0, self._env_float("VITALITY_CLEANUP_INACTIVE_DAYS", 14.0)
        )
        self._fts_available = False
        self._vector_available = self._embedding_backend not in {
            "none",
            "off",
            "disabled",
            "false",
            "0",
        }

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}

    @staticmethod
    def _first_env(names: List[str], default: str = "") -> str:
        for name in names:
            value = os.getenv(name)
            if value is None:
                continue
            candidate = value.strip()
            if candidate:
                return candidate
        return default

    def _resolve_embedding_api_base(self, backend: str) -> str:
        backend_value = (backend or "").strip().lower()
        if backend_value == "router":
            return self._normalize_embedding_api_base(
                self._first_env(
                    [
                        "ROUTER_API_BASE",
                        "RETRIEVAL_EMBEDDING_API_BASE",
                        "RETRIEVAL_EMBEDDING_BASE",
                    ]
                )
            )
        if backend_value == "openai":
            return self._normalize_embedding_api_base(
                self._first_env(
                    [
                        "OPENAI_BASE_URL",
                        "OPENAI_API_BASE",
                        "RETRIEVAL_EMBEDDING_API_BASE",
                        "RETRIEVAL_EMBEDDING_BASE",
                    ]
                )
            )
        return self._normalize_embedding_api_base(
            self._first_env(
                [
                    "RETRIEVAL_EMBEDDING_API_BASE",
                    "RETRIEVAL_EMBEDDING_BASE",
                    "ROUTER_API_BASE",
                    "OPENAI_BASE_URL",
                    "OPENAI_API_BASE",
                ]
            )
        )

    def _resolve_embedding_api_key(self, backend: str) -> str:
        backend_value = (backend or "").strip().lower()
        if backend_value == "router":
            return self._first_env(
                ["ROUTER_API_KEY", "RETRIEVAL_EMBEDDING_API_KEY", "RETRIEVAL_EMBEDDING_KEY"]
            )
        if backend_value == "openai":
            return self._first_env(
                ["OPENAI_API_KEY", "RETRIEVAL_EMBEDDING_API_KEY", "RETRIEVAL_EMBEDDING_KEY"]
            )
        return self._first_env(
            ["RETRIEVAL_EMBEDDING_API_KEY", "RETRIEVAL_EMBEDDING_KEY", "ROUTER_API_KEY", "OPENAI_API_KEY"]
        )

    async def init_db(self):
        """Create tables if they don't exist, and run migrations for schema changes."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Migration: add migrated_to column if not present (for existing DBs)
            await conn.run_sync(self._migrate_add_migrated_to)
        await apply_pending_migrations(self.database_url)
        async with self.engine.begin() as conn:
            capabilities = await conn.run_sync(self._setup_index_infra)
            self._fts_available = capabilities.get("fts_available", False)
            self._vector_available = capabilities.get("vector_available", True)
        await self._bootstrap_indexes()

    @staticmethod
    def _migrate_add_migrated_to(connection):
        """Add migrated_to column to memories table if it doesn't exist."""
        from sqlalchemy import inspect

        inspector = inspect(connection)
        columns = [col["name"] for col in inspector.get_columns("memories")]
        if "migrated_to" not in columns:
            connection.execute(
                text("ALTER TABLE memories ADD COLUMN migrated_to INTEGER")
            )

    def _setup_index_infra(self, connection) -> Dict[str, bool]:
        """Create index tables and probe optional SQLite capabilities."""
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_memory_chunks_memory_id "
                "ON memory_chunks(memory_id)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_chunks_memory_chunk "
                "ON memory_chunks(memory_id, chunk_index)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_memory_chunks_vec_memory_id "
                "ON memory_chunks_vec(memory_id)"
            )
        )

        fts_available = False
        try:
            connection.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_chunks_fts "
                    "USING fts5("
                    "chunk_id UNINDEXED, "
                    "memory_id UNINDEXED, "
                    "chunk_text"
                    ")"
                )
            )
            fts_available = True
        except Exception:
            # SQLite builds without FTS5 support should continue with LIKE fallback.
            fts_available = False

        now = _utc_now_naive().isoformat()
        self._sync_set_index_meta(connection, "fts_available", "1" if fts_available else "0", now)
        self._sync_set_index_meta(
            connection, "vector_available", "1" if self._vector_available else "0", now
        )
        self._sync_set_index_meta(connection, "embedding_backend", self._embedding_backend, now)
        self._sync_set_index_meta(connection, "embedding_model", self._embedding_model, now)
        return {"fts_available": fts_available, "vector_available": self._vector_available}

    async def _bootstrap_indexes(self) -> None:
        """
        Build missing chunk/vector indexes for existing active memories.
        """
        async with self.session() as session:
            missing_query = (
                select(Memory.id)
                .outerjoin(MemoryChunk, Memory.id == MemoryChunk.memory_id)
                .where(Memory.deprecated == False)
                .group_by(Memory.id)
                .having(func.count(MemoryChunk.id) == 0)
            )
            missing_ids = [row[0] for row in (await session.execute(missing_query)).all()]
            reindexed = 0
            for memory_id in missing_ids:
                reindexed += await self._reindex_memory(session, memory_id)
            await self._set_index_meta(session, "bootstrap_indexed_memories", str(len(missing_ids)))
            await self._set_index_meta(session, "bootstrap_indexed_chunks", str(reindexed))

    @staticmethod
    def _sync_set_index_meta(connection, key: str, value: str, updated_at: str):
        connection.execute(
            text(
                "INSERT INTO index_meta(key, value, updated_at) "
                "VALUES (:key, :value, :updated_at) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = excluded.value, "
                "updated_at = excluded.updated_at"
            ),
            {"key": key, "value": value, "updated_at": updated_at},
        )

    async def _set_index_meta(
        self, session: AsyncSession, key: str, value: str
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO index_meta(key, value, updated_at) "
                "VALUES (:key, :value, :updated_at) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = excluded.value, "
                "updated_at = excluded.updated_at"
            ),
            {"key": key, "value": value, "updated_at": _utc_now_naive().isoformat()},
        )

    async def get_runtime_meta(self, key: str) -> Optional[str]:
        """Read a runtime metadata value from index_meta."""
        key_value = (key or "").strip()
        if not key_value:
            return None
        async with self.session() as session:
            result = await session.execute(
                select(IndexMeta.value).where(IndexMeta.key == key_value)
            )
            value = result.scalar_one_or_none()
            return str(value) if value is not None else None

    async def set_runtime_meta(self, key: str, value: str) -> None:
        """Persist a runtime metadata value into index_meta."""
        key_value = (key or "").strip()
        if not key_value:
            raise ValueError("key must not be empty")
        async with self.session() as session:
            await self._set_index_meta(session, key_value, value)

    async def upsert_memory_gist(
        self,
        *,
        memory_id: int,
        gist_text: str,
        source_hash: str,
        gist_method: str = "fallback",
        quality_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Create or update a gist record for a memory and content hash.

        Upsert key = (memory_id, source_content_hash). Existing rows are refreshed
        to avoid duplicate gists for unchanged content.
        """
        parsed_memory_id = int(memory_id)
        if parsed_memory_id <= 0:
            raise ValueError("memory_id must be a positive integer")

        gist_value = (gist_text or "").strip()
        if not gist_value:
            raise ValueError("gist_text must not be empty")

        source_hash_value = (source_hash or "").strip()
        if not source_hash_value:
            raise ValueError("source_hash must not be empty")

        method_value = (gist_method or "fallback").strip().lower() or "fallback"
        quality_value: Optional[float]
        if quality_score is None:
            quality_value = None
        else:
            try:
                quality_value = float(quality_score)
            except (TypeError, ValueError) as exc:
                raise ValueError("quality_score must be a float value or null") from exc

        async with self.session() as session:
            memory_row = await session.get(Memory, parsed_memory_id)
            if memory_row is None:
                raise ValueError(f"memory_id={parsed_memory_id} not found")
            now_value = _utc_now_naive()
            await session.execute(
                text(
                    "INSERT INTO memory_gists("
                    "memory_id, gist_text, source_content_hash, gist_method, quality_score, created_at"
                    ") VALUES ("
                    ":memory_id, :gist_text, :source_content_hash, :gist_method, :quality_score, :created_at"
                    ") ON CONFLICT(memory_id, source_content_hash) DO UPDATE SET "
                    "gist_text = excluded.gist_text, "
                    "gist_method = excluded.gist_method, "
                    "quality_score = excluded.quality_score, "
                    "created_at = excluded.created_at"
                ),
                {
                    "memory_id": parsed_memory_id,
                    "gist_text": gist_value,
                    "source_content_hash": source_hash_value,
                    "gist_method": method_value,
                    "quality_score": quality_value,
                    "created_at": now_value,
                },
            )
            row = (
                await session.execute(
                    select(MemoryGist)
                    .where(MemoryGist.memory_id == parsed_memory_id)
                    .where(MemoryGist.source_content_hash == source_hash_value)
                    .limit(1)
                )
            ).scalar_one()

            return {
                "id": row.id,
                "memory_id": row.memory_id,
                "gist_text": row.gist_text,
                "source_hash": row.source_content_hash,
                "gist_method": row.gist_method,
                "quality_score": row.quality_score,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }

    @staticmethod
    def _memory_gist_to_dict(row: MemoryGist) -> Dict[str, Any]:
        return {
            "id": row.id,
            "memory_id": row.memory_id,
            "gist_text": row.gist_text,
            "source_hash": row.source_content_hash,
            "gist_method": row.gist_method,
            "quality_score": row.quality_score,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    async def _get_latest_gists_map(
        self, session: AsyncSession, memory_ids: List[int]
    ) -> Dict[int, Dict[str, Any]]:
        normalized_ids: List[int] = []
        for item in memory_ids:
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                normalized_ids.append(parsed)
        normalized_ids = list(dict.fromkeys(normalized_ids))
        if not normalized_ids:
            return {}

        result = await session.execute(
            select(MemoryGist)
            .where(MemoryGist.memory_id.in_(normalized_ids))
            .order_by(
                MemoryGist.memory_id.asc(),
                MemoryGist.created_at.desc(),
                MemoryGist.id.desc(),
            )
        )
        mapping: Dict[int, Dict[str, Any]] = {}
        for row in result.scalars().all():
            if row.memory_id in mapping:
                continue
            mapping[row.memory_id] = self._memory_gist_to_dict(row)
        return mapping

    async def get_latest_memory_gist(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """Read latest gist row for a memory."""
        parsed_memory_id = int(memory_id)
        if parsed_memory_id <= 0:
            return None
        async with self.session() as session:
            gist_map = await self._get_latest_gists_map(session, [parsed_memory_id])
            return gist_map.get(parsed_memory_id)

    async def get_gist_stats(self) -> Dict[str, Any]:
        """Return compact observability stats for gist materialization."""
        async with self.session() as session:
            total_rows = int(
                (await session.execute(select(func.count(MemoryGist.id)))).scalar() or 0
            )
            total_distinct_memory_count = int(
                (
                    await session.execute(
                        select(func.count(func.distinct(MemoryGist.memory_id)))
                    )
                ).scalar()
                or 0
            )
            distinct_memory_count = int(
                (
                    await session.execute(
                        select(func.count(func.distinct(MemoryGist.memory_id)))
                        .join(Memory, Memory.id == MemoryGist.memory_id)
                        .where(Memory.deprecated == False)
                    )
                ).scalar()
                or 0
            )
            active_memory_count = int(
                (
                    await session.execute(
                        select(func.count(Memory.id)).where(Memory.deprecated == False)
                    )
                ).scalar()
                or 0
            )
            with_quality_count = int(
                (
                    await session.execute(
                        select(func.count(MemoryGist.id)).where(
                            MemoryGist.quality_score.isnot(None)
                        )
                    )
                ).scalar()
                or 0
            )
            avg_quality_raw = (
                await session.execute(
                    select(func.avg(MemoryGist.quality_score)).where(
                        MemoryGist.quality_score.isnot(None)
                    )
                )
            ).scalar()
            avg_quality = round(float(avg_quality_raw or 0.0), 3)
            latest_created_at = (
                await session.execute(select(func.max(MemoryGist.created_at)))
            ).scalar()
            method_rows = (
                await session.execute(
                    select(MemoryGist.gist_method, func.count(MemoryGist.id)).group_by(
                        MemoryGist.gist_method
                    )
                )
            ).all()

            method_breakdown: Dict[str, int] = {}
            for method_name, count_value in method_rows:
                method_key = str(method_name or "unknown")
                method_breakdown[method_key] = int(count_value or 0)

            coverage_ratio = (
                round(distinct_memory_count / active_memory_count, 3)
                if active_memory_count > 0
                else 0.0
            )

            return {
                "total_rows": total_rows,
                "distinct_memory_count": distinct_memory_count,
                "total_distinct_memory_count": total_distinct_memory_count,
                "active_memory_count": active_memory_count,
                "coverage_ratio": coverage_ratio,
                "quality_coverage_ratio": (
                    round(with_quality_count / total_rows, 3) if total_rows > 0 else 0.0
                ),
                "avg_quality_score": avg_quality,
                "method_breakdown": method_breakdown,
                "latest_created_at": latest_created_at.isoformat()
                if latest_created_at
                else None,
            }

    @staticmethod
    def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None

    @staticmethod
    def _normalize_db_datetime(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    @staticmethod
    def _content_snippet(content: str, limit: int = 200) -> str:
        text = (content or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    @staticmethod
    def _build_vitality_state_hash(
        *,
        memory_id: int,
        vitality_score: float,
        access_count: int,
        path_count: int,
        deprecated: bool,
    ) -> str:
        # Keep state hash stable across wall-clock time.
        # Dynamic fields like inactive_days would drift every few seconds and
        # make review-confirm flow fail with false stale_state mismatches.
        payload = (
            f"{int(memory_id)}|{round(float(vitality_score), 6)}|{int(access_count)}|"
            f"{int(path_count)}|{int(bool(deprecated))}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _join_api_url(base: str, endpoint: str) -> str:
        return f"{base.rstrip('/')}/{endpoint.lstrip('/')}"

    @staticmethod
    def _normalize_chat_api_base(base: str) -> str:
        normalized = (base or "").strip().rstrip("/")
        if not normalized:
            return ""

        lowered = normalized.lower()
        for suffix in ("/chat/completions", "/responses"):
            if lowered.endswith(suffix):
                return normalized[: -len(suffix)]
        return normalized

    @staticmethod
    def _normalize_embedding_api_base(base: str) -> str:
        normalized = (base or "").strip().rstrip("/")
        if not normalized:
            return ""
        lowered = normalized.lower()
        if lowered.endswith("/embeddings"):
            return normalized[: -len("/embeddings")]
        return normalized

    @staticmethod
    def _normalize_reranker_api_base(base: str) -> str:
        normalized = (base or "").strip().rstrip("/")
        if not normalized:
            return ""
        lowered = normalized.lower()
        if lowered.endswith("/rerank"):
            return normalized[: -len("/rerank")]
        return normalized

    @staticmethod
    def _append_degrade_reason(
        degrade_reasons: Optional[List[str]], reason: str
    ) -> None:
        if degrade_reasons is None or not reason:
            return
        if reason not in degrade_reasons:
            degrade_reasons.append(reason)

    @staticmethod
    def _collect_keyword_hits(
        source_text: str, token_set: set[str], keywords: List[str]
    ) -> List[str]:
        hits: List[str] = []
        for raw_keyword in keywords:
            keyword = (raw_keyword or "").strip().lower()
            if not keyword:
                continue
            # English keywords use word boundaries to avoid substring false positives.
            if re.fullmatch(r"[a-z0-9_ ]+", keyword):
                if re.search(rf"\b{re.escape(keyword)}\b", source_text):
                    hits.append(keyword)
                continue
            # CJK keywords use substring matching.
            if keyword in source_text:
                hits.append(keyword)
        # Keep deterministic order and remove duplicates.
        return list(dict.fromkeys(hits))

    def preprocess_query(self, query: str) -> Dict[str, Any]:
        """
        Normalize a raw query into a deterministic retrieval-friendly form.

        Returns:
            {
              "original_query": str,
              "normalized_query": str,
              "rewritten_query": str,
              "tokens": list[str],
              "changed": bool
            }
        """
        original = (query or "").strip()
        normalized = re.sub(r"\s+", " ", original)
        lowered = normalized.lower()
        tokens = re.findall(r"[a-z0-9_]+", lowered)
        deduped_tokens = list(dict.fromkeys(tokens))

        has_uri_hint = "://" in normalized or "/" in normalized
        has_non_ascii = any(ord(ch) > 127 for ch in normalized)
        if has_uri_hint or has_non_ascii:
            # Preserve raw query for path/URI and multilingual lookups.
            rewritten = normalized
        else:
            rewritten = " ".join(deduped_tokens[:16]) if deduped_tokens else normalized
        changed = rewritten != original
        return {
            "original_query": original,
            "normalized_query": normalized,
            "rewritten_query": rewritten,
            "tokens": deduped_tokens[:16],
            "changed": changed,
        }

    def classify_intent(
        self, query: str, rewritten_query: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lightweight intent classifier for retrieval strategy routing.

        Supported intents:
        - factual
        - exploratory
        - temporal
        - causal
        """
        source = " ".join(
            part.strip().lower() for part in [query or "", rewritten_query or ""] if part
        )
        source = re.sub(r"\s+", " ", source).strip()
        token_set = set(re.findall(r"[a-z0-9_]+", source))

        intent_keywords = {
            "temporal": [
                "when",
                "timeline",
                "history",
                "before",
                "after",
                "recent",
                "latest",
                "yesterday",
                "today",
                "昨天",
                "最近",
                "之前",
                "之后",
                "时间",
            ],
            "causal": [
                "why",
                "cause",
                "because",
                "reason",
                "root cause",
                "导致",
                "原因",
                "因果",
                "为什么",
            ],
            "exploratory": [
                "explore",
                "brainstorm",
                "ideas",
                "compare",
                "alternatives",
                "options",
                "tradeoff",
                "可能",
                "探索",
                "方案",
                "对比",
                "建议",
            ],
        }

        hits_by_intent: Dict[str, List[str]] = {
            intent: self._collect_keyword_hits(source, token_set, keywords)
            for intent, keywords in intent_keywords.items()
        }
        scores = {intent: len(hits) for intent, hits in hits_by_intent.items()}

        ranked = sorted(
            ((intent, score) for intent, score in scores.items() if score > 0),
            key=lambda item: item[1],
            reverse=True,
        )

        if not ranked:
            return {
                "intent": "factual",
                "strategy_template": "factual_high_precision",
                "method": "keyword_scoring_v2",
                "confidence": 0.55,
                "signals": ["default_factual"],
            }

        if len(ranked) > 1:
            top_intent, top_score = ranked[0]
            runner_intent, runner_score = ranked[1]

            if top_score == runner_score:
                ambiguous_signals: List[str] = []
                for intent, _ in ranked[:2]:
                    for hit in hits_by_intent.get(intent, [])[:2]:
                        ambiguous_signals.append(f"{intent}:{hit}")
                if not ambiguous_signals:
                    ambiguous_signals = ["ambiguous_keyword_overlap"]
                return {
                    "intent": "unknown",
                    "strategy_template": "default",
                    "method": "keyword_scoring_v2",
                    "confidence": 0.42,
                    "signals": ambiguous_signals,
                }

            # Conservative fallback: low-signal mixed-intent queries should not
            # force a single strategy template.
            if top_score <= 2 and (top_score - runner_score) <= 1:
                ambiguous_signals = []
                for intent in (top_intent, runner_intent):
                    for hit in hits_by_intent.get(intent, [])[:2]:
                        ambiguous_signals.append(f"{intent}:{hit}")
                if not ambiguous_signals:
                    ambiguous_signals = ["ambiguous_keyword_overlap"]
                return {
                    "intent": "unknown",
                    "strategy_template": "default",
                    "method": "keyword_scoring_v2",
                    "confidence": 0.46,
                    "signals": ambiguous_signals,
                }

        winner_intent = ranked[0][0]
        top_score = ranked[0][1]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        margin = max(0, top_score - runner_up)
        confidence = round(min(0.96, 0.58 + top_score * 0.07 + margin * 0.04), 2)

        strategy_by_intent = {
            "temporal": "temporal_time_filtered",
            "causal": "causal_wide_pool",
            "exploratory": "exploratory_high_recall",
        }
        winner_signals = [
            f"{winner_intent}:{hit}" for hit in hits_by_intent.get(winner_intent, [])[:5]
        ] or [f"{winner_intent}:keyword_signal"]

        return {
            "intent": winner_intent,
            "strategy_template": strategy_by_intent[winner_intent],
            "method": "keyword_scoring_v2",
            "confidence": confidence,
            "signals": winner_signals,
        }

    @staticmethod
    def _normalize_unit_score(value: Any) -> Optional[float]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None

        if math.isnan(numeric) or math.isinf(numeric):
            return None
        if 0.0 <= numeric <= 1.0:
            return numeric
        if -1.0 <= numeric <= 1.0:
            return (numeric + 1.0) / 2.0
        try:
            return 1.0 / (1.0 + math.exp(-numeric))
        except OverflowError:
            return 0.0 if numeric < 0 else 1.0

    @staticmethod
    def _extract_embedding_from_response(payload: Any) -> Optional[List[float]]:
        candidates: List[Any] = []
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list) and data:
                first_item = data[0]
                if isinstance(first_item, dict):
                    candidates.append(first_item.get("embedding"))
                elif isinstance(first_item, list):
                    candidates.append(first_item)

            candidates.append(payload.get("embedding"))

            result = payload.get("result")
            if isinstance(result, dict):
                candidates.append(result.get("embedding"))
                result_data = result.get("data")
                if isinstance(result_data, list) and result_data:
                    first_result = result_data[0]
                    if isinstance(first_result, dict):
                        candidates.append(first_result.get("embedding"))
                    elif isinstance(first_result, list):
                        candidates.append(first_result)

        for candidate in candidates:
            if not isinstance(candidate, list):
                continue
            try:
                return [float(v) for v in candidate]
            except (TypeError, ValueError):
                continue
        return None

    def _extract_rerank_scores(
        self, payload: Any, total_documents: int
    ) -> Dict[int, float]:
        if total_documents <= 0 or not isinstance(payload, dict):
            return {}

        rows: List[Any] = []
        for key in ("results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break

        if not rows:
            result = payload.get("result")
            if isinstance(result, dict):
                for key in ("results", "data"):
                    value = result.get(key)
                    if isinstance(value, list):
                        rows = value
                        break

        parsed_scores: Dict[int, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue

            raw_index = row.get("index", row.get("document_index"))
            try:
                idx = int(raw_index)
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= total_documents:
                continue

            raw_score = row.get("score")
            if raw_score is None:
                raw_score = row.get("relevance_score")

            normalized_score = self._normalize_unit_score(raw_score)
            if normalized_score is None:
                continue
            previous = parsed_scores.get(idx)
            if previous is None or normalized_score > previous:
                parsed_scores[idx] = normalized_score

        return parsed_scores

    async def _post_json(
        self,
        base: str,
        endpoint: str,
        payload: Dict[str, Any],
        api_key: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not base:
            return None

        url = self._join_api_url(base, endpoint)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key

        try:
            timeout = httpx.Timeout(self._remote_http_timeout_sec)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                parsed = response.json()
            if isinstance(parsed, dict):
                return parsed
            return {"data": parsed}
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError):
            return None

    async def _fetch_remote_embedding(
        self, content: str, degrade_reasons: Optional[List[str]] = None
    ) -> Optional[List[float]]:
        if self._embedding_backend not in {"router", "api", "openai"}:
            return None
        if not self._embedding_api_base or not self._embedding_model:
            self._append_degrade_reason(degrade_reasons, "embedding_config_missing")
            return None

        payload = {"model": self._embedding_model, "input": content}
        response = await self._post_json(
            self._embedding_api_base,
            "/embeddings",
            payload,
            self._embedding_api_key,
        )
        if response is None:
            self._append_degrade_reason(degrade_reasons, "embedding_request_failed")
            return None

        embedding = self._extract_embedding_from_response(response)
        if embedding is None:
            self._append_degrade_reason(degrade_reasons, "embedding_response_invalid")
        return embedding

    async def _get_rerank_scores(
        self,
        query: str,
        documents: List[str],
        degrade_reasons: Optional[List[str]] = None,
    ) -> Dict[int, float]:
        if not self._reranker_enabled or not documents:
            return {}
        if not self._reranker_api_base or not self._reranker_model:
            self._append_degrade_reason(degrade_reasons, "reranker_config_missing")
            return {}

        payload = {
            "model": self._reranker_model,
            "query": query,
            "documents": documents,
        }
        response = await self._post_json(
            self._reranker_api_base,
            "/rerank",
            payload,
            self._reranker_api_key,
        )
        if response is None:
            self._append_degrade_reason(degrade_reasons, "reranker_request_failed")
            return {}

        parsed_scores = self._extract_rerank_scores(response, len(documents))
        if not parsed_scores:
            self._append_degrade_reason(degrade_reasons, "reranker_response_invalid")
        return parsed_scores

    def _chunk_content(self, content: str) -> List[Tuple[int, int, int, str]]:
        if not content:
            return []

        chunks: List[Tuple[int, int, int, str]] = []
        total_len = len(content)
        start = 0
        index = 0

        while start < total_len:
            end = min(total_len, start + self._chunk_size)
            if end < total_len:
                split_newline = content.rfind("\n", start, end)
                split_space = content.rfind(" ", start, end)
                split_point = max(split_newline, split_space)
                if split_point > start + (self._chunk_size // 2):
                    end = split_point

            if end <= start:
                end = min(total_len, start + self._chunk_size)
                if end <= start:
                    break

            chunk_text = content[start:end]
            if chunk_text.strip():
                chunks.append((index, start, end, chunk_text))
                index += 1

            if end >= total_len:
                break
            start = max(end - self._chunk_overlap, start + 1)

        return chunks

    def _hash_embedding(self, content: str, dim: Optional[int] = None) -> List[float]:
        embed_dim = dim or self._embedding_dim
        vector = [0.0] * embed_dim

        normalized = re.sub(r"\s+", " ", content.strip().lower())
        tokens = re.findall(r"[a-z0-9_]+", normalized)
        if not tokens and normalized:
            tokens = list(normalized)

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for i in range(0, 8, 2):
                idx = digest[i] % embed_dim
                sign = -1.0 if (digest[i + 1] & 1) else 1.0
                weight = 1.0 + (digest[(i + 2) % len(digest)] / 255.0)
                vector[idx] += sign * weight

        norm = math.sqrt(sum(v * v for v in vector))
        if norm <= 0:
            return [0.0] * embed_dim
        return [v / norm for v in vector]

    async def _get_embedding(
        self,
        session: AsyncSession,
        content: str,
        degrade_reasons: Optional[List[str]] = None,
    ) -> List[float]:
        normalized = re.sub(r"\s+", " ", content.strip().lower())
        text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        cache_key = f"{self._embedding_model}:{text_hash}"

        cache_row = await session.get(EmbeddingCache, cache_key)
        if cache_row:
            try:
                embedding = json.loads(cache_row.embedding)
                if isinstance(embedding, list):
                    return [float(v) for v in embedding]
            except (TypeError, ValueError):
                pass

        embedding: Optional[List[float]] = None
        backend_value = (self._embedding_backend or "hash").strip().lower()

        if backend_value in {"router", "api", "openai"}:
            embedding = await self._fetch_remote_embedding(
                normalized, degrade_reasons=degrade_reasons
            )
            if embedding is None:
                self._append_degrade_reason(degrade_reasons, "embedding_fallback_hash")
        elif backend_value not in {
            "hash",
            "local",
            "none",
            "off",
            "disabled",
            "false",
            "0",
        }:
            self._append_degrade_reason(degrade_reasons, "embedding_backend_unsupported")

        if embedding is None:
            embedding = self._hash_embedding(normalized, self._embedding_dim)

        payload = json.dumps(embedding, separators=(",", ":"))

        if cache_row:
            cache_row.embedding = payload
            cache_row.updated_at = _utc_now_naive()
        else:
            session.add(
                EmbeddingCache(
                    cache_key=cache_key,
                    text_hash=text_hash,
                    model=self._embedding_model,
                    embedding=payload,
                )
            )
        return embedding

    async def _clear_memory_index(self, session: AsyncSession, memory_id: int) -> None:
        if self._fts_available:
            try:
                await session.execute(
                    text(
                        "DELETE FROM memory_chunks_fts "
                        "WHERE memory_id = :memory_id"
                    ),
                    {"memory_id": memory_id},
                )
            except Exception:
                # FTS virtual table might be unavailable at runtime after migrations.
                self._fts_available = False
                await self._set_index_meta(session, "fts_available", "0")

        await session.execute(
            delete(MemoryChunkVec).where(MemoryChunkVec.memory_id == memory_id)
        )
        await session.execute(
            delete(MemoryChunk).where(MemoryChunk.memory_id == memory_id)
        )

    async def _reindex_memory(self, session: AsyncSession, memory_id: int) -> int:
        await self._clear_memory_index(session, memory_id)

        memory_result = await session.execute(
            select(Memory).where(Memory.id == memory_id)
        )
        memory = memory_result.scalar_one_or_none()
        if not memory or memory.deprecated:
            await self._set_index_meta(session, "last_indexed_memory_id", str(memory_id))
            await self._set_index_meta(session, "last_indexed_at", _utc_now_naive().isoformat())
            return 0

        chunks = self._chunk_content(memory.content or "")
        if not chunks:
            await self._set_index_meta(session, "last_indexed_memory_id", str(memory_id))
            await self._set_index_meta(session, "last_indexed_at", _utc_now_naive().isoformat())
            return 0

        chunk_rows: List[MemoryChunk] = []
        for chunk_index, char_start, char_end, chunk_text in chunks:
            chunk_rows.append(
                MemoryChunk(
                    memory_id=memory_id,
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    char_start=char_start,
                    char_end=char_end,
                )
            )

        session.add_all(chunk_rows)
        await session.flush()

        vec_rows: List[MemoryChunkVec] = []
        for chunk in chunk_rows:
            if self._vector_available:
                embedding = await self._get_embedding(session, chunk.chunk_text)
                vec_rows.append(
                    MemoryChunkVec(
                        chunk_id=chunk.id,
                        memory_id=memory_id,
                        vector=json.dumps(embedding, separators=(",", ":")),
                        model=self._embedding_model,
                        dim=len(embedding),
                    )
                )
            if self._fts_available:
                try:
                    await session.execute(
                        text(
                            "INSERT INTO memory_chunks_fts("
                            "rowid, chunk_id, memory_id, chunk_text"
                            ") VALUES (:rowid, :chunk_id, :memory_id, :chunk_text)"
                        ),
                        {
                            "rowid": chunk.id,
                            "chunk_id": chunk.id,
                            "memory_id": memory_id,
                            "chunk_text": chunk.chunk_text,
                        },
                    )
                except Exception:
                    self._fts_available = False
                    await self._set_index_meta(session, "fts_available", "0")

        if vec_rows:
            session.add_all(vec_rows)
        await self._set_index_meta(session, "last_indexed_memory_id", str(memory_id))
        await self._set_index_meta(session, "last_indexed_at", _utc_now_naive().isoformat())
        return len(chunk_rows)

    @staticmethod
    def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
        if not v1 or not v2:
            return 0.0
        length = min(len(v1), len(v2))
        if length == 0:
            return 0.0
        return float(sum(v1[i] * v2[i] for i in range(length)))

    @staticmethod
    def _normalize_positive_int_ids(raw_ids: Optional[List[Any]]) -> List[int]:
        normalized_ids: List[int] = []
        seen_ids = set()
        if not raw_ids:
            return normalized_ids
        for item in raw_ids:
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                continue
            if parsed <= 0 or parsed in seen_ids:
                continue
            seen_ids.add(parsed)
            normalized_ids.append(parsed)
        return normalized_ids

    async def close(self):
        """Close the database connection."""
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self):
        """Get an async session context manager."""
        async with self.async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _reinforce_memory_access(
        self,
        session: AsyncSession,
        memory_ids: List[int],
    ) -> int:
        """
        Reinforce vitality when memories are read/retrieved.

        Reinforcement is intentionally bounded to avoid runaway scores.
        """
        normalized_ids = sorted(self._normalize_positive_int_ids(memory_ids))
        if not normalized_ids:
            return 0

        rows = await session.execute(
            select(Memory)
            .where(Memory.id.in_(normalized_ids))
            .where(Memory.deprecated == False)
        )
        memories = list(rows.scalars().all())
        if not memories:
            return 0

        now_value = _utc_now_naive()
        for memory in memories:
            current_access = max(0, int(memory.access_count or 0))
            next_access = current_access + 1
            diminishing_factor = 1.0 + math.log1p(next_access)
            boost = self._vitality_reinforce_delta / max(1.0, diminishing_factor)

            memory.access_count = next_access
            memory.last_accessed_at = now_value
            memory.vitality_score = min(
                self._vitality_max_score,
                max(0.0, float(memory.vitality_score or 1.0)) + boost,
            )
            session.add(memory)

        return len(memories)

    async def apply_vitality_decay(
        self,
        *,
        force: bool = False,
        reason: str = "runtime",
        reference_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Apply at most once-per-day vitality decay unless forced.
        """
        now_value = self._normalize_db_datetime(reference_time) or _utc_now_naive()
        day_key = now_value.strftime("%Y-%m-%d")
        last_decay_day_key = "vitality.last_decay_day.v1"

        async with self.session() as session:
            meta_result = await session.execute(
                select(IndexMeta.value).where(IndexMeta.key == last_decay_day_key)
            )
            meta_value = meta_result.scalar_one_or_none()
            last_decay_day = str(meta_value) if meta_value is not None else None
            if (not force) and last_decay_day == day_key:
                return {
                    "applied": False,
                    "reason": "already_applied_today",
                    "day": day_key,
                    "last_decay_day": last_decay_day,
                }

            result = await session.execute(
                select(Memory).where(Memory.deprecated == False)
            )
            memories = list(result.scalars().all())

            updated_count = 0
            low_vitality_count = 0
            for memory in memories:
                current_score = max(0.0, float(memory.vitality_score or 1.0))
                access_count = max(0, int(memory.access_count or 0))
                reference_dt = (
                    self._normalize_db_datetime(memory.last_accessed_at)
                    or self._normalize_db_datetime(memory.created_at)
                    or now_value
                )
                age_days = max(
                    0.0, (now_value - reference_dt).total_seconds() / 86400.0
                )
                resistance = 1.0 + min(2.0, math.log1p(access_count) * 0.35)
                effective_age_days = age_days / resistance
                decay_ratio = math.exp(
                    -effective_age_days / self._vitality_decay_half_life_days
                )
                next_score = max(
                    self._vitality_decay_min_score, current_score * decay_ratio
                )
                if next_score < current_score - 1e-9:
                    memory.vitality_score = next_score
                    session.add(memory)
                    updated_count += 1
                if next_score <= self._vitality_cleanup_threshold:
                    low_vitality_count += 1

            await self._set_index_meta(session, last_decay_day_key, day_key)
            await self._set_index_meta(
                session,
                "vitality.last_decay_at",
                now_value.isoformat(),
            )
            await self._set_index_meta(
                session,
                "vitality.last_decay_reason",
                (reason or "runtime").strip() or "runtime",
            )

            return {
                "applied": True,
                "day": day_key,
                "checked_memories": len(memories),
                "updated_memories": updated_count,
                "low_vitality_count": low_vitality_count,
                "half_life_days": self._vitality_decay_half_life_days,
                "threshold": self._vitality_cleanup_threshold,
            }

    async def get_vitality_cleanup_candidates(
        self,
        *,
        threshold: Optional[float] = None,
        inactive_days: Optional[float] = None,
        limit: int = 50,
        domain: Optional[str] = None,
        path_prefix: Optional[str] = None,
        memory_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Query low-vitality cleanup candidates for human review.
        """
        threshold_value = (
            self._vitality_cleanup_threshold
            if threshold is None
            else max(0.0, float(threshold))
        )
        inactive_days_value = (
            self._vitality_cleanup_inactive_days
            if inactive_days is None
            else max(0.0, float(inactive_days))
        )
        limit_value = max(1, min(500, int(limit)))
        domain_value = domain.strip() if isinstance(domain, str) and domain.strip() else None
        path_prefix_value = (
            str(path_prefix).strip()
            if isinstance(path_prefix, str) and str(path_prefix).strip()
            else None
        )

        filter_ids: Optional[List[int]] = None
        if memory_ids is not None:
            filter_ids = self._normalize_positive_int_ids(memory_ids)
            if not filter_ids:
                return {
                    "items": [],
                    "summary": {
                        "total_candidates": 0,
                        "threshold": threshold_value,
                        "inactive_days": inactive_days_value,
                    },
                }

        now_value = _utc_now_naive()
        inactive_cutoff = now_value - timedelta(days=inactive_days_value)
        reference_dt_expr = func.coalesce(Memory.last_accessed_at, Memory.created_at)
        query_started_at = time.perf_counter()

        async with self.session() as session:
            memory_query = (
                select(Memory)
                .where(Memory.deprecated == False)
                .where(Memory.vitality_score <= threshold_value)
            )
            if inactive_days_value > 0:
                memory_query = memory_query.where(
                    or_(
                        and_(
                            Memory.last_accessed_at.is_not(None),
                            Memory.last_accessed_at <= inactive_cutoff,
                        ),
                        and_(
                            Memory.last_accessed_at.is_(None),
                            Memory.created_at <= inactive_cutoff,
                        ),
                    )
                )
            if filter_ids is not None:
                memory_query = memory_query.where(Memory.id.in_(filter_ids))
            if domain_value or path_prefix_value:
                path_scope_conditions = [Path.memory_id == Memory.id]
                if domain_value:
                    path_scope_conditions.append(Path.domain == domain_value)
                if path_prefix_value:
                    path_scope_conditions.append(Path.path.startswith(path_prefix_value))
                memory_query = memory_query.where(
                    select(Path.memory_id).where(*path_scope_conditions).exists()
                )

            memory_query = (
                memory_query.order_by(
                    Memory.vitality_score.asc(),
                    reference_dt_expr.asc(),
                    Memory.id.asc(),
                ).limit(limit_value)
            )
            plan_details: List[str] = []
            used_memory_cleanup_index = False
            used_path_scope_index = False
            full_scan_targets: List[str] = []
            explain_degrade_reason: Optional[str] = None
            try:
                compiled_query = memory_query.compile(
                    dialect=self.engine.sync_engine.dialect,
                    compile_kwargs={"literal_binds": True},
                )
                explain_sql = text(f"EXPLAIN QUERY PLAN {compiled_query}")
                explain_rows = (await session.execute(explain_sql)).all()
                for row in explain_rows:
                    detail_text = ""
                    try:
                        detail_text = str(row[3] or "")
                    except Exception:
                        detail_text = ""
                    if not detail_text:
                        continue
                    plan_details.append(detail_text)
                    detail_upper = detail_text.upper()
                    if "IDX_MEMORIES_CLEANUP_" in detail_upper:
                        used_memory_cleanup_index = True
                    if "IDX_PATHS_MEMORY_DOMAIN_PATH" in detail_upper:
                        used_path_scope_index = True
                    if (
                        "SCAN " in detail_upper
                        and "USING INDEX" not in detail_upper
                        and "USING COVERING INDEX" not in detail_upper
                    ):
                        if "MEMORIES" in detail_upper:
                            full_scan_targets.append("memories")
                        elif "PATHS" in detail_upper:
                            full_scan_targets.append("paths")
            except Exception:
                explain_degrade_reason = "cleanup_explain_failed"

            memory_rows = list((await session.execute(memory_query)).scalars().all())
            query_ms = round((time.perf_counter() - query_started_at) * 1000.0, 3)
            if not memory_rows:
                return {
                    "items": [],
                    "summary": {
                        "total_candidates": 0,
                        "threshold": threshold_value,
                        "inactive_days": inactive_days_value,
                        "query_profile": {
                            "query_ms": query_ms,
                            "memory_rows_considered": 0,
                            "path_rows_loaded": 0,
                            "index_usage": {
                                "memory_cleanup_index": used_memory_cleanup_index,
                                "path_scope_index": used_path_scope_index,
                            },
                            "full_scan": bool(full_scan_targets),
                            "full_scan_targets": sorted(set(full_scan_targets)),
                            "plan_details": plan_details[:8],
                            "degraded": explain_degrade_reason is not None,
                            "degrade_reason": explain_degrade_reason,
                        },
                    },
                }

            all_memory_ids = [int(memory.id) for memory in memory_rows]
            path_count_rows = (
                await session.execute(
                    select(Path.memory_id, func.count(Path.memory_id))
                    .where(Path.memory_id.in_(all_memory_ids))
                    .group_by(Path.memory_id)
                )
            ).all()
            path_count_by_memory: Dict[int, int] = {
                int(memory_id): int(count or 0)
                for memory_id, count in path_count_rows
            }

            ranked_path_query = (
                select(
                    Path.memory_id.label("memory_id"),
                    Path.domain.label("domain"),
                    Path.path.label("path"),
                    func.row_number()
                    .over(
                        partition_by=Path.memory_id,
                        order_by=(Path.priority.asc(), Path.path.asc()),
                    )
                    .label("row_num"),
                )
                .where(Path.memory_id.in_(all_memory_ids))
            )
            if domain_value:
                ranked_path_query = ranked_path_query.where(Path.domain == domain_value)
            if path_prefix_value:
                ranked_path_query = ranked_path_query.where(
                    Path.path.startswith(path_prefix_value)
                )
            ranked_paths = ranked_path_query.subquery()
            top_path_rows = (
                await session.execute(
                    select(
                        ranked_paths.c.memory_id,
                        ranked_paths.c.domain,
                        ranked_paths.c.path,
                    ).where(ranked_paths.c.row_num == 1)
                )
            ).all()
            top_path_by_memory: Dict[int, Tuple[str, str]] = {
                int(row.memory_id): (str(row.domain), str(row.path))
                for row in top_path_rows
            }
            path_rows_loaded = len(path_count_rows) + len(top_path_rows)

            items: List[Dict[str, Any]] = []
            for memory in memory_rows:
                memory_id = int(memory.id)
                path_count = int(path_count_by_memory.get(memory_id, 0))
                top_path = top_path_by_memory.get(memory_id)
                if (domain_value or path_prefix_value) and top_path is None:
                    continue

                vitality_score = max(0.0, float(memory.vitality_score or 0.0))
                access_count = max(0, int(memory.access_count or 0))
                reference_dt = (
                    self._normalize_db_datetime(memory.last_accessed_at)
                    or self._normalize_db_datetime(memory.created_at)
                    or now_value
                )
                inactive_days_value_real = max(
                    0.0, (now_value - reference_dt).total_seconds() / 86400.0
                )

                reason_codes = ["low_vitality", "inactive"]
                if path_count == 0:
                    reason_codes.append("orphaned")

                state_hash = self._build_vitality_state_hash(
                    memory_id=memory_id,
                    vitality_score=vitality_score,
                    access_count=access_count,
                    path_count=path_count,
                    deprecated=bool(memory.deprecated),
                )

                items.append(
                    {
                        "memory_id": memory_id,
                        "uri": (
                            f"{top_path[0]}://{top_path[1]}" if top_path else None
                        ),
                        "path_count": path_count,
                        "vitality_score": round(vitality_score, 6),
                        "access_count": access_count,
                        "last_accessed_at": (
                            self._normalize_db_datetime(memory.last_accessed_at).isoformat()
                            if memory.last_accessed_at is not None
                            else None
                        ),
                        "inactive_days": round(inactive_days_value_real, 3),
                        "content_snippet": self._content_snippet(memory.content),
                        "reason_codes": reason_codes,
                        "can_delete": path_count == 0 or bool(memory.deprecated),
                        "state_hash": state_hash,
                    }
                )

            return {
                "items": items,
                "summary": {
                    "total_candidates": len(items),
                    "threshold": threshold_value,
                    "inactive_days": inactive_days_value,
                    "query_profile": {
                        "query_ms": query_ms,
                        "memory_rows_considered": len(memory_rows),
                        "path_rows_loaded": path_rows_loaded,
                        "index_usage": {
                            "memory_cleanup_index": used_memory_cleanup_index,
                            "path_scope_index": used_path_scope_index,
                        },
                        "full_scan": bool(full_scan_targets),
                        "full_scan_targets": sorted(set(full_scan_targets)),
                        "plan_details": plan_details[:8],
                        "degraded": explain_degrade_reason is not None,
                        "degrade_reason": explain_degrade_reason,
                    },
                },
            }

    async def get_vitality_stats(self) -> Dict[str, Any]:
        """Aggregate vitality stats for maintenance observability."""
        threshold_value = self._vitality_cleanup_threshold
        async with self.session() as session:
            total = int(
                (
                    await session.execute(
                        select(func.count(Memory.id)).where(Memory.deprecated == False)
                    )
                ).scalar()
                or 0
            )
            avg_score = float(
                (
                    await session.execute(
                        select(func.avg(Memory.vitality_score)).where(
                            Memory.deprecated == False
                        )
                    )
                ).scalar()
                or 0.0
            )
            min_score = float(
                (
                    await session.execute(
                        select(func.min(Memory.vitality_score)).where(
                            Memory.deprecated == False
                        )
                    )
                ).scalar()
                or 0.0
            )
            max_score = float(
                (
                    await session.execute(
                        select(func.max(Memory.vitality_score)).where(
                            Memory.deprecated == False
                        )
                    )
                ).scalar()
                or 0.0
            )
            low_count = int(
                (
                    await session.execute(
                        select(func.count(Memory.id))
                        .where(Memory.deprecated == False)
                        .where(Memory.vitality_score <= threshold_value)
                    )
                ).scalar()
                or 0
            )

        return {
            "total_memories": total,
            "avg_score": round(avg_score, 6),
            "min_score": round(min_score, 6),
            "max_score": round(max_score, 6),
            "low_vitality_count": low_count,
            "threshold": threshold_value,
        }

    # =========================================================================
    # Read Operations
    # =========================================================================

    async def get_memory_by_path(
        self, path: str, domain: str = "core"
    ) -> Optional[Dict[str, Any]]:
        """
        Get a memory by its path.

        Args:
            path: The path to look up
            domain: The domain/namespace (e.g., "core", "writer", "game")

        Returns:
            Memory dict with id, content, priority, disclosure, created_at
            or None if not found
        """
        async with self.session() as session:
            result = await session.execute(
                select(Memory, Path)
                .join(Path, Memory.id == Path.memory_id)
                .where(Path.domain == domain)
                .where(Path.path == path)
                .where(Memory.deprecated == False)
            )
            row = result.first()

            if not row:
                return None

            memory, path_obj = row
            await self._reinforce_memory_access(session, [memory.id])
            gist_map = await self._get_latest_gists_map(session, [memory.id])
            gist = gist_map.get(memory.id) or {}
            return {
                "id": memory.id,
                "content": memory.content,
                "priority": path_obj.priority,  # From Path
                "disclosure": path_obj.disclosure,  # From Path
                "deprecated": memory.deprecated,
                "created_at": memory.created_at.isoformat()
                if memory.created_at
                else None,
                "domain": path_obj.domain,
                "path": path_obj.path,
                "gist_text": gist.get("gist_text"),
                "gist_method": gist.get("gist_method"),
                "gist_quality": gist.get("quality_score"),
                "gist_source_hash": gist.get("source_hash"),
            }

    async def get_memory_by_id(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a memory by its ID (including deprecated ones).

        Args:
            memory_id: The memory ID

        Returns:
            Memory dict or None if not found
        """
        async with self.session() as session:
            result = await session.execute(select(Memory).where(Memory.id == memory_id))
            memory = result.scalar_one_or_none()

            if not memory:
                return None

            if not bool(memory.deprecated):
                await self._reinforce_memory_access(session, [memory.id])

            # Get all paths pointing to this memory (with domain info)
            paths_result = await session.execute(
                select(Path.domain, Path.path).where(Path.memory_id == memory_id)
            )
            # Return as list of "domain://path" URIs
            paths = [f"{row[0]}://{row[1]}" for row in paths_result.all()]

            return {
                "id": memory.id,
                "content": memory.content,
                # Priority/Disclosure removed as they are path-dependent
                "deprecated": memory.deprecated,
                "migrated_to": memory.migrated_to,
                "created_at": memory.created_at.isoformat()
                if memory.created_at
                else None,
                "paths": paths,
            }

    async def get_children(
        self, memory_id: Optional[int] = None, domain: str = "core"
    ) -> List[Dict[str, Any]]:
        """
        Get direct children of a memory node.

        When memory_id is given, finds ALL paths (aliases) pointing to that
        memory across all domains, then collects direct children under each.
        This models human associative recall: once you reach a memory, the
        sub-memories depend on WHAT it IS, not WHICH path you used to get here.

        When memory_id is None (virtual root), returns root-level paths
        (paths with no '/') in the given domain.

        Args:
            memory_id: The memory ID to find children for.
                       If None, returns domain root elements.
            domain: Only used when memory_id is None (root browsing).

        Returns:
            List of child memories (deduplicated by domain+path),
            sorted by priority then path.
        """
        async with self.session() as session:
            if memory_id is None:
                # Virtual root: return paths with no slashes in the given domain
                query = (
                    select(Memory, Path)
                    .join(Path, Memory.id == Path.memory_id)
                    .where(Path.domain == domain)
                    .where(Memory.deprecated == False)
                    .where(Path.path.not_like("%/%"))
                    .order_by(Path.priority.asc(), Path.path)
                )

                result = await session.execute(query)
                rows = result.all()
                gist_map = await self._get_latest_gists_map(
                    session, [memory.id for memory, _ in rows]
                )

                children = []
                for memory, path_obj in rows:
                    gist = gist_map.get(memory.id) or {}
                    children.append(
                        {
                            "domain": path_obj.domain,
                            "path": path_obj.path,
                            "name": path_obj.path.rsplit("/", 1)[-1],
                            "content_snippet": memory.content[:100] + "..."
                            if len(memory.content) > 100
                            else memory.content,
                            "priority": path_obj.priority,
                            "disclosure": path_obj.disclosure,
                            "gist_text": gist.get("gist_text"),
                            "gist_method": gist.get("gist_method"),
                            "gist_quality": gist.get("quality_score"),
                            "gist_source_hash": gist.get("source_hash"),
                        }
                    )

                return children

            # --- memory_id provided: find children across all aliases ---

            # 1. Find all paths pointing to this memory
            parent_paths_result = await session.execute(
                select(Path.domain, Path.path).where(Path.memory_id == memory_id)
            )
            parent_paths = parent_paths_result.all()

            if not parent_paths:
                return []

            # 2. Build OR conditions for children under each parent path
            child_conditions = []
            for parent_domain, parent_path in parent_paths:
                safe_parent = (
                    parent_path.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                safe_prefix = f"{safe_parent}/"

                child_conditions.append(
                    and_(
                        Path.domain == parent_domain,
                        Path.path.like(f"{safe_prefix}%", escape="\\"),
                        Path.path.not_like(f"{safe_prefix}%/%", escape="\\"),
                    )
                )

            # 3. Query all children in one shot
            query = (
                select(Memory, Path)
                .join(Path, Memory.id == Path.memory_id)
                .where(Memory.deprecated == False)
                .where(or_(*child_conditions))
                .order_by(Path.priority.asc(), Path.path)
            )

            result = await session.execute(query)
            rows = result.all()
            gist_map = await self._get_latest_gists_map(
                session, [memory.id for memory, _ in rows]
            )

            # 4. Deduplicate by (domain, path)
            seen = set()
            children = []
            for memory, path_obj in rows:
                key = (path_obj.domain, path_obj.path)
                if key in seen:
                    continue
                seen.add(key)
                gist = gist_map.get(memory.id) or {}

                children.append(
                    {
                        "domain": path_obj.domain,
                        "path": path_obj.path,
                        "name": path_obj.path.rsplit("/", 1)[-1],
                        "content_snippet": memory.content[:100] + "..."
                        if len(memory.content) > 100
                        else memory.content,
                        "priority": path_obj.priority,
                        "disclosure": path_obj.disclosure,
                        "gist_text": gist.get("gist_text"),
                        "gist_method": gist.get("gist_method"),
                        "gist_quality": gist.get("quality_score"),
                        "gist_source_hash": gist.get("source_hash"),
                    }
                )

            return children

    async def get_all_paths(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all paths with their memory info.

        Args:
            domain: If specified, only return paths in this domain.
                    If None, return paths from all domains.

        Returns:
            List of path info dicts
        """
        async with self.session() as session:
            query = (
                select(Path, Memory)
                .join(Memory, Path.memory_id == Memory.id)
                .where(Memory.deprecated == False)
            )

            if domain is not None:
                query = query.where(Path.domain == domain)

            query = query.order_by(Path.domain, Path.path)
            result = await session.execute(query)

            paths = []
            for path_obj, memory in result.all():
                paths.append(
                    {
                        "domain": path_obj.domain,
                        "path": path_obj.path,
                        "uri": f"{path_obj.domain}://{path_obj.path}",
                        "name": path_obj.path.rsplit("/", 1)[
                            -1
                        ],  # Last segment of path
                        "priority": path_obj.priority,  # From Path
                        "memory_id": memory.id,
                    }
                )

            return paths

    # =========================================================================
    # Create Operations
    # =========================================================================

    async def create_memory(
        self,
        parent_path: str,
        content: str,
        priority: int,
        title: Optional[str] = None,
        disclosure: Optional[str] = None,
        domain: str = "core",
        index_now: bool = True,
    ) -> Dict[str, Any]:
        """
        Create a new memory under a parent path.

        Args:
            parent_path: Parent path (e.g. "memory-palace/salem")
            content: Memory content
            priority: Retrieval priority (lower = higher priority, min 0)
            title: Optional path segment name. If None, auto-assigns numeric ID.
                   This becomes the last segment of the path, NOT stored in memories table.
            disclosure: When to expand this memory
            domain: The domain/namespace (e.g., "core", "writer", "game")

        Returns:
            Created memory info with full path
        """
        async with self.session() as session:
            # Validate parent exists (if specified)
            if parent_path:
                parent_exists = await session.execute(
                    select(Path)
                    .where(Path.domain == domain)
                    .where(Path.path == parent_path)
                )
                if not parent_exists.scalar_one_or_none():
                    raise ValueError(
                        f"Parent '{domain}://{parent_path}' does not exist. "
                        f"Create the parent first, or use '{domain}://' as root."
                    )

            # Determine the final path
            if title:
                # Use provided title as path segment
                final_path = f"{parent_path}/{title}" if parent_path else title
            else:
                # Auto-assign numeric ID
                next_num = await self._get_next_numeric_id(session, parent_path, domain)
                final_path = (
                    f"{parent_path}/{next_num}" if parent_path else str(next_num)
                )

            # Check if path already exists in this domain
            existing = await session.execute(
                select(Path).where(Path.domain == domain).where(Path.path == final_path)
            )
            if existing.scalar_one_or_none():
                raise ValueError(f"Path '{domain}://{final_path}' already exists")

            # Create memory (content only, no title stored)
            memory = Memory(content=content)
            session.add(memory)
            await session.flush()  # Get the ID

            # Create path (with metadata)
            path_obj = Path(
                domain=domain,
                path=final_path,
                memory_id=memory.id,
                priority=priority,
                disclosure=disclosure,
            )
            session.add(path_obj)
            indexed_chunks = 0
            if index_now:
                indexed_chunks = await self._reindex_memory(session, memory.id)

            return {
                "id": memory.id,
                "domain": domain,
                "path": final_path,
                "uri": f"{domain}://{final_path}",
                "priority": priority,
                "indexed_chunks": indexed_chunks,
                "index_pending": not index_now,
                "index_targets": [memory.id],
            }

    async def _get_next_numeric_id(
        self, session: AsyncSession, parent_path: str, domain: str = "core"
    ) -> int:
        """Get the next numeric ID for auto-naming under a parent path in a domain."""
        prefix = f"{parent_path}/" if parent_path else ""

        # Prepare LIKE clause with escaping if parent_path exists
        if parent_path:
            safe_parent = (
                parent_path.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            like_pattern = f"{safe_parent}/%"
            like_clause = Path.path.like(like_pattern, escape="\\")
        else:
            like_clause = Path.path.like("%")

        result = await session.execute(
            select(Path.path).where(Path.domain == domain).where(like_clause)
        )

        max_num = 0
        for (path,) in result.all():
            remainder = path[len(prefix) :] if prefix else path
            # Only consider direct children
            if "/" not in remainder:
                try:
                    num = int(remainder)
                    max_num = max(max_num, num)
                except ValueError:
                    pass

        return max_num + 1

    # =========================================================================
    # Update Operations
    # =========================================================================

    async def update_memory(
        self,
        path: str,
        content: Optional[str] = None,
        priority: Optional[int] = None,
        disclosure: Optional[str] = None,
        domain: str = "core",
        index_now: bool = True,
    ) -> Dict[str, Any]:
        """
        Update a memory (creates new version, deprecates old, repoints path).

        Args:
            path: Path to update
            content: New content (None = keep old)
            priority: New priority (None = keep old)
            disclosure: New disclosure (None = keep old)
            domain: The domain/namespace (e.g., "core", "writer", "game")

        Returns:
            Updated memory info including old and new memory IDs
        """
        if content is None and priority is None and disclosure is None:
            raise ValueError(
                f"No update fields provided for '{domain}://{path}'. "
                "At least one of content, priority, or disclosure must be set."
            )

        async with self.session() as session:
            # 1. Get current memory and path
            result = await session.execute(
                select(Memory, Path)
                .join(Path, Memory.id == Path.memory_id)
                .where(Path.domain == domain)
                .where(Path.path == path)
                .where(Memory.deprecated == False)
            )
            row = result.first()

            if not row:
                raise ValueError(
                    f"Path '{domain}://{path}' not found or memory is deprecated"
                )

            old_memory, path_obj = row
            old_id = old_memory.id

            # Update Path Metadata
            if priority is not None:
                path_obj.priority = priority
            if disclosure is not None:
                path_obj.disclosure = disclosure

            new_memory_id = old_id
            index_targets: List[int] = []

            if content is not None:
                # Content update requested: ALWAYS create a new version.
                #
                # Previously this checked `content != old_memory.content` and
                # silently skipped when content was identical.  This caused a
                # TOCTOU bug: the MCP layer reads content in session A, computes
                # the replacement, then passes it here (session B).  If the DB
                # content was already updated between the two reads (or if the
                # MCP transport subtly normalised whitespace), the equality
                # check would pass, no new version was created, yet "Success"
                # was returned to the caller.
                #
                # The MCP layer is responsible for validating the change; the
                # DB layer should unconditionally persist whatever it receives.
                new_memory = Memory(content=content)
                session.add(new_memory)
                await session.flush()
                new_memory_id = new_memory.id

                # Mark old as deprecated and set migration pointer to new version
                await session.execute(
                    update(Memory)
                    .where(Memory.id == old_id)
                    .values(deprecated=True, migrated_to=new_memory.id)
                )

                # Repoint ALL paths pointing to the old memory to the new memory
                # This ensures aliases stay in sync with the content update
                await session.execute(
                    update(Path)
                    .where(Path.memory_id == old_id)
                    .values(memory_id=new_memory.id)
                )

                await self._clear_memory_index(session, old_id)
                index_targets = [new_memory.id]
                if index_now:
                    await self._reindex_memory(session, new_memory.id)

            if content is None:
                # Only metadata changed, explicitly add the path object for flush
                session.add(path_obj)

            return {
                "domain": domain,
                "path": path,
                "uri": f"{domain}://{path}",
                "old_memory_id": old_id,
                "new_memory_id": new_memory_id,
                "index_pending": bool(index_targets) and not index_now,
                "index_targets": index_targets,
            }

    async def rollback_to_memory(
        self,
        path: str,
        target_memory_id: int,
        domain: str = "core",
        index_now: bool = True,
    ) -> Dict[str, Any]:
        """
        Rollback a path to point to a specific memory version.

        Args:
            path: Path to rollback
            target_memory_id: Memory ID to restore to
            domain: The domain/namespace (e.g., "core", "writer", "game")

        Returns:
            Rollback result info
        """
        async with self.session() as session:
            # 1. Get current memory_id
            result = await session.execute(
                select(Path.memory_id)
                .where(Path.domain == domain)
                .where(Path.path == path)
            )
            current_id = result.scalar_one_or_none()

            if current_id is None:
                raise ValueError(f"Path '{domain}://{path}' not found")

            # 2. Verify target memory exists
            target = await session.execute(
                select(Memory).where(Memory.id == target_memory_id)
            )
            if not target.scalar_one_or_none():
                raise ValueError(f"Target memory ID {target_memory_id} not found")

            # 3. Mark current as deprecated and point to restored version
            await session.execute(
                update(Memory)
                .where(Memory.id == current_id)
                .values(deprecated=True, migrated_to=target_memory_id)
            )

            # 4. Un-deprecate target and clear its migration pointer (it's the active version now)
            await session.execute(
                update(Memory)
                .where(Memory.id == target_memory_id)
                .values(deprecated=False, migrated_to=None)
            )

            # 5. Repoint ALL paths that were pointing to the old memory
            await session.execute(
                update(Path)
                .where(Path.memory_id == current_id)
                .values(memory_id=target_memory_id)
            )

            await self._clear_memory_index(session, current_id)
            if index_now:
                await self._reindex_memory(session, target_memory_id)

            return {
                "domain": domain,
                "path": path,
                "uri": f"{domain}://{path}",
                "old_memory_id": current_id,
                "restored_memory_id": target_memory_id,
                "index_pending": not index_now,
                "index_targets": [target_memory_id],
            }

    async def reindex_memory(
        self, memory_id: int, reason: str = "manual"
    ) -> Dict[str, Any]:
        """Rebuild retrieval index rows for one memory."""
        if int(memory_id) <= 0:
            raise ValueError("memory_id must be a positive integer.")

        target_id = int(memory_id)
        indexed_chunks = 0
        exists = False
        deprecated = False
        now_iso = _utc_now_naive().isoformat()

        async with self.session() as session:
            memory_result = await session.execute(
                select(Memory).where(Memory.id == target_id)
            )
            memory = memory_result.scalar_one_or_none()
            exists = memory is not None
            deprecated = bool(memory.deprecated) if memory else False

            indexed_chunks = await self._reindex_memory(session, target_id)
            await self._set_index_meta(session, "last_reindex_reason", reason or "manual")
            await self._set_index_meta(session, "last_reindex_request_memory_id", str(target_id))
            await self._set_index_meta(session, "last_reindex_request_at", now_iso)

        return {
            "memory_id": target_id,
            "indexed_chunks": indexed_chunks,
            "exists": exists,
            "deprecated": deprecated,
            "indexed_at": now_iso,
            "reason": reason or "manual",
        }

    async def rebuild_index(
        self, include_deprecated: bool = False, reason: str = "manual"
    ) -> Dict[str, Any]:
        """Rebuild retrieval index rows for all selected memories."""
        async with self.session() as session:
            query = select(Memory.id).order_by(Memory.id.asc())
            if not include_deprecated:
                query = query.where(Memory.deprecated == False)
            rows = await session.execute(query)
            memory_ids = [int(memory_id) for (memory_id,) in rows.all()]

        total_chunks = 0
        failure_items: List[Dict[str, Any]] = []
        for target_id in memory_ids:
            try:
                item = await self.reindex_memory(
                    memory_id=target_id,
                    reason=f"rebuild:{reason or 'manual'}",
                )
                total_chunks += int(item.get("indexed_chunks", 0) or 0)
            except Exception as exc:
                failure_items.append({"memory_id": target_id, "error": str(exc)})

        finished_at = _utc_now_naive().isoformat()
        async with self.session() as session:
            await self._set_index_meta(session, "last_rebuild_at", finished_at)
            await self._set_index_meta(session, "last_rebuild_reason", reason or "manual")
            await self._set_index_meta(session, "last_rebuild_memories", str(len(memory_ids)))
            await self._set_index_meta(session, "last_rebuild_chunks", str(total_chunks))
            await self._set_index_meta(session, "last_rebuild_failures", str(len(failure_items)))

        return {
            "requested_memories": len(memory_ids),
            "indexed_chunks": total_chunks,
            "failure_count": len(failure_items),
            "failures": failure_items,
            "include_deprecated": bool(include_deprecated),
            "reason": reason or "manual",
            "finished_at": finished_at,
        }

    # =========================================================================
    # Path Operations
    # =========================================================================

    async def add_path(
        self,
        new_path: str,
        target_path: str,
        new_domain: str = "core",
        target_domain: str = "core",
        priority: int = 0,
        disclosure: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create an alias path pointing to the same memory as target_path.

        Args:
            new_path: New path to create
            target_path: Existing path to alias
            new_domain: Domain for the new path
            target_domain: Domain of the target path
            priority: Priority for this new alias
            disclosure: Disclosure trigger for this new alias

        Returns:
            Created alias info
        """
        async with self.session() as session:
            # Get target memory_id
            result = await session.execute(
                select(Path.memory_id)
                .where(Path.domain == target_domain)
                .where(Path.path == target_path)
            )
            target_id = result.scalar_one_or_none()

            if target_id is None:
                raise ValueError(
                    f"Target path '{target_domain}://{target_path}' not found"
                )

            # Validate parent of new_path exists
            if "/" in new_path:
                parent_path = new_path.rsplit("/", 1)[0]
                parent_exists = await session.execute(
                    select(Path)
                    .where(Path.domain == new_domain)
                    .where(Path.path == parent_path)
                )
                if not parent_exists.scalar_one_or_none():
                    raise ValueError(
                        f"Parent '{new_domain}://{parent_path}' does not exist. "
                        f"Create the parent first, or use a shallower alias path."
                    )

            # Check if new path exists in the new domain
            existing = await session.execute(
                select(Path)
                .where(Path.domain == new_domain)
                .where(Path.path == new_path)
            )
            if existing.scalar_one_or_none():
                raise ValueError(f"Path '{new_domain}://{new_path}' already exists")

            # Create alias
            path_obj = Path(
                domain=new_domain,
                path=new_path,
                memory_id=target_id,
                priority=priority,
                disclosure=disclosure,
            )
            session.add(path_obj)

            return {
                "new_uri": f"{new_domain}://{new_path}",
                "target_uri": f"{target_domain}://{target_path}",
                "memory_id": target_id,
            }

    async def remove_path(self, path: str, domain: str = "core") -> Dict[str, Any]:
        """
        Remove a path (but not the memory it points to).

        Refuses to delete a path that still has children. The caller must
        delete all child paths first before removing the parent.

        Args:
            path: Path to remove
            domain: The domain/namespace (e.g., "core", "writer", "game")

        Returns:
            Removal info

        Raises:
            ValueError: If the path has children or does not exist
        """
        async with self.session() as session:
            result = await session.execute(
                select(Path).where(Path.domain == domain).where(Path.path == path)
            )
            path_obj = result.scalar_one_or_none()

            if not path_obj:
                raise ValueError(f"Path '{domain}://{path}' not found")

            # Block deletion if child paths exist
            safe_path = (
                path.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            child_prefix = f"{safe_path}/"
            child_result = await session.execute(
                select(func.count())
                .select_from(Path)
                .where(Path.domain == domain)
                .where(Path.path.like(f"{child_prefix}%", escape="\\"))
            )
            child_count = child_result.scalar()

            if child_count > 0:
                # Fetch up to 5 child URIs for a helpful error message
                sample_result = await session.execute(
                    select(Path.path)
                    .where(Path.domain == domain)
                    .where(Path.path.like(f"{child_prefix}%", escape="\\"))
                    .order_by(Path.path)
                    .limit(5)
                )
                sample_paths = [
                    f"{domain}://{row[0]}" for row in sample_result.all()
                ]
                listing = ", ".join(sample_paths)
                suffix = f" (and {child_count - 5} more)" if child_count > 5 else ""
                raise ValueError(
                    f"Cannot delete '{domain}://{path}': "
                    f"it still has {child_count} child path(s). "
                    f"Delete children first: {listing}{suffix}"
                )

            memory_id = path_obj.memory_id
            await session.delete(path_obj)

            return {"removed_uri": f"{domain}://{path}", "memory_id": memory_id}

    async def restore_path(
        self,
        path: str,
        domain: str,
        memory_id: int,
        priority: int = 0,
        disclosure: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Restore a path pointing to a specific memory ID (used for rollback).

        Args:
            path: Path to restore
            domain: Domain
            memory_id: Memory ID to point to
            priority: Path priority
            disclosure: Path disclosure

        Returns:
            Restored path info
        """
        async with self.session() as session:
            # Check if memory exists
            memory_result = await session.execute(
                select(Memory).where(Memory.id == memory_id)
            )
            if not memory_result.scalar_one_or_none():
                raise ValueError(f"Memory ID {memory_id} not found")

            # Ensure memory is not deprecated (un-deprecate if needed)
            # This is critical for rollback: if we restore a path to a memory that was
            # deprecated (e.g. by a subsequent update), we must make it visible again.
            await session.execute(
                update(Memory).where(Memory.id == memory_id).values(deprecated=False)
            )

            # Check if path already exists (collision)
            existing = await session.execute(
                select(Path).where(Path.domain == domain).where(Path.path == path)
            )
            if existing.scalar_one_or_none():
                raise ValueError(f"Path '{domain}://{path}' already exists")

            # Create path
            path_obj = Path(
                domain=domain,
                path=path,
                memory_id=memory_id,
                priority=priority,
                disclosure=disclosure,
            )
            session.add(path_obj)

            return {"uri": f"{domain}://{path}", "memory_id": memory_id}

    # =========================================================================
    # Search Operations
    # =========================================================================

    @staticmethod
    def _escape_like_pattern(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _make_snippet(text_content: str, query: str, around: int = 50) -> str:
        if not text_content:
            return ""
        if not query:
            return text_content[:120] + ("..." if len(text_content) > 120 else "")

        text_lower = text_content.lower()
        query_lower = query.lower()
        pos = text_lower.find(query_lower)
        if pos < 0:
            return text_content[:120] + ("..." if len(text_content) > 120 else "")

        start = max(0, pos - around)
        end = min(len(text_content), pos + len(query) + around)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text_content) else ""
        return f"{prefix}{text_content[start:end]}{suffix}"

    @staticmethod
    def _like_text_score(query: str, chunk_text: str, path: str) -> float:
        if not query:
            return 0.0
        q = query.lower()
        score = 0.0
        if q in (chunk_text or "").lower():
            score += 0.7
        if q in (path or "").lower():
            score += 0.3
        return min(score, 1.0)

    @staticmethod
    def _normalize_guard_action(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        action = value.strip().upper()
        if action in {"ADD", "UPDATE", "NOOP", "DELETE"}:
            return action
        return None

    @staticmethod
    def _extract_chat_message_text(payload: Dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return ""
        message = first_choice.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text_content = item.get("text")
                if isinstance(text_content, str) and text_content.strip():
                    parts.append(text_content.strip())
            return "\n".join(parts).strip()
        return ""

    @staticmethod
    def _parse_chat_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
        candidate = (raw_text or "").strip()
        if not candidate:
            return None

        parse_candidates = [candidate]
        if candidate.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)
            parse_candidates.append(stripped.strip())

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            parse_candidates.append(candidate[start : end + 1])

        for item in parse_candidates:
            try:
                parsed = json.loads(item)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    async def generate_compact_gist(
        self,
        *,
        summary: str,
        max_points: int = 3,
        max_chars: int = 280,
        degrade_reasons: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        source = (summary or "").strip()
        if not source:
            return None

        llm_enabled = self._env_bool(
            "COMPACT_GIST_LLM_ENABLED",
            self._env_bool("WRITE_GUARD_LLM_ENABLED", False),
        )
        if not llm_enabled:
            self._append_degrade_reason(degrade_reasons, "compact_gist_llm_disabled")
            return None

        llm_api_base = self._first_env(
            [
                "COMPACT_GIST_LLM_API_BASE",
                "WRITE_GUARD_LLM_API_BASE",
                "LLM_RESPONSES_URL",
                "OPENAI_BASE_URL",
                "OPENAI_API_BASE",
                "ROUTER_API_BASE",
            ]
        )
        llm_api_base = self._normalize_chat_api_base(llm_api_base)
        llm_api_key = self._first_env(
            [
                "COMPACT_GIST_LLM_API_KEY",
                "WRITE_GUARD_LLM_API_KEY",
                "LLM_API_KEY",
                "OPENAI_API_KEY",
                "ROUTER_API_KEY",
            ]
        )
        llm_model = self._first_env(
            [
                "COMPACT_GIST_LLM_MODEL",
                "WRITE_GUARD_LLM_MODEL",
                "LLM_MODEL_NAME",
                "OPENAI_MODEL",
                "ROUTER_CHAT_MODEL",
            ]
        )
        if not llm_api_base or not llm_model:
            self._append_degrade_reason(degrade_reasons, "compact_gist_llm_config_missing")
            return None

        try:
            bounded_points = max(1, int(max_points))
        except (TypeError, ValueError):
            bounded_points = 3
        try:
            bounded_chars = max(80, int(max_chars))
        except (TypeError, ValueError):
            bounded_chars = 280

        payload = {
            "model": llm_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a compact_context semantic gist generator. "
                        "Return strict JSON only with keys: gist_text, quality. "
                        "quality must be a float in [0,1]."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Summarize the following session trace into at most {bounded_points} "
                        f"high-signal points and <= {bounded_chars} chars.\n\n"
                        f"{source}"
                    ),
                },
            ],
        }

        response = await self._post_json(
            llm_api_base,
            "/chat/completions",
            payload,
            llm_api_key,
        )
        if response is None:
            self._append_degrade_reason(degrade_reasons, "compact_gist_llm_request_failed")
            return None

        message_text = self._extract_chat_message_text(response)
        if not message_text:
            self._append_degrade_reason(degrade_reasons, "compact_gist_llm_response_empty")
            return None

        parsed = self._parse_chat_json_object(message_text)
        if parsed is None:
            self._append_degrade_reason(degrade_reasons, "compact_gist_llm_response_invalid")
            return None

        gist_text = str(parsed.get("gist_text") or "").strip()
        if not gist_text:
            self._append_degrade_reason(degrade_reasons, "compact_gist_llm_gist_missing")
            return None
        if len(gist_text) > bounded_chars:
            gist_text = gist_text[: max(24, bounded_chars - 3)].rstrip() + "..."

        quality_value = parsed.get("quality")
        try:
            quality = float(quality_value)
        except (TypeError, ValueError):
            quality = 0.72
        quality = max(0.0, min(1.0, quality))

        return {
            "gist_text": gist_text,
            "gist_method": "llm_gist",
            "quality": round(quality, 3),
        }

    @staticmethod
    def _collect_guard_candidates(
        payload: Dict[str, Any],
        *,
        exclude_memory_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        rows = payload.get("results")
        if not isinstance(rows, list):
            return []

        by_memory_id: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            memory_id = row.get("memory_id")
            if not isinstance(memory_id, int) or memory_id <= 0:
                continue
            if exclude_memory_id is not None and memory_id == exclude_memory_id:
                continue

            scores = row.get("scores")
            if not isinstance(scores, dict):
                scores = {}

            candidate = {
                "memory_id": memory_id,
                "uri": str(row.get("uri") or ""),
                "snippet": str(row.get("snippet") or "")[:220],
                "vector_score": float(scores.get("vector") or 0.0),
                "text_score": float(scores.get("text") or 0.0),
                "final_score": float(scores.get("final") or 0.0),
            }
            existing = by_memory_id.get(memory_id)
            if existing is None or candidate["final_score"] > existing["final_score"]:
                by_memory_id[memory_id] = candidate

        return sorted(
            by_memory_id.values(), key=lambda item: item.get("final_score", 0.0), reverse=True
        )

    @staticmethod
    def _guard_candidate_view(candidate: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not candidate:
            return None
        return {
            "memory_id": candidate.get("memory_id"),
            "uri": candidate.get("uri"),
            "vector_score": round(float(candidate.get("vector_score") or 0.0), 6),
            "text_score": round(float(candidate.get("text_score") or 0.0), 6),
            "final_score": round(float(candidate.get("final_score") or 0.0), 6),
        }

    def _build_guard_decision(
        self,
        *,
        action: str,
        reason: str,
        method: str,
        target_id: Optional[int] = None,
        target_uri: Optional[str] = None,
        degrade_reasons: Optional[List[str]] = None,
        semantic_top: Optional[Dict[str, Any]] = None,
        keyword_top: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        decision = {
            "action": action,
            "target_id": target_id,
            "target_uri": target_uri,
            "reason": reason,
            "method": method,
            "degraded": bool(degrade_reasons),
            "degrade_reasons": list(degrade_reasons or []),
            "candidates": {
                "semantic_top": self._guard_candidate_view(semantic_top),
                "keyword_top": self._guard_candidate_view(keyword_top),
            },
        }
        return decision

    async def _write_guard_llm_decision(
        self,
        *,
        content: str,
        semantic_candidates: List[Dict[str, Any]],
        keyword_candidates: List[Dict[str, Any]],
        degrade_reasons: List[str],
    ) -> Optional[Dict[str, Any]]:
        if not self._env_bool("WRITE_GUARD_LLM_ENABLED", False):
            self._append_degrade_reason(degrade_reasons, "write_guard_llm_disabled")
            return None

        llm_api_base = self._first_env(
            [
                "WRITE_GUARD_LLM_API_BASE",
                "LLM_RESPONSES_URL",
                "OPENAI_BASE_URL",
                "OPENAI_API_BASE",
                "ROUTER_API_BASE",
            ]
        )
        llm_api_base = self._normalize_chat_api_base(llm_api_base)
        llm_api_key = self._first_env(
            ["WRITE_GUARD_LLM_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "ROUTER_API_KEY"]
        )
        llm_model = self._first_env(
            ["WRITE_GUARD_LLM_MODEL", "LLM_MODEL_NAME", "OPENAI_MODEL", "ROUTER_CHAT_MODEL"]
        )
        if not llm_api_base or not llm_model:
            self._append_degrade_reason(degrade_reasons, "write_guard_llm_config_missing")
            return None

        shortlist: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()
        for item in semantic_candidates + keyword_candidates:
            memory_id = item.get("memory_id")
            if not isinstance(memory_id, int) or memory_id in seen_ids:
                continue
            seen_ids.add(memory_id)
            shortlist.append(item)
            if len(shortlist) >= 5:
                break

        if not shortlist:
            self._append_degrade_reason(degrade_reasons, "write_guard_llm_no_candidates")
            return None

        candidate_lines = []
        for idx, item in enumerate(shortlist, start=1):
            candidate_lines.append(
                f"{idx}. memory_id={item.get('memory_id')} uri={item.get('uri')} "
                f"vector={item.get('vector_score', 0.0):.3f} text={item.get('text_score', 0.0):.3f} "
                f"snippet={item.get('snippet', '')}"
            )

        system_prompt = (
            "You are a write guard for a memory system. "
            "Return strict JSON only with keys: action,target_id,reason,method. "
            "Allowed action: ADD,UPDATE,NOOP,DELETE."
        )
        user_prompt = (
            "New content:\n"
            f"{content}\n\n"
            "Candidate memories:\n"
            f"{chr(10).join(candidate_lines)}\n\n"
            "Decide if this should be added, update an existing memory, noop, or delete."
        )
        payload = {
            "model": llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = await self._post_json(
            llm_api_base,
            "/chat/completions",
            payload,
            llm_api_key,
        )
        if response is None:
            self._append_degrade_reason(degrade_reasons, "write_guard_llm_request_failed")
            return None

        message_text = self._extract_chat_message_text(response)
        if not message_text:
            self._append_degrade_reason(degrade_reasons, "write_guard_llm_response_empty")
            return None

        parsed: Optional[Dict[str, Any]] = None
        try:
            loaded = json.loads(message_text)
            if isinstance(loaded, dict):
                parsed = loaded
        except (TypeError, ValueError):
            parsed = None

        if parsed is None:
            self._append_degrade_reason(degrade_reasons, "write_guard_llm_response_invalid")
            return None

        action = self._normalize_guard_action(parsed.get("action"))
        if action is None:
            self._append_degrade_reason(degrade_reasons, "write_guard_llm_action_invalid")
            return None

        target_id = parsed.get("target_id")
        if not isinstance(target_id, int) or target_id <= 0:
            target_id = None
        reason = str(parsed.get("reason") or "llm_decision")
        method = str(parsed.get("method") or "llm")

        target_uri = None
        if target_id is not None:
            matched = next(
                (item for item in shortlist if item.get("memory_id") == target_id), None
            )
            if matched is not None:
                target_uri = matched.get("uri")

        return self._build_guard_decision(
            action=action,
            reason=reason,
            method=method,
            target_id=target_id,
            target_uri=target_uri,
            degrade_reasons=degrade_reasons,
            semantic_top=semantic_candidates[0] if semantic_candidates else None,
            keyword_top=keyword_candidates[0] if keyword_candidates else None,
        )

    async def write_guard(
        self,
        *,
        content: str,
        domain: str = "core",
        path_prefix: Optional[str] = None,
        exclude_memory_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        query = (content or "").strip()
        if not query:
            return self._build_guard_decision(
                action="NOOP",
                reason="empty_content",
                method="keyword",
            )

        filters: Dict[str, Any] = {"domain": domain or "core"}
        if isinstance(path_prefix, str) and path_prefix.strip():
            filters["path_prefix"] = path_prefix.strip("/")

        degrade_reasons: List[str] = []

        semantic_payload: Dict[str, Any]
        keyword_payload: Dict[str, Any]
        try:
            semantic_payload = await self.search_advanced(
                query=query,
                mode="semantic",
                max_results=6,
                candidate_multiplier=6,
                filters=filters,
            )
        except Exception as exc:
            self._append_degrade_reason(
                degrade_reasons, f"write_guard_semantic_failed:{type(exc).__name__}"
            )
            semantic_payload = {"results": [], "degrade_reasons": []}
        try:
            keyword_payload = await self.search_advanced(
                query=query,
                mode="keyword",
                max_results=6,
                candidate_multiplier=6,
                filters=filters,
            )
        except Exception as exc:
            self._append_degrade_reason(
                degrade_reasons, f"write_guard_keyword_failed:{type(exc).__name__}"
            )
            keyword_payload = {"results": [], "degrade_reasons": []}

        for payload in (semantic_payload, keyword_payload):
            reasons = payload.get("degrade_reasons")
            if not isinstance(reasons, list):
                continue
            for reason in reasons:
                if isinstance(reason, str):
                    self._append_degrade_reason(degrade_reasons, reason)

        semantic_candidates = self._collect_guard_candidates(
            semantic_payload,
            exclude_memory_id=exclude_memory_id,
        )
        keyword_candidates = self._collect_guard_candidates(
            keyword_payload,
            exclude_memory_id=exclude_memory_id,
        )

        semantic_top = (
            max(
                semantic_candidates,
                key=lambda item: float(item.get("vector_score") or 0.0),
            )
            if semantic_candidates
            else None
        )
        if semantic_top is not None:
            vector_score = float(semantic_top.get("vector_score") or 0.0)
            if vector_score >= 0.92:
                return self._build_guard_decision(
                    action="NOOP",
                    target_id=semantic_top.get("memory_id"),
                    target_uri=semantic_top.get("uri"),
                    reason=f"semantic similarity {vector_score:.3f} >= 0.920",
                    method="embedding",
                    degrade_reasons=degrade_reasons,
                    semantic_top=semantic_top,
                    keyword_top=(
                        max(
                            keyword_candidates,
                            key=lambda item: float(item.get("text_score") or 0.0),
                        )
                        if keyword_candidates
                        else None
                    ),
                )
            if vector_score >= 0.78:
                return self._build_guard_decision(
                    action="UPDATE",
                    target_id=semantic_top.get("memory_id"),
                    target_uri=semantic_top.get("uri"),
                    reason=f"semantic similarity {vector_score:.3f} >= 0.780",
                    method="embedding",
                    degrade_reasons=degrade_reasons,
                    semantic_top=semantic_top,
                    keyword_top=(
                        max(
                            keyword_candidates,
                            key=lambda item: float(item.get("text_score") or 0.0),
                        )
                        if keyword_candidates
                        else None
                    ),
                )

        keyword_top = (
            max(
                keyword_candidates,
                key=lambda item: float(item.get("text_score") or 0.0),
            )
            if keyword_candidates
            else None
        )
        if keyword_top is not None:
            text_score = float(keyword_top.get("text_score") or 0.0)
            if text_score >= 0.82:
                return self._build_guard_decision(
                    action="NOOP",
                    target_id=keyword_top.get("memory_id"),
                    target_uri=keyword_top.get("uri"),
                    reason=f"keyword overlap score {text_score:.3f} >= 0.820",
                    method="keyword",
                    degrade_reasons=degrade_reasons,
                    semantic_top=semantic_top,
                    keyword_top=keyword_top,
                )
            if text_score >= 0.55:
                return self._build_guard_decision(
                    action="UPDATE",
                    target_id=keyword_top.get("memory_id"),
                    target_uri=keyword_top.get("uri"),
                    reason=f"keyword overlap score {text_score:.3f} >= 0.550",
                    method="keyword",
                    degrade_reasons=degrade_reasons,
                    semantic_top=semantic_top,
                    keyword_top=keyword_top,
                )

        llm_decision = await self._write_guard_llm_decision(
            content=query,
            semantic_candidates=semantic_candidates,
            keyword_candidates=keyword_candidates,
            degrade_reasons=degrade_reasons,
        )
        if llm_decision is not None:
            return llm_decision

        return self._build_guard_decision(
            action="ADD",
            reason="no strong duplicate signal",
            method="keyword",
            degrade_reasons=degrade_reasons,
            semantic_top=semantic_top,
            keyword_top=keyword_top,
        )

    async def search_advanced(
        self,
        query: str,
        mode: str = "keyword",
        max_results: int = 8,
        candidate_multiplier: int = 4,
        filters: Optional[Dict[str, Any]] = None,
        intent_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Advanced retrieval with keyword/semantic/hybrid modes.

        Returns chunk-level hits with component scores and metadata.
        """
        query = (query or "").strip()
        intent_applied: Optional[str] = None
        strategy_template = "default"
        try:
            requested_candidate_multiplier = int(candidate_multiplier)
        except (TypeError, ValueError):
            requested_candidate_multiplier = 4
        applied_candidate_multiplier = max(1, requested_candidate_multiplier)

        if isinstance(intent_profile, dict):
            intent_candidate = str(intent_profile.get("intent") or "").strip().lower()
            if intent_candidate in {"factual", "exploratory", "temporal", "causal"}:
                intent_applied = intent_candidate
                if intent_candidate == "factual":
                    strategy_template = "factual_high_precision"
                    applied_candidate_multiplier = min(applied_candidate_multiplier, 2)
                elif intent_candidate == "exploratory":
                    strategy_template = "exploratory_high_recall"
                    applied_candidate_multiplier = max(applied_candidate_multiplier, 6)
                elif intent_candidate == "temporal":
                    strategy_template = "temporal_time_filtered"
                    applied_candidate_multiplier = max(applied_candidate_multiplier, 5)
                elif intent_candidate == "causal":
                    strategy_template = "causal_wide_pool"
                    applied_candidate_multiplier = max(applied_candidate_multiplier, 8)

        strategy_metadata = {
            "intent": intent_applied,
            "strategy_template": strategy_template,
            "candidate_multiplier_applied": applied_candidate_multiplier,
        }

        if not query:
            degrade_reasons = ["empty_query"]
            return {
                "results": [],
                "mode": "keyword",
                "requested_mode": mode,
                "degraded": True,
                "degrade_reason": "empty_query",
                "degrade_reasons": degrade_reasons,
                "metadata": {
                    "degraded": True,
                    "degrade_reasons": degrade_reasons,
                    **strategy_metadata,
                },
            }

        mode_value = (mode or "keyword").strip().lower()
        if mode_value not in {"keyword", "semantic", "hybrid"}:
            raise ValueError("mode must be one of: keyword, semantic, hybrid")

        requested_mode = mode_value
        degrade_reasons: List[str] = []
        if mode_value in {"semantic", "hybrid"} and not self._vector_available:
            mode_value = "keyword"
            self._append_degrade_reason(degrade_reasons, "vector_backend_disabled")

        try:
            parsed_max_results = int(max_results)
        except (TypeError, ValueError):
            parsed_max_results = 8
        max_results = max(1, parsed_max_results)
        candidate_multiplier = applied_candidate_multiplier
        candidate_limit = max_results * candidate_multiplier
        filters = filters or {}

        async with self.session() as session:
            where_parts = ["m.deprecated = 0"]
            where_params: Dict[str, Any] = {}

            domain_filter = filters.get("domain")
            path_prefix_filter = filters.get("path_prefix")
            priority_filter = filters.get("max_priority", filters.get("priority"))
            updated_after_filter = self._parse_iso_datetime(filters.get("updated_after"))

            if path_prefix_filter and isinstance(path_prefix_filter, str) and "://" in path_prefix_filter:
                prefix_domain, prefix_path = path_prefix_filter.split("://", 1)
                prefix_domain = prefix_domain.strip().lower()
                prefix_path = prefix_path.strip("/")
                if prefix_domain:
                    domain_filter = domain_filter or prefix_domain
                path_prefix_filter = prefix_path

            if domain_filter:
                where_parts.append("p.domain = :domain_filter")
                where_params["domain_filter"] = str(domain_filter)

            if path_prefix_filter:
                escaped_prefix = self._escape_like_pattern(str(path_prefix_filter))
                where_parts.append("p.path LIKE :path_prefix_filter ESCAPE '\\'")
                where_params["path_prefix_filter"] = f"{escaped_prefix}%"

            if priority_filter is not None:
                try:
                    where_parts.append("p.priority <= :priority_filter")
                    where_params["priority_filter"] = int(priority_filter)
                except (TypeError, ValueError):
                    pass

            if updated_after_filter is not None:
                where_parts.append("m.created_at >= :updated_after_filter")
                where_params["updated_after_filter"] = updated_after_filter.strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                )

            where_clause = " AND ".join(where_parts)

            keyword_rows: List[Dict[str, Any]] = []
            semantic_rows: List[Dict[str, Any]] = []

            if mode_value in {"keyword", "hybrid"}:
                if self._fts_available:
                    terms = re.findall(r"[a-zA-Z0-9_]+", query)
                    fts_query = " ".join(terms) if terms else query
                    try:
                        keyword_result = await session.execute(
                            text(
                                "SELECT "
                                "mc.id AS chunk_id, mc.memory_id AS memory_id, "
                                "mc.chunk_text AS chunk_text, mc.char_start AS char_start, mc.char_end AS char_end, "
                                "p.domain AS domain, p.path AS path, p.priority AS priority, p.disclosure AS disclosure, "
                                "m.created_at AS created_at, bm25(memory_chunks_fts) AS text_rank "
                                "FROM memory_chunks_fts "
                                "JOIN memory_chunks mc ON mc.id = memory_chunks_fts.chunk_id "
                                "JOIN memories m ON m.id = mc.memory_id "
                                "JOIN paths p ON p.memory_id = mc.memory_id "
                                f"WHERE {where_clause} "
                                "AND memory_chunks_fts MATCH :fts_query "
                                "ORDER BY text_rank ASC "
                                "LIMIT :candidate_limit"
                            ),
                            {
                                **where_params,
                                "fts_query": fts_query,
                                "candidate_limit": candidate_limit,
                            },
                        )
                        keyword_rows = [dict(row) for row in keyword_result.mappings().all()]
                    except Exception:
                        self._fts_available = False
                        await self._set_index_meta(session, "fts_available", "0")

                if not keyword_rows:
                    like_pattern = f"%{query.lower()}%"
                    keyword_result = await session.execute(
                        text(
                            "SELECT "
                            "mc.id AS chunk_id, mc.memory_id AS memory_id, "
                            "mc.chunk_text AS chunk_text, mc.char_start AS char_start, mc.char_end AS char_end, "
                            "p.domain AS domain, p.path AS path, p.priority AS priority, p.disclosure AS disclosure, "
                            "m.created_at AS created_at "
                            "FROM memory_chunks mc "
                            "JOIN memories m ON m.id = mc.memory_id "
                            "JOIN paths p ON p.memory_id = mc.memory_id "
                            f"WHERE {where_clause} "
                            "AND (LOWER(mc.chunk_text) LIKE :like_pattern OR LOWER(p.path) LIKE :like_pattern) "
                            "ORDER BY p.priority ASC, m.created_at DESC "
                            "LIMIT :candidate_limit"
                        ),
                        {
                            **where_params,
                            "like_pattern": like_pattern,
                            "candidate_limit": candidate_limit,
                        },
                    )
                    keyword_rows = [dict(row) for row in keyword_result.mappings().all()]

                # Legacy fallback for pre-index data
                if not keyword_rows:
                    search_pattern = f"%{query}%"
                    legacy_query = (
                        select(Memory, Path)
                        .join(Path, Memory.id == Path.memory_id)
                        .where(Memory.deprecated == False)
                        .where(
                            or_(
                                Path.path.like(search_pattern),
                                Memory.content.like(search_pattern),
                            )
                        )
                    )
                    if domain_filter:
                        legacy_query = legacy_query.where(Path.domain == str(domain_filter))
                    if path_prefix_filter:
                        escaped = self._escape_like_pattern(str(path_prefix_filter))
                        legacy_query = legacy_query.where(
                            Path.path.like(f"{escaped}%", escape="\\")
                        )
                    if priority_filter is not None:
                        try:
                            legacy_query = legacy_query.where(
                                Path.priority <= int(priority_filter)
                            )
                        except (TypeError, ValueError):
                            pass
                    if updated_after_filter is not None:
                        legacy_query = legacy_query.where(
                            Memory.created_at >= updated_after_filter
                        )

                    legacy_result = await session.execute(
                        legacy_query.order_by(Path.priority.asc(), Memory.created_at.desc()).limit(
                            candidate_limit
                        )
                    )
                    for memory, path_obj in legacy_result.all():
                        keyword_rows.append(
                            {
                                "chunk_id": None,
                                "memory_id": memory.id,
                                "chunk_text": memory.content,
                                "char_start": 0,
                                "char_end": len(memory.content or ""),
                                "domain": path_obj.domain,
                                "path": path_obj.path,
                                "priority": path_obj.priority,
                                "disclosure": path_obj.disclosure,
                                "created_at": memory.created_at,
                            }
                        )

            if mode_value in {"semantic", "hybrid"}:
                query_embedding = await self._get_embedding(
                    session,
                    query,
                    degrade_reasons=degrade_reasons,
                )
                semantic_pool_limit = min(max(candidate_limit * 8, 64), 3000)
                semantic_result = await session.execute(
                    text(
                        "SELECT "
                        "mc.id AS chunk_id, mc.memory_id AS memory_id, "
                        "mc.chunk_text AS chunk_text, mc.char_start AS char_start, mc.char_end AS char_end, "
                        "mcv.vector AS vector_json, "
                        "p.domain AS domain, p.path AS path, p.priority AS priority, p.disclosure AS disclosure, "
                        "m.created_at AS created_at "
                        "FROM memory_chunks_vec mcv "
                        "JOIN memory_chunks mc ON mc.id = mcv.chunk_id "
                        "JOIN memories m ON m.id = mc.memory_id "
                        "JOIN paths p ON p.memory_id = mc.memory_id "
                        f"WHERE {where_clause} "
                        "LIMIT :semantic_pool_limit"
                    ),
                    {**where_params, "semantic_pool_limit": semantic_pool_limit},
                )

                semantic_scored: List[Tuple[float, Dict[str, Any]]] = []
                for row in semantic_result.mappings().all():
                    vector_payload = row.get("vector_json")
                    if not vector_payload:
                        continue
                    try:
                        chunk_vec = [float(v) for v in json.loads(vector_payload)]
                    except (TypeError, ValueError):
                        continue
                    similarity = self._cosine_similarity(query_embedding, chunk_vec)
                    semantic_scored.append((similarity, dict(row)))

                semantic_scored.sort(key=lambda item: item[0], reverse=True)
                semantic_rows = []
                for similarity, row in semantic_scored[:candidate_limit]:
                    row["vector_similarity"] = similarity
                    semantic_rows.append(row)

            candidates: Dict[Tuple[str, str, Any], Dict[str, Any]] = {}

            def upsert_candidate(row: Dict[str, Any], vector_score: float, text_score: float) -> None:
                key = (str(row.get("domain", "")), str(row.get("path", "")), row.get("chunk_id"))
                item = candidates.get(key)
                if item is None:
                    item = {
                        "memory_id": row.get("memory_id"),
                        "chunk_id": row.get("chunk_id"),
                        "chunk_text": row.get("chunk_text") or "",
                        "char_start": int(row.get("char_start") or 0),
                        "char_end": int(row.get("char_end") or 0),
                        "domain": row.get("domain") or "core",
                        "path": row.get("path") or "",
                        "priority": int(row.get("priority") or 0),
                        "disclosure": row.get("disclosure"),
                        "created_at": row.get("created_at"),
                        "vector_score": 0.0,
                        "text_score": 0.0,
                    }
                    candidates[key] = item

                item["vector_score"] = max(item["vector_score"], vector_score)
                item["text_score"] = max(item["text_score"], text_score)

            for row in keyword_rows:
                text_rank = row.get("text_rank")
                if text_rank is not None:
                    try:
                        score = 1.0 / (1.0 + max(float(text_rank), 0.0))
                    except (TypeError, ValueError):
                        score = self._like_text_score(query, row.get("chunk_text", ""), row.get("path", ""))
                else:
                    score = self._like_text_score(
                        query, row.get("chunk_text", ""), row.get("path", "")
                    )
                upsert_candidate(row, vector_score=0.0, text_score=score)

            for row in semantic_rows:
                similarity = float(row.get("vector_similarity", 0.0))
                vector_score = max(0.0, min(1.0, (similarity + 1.0) / 2.0))
                upsert_candidate(row, vector_score=vector_score, text_score=0.0)

            if not candidates:
                degraded = bool(degrade_reasons)
                return {
                    "results": [],
                    "mode": mode_value,
                    "requested_mode": requested_mode,
                    "degraded": degraded,
                    "degrade_reason": degrade_reasons[0] if degrade_reasons else None,
                    "degrade_reasons": list(degrade_reasons),
                    "metadata": {
                        "degraded": degraded,
                        "degrade_reasons": list(degrade_reasons),
                        **strategy_metadata,
                    },
                }

            if mode_value == "keyword":
                weights = {
                    "vector": 0.0,
                    "text": 0.80,
                    "priority": 0.12,
                    "recency": 0.06,
                    "path_prefix": 0.02,
                }
            elif mode_value == "semantic":
                weights = {
                    "vector": 0.82,
                    "text": 0.0,
                    "priority": 0.10,
                    "recency": 0.06,
                    "path_prefix": 0.02,
                }
            else:
                weights = {
                    "vector": self._weight_vector,
                    "text": self._weight_text,
                    "priority": self._weight_priority,
                    "recency": self._weight_recency,
                    "path_prefix": self._weight_path_prefix,
                }

            if strategy_template != "default" and mode_value == "hybrid":
                if strategy_template == "factual_high_precision":
                    weights = {
                        "vector": 0.22,
                        "text": 0.58,
                        "priority": 0.12,
                        "recency": 0.06,
                        "path_prefix": 0.02,
                    }
                elif strategy_template == "exploratory_high_recall":
                    weights = {
                        "vector": 0.58,
                        "text": 0.24,
                        "priority": 0.08,
                        "recency": 0.07,
                        "path_prefix": 0.03,
                    }
                elif strategy_template == "temporal_time_filtered":
                    weights = {
                        "vector": 0.28,
                        "text": 0.22,
                        "priority": 0.08,
                        "recency": 0.38,
                        "path_prefix": 0.04,
                    }
                elif strategy_template == "causal_wide_pool":
                    weights = {
                        "vector": 0.52,
                        "text": 0.28,
                        "priority": 0.08,
                        "recency": 0.08,
                        "path_prefix": 0.04,
                    }

            now = _utc_now_naive()
            scored_results: List[Dict[str, Any]] = []
            prefix_value = str(path_prefix_filter) if path_prefix_filter else ""
            candidate_items = list(candidates.values())
            rerank_scores_by_index: Dict[int, float] = {}
            if candidate_items and self._reranker_enabled:
                rerank_documents = [str(item.get("chunk_text", "")) for item in candidate_items]
                rerank_scores_by_index = await self._get_rerank_scores(
                    query,
                    rerank_documents,
                    degrade_reasons=degrade_reasons,
                )

            for idx, item in enumerate(candidate_items):
                created_at = item.get("created_at")
                if isinstance(created_at, str):
                    created_at = self._parse_iso_datetime(created_at)

                if isinstance(created_at, datetime):
                    ref_now = (
                        datetime.now(created_at.tzinfo)
                        if created_at.tzinfo is not None
                        else now
                    )
                    age_days = max(0.0, (ref_now - created_at).total_seconds() / 86400.0)
                else:
                    age_days = 365.0

                priority_score = 1.0 / (1.0 + max(item.get("priority", 0), 0))
                recency_score = math.exp(-age_days / self._recency_half_life_days)
                path_prefix_score = (
                    1.0 if prefix_value and str(item.get("path", "")).startswith(prefix_value) else 0.0
                )

                base_score = (
                    weights["vector"] * item["vector_score"]
                    + weights["text"] * item["text_score"]
                    + weights["priority"] * priority_score
                    + weights["recency"] * recency_score
                    + weights["path_prefix"] * path_prefix_score
                )
                rerank_score = rerank_scores_by_index.get(idx, 0.0)
                final_score = base_score + (self._rerank_weight * rerank_score)

                snippet = self._make_snippet(item["chunk_text"], query)
                domain = item.get("domain") or "core"
                path = item.get("path") or ""

                scored_results.append(
                    {
                        "uri": f"{domain}://{path}",
                        "memory_id": item["memory_id"],
                        "chunk_id": item.get("chunk_id"),
                        "snippet": snippet,
                        "char_range": [item["char_start"], item["char_end"]],
                        "scores": {
                            "vector": round(item["vector_score"], 6),
                            "text": round(item["text_score"], 6),
                            "priority": round(priority_score, 6),
                            "recency": round(recency_score, 6),
                            "path_prefix": round(path_prefix_score, 6),
                            "rerank": round(rerank_score, 6),
                            "final": round(final_score, 6),
                        },
                        "metadata": {
                            "domain": domain,
                            "path": path,
                            "priority": item.get("priority", 0),
                            "disclosure": item.get("disclosure"),
                            "updated_at": created_at.isoformat()
                            if isinstance(created_at, datetime)
                            else None,
                        },
                    }
                )

            scored_results.sort(key=lambda row: row["scores"]["final"], reverse=True)
            top_results = scored_results[:max_results]
            await self._reinforce_memory_access(
                session,
                [
                    int(row.get("memory_id"))
                    for row in top_results
                    if row.get("memory_id") is not None
                ],
            )
            degraded = bool(degrade_reasons)
            return {
                "results": top_results,
                "mode": mode_value,
                "requested_mode": requested_mode,
                "degraded": degraded,
                "degrade_reason": degrade_reasons[0] if degrade_reasons else None,
                "degrade_reasons": list(degrade_reasons),
                "metadata": {
                    "degraded": degraded,
                    "degrade_reasons": list(degrade_reasons),
                    **strategy_metadata,
                },
            }

    async def search(
        self, query: str, limit: int = 10, domain: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Legacy-compatible search by path/content.

        Args:
            query: Search query
            limit: Max results
            domain: If specified, only search in this domain.
                    If None, search across all domains.

        Returns:
            Legacy result structure used by existing MCP layer.
        """
        filters = {"domain": domain} if domain is not None else {}
        advanced_payload = await self.search_advanced(
            query=query,
            mode="keyword",
            max_results=max(1, limit),
            candidate_multiplier=4,
            filters=filters,
        )
        advanced_results = (
            advanced_payload.get("results", [])
            if isinstance(advanced_payload, dict)
            else advanced_payload
        )

        matches: List[Dict[str, Any]] = []
        seen_memory_ids = set()

        for row in advanced_results:
            memory_id = row.get("memory_id")
            if memory_id in seen_memory_ids:
                continue
            seen_memory_ids.add(memory_id)

            metadata = row.get("metadata", {})
            domain_value = metadata.get("domain", "core")
            path_value = metadata.get("path", "")
            matches.append(
                {
                    "domain": domain_value,
                    "path": path_value,
                    "uri": row.get("uri", f"{domain_value}://{path_value}"),
                    "name": path_value.rsplit("/", 1)[-1] if path_value else "",
                    "snippet": row.get("snippet", ""),
                    "priority": metadata.get("priority", 0),
                }
            )

            if len(matches) >= limit:
                break

        return matches

    async def read_memory_segment(
        self,
        *,
        uri: Optional[str] = None,
        memory_id: Optional[int] = None,
        chunk_id: Optional[int] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        max_chars: Optional[int] = None,
        domain: str = "core",
    ) -> Optional[Dict[str, Any]]:
        """
        Read a memory fragment by uri/memory/chunk.
        """
        async with self.session() as session:
            if chunk_id is not None:
                chunk_result = await session.execute(
                    select(MemoryChunk, Memory, Path)
                    .join(Memory, MemoryChunk.memory_id == Memory.id)
                    .join(Path, Path.memory_id == MemoryChunk.memory_id)
                    .where(MemoryChunk.id == chunk_id)
                    .where(Memory.deprecated == False)
                    .order_by(Path.priority.asc())
                )
                row = chunk_result.first()
                if not row:
                    return None

                chunk_obj, memory_obj, path_obj = row
                await self._reinforce_memory_access(session, [memory_obj.id])
                return {
                    "memory_id": memory_obj.id,
                    "chunk_id": chunk_obj.id,
                    "uri": f"{path_obj.domain}://{path_obj.path}",
                    "segment": chunk_obj.chunk_text,
                    "content": chunk_obj.chunk_text,
                    "char_range": [chunk_obj.char_start, chunk_obj.char_end],
                    "metadata": {
                        "domain": path_obj.domain,
                        "path": path_obj.path,
                        "priority": path_obj.priority,
                        "disclosure": path_obj.disclosure,
                        "updated_at": memory_obj.created_at.isoformat()
                        if memory_obj.created_at
                        else None,
                    },
                }

            target_memory: Optional[Memory] = None
            target_path: Optional[Path] = None

            if uri:
                if "://" in uri:
                    uri_domain, uri_path = uri.split("://", 1)
                else:
                    uri_domain, uri_path = domain, uri
                mem_result = await session.execute(
                    select(Memory, Path)
                    .join(Path, Memory.id == Path.memory_id)
                    .where(Path.domain == uri_domain)
                    .where(Path.path == uri_path)
                    .where(Memory.deprecated == False)
                )
                row = mem_result.first()
                if not row:
                    return None
                target_memory, target_path = row
            elif memory_id is not None:
                mem_result = await session.execute(
                    select(Memory, Path)
                    .join(Path, Memory.id == Path.memory_id)
                    .where(Memory.id == memory_id)
                    .where(Memory.deprecated == False)
                    .order_by(Path.priority.asc())
                )
                row = mem_result.first()
                if row:
                    target_memory, target_path = row
                else:
                    memory_result = await session.execute(
                        select(Memory).where(Memory.id == memory_id)
                    )
                    target_memory = memory_result.scalar_one_or_none()
                    if not target_memory:
                        return None
            else:
                return None

            full_content = target_memory.content or ""
            content_len = len(full_content)
            start_idx = max(0, int(start or 0))

            if end is not None:
                end_idx = min(content_len, max(start_idx, int(end)))
            elif max_chars is not None:
                end_idx = min(content_len, start_idx + max(1, int(max_chars)))
            else:
                end_idx = content_len

            segment = full_content[start_idx:end_idx]
            uri_value = (
                f"{target_path.domain}://{target_path.path}"
                if target_path is not None
                else None
            )
            await self._reinforce_memory_access(session, [target_memory.id])

            return {
                "memory_id": target_memory.id,
                "chunk_id": None,
                "uri": uri_value,
                "segment": segment,
                "content": segment,
                "char_range": [start_idx, end_idx],
                "metadata": {
                    "domain": target_path.domain if target_path else None,
                    "path": target_path.path if target_path else None,
                    "priority": target_path.priority if target_path else None,
                    "disclosure": target_path.disclosure if target_path else None,
                    "updated_at": target_memory.created_at.isoformat()
                    if target_memory.created_at
                    else None,
                },
            }

    async def get_index_status(self) -> Dict[str, Any]:
        """
        Return index capabilities, table counts, and current index metadata.
        """
        async with self.session() as session:
            memory_count_result = await session.execute(
                select(func.count()).select_from(Memory).where(Memory.deprecated == False)
            )
            chunk_count_result = await session.execute(
                select(func.count()).select_from(MemoryChunk)
            )
            vector_count_result = await session.execute(
                select(func.count()).select_from(MemoryChunkVec)
            )
            cache_count_result = await session.execute(
                select(func.count()).select_from(EmbeddingCache)
            )

            fts_exists_result = await session.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'memory_chunks_fts' "
                    "LIMIT 1"
                )
            )
            fts_exists = fts_exists_result.first() is not None
            self._fts_available = self._fts_available and fts_exists

            meta_rows = await session.execute(select(IndexMeta))
            meta = {row.key: row.value for row in meta_rows.scalars().all()}

            return {
                "capabilities": {
                    "fts_available": self._fts_available and fts_exists,
                    "vector_available": self._vector_available,
                    "embedding_backend": self._embedding_backend,
                    "embedding_model": self._embedding_model,
                    "embedding_dim": self._embedding_dim,
                    "reranker_enabled": self._reranker_enabled,
                    "reranker_model": self._reranker_model,
                    "rerank_weight": self._rerank_weight,
                },
                "counts": {
                    "active_memories": int(memory_count_result.scalar() or 0),
                    "memory_chunks": int(chunk_count_result.scalar() or 0),
                    "memory_chunks_vec": int(vector_count_result.scalar() or 0),
                    "embedding_cache": int(cache_count_result.scalar() or 0),
                },
                "meta": meta,
            }

    # =========================================================================
    # Recent Memories
    # =========================================================================

    async def get_recent_memories(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the most recently created/updated non-deprecated memories
        that have at least one path (URI) pointing to them.

        Since updates create new Memory rows (old ones are deprecated),
        created_at on non-deprecated rows effectively means "last modified".

        Args:
            limit: Maximum number of results to return

        Returns:
            List of dicts with uri, priority, disclosure, created_at,
            ordered by created_at DESC (most recent first).
        """
        async with self.session() as session:
            # Subquery: find non-deprecated memory IDs that have paths
            # Group by memory_id to avoid duplicates when a memory has multiple paths
            result = await session.execute(
                select(Memory, Path)
                .join(Path, Memory.id == Path.memory_id)
                .where(Memory.deprecated == False)
                .order_by(Memory.created_at.desc())
            )

            seen_memory_ids = set()
            memories = []

            for memory, path_obj in result.all():
                if memory.id in seen_memory_ids:
                    continue
                seen_memory_ids.add(memory.id)

                memories.append(
                    {
                        "memory_id": memory.id,
                        "uri": f"{path_obj.domain}://{path_obj.path}",
                        "priority": path_obj.priority,
                        "disclosure": path_obj.disclosure,
                        "created_at": memory.created_at.isoformat()
                        if memory.created_at
                        else None,
                    }
                )

                if len(memories) >= limit:
                    break

            return memories

    # =========================================================================
    # Deprecated Memory Operations (for human's review)
    # =========================================================================

    async def get_memory_version(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific memory version by ID (including deprecated ones).

        Args:
            memory_id: The memory ID

        Returns:
            Memory details
        """
        async with self.session() as session:
            result = await session.execute(select(Memory).where(Memory.id == memory_id))
            memory = result.scalar_one_or_none()

            if not memory:
                return None

            # Get paths pointing to this memory
            paths_result = await session.execute(
                select(Path).where(Path.memory_id == memory_id)
            )
            paths = [f"{p.domain}://{p.path}" for p in paths_result.scalars().all()]

            return {
                "memory_id": memory.id,
                "content": memory.content,
                # Importance/Disclosure removed
                "created_at": memory.created_at.isoformat()
                if memory.created_at
                else None,
                "deprecated": memory.deprecated,
                "migrated_to": memory.migrated_to,
                "paths": paths,
            }

    async def get_deprecated_memories(self) -> List[Dict[str, Any]]:
        """
        Get all deprecated memories for human's review.

        Returns:
            List of deprecated memories
        """
        async with self.session() as session:
            result = await session.execute(
                select(Memory)
                .where(Memory.deprecated == True)
                .order_by(Memory.created_at.desc())
            )

            memories = []
            for memory in result.scalars().all():
                memories.append(
                    {
                        "id": memory.id,
                        "content_snippet": memory.content[:200] + "..."
                        if len(memory.content) > 200
                        else memory.content,
                        "migrated_to": memory.migrated_to,
                        "created_at": memory.created_at.isoformat()
                        if memory.created_at
                        else None,
                    }
                )

            return memories

    async def _resolve_migration_chain(
        self, session: AsyncSession, start_id: int, max_hops: int = 50
    ) -> Optional[Dict[str, Any]]:
        """
        Follow the migrated_to chain from start_id to the final target.

        The final target is the memory at the end of the chain (migrated_to=NULL).
        Returns None if the chain is broken (missing memory) or too long (cycle).
        """
        current_id = start_id
        for _ in range(max_hops):
            result = await session.execute(
                select(Memory).where(Memory.id == current_id)
            )
            memory = result.scalar_one_or_none()
            if not memory:
                return None  # Broken chain
            if memory.migrated_to is None:
                # Final target reached
                paths_result = await session.execute(
                    select(Path).where(Path.memory_id == memory.id)
                )
                paths = [f"{p.domain}://{p.path}" for p in paths_result.scalars().all()]
                return {
                    "id": memory.id,
                    "content": memory.content,
                    "content_snippet": (
                        memory.content[:200] + "..."
                        if len(memory.content) > 200
                        else memory.content
                    ),
                    "created_at": memory.created_at.isoformat()
                    if memory.created_at
                    else None,
                    "deprecated": memory.deprecated,
                    "paths": paths,
                }
            current_id = memory.migrated_to
        return None  # Chain too long, likely a cycle

    async def get_all_orphan_memories(self) -> List[Dict[str, Any]]:
        """
        Get all orphan memories in the system.

        Two categories:
        - "deprecated": deprecated=True, created by update_memory. Has migrated_to.
        - "orphaned": deprecated=False but no paths point to it. Created by path deletion.

        For deprecated memories with migrated_to, resolves the migration chain to
        find the final target and its current paths.
        """
        async with self.session() as session:
            orphans = []

            # 1. Deprecated memories (from update_memory)
            deprecated_result = await session.execute(
                select(Memory)
                .where(Memory.deprecated == True)
                .order_by(Memory.created_at.desc())
            )

            for memory in deprecated_result.scalars().all():
                item = {
                    "id": memory.id,
                    "content_snippet": (
                        memory.content[:200] + "..."
                        if len(memory.content) > 200
                        else memory.content
                    ),
                    "created_at": memory.created_at.isoformat()
                    if memory.created_at
                    else None,
                    "deprecated": True,
                    "migrated_to": memory.migrated_to,
                    "category": "deprecated",
                    "migration_target": None,
                }

                if memory.migrated_to:
                    target = await self._resolve_migration_chain(
                        session, memory.migrated_to
                    )
                    if target:
                        item["migration_target"] = {
                            "id": target["id"],
                            "paths": target["paths"],
                            "content_snippet": target["content_snippet"],
                        }

                orphans.append(item)

            # 2. Truly orphaned memories (non-deprecated, no paths)
            orphaned_result = await session.execute(
                select(Memory)
                .outerjoin(Path, Memory.id == Path.memory_id)
                .where(Memory.deprecated == False)
                .where(Path.memory_id.is_(None))
                .order_by(Memory.created_at.desc())
            )

            for memory in orphaned_result.scalars().all():
                orphans.append(
                    {
                        "id": memory.id,
                        "content_snippet": (
                            memory.content[:200] + "..."
                            if len(memory.content) > 200
                            else memory.content
                        ),
                        "created_at": memory.created_at.isoformat()
                        if memory.created_at
                        else None,
                        "deprecated": False,
                        "migrated_to": memory.migrated_to,
                        "category": "orphaned",
                        "migration_target": None,
                    }
                )

            return orphans

    async def get_orphan_detail(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """
        Get full detail of an orphan memory for content viewing and diff comparison.

        Returns full content of both the orphan and its final migration target
        (if applicable).
        """
        async with self.session() as session:
            result = await session.execute(select(Memory).where(Memory.id == memory_id))
            memory = result.scalar_one_or_none()
            if not memory:
                return None

            # Determine category
            if memory.deprecated:
                category = "deprecated"
            else:
                paths_count_result = await session.execute(
                    select(func.count())
                    .select_from(Path)
                    .where(Path.memory_id == memory_id)
                )
                category = "orphaned" if paths_count_result.scalar() == 0 else "active"

            detail = {
                "id": memory.id,
                "content": memory.content,
                "created_at": memory.created_at.isoformat()
                if memory.created_at
                else None,
                "deprecated": memory.deprecated,
                "migrated_to": memory.migrated_to,
                "category": category,
                "migration_target": None,
            }

            # Resolve migration chain for diff comparison
            if memory.migrated_to:
                target = await self._resolve_migration_chain(
                    session, memory.migrated_to
                )
                if target:
                    detail["migration_target"] = {
                        "id": target["id"],
                        "content": target["content"],
                        "paths": target["paths"],
                        "created_at": target["created_at"],
                    }

            return detail

    async def permanently_delete_memory(
        self,
        memory_id: int,
        *,
        require_orphan: bool = False,
        expected_state_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Permanently delete a memory (human only).

        Before deletion, repairs the version chain: if any other memory
        has migrated_to pointing to this one, it will be updated to skip
        over and point to this memory's own migrated_to target.

        Example: A(migrated_to=B) → B(migrated_to=C) → C
                 Delete B → A(migrated_to=C) → C

        Args:
            memory_id: Memory ID to delete
            require_orphan: If True, verify the memory is still an orphan
                (deprecated or path-less) within the same transaction.
                Raises PermissionError if the memory has active paths.
            expected_state_hash: Optional stale-check hash generated from
                get_vitality_cleanup_candidates. When provided, deletion only
                proceeds if the current memory state hash matches.

        Returns:
            Deletion info

        Raises:
            ValueError: Memory ID not found
            PermissionError: Memory has active paths (only when require_orphan=True)
            RuntimeError: Candidate state hash changed (when expected_state_hash is provided)
        """
        async with self.session() as session:
            # 1. Get the memory being deleted
            target_result = await session.execute(
                select(
                    Memory.deprecated,
                    Memory.migrated_to,
                    Memory.vitality_score,
                    Memory.access_count,
                ).where(Memory.id == memory_id)
            )
            target_row = target_result.first()
            if not target_row:
                raise ValueError(f"Memory ID {memory_id} not found")

            deprecated, successor_id, vitality_score, access_count = target_row

            expected_hash_value = (expected_state_hash or "").strip()
            path_count: Optional[int] = None
            if require_orphan or expected_hash_value:
                path_count_result = await session.execute(
                    select(func.count())
                    .select_from(Path)
                    .where(Path.memory_id == memory_id)
                )
                path_count = int(path_count_result.scalar() or 0)

            if expected_hash_value:
                current_hash = self._build_vitality_state_hash(
                    memory_id=memory_id,
                    vitality_score=max(0.0, float(vitality_score or 0.0)),
                    access_count=max(0, int(access_count or 0)),
                    path_count=max(0, int(path_count or 0)),
                    deprecated=bool(deprecated),
                )
                if current_hash != expected_hash_value:
                    raise RuntimeError("stale_state")

            # 2. If caller requires orphan safety, verify within this transaction
            if require_orphan and not deprecated:
                if int(path_count or 0) > 0:
                    raise PermissionError(
                        f"Memory {memory_id} is no longer an orphan "
                        f"(has {int(path_count or 0)} active path(s)). Deletion aborted."
                    )

            # 3. Repair the chain: any memory pointing to the deleted node
            #    should now point to the deleted node's successor
            await session.execute(
                update(Memory)
                .where(Memory.migrated_to == memory_id)
                .values(migrated_to=successor_id)
            )

            # 4. Remove any paths pointing to this memory
            await session.execute(delete(Path).where(Path.memory_id == memory_id))

            # 5. Delete the memory
            result = await session.execute(delete(Memory).where(Memory.id == memory_id))

            if result.rowcount == 0:
                raise ValueError(f"Memory ID {memory_id} not found")

            return {"deleted_memory_id": memory_id, "chain_repaired_to": successor_id}


# =============================================================================
# Global Singleton
# =============================================================================

_sqlite_client: Optional[SQLiteClient] = None


def get_sqlite_client() -> SQLiteClient:
    """Get the global SQLiteClient instance."""
    global _sqlite_client
    if _sqlite_client is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError(
                "DATABASE_URL environment variable is not set. Please check your .env file."
            )
        _sqlite_client = SQLiteClient(database_url)
    return _sqlite_client


async def close_sqlite_client():
    """Close the global SQLiteClient connection."""
    global _sqlite_client
    if _sqlite_client:
        await _sqlite_client.close()
        _sqlite_client = None
