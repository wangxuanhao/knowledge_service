ALTER TABLE source_document_versions ADD COLUMN parser_metadata TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(parser_metadata::jsonb)='object');
ALTER TABLE source_assets ADD COLUMN provenance TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(provenance::jsonb)='object');
