-- knowledge-service-migration: no-transaction
-- Source refresh also visits hidden attributes to retire old indexed rows.
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_projection_attribute_owner
    ON record_versions (project_id, read_subject_id, id)
    WHERE superseded_at IS NULL AND read_kind = 'attribute';
