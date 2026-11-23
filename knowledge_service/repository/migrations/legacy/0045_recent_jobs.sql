-- knowledge-service-migration: no-transaction
-- The global task monitor reads the newest receipts across all projects.
CREATE INDEX CONCURRENTLY IF NOT EXISTS artifacts_recent_jobs
    ON artifacts (seq DESC)
    WHERE kind = 'job';
