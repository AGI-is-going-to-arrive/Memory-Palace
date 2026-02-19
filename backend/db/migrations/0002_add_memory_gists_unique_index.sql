-- Week 4 hardening migration.
-- Enforce deterministic gist upsert key: (memory_id, source_content_hash).

UPDATE memory_gists
SET source_content_hash = COALESCE(source_content_hash, 'legacy:' || id);

UPDATE memory_gists
SET gist_method = COALESCE(gist_method, 'fallback');

UPDATE memory_gists
SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP);

DELETE FROM memory_gists
WHERE id NOT IN (
    SELECT MAX(id)
    FROM memory_gists
    GROUP BY memory_id, source_content_hash
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_gists_memory_source_hash_unique
    ON memory_gists(memory_id, source_content_hash);
