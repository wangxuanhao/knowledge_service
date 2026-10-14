-- Migrations run as the owner; ingestion runs as a role without DDL rights.
-- Target only the new data tables, including on installations without owner
-- default privileges. The migrator supplies a configurable role name safely.
DO $$
DECLARE app_role TEXT := COALESCE(NULLIF(current_setting('knowledge_service.app_role', true), ''), 'knowledge_app');
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON ingest_batches, ingest_batch_items, ingest_item_attempts, ingest_chunk_checkpoints, source_objects, source_document_versions, source_assets, parsed_blocks, source_asset_ocr_runs, source_asset_ocr_blocks, chunk_source_spans TO %I',
            app_role
        );
    END IF;
END $$;
