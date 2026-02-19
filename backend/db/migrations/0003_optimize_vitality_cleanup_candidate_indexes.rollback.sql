-- Week 6.5 rollback script.
-- Remove vitality cleanup candidate query indexes introduced by 0003.

DROP INDEX IF EXISTS idx_paths_memory_domain_path;
DROP INDEX IF EXISTS idx_memories_cleanup_created;
DROP INDEX IF EXISTS idx_memories_cleanup_last_accessed;
