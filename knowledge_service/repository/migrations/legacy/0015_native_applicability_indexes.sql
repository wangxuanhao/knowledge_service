-- knowledge-service-migration: no-transaction
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_platform_mode
    ON record_versions (project_id, platform_mode) WHERE superseded_at IS NULL;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_business_mode
    ON record_versions (project_id, business_mode) WHERE superseded_at IS NULL;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_audience_mode
    ON record_versions (project_id, audience_mode) WHERE superseded_at IS NULL;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_platform_values_gin
    ON record_versions USING gin (platform_values)
    WHERE superseded_at IS NULL AND platform_mode = 'specific';
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_business_values_gin
    ON record_versions USING gin (business_values)
    WHERE superseded_at IS NULL AND business_mode = 'specific';
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_audience_values_gin
    ON record_versions USING gin (audience_values)
    WHERE superseded_at IS NULL AND audience_mode = 'specific';
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_valid_from
    ON record_versions (project_id, valid_from_epoch_ms)
    WHERE superseded_at IS NULL AND valid_from_known;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_valid_until
    ON record_versions (project_id, valid_until_epoch_ms)
    WHERE superseded_at IS NULL AND valid_until_known;
-- knowledge-service-migration: statement-break
-- 0001 already has (project_id, recorded_at, superseded_at). Reverse the two
-- range columns so historical visibility can start from the system end bound.
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_system_end
    ON record_versions (project_id, superseded_at, recorded_at);
