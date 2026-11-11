# Platform LLM Management Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox syntax for tracking.

**Goal:** Save multiple OpenAI-compatible model configurations in the platform and switch the global default safely.

**Architecture:** PostgreSQL stores model profiles and the atomic default selection. An authenticated encryption service protects credentials and durable snapshots. A context-bound immutable runtime configuration is resolved once per operation, including background work and streaming answers. Environment settings remain the bootstrap compatibility source.

**Tech Stack:** Python 3.12, FastAPI, psycopg, cryptography Fernet, native JavaScript/CSS, pytest and node:test.

## Task 1 — Persistent configuration and management API

- [x] Add failing behavioral tests in tests/service/test_llm_models.py for encrypted persistence, default switching, reopen, keep-secret edits, configuration validation, connection testing, and superadmin-only routes. Run with D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_llm_models.py -q and confirm missing feature failures.
- [x] Add migration 0035_llm_models.sql, repository/llm_models.py, core/llm_config.py, services/llm_models.py and api/llm_models.py. Wire service composition and routes, declare cryptography dependency. Keep schema and encryption concerns isolated. Use existing repository connection/transaction conventions.
- [x] API contract is GET/POST /api/llm-models, PATCH /api/llm-models/{id}, POST /api/llm-models/{id}/activate, POST /api/llm-models/{id}/test. List returns items, active and environment availability. Public profiles contain id, name, base_url, model, has_api_key, is_active. Never return ciphertext or plaintext secrets.
- [x] Runtime contract is LLMConfig frozen dataclass with profile_id, name, base_url, model, api_key, max_tokens and proxy, plus safe public identity. LLMModels.resolve() returns active profile or environment. bound_llm(config) and current_llm() use ContextVar. Snapshot sealing/unsealing preserves credentials privately. No process-global environment mutations.
- [x] Run targeted tests and review specification compliance followed by quality/security. Fix findings.

## Task 2 — Runtime integration and task isolation

- [x] Add failing tests for stable binding across default switches and concurrent tasks, and ordinary caller entry points. Read jobs, ingest workers, semantica adapter, answers, hierarchy suggestions, experiments and capability reporting.
- [x] Replace direct KG_LLM_* reads at integrations/semantica_adapter.py, services/answers.py, services/service.py, services/hierarchy_suggestion.py, services/retrieval_experiments.py, services/runtime_capabilities.py with the unified resolver. Wrap complete operations and generators in a configuration context. Pass context explicitly into child threads.
- [x] Bind durable job and batch-document work before parsing/extraction, persist sealed snapshots without exposing them in public task/batch payloads. Preserve checkpoint identity and retry consistency. Record safe model metadata with results/provenance. Existing records without snapshots resolve once and persist before LLM work.
- [x] Run targeted pytest groups in one session. Review specification and quality findings.

## Task 3 — Platform UI

- [x] Add frontend behavioral tests in tests/service/llm-models.test.cjs for list loading, save validation, preserve-key edits, connection-test feedback, activation, authorization visibility and failure states. Run node --test tests/service/llm-models.test.cjs and confirm failures.
- [x] Add web/llm-models.js and web/llm-models.css. Integrate versioned assets, tab, management navigation and titles into index.html, menu-hierarchy.js and the existing tab/context helpers. Match the current restrained administration design. Show clear active model, environment fallback, connection status, loading and error states. Use accessible labels and native dialog focus handling.
- [x] Run frontend tests, targeted navigation tests and browser validation when available. Review specification and quality.

## Task 4 — Verification and delivery

- [x] Apply migration with the existing migrator, update .env.example and minimal README usage with secret-key backup requirements, and restart the local service if appropriate.
- [x] Run targeted and broader Python and frontend regression tests sequentially, inspect git diff --check, and verify routes/page through the normal application entry point. Do not expose .env values.
- [x] Report verified outcomes and any remaining live-provider validation limits. Leave unrelated work intact.

## Verification record

- Expanded Python API/auth/runtime/ingest/experiment regression group passed 142 tests. Final caller and metadata compatibility group passed 89 tests.
- Frontend suite passed 1,044 tests, with zero failures.
- Schema migrated and checked at version 35 using the application role permissions.
- Actual browser login, model listing, native dialog/password clearing, desktop/mobile layout and absence of page errors verified against the restarted service on port 8100.
- Existing configured provider passed the real connection probe through a temporary profile in an isolated test database, which was cleaned up afterward.
- Specification and quality reviews completed with findings fixed.
- Final release authorization/configuration/generation-state group passed 71 tests after the last compatibility fixes.
- The encryption dependency and its transitive packages were added to the lockfile while preserving existing package versions.
