-- knowledge-service-migration: no-transaction
-- The summary index is optional read acceleration. Including both complete
-- endpoint IDs can exceed a btree tuple even when each identity fits its
-- existing primary/keyset indexes. Keep grouping/validity acceleration and
-- read the unchanged identities from the heap when qualification needs them.
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_visible_summary_compact
    ON record_versions (project_id, read_kind, read_type)
    INCLUDE (valid_from_known)
    WHERE superseded_at IS NULL AND read_visible;
-- knowledge-service-migration: statement-break
-- Build the replacement before removing the old index. Both statements are
-- retryable; existing migration checks validate the replacement before ledgering.
DROP INDEX CONCURRENTLY IF EXISTS versions_visible_summary;
