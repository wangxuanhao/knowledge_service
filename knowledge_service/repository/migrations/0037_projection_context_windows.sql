-- Each canonical fact may have multiple correlated supports and text windows.
-- Keep legacy per-record receipts intact for old generations and rollback.
CREATE TABLE IF NOT EXISTS milvus_vector_receipts (
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    index_version bigint NOT NULL CHECK (index_version>0),
    vector_id text NOT NULL CHECK (length(vector_id)>0),
    record_id text NOT NULL CHECK (length(record_id)>0),
    version_id text NOT NULL CHECK (length(version_id)>0),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (project_id,index_version,vector_id)
);
CREATE INDEX IF NOT EXISTS milvus_vector_receipts_record
    ON milvus_vector_receipts(project_id,index_version,record_id);

-- Rebuild passes must use the exact labels/supports captured before encoding.
ALTER TABLE projection_rebuild_snapshots ADD COLUMN IF NOT EXISTS projection_input text;

DO $$
DECLARE app_role text := COALESCE(NULLIF(current_setting('knowledge_service.app_role',true),''),'knowledge_app');
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
        EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON milvus_vector_receipts TO %I',app_role);
    END IF;
END $$;
