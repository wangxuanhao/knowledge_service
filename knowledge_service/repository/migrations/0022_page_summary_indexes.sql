-- knowledge-service-migration: no-transaction
-- Page summaries look up only source-linked or candidate-linked records.
-- Keep the authoritative text payload and all historical versions unchanged.
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_source_directory
    ON record_versions (project_id, (payload::jsonb->>'source_id'), id, version)
    WHERE payload::jsonb->>'source_id' IS NOT NULL;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_candidate_single
    ON record_versions (project_id, (payload::jsonb#>>'{metadata,discovery_candidate_id}'))
    WHERE superseded_at IS NULL
      AND payload::jsonb#>>'{metadata,discovery_candidate_id}' IS NOT NULL;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_candidate_many
    ON record_versions USING gin ((payload::jsonb#>'{metadata,discovery_candidate_ids}'))
    WHERE superseded_at IS NULL
      AND payload::jsonb#>'{metadata,discovery_candidate_ids}' IS NOT NULL;
