# Incremental Ontology Discovery Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make repeated open discovery accumulate safely, publish versioned ontology diffs without materializing candidates, and preserve stable ontology term IRIs across versions.

**Architecture:** Keep discovery occurrences in document provenance and use draft artifacts as immutable candidate snapshots. Build each draft on the current ontology, publish only the ontology contract with lineage metadata, and require controlled ingestion for the formal graph.

**Tech Stack:** Python 3.12, FastAPI, SQLite, RDFLib, Semantica, pytest, vanilla JavaScript.

---

### Task 1: Persist ontology lineage metadata

**Files:**
- Modify: `knowledge_service/repository.py`
- Test: `tests/service/test_repository.py`

- [ ] Write a failing repository test that saves V1 and V2 with metadata and checks `parent_version_id`, `source_draft_id`, and `diff` after reopening SQLite.
- [ ] Add a backward-compatible `metadata` column migration for `ontologies`.
- [ ] Extend `save_ontology` with optional metadata and use explicit insert column names.
- [ ] Parse ontology metadata from `list_ontologies`; update atomic ontology-change insertion.
- [ ] Run the focused repository test.

### Task 2: Build cumulative drafts on the current ontology

**Files:**
- Modify: `knowledge_service/ontology_discovery.py`
- Test: `tests/service/test_ontology_discovery.py`

- [ ] Write failing tests for stable term IRI reuse and additive diff generation from V0.
- [ ] Add helpers that index existing terms by IRI, label, and local name.
- [ ] Extend `_induce` with optional baseline Turtle and start the RDF graph from that baseline.
- [ ] Add deterministic ontology diff generation for classes, relations, and attributes.
- [ ] Save `parent_ontology_id`, diff, and the full candidate snapshot on every draft.
- [ ] Run focused induction and discovery tests.

### Task 3: Decouple ontology publication from formal graph materialization

**Files:**
- Modify: `knowledge_service/ontology_discovery.py`
- Test: `tests/service/test_ontology_discovery.py`

- [ ] Change the existing publication test to assert that no entity or relation is created.
- [ ] Add a failing stale-parent publication test.
- [ ] At publish time compare the draft parent with the current ontology under the service lock.
- [ ] Save the ontology with lineage metadata and update the draft status without mapping candidates.
- [ ] Return compatibility counters at zero plus `requires_controlled_reingest`.
- [ ] Run all ontology discovery tests.

### Task 4: Expose candidate lifecycle state

**Files:**
- Modify: `knowledge_service/ontology_discovery.py`
- Test: `tests/service/test_ontology_discovery.py`

- [ ] Add failing assertions for pending, included, approved, and materialized counts.
- [ ] Derive candidate states from draft snapshots and existing formal records that carry `discovery_candidate_id`.
- [ ] Preserve `unpublished_candidate_count` as a compatibility alias for pending plus included-but-unapproved candidates.
- [ ] Return a controlled-reingest recommendation after publication.
- [ ] Run focused tests.

### Task 5: Update the discovery UI contract

**Files:**
- Modify: `knowledge_service/web/workspace.js`
- Modify: `knowledge_service/web/workbench.js`
- Modify: `knowledge_service/web/task-review.js`
- Test: `tests/test_web_ui_contract.py`

- [ ] Add failing UI contract assertions for the new publication wording and reingest guidance.
- [ ] Show lifecycle counts and ontology diff on each draft.
- [ ] Rename the action from publishing-and-mapping to publishing the ontology version.
- [ ] Replace graph/review hints that claim candidates are automatically materialized.
- [ ] Run UI contract tests.

### Task 6: Documentation and regression verification

**Files:**
- Modify: `README.md`
- Modify: `docs/KNOWLEDGE_SERVICE.md`

- [ ] Update the documented cold-start flow and clarify that publishing does not create formal graph records.
- [ ] Run `pytest tests/service/test_repository.py tests/service/test_ontology_discovery.py -q`.
- [ ] Run `pytest tests/service -q`.
- [ ] Run `pytest tests/test_web_ui_contract.py -q`.
- [ ] Record any unrelated existing failures without modifying out-of-scope code.

Note: this workspace has no `.git` directory, so worktree creation and commit steps are unavailable.
