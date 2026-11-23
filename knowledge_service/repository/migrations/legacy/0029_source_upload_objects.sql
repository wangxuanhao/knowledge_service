-- Original upload bytes now live in the configured content-addressed object store.
-- Existing BYTEA rows remain readable until explicitly migrated and verified.
ALTER TABLE ingest_batch_items ALTER COLUMN content DROP NOT NULL;
ALTER TABLE ingest_batch_items ADD COLUMN upload_object_id TEXT REFERENCES source_objects(id);
ALTER TABLE ingest_batch_items ADD CONSTRAINT ingest_item_upload_bytes_or_object
 CHECK (content IS NOT NULL OR upload_object_id IS NOT NULL);
CREATE INDEX ingest_batch_items_upload_object_idx ON ingest_batch_items(upload_object_id)
 WHERE upload_object_id IS NOT NULL;
