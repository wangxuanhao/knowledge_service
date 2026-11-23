-- Durable retirement markers also survive deletion of the authority project.
CREATE TABLE IF NOT EXISTS milvus_generation_cleanup_outbox (
    project_id text NOT NULL,
    index_version bigint NOT NULL CHECK (index_version > 0),
    retired_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    not_before timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claim_owner text,
    claim_token text,
    claim_until timestamptz,
    cleaned_at timestamptz,
    last_error text,
    PRIMARY KEY (project_id,index_version)
);
CREATE INDEX IF NOT EXISTS milvus_generation_cleanup_claimable
    ON milvus_generation_cleanup_outbox(not_before,claim_until);

INSERT INTO milvus_generation_cleanup_outbox(project_id,index_version)
SELECT project_id,(rebuild_state->>'previous_index_version')::bigint
FROM projection_cursors WHERE target='milvus'
  AND (rebuild_state->>'previous_index_version') ~ '^[1-9][0-9]*$'
  AND (rebuild_state->>'index_version') ~ '^[1-9][0-9]*$'
  AND (rebuild_state->>'previous_index_version')::bigint < (rebuild_state->>'index_version')::bigint
ON CONFLICT DO NOTHING;

DO $$
DECLARE app_role text := COALESCE(NULLIF(current_setting('knowledge_service.app_role',true),''),'knowledge_app');
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
        EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON milvus_generation_cleanup_outbox TO %I',app_role);
    END IF;
END $$;
