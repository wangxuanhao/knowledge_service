-- Add a nullable header without rewriting existing snapshot bodies.
ALTER TABLE artifacts ADD COLUMN snapshot_summary text;

CREATE FUNCTION write_snapshot_summary() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.snapshot_summary := (NEW.payload::jsonb - ARRAY['records', 'ontologies'])::text;
    RETURN NEW;
END;
$$;

CREATE TRIGGER artifacts_snapshot_summary
    BEFORE INSERT OR UPDATE OF kind, payload ON artifacts
    FOR EACH ROW WHEN (NEW.kind = 'snapshot')
    EXECUTE FUNCTION write_snapshot_summary();
