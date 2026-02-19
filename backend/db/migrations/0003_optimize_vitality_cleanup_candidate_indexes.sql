-- Week 6.5 performance migration.
-- Optimize vitality cleanup candidate query with focused indexes.

CREATE INDEX IF NOT EXISTS idx_memories_cleanup_last_accessed
    ON memories(deprecated, vitality_score, last_accessed_at, id);

CREATE INDEX IF NOT EXISTS idx_memories_cleanup_created
    ON memories(deprecated, vitality_score, created_at, id);

CREATE INDEX IF NOT EXISTS idx_paths_memory_domain_path
    ON paths(memory_id, domain, path);
