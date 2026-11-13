-- Preserve the established JSON truthiness contract for legacy deletion flags.
-- An empty string/list/object or numeric zero is visible, not a boolean cast.
CREATE OR REPLACE FUNCTION identity_key_record_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k text; p jsonb;
BEGIN
 DELETE FROM entity_identity_keys WHERE project_id=NEW.project_id AND record_id=NEW.id;
 p=NEW.payload::jsonb;
 IF NEW.superseded_at IS NULL AND p->>'kind'='entity'
    AND coalesce(p#>'{metadata,_deleted}','null'::jsonb)
        IN ('null'::jsonb,'false'::jsonb,'0'::jsonb,'""'::jsonb,'[]'::jsonb,'{}'::jsonb)
    AND p#>>'{metadata,identity_policy}' IN ('reference','scoped') THEN
  FOR k IN SELECT jsonb_array_elements_text(coalesce(p#>'{metadata,identity_key_fields}','[]'::jsonb)) LOOP
   IF p#>ARRAY['metadata','identity_keys',k] IS NOT NULL THEN
    INSERT INTO entity_identity_keys VALUES(NEW.project_id,p->>'type',
      coalesce(p#>>'{metadata,identity_namespace}',''),k,(p#>ARRAY['metadata','identity_keys',k])::text,NEW.id,NEW.version_id);
   END IF;
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
