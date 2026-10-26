# Candidate Review Workspace Implementation Plan

> **For agentic workers:** Use subagent-driven-development for bounded implementation and review. Preserve all pre-existing local changes. Do not commit. Reload the local service only after checking that all persisted jobs, batches and batch items are idle; preserve its existing interpreter, working directory and non-demo mode.

**Goal:** Make candidate scope, processing outcomes and bulk confirmation understandable and reliable.

**Architecture:** Reuse the existing OntologyIntake controller and fingerprinted preview/confirmation APIs. Add genuine source filters at the inventory boundary, bulk operations within the loaded list, and a responsive master/detail presentation using the existing shared dialog manager.

**Tech Stack:** Native JavaScript/CSS, FastAPI/Python, PostgreSQL, Node test, Python Playwright with system Edge.

---

### Task 1: Source scope

Files: `knowledge_service/api/ontology_discovery.py`, `knowledge_service/services/ontology_intake.py`, `knowledge_service/repository/page_reads.py` and narrowly required provenance readers; `tests/service/test_ontology_intake.py`.

- [x] Trace run/batch provenance and repository APIs before choosing filters. Add tests where a requested batch/task must not expose another task's candidates or a reprocessed document's replacement candidates. Missing or stale provenance must fail closed with a readable scope note.
- [x] Add optional inventory `batch_id` and `run_id` filters using verifiable stored provenance; keep existing document filter and pagination semantics. Return scope description/warnings if needed. Never treat document current version as an ingest run identity.
- [x] Run the new focused tests followed by intake tests in one pytest process with `D:/anaconda/envs/llm_model/python.exe -m pytest -q tests/service/test_ontology_intake.py` (coordinated by root, never simultaneous).

### Task 2: Selection controller

Files: `knowledge_service/web/ontology-intake.js`, `tests/service/ontology-intake.test.cjs`.

- [x] Add failing behavior tests for selecting many loaded identities, rejecting unseen IDs, clearing mappings when deselecting, kind-compatible suggested mapping without overwriting manual mappings, invalidating previews, source query parameters and same-scope reopen recovery.
- [x] Implement `selectMany(ids,on)`, `useSuggestedMappings(ids)`, explicit dependency selection helpers and scope query parameters. Selection is bounded to loaded items and the API 10,000 candidate limit. Only same-kind published mappings are allowed.
- [x] Preserve actor/project-scoped drafts in controller memory on close; refresh base context before preview and retain durable unknown-request recovery above ordinary selections.
- [x] Run `node --test tests/service/ontology-intake.test.cjs`.

### Task 3: Workspace and understandable goals

Files: `knowledge_service/web/ontology-intake.js`, `knowledge_service/web/ontology-intake.css`, `knowledge_service/web/ontology-model.css`, source/batch caller files, `knowledge_service/web/index.html`, related Node contracts.

- [x] Add failing UI behavior tests for clear target descriptions, explicit scope, bulk actions and source context callers.
- [x] Project opens a wide workspace; source-specific entries open a drawer. Use compact candidate list and selected-item detail, collapsed technical identity and evidence; show document/kind/type filter controls with loaded-list scope. Add summary rows and source/type bulk selectors without merging candidate facts.
- [x] Present two business goal choices and a separate existing/new draft selector for the schema path. Preserve the server preview and per-fact outcome check before enabling confirmation. Explain when knowledge becomes formal.
- [x] Add bulk selection, clear, compatible suggestions and explicit dependency supplements; keep preview/confirmation at a visible action area. Prevent backdrop dismissal and retain state on reopen.
- [x] Pass actual batch/run fields and readable document names from supported source entries, add task-record candidate entry where a real task ID exists. Fix toolbar summary margin/alignment. Bump affected `index.html` resource versions and locked assertions.

### Task 4: Verification and review

- [x] Run all Node contracts: `node --test tests/service/*.test.cjs`. Run selected Python intake/provenance tests once, without concurrent pytest sessions.
- [x] Add a deterministic `.benchmarks/candidate-review-check.py` using actual CSS/JS, system Edge and mocked APIs, verifying 1280/900/390px layouts, keyboard labels, bulk preview, close/reopen and no unintended writes. Inspect screenshots.
- [x] Review spec compliance, then code quality, and fix concrete findings. Run `git diff --check` and confirm current 8100 static resources return the bumped assets. Report the two goals, batch workflow and verified limitations succinctly in Chinese.


Validation: 462 Node contracts passed; 37 intake Python tests passed (one existing AnyIO warning); 58 frontend static contracts passed. Production CSS/JS acceptance passed at 1280/900/390px, including 100-item bulk selection, scoped pagination, mapping, read-only preview, close/reopen recovery and aligned 36px toolbar controls. Spec and code quality reviews passed after fixing delayed-request and cross-project recovery races.

## Final candidate lifecycle and merge verification — 2026-10-26

The default list contains candidates awaiting review, including explicitly reusable materialized entity dependencies. Candidates linked to an unpublished draft are available through the processing-status filter, with disabled selection and a link to the existing draft. Bulk selection skips these rows. Pinned candidate scopes and restored selections cannot add them again; the preview boundary also rejects stale selections. Existing server conflict and fingerprint checks remain authoritative.

- [x] Reproduce duplicate selection, pinned-selection and reopen-recovery failures with Node behavior tests.
- [x] Enforce the same lifecycle rule in the controller and list, group and dependency selectors. Keep materialized entity dependencies selectable for explicit reuse.
- [x] Add processing-status filtering, draft navigation and desktop/mobile filter layout; bump the affected JS/CSS cache versions.
- [x] Verify all 523 Node contracts, production browser fixtures at 1280/900/390 px and independent merge review. The browser fixtures cover draft-linked read-only rows, status filtering, exact pinned selection and draft navigation without live writes.
- [x] Verify the related Python intake, draft/API/review-plan and frontend static suites before the requested master integration: 186 passed, zero failed or skipped (one existing AnyIO deprecation warning), in one isolated pytest session.

The final review also identified that task and batch result cards can show current-document assertions after reprocessing. A visible scope note now precedes these details, distinguishing them from the saved task counts and snapshot. Both entry paths have a regression test.

Full-repository pytest was attempted. It exposes pre-existing test-contract mismatches in `test_answer_prompt_graph.py::AnswerPromptGraphTests::test_json_and_sse_provider_requests_contain_the_same_topology` (bare repository stub omits the required projection index adapter) and `test_api.py::test_filtered_ingestion_search_and_restart` (search invoked before explicitly building the required index). These tests and their QA/retrieval execution paths are unchanged from base commit `54b97ba`. An independent read-only review confirmed neither failure passes through this change's candidate or assisted-review paths. No unrelated production fallback, skipped test or permanent fixture workaround was added. The full suite must not be reported as passing.
