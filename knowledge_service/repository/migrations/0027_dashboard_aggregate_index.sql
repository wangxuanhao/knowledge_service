-- knowledge-service-migration: no-transaction
-- Current project statistics need compact kinds, types, validity and endpoint
-- identities, never the large authoritative record payloads.
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_visible_summary
    ON record_versions (project_id, read_kind, read_type)
    INCLUDE (id, valid_from_known, read_subject_id, read_object_id)
    WHERE superseded_at IS NULL AND read_visible;
