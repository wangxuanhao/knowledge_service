CREATE TABLE ingest_batches (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','running','paused','cancelled','completed')),
 settings_json JSONB NOT NULL CHECK(jsonb_typeof(settings_json)='object'),
 settings_fingerprint TEXT NOT NULL, requested_concurrency TEXT NOT NULL DEFAULT 'auto',
 effective_concurrency INTEGER NOT NULL DEFAULT 1 CHECK(effective_concurrency>0),
 created_by TEXT, paused_reason TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
 UNIQUE(project_id,id)
);
CREATE TABLE ingest_batch_items (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, project_id TEXT NOT NULL,
 document_id TEXT NOT NULL UNIQUE, source_document_id TEXT, source_version_id TEXT, source_restart_of TEXT,
 document_generation INTEGER NOT NULL DEFAULT 0,
 original_name TEXT NOT NULL, content BYTEA NOT NULL, size_bytes BIGINT NOT NULL CHECK(size_bytes>=0),
 file_sha256 TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 options_json JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(options_json)='object'),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','retry_wait','succeeded','failed','cancelled','interrupted')),
 stage TEXT NOT NULL DEFAULT 'queued', progress DOUBLE PRECISION NOT NULL DEFAULT 0,
 attempt INTEGER NOT NULL DEFAULT 0, auto_retry_count INTEGER NOT NULL DEFAULT 0,
 next_retry_at TIMESTAMPTZ, error_code TEXT, error_message TEXT, public_error TEXT,
 failed_chunk_ordinal INTEGER, result JSONB, retry_mode TEXT NOT NULL DEFAULT 'resume_checkpoint',
 lease_owner TEXT, lease_token TEXT, lease_until TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), completed_at TIMESTAMPTZ,
 FOREIGN KEY(project_id,batch_id) REFERENCES ingest_batches(project_id,id) ON DELETE CASCADE,
 UNIQUE(batch_id,idempotency_key)
);
CREATE INDEX ingest_items_claim ON ingest_batch_items(batch_id,status,next_retry_at,created_at);
CREATE INDEX ingest_items_lease ON ingest_batch_items(lease_until) WHERE status='running';
CREATE INDEX ingest_batches_project ON ingest_batches(project_id,created_at);
CREATE TABLE ingest_item_attempts (
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES ingest_batch_items(id) ON DELETE CASCADE,
 attempt INTEGER NOT NULL, lease_owner TEXT NOT NULL, lease_token TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'running', error_code TEXT, error_message TEXT, result JSONB,
 started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), completed_at TIMESTAMPTZ,
 UNIQUE(item_id,attempt)
);
CREATE TABLE ingest_chunk_checkpoints (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL, chunk_ordinal INTEGER NOT NULL CHECK(chunk_ordinal>=0),
 configuration_fingerprint TEXT NOT NULL, batch_item_id TEXT REFERENCES ingest_batch_items(id) ON DELETE CASCADE,
 attempt INTEGER NOT NULL DEFAULT 0, document_generation INTEGER NOT NULL DEFAULT 0, chunk_id TEXT, chunk_sha256 TEXT,
 status TEXT NOT NULL DEFAULT 'succeeded' CHECK(status='succeeded'),
 output_json JSONB NOT NULL, diagnostics_json JSONB NOT NULL DEFAULT '{}',
 started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), completed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(document_id,chunk_ordinal,configuration_fingerprint)
);
