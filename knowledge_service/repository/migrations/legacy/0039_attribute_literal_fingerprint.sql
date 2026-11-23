-- Compare typed values without repeatedly decoding large paragraph payloads
-- for every source occurrence. No authority payload/version is rewritten.
CREATE FUNCTION attribute_literal_fingerprint(body text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT CASE WHEN body::jsonb->>'kind'='attribute' THEN
  encode(sha256(convert_to(coalesce((body::jsonb->'datatype')::text,'null') || ':' ||
   coalesce((body::jsonb->'value')::text,'null'),'UTF8')),'hex') END
$$;
ALTER TABLE record_versions ADD COLUMN read_attribute_fingerprint text
 GENERATED ALWAYS AS (attribute_literal_fingerprint(payload)) STORED;
