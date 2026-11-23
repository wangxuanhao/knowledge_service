-- Current drafts retain source context and governed operation history directly.
-- No current writer or reader uses the former discovery-artifact link.
ALTER TABLE ontology_drafts DROP COLUMN legacy_artifact_id;
