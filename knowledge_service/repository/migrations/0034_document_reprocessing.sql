-- Processing attempts retain the stable document identity across generations.
ALTER TABLE ingest_batch_items DROP CONSTRAINT ingest_batch_items_document_id_key;
CREATE INDEX ingest_batch_items_document_history
 ON ingest_batch_items(project_id,document_id,created_at);
