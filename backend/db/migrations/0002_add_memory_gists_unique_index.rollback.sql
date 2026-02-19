-- Week 4 rollback script.
-- Remove unique gist upsert key index.

DROP INDEX IF EXISTS idx_memory_gists_memory_source_hash_unique;
