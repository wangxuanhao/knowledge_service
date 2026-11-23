-- Private display snapshots only. Frozen evidence and governed knowledge remain
-- in their existing authoritative stores and outlive conversation deletion.
CREATE TABLE qa_conversations (
    id          text PRIMARY KEY,
    owner_id    text NOT NULL,
    project_id  text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title       text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 80),
    title_auto  boolean NOT NULL DEFAULT true,
    messages    text NOT NULL DEFAULT '[]' CHECK (json_typeof(messages::json) = 'array'),
    version     integer NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at  timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL
);

CREATE INDEX qa_conversations_owner_project_updated
    ON qa_conversations (owner_id, project_id, updated_at DESC, id DESC);

-- The application has no DDL rights; support configured application roles even
-- where the migration owner has no default table privileges for that role.
DO $$
DECLARE app_role text := COALESCE(NULLIF(current_setting('knowledge_service.app_role',true),''),'knowledge_app');
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
        EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON qa_conversations TO %I',app_role);
    END IF;
END $$;
