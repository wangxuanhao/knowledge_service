# New Service Feature Parity Implementation Plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development for bounded tasks and independent reviews. User selected new service as foundation, rebuilding previous features. No Git repository exists; do not fabricate commits/worktrees.

**Goal:** Restore legacy user capabilities on the new bitemporal service, preserving original disk projects, and run in conda llm_model with Semantica 0.6.8.

**Architecture:** Keep SQLite truth and existing API. Add separate import, governance, explorer and task modules. Rebuild rich workbench against these APIs, not an iframe/link to the old backend. Unknown legacy business times remain unknown. Compatibility means original names and data can be loaded, not that incompatible old writes silently mutate both stores.

**Tech Stack:** Python 3.12 conda llm_model, Semantica 0.6.8, FastAPI, SQLite, FAISS/BGE-M3, RDF/SHACL, vanilla JS and local ECharts.

## 1. Runtime and regression baseline
- [ ] Reproduce missing pyshacl and native torch/faiss import-order failure; install only needed new service dependency, use safe import ordering without KMP_DUPLICATE_LIB_OK.
- [ ] Verify Semantica version, NER/Relation/ContextGraph/DuplicateDetector imports and service/old UI tests in specified environment.
- [ ] Update start-service.ps1 to resolve conda llm_model, not a hardcoded obsolete machine path. No broad process termination.

## 2. Local project import — legacy_import.py + test_legacy_import.py
- [ ] Test inventory, traversal rejection, same-name idempotency, full node/edge/segment/source/alias preservation, unknown time, count reconciliation and no old-file mutation.
- [ ] Implement LegacyImporter(service, root).list_projects() and import_project(name) for data/projects; expose original names and imported project IDs. Preserve raw snapshot in project metadata; source evidence + existing aliases; map full old labels to declared types where possible, generate project legacy vocabulary for undeclared terms rather than discard records. Imported violations are reported, not silently dropped.
- [ ] Re-encode vectors with configured model (don't guess old model identity); write records atomically. HTTP job wraps potentially slow import; no paid LLM needed.
- [ ] Also inventory/import graph outputs from data/rule_demo_output with explicit root-kind, no arbitrary filesystem path.

## 3. Governance — governance.py + test_governance.py
- [ ] Test alias lookup, Semantica duplicate proposals, type protection, temporal-safe merge, endpoint rewiring, evidence preservation, optimistic conflict, soft deletion and restore.
- [ ] Use Semantica DuplicateDetector and EntityMerger for actual detection/merge advice. Service controls truth mutation transaction and validation. Merge same-type equal-interval entities initially; reject incompatible intervals instead of widening facts. Preserve aliases, source IDs and originals in immutable history; mark merged-away record tombstoned; rewire relations atomically, never silently collapse distinct predicates.
- [ ] Soft delete closes latest visibility with metadata tombstone, query excludes except explicit history; relation endpoints still enforced. Restore creates audited new versions. Keep repository history.
- [ ] Incremental extraction optionally resolves aliases/exact same-type names; semantic matches require explicit auto-merge flag and threshold; never automatically merge incompatible time intervals.

## 4. Explorer/tasks — explorer.py, jobs.py, parity_api.py and test_explorer.py
- [ ] Test dashboard counts, source full text, node adjacency, scoped subgraph, bounded cycle-safe mindmap, same-scope graph evidence expansion, task project isolation, durable status and restart interrupted tasks.
- [ ] Routes: GET /api/local-projects; POST /api/local-projects/import {name,kind}; GET /api/jobs/{id}; GET /api/projects/{id}/jobs. POST project /dashboard,/sources,/subgraph {scope,node_id,hops},/mindmap {scope,root_id,depth}; /resolve {text}; /aliases {entity_id,alias,expected_version}; /merge {keep_id,drop_id,expected_versions}; /delete {record_id,expected_version}; /restore {record_id,version,expected_version}; /documents/jobs {Ingest}.
- [ ] Retain /graph,/records/query,/search,/qa contracts; QA independently recalls entity top-k and source-chunk top-k within identical scope, expands only allowed graph neighbors, then deduplicates evidence. Add graph evidence and response streaming with explicit evidence event; evidence UI links to graph nodes/source/history. Jobs log start, stage, completion/error; no false worker queue guarantees.
- [ ] Project export/snapshot and restore must preserve full latest state and version history. Evaluation supports supplied gold entity/relation triples and counts with no external LLM.

## 5. Rich workbench — web/index.html, app.js, style.css plus tests
- [ ] Red test forbids native prompt/confirm/alert. Replace creation with accessible inline form/dialog, validation and error handling.
- [ ] Add local projects load, dashboard, interactive ECharts graph (zoom/drag/node detail/1-2 hop), source viewer, mindmap, entity table and governance panel, tasks/progress, snapshot/history and evaluation. Retain existing time/metadata/ontology controls.
- [ ] Bind all write controls to real APIs; no placeholders or link-only substitutes. Prevent cross-project stale responses. All dynamic content escaped.
- [ ] Actual browser exercise create, import, filtered graph, entity resolution, merge and rollback, source/fulltext, ontology; document exact omissions if any remain.

## 6. Review and delivery
- [ ] Independent spec review then quality review; fix important issues.
- [ ] Run conda tests, local embedding and real HTTP smoke; import all four existing old projects non-destructively and reconcile node/edge/segment counts and preserved sources/aliases per project.
- [ ] Restart only previously launched 8100 service's verified process, using conda environment; health displays executable/runtime/actual capabilities.
- [ ] Update README and capability matrix with implemented versus remaining, no claim of full parity if tests or workflows missing.

Tests: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; & 'D:/anaconda/envs/llm_model/python.exe' -m pytest tests/service tests/test_web_ui_contract.py -q`. Every behavior test fails before implementation and passes afterward. No old data deletion.
