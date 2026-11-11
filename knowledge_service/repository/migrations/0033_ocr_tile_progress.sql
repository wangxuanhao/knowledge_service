ALTER TABLE source_asset_ocr_runs ADD COLUMN processing_details TEXT NOT NULL DEFAULT '{}'
 CHECK(jsonb_typeof(processing_details::jsonb)='object');
