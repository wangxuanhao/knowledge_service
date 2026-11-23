CREATE TABLE community_graph_changes (
    project_id text PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    generation bigint NOT NULL,
    changed_at timestamptz NOT NULL
);

CREATE TABLE community_scopes (
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope_key text NOT NULL,
    scope jsonb NOT NULL,
    observed_generation bigint NOT NULL DEFAULT -1,
    live_revision text,
    revision text,
    job_id text,
    algorithm text,
    counts jsonb,
    built_at timestamptz,
    PRIMARY KEY (project_id, scope_key)
);

CREATE TABLE knowledge_communities (
    project_id text NOT NULL,
    scope_key text NOT NULL,
    id text NOT NULL,
    parent_id text,
    level integer NOT NULL,
    title text NOT NULL,
    member_ids text[] NOT NULL,
    member_count integer NOT NULL,
    relationship_count integer NOT NULL,
    evidence_count integer NOT NULL,
    search_text text NOT NULL,
    content jsonb NOT NULL,
    report jsonb NOT NULL,
    PRIMARY KEY (project_id, scope_key, id),
    FOREIGN KEY (project_id, scope_key) REFERENCES community_scopes(project_id, scope_key) ON DELETE CASCADE
);

CREATE INDEX knowledge_communities_order_idx
    ON knowledge_communities (project_id, scope_key, level, member_count DESC, id);
CREATE INDEX knowledge_communities_parent_idx
    ON knowledge_communities (project_id, scope_key, parent_id);
CREATE INDEX knowledge_communities_members_idx ON knowledge_communities USING gin (member_ids);

CREATE FUNCTION flag_community_graph_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_project text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_project := OLD.project_id;
    ELSE
        target_project := NEW.project_id;
    END IF;
    IF EXISTS (SELECT 1 FROM projects WHERE id = target_project) THEN
        INSERT INTO community_graph_changes (project_id, generation, changed_at)
        VALUES (target_project, 1, clock_timestamp())
        ON CONFLICT (project_id) DO UPDATE
           SET generation = community_graph_changes.generation + 1, changed_at = clock_timestamp();
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER community_graph_insert AFTER INSERT ON record_versions FOR EACH ROW
    WHEN (NEW.read_kind IN ('entity', 'relation', 'attribute') AND NEW.read_visible AND NOT NEW.read_quarantined)
    EXECUTE FUNCTION flag_community_graph_change();
CREATE TRIGGER community_graph_update AFTER UPDATE ON record_versions FOR EACH ROW
    WHEN ((OLD.read_kind IN ('entity', 'relation', 'attribute') AND OLD.read_visible AND NOT OLD.read_quarantined
           OR NEW.read_kind IN ('entity', 'relation', 'attribute') AND NEW.read_visible AND NOT NEW.read_quarantined)
          AND (OLD.payload, OLD.superseded_at) IS DISTINCT FROM (NEW.payload, NEW.superseded_at))
    EXECUTE FUNCTION flag_community_graph_change();
CREATE TRIGGER community_graph_delete AFTER DELETE ON record_versions FOR EACH ROW
    WHEN (OLD.read_kind IN ('entity', 'relation', 'attribute') AND OLD.read_visible AND NOT OLD.read_quarantined)
    EXECUTE FUNCTION flag_community_graph_change();

CREATE TRIGGER community_support_insert AFTER INSERT ON source_context_support FOR EACH ROW
    WHEN (NEW.kind IN ('entity', 'relation', 'attribute') AND NEW.invalid_reason IS NULL AND NEW.known_until IS NULL)
    EXECUTE FUNCTION flag_community_graph_change();
CREATE TRIGGER community_support_update AFTER UPDATE ON source_context_support FOR EACH ROW
    WHEN (NEW.kind IN ('entity', 'relation', 'attribute') AND OLD IS DISTINCT FROM NEW)
    EXECUTE FUNCTION flag_community_graph_change();
CREATE TRIGGER community_support_delete AFTER DELETE ON source_context_support FOR EACH ROW
    WHEN (OLD.kind IN ('entity', 'relation', 'attribute') AND OLD.invalid_reason IS NULL AND OLD.known_until IS NULL)
    EXECUTE FUNCTION flag_community_graph_change();

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'knowledge_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            community_graph_changes, community_scopes, knowledge_communities TO knowledge_app;
    END IF;
END;
$$;
