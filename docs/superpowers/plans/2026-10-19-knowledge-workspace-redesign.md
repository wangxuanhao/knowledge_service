# Knowledge Workspace Redesign Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the bounded tasks below. All workers share this isolated checkout; only the coordinator runs PostgreSQL pytest sessions. Steps use checkbox syntax for tracking. Do not commit unrelated work or overwrite another worker's files.

**Goal:** Deliver the approved usable knowledge workspace, retain existing capabilities, repair verified contract gaps, add real Local/OAG and ablation flows and a 150,000-entity graph mode, then write a new explanatory manual covering the complete shipped product.

**Architecture:** Preserve native JS/CSS and the existing API → services → PostgreSQL layering. Reorganize and reuse working modules; add focused modules for scoped UI context, graph sessions/rendering, and reproducible evaluations. Published ontology identities, record revisions and source evidence stay authoritative in existing services; derived graph/vector state never replaces them.

**Tech Stack:** Python 3.12 (`D:/anaconda/envs/llm_model/python.exe`), FastAPI, PostgreSQL, existing Semantica adapter/RDFLib/SHACL, native browser JS/CSS and WebGL, Node 26 contract tests, Playwright Edge.

**Approved specifications:**
- `docs/superpowers/specs/2026-10-16-project-workflow-and-contextual-filter-design.md`
- `docs/superpowers/specs/2026-10-19-project-and-people-management-design.md`
- `docs/superpowers/specs/2026-10-19-search-graph-scale-and-capability-acceptance-design.md`
- `docs/research/2026-10-19-full-capability-ui-parity.md` is the preservation checklist.

**Execution boundary:** Community Global/DRIFT and per-project membership ACL remain explicitly separate future phases as specified; do not expose fake executable buttons for them. Current system users/roles remain fully supported. Everything implemented in this change must have real services, usable UI and honest failure states. Never call the 150k requirement complete without loading/drawing that many actual entities in the capacity check.

**User update (2026-10-19):** Docker is now running; architecture and storage optimization are authorized. Milvus is the only vector storage/index/retrieval backend. Semantic/hybrid requests fail explicitly on unavailable or inconsistent Milvus/encoder/index state; no local vector or automatic keyword fallback. Explicit keyword retrieval remains a separate user-selected capability. This update overrides earlier fallback clauses in specs and this plan. Evaluate storage/query optimizations against real PostgreSQL/Milvus services without touching business records.

## Task 0: Isolation, baseline and capability ledger

**Files:** this plan; new `docs/research/2026-10-19-redesign-acceptance.md`.

- [x] Create branch `codex/knowledge-workspace-redesign` at `724c10d` in `C:/Users/Thinkpad/.codex/worktrees/knowledge-workspace-redesign`; copy approved specs and ignored local environment without printing secrets. Preserve the original checkout.
- [x] Run baseline Node suite with `D:/nodejs/node.exe --test tests/service/*.test.cjs` and one isolated `python -m pytest -q`; record real counts and environmental limitations.
- [x] Record existing → new entry → API/guard → verification for every row of the preservation checklist; update continuously, not from screenshot counts.
- [x] Review this plan against the three specs before implementation. User has already authorized execution; no further approval handoff is required.

## Task 1: Navigation, page-owned context, project and personnel centers

**Files:** modify `knowledge_service/web/index.html`, `app.js`, `menu-hierarchy.js`, `runtime-view.js`, `workspace.js`, `workspace.css`, `user-admin.js`; create focused workspace-context/management modules as needed. Tests in new `tests/service/workspace-redesign.test.cjs` and existing frontend suites.

- [x] Write behavioral tests for per-project/per-page scope, hidden-filter exclusion, global management routing, project quick switch, and current role visibility. Watch focused tests fail.
- [x] Implement stable groups (使用知识/管理资料/运行与质量) plus fixed 项目管理/人员管理. Reuse existing functional tab modules and expose their previous capabilities, rather than embedding the standalone mockup.
- [x] Move content Metadata to a closed-by-default rail only on sources/QA/search/records. Keep applied chips, advanced AND/OR, temporal/history fields and page-specific drafts. Explicit actions alone transfer scope. Scope changes preserve chat and reject stale responses.
- [x] Project center uses existing APIs; creation explicitly sends `{name, ontology_mode:'discovery', use_default_ontology:false}`. Settings rename, deletion and projection cleanup status retain actual service semantics. Handle deleting the current/last project.
- [x] Personnel center reuses account CRUD, display name, role/status, reset and delete API. Respect admin/superadmin/self protection; use personal account path for own password. Show system role scope, without inventing project-member permissions.
- [x] Update resource cache versions and affected legacy contracts. Verify meaningful UI behavior, not only source strings.

## Task 2: Ontology editing and existing governance completeness

**Files:** `knowledge_service/web/ontology-model.js`, `ontology-model-canvas.js`, `ontology-model-panel.js`, associated CSS and focused new modules; `web/records-view.js`, `web/workbench.js`, `models.py`, `api/knowledge.py`, `api/parity.py`, `services/governance.py`. Tests in focused existing suites and new contract/regression cases.

- [x] First reproduce restore_term missing `source_ontology_id/selected_fields`, and merge/review `attribute_winners` bridge gaps with failing tests; repair without changing service authority or concurrency guards.
- [x] Enhance real ontology view with searchable class/relation/attribute catalog, graph/type-card inspector and source/RDF access. Retain hierarchy view and unsupported RDF expressions.
- [x] Preserve manual insert/new class/child/relation/property, multiple parents and optional/multiple domain/range. Keep two-click edges and parent discovery with duplicate/cycle/baseline checks.
- [x] Keep published snapshots read-only, explicit draft creation/selection, continued modeling from existing parsed sources, validations, item/safe review, publish, version/history/diff, retire/restore and controlled reclassification/undo. Make all existing operations reachable; do not replace with explanatory toasts.
- [x] Add unified history-preview rejection in `ensureDraft/sendCommand` and inspector write controls, plus `ensureDraft` for the first relation connection from a published version. The immediate continued-discovery action uses the existing truthful project-candidate/current-published-baseline service. Precise document-revision selection, logical source new-revision/withdrawal, candidate manifests/mapping and cross-draft merge remain the separately specified second-phase service extension; do not expose unsupported submit controls or claim them delivered. Preserve existing document revisions/evidence and supported candidate actions.
- [x] Complete entity/relationship/independent attribute CRUD, aliases, duplicate groups, fusion conflict choices, review and undo wiring through the existing real record editor/history.
- [x] Validate selected controls and error/revision/baseline behavior, then run targeted frontend and Python regressions via coordinator.

## Task 3: Retrieval correctness and real Local/OAG evidence

**Files:** `services/retrieval.py`, `retrieval_context.py`, `answers.py` and focused helpers; existing native search adapter only where necessary. Tests in `test_hybrid_retrieval.py`, `test_explorer.py`, plus `test_retrieval_channel_parity.py`.

- [x] Write failing tests for per-kind top-k budgets, native/independent-keyword source+adjacent context parity and nonempty qualified graph evidence in normal grouped QA.
- [x] Search independent entity/relation/chunk channels with explicit budgets in Milvus. Return actual strategy/unavailable diagnostics; preserve evidence eligibility, project scope and time. Remove production local-vector fallback; explicit keyword retrieval stays independent, never an automatic semantic substitute.
- [x] Build Local/OAG context from retrieved entity anchors, qualified relations/neighbors and source chunks; include graph facts/paths in normal QA context and trace without manufacturing logical proof. Bound hops/cost and prevent out-of-scope leaks.
- [x] Keep full-corpus lookup and large entity selectors server-side/paginated; avoid matching only a previously loaded prefix. Coordinate any repository paging changes with Task 4.
- [x] Run deterministic source/metadata/history/empty/no-model cases and true native adapter tests where services are available. Record external-runtime limits separately.

## Task 4: Full graph data model and 150k WebGL mode

**Files:** new `repository/scoped_reads.py`, `services/graph_sessions.py`, `api/graph_sessions.py`, `web/full-graph.js`, `web/full-graph-worker.js`, `web/full-graph.css`; minimal app/graph integration. New `test_graph_sessions.py`, browser/Node checks, `scripts/verify_full_graph_capacity.py`.

- [x] Define and test manifest + stable cursor API: fixed project/scope/watermark, entity and relation counts, page size, node/edge schema, expiry/invalidation and errors. Reject arbitrary/other-session cursors and never produce dangling edges.
- [x] Use indexed PostgreSQL filtering and batched compact rows, no per-node roundtrips or `Explorer.scoped()` full-record preload for each page. Preserve temporal/metadata/type/predicate qualification; SQL belongs in repository.
- [x] Implement actual WebGL point/edge buffers, worker placement/decoding, pan/zoom/pick, progressive counts and cancellation; labels are limited independently of actual loaded entity count. Keep local graph mode and shared entity/edge detail/editing.
- [x] Manifest/page totals reconcile without silent sampling/truncation. GPU failure/context loss/stream failure show resumable or explicit incomplete states; never silently call a local fallback the full graph.
- [x] Add explicit `max_nodes/max_edges` and truthful truncation metadata to bounded local exploration; truncation is shown as incomplete, never “no path in whole corpus”. The shipped local view supports raising the budget and requesting again or changing seeds; it does not implement a signed continuation cursor.
- [x] Run isolated deterministic 150,000-entity and representative edge fixture. Verify all entities loaded in renderer data, drawn, exact lookup/detail reachability and bounded responsiveness; record hardware, edge count, timings, memory and real limits. Run a PostgreSQL-backed paging scale check separately; browser fixture alone is not backend evidence.
- [ ] Capacity report includes 1k/10k/50k/150k entity gradients, 300k/1m edge cases, complete load time, frame interval and pick P95, predeclared browser/server memory budgets, a 30-minute stability run, and 150k corpus exact retrieval plus shared editor revision/permission/conflict regression. Targets and actual measurements remain separate. Record unsupported hardware or failed targets explicitly; do not silently lower the requirement.

## Task 5: Real retrieval/QA ablation experiments

**Files:** new `services/retrieval_experiments.py`, `api/retrieval_experiments.py`, `web/retrieval-experiments.js/.css`, migration only if needed for durable runs. Existing extraction evaluation stays intact. Tests `test_retrieval_experiments.py` and frontend contracts.

- [x] Define fixed question set, expected evidence/answer, scope, input revisions and configured model; variants explicitly toggle ontology expansion, graph context and retrieval channel rather than hiding a graph view.
- [x] Persist the run manifest and results: ontology publication IDs, knowledge/source versions, available index generation/watermark (unknown stays unknown), model/encoder/prompt identities, channel/hop/token budgets and randomness configuration. Each variant records the actual omitted stages: no-ontology omits term expansion and ontology-assisted context/structural hints; no-graph skips neighbor/path collection; document-only omits structured entity/relation retrieval. Do not label the single `ontology_expansion` flag as all ontology assistance removed.
- [x] Implement real service execution, per-question results with actual hits/evidence/strategy/timing, cancellation and retry/export; no invented scores or model answers when model unavailable. Preserve fixed inputs across variants; detect changed source/knowledge watermarks.
- [x] Recheck current authentication/project scope and source withdrawal eligibility on execution, result read and export; deny or invalidate unavailable evidence instead of exposing a frozen formerly allowed payload. Resume/retry reuse the fixed manifest and fail explicitly if it cannot still be honored.
- [x] Integrate a separate 检索与问答消融 tab into quality evaluation, with configure/run/status/details/export and source/evidence drilldown.
- [x] Test input isolation, variant differences, failure/cancel behavior, score denominators and truthful no-model state.

## Task 6: Integrate sources/upload/tasks and final functional coverage

**Files:** existing ingest, source reader, task-review, projection UI modules as identified; cache versions in `index.html`.

- [x] Keep actual file capabilities/OCR/ZIP, batch and per-file options, progress/errors/retry/resume. Open vs controlled extraction only governs the submitted batch; both allow later modeling.
- [x] Source-centric list/details expose original text, extracted assertions, processing/version history and evidence navigation. Avoid relying on nonexistent top-level source_id for derived facts.
- [x] Keep index verification/repair/retry and runtime actions where valid, honest states for projections, knowledge backup/export/restore and extraction evaluation. Distinguish document rN / ontology vN+ID / fact rN / task / index generation.
- [x] Do not claim new source logical-identity/revision-upload/withdrawal invalidation or arbitrary server-side document filters from frontend work; these are second-phase service changes, while existing source listing/assertion/history remains supported. Existing index task dedup/verification/repair behavior must be reused and tested, and a missing dedup contract is recorded as a backend gap rather than simulated with disabled buttons.
- [x] Close every preservation-ledger row with actual new entry and behavior proof; mark future phases explicitly and never reclassify an old supported operation as future to avoid implementing it.

## Task 7: New full explanatory manual and acceptance

**Files:** new `docs/2026-10-19-知识平台完整操作手册.md`, `docs/知识平台完整操作手册.html`, supporting screenshots in `docs/manual-redesign/`; update README links and add `scripts/verify_redesign_manual.py`.

- [x] Write from shipped behavior: task/scenario → why/when → prerequisites/role → exact steps → expected result → failure/recovery → next step. Cover every functional group and old advanced capability, not just changed pages.
- [x] Include complete first-project walkthrough, document-only/controlled/open flows, continued ontology editing, candidate vs fact/term, parent discovery/insert/constraints/review/version/restore/migration, entity edits/fusion/disambiguation/undo, retrieval/OAG/graph/full graph/ablation, projects/users/password, tasks/indexes/backup and troubleshooting.
- [x] Explain version relationships with concrete examples; list currently available capabilities separately from explicitly deferred community/project-member work. Record reproducible capacity evidence and tested limits without promising universal hardware performance.
- [x] Capture real running application screenshots using isolated demo/test data, render the HTML manual and inspect layout/navigation/link correctness. Do not use mockup screenshots as proof of real features.
- [x] Run full single-session pytest and full Node suite, targeted browser flows at desktop/mobile, changed-API security checks and manual coverage/link checker. Record all actual results and unresolved limitations in the acceptance ledger.
- [x] Review final diff for spec coverage and code quality; integrate into the original checkout without overwriting preserved user docs or production data. Final response links runnable UI/manual and states actual verified results.

## Final verification boundary

Final regression evidence is recorded in `docs/research/2026-10-19-final-regression-validation.json`. The complete-load P95 target remains unmet (48.187s vs 30s), and one isolated large-corpus revision still takes 125.454s. These measurements are retained rather than marking the full capacity specification accepted. Final original-directory synchronization and app startup are recorded separately in the acceptance record.
