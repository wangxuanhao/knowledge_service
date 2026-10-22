-- knowledge-service-migration: no-transaction
-- Preserve byte-exact authoritative payloads. These generated values are an
-- automatically maintained read model; historical versions keep their own
-- values and ordinary writes cannot make the projection stale.
-- Add all stored columns in one ALTER so existing payloads are rewritten once.
ALTER TABLE record_versions
    ADD COLUMN IF NOT EXISTS read_kind text GENERATED ALWAYS AS (payload::jsonb->>'kind') STORED,
    ADD COLUMN IF NOT EXISTS read_type text GENERATED ALWAYS AS (payload::jsonb->>'type') STORED,
    ADD COLUMN IF NOT EXISTS read_text text GENERATED ALWAYS AS (left(COALESCE(payload::jsonb->>'text',''),160)) STORED,
    ADD COLUMN IF NOT EXISTS read_subject_id text GENERATED ALWAYS AS (payload::jsonb->>'subject_id') STORED,
    ADD COLUMN IF NOT EXISTS read_object_id text GENERATED ALWAYS AS (payload::jsonb->>'object_id') STORED,
    ADD COLUMN IF NOT EXISTS read_visible boolean GENERATED ALWAYS AS (
        COALESCE(payload::jsonb#>'{metadata,_deleted}', 'null'::jsonb)
            IN ('null'::jsonb,'false'::jsonb,'0'::jsonb,'""'::jsonb,'[]'::jsonb,'{}'::jsonb)
        AND COALESCE(payload::jsonb#>'{metadata,_audit}', 'null'::jsonb)
            IN ('null'::jsonb,'false'::jsonb,'0'::jsonb,'""'::jsonb,'[]'::jsonb,'{}'::jsonb)
    ) STORED;
-- knowledge-service-migration: statement-break
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_visible_graph_keyset
    ON record_versions (project_id, read_kind, id, version)
    WHERE superseded_at IS NULL AND read_visible;
-- knowledge-service-migration: statement-break
-- Project summaries retain their existing count semantics, including current
-- tombstones/audit rows. Visibility belongs only to scoped reads.
CREATE INDEX CONCURRENTLY IF NOT EXISTS versions_current_kind_counts
    ON record_versions (project_id, read_kind)
    WHERE superseded_at IS NULL;
-- knowledge-service-migration: statement-break
ANALYZE record_versions;
