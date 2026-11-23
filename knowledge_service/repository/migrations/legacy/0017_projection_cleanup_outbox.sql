-- Project cleanup survives deletion of the authoritative projects row.
CREATE TABLE IF NOT EXISTS projection_cleanup_outbox (
    project_id text NOT NULL,
    target text NOT NULL CHECK (target IN ('milvus','neo4j')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claim_owner text,
    claim_token text,
    claim_until timestamptz,
    last_error text,
    PRIMARY KEY (project_id, target)
);

CREATE INDEX IF NOT EXISTS projection_cleanup_claimable
    ON projection_cleanup_outbox(claim_until, created_at);
