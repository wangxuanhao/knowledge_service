-- knowledge-service-migration: no-transaction
-- Run after 0013 commits. Explicit statement boundaries release each lock before
-- the next phase; the session-local helper functions survive autocommit.
CREATE OR REPLACE FUNCTION pg_temp.ks_scope_mode(value jsonb)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN jsonb_typeof(value) = 'array' THEN
            CASE WHEN jsonb_array_length(value) > 0 THEN 'specific' ELSE 'unknown' END
        WHEN jsonb_typeof(value) = 'object'
             AND value->>'mode' IN ('specific', 'all', 'unknown') THEN value->>'mode'
        ELSE 'unknown'
    END
$$;

-- knowledge-service-migration: statement-break
-- Match Python 3.12 unicodedata.normalize('NFKC', value).strip(): all 29 spaces.
CREATE OR REPLACE FUNCTION pg_temp.ks_normalized_scope_value(value text)
RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT btrim(normalize(value, NFKC),
        chr(9) || chr(10) || chr(11) || chr(12) || chr(13) ||
        chr(28) || chr(29) || chr(30) || chr(31) || chr(32) ||
        chr(133) || chr(160) || chr(5760) ||
        chr(8192) || chr(8193) || chr(8194) || chr(8195) || chr(8196) ||
        chr(8197) || chr(8198) || chr(8199) || chr(8200) || chr(8201) ||
        chr(8202) || chr(8232) || chr(8233) || chr(8239) || chr(8287) || chr(12288))
$$;

-- knowledge-service-migration: statement-break
CREATE OR REPLACE FUNCTION pg_temp.ks_scope_values(value jsonb)
RETURNS text[] LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN pg_temp.ks_scope_mode(value) <> 'specific' THEN '{}'::text[]
        WHEN jsonb_typeof(value) = 'array' THEN ARRAY(
            SELECT DISTINCT pg_temp.ks_normalized_scope_value(item) AS normalized
            FROM jsonb_array_elements_text(value) item
            WHERE pg_temp.ks_normalized_scope_value(item) <> '' ORDER BY normalized)
        ELSE ARRAY(
            SELECT DISTINCT pg_temp.ks_normalized_scope_value(item) AS normalized
            FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(value->'values') = 'array'
                     THEN value->'values' ELSE '[]'::jsonb END) item
            WHERE pg_temp.ks_normalized_scope_value(item) <> '' ORDER BY normalized)
    END
$$;

-- knowledge-service-migration: statement-break
CREATE OR REPLACE FUNCTION pg_temp.ks_platform_values(value jsonb)
RETURNS text[] LANGUAGE sql IMMUTABLE AS $$
    SELECT ARRAY(
        SELECT DISTINCT CASE lower(item)
            WHEN 'meituan' THEN '美团'
            WHEN 'taobao' THEN '淘宝'
            WHEN 'jd' THEN '京东'
            ELSE item
        END AS normalized
        FROM unnest(pg_temp.ks_scope_values(value)) item ORDER BY normalized)
$$;

-- knowledge-service-migration: statement-break
CREATE OR REPLACE FUNCTION pg_temp.ks_json_text_array(value text[])
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT '[' || COALESCE(string_agg(to_jsonb(item)::text, ',' ORDER BY ordinal), '') || ']'
    FROM unnest(value) WITH ORDINALITY AS values_(item, ordinal)
$$;

-- knowledge-service-migration: statement-break
CREATE OR REPLACE FUNCTION pg_temp.ks_backfill_native_applicability()
RETURNS void LANGUAGE sql AS $backfill$
WITH projected AS (
    SELECT project_id, id, version,
           pg_temp.ks_scope_mode(payload::jsonb#>'{metadata,applicability,platforms}') AS pm,
           pg_temp.ks_platform_values(payload::jsonb#>'{metadata,applicability,platforms}') AS pv,
           pg_temp.ks_scope_mode(payload::jsonb#>'{metadata,applicability,businesses}') AS bm,
           pg_temp.ks_scope_values(payload::jsonb#>'{metadata,applicability,businesses}') AS bv,
           pg_temp.ks_scope_mode(payload::jsonb#>'{metadata,applicability,audiences}') AS am,
           pg_temp.ks_scope_values(payload::jsonb#>'{metadata,applicability,audiences}') AS av,
           payload::jsonb->>'valid_from' AS valid_from,
           payload::jsonb->>'valid_until' AS valid_until
    FROM record_versions
    WHERE platform_mode IS NULL OR platform_values IS NULL
       OR business_mode IS NULL OR business_values IS NULL
       OR audience_mode IS NULL OR audience_values IS NULL
       OR scope_identity IS NULL OR valid_from_known IS NULL
       OR valid_from_epoch_ms IS NULL OR valid_until_known IS NULL
       OR valid_until_epoch_ms IS NULL
), identified AS (
    SELECT *, CASE
        WHEN pm = 'unknown' AND bm = 'unknown' AND am = 'unknown' THEN ''
        ELSE encode(digest(convert_to(
            CASE WHEN 'all' IN (pm, bm, am) THEN
                '{"audiences":{"mode":' || to_jsonb(am)::text || ',"values":' ||
                    pg_temp.ks_json_text_array(av) || '},"businesses":{"mode":' ||
                    to_jsonb(bm)::text || ',"values":' || pg_temp.ks_json_text_array(bv) ||
                    '},"platforms":{"mode":' || to_jsonb(pm)::text || ',"values":' ||
                    pg_temp.ks_json_text_array(pv) || '},"schema_version":2}'
            ELSE
                '{"audiences":' || pg_temp.ks_json_text_array(av) ||
                ',"businesses":' || pg_temp.ks_json_text_array(bv) ||
                ',"platforms":' || pg_temp.ks_json_text_array(pv) || '}'
            END, 'UTF8'), 'sha256'), 'hex')
    END AS identity
    FROM projected
)
UPDATE record_versions AS versions SET
    platform_mode = identified.pm, platform_values = identified.pv,
    business_mode = identified.bm, business_values = identified.bv,
    audience_mode = identified.am, audience_values = identified.av,
    scope_identity = identified.identity,
    valid_from_known = identified.valid_from IS NOT NULL,
    valid_from_epoch_ms = CASE WHEN identified.valid_from IS NULL THEN 0 ELSE
        floor(extract(epoch FROM identified.valid_from::timestamptz) * 1000)::bigint END,
    valid_until_known = identified.valid_until IS NOT NULL,
    valid_until_epoch_ms = CASE WHEN identified.valid_until IS NULL THEN 0 ELSE
        floor(extract(epoch FROM identified.valid_until::timestamptz) * 1000)::bigint END
FROM identified
WHERE versions.project_id = identified.project_id
  AND versions.id = identified.id AND versions.version = identified.version
$backfill$;

-- knowledge-service-migration: statement-break
SELECT pg_temp.ks_backfill_native_applicability();

-- knowledge-service-migration: statement-break
-- Add proof constraints after the backfill. NOT VALID avoids scanning under the
-- ALTER lock; VALIDATE uses a weaker lock, then SET NOT NULL reuses the proof.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'record_versions'::regclass
                     AND conname = 'record_versions_platform_mode_nn') THEN
        ALTER TABLE record_versions
            ADD CONSTRAINT record_versions_platform_mode_nn CHECK (platform_mode IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_platform_values_nn CHECK (platform_values IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_business_mode_nn CHECK (business_mode IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_business_values_nn CHECK (business_values IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_audience_mode_nn CHECK (audience_mode IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_audience_values_nn CHECK (audience_values IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_scope_identity_nn CHECK (scope_identity IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_valid_from_known_nn CHECK (valid_from_known IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_valid_from_epoch_ms_nn CHECK (valid_from_epoch_ms IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_valid_until_known_nn CHECK (valid_until_known IS NOT NULL) NOT VALID,
            ADD CONSTRAINT record_versions_valid_until_epoch_ms_nn CHECK (valid_until_epoch_ms IS NOT NULL) NOT VALID;
    END IF;
END
$$;

-- knowledge-service-migration: statement-break
-- Close the only write race: rows inserted by an old application after the first
-- pass remain NULL, the newly installed checks now reject further old writes, and
-- this second pass converges the finite gap before validation/SET NOT NULL.
SELECT pg_temp.ks_backfill_native_applicability();

-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_platform_mode_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_platform_values_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_business_mode_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_business_values_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_audience_mode_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_audience_values_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_scope_identity_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_valid_from_known_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_valid_from_epoch_ms_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_valid_until_known_nn;
-- knowledge-service-migration: statement-break
ALTER TABLE record_versions VALIDATE CONSTRAINT record_versions_valid_until_epoch_ms_nn;

-- knowledge-service-migration: statement-break
ALTER TABLE record_versions
    ALTER COLUMN platform_mode SET NOT NULL,
    ALTER COLUMN platform_values SET NOT NULL,
    ALTER COLUMN business_mode SET NOT NULL,
    ALTER COLUMN business_values SET NOT NULL,
    ALTER COLUMN audience_mode SET NOT NULL,
    ALTER COLUMN audience_values SET NOT NULL,
    ALTER COLUMN scope_identity SET NOT NULL,
    ALTER COLUMN valid_from_known SET NOT NULL,
    ALTER COLUMN valid_from_epoch_ms SET NOT NULL,
    ALTER COLUMN valid_until_known SET NOT NULL,
    ALTER COLUMN valid_until_epoch_ms SET NOT NULL;

-- knowledge-service-migration: statement-break
ALTER TABLE record_versions
    DROP CONSTRAINT IF EXISTS record_versions_platform_mode_nn,
    DROP CONSTRAINT IF EXISTS record_versions_platform_values_nn,
    DROP CONSTRAINT IF EXISTS record_versions_business_mode_nn,
    DROP CONSTRAINT IF EXISTS record_versions_business_values_nn,
    DROP CONSTRAINT IF EXISTS record_versions_audience_mode_nn,
    DROP CONSTRAINT IF EXISTS record_versions_audience_values_nn,
    DROP CONSTRAINT IF EXISTS record_versions_scope_identity_nn,
    DROP CONSTRAINT IF EXISTS record_versions_valid_from_known_nn,
    DROP CONSTRAINT IF EXISTS record_versions_valid_from_epoch_ms_nn,
    DROP CONSTRAINT IF EXISTS record_versions_valid_until_known_nn,
    DROP CONSTRAINT IF EXISTS record_versions_valid_until_epoch_ms_nn;
