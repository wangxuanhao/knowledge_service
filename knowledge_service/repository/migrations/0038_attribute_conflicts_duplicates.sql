-- Disputed facts keep their immutable revisions and exact source decisions.
ALTER TABLE record_versions ADD COLUMN read_quarantined boolean GENERATED ALWAYS AS (
 coalesce(payload::jsonb#>'{metadata,_quarantined}','false'::jsonb)='true'::jsonb
) STORED;

CREATE TABLE attribute_conflict_sets (
 project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 id text NOT NULL, entity_id text NOT NULL, predicate text NOT NULL,
 version integer NOT NULL DEFAULT 1 CHECK(version>0),
 state text NOT NULL CHECK(state IN ('open','resolved','reversed')),
 operation_id text NOT NULL, created_at timestamptz NOT NULL,
 resolved_at timestamptz, reason text, actor text,
 decisions jsonb NOT NULL DEFAULT '[]'::jsonb,
 PRIMARY KEY(project_id,id)
);
CREATE INDEX attribute_conflict_entity ON attribute_conflict_sets(project_id,entity_id,id)
 WHERE state='open';
CREATE INDEX attribute_conflict_history ON attribute_conflict_sets(project_id,entity_id,created_at,resolved_at);
CREATE INDEX attribute_conflict_operation ON attribute_conflict_sets(project_id,operation_id);
CREATE TABLE attribute_conflict_members (
 project_id text NOT NULL, set_id text NOT NULL, record_id text NOT NULL,
 version integer NOT NULL, version_id text NOT NULL REFERENCES record_versions(version_id),
 assertion_versions jsonb NOT NULL,
 PRIMARY KEY(project_id,set_id,record_id),
 FOREIGN KEY(project_id,set_id) REFERENCES attribute_conflict_sets(project_id,id) ON DELETE CASCADE
);
CREATE INDEX attribute_conflict_member_record ON attribute_conflict_members(project_id,record_id);

-- Indexed, rebuildable duplicate keys. No corpus payloads are decoded by the
-- scan endpoint. Alias/name keys are candidates, never a uniqueness rule.
CREATE TABLE entity_duplicate_keys (
 project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 class_iri text NOT NULL, identity_scope text NOT NULL,
 match_kind text NOT NULL, match_key text NOT NULL, record_id text NOT NULL,
 PRIMARY KEY(project_id,class_iri,identity_scope,match_kind,match_key,record_id)
);
CREATE INDEX entity_duplicate_record ON entity_duplicate_keys(project_id,record_id);
CREATE FUNCTION entity_duplicate_record_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb; name text; key_name text; key_value jsonb; scope text;
BEGIN
 IF NEW.superseded_at IS NOT NULL THEN RETURN NEW; END IF;
 DELETE FROM entity_duplicate_keys WHERE project_id=NEW.project_id AND record_id=NEW.id;
 p=NEW.payload::jsonb;
 IF p->>'kind'<>'entity' OR btrim(coalesce(p->>'type',''))='' OR NOT NEW.read_visible OR NEW.read_quarantined THEN RETURN NEW; END IF;
 scope=coalesce((p#>'{metadata,identity_scope}')::text,'""');
 FOR name IN SELECT p->>'text' UNION SELECT jsonb_array_elements_text(
   CASE WHEN jsonb_typeof(p#>'{metadata,aliases}')='array' THEN p#>'{metadata,aliases}' ELSE '[]'::jsonb END)
 LOOP
  name=lower(btrim(regexp_replace(normalize(coalesce(name,''),NFKC),'\s+',' ','g')));
  IF name<>'' THEN INSERT INTO entity_duplicate_keys VALUES(NEW.project_id,p->>'type',scope,'name',name,NEW.id) ON CONFLICT DO NOTHING; END IF;
 END LOOP;
 FOR key_name,key_value IN SELECT key,value FROM jsonb_each(
  CASE WHEN jsonb_typeof(p#>'{metadata,identity_keys}')='object' THEN p#>'{metadata,identity_keys}' ELSE '{}'::jsonb END)
 LOOP
  INSERT INTO entity_duplicate_keys VALUES(NEW.project_id,p->>'type',scope,'key:'||key_name,key_value::text,NEW.id) ON CONFLICT DO NOTHING;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER entity_duplicate_record AFTER INSERT ON record_versions
 FOR EACH ROW EXECUTE FUNCTION entity_duplicate_record_changed();
-- Event-free read projection backfill; this does not merge or revise facts.
INSERT INTO entity_duplicate_keys
 SELECT DISTINCT r.project_id,r.read_type,coalesce((r.payload::jsonb#>'{metadata,identity_scope}')::text,'""'),
 'name',lower(btrim(regexp_replace(normalize(n.name,NFKC),'\s+',' ','g'))),r.id
 FROM record_versions r CROSS JOIN LATERAL (
  SELECT r.payload::jsonb->>'text' AS name UNION SELECT jsonb_array_elements_text(
   CASE WHEN jsonb_typeof(r.payload::jsonb#>'{metadata,aliases}')='array' THEN r.payload::jsonb#>'{metadata,aliases}' ELSE '[]'::jsonb END)
 ) n WHERE r.superseded_at IS NULL AND r.read_visible AND NOT r.read_quarantined
 AND r.read_kind='entity' AND btrim(coalesce(r.read_type,''))<>'' AND btrim(coalesce(n.name,''))<>'' ON CONFLICT DO NOTHING;
INSERT INTO entity_duplicate_keys
 SELECT r.project_id,r.read_type,coalesce((r.payload::jsonb#>'{metadata,identity_scope}')::text,'""'),
 'key:'||k.key,k.value::text,r.id FROM record_versions r CROSS JOIN LATERAL jsonb_each(
 CASE WHEN jsonb_typeof(r.payload::jsonb#>'{metadata,identity_keys}')='object' THEN r.payload::jsonb#>'{metadata,identity_keys}' ELSE '{}'::jsonb END) k
 WHERE r.superseded_at IS NULL AND r.read_visible AND NOT r.read_quarantined AND r.read_kind='entity' AND btrim(coalesce(r.read_type,''))<>''
 ON CONFLICT DO NOTHING;
DO $$
DECLARE app_role text := coalesce(nullif(current_setting('knowledge_service.app_role',true),''),'knowledge_app');
BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON attribute_conflict_sets,attribute_conflict_members,entity_duplicate_keys TO %I',app_role);
 END IF;
END $$;
