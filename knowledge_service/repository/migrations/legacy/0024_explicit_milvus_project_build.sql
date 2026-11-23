-- Incremental workers must leave unhealthy generations for an explicit page job.
ALTER TABLE projection_cursors
    DROP CONSTRAINT projection_cursors_status_check;

ALTER TABLE projection_cursors
    ADD CONSTRAINT projection_cursors_status_check CHECK (status IN (
        'pending', 'applying', 'ready', 'failed', 'rebuilding',
        'validating', 'switching', 'requires_rebuild'));
