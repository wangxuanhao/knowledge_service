-- knowledge-service-migration: no-transaction
-- Complete graph keyset pages and local adjacency must not rescan/decode the
-- project payload on each page. Keep authoritative payload bytes unchanged.
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_kind_keyset
    ON record_versions (project_id, (payload::jsonb->>'kind'), id, version)
    WHERE superseded_at IS NULL;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_subject_keyset
    ON record_versions (project_id, (payload::jsonb->>'subject_id'), id)
    WHERE superseded_at IS NULL;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_object_keyset
    ON record_versions (project_id, (payload::jsonb->>'object_id'), id)
    WHERE superseded_at IS NULL;
