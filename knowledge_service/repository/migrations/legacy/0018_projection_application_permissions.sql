-- Projection delivery uses the application role, including on installations
-- whose migration owner has no default privileges for newly created tables.
DO $$
DECLARE app_role TEXT := COALESCE(NULLIF(current_setting('knowledge_service.app_role', true), ''), 'knowledge_app');
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON project_projection_sequences, projection_outbox, projection_cursors, projection_rebuild_snapshots, projection_cleanup_outbox TO %I',
            app_role
        );
    END IF;
END $$;
