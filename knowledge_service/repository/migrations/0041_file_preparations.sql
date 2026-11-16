CREATE TABLE preparation_sessions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 owner_id TEXT NOT NULL, payload JSONB NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL DEFAULT (now()+interval '7 days'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX preparation_sessions_owner ON preparation_sessions(project_id,owner_id,created_at);
CREATE TABLE preparation_files (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES preparation_sessions(id) ON DELETE CASCADE,
 content BYTEA NOT NULL
);
CREATE TABLE preparation_tasks (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'queued',
 file_id TEXT REFERENCES preparation_files(id) ON DELETE SET NULL,
 original_name TEXT NOT NULL, options JSONB NOT NULL, result JSONB,
 lease_token TEXT, lease_until TIMESTAMPTZ, heartbeat_at TIMESTAMPTZ, started_at TIMESTAMPTZ,
 error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,fingerprint)
);
ALTER TABLE ingest_batch_items ADD COLUMN prepared_result JSONB;
DO $$
DECLARE app_role TEXT := COALESCE(NULLIF(current_setting('knowledge_service.app_role',true),''),'knowledge_app');
BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON preparation_sessions,preparation_files,preparation_tasks TO %I',app_role);
 END IF;
END $$;
CREATE TABLE preparation_operations (
 project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 operation TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 session_id TEXT NOT NULL REFERENCES preparation_sessions(id) ON DELETE CASCADE,
 request_hash TEXT NOT NULL,
 PRIMARY KEY(project_id,operation,idempotency_key)
);
DO $$
DECLARE app_role TEXT := COALESCE(NULLIF(current_setting('knowledge_service.app_role',true),''),'knowledge_app');
BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON preparation_operations TO %I',app_role);
 END IF;
END $$;
CREATE INDEX preparation_sessions_expiry ON preparation_sessions(expires_at);
CREATE INDEX preparation_files_session ON preparation_files(session_id);
CREATE INDEX preparation_operations_session ON preparation_operations(session_id);
CREATE INDEX preparation_tasks_dispatch ON preparation_tasks(status,created_at)
 WHERE status IN ('queued','running','cancelling');
CREATE INDEX preparation_source_object_lookup ON source_document_versions(project_id,source_object_id);
CREATE INDEX preparation_ingest_source_lookup ON ingest_batch_items(project_id,source_version_id)
 WHERE source_version_id IS NOT NULL;
