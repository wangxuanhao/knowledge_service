# 规划样式校正与保留容量项目 Implementation Plan

> **For agentic workers:** Use independent agents for ontology UI and retrieval UI, with root coordinating data migration, measurements and integration. Steps use checkbox syntax.

**Goal:** Restore the approved light ontology/retrieval workspace, retain all working controls, and expose the existing 150001-entity capacity project in the main application's project list with usable authoritative data and Milvus projection.

**Architecture:** Reuse current ontology editing, evidence inspection and WebGL implementations. Preserve the existing capacity database as source, copy only its selected project to the original application's database, verify authority and vector identity before readiness, and measure actual edit/load stages without duplicate encoder processes.

**Tech Stack:** Native JavaScript/CSS, Cytoscape, WebGL, FastAPI, PostgreSQL, Milvus, Node test runner, Python 3.12.

## Accepted constraints

- User already approved the original mockup and implementation; this is a correction against it.
- The test project must appear in current 项目管理. A separate app is not the delivered entrance.
- Preserve source corpus, revisions, original projects and accounts. Never rerun the reset seeder.
- Milvus only; retain explicit keyword retrieval independently.
- No claims of passing capacity targets or database lock causality without fresh measurements.

## Tasks

- [x] Ontology: coherent light tokens; connected class cards with property/datatype rows; retain term grid under an accurate name; compact-node display; distinguish inheritance/business arrows; actual hierarchy navigation with multi-parent references; responsive inspector. Files: `knowledge_service/web/ontology-model*`; focused Node behavioral tests plus browser visual check.
- [x] Retrieval: compact top query bar, evidence results at left, large canvas, inspector on demand; advanced graph settings in a disclosure; distinguish local/full controls. Files: `knowledge_service/web/index.html`, `workspace.*`, `full-graph-view.js`, `full-graph.css`; focused Node tests and browser check. Bump changed asset versions without removing any operation.
- [x] Migration: inspect snapshot APIs/schema and vector generation identities; bounded project copy preserving immutable IDs/revisions; verify counts and per-table digests; reuse compatible vectors in batches or explicitly rebuild valid projection; expose clear project name and test instructions. Private migration harness and public result without credentials.
- [x] Diagnosis: phase instrumentation of actual PUT; time application/repository lock acquisition separately from hold; SQL execution/fetch/materialization/fingerprints and encoder; independent PostgreSQL lock sampling and host resources. Time actual graph network/JSON/layout/GPU stages. Save new `2026-10-20` reports alongside historical evidence.
- [x] Integrate: review independent changes; run Node suite and relevant API checks; sync via guarded manifest helper; refresh manual screenshots and explanation; restart only owned app if backend/instrumentation requires; confirm health and retained project in current UI.

## Validation

Visual changes must be inspected in the real browser at desktop and narrow widths. Behavioral view changes need meaningful tests reaching the view compilation or interaction seam. Migration must prove no existing project overwrite and no auth table changes, plus exact selected-project counts and retained history. Projection readiness needs actual Milvus search and authoritative version agreement. Performance results distinguish single-run phase diagnosis from distribution/target acceptance; recording a slow result is not success against the budget.

## Recorded results

- Capacity project is retained in the main app at `http://127.0.0.1:8100/#projects`; main authority counts are 150001 entities / 300000 relations. Migration and exact vector checks are recorded in `docs/research/2026-10-20-retained-capacity-migration.json`.
- Normal app startup replaces the private diagnostic wrapper. Semantic, hybrid and explicitly selected keyword HTTP searches all return 200 with hits, no degradation and no retrieval errors; vector modes report Milvus. Evidence: `docs/research/2026-10-20-normal-main-smoke.json`.
- Graph view-switch and legend regressions have focused tests and real browser checks. The complete Node frontend suite passes. Offline manual checks cover 27 chapters and nine real UI screenshots; browser checks cover navigation and 390/850px overflow.
- Current performance remains below target. Single PUT is 102.888 seconds; single full graph load is 77.516 seconds. Application locks, all-project snapshots and timestamp work are separated in `docs/research/2026-10-20-main-capacity-diagnosis.md`; lock owner and some timing boundaries remain unresolved.
- The newer pending-modeling workflow is a separately marked static design proposal, with spec/API gaps and an interactive prototype. It has not been implemented in production.
