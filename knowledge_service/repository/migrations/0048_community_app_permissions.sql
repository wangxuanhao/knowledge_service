DO $$
DECLARE
    app_role text := COALESCE(NULLIF(current_setting('knowledge_service.app_role', true), ''), 'knowledge_app');
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON community_graph_changes, community_scopes, knowledge_communities TO %I',
            app_role
        );
    END IF;
END;
$$;
