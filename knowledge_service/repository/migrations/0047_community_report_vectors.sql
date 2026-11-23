ALTER TABLE community_scopes
    ADD COLUMN vector_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN vector_snapshot jsonb;
