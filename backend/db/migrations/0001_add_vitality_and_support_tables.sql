-- Week 1 bootstrap migration.
-- Subtask 1-2 baseline: initialize schema_migrations + memory lifecycle columns.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL
);

ALTER TABLE memories ADD COLUMN vitality_score REAL DEFAULT 1.0;
ALTER TABLE memories ADD COLUMN last_accessed_at DATETIME;
ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;

UPDATE memories SET vitality_score = 1.0 WHERE vitality_score IS NULL;
UPDATE memories SET access_count = 0 WHERE access_count IS NULL;

CREATE TABLE IF NOT EXISTS memory_gists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    gist_text TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    gist_method TEXT NOT NULL DEFAULT 'fallback',
    quality_score REAL,
    created_at DATETIME,
    FOREIGN KEY(memory_id) REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_gists_memory_id
    ON memory_gists(memory_id);
DROP INDEX IF EXISTS ix_memory_gists_memory_id;

CREATE TABLE IF NOT EXISTS memory_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    tag_type TEXT NOT NULL,
    tag_value TEXT NOT NULL,
    confidence REAL,
    created_at DATETIME,
    FOREIGN KEY(memory_id) REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_tags_value
    ON memory_tags(tag_value);

CREATE INDEX IF NOT EXISTS idx_memory_tags_memory_id
    ON memory_tags(memory_id);
DROP INDEX IF EXISTS ix_memory_tags_memory_id;
