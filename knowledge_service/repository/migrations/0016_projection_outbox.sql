-- Authoritative per-project ordering and durable projection delivery state.
CREATE TABLE IF NOT EXISTS project_projection_sequences (
    project_id text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    projection_seq bigint NOT NULL DEFAULT 0 CHECK (projection_seq >= 0)
);

INSERT INTO project_projection_sequences(project_id, projection_seq)
SELECT id, 0 FROM projects
ON CONFLICT (project_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS projection_outbox (
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    seq bigint NOT NULL CHECK (seq > 0),
    operation text NOT NULL CHECK (operation IN ('upsert', 'tombstone')),
    version_id text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (project_id, seq),
    UNIQUE (project_id, seq)
);

CREATE INDEX IF NOT EXISTS projection_outbox_version
    ON projection_outbox(project_id, version_id);

CREATE TABLE IF NOT EXISTS projection_cursors (
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    applied_seq bigint NOT NULL DEFAULT 0 CHECK (applied_seq >= 0),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'applying', 'ready', 'failed', 'rebuilding',
        'validating', 'switching')),
    last_error text,
    claim_owner text,
    claim_token text,
    claim_until timestamptz,
    claimed_from_seq bigint,
    claimed_to_seq bigint,
    active_destination text,
    previous_destination text,
    rollback_until timestamptz,
    rollback_applied_seq bigint NOT NULL DEFAULT 0,
    rebuild_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (project_id, target)
);

CREATE INDEX IF NOT EXISTS projection_cursors_status
    ON projection_cursors(target, status, updated_at);

CREATE TABLE IF NOT EXISTS projection_rebuild_snapshots (
    project_id text NOT NULL,
    target text NOT NULL,
    build_id text NOT NULL,
    ordinal integer NOT NULL,
    version_id text NOT NULL,
    PRIMARY KEY (project_id, target, build_id, ordinal),
    UNIQUE (project_id, target, build_id, version_id),
    FOREIGN KEY (project_id, target)
        REFERENCES projection_cursors(project_id, target) ON DELETE CASCADE,
    FOREIGN KEY (project_id, version_id)
        REFERENCES record_versions(project_id, version_id) ON DELETE CASCADE
);
