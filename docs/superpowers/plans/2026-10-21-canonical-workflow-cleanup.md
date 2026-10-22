# Canonical workflow cleanup implementation plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development for isolated implementation and review. Do not run concurrent pytest sessions or overwrite unrelated workspace edits.

**Goal:** Remove obsolete discovery and data compatibility execution paths while retaining complete current intake and governed publication.

**Architecture:** Current selected intake owns candidate write intents; ontology-drafts owns structural commands and publication. Read views use bounded current projections and canonical assertions. Obsolete endpoints and wrappers are removed with their callers.

**Tech Stack:** FastAPI, PostgreSQL, conda llm_model Python 3.12, native JS, node:test.

## Tasks

- [x] Write failing route and read-ownership tests in tests/service/test_canonical_workflow.py. Exercise installed routes and real overview/mindmap orchestration; forbid legacy artifact reads and full record scans.
- [x] Replace services/ontology_discovery_workflow.py with current read orchestration; move source_context into ontology_intake.py. Delete obsolete API request models and write routes in api/ontology_discovery.py. Unify lifecycle inputs in services/ontology_discovery.py.
- [x] Remove dead generation/finalize UI methods and their transient states in web/ontology-model.js and ontology-model-panel.js. Keep openIntake and discoverParents. Bump index.html versions; update contracts that intentionally asserted removed behavior.
- [x] Complete adapter/full-query inventory. Migrate any live caller before deleting its old endpoint, response wrapper or data protocol. Validate current source workflows rather than preserve old tests for obsolete endpoints.
- [x] Remove embedded-record discovery publication and historical generator fallbacks; reject singular discovery publication_effects explicitly with StaleSource before formal writes. Retain candidate proposal/import effects while those are active workflows, and test that rejection performs no writes.
- [x] Write PG source-association regression tests; remove derived fact source_id and candidate-metadata fallback in repository/page_reads.py. Preserve chunk source_id, historical assertion_events and bounded summaries.
- [ ] Review spec compliance, then quality; address concrete defects. Run affected Python tests in one session, Node contracts, diff whitespace check, restart 8100 safely and perform HTTP smoke. Save evidence and final residual inventory.

Retained-workflow PG validation must include tests/service/test_ontology_intake.py, test_ontology_intake_validation.py, test_source_scope_inheritance.py and test_ontology_drafts.py (or focused cases covering manual create, rejection/replacement, withdrawal and publication). Run the entire selected-intake suite for multi-batch publication, reuse-only confirmation, idempotency and concurrent CAS; add a regression at the publication seam for obsolete singular effects with no writes.

## Commands

Use D:/anaconda/envs/llm_model/python.exe -X utf8 -m pytest with explicit affected test files, never source .env. Use node --test tests/service/*.test.cjs for frontend. Validate no other process has an independent pytest argument before each Python suite. Do not commit this dirty shared workspace as a whole.
