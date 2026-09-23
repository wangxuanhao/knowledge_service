# Unified Ontology Workbench Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one governed ontology workbench that unifies discovery and manual/Turtle modeling, supports multi-parent DAGs, human review, reversible retirement, atomic publishing, and end-to-end provenance without removing current capabilities.

**Architecture:** Add a dedicated SQLite-backed `OntologyDraftStore` and a deep `OntologyDrafts` service seam. All public ontology writes become commands against immutable operations and decisions; one publish transaction creates the ontology version, closes the draft, records provenance, and applies source-specific side effects. The existing single-page UI keeps its global visual language but replaces flat ontology maintenance with a lazy object/DAG/review workbench.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLite, RDFLib/OWL/SHACL, Semantica 0.6.7, vanilla JavaScript/CSS, pytest, Node test runner, Playwright.

---

## File map and boundaries

Create:

- `knowledge_service/repository/ontology_draft_store.py` — SQL persistence, CAS, immutable operations/decisions, export/delete helpers.
- `knowledge_service/services/ontology_drafts.py` — draft lifecycle, command normalization, risk, validation, review, rebase and publish orchestration.
- `knowledge_service/services/ontology_operations.py` — RDF operation compiler, OWL union handling, retirement invariants and canonical Turtle diff.
- `knowledge_service/api/ontology_drafts.py` — new write/read API models and routes only; no domain logic.
- `knowledge_service/web/ontology-workbench.js` — workbench state, lazy hierarchy/object/matrix/review rendering and interactions.
- `knowledge_service/web/ontology-workbench.css` — namespaced three-pane workbench styles using existing tokens.
- `tests/service/test_ontology_draft_repository.py` — migration, CAS, immutable history, export/delete and atomicity.
- `tests/service/test_ontology_operations.py` — RDF compiler, DAG, union, annotation, retirement and Turtle invariants.
- `tests/service/test_ontology_drafts.py` — lifecycle, decisions, risk, validation fingerprint, rebase and publish.
- `tests/service/test_ontology_draft_api.py` — API contracts, compatibility and hierarchy pagination.
- `tests/service/test_ontology_workbench_ui.py` — Playwright behavior for editing and fast review.

Modify:

- `knowledge_service/repository/core.py` — migrations 13/14, store composition, controlled ontology insert, project export/delete.
- `knowledge_service/repository/__init__.py` — export migrations used by upgrade tests.
- `knowledge_service/repository/provenance_store.py` — ontology activity/edge kinds and append helpers.
- `knowledge_service/services/ontology.py` — active/deprecated summaries and common hierarchy queries.
- `knowledge_service/services/formal_writes.py` — reject new formal facts that use deprecated classes/properties.
- `knowledge_service/services/ontology_discovery.py` — Semantica hierarchy adapter and discovery-to-draft conversion.
- `knowledge_service/services/ontology_changes.py` — candidate proposal compatibility adapter.
- `knowledge_service/services/legacy_import.py` — existing-project import creates a draft; trusted empty-project restore and full-governance import use explicit ordered paths.
- `knowledge_service/api/__init__.py` — install the new router.
- `knowledge_service/api/workspace.py` — old structured writes delegate to drafts.
- `knowledge_service/api/knowledge.py` — old Turtle POST delegates to Turtle diff draft.
- `knowledge_service/api/ontology_discovery.py` — discovery draft endpoints delegate while preserving responses.
- `knowledge_service/api/ontology_changes.py` — proposal endpoints delegate while preserving source linkage.
- `knowledge_service/api/projects.py` — trusted bootstrap calls the private insert primitive with provenance.
- `knowledge_service/web/index.html` — one “本体工作台” entry and versioned assets.
- `knowledge_service/web/workspace.js` — route current ontology screens into the unified workbench.
- `knowledge_service/web/candidate-mindmap.js` and `.css` — reuse the existing candidate graph inside the discovery stage instead of duplicating it.
- `tests/service/test_frontend_static_contract.py` — asset/order/no-duplicate-entry contract.
- `tests/service/test_ontology_maintenance.py`, `test_ontology_discovery.py`, `test_ontology_change_proposals.py` — compatibility behavior now returns drafts and publishes through governance.
- `tests/service/test_project_management.py`, `test_provenance_repository.py` — lifecycle/migration expectations.
- `docs/KNOWLEDGE_SERVICE.md` — operator-facing lifecycle and API notes.

Do not grow `repository/core.py`, `services/ontology.py`, or `web/workspace.js` with the new feature's full implementation. They remain composition/compatibility seams; new complexity lives in the focused files above.

## Environment and test convention

Every Python test command must clear the host-only TLS key log variable first on Windows:

```powershell
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest ...
```

This prevents the confirmed `OPENSSL_Uplink ... no OPENSSL_Applink` process exit. Semantica 0.6.7 is installed in the worktree environment with `uv pip install --no-deps semantica==0.6.7`.

### Task 1: Stabilize the existing verification baseline

**Files:**
- Modify: `knowledge_service/web/workbench.js`
- Modify: `tests/service/test_frontend_retrieval_flow.py`
- Modify: `knowledge_service/repository/core.py`
- Test: `tests/service/test_repository.py`

- [ ] **Step 1: Add focused failing tests for graph-state ownership and bitemporal history.**

Extend the existing four failing tests so they wait on the intended UI state rather than time alone, and add a repository assertion that `known_at=initial['recorded_at']` returns version 1 after a correction whose system timestamp is later.

- [ ] **Step 2: Reproduce the failures independently.**

Run:

```powershell
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest tests/service/test_frontend_retrieval_flow.py::test_search_updates_only_the_result_rail_and_ignores_graph_keys tests/service/test_frontend_retrieval_flow.py::test_project_and_scope_changes_clear_isolated_state tests/service/test_frontend_retrieval_flow.py::test_knowledge_chat_keyboard_ime_and_scope_change tests/service/test_repository.py::test_correction_replaces_interval_but_history_retains_original -vv
```

Expected: four reproducible failures matching the baseline report, not TLS process termination.

- [ ] **Step 3: Fix ownership/race behavior at the source.**

Ensure search does not call graph summary rendering, project/scope/time changes synchronously clear graph state before any optional reload, and hidden scope controls are changed through their owning panel/state function rather than a forced click. Normalize repository system-time comparisons so an exact stored `recorded_at` includes that version while later corrections remain excluded.

- [ ] **Step 4: Run the focused tests.**

Expected: 4 passed.

- [ ] **Step 5: Commit.**

```powershell
git add knowledge_service/web/workbench.js knowledge_service/repository/core.py tests/service/test_frontend_retrieval_flow.py tests/service/test_repository.py
git commit -m "fix: stabilize existing state and history baselines"
```

### Task 2: Add durable ontology draft persistence

**Files:**
- Create: `knowledge_service/repository/ontology_draft_store.py`
- Modify: `knowledge_service/repository/core.py`
- Modify: `knowledge_service/repository/__init__.py`
- Create: `tests/service/test_ontology_draft_repository.py`
- Modify: `tests/service/test_project_management.py`

- [ ] **Step 1: Write migration and repository contract tests.**

Assert migration 13 creates `ontology_drafts`, `ontology_operations`, `ontology_review_decisions`, and `ontology_publish_requests`; assert project FKs/cascades, nullable first-version base, unique idempotency key, and operation/decision immutability. Open two `Repository` instances against one database and prove only one `expected_revision=1` CAS update succeeds.

- [ ] **Step 2: Run the new repository tests and verify schema/method failures.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_draft_repository.py -vv`

Expected: FAIL because migration 13 and `OntologyDraftStore` do not exist.

- [ ] **Step 3: Implement migration 13.**

Use explicit columns for lifecycle/indexed fields and JSON for validated snapshots. Required constraints:

```sql
CREATE TABLE ontology_drafts (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  base_ontology_id TEXT REFERENCES ontologies(id),
  source_kind TEXT NOT NULL CHECK(source_kind IN ('manual','turtle','import','ai','discovery','candidate')),
  status TEXT NOT NULL CHECK(status IN ('editing','submitted','reviewed','published','closed','stale_base','stale_source')),
  revision INTEGER NOT NULL CHECK(revision >= 1),
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  source_context TEXT NOT NULL CHECK(json_valid(source_context)),
  validation_report TEXT,
  validation_fingerprint TEXT,
  published_ontology_id TEXT REFERENCES ontologies(id),
  legacy_artifact_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Operations and decisions are append-only; adjustment inserts a new operation with `supersedes_operation_id`. Publish requests store request hash plus result ontology id under `UNIQUE(project_id,draft_id,idempotency_key)`.

- [ ] **Step 4: Implement `OntologyDraftStore`.**

Expose `create`, `get`, `list`, `append_operations`, `compare_and_set`, `append_decisions`, `effective_operations`, `export`, and `delete_counts`. All writes use the owning Repository transaction; CAS is one SQL update with `WHERE revision=?`.

- [ ] **Step 5: Compose the store and project lifecycle.**

Instantiate `self._ontology_drafts`; include drafts/operations/decisions in full export, explicit project deletion counts/order, and migration exports. Mark light projection exports with `governance_history_included=false` if applicable.

- [ ] **Step 6: Implement and test ordered full-governance restore.**

Restore in dependency order: project → immutable ontology versions → drafts → operations → decisions → publish requests → provenance activities/edges. Verify stable IDs, base/published ontology references and decision/operation fingerprints survive export/import. Reject partial “full” backups that declare `governance_history_included=true` but omit a required section.

- [ ] **Step 7: Run repository, migration, project deletion and export/import tests.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_draft_repository.py tests/service/test_project_management.py -vv`

Expected: all tests pass, including dual-connection CAS and governance export/import.

- [ ] **Step 8: Commit.**

```powershell
git add knowledge_service/repository tests/service/test_ontology_draft_repository.py tests/service/test_project_management.py
git commit -m "feat: persist governed ontology drafts"
```

### Task 3: Implement RDF operations and ontology invariants

**Files:**
- Create: `knowledge_service/services/ontology_operations.py`
- Modify: `knowledge_service/services/ontology.py`
- Create: `tests/service/test_ontology_operations.py`

- [ ] **Step 1: Write failing compiler tests.**

Cover 0..N parents, cycle/self/duplicate rejection, per-language annotation add/remove, class/object/datatype-property creation, datatype changes, and canonical domain/range OR sets: zero removes the constraint, one writes a direct IRI, more than one writes one sorted `owl:unionOf` list.

- [ ] **Step 2: Add retirement/restore invariant tests.**

Assert retirement adds `owl:deprecated true` without deleting declarations/labels/edges; `dcterms:isReplacedBy` is structured; active subclass/domain/range/SHACL dependencies on deprecated terms produce the exact error/warning matrix. A restore command must require `source_ontology_id`, load that immutable version, and reconstruct exactly the user-selected annotations, parents, domain/range and datatype before removing the deprecation marker. Assert the preview shows the complete restored definition and its newly reactivated constraint impact.

- [ ] **Step 3: Add canonical Turtle diff tests.**

Assert isomorphic blank-node graphs yield zero operations; supported union/SHACL subgraphs group atomically; unsupported restrictions block; removing a published declaration becomes `retire_term` or an error; `advanced_rdf_patch` cannot change kind, deprecation, or replacement invariants.

- [ ] **Step 4: Run and confirm failures.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_operations.py -vv`

- [ ] **Step 5: Implement normalized operation data and compiler.**

Use stable SHA-256 fingerprints over canonical JSON. Keep all graph mutation in `apply_operations(base_turtle, operations)`. Resolve restore templates only from the requested immutable `source_ontology_id`; store the selected definition in the operation fingerprint so later source/version drift cannot change it. Add `active_only=True` to ontology summaries without hiding deprecated terms from version/history views.

- [ ] **Step 6: Implement graph validation and risk primitives.**

Return structured `{code,severity,message,operation_ids,term_iris}` issues and lock the complete action matrix in parameterized tests:

- low only: manual annotation add/remove and an unreferenced manual leaf `create_term`;
- medium minimum: `add_parent`, `add_domain`, `add_range`, and every discovery/AI/import/candidate semantic suggestion even when otherwise unused;
- high: `remove_parent`, `remove_domain`, `remove_range`, `set_datatype`, `retire_term`, `restore_term`, `advanced_rdf_patch`, any formal-record impact, descendants >50, constraint references >10, or pending candidates >20;
- any warning raises an otherwise-low operation to at least medium; no confidence value may lower the source minimum.

Add a negative batch-approval test for every medium/high action so a classification omission cannot silently make it batchable.

- [ ] **Step 7: Enforce deprecation in formal knowledge writes.**

Add tests to `tests/service/test_formal_writes.py` proving new entity, relation and attribute writes reject deprecated target types/properties by default while historical records bound to older ontology IDs remain readable and valid against their own version. Implement the check in `services/formal_writes.py` through `Ontology.is_active_term`, not through UI filtering.

- [ ] **Step 8: Run tests and commit.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_operations.py tests/service/test_formal_writes.py -vv`

Expected: all tests pass.

```powershell
git add knowledge_service/services/ontology.py knowledge_service/services/ontology_operations.py knowledge_service/services/formal_writes.py tests/service/test_ontology_operations.py tests/service/test_formal_writes.py
git commit -m "feat: compile governed ontology operations"
```

### Task 4: Build the OntologyDrafts lifecycle service

**Files:**
- Create: `knowledge_service/services/ontology_drafts.py`
- Create: `tests/service/test_ontology_drafts.py`

- [ ] **Step 1: Write lifecycle tests for create/command/submit.**

Test nullable-base first draft, command CAS, immutable supersession, editing/submitted transitions, source context, preview overlay, and close reason/actor. For `restore_term`, require `source_ontology_id` plus an explicit field/edge selection, reject missing or cross-project source versions, and preserve the resolved template in the immutable operation.

- [ ] **Step 2: Write decision and validation tests.**

Test approve/reject/request_changes; max 100 batch only for no-warning low risk; every `reject` and `request_changes` requires a non-empty reason regardless of risk, and every high-risk `approve` requires a non-empty reason. Medium-risk approval may omit a reason only when it has no warning requiring acknowledgement. Validation fingerprint binds base/source versions, operation fingerprints, report and rule version; changed/unacknowledged warnings reject decisions/publish.

- [ ] **Step 3: Write stale/rebase tests.**

Test `stale_base` after another ontology publishes, `stale_source` after a document revision changes, rebase only to latest, clean/conflict/no-op classification, and retention of decisions only when operation fingerprints are unchanged.

- [ ] **Step 4: Run and verify service tests fail.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_drafts.py -vv`

- [ ] **Step 5: Implement the deep service interface.**

Implement `create`, `command`, `submit`, `decide`, `validate`, `rebase`, `close`, `publish`, plus paginated `roots`, `children`, `search`, `neighborhood`, and `matrix`. The service owns state transitions and never trusts client-provided Turtle or risk.

- [ ] **Step 6: Verify and commit.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_drafts.py -vv`

Expected: all tests pass.

```powershell
git add knowledge_service/services/ontology_drafts.py tests/service/test_ontology_drafts.py
git commit -m "feat: add ontology draft lifecycle"
```

### Task 5: Extend ontology provenance and atomic publish

**Files:**
- Modify: `knowledge_service/repository/core.py`
- Modify: `knowledge_service/repository/provenance_store.py`
- Modify: `knowledge_service/repository/ontology_draft_store.py`
- Modify: `knowledge_service/services/ontology_drafts.py`
- Modify: `tests/service/test_provenance_repository.py`
- Modify: `tests/service/test_ontology_draft_repository.py`
- Modify: `tests/service/test_ontology_drafts.py`

- [ ] **Step 1: Write migration 14 preservation tests.**

Seed migration-12 retrieval/answer rows, including existing `decided-by` edges, run migration 14, and assert rows survive while activity kinds `ontology_draft`/`ontology_publish` and relations `published-from`, `contains-operation`, `decided-by`, `proposed-by`, `supported-by`, `based-on` are accepted. Force migration failure and assert rollback.

- [ ] **Step 2: Write atomic publish and idempotency tests.**

Inject failure after ontology insert and assert no ontology/draft/provenance partial state. Retry same idempotency key/payload returns the same ontology; same key/different request hash returns conflict.

- [ ] **Step 3: Implement migration 14 by table rebuild.**

Copy existing rows unchanged into expanded CHECK-constrained tables inside the migration transaction, recreate indexes/FKs, validate counts, then replace old tables.

- [ ] **Step 4: Implement one repository publish transaction.**

Recheck base/source/revision and validation fingerprint inside the transaction; insert the ontology through a private `_insert_ontology_version`; persist publish request, draft terminal state, ontology activities and stable refs (`ontology-draft:`, `ontology-operation:`, `ontology-decision:`, `ontology-version:`). For Milvus-backed source changes, persist a fingerprinted pending sync artifact/job in this same transaction; execute it only after commit so a process crash cannot lose the retry record.

- [ ] **Step 5: Restrict direct ontology insertion.**

Public routes/services must no longer call `save_ontology`. Keep only explicit `bootstrap_ontology`, `restore_empty_project_snapshot`, and unified publish wrappers around the private primitive, each with provenance.

- [ ] **Step 6: Run tests and commit.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_provenance_repository.py tests/service/test_ontology_draft_repository.py tests/service/test_ontology_drafts.py -vv`

Expected: all tests pass.

```powershell
git add knowledge_service/repository knowledge_service/services/ontology_drafts.py tests/service/test_provenance_repository.py tests/service/test_ontology_draft_repository.py tests/service/test_ontology_drafts.py
git commit -m "feat: publish ontology drafts atomically with provenance"
```

### Task 6: Expose the unified API and lazy DAG reads

**Files:**
- Create: `knowledge_service/api/ontology_drafts.py`
- Modify: `knowledge_service/api/__init__.py`
- Create: `tests/service/test_ontology_draft_api.py`

- [ ] **Step 1: Write API contract tests.**

Cover create/list/get, commands, submit, decisions, validate, rebase, close, publish and exact 409/422 conflict bodies. Require `expected_revision`; require ontology/validation/idempotency fields at the designed boundaries.

- [ ] **Step 2: Write hierarchy read tests.**

Cover cursor pagination, draft overlay and HTTP IRI values containing `/` and `#` passed through `?iri=` for children/neighborhood. Assert repeated DAG reference rows share canonical IRI and expose `other_parent_count`.

- [ ] **Step 3: Run and confirm route failures.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_draft_api.py -vv`

- [ ] **Step 4: Implement thin Pydantic models/routes.**

Map domain conflicts to stable machine codes: `revision_conflict`, `stale_base`, `stale_source`, `validation_changed`, `validation_failed`, `batch_not_allowed`. Do not duplicate lifecycle logic in handlers.

- [ ] **Step 5: Install router, run tests and commit.**

```powershell
git add knowledge_service/api tests/service/test_ontology_draft_api.py
git commit -m "feat: expose ontology draft and hierarchy APIs"
```

### Task 7: Adapt structured, Turtle, discovery and candidate write paths

**Files:**
- Modify: `knowledge_service/api/workspace.py`
- Modify: `knowledge_service/api/knowledge.py`
- Modify: `knowledge_service/api/ontology_discovery.py`
- Modify: `knowledge_service/api/ontology_changes.py`
- Modify: `knowledge_service/services/ontology_discovery.py`
- Modify: `knowledge_service/services/ontology_changes.py`
- Modify: `knowledge_service/services/legacy_import.py`
- Modify: `knowledge_service/api/projects.py`
- Modify: `tests/service/test_ontology_maintenance.py`
- Modify: `tests/service/test_ontology_discovery.py`
- Modify: `tests/service/test_ontology_change_proposals.py`

- [ ] **Step 1: Rewrite compatibility tests first.**

Old structured/Turtle writes must create or update a draft and return deprecation metadata, never increase ontology versions. Project creation remains trusted bootstrap; import into an existing project creates an import draft.

- [ ] **Step 2: Pin the Semantica 0.6.7 hierarchy adapter test.**

Use a fixed fixture with `classes[].name` and optional singular `parent`; accept `subClassOf` only as compatibility, missing/unknown parent as root, and never invent a parent. Generated suggestions are minimum medium risk.

- [ ] **Step 3: Preserve discovery/candidate publish side effects in tests.**

Assert document/candidate expected versions, mapped/skipped/materialized updates, candidate revalidation, transaction rollback and post-commit fingerprinted Milvus sync artifact behavior.

- [ ] **Step 4: Implement compatibility adapters.**

Convert structured requests into normalized commands, Turtle into canonical diff commands, discovery output into evidence-linked operations, and candidate proposals into source-linked operations. Keep legacy response fields where existing callers require them, but add `draft_id`, `revision`, and deprecation headers/body.

- [ ] **Step 5: Implement source-specific publish hooks.**

Run database side effects inside the unified publish transaction; enqueue Milvus sync after commit. Source changes must set `stale_source`, not partially publish.

- [ ] **Step 6: Run compatibility suites and commit.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_maintenance.py tests/service/test_ontology_discovery.py tests/service/test_ontology_change_proposals.py -vv`

Expected: all tests pass with legacy response compatibility and no direct publish bypass.

```powershell
git add knowledge_service/api knowledge_service/services tests/service/test_ontology_maintenance.py tests/service/test_ontology_discovery.py tests/service/test_ontology_change_proposals.py
git commit -m "feat: route ontology writes through unified governance"
```

### Task 8: Build the project-aligned workbench shell

**Required skills:** `@frontend-design`

**Files:**
- Create: `knowledge_service/web/ontology-workbench.css`
- Create: `knowledge_service/web/ontology-workbench.js`
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/workspace.js`
- Modify: `knowledge_service/web/candidate-mindmap.js`
- Modify: `knowledge_service/web/candidate-mindmap.css`
- Modify: `tests/service/test_frontend_static_contract.py`
- Modify: `tests/test_web_ui_contract.py`

- [ ] **Step 1: Write static contract failures.**

Assert one visible “本体工作台” entry, versioned CSS/JS loaded after shared tokens, selectors namespaced under `.ontology-workbench`, no inline event handlers, no second formal `ontology-manager.html` entry, and the five lifecycle stage controls. Assert the discovery stage still exposes statistics, clustering, candidate mind map, source/confidence filters and evidence inspection hooks.

- [ ] **Step 2: Implement semantic shell markup/rendering.**

Use the existing dark-green navigation, light canvas, green primary action and amber review accents. Create three responsive panes: object/change library, central object/hierarchy/matrix view, and inspector/evidence/review. Preserve keyboard focus and `prefers-reduced-motion`.

- [ ] **Step 3: Implement shared state/loading/error primitives.**

State contains project, ontology/draft ids, revision, selected canonical IRI, display path, mode, filters and cursor maps. Render all external strings with DOM text nodes/escaping.

- [ ] **Step 4: Embed the existing discovery experience as stage one.**

Move/reuse the current discovery statistics, clustering, candidate mind map, candidate filtering, confidence/source evidence and “generate cumulative draft” controls inside the workbench shell. Preserve existing API calls and test hooks during the transition; generating a draft now selects it and advances to design rather than opening a parallel ontology page.

- [ ] **Step 5: Run static tests and commit.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_frontend_static_contract.py tests/test_web_ui_contract.py -vv`

Expected: all tests pass.

```powershell
git add knowledge_service/web tests/service/test_frontend_static_contract.py tests/test_web_ui_contract.py
git commit -m "feat: add unified ontology workbench shell"
```

### Task 9: Implement object editing, hierarchy and matrix UX

**Files:**
- Modify: `knowledge_service/web/ontology-workbench.js`
- Modify: `knowledge_service/web/ontology-workbench.css`
- Create: `tests/service/test_ontology_workbench_ui.py`

- [ ] **Step 1: Write Playwright tests for scale and multi-parent behavior.**

Test lazy roots/children loading, virtualized rows, search, repeated IRI reference rows with shared selection, display path plus “N other parents”, draft overlay and no eager full-summary tree build.

Also cover discovery-stage regression: statistics/clusters render, candidate filters narrow the mind map and queue, selecting a candidate reveals source evidence/confidence, and generating a discovery draft carries the same candidate/document refs into design.

- [ ] **Step 2: Write editor behavior tests.**

Test add class/relation/attribute, multiple parent chips, independent domain/range OR chips, datatype, multilingual annotations, retirement dependency preview, restore source-version selection and request-changes adjustment.

- [ ] **Step 3: Implement object/hierarchy/matrix modes.**

Object mode is default/authoritative. Hierarchy uses paginated expansion and reference rows; matrix pages relation/property constraints. Forms only send commands with current revision and keep unsent input after network errors.

- [ ] **Step 4: Verify responsive and keyboard behavior.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_workbench_ui.py -vv`

- [ ] **Step 5: Commit.**

```powershell
git add knowledge_service/web/ontology-workbench.js knowledge_service/web/ontology-workbench.css tests/service/test_ontology_workbench_ui.py
git commit -m "feat: add ontology DAG editing experience"
```

### Task 10: Implement fast review, validation and version governance UI

**Files:**
- Modify: `knowledge_service/web/ontology-workbench.js`
- Modify: `knowledge_service/web/ontology-workbench.css`
- Modify: `tests/service/test_ontology_workbench_ui.py`
- Modify: `tests/service/test_ontology_draft_review_ui.py`

- [ ] **Step 1: Write review workflow tests.**

Cover filters/grouping, before/after/context/evidence panes, A approve, E adjust, R reject, J/K navigation, autosaved decisions, mandatory reasons for every rejection and adjustment, mandatory reasons for high-risk approval, warning acknowledgements and stale recovery. Verify the UI cannot submit these decisions with missing reasons and the API still rejects a crafted bypass.

- [ ] **Step 2: Write batch safety tests.**

Only visible-filter, no-warning low-risk operations may batch approve; max 100; medium/high/retire/restore/advanced patch never expose or accept batch approve. Show validation fingerprint changes and force re-review.

- [ ] **Step 3: Implement review, validation and publish stages.**

Render change-driven queues rather than whole-ontology approval. Validation shows graph/prospective/historical sections. Publish sends idempotency key and confirmed warning codes, then links to the created immutable version/provenance chain.

- [ ] **Step 4: Integrate existing version, diff, Turtle history and SPARQL tools.**

Keep these capabilities reachable under “版本治理”; Turtle editing creates a draft rather than publishing directly.

- [ ] **Step 5: Run UI suites and commit.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_workbench_ui.py tests/service/test_ontology_draft_review_ui.py -vv`

Expected: all tests pass.

```powershell
git add knowledge_service/web tests/service/test_ontology_workbench_ui.py tests/service/test_ontology_draft_review_ui.py
git commit -m "feat: add safe ontology review and publish UI"
```

### Task 11: Complete migration compatibility and operator documentation

**Files:**
- Modify: `knowledge_service/services/ontology_drafts.py`
- Modify: `knowledge_service/repository/ontology_draft_store.py`
- Modify: `tests/service/test_ontology_drafts.py`
- Modify: `docs/KNOWLEDGE_SERVICE.md`

- [ ] **Step 1: Write legacy artifact conversion tests.**

Published/rejected old artifacts stay read-only. Pending artifacts lazily convert once with `legacy_artifact_id`; original remains untouched; conversion failure returns `needs_migration_review` and cannot publish.

- [ ] **Step 2: Implement transactional lazy conversion.**

Map discovery and candidate artifact fields to normalized source context/operations without inventing historical decisions.

- [ ] **Step 3: Document lifecycle and compatibility.**

Describe roots/multi-parent semantics, retirement vs deletion, draft states, review shortcuts, API concurrency fields, warning acknowledgements, bootstrap exception and Semantica installation.

- [ ] **Step 4: Run focused tests and commit.**

Run: `Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue; .\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_drafts.py tests/service/test_project_management.py -vv`

Expected: all legacy conversion and governance export/import tests pass.

```powershell
git add knowledge_service/services/ontology_drafts.py knowledge_service/repository/ontology_draft_store.py tests/service/test_ontology_drafts.py docs/KNOWLEDGE_SERVICE.md
git commit -m "docs: complete ontology governance migration"
```

### Task 12: Full verification and final review

**Required skills:** `@verification-before-completion`, then `@requesting-code-review`.

**Files:**
- Modify only files required by verified failures; no opportunistic refactors.

- [ ] **Step 1: Run focused backend suites.**

```powershell
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest tests/service/test_ontology_draft_repository.py tests/service/test_ontology_operations.py tests/service/test_ontology_drafts.py tests/service/test_ontology_draft_api.py tests/service/test_ontology_maintenance.py tests/service/test_ontology_discovery.py tests/service/test_ontology_change_proposals.py tests/service/test_provenance_repository.py tests/service/test_formal_writes.py tests/service/test_project_management.py -q
```

Expected: all pass.

- [ ] **Step 2: Run frontend contracts and browser tests.**

```powershell
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest tests/service/test_frontend_static_contract.py tests/test_web_ui_contract.py tests/service/test_ontology_workbench_ui.py tests/service/test_ontology_draft_review_ui.py tests/service/test_frontend_retrieval_flow.py -q
```

Expected: all pass.

- [ ] **Step 3: Run JavaScript unit tests.**

```powershell
node --test tests/service/*.test.cjs
```

Expected: all pass.

- [ ] **Step 4: Run the complete clean-environment suite.**

```powershell
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: no failures; skipped tests must be explained by optional integrations only.

- [ ] **Step 5: Run syntax/diff checks and inspect the page.**

```powershell
Remove-Item Env:SSLKEYLOGFILE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m compileall -q knowledge_service
git diff --check
git status --short
```

Launch the service, inspect the workbench at desktop and narrow widths, and exercise create → submit → review → validate → publish → provenance once with a multi-parent class.

- [ ] **Step 6: Request code review and resolve only evidence-backed issues.**

Review against `docs/superpowers/specs/2026-09-23-unified-ontology-workbench-design.md`, rerun affected suites, then rerun the full suite.

- [ ] **Step 7: Commit final verified adjustments.**

```powershell
git add -A
git commit -m "test: verify unified ontology workbench"
```

## Completion evidence

The implementation is complete only when:

- every public ontology write creates/updates a draft;
- first-version bootstrap is the only normal null-base internal exception;
- published terms have no physical deletion path and retirement is reversible;
- multi-parent RDF survives create/edit/review/publish/read round trips;
- low-risk batch and high-risk individual review are enforced server-side;
- discovery/candidate side effects and Milvus retry semantics remain intact;
- version → draft → operation → decision → source provenance is queryable;
- large hierarchy reads are paginated and the browser does not render the full graph eagerly;
- all focused and full test commands above pass in the clean environment.
