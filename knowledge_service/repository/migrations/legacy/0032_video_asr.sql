CREATE TABLE source_asset_asr_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_version_id TEXT NOT NULL, asset_id TEXT NOT NULL,
 asset_sha256 TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('completed','failed')),
 attempt INTEGER NOT NULL CHECK(attempt>0), engine TEXT NOT NULL, engine_version TEXT NOT NULL,
 configuration_fingerprint TEXT NOT NULL, duration_ms INTEGER CHECK(duration_ms>0),
 text_sha256 TEXT, normalized_text TEXT NOT NULL, error_code TEXT, public_error TEXT,
 started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
 FOREIGN KEY(project_id,document_version_id,asset_id) REFERENCES source_assets(project_id,document_version_id,id) ON DELETE CASCADE,
 UNIQUE(project_id,document_version_id,id)
);
CREATE INDEX source_asr_reuse ON source_asset_asr_runs(project_id,asset_sha256,configuration_fingerprint,status);
CREATE TABLE source_asset_asr_segments (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_version_id TEXT NOT NULL, asr_run_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0), start_ms INTEGER NOT NULL CHECK(start_ms>=0),
 end_ms INTEGER NOT NULL CHECK(end_ms>start_ms), text TEXT NOT NULL, normalized_text TEXT NOT NULL,
 FOREIGN KEY(project_id,document_version_id,asr_run_id) REFERENCES source_asset_asr_runs(project_id,document_version_id,id) ON DELETE CASCADE,
 UNIQUE(project_id,document_version_id,id), UNIQUE(asr_run_id,ordinal)
);
ALTER TABLE chunk_source_spans ADD COLUMN asr_segment_id TEXT;
ALTER TABLE chunk_source_spans ADD CONSTRAINT chunk_source_spans_asr_segment_fk
 FOREIGN KEY(project_id,document_version_id,asr_segment_id) REFERENCES source_asset_asr_segments(project_id,document_version_id,id) ON DELETE CASCADE;
ALTER TABLE chunk_source_spans DROP CONSTRAINT chunk_source_spans_check;
ALTER TABLE chunk_source_spans ADD CONSTRAINT chunk_source_spans_source_check
 CHECK(block_id IS NOT NULL OR ocr_block_id IS NOT NULL OR asr_segment_id IS NOT NULL);
DO $$
DECLARE app_role TEXT := COALESCE(NULLIF(current_setting('knowledge_service.app_role', true), ''), 'knowledge_app');
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON source_asset_asr_runs, source_asset_asr_segments TO %I',app_role);
 END IF;
END $$;
