# Architecture, Milvus, Ontology and QA Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the intended application layering, make Milvus projection durable and observable, bind audit events to authenticated actors, deepen ontology-driven extraction with Semantica quality checks, and expose frozen source text in QA and the generated operator manual.

**Architecture:** `knowledge_service/bootstrap.py` becomes the sole composition root; HTTP routes call services only, services own use cases and public repository units of work, and repository/integrations depend only on domain values. PostgreSQL remains authoritative while Milvus is updated through a leased, generation-fenced outbox. QA summaries are derived only from frozen provenance edges, and ontology publication combines existing validation with a stable Semantica report.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, PostgreSQL, Milvus/pymilvus, Semantica 0.7.0, vanilla JavaScript, Node test runner, pytest.

---

## File map

- Create `knowledge_service/domain/__init__.py`, `audit.py`, `ontology_names.py`, `projection.py`: pure values, codecs and invariants.
- Create `knowledge_service/bootstrap.py`: application/container construction only.
- Create `knowledge_service/services/ontology_discovery_workflow.py`: open-discovery transactions previously in the route.
- Create `knowledge_service/services/api_workflows.py`: narrow project, knowledge, workspace, parity, ontology-change, evidence and provenance use cases used by HTTP routes.
- Create `knowledge_service/repository/unit_of_work.py`: public transaction seam.
- Create `knowledge_service/repository/projection_jobs_store.py` and migration `0010_projection_jobs.sql`: durable projection queue and project generation state.
- Create `knowledge_service/services/projection_sync.py`, `runtime_capabilities.py`: worker and truthful health DTO.
- Create `knowledge_service/services/answer_evidence.py`: frozen citation/source summaries.
- Create `knowledge_service/integrations/semantica_ontology.py`: Semantica quality adapter.
- Create `knowledge_service/services/ontology_slice.py`: deterministic ontology slice selection.
- Modify API/service/repository/integration modules listed per task to adopt those boundaries.
- Modify `knowledge_service/web/workbench.js`, `runtime-view.js`, related CSS/HTML only where required for evidence and projection status.
- Modify `scripts/build_knowledge_service_manual.py`, screenshots under `docs/assets/manual-v110/`, and regenerate `docs/知识图谱操作手册.html`.

### Task 1: Executable layering rules and pure domain values

**Files:**
- Create: `knowledge_service/domain/__init__.py`
- Create: `knowledge_service/domain/ontology_names.py`
- Create: `tests/service/test_architecture_boundaries.py`
- Modify: `knowledge_service/services/discovery_vocabulary.py`
- Modify: `knowledge_service/services/ontology_iri.py`
- Modify: `knowledge_service/integrations/semantica_adapter.py`
- Modify: `knowledge_service/repository/discovery_run_store.py`
- Modify: `knowledge_service/repository/core.py`

- [ ] Write import-AST tests that reject `repository|integrations -> services`, `services -> api`, `api -> repository|integrations`, API repository attribute access, and API/service use of `_transaction`, `_put`, or `_db`.
- [ ] Run `python -m pytest tests/service/test_architecture_boundaries.py -q` and verify it fails on the known reverse imports and private route accesses.
- [ ] Move `canonical_name`, `valid_application_iri`, and their constants into `domain/ontology_names.py`; leave compatibility re-exports only where existing callers require them.
- [ ] Replace integration/repository imports of services with domain imports and replace repository callbacks into `Ontology`, `FormalFactWriter`, and `revalidation` with immutable command/plan inputs created by services.
- [ ] Run the architecture test plus `test_ontology_names.py`, `test_semantica_adapter.py`, `test_formal_writes.py`, and `test_ontology_version_management.py`; keep the new architecture test red only for Task 2 API violations.

### Task 2: Composition root, public UoW, and discovery workflow

**Files:**
- Create: `knowledge_service/bootstrap.py`
- Create: `knowledge_service/repository/unit_of_work.py`
- Create: `knowledge_service/services/ontology_discovery_workflow.py`
- Create: `knowledge_service/services/api_workflows.py`
- Create: `tests/service/test_bootstrap.py`
- Create: `tests/service/test_ontology_discovery_workflow.py`
- Create: `tests/service/test_api_service_boundaries.py`
- Modify: `knowledge_service/api/__init__.py`
- Modify: `knowledge_service/api/auth.py`
- Modify: `knowledge_service/api/users.py`
- Modify: `knowledge_service/api/projects.py`
- Modify: `knowledge_service/api/knowledge.py`
- Modify: `knowledge_service/api/workspace.py`
- Modify: `knowledge_service/api/parity.py`
- Modify: `knowledge_service/api/ontology_changes.py`
- Modify: `knowledge_service/api/evidence.py`
- Modify: `knowledge_service/api/provenance.py`
- Modify: `knowledge_service/api/reviews.py`
- Modify: `knowledge_service/api/ontology_drafts.py`
- Modify: `knowledge_service/api/ontology_discovery.py`
- Modify: `knowledge_service/repository/__init__.py`
- Modify: `knowledge_service/repository/core.py`
- Modify: `tests/service/test_ontology_discovery.py`

- [ ] Write failing tests for a public `repository.unit_of_work()` commit/rollback seam and workflow-owned `create_run()`/`finalize_run()` CAS, idempotency, and materialization behavior.
- [ ] Run the focused tests and verify failures are caused by the missing UoW/workflow.
- [ ] Implement a narrow UoW exposing discovery and record stores without `_db`, `_put`, or `_transaction`; migrate the two private transactions and direct `_put` call from `api/ontology_discovery.py`.
- [ ] Add endpoint-contract tests for every router module. For each endpoint, assert the route performs only validation/authentication, calls exactly one public workflow method, and maps the workflow result/error without touching `service.repository`, Milvus, the encoder, or repository stores.
- [ ] Implement explicit workflow methods grouped by use case: `AuthWorkflow.login/me/change_password`, `UserWorkflow.list/create/update/delete`, `ProjectWorkflow.list/create/get/update/delete`, `KnowledgeWorkflow.records/query/ingest/search/answer/ontology`, `WorkspaceWorkflow.dashboard/rebuild/status`, `ParityWorkflow.jobs/preview/governance/snapshot/evaluate/export/stream_answer`, `OntologyChangeWorkflow.list/create/detail/decide`, `EvidenceQuery.get`, `ProvenanceQuery.trace`, and draft/review workflow entry points. Move transaction boundaries, derived-store cleanup, rebuild loops and read-model assembly out of route files.
- [ ] Migrate `auth.py`, `users.py`, `projects.py`, `knowledge.py`, `workspace.py`, `parity.py`, `ontology_changes.py`, `evidence.py`, `provenance.py`, `reviews.py`, `ontology_drafts.py`, and `ontology_discovery.py` endpoint by endpoint; preserve existing paths/status codes/response shapes and run each module's existing API/auth/RBAC tests immediately after migration.
- [ ] Write a failing bootstrap test asserting `api.create_app()` receives a constructed container and route modules do not construct integrations/repositories.
- [ ] Move encoder, Milvus, repository, services, worker, and router construction into `bootstrap.py`; keep `api/__init__.py` limited to FastAPI lifecycle/router assembly.
- [ ] Run Task 1/2 tests and ensure `test_architecture_boundaries.py` is fully green.

### Task 3: Authenticated ActorRef across all governance writes

**Files:**
- Create: `knowledge_service/domain/audit.py`
- Create: `tests/service/test_actor_ref.py`
- Create: `tests/service/test_authoritative_actor_api.py`
- Modify: `knowledge_service/models.py`
- Modify: `knowledge_service/api/knowledge.py`
- Modify: `knowledge_service/api/workspace.py`
- Modify: `knowledge_service/api/ontology_discovery.py`
- Modify: `knowledge_service/api/ontology_changes.py`
- Modify: `knowledge_service/api/ontology_drafts.py`
- Modify: `knowledge_service/api/parity.py`
- Modify: `knowledge_service/api/reviews.py`
- Modify: `knowledge_service/services/ontology_drafts_*.py`
- Modify: `knowledge_service/services/ontology_drafts.py`
- Modify: `knowledge_service/services/reviews.py`
- Modify: `knowledge_service/services/reclassify.py`
- Modify: `knowledge_service/repository/assertions_store.py`
- Modify: `knowledge_service/repository/review_store.py`

- [ ] Write codec tests for canonical JSON/base64url storage, legacy strings, malformed `actor:v1:` values, stable display strings, and snapshot round-trips.
- [ ] Verify RED, then implement immutable `ActorRef`, `encode_actor`, `decode_actor`, `display_actor`, and `actor_from_user`.
- [ ] Build a parameterized governance-write inventory covering record resolution, review decisions, draft create/command/submit/decision/rebase/close/batch-approve/publish, discovery draft/run/finalize/publish, ontology-change create/decision, workspace ontology writes, reclassification, merge/delete/restore/undo and snapshots. For every HTTP case assert authenticated user A cannot forge actor B, the request model rejects `actor` (`extra='forbid'`), and stored audit data decodes to A.
- [ ] Remove actor fields from public request models; derive the actor from `request.state.user`, use structured system actors for workers, and accept only `ActorRef` below HTTP.
- [ ] Preserve response compatibility with `actor` plus `actor_ref`; update existing actor assertions and run auth, review, draft, reclassify, provenance, and snapshot tests.

### Task 4: Durable Milvus projection outbox and generation fencing

**Files:**
- Create: `knowledge_service/repository/migrations/0010_projection_jobs.sql`
- Create: `knowledge_service/domain/projection.py`
- Create: `knowledge_service/repository/projection_jobs_store.py`
- Create: `knowledge_service/services/projection_sync.py`
- Create: `tests/service/test_projection_jobs_store.py`
- Create: `tests/service/test_projection_sync.py`
- Modify: `knowledge_service/repository/core.py`
- Modify: `knowledge_service/services/service.py`
- Modify: `knowledge_service/api/workspace.py`
- Modify: `knowledge_service/integrations/milvus_store.py`

- [ ] Write migration/store tests for schema, dedupe keys, enqueue-in-authoritative-transaction, head-of-line claim, lease token CAS, heartbeat, retry_wait/backoff, terminal failure/manual retry, supersede restrictions, and generation allocation.
- [ ] Verify RED and implement the migration plus store with PostgreSQL `FOR UPDATE SKIP LOCKED` and project advisory locks; keep test doubles only at the Milvus network boundary.
- [ ] Write worker tests for same-project multi-worker ordering, lease expiry while waiting for the lock, old generation versus rebuild, immutable rebuild snapshot, Milvus success/DB-completion crash replay, and failed rebuild blocking.
- [ ] Implement staging-generation writes and `active_generation` filtering in `MilvusStore`; perform pointer cutover only in the successful completion transaction.
- [ ] Replace `_sync_milvus` best-effort calls and synchronous rebuild route logic with enqueue/status/retry use cases; migrate compatible old ontology sync artifacts without deleting history.
- [ ] Run projection, vector-dimension, vector-storage, ingest, project and workspace tests.

### Task 5: Truthful runtime capability health and lifecycle worker

**Files:**
- Create: `knowledge_service/services/runtime_capabilities.py`
- Create: `tests/service/test_runtime_capabilities.py`
- Modify: `knowledge_service/bootstrap.py`
- Modify: `knowledge_service/api/__init__.py`
- Modify: `knowledge_service/web/runtime-view.js`
- Modify: `tests/service/test_runtime_page_ui.py`

- [ ] Write failing matrix tests for real local encoder, demo encoder, Milvus configured/unavailable, collection readiness, and dimension mismatch with secret redaction.
- [ ] Implement `RuntimeCapabilities.snapshot()` returning the specified embedding/vector DTO and `ok|degraded` without hiding configured failures.
- [ ] Start/stop the projection worker from FastAPI lifespan, draining only to a bounded interrupt point.
- [ ] Update runtime UI to display configured/connected/collection/indexed/degraded and actionable retry state.
- [ ] Run health/API/runtime UI tests and probe the real `.env` process.

### Task 6: Frozen multi-source QA summaries and visible original text

**Files:**
- Create: `knowledge_service/services/answer_evidence.py`
- Create: `tests/service/test_answer_evidence.py`
- Modify: `knowledge_service/services/provenance.py`
- Modify: `knowledge_service/services/answers.py`
- Modify: `knowledge_service/repository/provenance_store.py`
- Modify: `knowledge_service/api/provenance.py`
- Modify: `knowledge_service/web/workbench.js`
- Modify: `tests/service/knowledge-chat-provenance.test.cjs`
- Modify: `tests/service/evidence-inspector-frozen-source.test.cjs`

- [ ] Write failing tests for chunk/entity/relation/manual/missing-span summaries, multiple sources ordered by frozen edge ordinal and span, deduplication, no current-version fallback, and explicit incomplete reasons.
- [ ] Implement `AnswerEvidence.summaries()` using only the answer's frozen `offered` branch and return citation, record_version_id, retrieval channel/ranker version, evidence text, sources, source_status, and provenance_ref.
- [ ] Add failing Node tests asserting the first three evidence entries are visible without a click and remaining evidence entries expand. Within each evidence entry assert the first complete source is visible, “另有 N 个来源” expands that entry's additional sources independently, citations open provenance, and legacy answers stay explicitly legacy.
- [ ] Render each evidence entry with citation and evidence text plus its first complete source's document title, up to 240 source characters, version/channel, and independent “另有 N 个来源”; add a separate control for evidence entries beyond the first three, show retrieval degradation, and remove the default-hidden evidence panel behavior.
- [ ] Run provenance Python tests and all QA-related Node tests.

### Task 7: Semantica ontology quality gate

**Files:**
- Create: `knowledge_service/integrations/semantica_ontology.py`
- Create: `tests/service/test_semantica_ontology.py`
- Modify: `knowledge_service/services/ontology_drafts_review.py`
- Modify: `knowledge_service/services/ontology_drafts_readmodels.py`
- Modify: `tests/service/test_ontology_drafts.py`
- Modify: `tests/service/test_ontology_draft_api.py`

- [ ] Write adapter tests that convert Semantica 0.7.0 `ontology_quality_check` results into stable metrics/issues/threshold/CQ DTOs and turn import/runtime failure into `quality_engine_unavailable`.
- [ ] Verify RED, then implement the adapter without leaking Semantica objects across the integration boundary.
- [ ] Write publication tests for error blocking, warning acknowledgement, Semantica-version/fingerprint drift, and preservation of existing SHACL/domain/range/DAG gates.
- [ ] Integrate quality report into preflight/readiness/publish and persist the stable report and version in validation artifacts.
- [ ] Run ontology draft, SHACL, ontology version, and Semantica tests.

### Task 8: Auditable Ontology RAG slices

**Files:**
- Create: `knowledge_service/services/ontology_slice.py`
- Create: `tests/service/test_ontology_slice.py`
- Modify: `knowledge_service/services/service.py`
- Modify: `knowledge_service/integrations/semantica_adapter.py`
- Modify: `knowledge_service/repository/ingest_store.py`
- Modify: `tests/service/test_document_upload_api.py`
- Modify: `tests/service/test_typed_reviews.py`

- [ ] Write deterministic selector tests for lexical/embedding hits, ancestors/children, object-property domain/range, data-property constraints, configured root fallback, stable ordering/version, and no cross-ontology leakage.
- [ ] Implement `OntologySliceSelector.select_terms()` as a service using the published ontology vocabulary plus injected encoder; return term IRI, surface hit, reason, ontology id, selector version.
- [ ] Write ingest tests proving controlled extraction receives only the slice, persists it in the ingest run, records explicit full-ontology degraded fallback, and never silently switches to open discovery.
- [ ] Integrate the selector before `SemanticaExtractor.extract()` and expose slice metadata in ingest read models.
- [ ] Run extraction, upload, typed-review, ontology query-expansion, and ingest-run tests.

### Task 9: Generated operator manual and UI evidence

**Files:**
- Modify: `scripts/build_knowledge_service_manual.py`
- Modify: `scripts/capture_knowledge_service_manual.py`
- Modify: `docs/assets/manual-v110/*`
- Regenerate: `docs/知识图谱操作手册.html`
- Modify: `tests/service/test_docs_contract.py`
- Modify: `tests/service/test_generator_contract.py`

- [ ] Write failing documentation contract tests for visible answer sources, projection status/retry, Semantica quality/warning flow, and recorded ontology slice terminology.
- [ ] Update the manual builder first; never hand-edit only the generated HTML.
- [ ] Start an isolated PostgreSQL test database/project, seed only disposable manual data, and capture real screenshots of QA, runtime/project status, and ontology publication.
- [ ] Regenerate the self-contained HTML and run docs contract, generator, link/image-dimension, font-floor, and browser render checks.

### Task 10: Full verification and delivery

**Files:**
- Modify only defects exposed by verification, always with a new failing regression test first.

- [ ] Run `python -m pytest -q` to completion and record total/pass/fail/duration.
- [ ] Run `node --test tests/service/*.test.cjs` and record the exact summary.
- [ ] Restart the real service without `--demo` or environment overrides and verify `/api/health` reports local semantic encoder plus connected/ready Milvus with matching dimension.
- [ ] Run `python scripts/verify_all_chains.py --url http://127.0.0.1:8100`.
- [ ] Verify Compose/PostgreSQL/Milvus/etcd/MinIO health and execute one isolated end-to-end flow: quality check → publish → upload → ontology slice extraction → projection completion → QA visible source → provenance drawer.
- [ ] Run browser checks for QA, runtime/project, ontology review and `docs/知识图谱操作手册.html`.
- [ ] Run `git diff --check`, inspect `git status --short`, preserve `docs/2026-10-10-ontology-driven-dev调研.md`, and report any environment-only blocker without overstating partial verification.
