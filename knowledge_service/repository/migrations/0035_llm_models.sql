CREATE TABLE llm_models (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL CHECK(length(name)>0),
 base_url TEXT NOT NULL,
 model TEXT NOT NULL CHECK(length(model)>0),
 api_key_encrypted TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE llm_model_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
 active_id TEXT REFERENCES llm_models(id),
 key_check TEXT
);
INSERT INTO llm_model_settings(singleton) VALUES (true);
CREATE TABLE llm_task_snapshots (
 task_id TEXT PRIMARY KEY,
 sealed_config TEXT NOT NULL,
 identity TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
DO $$
DECLARE app_role TEXT := COALESCE(NULLIF(current_setting('knowledge_service.app_role', true), ''), 'knowledge_app');
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=app_role) THEN
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON llm_models, llm_model_settings, llm_task_snapshots TO %I',app_role);
 END IF;
END $$;
