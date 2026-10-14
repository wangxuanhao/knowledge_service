CREATE TABLE source_objects (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256)=64),
 size_bytes BIGINT NOT NULL CHECK(size_bytes>=0), mime_type TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE source_document_versions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 document_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 source_object_id TEXT NOT NULL REFERENCES source_objects(id), original_name TEXT NOT NULL,
 format TEXT NOT NULL, parser_name TEXT NOT NULL, parser_version TEXT NOT NULL,
 parse_status TEXT NOT NULL, source_text TEXT NOT NULL, rendered_html TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(project_id,document_id,version), UNIQUE(project_id,id)
);
CREATE TABLE source_assets (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_version_id TEXT NOT NULL,
 source_object_id TEXT REFERENCES source_objects(id), original_path TEXT NOT NULL,
 resolved_path TEXT NOT NULL, alt_text TEXT NOT NULL, width INTEGER, height INTEGER,
 status TEXT NOT NULL, kind TEXT NOT NULL, source_start_line INTEGER, source_end_line INTEGER,
 ignored_by TEXT, ignored_at TEXT, ignore_reason TEXT,
 FOREIGN KEY(project_id,document_version_id) REFERENCES source_document_versions(project_id,id) ON DELETE CASCADE,
 UNIQUE(project_id,document_version_id,id)
);
CREATE TABLE parsed_blocks (
 block_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_version_id TEXT NOT NULL,
 type TEXT NOT NULL, ordinal INTEGER NOT NULL, normalized_text TEXT NOT NULL,
 source_start_char INTEGER NOT NULL, source_end_char INTEGER NOT NULL,
 source_start_line INTEGER NOT NULL, source_end_line INTEGER NOT NULL,
 heading_path TEXT NOT NULL CHECK(jsonb_typeof(heading_path::jsonb)='array'),
 payload TEXT NOT NULL CHECK(jsonb_typeof(payload::jsonb)='object'),
 FOREIGN KEY(project_id,document_version_id) REFERENCES source_document_versions(project_id,id) ON DELETE CASCADE,
 UNIQUE(project_id,document_version_id,block_id)
);
CREATE TABLE source_asset_ocr_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_version_id TEXT NOT NULL, asset_id TEXT NOT NULL,
 asset_sha256 TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL,
 engine TEXT NOT NULL, engine_version TEXT NOT NULL, language_config TEXT NOT NULL,
 preprocess_fingerprint TEXT NOT NULL, configuration_fingerprint TEXT NOT NULL,
 average_confidence DOUBLE PRECISION, text_sha256 TEXT, normalized_text TEXT NOT NULL,
 error_code TEXT, public_error TEXT, started_at TEXT NOT NULL, completed_at TEXT,
 FOREIGN KEY(project_id,document_version_id,asset_id) REFERENCES source_assets(project_id,document_version_id,id) ON DELETE CASCADE,
 UNIQUE(project_id,document_version_id,id)
);
CREATE INDEX source_ocr_reuse ON source_asset_ocr_runs(project_id,asset_sha256,configuration_fingerprint,status);
CREATE TABLE source_asset_ocr_blocks (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_version_id TEXT NOT NULL, ocr_run_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL, text TEXT NOT NULL, normalized_text TEXT NOT NULL, confidence DOUBLE PRECISION NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 x DOUBLE PRECISION NOT NULL CHECK(x BETWEEN 0 AND 1), y DOUBLE PRECISION NOT NULL CHECK(y BETWEEN 0 AND 1),
 width DOUBLE PRECISION NOT NULL CHECK(width BETWEEN 0 AND 1), height DOUBLE PRECISION NOT NULL CHECK(height BETWEEN 0 AND 1), page_or_frame INTEGER NOT NULL DEFAULT 1,
 FOREIGN KEY(project_id,document_version_id,ocr_run_id) REFERENCES source_asset_ocr_runs(project_id,document_version_id,id) ON DELETE CASCADE,
 UNIQUE(project_id,document_version_id,id)
);
CREATE UNIQUE INDEX source_chunk_version_project_fk ON record_versions(project_id,version_id);
CREATE TABLE chunk_source_spans (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_version_id TEXT NOT NULL,
 chunk_version_id TEXT NOT NULL, block_id TEXT, ocr_block_id TEXT,
 ordinal INTEGER NOT NULL, chunk_start_char INTEGER NOT NULL, chunk_end_char INTEGER NOT NULL,
 block_start_char INTEGER NOT NULL, block_end_char INTEGER NOT NULL,
 payload TEXT NOT NULL CHECK(jsonb_typeof(payload::jsonb)='object'),
 FOREIGN KEY(project_id,document_version_id) REFERENCES source_document_versions(project_id,id) ON DELETE CASCADE,
 FOREIGN KEY(project_id,chunk_version_id) REFERENCES record_versions(project_id,version_id) ON DELETE CASCADE,
 FOREIGN KEY(project_id,document_version_id,block_id) REFERENCES parsed_blocks(project_id,document_version_id,block_id) ON DELETE CASCADE,
 FOREIGN KEY(project_id,document_version_id,ocr_block_id) REFERENCES source_asset_ocr_blocks(project_id,document_version_id,id) ON DELETE CASCADE,
 CHECK(block_id IS NOT NULL OR ocr_block_id IS NOT NULL),
 UNIQUE(project_id,document_version_id,chunk_version_id,ordinal)
);
