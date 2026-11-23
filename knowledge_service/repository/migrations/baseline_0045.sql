-- PostgreSQL schema after migration 0045.
-- The original SQL in legacy/ remains the upgrade path for existing databases.
-- This baseline runs only against an empty migration ledger in one transaction.
SET LOCAL check_function_bodies = off;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE FUNCTION attribute_literal_fingerprint(body text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $$
 SELECT CASE WHEN body::jsonb->>'kind'='attribute' THEN
  encode(sha256(convert_to(coalesce((body::jsonb->'datatype')::text,'null') || ':' ||
   coalesce((body::jsonb->'value')::text,'null'),'UTF8')),'hex') END
$$;

CREATE FUNCTION check_ontology_decision_cycle() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.supersedes_decision_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE supersession(id) AS (
            VALUES (NEW.supersedes_decision_id)
            UNION
            SELECT decision.supersedes_decision_id
              FROM ontology_review_decisions AS decision
              JOIN supersession ON decision.id = supersession.id
             WHERE decision.project_id = NEW.project_id
               AND decision.draft_id = NEW.draft_id
               AND decision.supersedes_decision_id IS NOT NULL
        )
        SELECT 1 FROM supersession WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'ontology decision supersession cycle'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION check_ontology_operation_cycle() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.supersedes_operation_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE supersession(id) AS (
            VALUES (NEW.supersedes_operation_id)
            UNION
            SELECT operation.supersedes_operation_id
              FROM ontology_operations AS operation
              JOIN supersession ON operation.id = supersession.id
             WHERE operation.project_id = NEW.project_id
               AND operation.draft_id = NEW.draft_id
               AND operation.supersedes_operation_id IS NOT NULL
        )
        SELECT 1 FROM supersession WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'ontology operation supersession cycle'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NULL;   -- AFTER 触发器的返回值无意义
END;
$$;

CREATE FUNCTION entity_duplicate_record_changed() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

CREATE FUNCTION guard_ontology_decision_insert() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- [C]
    IF NOT EXISTS (
        SELECT 1 FROM ontology_operations AS operation
         WHERE operation.project_id = NEW.project_id
           AND operation.draft_id = NEW.draft_id
           AND operation.id = NEW.operation_id
           AND operation.fingerprint = NEW.operation_fingerprint
    ) THEN
        RAISE EXCEPTION 'ontology decision operation fingerprint mismatch'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- [D]
    IF EXISTS (
        SELECT 1 FROM ontology_review_decisions AS prior
         WHERE prior.project_id = NEW.project_id
           AND prior.draft_id = NEW.draft_id
           AND prior.id = NEW.supersedes_decision_id
           AND (prior.operation_id <> NEW.operation_id
                OR prior.operation_fingerprint <> NEW.operation_fingerprint)
    ) OR EXISTS (
        SELECT 1 FROM ontology_review_decisions AS replacement
         WHERE replacement.project_id = NEW.project_id
           AND replacement.draft_id = NEW.draft_id
           AND replacement.supersedes_decision_id = NEW.id
           AND (replacement.operation_id <> NEW.operation_id
                OR replacement.operation_fingerprint <> NEW.operation_fingerprint)
    ) THEN
        RAISE EXCEPTION 'superseded decisions must bind to the same operation'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- [E]
    IF NEW.supersedes_decision_id = NEW.id
       OR EXISTS (SELECT 1 FROM ontology_review_decisions WHERE id = NEW.id) THEN
        RAISE EXCEPTION 'ontology review decisions are immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- [F]
    IF NEW.supersedes_decision_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE supersession(id) AS (
            VALUES (NEW.supersedes_decision_id)
            UNION
            SELECT decision.supersedes_decision_id
              FROM ontology_review_decisions AS decision
              JOIN supersession ON decision.id = supersession.id
             WHERE decision.project_id = NEW.project_id
               AND decision.draft_id = NEW.draft_id
               AND decision.supersedes_decision_id IS NOT NULL
        )
        SELECT 1 FROM supersession WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'ontology review decision supersession cycle'
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE FUNCTION guard_ontology_history_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF ontology_history_delete_allowed() = 0 THEN
        RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$;

CREATE FUNCTION guard_ontology_operation_insert() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- [A]
    IF NEW.supersedes_operation_id = NEW.id
       OR EXISTS (SELECT 1 FROM ontology_operations WHERE id = NEW.id) THEN
        RAISE EXCEPTION 'ontology operations are immutable' USING ERRCODE = 'restrict_violation';
    END IF;

    -- [B] supersedes 链成环检测（自环已在 [A] 排除）
    IF NEW.supersedes_operation_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE supersession(id) AS (
            VALUES (NEW.supersedes_operation_id)
            UNION
            SELECT operation.supersedes_operation_id
              FROM ontology_operations AS operation
              JOIN supersession ON operation.id = supersession.id
             WHERE operation.project_id = NEW.project_id
               AND operation.draft_id = NEW.draft_id
               AND operation.supersedes_operation_id IS NOT NULL
        )
        SELECT 1 FROM supersession WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'ontology operation supersession cycle' USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE FUNCTION guard_ontology_repair_insert() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM ontology_history_repairs
                WHERE repair_key = NEW.repair_key) THEN
        RAISE EXCEPTION 'ontology history repair records are immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION identity_key_record_changed() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

CREATE FUNCTION identity_support_changed() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
 PERFORM refresh_identity_support(NEW.project_id,NEW.assertion_id);
 RETURN NEW;
END $$;

CREATE FUNCTION identity_support_epoch(value text) RETURNS bigint
    LANGUAGE plpgsql IMMUTABLE
    AS $_$
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
END $_$;

CREATE FUNCTION identity_support_normalize(value text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $$
 SELECT btrim(normalize(value,NFKC),
  chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||
  chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||
  chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||
  chr(8239)||chr(8287)||chr(12288))
$$;

CREATE FUNCTION identity_support_values(value jsonb, platform boolean DEFAULT false) RETURNS text[]
    LANGUAGE sql IMMUTABLE
    AS $$
 SELECT coalesce(array_agg(DISTINCT CASE WHEN platform THEN CASE lower(item)
     WHEN 'meituan' THEN '美团' WHEN 'taobao' THEN '淘宝' WHEN 'jd' THEN '京东'
     ELSE item END ELSE item END ORDER BY CASE WHEN platform THEN CASE lower(item)
     WHEN 'meituan' THEN '美团' WHEN 'taobao' THEN '淘宝' WHEN 'jd' THEN '京东'
     ELSE item END ELSE item END), '{}'::text[])
 FROM (SELECT identity_support_normalize(raw#>>'{}') item FROM jsonb_array_elements(
   CASE WHEN jsonb_typeof(value->'values')='array' THEN value->'values' ELSE '[]'::jsonb END) raw
   WHERE jsonb_typeof(raw)='string') normalized WHERE item<>''
$$;

CREATE FUNCTION ontology_history_delete_allowed() RETURNS integer
    LANGUAGE sql STABLE
    AS $$
    SELECT COALESCE(
        NULLIF(current_setting('knowledge.ontology_history_delete_allowed', true), ''),
        '0'
    )::integer;
$$;

CREATE FUNCTION qa_text_bigrams(value text) RETURNS text[]
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$
    SELECT COALESCE(array_agg(DISTINCT substr(value, n, 2)), ARRAY[]::text[])
    FROM generate_series(1, char_length(value) - 1) AS n
$$;

CREATE FUNCTION refresh_identity_support(selected_project text, selected_assertion text) RETURNS void
    LANGUAGE plpgsql
    AS $$
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

CREATE FUNCTION reject_ontology_history_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE FUNCTION write_snapshot_summary() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.snapshot_summary := (NEW.payload::jsonb - ARRAY['records', 'ontologies'])::text;
    RETURN NEW;
END;
$$;

CREATE TABLE artifacts (
    id text NOT NULL,
    kind text NOT NULL,
    project_id text,
    payload text NOT NULL,
    seq bigint NOT NULL,
    snapshot_summary text
);

ALTER TABLE artifacts ALTER COLUMN seq ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME artifacts_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE assertion_events (
    id text NOT NULL,
    assertion_id text NOT NULL,
    project_id text NOT NULL,
    from_status text,
    to_status text NOT NULL,
    decision_version integer NOT NULL,
    reason text NOT NULL,
    actor text NOT NULL,
    canonical_record_id text,
    created_at timestamp with time zone NOT NULL,
    seq bigint NOT NULL
);

ALTER TABLE assertion_events ALTER COLUMN seq ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME assertion_events_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE assertions (
    id text NOT NULL,
    project_id text NOT NULL,
    kind text NOT NULL,
    document_id text,
    document_version_id text,
    chunk_id text,
    source_hash text,
    start_char integer,
    end_char integer,
    quote text,
    payload text NOT NULL,
    status text NOT NULL,
    canonical_record_id text,
    decision_reason text,
    decision_version integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    decided_at timestamp with time zone,
    actor text NOT NULL
);

CREATE TABLE attribute_conflict_members (
    project_id text NOT NULL,
    set_id text NOT NULL,
    record_id text NOT NULL,
    version integer NOT NULL,
    version_id text NOT NULL,
    assertion_versions jsonb NOT NULL
);

CREATE TABLE attribute_conflict_sets (
    project_id text NOT NULL,
    id text NOT NULL,
    entity_id text NOT NULL,
    predicate text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    state text NOT NULL,
    operation_id text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    reason text,
    actor text,
    decisions jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT attribute_conflict_sets_state_check CHECK ((state = ANY (ARRAY['open'::text, 'resolved'::text, 'reversed'::text]))),
    CONSTRAINT attribute_conflict_sets_version_check CHECK ((version > 0))
);

CREATE TABLE chunk_source_spans (
    id text NOT NULL,
    project_id text NOT NULL,
    document_version_id text NOT NULL,
    chunk_version_id text NOT NULL,
    block_id text,
    ocr_block_id text,
    ordinal integer NOT NULL,
    chunk_start_char integer NOT NULL,
    chunk_end_char integer NOT NULL,
    block_start_char integer NOT NULL,
    block_end_char integer NOT NULL,
    payload text NOT NULL,
    asr_segment_id text,
    CONSTRAINT chunk_source_spans_payload_check CHECK ((jsonb_typeof((payload)::jsonb) = 'object'::text)),
    CONSTRAINT chunk_source_spans_source_check CHECK (((block_id IS NOT NULL) OR (ocr_block_id IS NOT NULL) OR (asr_segment_id IS NOT NULL)))
);

CREATE TABLE entity_duplicate_keys (
    project_id text NOT NULL,
    class_iri text NOT NULL,
    identity_scope text NOT NULL,
    match_kind text NOT NULL,
    match_key text NOT NULL,
    record_id text NOT NULL
);

CREATE TABLE entity_identity_keys (
    project_id text NOT NULL,
    class_iri text NOT NULL,
    identity_scope text NOT NULL,
    key_name text NOT NULL,
    key_value text NOT NULL,
    record_id text NOT NULL,
    record_version_id text NOT NULL
);

CREATE TABLE fact_keys (
    project_id text NOT NULL,
    fact_key text NOT NULL,
    canonical_record_id text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    retired_at timestamp with time zone
);

CREATE TABLE ingest_batch_items (
    id text NOT NULL,
    batch_id text NOT NULL,
    project_id text NOT NULL,
    document_id text NOT NULL,
    source_document_id text,
    source_version_id text,
    source_restart_of text,
    document_generation integer DEFAULT 0 NOT NULL,
    original_name text NOT NULL,
    content bytea,
    size_bytes bigint NOT NULL,
    file_sha256 text NOT NULL,
    idempotency_key text NOT NULL,
    options_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    stage text DEFAULT 'queued'::text NOT NULL,
    progress double precision DEFAULT 0 NOT NULL,
    attempt integer DEFAULT 0 NOT NULL,
    auto_retry_count integer DEFAULT 0 NOT NULL,
    next_retry_at timestamp with time zone,
    error_code text,
    error_message text,
    public_error text,
    failed_chunk_ordinal integer,
    result jsonb,
    retry_mode text DEFAULT 'resume_checkpoint'::text NOT NULL,
    lease_owner text,
    lease_token text,
    lease_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    completed_at timestamp with time zone,
    upload_object_id text,
    prepared_result jsonb,
    CONSTRAINT ingest_batch_items_options_json_check CHECK ((jsonb_typeof(options_json) = 'object'::text)),
    CONSTRAINT ingest_batch_items_size_bytes_check CHECK ((size_bytes >= 0)),
    CONSTRAINT ingest_batch_items_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'retry_wait'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text, 'interrupted'::text]))),
    CONSTRAINT ingest_item_upload_bytes_or_object CHECK (((content IS NOT NULL) OR (upload_object_id IS NOT NULL)))
);

CREATE TABLE ingest_batches (
    id text NOT NULL,
    project_id text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    settings_json jsonb NOT NULL,
    settings_fingerprint text NOT NULL,
    requested_concurrency text DEFAULT 'auto'::text NOT NULL,
    effective_concurrency integer DEFAULT 1 NOT NULL,
    created_by text,
    paused_reason text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    CONSTRAINT ingest_batches_effective_concurrency_check CHECK ((effective_concurrency > 0)),
    CONSTRAINT ingest_batches_settings_json_check CHECK ((jsonb_typeof(settings_json) = 'object'::text)),
    CONSTRAINT ingest_batches_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'running'::text, 'paused'::text, 'cancelled'::text, 'completed'::text])))
);

CREATE TABLE ingest_chunk_checkpoints (
    id text NOT NULL,
    document_id text NOT NULL,
    chunk_ordinal integer NOT NULL,
    configuration_fingerprint text NOT NULL,
    batch_item_id text,
    attempt integer DEFAULT 0 NOT NULL,
    document_generation integer DEFAULT 0 NOT NULL,
    chunk_id text,
    chunk_sha256 text,
    status text DEFAULT 'succeeded'::text NOT NULL,
    output_json jsonb NOT NULL,
    diagnostics_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    completed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT ingest_chunk_checkpoints_chunk_ordinal_check CHECK ((chunk_ordinal >= 0)),
    CONSTRAINT ingest_chunk_checkpoints_status_check CHECK ((status = 'succeeded'::text))
);

CREATE TABLE ingest_item_attempts (
    id text NOT NULL,
    item_id text NOT NULL,
    attempt integer NOT NULL,
    lease_owner text NOT NULL,
    lease_token text NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    error_code text,
    error_message text,
    result jsonb,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    completed_at timestamp with time zone
);

CREATE TABLE ingest_runs (
    id text NOT NULL,
    project_id text NOT NULL,
    document_id text NOT NULL,
    document_version_id text NOT NULL,
    attempt integer NOT NULL,
    retry_of text,
    status text NOT NULL,
    active_stage text,
    readiness text NOT NULL,
    counts text NOT NULL,
    failure text,
    version integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE TABLE ingest_stage_outputs (
    project_id text NOT NULL,
    document_version_id text NOT NULL,
    chunk_id text NOT NULL,
    stage text NOT NULL,
    input_hash text NOT NULL,
    payload text NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE llm_model_settings (
    singleton boolean DEFAULT true NOT NULL,
    active_id text,
    key_check text,
    CONSTRAINT llm_model_settings_singleton_check CHECK (singleton)
);

CREATE TABLE llm_models (
    id text NOT NULL,
    name text NOT NULL,
    base_url text NOT NULL,
    model text NOT NULL,
    api_key_encrypted text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_models_model_check CHECK ((length(model) > 0)),
    CONSTRAINT llm_models_name_check CHECK ((length(name) > 0))
);

CREATE TABLE llm_task_snapshots (
    task_id text NOT NULL,
    sealed_config text NOT NULL,
    identity text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE merge_operations (
    id text NOT NULL,
    project_id text NOT NULL,
    operation text NOT NULL,
    status text NOT NULL,
    redirects text NOT NULL,
    assertion_moves text NOT NULL,
    before_state text NOT NULL,
    after_state text NOT NULL,
    expected_versions text NOT NULL,
    reversal_of text,
    created_at timestamp with time zone NOT NULL,
    seq bigint NOT NULL
);

ALTER TABLE merge_operations ALTER COLUMN seq ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME merge_operations_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE milvus_generation_cleanup_outbox (
    project_id text NOT NULL,
    index_version bigint NOT NULL,
    retired_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    not_before timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    claim_owner text,
    claim_token text,
    claim_until timestamp with time zone,
    cleaned_at timestamp with time zone,
    last_error text,
    CONSTRAINT milvus_generation_cleanup_outbox_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT milvus_generation_cleanup_outbox_index_version_check CHECK ((index_version > 0))
);

CREATE TABLE milvus_projection_receipts (
    project_id text NOT NULL,
    index_version bigint NOT NULL,
    record_id text NOT NULL,
    version_id text NOT NULL,
    fingerprint text NOT NULL,
    CONSTRAINT milvus_projection_receipts_fingerprint_check CHECK ((fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT milvus_projection_receipts_index_version_check CHECK ((index_version > 0)),
    CONSTRAINT milvus_projection_receipts_record_id_check CHECK ((length(record_id) > 0)),
    CONSTRAINT milvus_projection_receipts_version_id_check CHECK ((length(version_id) > 0))
);

CREATE TABLE milvus_vector_receipts (
    project_id text NOT NULL,
    index_version bigint NOT NULL,
    vector_id text NOT NULL,
    record_id text NOT NULL,
    version_id text NOT NULL,
    fingerprint text NOT NULL,
    CONSTRAINT milvus_vector_receipts_fingerprint_check CHECK ((fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT milvus_vector_receipts_index_version_check CHECK ((index_version > 0)),
    CONSTRAINT milvus_vector_receipts_record_id_check CHECK ((length(record_id) > 0)),
    CONSTRAINT milvus_vector_receipts_vector_id_check CHECK ((length(vector_id) > 0)),
    CONSTRAINT milvus_vector_receipts_version_id_check CHECK ((length(version_id) > 0))
);

CREATE TABLE ontologies (
    id text NOT NULL,
    project_id text NOT NULL,
    turtle text NOT NULL,
    summary text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    metadata text DEFAULT '{}'::text NOT NULL,
    seq bigint NOT NULL
);

ALTER TABLE ontologies ALTER COLUMN seq ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME ontologies_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE ontology_drafts (
    id text NOT NULL,
    project_id text NOT NULL,
    base_ontology_id text,
    source_kind text NOT NULL,
    status text NOT NULL,
    revision integer NOT NULL,
    title text NOT NULL,
    summary text NOT NULL,
    source_context text NOT NULL,
    validation_report text,
    validation_fingerprint text,
    published_ontology_id text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    seq bigint NOT NULL,
    validated_at timestamp with time zone,
    submitted_at timestamp with time zone,
    CONSTRAINT ontology_drafts_revision_check CHECK ((revision >= 1)),
    CONSTRAINT ontology_drafts_source_context_check CHECK ((source_context IS JSON)),
    CONSTRAINT ontology_drafts_source_kind_check CHECK ((source_kind = ANY (ARRAY['manual'::text, 'turtle'::text, 'import'::text, 'ai'::text, 'discovery'::text, 'candidate'::text, 'revert'::text]))),
    CONSTRAINT ontology_drafts_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'accepted'::text, 'rejected'::text]))),
    CONSTRAINT ontology_drafts_validation_report_check CHECK (((validation_report IS NULL) OR (validation_report IS JSON)))
);

ALTER TABLE ontology_drafts ALTER COLUMN seq ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME ontology_drafts_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE ontology_history_repairs (
    repair_key text NOT NULL,
    schema_version integer NOT NULL,
    reason text NOT NULL,
    operations_repaired integer NOT NULL,
    decisions_repaired integer NOT NULL,
    repaired_at timestamp with time zone NOT NULL,
    CONSTRAINT ontology_history_repairs_decisions_repaired_check CHECK ((decisions_repaired >= 0)),
    CONSTRAINT ontology_history_repairs_operations_repaired_check CHECK ((operations_repaired >= 0))
);

CREATE TABLE ontology_operations (
    id text NOT NULL,
    project_id text NOT NULL,
    draft_id text NOT NULL,
    action text NOT NULL,
    target_iri text NOT NULL,
    before_json text NOT NULL,
    after_json text NOT NULL,
    evidence_json text NOT NULL,
    impact_json text NOT NULL,
    validation_json text NOT NULL,
    risk text NOT NULL,
    fingerprint text NOT NULL,
    reason text,
    supersedes_operation_id text,
    created_at timestamp with time zone NOT NULL,
    seq bigint NOT NULL,
    CONSTRAINT ontology_operations_after_json_check CHECK ((after_json IS JSON)),
    CONSTRAINT ontology_operations_before_json_check CHECK ((before_json IS JSON)),
    CONSTRAINT ontology_operations_evidence_json_check CHECK ((evidence_json IS JSON)),
    CONSTRAINT ontology_operations_impact_json_check CHECK ((impact_json IS JSON)),
    CONSTRAINT ontology_operations_validation_json_check CHECK ((validation_json IS JSON))
);

ALTER TABLE ontology_operations ALTER COLUMN seq ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME ontology_operations_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE ontology_publish_requests (
    id text NOT NULL,
    project_id text NOT NULL,
    draft_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    result_ontology_id text,
    created_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone
);

CREATE TABLE ontology_review_decisions (
    id text NOT NULL,
    project_id text NOT NULL,
    draft_id text NOT NULL,
    operation_id text NOT NULL,
    operation_fingerprint text NOT NULL,
    action text NOT NULL,
    reason text,
    actor text NOT NULL,
    supersedes_decision_id text,
    created_at timestamp with time zone NOT NULL,
    seq bigint NOT NULL,
    CONSTRAINT ontology_review_decisions_action_check CHECK ((action = ANY (ARRAY['approve'::text, 'reject'::text, 'request_changes'::text])))
);

ALTER TABLE ontology_review_decisions ALTER COLUMN seq ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME ontology_review_decisions_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE parsed_blocks (
    block_id text NOT NULL,
    project_id text NOT NULL,
    document_version_id text NOT NULL,
    type text NOT NULL,
    ordinal integer NOT NULL,
    normalized_text text NOT NULL,
    source_start_char integer NOT NULL,
    source_end_char integer NOT NULL,
    source_start_line integer NOT NULL,
    source_end_line integer NOT NULL,
    heading_path text NOT NULL,
    payload text NOT NULL,
    CONSTRAINT parsed_blocks_heading_path_check CHECK ((jsonb_typeof((heading_path)::jsonb) = 'array'::text)),
    CONSTRAINT parsed_blocks_payload_check CHECK ((jsonb_typeof((payload)::jsonb) = 'object'::text))
);

CREATE TABLE preparation_files (
    id text NOT NULL,
    session_id text NOT NULL,
    content bytea NOT NULL,
    rechunked_result jsonb
);

CREATE TABLE preparation_operations (
    project_id text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    session_id text NOT NULL,
    request_hash text NOT NULL
);

CREATE TABLE preparation_sessions (
    id text NOT NULL,
    project_id text NOT NULL,
    owner_id text NOT NULL,
    payload jsonb NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '7 days'::interval) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE preparation_tasks (
    id text NOT NULL,
    project_id text NOT NULL,
    fingerprint text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    stage text DEFAULT 'queued'::text NOT NULL,
    file_id text,
    original_name text NOT NULL,
    options jsonb NOT NULL,
    result jsonb,
    lease_token text,
    lease_until timestamp with time zone,
    heartbeat_at timestamp with time zone,
    started_at timestamp with time zone,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE project_projection_sequences (
    project_id text NOT NULL,
    projection_seq bigint DEFAULT 0 NOT NULL,
    CONSTRAINT project_projection_sequences_projection_seq_check CHECK ((projection_seq >= 0))
);

CREATE TABLE projection_cleanup_outbox (
    project_id text NOT NULL,
    target text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    claim_owner text,
    claim_token text,
    claim_until timestamp with time zone,
    last_error text,
    CONSTRAINT projection_cleanup_outbox_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT projection_cleanup_outbox_target_check CHECK ((target = ANY (ARRAY['milvus'::text, 'neo4j'::text])))
);

CREATE TABLE projection_cursors (
    project_id text NOT NULL,
    target text NOT NULL,
    schema_version integer NOT NULL,
    applied_seq bigint DEFAULT 0 NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    last_error text,
    claim_owner text,
    claim_token text,
    claim_until timestamp with time zone,
    claimed_from_seq bigint,
    claimed_to_seq bigint,
    active_destination text,
    previous_destination text,
    rollback_until timestamp with time zone,
    rollback_applied_seq bigint DEFAULT 0 NOT NULL,
    rebuild_state jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT projection_cursors_applied_seq_check CHECK ((applied_seq >= 0)),
    CONSTRAINT projection_cursors_schema_version_check CHECK ((schema_version > 0)),
    CONSTRAINT projection_cursors_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'applying'::text, 'ready'::text, 'failed'::text, 'rebuilding'::text, 'validating'::text, 'switching'::text, 'requires_rebuild'::text])))
);

CREATE TABLE projection_outbox (
    project_id text NOT NULL,
    seq bigint NOT NULL,
    operation text NOT NULL,
    version_id text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT projection_outbox_operation_check CHECK ((operation = ANY (ARRAY['upsert'::text, 'tombstone'::text]))),
    CONSTRAINT projection_outbox_seq_check CHECK ((seq > 0))
);

CREATE TABLE projection_rebuild_snapshots (
    project_id text NOT NULL,
    target text NOT NULL,
    build_id text NOT NULL,
    ordinal integer NOT NULL,
    version_id text NOT NULL,
    projection_input text
);

CREATE TABLE projects (
    id text NOT NULL,
    name text NOT NULL,
    metadata text NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE provenance_activities (
    id text NOT NULL,
    project_id text NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    payload text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT provenance_activities_kind_check CHECK ((kind = ANY (ARRAY['retrieval'::text, 'answer'::text, 'ontology_draft'::text, 'ontology_publish'::text]))),
    CONSTRAINT provenance_activities_payload_check CHECK ((payload IS JSON)),
    CONSTRAINT provenance_activities_status_check CHECK ((status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text, 'cancelled'::text])))
);

CREATE TABLE provenance_edges (
    id text NOT NULL,
    project_id text NOT NULL,
    activity_id text NOT NULL,
    source_ref text NOT NULL,
    relation text NOT NULL,
    target_ref text NOT NULL,
    ordinal integer NOT NULL,
    payload text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT provenance_edges_ordinal_check CHECK ((ordinal >= 0)),
    CONSTRAINT provenance_edges_payload_check CHECK ((payload IS JSON)),
    CONSTRAINT provenance_edges_relation_check CHECK ((relation = ANY (ARRAY['considered'::text, 'used'::text, 'offered'::text, 'cites'::text, 'supported-by'::text, 'decided-by'::text, 'extracted-from'::text, 'sourced-from'::text, 'processed-by'::text, 'published-from'::text, 'contains-operation'::text, 'proposed-by'::text, 'based-on'::text])))
);

CREATE TABLE qa_conversations (
    id text NOT NULL,
    owner_id text NOT NULL,
    project_id text NOT NULL,
    title text NOT NULL,
    title_auto boolean DEFAULT true NOT NULL,
    messages text DEFAULT '[]'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT qa_conversations_messages_check CHECK ((json_typeof((messages)::json) = 'array'::text)),
    CONSTRAINT qa_conversations_title_check CHECK (((char_length(title) >= 1) AND (char_length(title) <= 80))),
    CONSTRAINT qa_conversations_version_check CHECK ((version > 0))
);

CREATE TABLE record_fts (
    project_id text NOT NULL,
    record_id text NOT NULL,
    kind text NOT NULL,
    text text NOT NULL,
    metadata text DEFAULT '{}'::text NOT NULL
);

CREATE TABLE record_operation_reservations (
    token text NOT NULL,
    project_id text NOT NULL,
    recorded_at timestamp with time zone NOT NULL
);

CREATE TABLE record_version_assertions (
    project_id text NOT NULL,
    record_id text NOT NULL,
    record_version_id text NOT NULL,
    assertion_id text NOT NULL,
    assertion_event_id text NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE record_versions (
    project_id text NOT NULL,
    id text NOT NULL,
    version integer NOT NULL,
    version_id text NOT NULL,
    payload text NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    superseded_at timestamp with time zone,
    platform_mode text NOT NULL,
    platform_values text[] NOT NULL,
    business_mode text NOT NULL,
    business_values text[] NOT NULL,
    audience_mode text NOT NULL,
    audience_values text[] NOT NULL,
    scope_identity text NOT NULL,
    valid_from_known boolean NOT NULL,
    valid_from_epoch_ms bigint NOT NULL,
    valid_until_known boolean NOT NULL,
    valid_until_epoch_ms bigint NOT NULL,
    read_kind text GENERATED ALWAYS AS (((payload)::jsonb ->> 'kind'::text)) STORED,
    read_type text GENERATED ALWAYS AS (((payload)::jsonb ->> 'type'::text)) STORED,
    read_text text GENERATED ALWAYS AS ("left"(COALESCE(((payload)::jsonb ->> 'text'::text), ''::text), 160)) STORED,
    read_subject_id text GENERATED ALWAYS AS (((payload)::jsonb ->> 'subject_id'::text)) STORED,
    read_object_id text GENERATED ALWAYS AS (((payload)::jsonb ->> 'object_id'::text)) STORED,
    read_visible boolean GENERATED ALWAYS AS (((COALESCE(((payload)::jsonb #> '{metadata,_deleted}'::text[]), 'null'::jsonb) = ANY (ARRAY['null'::jsonb, 'false'::jsonb, '0'::jsonb, '""'::jsonb, '[]'::jsonb, '{}'::jsonb])) AND (COALESCE(((payload)::jsonb #> '{metadata,_audit}'::text[]), 'null'::jsonb) = ANY (ARRAY['null'::jsonb, 'false'::jsonb, '0'::jsonb, '""'::jsonb, '[]'::jsonb, '{}'::jsonb])))) STORED,
    read_quarantined boolean GENERATED ALWAYS AS ((COALESCE(((payload)::jsonb #> '{metadata,_quarantined}'::text[]), 'false'::jsonb) = 'true'::jsonb)) STORED,
    read_attribute_fingerprint text GENERATED ALWAYS AS (attribute_literal_fingerprint(payload)) STORED
);

CREATE TABLE resolution_reviews (
    id text NOT NULL,
    project_id text NOT NULL,
    source_entity_id text NOT NULL,
    candidate_entity_id text NOT NULL,
    score double precision NOT NULL,
    status text NOT NULL,
    decision_version integer NOT NULL,
    payload text NOT NULL,
    reason text,
    actor text,
    created_at timestamp with time zone NOT NULL,
    decided_at timestamp with time zone
);

CREATE TABLE service_settings (
    key text NOT NULL,
    value text NOT NULL
);

CREATE TABLE source_asset_asr_runs (
    id text NOT NULL,
    project_id text NOT NULL,
    document_version_id text NOT NULL,
    asset_id text NOT NULL,
    asset_sha256 text NOT NULL,
    status text NOT NULL,
    attempt integer NOT NULL,
    engine text NOT NULL,
    engine_version text NOT NULL,
    configuration_fingerprint text NOT NULL,
    duration_ms integer,
    text_sha256 text,
    normalized_text text NOT NULL,
    error_code text,
    public_error text,
    started_at text NOT NULL,
    completed_at text NOT NULL,
    CONSTRAINT source_asset_asr_runs_attempt_check CHECK ((attempt > 0)),
    CONSTRAINT source_asset_asr_runs_duration_ms_check CHECK ((duration_ms > 0)),
    CONSTRAINT source_asset_asr_runs_status_check CHECK ((status = ANY (ARRAY['completed'::text, 'failed'::text])))
);

CREATE TABLE source_asset_asr_segments (
    id text NOT NULL,
    project_id text NOT NULL,
    document_version_id text NOT NULL,
    asr_run_id text NOT NULL,
    ordinal integer NOT NULL,
    start_ms integer NOT NULL,
    end_ms integer NOT NULL,
    text text NOT NULL,
    normalized_text text NOT NULL,
    CONSTRAINT source_asset_asr_segments_check CHECK ((end_ms > start_ms)),
    CONSTRAINT source_asset_asr_segments_ordinal_check CHECK ((ordinal >= 0)),
    CONSTRAINT source_asset_asr_segments_start_ms_check CHECK ((start_ms >= 0))
);

CREATE TABLE source_asset_ocr_blocks (
    id text NOT NULL,
    project_id text NOT NULL,
    document_version_id text NOT NULL,
    ocr_run_id text NOT NULL,
    ordinal integer NOT NULL,
    text text NOT NULL,
    normalized_text text NOT NULL,
    confidence double precision NOT NULL,
    x double precision NOT NULL,
    y double precision NOT NULL,
    width double precision NOT NULL,
    height double precision NOT NULL,
    page_or_frame integer DEFAULT 1 NOT NULL,
    CONSTRAINT source_asset_ocr_blocks_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision))),
    CONSTRAINT source_asset_ocr_blocks_height_check CHECK (((height >= (0)::double precision) AND (height <= (1)::double precision))),
    CONSTRAINT source_asset_ocr_blocks_width_check CHECK (((width >= (0)::double precision) AND (width <= (1)::double precision))),
    CONSTRAINT source_asset_ocr_blocks_x_check CHECK (((x >= (0)::double precision) AND (x <= (1)::double precision))),
    CONSTRAINT source_asset_ocr_blocks_y_check CHECK (((y >= (0)::double precision) AND (y <= (1)::double precision)))
);

CREATE TABLE source_asset_ocr_runs (
    id text NOT NULL,
    project_id text NOT NULL,
    document_version_id text NOT NULL,
    asset_id text NOT NULL,
    asset_sha256 text NOT NULL,
    status text NOT NULL,
    attempt integer NOT NULL,
    engine text NOT NULL,
    engine_version text NOT NULL,
    language_config text NOT NULL,
    preprocess_fingerprint text NOT NULL,
    configuration_fingerprint text NOT NULL,
    average_confidence double precision,
    text_sha256 text,
    normalized_text text NOT NULL,
    error_code text,
    public_error text,
    started_at text NOT NULL,
    completed_at text,
    processing_details text DEFAULT '{}'::text NOT NULL,
    CONSTRAINT source_asset_ocr_runs_processing_details_check CHECK ((jsonb_typeof((processing_details)::jsonb) = 'object'::text))
);

CREATE TABLE source_assets (
    id text NOT NULL,
    project_id text NOT NULL,
    document_version_id text NOT NULL,
    source_object_id text,
    original_path text NOT NULL,
    resolved_path text NOT NULL,
    alt_text text NOT NULL,
    width integer,
    height integer,
    status text NOT NULL,
    kind text NOT NULL,
    source_start_line integer,
    source_end_line integer,
    ignored_by text,
    ignored_at text,
    ignore_reason text,
    provenance text DEFAULT '{}'::text NOT NULL,
    CONSTRAINT source_assets_provenance_check CHECK ((jsonb_typeof((provenance)::jsonb) = 'object'::text))
);

CREATE TABLE source_context_support (
    project_id text NOT NULL,
    id text NOT NULL,
    assertion_id text NOT NULL,
    record_id text NOT NULL,
    record_version_id text NOT NULL,
    decision_version integer NOT NULL,
    kind text NOT NULL,
    document_id text,
    document_version_id text,
    known_from timestamp with time zone NOT NULL,
    known_until timestamp with time zone,
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
    CONSTRAINT source_context_support_check CHECK (((known_until IS NULL) OR (known_until >= known_from)))
);

CREATE TABLE source_document_versions (
    id text NOT NULL,
    project_id text NOT NULL,
    document_id text NOT NULL,
    version integer NOT NULL,
    source_object_id text NOT NULL,
    original_name text NOT NULL,
    format text NOT NULL,
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    parse_status text NOT NULL,
    source_text text NOT NULL,
    rendered_html text NOT NULL,
    created_at text NOT NULL,
    parser_metadata text DEFAULT '{}'::text NOT NULL,
    CONSTRAINT source_document_versions_parser_metadata_check CHECK ((jsonb_typeof((parser_metadata)::jsonb) = 'object'::text)),
    CONSTRAINT source_document_versions_version_check CHECK ((version > 0))
);

CREATE TABLE source_objects (
    id text NOT NULL,
    sha256 text NOT NULL,
    size_bytes bigint NOT NULL,
    mime_type text NOT NULL,
    storage_key text NOT NULL,
    created_at text NOT NULL,
    CONSTRAINT source_objects_sha256_check CHECK ((length(sha256) = 64)),
    CONSTRAINT source_objects_size_bytes_check CHECK ((size_bytes >= 0))
);

CREATE TABLE users (
    id text NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    display_name text DEFAULT ''::text NOT NULL,
    role text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['superadmin'::text, 'admin'::text, 'viewer'::text])))
);

ALTER TABLE ONLY artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY assertion_events
    ADD CONSTRAINT assertion_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY assertions
    ADD CONSTRAINT assertions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY attribute_conflict_members
    ADD CONSTRAINT attribute_conflict_members_pkey PRIMARY KEY (project_id, set_id, record_id);

ALTER TABLE ONLY attribute_conflict_sets
    ADD CONSTRAINT attribute_conflict_sets_pkey PRIMARY KEY (project_id, id);

ALTER TABLE ONLY chunk_source_spans
    ADD CONSTRAINT chunk_source_spans_pkey PRIMARY KEY (id);

ALTER TABLE ONLY chunk_source_spans
    ADD CONSTRAINT chunk_source_spans_project_id_document_version_id_chunk_ver_key UNIQUE (project_id, document_version_id, chunk_version_id, ordinal);

ALTER TABLE ONLY entity_duplicate_keys
    ADD CONSTRAINT entity_duplicate_keys_pkey PRIMARY KEY (project_id, class_iri, identity_scope, match_kind, match_key, record_id);

ALTER TABLE ONLY entity_identity_keys
    ADD CONSTRAINT entity_identity_keys_pkey PRIMARY KEY (project_id, class_iri, identity_scope, key_name, key_value);

ALTER TABLE ONLY fact_keys
    ADD CONSTRAINT fact_keys_pkey PRIMARY KEY (project_id, fact_key, created_at);

ALTER TABLE ONLY ingest_batch_items
    ADD CONSTRAINT ingest_batch_items_batch_id_idempotency_key_key UNIQUE (batch_id, idempotency_key);

ALTER TABLE ONLY ingest_batch_items
    ADD CONSTRAINT ingest_batch_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ingest_batches
    ADD CONSTRAINT ingest_batches_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ingest_batches
    ADD CONSTRAINT ingest_batches_project_id_id_key UNIQUE (project_id, id);

ALTER TABLE ONLY ingest_chunk_checkpoints
    ADD CONSTRAINT ingest_chunk_checkpoints_document_id_chunk_ordinal_configur_key UNIQUE (document_id, chunk_ordinal, configuration_fingerprint);

ALTER TABLE ONLY ingest_chunk_checkpoints
    ADD CONSTRAINT ingest_chunk_checkpoints_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ingest_item_attempts
    ADD CONSTRAINT ingest_item_attempts_item_id_attempt_key UNIQUE (item_id, attempt);

ALTER TABLE ONLY ingest_item_attempts
    ADD CONSTRAINT ingest_item_attempts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ingest_runs
    ADD CONSTRAINT ingest_runs_document_version_id_attempt_key UNIQUE (document_version_id, attempt);

ALTER TABLE ONLY ingest_runs
    ADD CONSTRAINT ingest_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ingest_stage_outputs
    ADD CONSTRAINT ingest_stage_outputs_pkey PRIMARY KEY (document_version_id, chunk_id, stage, input_hash);

ALTER TABLE ONLY llm_model_settings
    ADD CONSTRAINT llm_model_settings_pkey PRIMARY KEY (singleton);

ALTER TABLE ONLY llm_models
    ADD CONSTRAINT llm_models_pkey PRIMARY KEY (id);

ALTER TABLE ONLY llm_task_snapshots
    ADD CONSTRAINT llm_task_snapshots_pkey PRIMARY KEY (task_id);

ALTER TABLE ONLY merge_operations
    ADD CONSTRAINT merge_operations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY milvus_generation_cleanup_outbox
    ADD CONSTRAINT milvus_generation_cleanup_outbox_pkey PRIMARY KEY (project_id, index_version);

ALTER TABLE ONLY milvus_projection_receipts
    ADD CONSTRAINT milvus_projection_receipts_pkey PRIMARY KEY (project_id, index_version, record_id);

ALTER TABLE ONLY milvus_vector_receipts
    ADD CONSTRAINT milvus_vector_receipts_pkey PRIMARY KEY (project_id, index_version, vector_id);

ALTER TABLE ONLY ontologies
    ADD CONSTRAINT ontologies_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ontology_drafts
    ADD CONSTRAINT ontology_drafts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ontology_drafts
    ADD CONSTRAINT ontology_drafts_project_id_id_key UNIQUE (project_id, id);

ALTER TABLE ONLY ontology_history_repairs
    ADD CONSTRAINT ontology_history_repairs_pkey PRIMARY KEY (repair_key);

ALTER TABLE ONLY ontology_operations
    ADD CONSTRAINT ontology_operations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ontology_operations
    ADD CONSTRAINT ontology_operations_project_id_draft_id_id_key UNIQUE (project_id, draft_id, id);

ALTER TABLE ONLY ontology_operations
    ADD CONSTRAINT ontology_operations_project_id_id_key UNIQUE (project_id, id);

ALTER TABLE ONLY ontology_publish_requests
    ADD CONSTRAINT ontology_publish_requests_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ontology_publish_requests
    ADD CONSTRAINT ontology_publish_requests_project_id_draft_id_idempotency_k_key UNIQUE (project_id, draft_id, idempotency_key);

ALTER TABLE ONLY ontology_review_decisions
    ADD CONSTRAINT ontology_review_decisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ontology_review_decisions
    ADD CONSTRAINT ontology_review_decisions_project_id_draft_id_id_key UNIQUE (project_id, draft_id, id);

ALTER TABLE ONLY ontology_review_decisions
    ADD CONSTRAINT ontology_review_decisions_project_id_id_key UNIQUE (project_id, id);

ALTER TABLE ONLY parsed_blocks
    ADD CONSTRAINT parsed_blocks_pkey PRIMARY KEY (block_id);

ALTER TABLE ONLY parsed_blocks
    ADD CONSTRAINT parsed_blocks_project_id_document_version_id_block_id_key UNIQUE (project_id, document_version_id, block_id);

ALTER TABLE ONLY preparation_files
    ADD CONSTRAINT preparation_files_pkey PRIMARY KEY (id);

ALTER TABLE ONLY preparation_operations
    ADD CONSTRAINT preparation_operations_pkey PRIMARY KEY (project_id, operation, idempotency_key);

ALTER TABLE ONLY preparation_sessions
    ADD CONSTRAINT preparation_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY preparation_tasks
    ADD CONSTRAINT preparation_tasks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY preparation_tasks
    ADD CONSTRAINT preparation_tasks_project_id_fingerprint_key UNIQUE (project_id, fingerprint);

ALTER TABLE ONLY project_projection_sequences
    ADD CONSTRAINT project_projection_sequences_pkey PRIMARY KEY (project_id);

ALTER TABLE ONLY projection_cleanup_outbox
    ADD CONSTRAINT projection_cleanup_outbox_pkey PRIMARY KEY (project_id, target);

ALTER TABLE ONLY projection_cursors
    ADD CONSTRAINT projection_cursors_pkey PRIMARY KEY (project_id, target);

ALTER TABLE ONLY projection_outbox
    ADD CONSTRAINT projection_outbox_pkey PRIMARY KEY (project_id, seq);

ALTER TABLE ONLY projection_rebuild_snapshots
    ADD CONSTRAINT projection_rebuild_snapshots_pkey PRIMARY KEY (project_id, target, build_id, ordinal);

ALTER TABLE ONLY projection_rebuild_snapshots
    ADD CONSTRAINT projection_rebuild_snapshots_project_id_target_build_id_ver_key UNIQUE (project_id, target, build_id, version_id);

ALTER TABLE ONLY projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY provenance_activities
    ADD CONSTRAINT provenance_activities_project_id_id_key UNIQUE (project_id, id);

ALTER TABLE ONLY provenance_edges
    ADD CONSTRAINT provenance_edges_pkey PRIMARY KEY (id);

ALTER TABLE ONLY provenance_edges
    ADD CONSTRAINT provenance_edges_project_id_activity_id_source_ref_relation_key UNIQUE (project_id, activity_id, source_ref, relation, target_ref, ordinal);

ALTER TABLE ONLY qa_conversations
    ADD CONSTRAINT qa_conversations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY record_fts
    ADD CONSTRAINT record_fts_pkey PRIMARY KEY (project_id, record_id);

ALTER TABLE ONLY record_operation_reservations
    ADD CONSTRAINT record_operation_reservations_pkey PRIMARY KEY (token);

ALTER TABLE ONLY record_operation_reservations
    ADD CONSTRAINT record_operation_reservations_project_id_recorded_at_key UNIQUE (project_id, recorded_at);

ALTER TABLE ONLY record_version_assertions
    ADD CONSTRAINT record_version_assertions_project_id_record_version_id_asse_key UNIQUE (project_id, record_version_id, assertion_id, assertion_event_id);

ALTER TABLE ONLY record_versions
    ADD CONSTRAINT record_versions_pkey PRIMARY KEY (project_id, id, version);

ALTER TABLE ONLY record_versions
    ADD CONSTRAINT record_versions_version_id_key UNIQUE (version_id);

ALTER TABLE ONLY resolution_reviews
    ADD CONSTRAINT resolution_reviews_pkey PRIMARY KEY (id);

ALTER TABLE ONLY service_settings
    ADD CONSTRAINT service_settings_pkey PRIMARY KEY (key);

ALTER TABLE ONLY source_asset_asr_runs
    ADD CONSTRAINT source_asset_asr_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source_asset_asr_runs
    ADD CONSTRAINT source_asset_asr_runs_project_id_document_version_id_id_key UNIQUE (project_id, document_version_id, id);

ALTER TABLE ONLY source_asset_asr_segments
    ADD CONSTRAINT source_asset_asr_segments_asr_run_id_ordinal_key UNIQUE (asr_run_id, ordinal);

ALTER TABLE ONLY source_asset_asr_segments
    ADD CONSTRAINT source_asset_asr_segments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source_asset_asr_segments
    ADD CONSTRAINT source_asset_asr_segments_project_id_document_version_id_id_key UNIQUE (project_id, document_version_id, id);

ALTER TABLE ONLY source_asset_ocr_blocks
    ADD CONSTRAINT source_asset_ocr_blocks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source_asset_ocr_blocks
    ADD CONSTRAINT source_asset_ocr_blocks_project_id_document_version_id_id_key UNIQUE (project_id, document_version_id, id);

ALTER TABLE ONLY source_asset_ocr_runs
    ADD CONSTRAINT source_asset_ocr_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source_asset_ocr_runs
    ADD CONSTRAINT source_asset_ocr_runs_project_id_document_version_id_id_key UNIQUE (project_id, document_version_id, id);

ALTER TABLE ONLY source_assets
    ADD CONSTRAINT source_assets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source_assets
    ADD CONSTRAINT source_assets_project_id_document_version_id_id_key UNIQUE (project_id, document_version_id, id);

ALTER TABLE ONLY source_context_support
    ADD CONSTRAINT source_context_support_pkey PRIMARY KEY (project_id, id);

ALTER TABLE ONLY source_document_versions
    ADD CONSTRAINT source_document_versions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source_document_versions
    ADD CONSTRAINT source_document_versions_project_id_document_id_version_key UNIQUE (project_id, document_id, version);

ALTER TABLE ONLY source_document_versions
    ADD CONSTRAINT source_document_versions_project_id_id_key UNIQUE (project_id, id);

ALTER TABLE ONLY source_objects
    ADD CONSTRAINT source_objects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source_objects
    ADD CONSTRAINT source_objects_sha256_key UNIQUE (sha256);

ALTER TABLE ONLY source_objects
    ADD CONSTRAINT source_objects_storage_key_key UNIQUE (storage_key);

ALTER TABLE ONLY users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

ALTER TABLE ONLY users
    ADD CONSTRAINT users_username_key UNIQUE (username);

CREATE INDEX artifacts_kind_project_seq ON artifacts USING btree (kind, project_id, seq);

CREATE INDEX artifacts_project_seq ON artifacts USING btree (project_id, seq);

CREATE INDEX artifacts_recent_jobs ON artifacts USING btree (seq DESC) WHERE (kind = 'job'::text);

CREATE INDEX assertion_events_assertion ON assertion_events USING btree (project_id, assertion_id, decision_version);

CREATE INDEX assertion_events_project_created_seq ON assertion_events USING btree (project_id, created_at, seq);

CREATE UNIQUE INDEX assertion_events_provenance_fk ON assertion_events USING btree (project_id, assertion_id, id, canonical_record_id);

CREATE INDEX assertions_canonical ON assertions USING btree (project_id, canonical_record_id, status);

CREATE INDEX assertions_document ON assertions USING btree (project_id, document_id, document_version_id);

CREATE INDEX assertions_project_status ON assertions USING btree (project_id, status, created_at);

CREATE INDEX attribute_conflict_entity ON attribute_conflict_sets USING btree (project_id, entity_id, id) WHERE (state = 'open'::text);

CREATE INDEX attribute_conflict_history ON attribute_conflict_sets USING btree (project_id, entity_id, created_at, resolved_at);

CREATE INDEX attribute_conflict_member_record ON attribute_conflict_members USING btree (project_id, record_id);

CREATE INDEX attribute_conflict_operation ON attribute_conflict_sets USING btree (project_id, operation_id);

CREATE INDEX entity_duplicate_record ON entity_duplicate_keys USING btree (project_id, record_id);

CREATE INDEX entity_identity_record ON entity_identity_keys USING btree (project_id, record_id);

CREATE UNIQUE INDEX fact_keys_current_key ON fact_keys USING btree (project_id, fact_key) WHERE (retired_at IS NULL);

CREATE INDEX fact_keys_current_record ON fact_keys USING btree (project_id, canonical_record_id) WHERE (retired_at IS NULL);

CREATE INDEX identity_accepted_record ON assertion_events USING btree (project_id, canonical_record_id) WHERE (to_status = 'accepted'::text);

CREATE INDEX identity_mapping_record ON record_version_assertions USING btree (project_id, record_id);

CREATE INDEX ingest_batch_items_document_history ON ingest_batch_items USING btree (project_id, document_id, created_at);

CREATE INDEX ingest_batch_items_upload_object_idx ON ingest_batch_items USING btree (upload_object_id) WHERE (upload_object_id IS NOT NULL);

CREATE INDEX ingest_batches_project ON ingest_batches USING btree (project_id, created_at);

CREATE INDEX ingest_items_claim ON ingest_batch_items USING btree (batch_id, status, next_retry_at, created_at);

CREATE INDEX ingest_items_lease ON ingest_batch_items USING btree (lease_until) WHERE (status = 'running'::text);

CREATE INDEX ingest_runs_project_document ON ingest_runs USING btree (project_id, document_id, attempt);

CREATE INDEX merge_operations_project ON merge_operations USING btree (project_id, created_at);

CREATE INDEX merge_operations_project_created_seq ON merge_operations USING btree (project_id, created_at, seq);

CREATE INDEX milvus_generation_cleanup_claimable ON milvus_generation_cleanup_outbox USING btree (not_before, claim_until);

CREATE INDEX milvus_vector_receipts_record ON milvus_vector_receipts USING btree (project_id, index_version, record_id);

CREATE UNIQUE INDEX ontologies_project_id_fk ON ontologies USING btree (project_id, id);

CREATE INDEX ontologies_project_seq ON ontologies USING btree (project_id, seq);

CREATE INDEX ontology_drafts_project_seq ON ontology_drafts USING btree (project_id, seq);

CREATE INDEX ontology_drafts_project_status ON ontology_drafts USING btree (project_id, status, updated_at, id);

CREATE INDEX ontology_drafts_source_kind_idx ON ontology_drafts USING btree (project_id, source_kind);

CREATE INDEX ontology_operations_draft ON ontology_operations USING btree (project_id, draft_id, created_at, id);

CREATE UNIQUE INDEX ontology_operations_identity_fingerprint ON ontology_operations USING btree (project_id, draft_id, id, fingerprint);

CREATE INDEX ontology_operations_project_seq ON ontology_operations USING btree (project_id, seq);

CREATE INDEX ontology_operations_supersedes ON ontology_operations USING btree (project_id, draft_id, supersedes_operation_id);

CREATE INDEX ontology_review_decisions_draft ON ontology_review_decisions USING btree (project_id, draft_id, created_at, id);

CREATE INDEX ontology_review_decisions_project_seq ON ontology_review_decisions USING btree (project_id, seq);

CREATE INDEX ontology_review_decisions_supersedes ON ontology_review_decisions USING btree (project_id, draft_id, supersedes_decision_id);

CREATE INDEX preparation_files_session ON preparation_files USING btree (session_id);

CREATE INDEX preparation_ingest_source_lookup ON ingest_batch_items USING btree (project_id, source_version_id) WHERE (source_version_id IS NOT NULL);

CREATE INDEX preparation_operations_session ON preparation_operations USING btree (session_id);

CREATE INDEX preparation_sessions_expiry ON preparation_sessions USING btree (expires_at);

CREATE INDEX preparation_sessions_owner ON preparation_sessions USING btree (project_id, owner_id, created_at);

CREATE INDEX preparation_source_object_lookup ON source_document_versions USING btree (project_id, source_object_id);

CREATE INDEX preparation_tasks_dispatch ON preparation_tasks USING btree (status, created_at) WHERE (status = ANY (ARRAY['queued'::text, 'running'::text, 'cancelling'::text]));

CREATE INDEX projection_cleanup_claimable ON projection_cleanup_outbox USING btree (claim_until, created_at);

CREATE INDEX projection_cursors_status ON projection_cursors USING btree (target, status, updated_at);

CREATE INDEX projection_outbox_version ON projection_outbox USING btree (project_id, version_id);

CREATE INDEX provenance_edges_activity ON provenance_edges USING btree (project_id, activity_id);

CREATE INDEX provenance_edges_source ON provenance_edges USING btree (project_id, source_ref);

CREATE INDEX provenance_edges_target ON provenance_edges USING btree (project_id, target_ref);

CREATE INDEX qa_conversations_owner_project_updated ON qa_conversations USING btree (owner_id, project_id, updated_at DESC, id DESC);

CREATE INDEX record_fts_kind ON record_fts USING btree (project_id, kind);

CREATE INDEX record_fts_text_en ON record_fts USING gin (to_tsvector('english'::regconfig, text));

CREATE INDEX record_fts_text_trgm ON record_fts USING gin (text gin_trgm_ops);

CREATE INDEX record_operation_reservations_project ON record_operation_reservations USING btree (project_id, recorded_at);

CREATE INDEX record_version_assertions_assertion ON record_version_assertions USING btree (project_id, assertion_id);

CREATE INDEX record_version_assertions_version ON record_version_assertions USING btree (project_id, record_version_id);

CREATE UNIQUE INDEX record_versions_provenance_fk ON record_versions USING btree (project_id, id, version_id);

CREATE INDEX resolution_reviews_project_status ON resolution_reviews USING btree (project_id, status, created_at);

CREATE INDEX source_asr_reuse ON source_asset_asr_runs USING btree (project_id, asset_sha256, configuration_fingerprint, status);

CREATE UNIQUE INDEX source_chunk_version_project_fk ON record_versions USING btree (project_id, version_id);

CREATE INDEX source_context_assertion ON source_context_support USING btree (project_id, assertion_id);

CREATE INDEX source_context_audience ON source_context_support USING gin (audience_values);

CREATE INDEX source_context_business ON source_context_support USING gin (business_values);

CREATE INDEX source_context_current ON source_context_support USING btree (project_id, record_id) WHERE ((known_until IS NULL) AND (invalid_reason IS NULL));

CREATE INDEX source_context_document ON source_context_support USING btree (project_id, document_id, document_version_id);

CREATE INDEX source_context_history ON source_context_support USING btree (project_id, record_id, known_from, known_until);

CREATE INDEX source_context_platform ON source_context_support USING gin (platform_values);

CREATE INDEX source_ocr_reuse ON source_asset_ocr_runs USING btree (project_id, asset_sha256, configuration_fingerprint, status);

CREATE INDEX users_username_active ON users USING btree (username, is_active);

CREATE INDEX versions_candidate_many ON record_versions USING gin ((((payload)::jsonb #> '{metadata,discovery_candidate_ids}'::text[]))) WHERE ((superseded_at IS NULL) AND (((payload)::jsonb #> '{metadata,discovery_candidate_ids}'::text[]) IS NOT NULL));

CREATE INDEX versions_candidate_single ON record_versions USING btree (project_id, (((payload)::jsonb #>> '{metadata,discovery_candidate_id}'::text[]))) WHERE ((superseded_at IS NULL) AND (((payload)::jsonb #>> '{metadata,discovery_candidate_id}'::text[]) IS NOT NULL));

CREATE UNIQUE INDEX versions_current ON record_versions USING btree (project_id, id) WHERE (superseded_at IS NULL);

CREATE INDEX versions_current_audience_mode ON record_versions USING btree (project_id, audience_mode) WHERE (superseded_at IS NULL);

CREATE INDEX versions_current_audience_values_gin ON record_versions USING gin (audience_values) WHERE ((superseded_at IS NULL) AND (audience_mode = 'specific'::text));

CREATE INDEX versions_current_business_mode ON record_versions USING btree (project_id, business_mode) WHERE (superseded_at IS NULL);

CREATE INDEX versions_current_business_values_gin ON record_versions USING gin (business_values) WHERE ((superseded_at IS NULL) AND (business_mode = 'specific'::text));

CREATE INDEX versions_current_kind_counts ON record_versions USING btree (project_id, read_kind) WHERE (superseded_at IS NULL);

CREATE INDEX versions_current_kind_keyset ON record_versions USING btree (project_id, (((payload)::jsonb ->> 'kind'::text)), id, version) WHERE (superseded_at IS NULL);

CREATE INDEX versions_current_object_keyset ON record_versions USING btree (project_id, (((payload)::jsonb ->> 'object_id'::text)), id) WHERE (superseded_at IS NULL);

CREATE INDEX versions_current_platform_mode ON record_versions USING btree (project_id, platform_mode) WHERE (superseded_at IS NULL);

CREATE INDEX versions_current_platform_values_gin ON record_versions USING gin (platform_values) WHERE ((superseded_at IS NULL) AND (platform_mode = 'specific'::text));

CREATE INDEX versions_current_projection_attribute_owner ON record_versions USING btree (project_id, read_subject_id, id) WHERE ((superseded_at IS NULL) AND (read_kind = 'attribute'::text));

CREATE INDEX versions_current_subject_keyset ON record_versions USING btree (project_id, (((payload)::jsonb ->> 'subject_id'::text)), id) WHERE (superseded_at IS NULL);

CREATE INDEX versions_current_valid_from ON record_versions USING btree (project_id, valid_from_epoch_ms) WHERE ((superseded_at IS NULL) AND valid_from_known);

CREATE INDEX versions_current_valid_until ON record_versions USING btree (project_id, valid_until_epoch_ms) WHERE ((superseded_at IS NULL) AND valid_until_known);

CREATE INDEX versions_project ON record_versions USING btree (project_id, recorded_at, superseded_at);

CREATE INDEX versions_qa_text_bigrams ON record_versions USING gin (qa_text_bigrams(lower(COALESCE(((payload)::jsonb ->> 'text'::text), ''::text))));

CREATE INDEX versions_source_directory ON record_versions USING btree (project_id, (((payload)::jsonb ->> 'source_id'::text)), id, version) WHERE (((payload)::jsonb ->> 'source_id'::text) IS NOT NULL);

CREATE INDEX versions_system_end ON record_versions USING btree (project_id, superseded_at, recorded_at);

CREATE INDEX versions_visible_graph_keyset ON record_versions USING btree (project_id, read_kind, id, version) WHERE ((superseded_at IS NULL) AND read_visible);

CREATE INDEX versions_visible_summary_compact ON record_versions USING btree (project_id, read_kind, read_type) INCLUDE (valid_from_known) WHERE ((superseded_at IS NULL) AND read_visible);

CREATE TRIGGER artifacts_snapshot_summary BEFORE INSERT OR UPDATE OF kind, payload ON artifacts FOR EACH ROW WHEN ((new.kind = 'snapshot'::text)) EXECUTE FUNCTION write_snapshot_summary();

CREATE TRIGGER entity_duplicate_record AFTER INSERT ON record_versions FOR EACH ROW EXECUTE FUNCTION entity_duplicate_record_changed();

CREATE TRIGGER identity_key_record AFTER INSERT OR UPDATE OF superseded_at ON record_versions FOR EACH ROW EXECUTE FUNCTION identity_key_record_changed();

CREATE TRIGGER identity_support_event AFTER INSERT ON assertion_events FOR EACH ROW EXECUTE FUNCTION identity_support_changed();

CREATE TRIGGER identity_support_mapping AFTER INSERT ON record_version_assertions FOR EACH ROW EXECUTE FUNCTION identity_support_changed();

CREATE CONSTRAINT TRIGGER ontology_decisions_deferred_cycle_guard AFTER INSERT ON ontology_review_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_ontology_decision_cycle();

CREATE TRIGGER ontology_history_repairs_delete_immutable BEFORE DELETE ON ontology_history_repairs FOR EACH ROW EXECUTE FUNCTION reject_ontology_history_mutation('ontology history repair records are immutable');

CREATE TRIGGER ontology_history_repairs_insert_immutable BEFORE INSERT ON ontology_history_repairs FOR EACH ROW EXECUTE FUNCTION guard_ontology_repair_insert();

CREATE TRIGGER ontology_history_repairs_update_immutable BEFORE UPDATE ON ontology_history_repairs FOR EACH ROW EXECUTE FUNCTION reject_ontology_history_mutation('ontology history repair records are immutable');

CREATE CONSTRAINT TRIGGER ontology_operations_deferred_cycle_guard AFTER INSERT ON ontology_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_ontology_operation_cycle();

CREATE TRIGGER ontology_operations_delete_immutable BEFORE DELETE ON ontology_operations FOR EACH ROW EXECUTE FUNCTION guard_ontology_history_delete('ontology operations are immutable');

CREATE TRIGGER ontology_operations_immutable BEFORE UPDATE ON ontology_operations FOR EACH ROW EXECUTE FUNCTION reject_ontology_history_mutation('ontology operations are immutable');

CREATE TRIGGER ontology_operations_insert_immutable BEFORE INSERT ON ontology_operations FOR EACH ROW EXECUTE FUNCTION guard_ontology_operation_insert();

CREATE TRIGGER ontology_review_decisions_delete_immutable BEFORE DELETE ON ontology_review_decisions FOR EACH ROW EXECUTE FUNCTION guard_ontology_history_delete('ontology review decisions are immutable');

CREATE TRIGGER ontology_review_decisions_immutable BEFORE UPDATE ON ontology_review_decisions FOR EACH ROW EXECUTE FUNCTION reject_ontology_history_mutation('ontology review decisions are immutable');

CREATE TRIGGER ontology_review_decisions_insert_immutable BEFORE INSERT ON ontology_review_decisions FOR EACH ROW EXECUTE FUNCTION guard_ontology_decision_insert();

ALTER TABLE ONLY assertion_events
    ADD CONSTRAINT assertion_events_assertion_id_fkey FOREIGN KEY (assertion_id) REFERENCES assertions(id) ON DELETE CASCADE;

ALTER TABLE ONLY assertion_events
    ADD CONSTRAINT assertion_events_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY assertions
    ADD CONSTRAINT assertions_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY attribute_conflict_members
    ADD CONSTRAINT attribute_conflict_members_project_id_set_id_fkey FOREIGN KEY (project_id, set_id) REFERENCES attribute_conflict_sets(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY attribute_conflict_members
    ADD CONSTRAINT attribute_conflict_members_version_id_fkey FOREIGN KEY (version_id) REFERENCES record_versions(version_id);

ALTER TABLE ONLY attribute_conflict_sets
    ADD CONSTRAINT attribute_conflict_sets_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY chunk_source_spans
    ADD CONSTRAINT chunk_source_spans_asr_segment_fk FOREIGN KEY (project_id, document_version_id, asr_segment_id) REFERENCES source_asset_asr_segments(project_id, document_version_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY chunk_source_spans
    ADD CONSTRAINT chunk_source_spans_project_id_chunk_version_id_fkey FOREIGN KEY (project_id, chunk_version_id) REFERENCES record_versions(project_id, version_id) ON DELETE CASCADE;

ALTER TABLE ONLY chunk_source_spans
    ADD CONSTRAINT chunk_source_spans_project_id_document_version_id_block_id_fkey FOREIGN KEY (project_id, document_version_id, block_id) REFERENCES parsed_blocks(project_id, document_version_id, block_id) ON DELETE CASCADE;

ALTER TABLE ONLY chunk_source_spans
    ADD CONSTRAINT chunk_source_spans_project_id_document_version_id_fkey FOREIGN KEY (project_id, document_version_id) REFERENCES source_document_versions(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY chunk_source_spans
    ADD CONSTRAINT chunk_source_spans_project_id_document_version_id_ocr_bloc_fkey FOREIGN KEY (project_id, document_version_id, ocr_block_id) REFERENCES source_asset_ocr_blocks(project_id, document_version_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY entity_duplicate_keys
    ADD CONSTRAINT entity_duplicate_keys_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY entity_identity_keys
    ADD CONSTRAINT entity_identity_keys_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY fact_keys
    ADD CONSTRAINT fact_keys_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY ingest_batch_items
    ADD CONSTRAINT ingest_batch_items_project_id_batch_id_fkey FOREIGN KEY (project_id, batch_id) REFERENCES ingest_batches(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY ingest_batch_items
    ADD CONSTRAINT ingest_batch_items_upload_object_id_fkey FOREIGN KEY (upload_object_id) REFERENCES source_objects(id);

ALTER TABLE ONLY ingest_batches
    ADD CONSTRAINT ingest_batches_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY ingest_chunk_checkpoints
    ADD CONSTRAINT ingest_chunk_checkpoints_batch_item_id_fkey FOREIGN KEY (batch_item_id) REFERENCES ingest_batch_items(id) ON DELETE CASCADE;

ALTER TABLE ONLY ingest_item_attempts
    ADD CONSTRAINT ingest_item_attempts_item_id_fkey FOREIGN KEY (item_id) REFERENCES ingest_batch_items(id) ON DELETE CASCADE;

ALTER TABLE ONLY ingest_runs
    ADD CONSTRAINT ingest_runs_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY ingest_runs
    ADD CONSTRAINT ingest_runs_retry_of_fkey FOREIGN KEY (retry_of) REFERENCES ingest_runs(id);

ALTER TABLE ONLY ingest_stage_outputs
    ADD CONSTRAINT ingest_stage_outputs_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY llm_model_settings
    ADD CONSTRAINT llm_model_settings_active_id_fkey FOREIGN KEY (active_id) REFERENCES llm_models(id);

ALTER TABLE ONLY merge_operations
    ADD CONSTRAINT merge_operations_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY merge_operations
    ADD CONSTRAINT merge_operations_reversal_of_fkey FOREIGN KEY (reversal_of) REFERENCES merge_operations(id);

ALTER TABLE ONLY milvus_projection_receipts
    ADD CONSTRAINT milvus_projection_receipts_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY milvus_vector_receipts
    ADD CONSTRAINT milvus_vector_receipts_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY ontologies
    ADD CONSTRAINT ontologies_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id);

ALTER TABLE ONLY ontology_drafts
    ADD CONSTRAINT ontology_drafts_project_id_base_ontology_id_fkey FOREIGN KEY (project_id, base_ontology_id) REFERENCES ontologies(project_id, id);

ALTER TABLE ONLY ontology_drafts
    ADD CONSTRAINT ontology_drafts_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY ontology_drafts
    ADD CONSTRAINT ontology_drafts_project_id_published_ontology_id_fkey FOREIGN KEY (project_id, published_ontology_id) REFERENCES ontologies(project_id, id);

ALTER TABLE ONLY ontology_operations
    ADD CONSTRAINT ontology_operations_project_id_draft_id_fkey FOREIGN KEY (project_id, draft_id) REFERENCES ontology_drafts(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY ontology_operations
    ADD CONSTRAINT ontology_operations_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY ontology_operations
    ADD CONSTRAINT ontology_operations_supersedes_fk FOREIGN KEY (project_id, draft_id, supersedes_operation_id) REFERENCES ontology_operations(project_id, draft_id, id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY ontology_publish_requests
    ADD CONSTRAINT ontology_publish_requests_project_id_draft_id_fkey FOREIGN KEY (project_id, draft_id) REFERENCES ontology_drafts(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY ontology_publish_requests
    ADD CONSTRAINT ontology_publish_requests_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY ontology_publish_requests
    ADD CONSTRAINT ontology_publish_requests_project_id_result_ontology_id_fkey FOREIGN KEY (project_id, result_ontology_id) REFERENCES ontologies(project_id, id);

ALTER TABLE ONLY ontology_review_decisions
    ADD CONSTRAINT ontology_review_decisions_project_id_draft_id_fkey FOREIGN KEY (project_id, draft_id) REFERENCES ontology_drafts(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY ontology_review_decisions
    ADD CONSTRAINT ontology_review_decisions_project_id_draft_id_operation_id_fkey FOREIGN KEY (project_id, draft_id, operation_id) REFERENCES ontology_operations(project_id, draft_id, id);

ALTER TABLE ONLY ontology_review_decisions
    ADD CONSTRAINT ontology_review_decisions_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY ontology_review_decisions
    ADD CONSTRAINT ontology_review_decisions_supersedes_fk FOREIGN KEY (project_id, draft_id, supersedes_decision_id) REFERENCES ontology_review_decisions(project_id, draft_id, id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY parsed_blocks
    ADD CONSTRAINT parsed_blocks_project_id_document_version_id_fkey FOREIGN KEY (project_id, document_version_id) REFERENCES source_document_versions(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY preparation_files
    ADD CONSTRAINT preparation_files_session_id_fkey FOREIGN KEY (session_id) REFERENCES preparation_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY preparation_operations
    ADD CONSTRAINT preparation_operations_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY preparation_operations
    ADD CONSTRAINT preparation_operations_session_id_fkey FOREIGN KEY (session_id) REFERENCES preparation_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY preparation_sessions
    ADD CONSTRAINT preparation_sessions_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY preparation_tasks
    ADD CONSTRAINT preparation_tasks_file_id_fkey FOREIGN KEY (file_id) REFERENCES preparation_files(id) ON DELETE SET NULL;

ALTER TABLE ONLY preparation_tasks
    ADD CONSTRAINT preparation_tasks_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY project_projection_sequences
    ADD CONSTRAINT project_projection_sequences_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY projection_cursors
    ADD CONSTRAINT projection_cursors_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY projection_outbox
    ADD CONSTRAINT projection_outbox_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY projection_rebuild_snapshots
    ADD CONSTRAINT projection_rebuild_snapshots_project_id_target_fkey FOREIGN KEY (project_id, target) REFERENCES projection_cursors(project_id, target) ON DELETE CASCADE;

ALTER TABLE ONLY projection_rebuild_snapshots
    ADD CONSTRAINT projection_rebuild_snapshots_project_id_version_id_fkey FOREIGN KEY (project_id, version_id) REFERENCES record_versions(project_id, version_id) ON DELETE CASCADE;

ALTER TABLE ONLY provenance_activities
    ADD CONSTRAINT provenance_activities_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY provenance_edges
    ADD CONSTRAINT provenance_edges_project_id_activity_id_fkey FOREIGN KEY (project_id, activity_id) REFERENCES provenance_activities(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY qa_conversations
    ADD CONSTRAINT qa_conversations_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY record_operation_reservations
    ADD CONSTRAINT record_operation_reservations_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY record_version_assertions
    ADD CONSTRAINT record_version_assertions_project_id_assertion_id_assertio_fkey FOREIGN KEY (project_id, assertion_id, assertion_event_id, record_id) REFERENCES assertion_events(project_id, assertion_id, id, canonical_record_id) ON DELETE CASCADE;

ALTER TABLE ONLY record_version_assertions
    ADD CONSTRAINT record_version_assertions_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY record_version_assertions
    ADD CONSTRAINT record_version_assertions_project_id_record_id_record_vers_fkey FOREIGN KEY (project_id, record_id, record_version_id) REFERENCES record_versions(project_id, id, version_id) ON DELETE CASCADE;

ALTER TABLE ONLY record_versions
    ADD CONSTRAINT record_versions_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id);

ALTER TABLE ONLY resolution_reviews
    ADD CONSTRAINT resolution_reviews_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY source_asset_asr_runs
    ADD CONSTRAINT source_asset_asr_runs_project_id_document_version_id_asset_fkey FOREIGN KEY (project_id, document_version_id, asset_id) REFERENCES source_assets(project_id, document_version_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY source_asset_asr_segments
    ADD CONSTRAINT source_asset_asr_segments_project_id_document_version_id_a_fkey FOREIGN KEY (project_id, document_version_id, asr_run_id) REFERENCES source_asset_asr_runs(project_id, document_version_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY source_asset_ocr_blocks
    ADD CONSTRAINT source_asset_ocr_blocks_project_id_document_version_id_ocr_fkey FOREIGN KEY (project_id, document_version_id, ocr_run_id) REFERENCES source_asset_ocr_runs(project_id, document_version_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY source_asset_ocr_runs
    ADD CONSTRAINT source_asset_ocr_runs_project_id_document_version_id_asset_fkey FOREIGN KEY (project_id, document_version_id, asset_id) REFERENCES source_assets(project_id, document_version_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY source_assets
    ADD CONSTRAINT source_assets_project_id_document_version_id_fkey FOREIGN KEY (project_id, document_version_id) REFERENCES source_document_versions(project_id, id) ON DELETE CASCADE;

ALTER TABLE ONLY source_assets
    ADD CONSTRAINT source_assets_source_object_id_fkey FOREIGN KEY (source_object_id) REFERENCES source_objects(id);

ALTER TABLE ONLY source_context_support
    ADD CONSTRAINT source_context_support_assertion_id_fkey FOREIGN KEY (assertion_id) REFERENCES assertions(id) ON DELETE CASCADE;

ALTER TABLE ONLY source_context_support
    ADD CONSTRAINT source_context_support_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY source_document_versions
    ADD CONSTRAINT source_document_versions_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY source_document_versions
    ADD CONSTRAINT source_document_versions_source_object_id_fkey FOREIGN KEY (source_object_id) REFERENCES source_objects(id);

-- Initial row for the application model settings.
INSERT INTO llm_model_settings(singleton) VALUES (true);

DO $baseline_permissions$
DECLARE
    app_role text := COALESCE(NULLIF(current_setting('knowledge_service.app_role', true), ''), 'knowledge_app');
    app_schema text := current_schema();
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', app_schema, app_role);
        EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM %I', app_schema, app_role);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I', app_schema, app_role);
        EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', app_schema, app_role);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', app_schema, app_role);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I', app_schema, app_role);
    END IF;
END;
$baseline_permissions$;
