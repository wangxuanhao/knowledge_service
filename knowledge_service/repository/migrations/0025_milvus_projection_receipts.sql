-- Internal delivery receipts; PostgreSQL retains the authoritative business rows.
CREATE TABLE IF NOT EXISTS milvus_projection_receipts (
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    index_version bigint NOT NULL CHECK (index_version > 0),
    record_id text NOT NULL CHECK (length(record_id) > 0),
    version_id text NOT NULL CHECK (length(version_id) > 0),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (project_id, index_version, record_id)
);

-- Reject malformed legacy manifests before touching either representation.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM projection_cursors
        WHERE target='milvus' AND jsonb_exists(rebuild_state,'expected_projection')
          AND (jsonb_typeof(rebuild_state->'expected_projection') IS DISTINCT FROM 'object'
            OR COALESCE(rebuild_state->>'index_version','') !~ '^[1-9][0-9]*$')
    ) THEN
        RAISE EXCEPTION 'Invalid legacy Milvus projection manifest or generation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM projection_cursors c,
             LATERAL jsonb_each(c.rebuild_state->'expected_projection') receipt
        WHERE c.target='milvus' AND jsonb_exists(c.rebuild_state,'expected_projection')
          AND (receipt.key='' OR jsonb_typeof(receipt.value) IS DISTINCT FROM 'object'
            OR jsonb_typeof(receipt.value->'version_id') IS DISTINCT FROM 'string'
            OR COALESCE(receipt.value->>'version_id','')=''
            OR jsonb_typeof(receipt.value->'fingerprint') IS DISTINCT FROM 'string'
            OR COALESCE(receipt.value->>'fingerprint','') !~ '^[0-9a-f]{64}$')
    ) THEN
        RAISE EXCEPTION 'Invalid legacy Milvus projection receipt';
    END IF;
END $$;

DELETE FROM milvus_projection_receipts r USING projection_cursors c
WHERE c.target='milvus' AND jsonb_exists(c.rebuild_state,'expected_projection')
  AND r.project_id=c.project_id
  AND r.index_version=(c.rebuild_state->>'index_version')::bigint;

INSERT INTO milvus_projection_receipts
    (project_id,index_version,record_id,version_id,fingerprint)
SELECT c.project_id,(c.rebuild_state->>'index_version')::bigint,
       receipt.key,receipt.value->>'version_id',receipt.value->>'fingerprint'
FROM projection_cursors c,
     LATERAL jsonb_each(c.rebuild_state->'expected_projection') receipt
WHERE c.target='milvus' AND jsonb_exists(c.rebuild_state,'expected_projection');

-- The migration runner commits this removal atomically with the backfill.
UPDATE projection_cursors SET rebuild_state=rebuild_state-'expected_projection'
WHERE target='milvus' AND jsonb_exists(rebuild_state,'expected_projection');

DO $$
DECLARE app_role text := COALESCE(NULLIF(current_setting('knowledge_service.app_role',true),''),'knowledge_app');
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
        EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON milvus_projection_receipts TO %I',app_role);
    END IF;
END $$;
