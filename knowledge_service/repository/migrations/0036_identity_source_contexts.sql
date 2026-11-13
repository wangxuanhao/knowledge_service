-- Rebuildable support projection. Assertions + accepted decision events + exact
-- version mappings remain authority. An accepted->accepted identity move closes
-- the old context; it never changes the original occurrence payload.
CREATE TABLE source_context_support (
 project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 id text NOT NULL,
 assertion_id text NOT NULL REFERENCES assertions(id) ON DELETE CASCADE,
 record_id text NOT NULL,
 record_version_id text NOT NULL,
 decision_version integer NOT NULL,
 kind text NOT NULL,
 document_id text,
 document_version_id text,
 known_from timestamptz NOT NULL,
 known_until timestamptz,
 valid_from text,
 valid_until text,
 valid_from_known boolean NOT NULL,
 valid_until_known boolean NOT NULL,
 valid_from_epoch_ms bigint NOT NULL,
 valid_until_epoch_ms bigint NOT NULL,
 platform_mode text NOT NULL,
 platform_values text[] NOT NULL,
 business_mode text NOT NULL,
 business_values text[] NOT NULL,
 audience_mode text NOT NULL,
 audience_values text[] NOT NULL,
 applicability jsonb NOT NULL,
 invalid_reason text,
 PRIMARY KEY(project_id,id),
 CHECK(known_until IS NULL OR known_until>=known_from)
);
CREATE INDEX source_context_current ON source_context_support(project_id,record_id)
 WHERE known_until IS NULL AND invalid_reason IS NULL;
CREATE INDEX source_context_history ON source_context_support(project_id,record_id,known_from,known_until);
CREATE INDEX source_context_assertion ON source_context_support(project_id,assertion_id);
CREATE INDEX source_context_document ON source_context_support(project_id,document_id,document_version_id);
CREATE INDEX source_context_platform ON source_context_support USING gin(platform_values);
CREATE INDEX source_context_business ON source_context_support USING gin(business_values);
CREATE INDEX source_context_audience ON source_context_support USING gin(audience_values);
CREATE INDEX identity_mapping_record ON record_version_assertions(project_id,record_id);
CREATE INDEX identity_accepted_record ON assertion_events(project_id,canonical_record_id)
 WHERE to_status='accepted';

CREATE FUNCTION identity_support_epoch(value text) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
 IF value IS NULL THEN RETURN 0; END IF;
 -- Python's public date-only contract is UTC, independent of the database
 -- session timezone. Full datetimes require an explicit ISO timezone.
 IF value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
  RETURN floor(extract(epoch FROM (value::date::timestamp AT TIME ZONE 'UTC'))*1000)::bigint;
 END IF;
 IF value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}(:[0-9]{2}([.][0-9]+)?)?(Z|[+-][0-9]{2}(:?[0-9]{2})?(:[0-9]{2})?)$' THEN
  RETURN NULL;
 END IF;
 RETURN floor(extract(epoch FROM value::timestamptz)*1000)::bigint;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END $$;

CREATE FUNCTION identity_support_normalize(value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT btrim(normalize(value,NFKC),
  chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||
  chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||
  chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||
  chr(8239)||chr(8287)||chr(12288))
$$;
CREATE FUNCTION identity_support_values(value jsonb, platform boolean DEFAULT false)
RETURNS text[] LANGUAGE sql IMMUTABLE AS $$
 SELECT coalesce(array_agg(DISTINCT CASE WHEN platform THEN CASE lower(item)
     WHEN 'meituan' THEN '美团' WHEN 'taobao' THEN '淘宝' WHEN 'jd' THEN '京东'
     ELSE item END ELSE item END ORDER BY CASE WHEN platform THEN CASE lower(item)
     WHEN 'meituan' THEN '美团' WHEN 'taobao' THEN '淘宝' WHEN 'jd' THEN '京东'
     ELSE item END ELSE item END), '{}'::text[])
 FROM (SELECT identity_support_normalize(raw#>>'{}') item FROM jsonb_array_elements(
   CASE WHEN jsonb_typeof(value->'values')='array' THEN value->'values' ELSE '[]'::jsonb END) raw
   WHERE jsonb_typeof(raw)='string') normalized WHERE item<>''
$$;

CREATE FUNCTION refresh_identity_support(selected_project text, selected_assertion text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 -- Serialize rebuilds and decision/mapping triggers on the assertion authority
 -- row. A concurrent backfill cannot resurrect an already withdrawn interval.
 PERFORM 1 FROM assertions WHERE project_id=selected_project AND id=selected_assertion FOR UPDATE;
 DELETE FROM source_context_support WHERE project_id=selected_project AND assertion_id=selected_assertion;
 INSERT INTO source_context_support
 SELECT a.project_id,e.id,a.id,e.canonical_record_id,m.record_version_id,e.decision_version,a.kind,
        a.document_id,a.document_version_id,greatest(e.created_at,m.created_at,r.recorded_at),
        CASE WHEN e.next_at IS NOT NULL THEN greatest(e.next_at,e.created_at,m.created_at,r.recorded_at) END,
        a.payload::jsonb->>'valid_from',a.payload::jsonb->>'valid_until',
        a.payload::jsonb->>'valid_from' IS NOT NULL,a.payload::jsonb->>'valid_until' IS NOT NULL,
        coalesce(identity_support_epoch(a.payload::jsonb->>'valid_from'),0),
        coalesce(identity_support_epoch(a.payload::jsonb->>'valid_until'),0),
        coalesce(app.value#>>'{platforms,mode}','unknown'),identity_support_values(app.value->'platforms',true),
        coalesce(app.value#>>'{businesses,mode}','unknown'),identity_support_values(app.value->'businesses'),
        coalesce(app.value#>>'{audiences,mode}','unknown'),identity_support_values(app.value->'audiences'),
        coalesce(app.value,'{}'::jsonb),
        CASE WHEN e.next_at IS NOT NULL AND greatest(e.created_at,m.created_at,r.recorded_at)>=e.next_at
             THEN 'mapping_not_known_during_accepted_interval'
             WHEN identity_support_epoch(a.payload::jsonb->>'valid_from') IS NULL
                  OR identity_support_epoch(a.payload::jsonb->>'valid_until') IS NULL
             THEN 'invalid_source_time'
             WHEN a.payload::jsonb->>'valid_until' IS NOT NULL
                  AND a.payload::jsonb->>'valid_from' IS NOT NULL
                  AND identity_support_epoch(a.payload::jsonb->>'valid_until')<=identity_support_epoch(a.payload::jsonb->>'valid_from')
             THEN 'invalid_source_interval'
             WHEN EXISTS (SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(app.value)='object'
                   THEN app.value ELSE '{}'::jsonb END) d WHERE d.key NOT IN ('platforms','businesses','audiences')
                   OR (d.value<>'null'::jsonb AND (jsonb_typeof(d.value)<>'object'
                     OR coalesce(d.value->>'mode','') NOT IN ('unknown','all','specific')
                     OR (d.value->>'mode'='specific' AND cardinality(identity_support_values(d.value))=0)
                     OR (d.value->>'mode' IN ('all','unknown') AND cardinality(identity_support_values(d.value))>0))))
                  OR (app.value IS NOT NULL AND jsonb_typeof(app.value)<>'object')
             THEN 'invalid_source_applicability' END
 FROM assertions a JOIN (
   SELECT *,lead(created_at) OVER(PARTITION BY assertion_id ORDER BY decision_version,seq) next_at
   FROM assertion_events WHERE project_id=selected_project AND assertion_id=selected_assertion
 ) e ON e.project_id=a.project_id AND e.assertion_id=a.id
 JOIN record_version_assertions m ON m.project_id=e.project_id AND m.assertion_event_id=e.id
   AND m.assertion_id=e.assertion_id AND m.record_id=e.canonical_record_id
 JOIN record_versions r ON r.project_id=m.project_id AND r.version_id=m.record_version_id
 CROSS JOIN LATERAL (SELECT nullif(a.payload::jsonb#>'{metadata,applicability}','null'::jsonb) value) app
 WHERE a.project_id=selected_project AND a.id=selected_assertion
   AND e.to_status='accepted' AND a.kind IN ('entity','relation','attribute')
 ORDER BY e.id,r.version,m.record_version_id
 ON CONFLICT(project_id,id) DO NOTHING;
END $$;

CREATE FUNCTION identity_support_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM refresh_identity_support(NEW.project_id,NEW.assertion_id);
 RETURN NEW;
END $$;
CREATE TRIGGER identity_support_event AFTER INSERT ON assertion_events
 FOR EACH ROW EXECUTE FUNCTION identity_support_changed();
CREATE TRIGGER identity_support_mapping AFTER INSERT ON record_version_assertions
 FOR EACH ROW EXECUTE FUNCTION identity_support_changed();

-- Existing data is rebuilt explicitly through backfill_source_contexts in
-- bounded, resumable batches. Installing schema must not trigger an unbounded
-- corpus backfill or silently alter a project's activation readiness.

-- Strong keys are scoped to project, stable class and declared identity scope;
-- names deliberately have no uniqueness constraint.
CREATE TABLE entity_identity_keys (
 project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 class_iri text NOT NULL,
 identity_scope text NOT NULL,
 key_name text NOT NULL,
 key_value text NOT NULL,
 record_id text NOT NULL,
 record_version_id text NOT NULL,
 PRIMARY KEY(project_id,class_iri,identity_scope,key_name,key_value)
);
CREATE INDEX entity_identity_record ON entity_identity_keys(project_id,record_id);
CREATE FUNCTION identity_key_record_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k text; p jsonb;
BEGIN
 DELETE FROM entity_identity_keys WHERE project_id=NEW.project_id AND record_id=NEW.id;
 p=NEW.payload::jsonb;
 IF NEW.superseded_at IS NULL AND p->>'kind'='entity'
    AND coalesce((p#>>'{metadata,_deleted}')::boolean,false)=false
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
CREATE TRIGGER identity_key_record AFTER INSERT OR UPDATE OF superseded_at ON record_versions
 FOR EACH ROW EXECUTE FUNCTION identity_key_record_changed();
DO $$
DECLARE app_role text := coalesce(nullif(current_setting('knowledge_service.app_role',true),''),'knowledge_app');
BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON source_context_support,entity_identity_keys TO %I',app_role);
 END IF;
END $$;
