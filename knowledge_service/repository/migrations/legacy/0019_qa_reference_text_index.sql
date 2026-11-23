-- Chinese two-character search terms cannot benefit from pg_trgm LIKE indexes.
-- Index adjacent character pairs, then recheck the actual substring in SQL.
-- No record mutation or derived source metadata is required.
CREATE OR REPLACE FUNCTION qa_text_bigrams(value text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT COALESCE(array_agg(DISTINCT substr(value, n, 2)), ARRAY[]::text[])
    FROM generate_series(1, char_length(value) - 1) AS n
$$;

CREATE INDEX IF NOT EXISTS versions_qa_text_bigrams
    ON record_versions USING gin
    (qa_text_bigrams(lower(COALESCE(payload::jsonb->>'text', ''))));
