# Governed Knowledge Pipeline Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add source-scoped Assertions, one governed formal-write seam, independent ingest readiness, reviewable/reversible entity resolution, and reusable BM25/vector hybrid retrieval while preserving current ontology and history behavior.

**Architecture:** Keep canonical documents, chunks, entities, and relations in `record_versions`. Add adjacent governance tables owned by `Repository`, put formal graph mutations behind `FormalFactWriter`, and expose retrieval through one `RetrievalEngine`. Migrate legacy metadata transactionally and preserve existing APIs with additive response fields.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite/FTS5, NumPy/FAISS, rdflib/pySHACL, pytest, Node test runner.

---

## File map

- Create `knowledge_service/assertions.py`: assertion models, legal transitions, deterministic occurrence IDs.
- Create `knowledge_service/formal_writes.py`: `FormalFactWriter` and canonical Fact-key behavior.
- Create `knowledge_service/entity_resolution.py`: three-band mention alignment and resolution-review outcomes.
- Create `knowledge_service/ingest_runs.py`: readiness/state projection helpers.
- Modify `knowledge_service/repository.py`: schemas, migrations, transactions, governance persistence, FTS, export/delete.
- Modify `knowledge_service/service.py`: staged ingest orchestration and formal writer integration.
- Modify `knowledge_service/reconciliation.py`: delegate identity decisions to `EntityResolver`.
- Modify `knowledge_service/reviews.py`: first-class Assertion and entity-resolution review decisions.
- Modify `knowledge_service/governance.py`: atomic merge ledger and reversal.
- Modify `knowledge_service/retrieval.py`: keyword retrieval, RRF, unified engine.
- Modify `knowledge_service/answers.py`: hybrid seeds before graph expansion.
- Modify `knowledge_service/models.py`, `knowledge_service/api.py`, `knowledge_service/parity_api.py`: additive request/response contracts.
- Modify `knowledge_service/explorer.py`, `knowledge_service/legacy_import.py`, `knowledge_service/neo4j_store.py`: governance export/import/restore compatibility.
- Modify ontology-discovery/change entry points only where they publish or mutate formal records.
- Modify web search/workbench files only to expose retrieval mode and readiness already returned by the backend.
- Add focused test modules listed below; update existing compatibility tests only when the public response is intentionally additive.

## Fixed implementation contracts

The plan implements these interfaces without redesign during execution:

```python
Repository.create_assertion(project_id, assertion, *, tx=None) -> dict
Repository.transition_assertion(project_id, assertion_id, expected_decision_version,
                                status, reason, actor, *, canonical_record_id=None, tx=None) -> dict
Repository.reprocess_assertion(project_id, assertion_id, expected_decision_version,
                               reason, actor, *, tx=None) -> dict
Repository.apply_formal_operation(project_id, operation, expected, callback) -> dict

FormalFactWriter.apply(operation: str, project_id: str,
                       assertions: list[dict], expected: dict,
                       policy: dict) -> FormalWriteResult

EntityResolver.resolve(project_id, mention, *, review_threshold,
                       merge_threshold, tx) -> ResolutionOutcome

RetrievalEngine.search(project_id, query, *, retrieval_mode,
                       scope, content_channels, k, content_k=None) -> dict
```

`FormalWriteResult` always contains `accepted_records`, `assertion_updates`, `review_items`, `ontology_proposals`, and `diagnostics`. Repository transaction callbacks receive a SQLite connection and must not open nested transactions.

Before implementation, record `git status --short` and inspect `git diff` for every task-owned dirty file. Do not commit implementation tasks in this dirty worktree. Apply only exact patches, never replace whole files, and after each task inspect `git diff -- <touched paths>` to ensure pre-existing hunks remain. The design/plan documentation commits are separate and already isolated.

### Task 0: Baseline and migration harness

**Files:**
- Modify: `tests/service/test_repository.py`
- Modify: `knowledge_service/repository.py`

- [ ] **Step 1: Capture the dirty-file baseline in the execution log**

Run: `git status --short` and `git diff --stat`.

- [ ] **Step 2: Write a failing test for transactional, versioned migration**

Open a database twice, verify each migration runs once, and inject a failing migration to prove schema/data changes roll back together.

- [ ] **Step 3: Run the test and confirm RED**

Run: `pytest tests/service/test_repository.py -q`

- [ ] **Step 4: Implement `schema_migrations` and a migration runner**

Migrations receive the active SQLite connection; `Repository.__init__` executes them before normal reads. No migration may call a public repository method that starts another transaction.

- [ ] **Step 5: Verify GREEN and inspect the repository diff**

Run: `pytest tests/service/test_repository.py -q`

Run: `git diff -- knowledge_service/repository.py tests/service/test_repository.py`

### Task 1: Governance schema and Assertion lifecycle

**Files:**
- Create: `knowledge_service/assertions.py`
- Modify: `knowledge_service/repository.py`
- Test: `tests/service/test_assertions.py`
- Test: `tests/service/test_repository.py`

- [ ] **Step 1: Write failing schema and lifecycle tests**

Cover deterministic source-occurrence IDs, replay of the same payload, explicit failure for an unlike payload at the same occurrence key, legal/illegal transitions, actor-bearing append-only events, decision-version CAS, project isolation, cascade deletion, rejection of generic `rejected -> pending`, and acceptance only through `reprocess_assertion()`.

```python
def test_assertion_decision_is_compare_and_set(repo, project):
    assertion = repo.create_assertion(project, source_assertion())
    accepted = repo.transition_assertion(project, assertion['id'], 1, 'accepted', 'valid')
    assert accepted['decision_version'] == 2
    with pytest.raises(ValueError, match='Version conflict'):
        repo.transition_assertion(project, assertion['id'], 1, 'rejected', 'stale')
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `pytest tests/service/test_assertions.py tests/service/test_repository.py -q`

Expected: missing Assertion APIs/tables.

- [ ] **Step 3: Implement Assertion domain helpers**

Add status constants, `occurrence_id(namespace, document_version_id, chunk_id, kind, ordinal, raw_terms)`, payload equality checks, and the legal transition matrix in `assertions.py`.

- [ ] **Step 4: Add only Assertion/event tables and repository methods**

Use the Task 0 migration runner. Implement create/get/list/transition methods and append events in the same transaction.

- [ ] **Step 5: Run lifecycle tests GREEN before adding more tables**

Run: `pytest tests/service/test_assertions.py -q`

- [ ] **Step 6: Write failing Fact-key/backfill tests**

Cover valid relation keys, duplicate-key consolidation, invalid legacy predicates/endpoints, candidate-state mapping, grandfathered manual records, and idempotent re-open.

- [ ] **Step 7: Add Fact-key storage and backfill only**

Add `fact_keys`, then implement the transactional legacy backfill. Ingest-run, resolution-review, and merge-ledger tables are deliberately deferred until their Tasks 4–6 tests fail.

- [ ] **Step 8: Run focused tests and refactor**

Run: `pytest tests/service/test_assertions.py tests/service/test_repository.py -q`

- [ ] **Step 9: Inspect task-owned diffs and preserve baseline hunks**

Run: `git diff -- knowledge_service/assertions.py knowledge_service/repository.py tests/service/test_assertions.py tests/service/test_repository.py`

### Task 2: Canonical Fact keys and the formal-write seam

**Files:**
- Create: `knowledge_service/formal_writes.py`
- Modify: `knowledge_service/repository.py`
- Modify: `knowledge_service/service.py`
- Modify: `knowledge_service/api.py`
- Test: `tests/service/test_formal_writes.py`
- Test: `tests/service/test_api.py`

- [ ] **Step 1: Write failing tests for canonical identity and atomicity**

Test that two accepted Assertions with the same normalized relation key share one immutable Fact, ontology-version changes do not split stable IRIs, stale expected versions abort all changes, source fields stay on Assertions rather than new canonical relations, one malformed extraction item does not discard valid siblings, and a database failure rolls back the entire well-formed chunk.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_formal_writes.py -q`

- [ ] **Step 3: Implement canonical-key lookup/create inside an existing transaction**

Add repository primitives to reserve/update/release a Fact key without nested transactions. Verify concurrent/replayed equality and immutable Fact IDs.

- [ ] **Step 4: Implement `FormalFactWriter.apply()` for `extract` only**

Own one chunk transaction and return the fixed result shape. Parseable malformed items become rejected Assertions in a separate safe transaction; infrastructure failure commits no well-formed chunk changes.

- [ ] **Step 5: Add `manual_write`, `approve_review`, and `legacy_import` operations one at a time**

Add one failing test then minimal operation implementation for each operation type.

- [ ] **Step 6: Route direct record creation and `KnowledgeService.write()` through the seam**

Preserve current API status codes and optimistic locking. Manual entity/relation writes create grandfathered/manual Assertions.

- [ ] **Step 7: Run focused and API tests**

Run: `pytest tests/service/test_formal_writes.py tests/service/test_api.py -q`

- [ ] **Step 8: Inspect task-owned diffs and preserve baseline hunks**

### Task 3: Separate open discovery from formal adoption

**Files:**
- Modify: `knowledge_service/ontology_discovery.py`
- Modify: `knowledge_service/ontology_changes.py`
- Modify: `knowledge_service/api.py`
- Modify: `knowledge_service/workspace_api.py`
- Modify: `knowledge_service/reviews.py`
- Modify: `knowledge_service/formal_writes.py`
- Test: `tests/service/test_ontology_discovery.py`
- Test: `tests/service/test_ontology_change_proposals.py`
- Test: `tests/service/test_reviews.py`
- Test: `tests/service/test_ontology_maintenance.py`

- [ ] **Step 1: Write failing boundary tests**

Prove discovery candidates may use terms outside the formal ontology, remain pending Assertions without canonical links, and only encounter formal constraints during adoption/publication. Add a routing spy test covering ontology upload, term create/update/retire, discovery draft publication, proposal approval, and review approval; each formal mutation must name a `FormalFactWriter` operation.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_ontology_discovery.py tests/service/test_ontology_change_proposals.py tests/service/test_reviews.py -q`

- [ ] **Step 3: Persist discovery/review candidates as Assertions**

Keep legacy metadata during compatibility release, but make list/decision APIs read the first-class rows after migration.

- [ ] **Step 4: Add formal operations**

Implement `adopt_discovery`, `publish_discovery_draft`, `ontology_publish`, and `ontology_remap` with ontology/draft/proposal/Assertion/canonical-record atomicity.

- [ ] **Step 5: Verify focused suites**

Run: `pytest tests/service/test_ontology_discovery.py tests/service/test_ontology_change_proposals.py tests/service/test_reviews.py tests/service/test_ontology_maintenance.py -q`

- [ ] **Step 6: Inspect task-owned diffs and preserve baseline hunks**

### Task 4: Staged ingest and independent readiness

**Files:**
- Create: `knowledge_service/ingest_runs.py`
- Modify: `knowledge_service/repository.py`
- Modify: `knowledge_service/service.py`
- Modify: `knowledge_service/jobs.py`
- Modify: `knowledge_service/api.py`
- Modify: `knowledge_service/parity_api.py`
- Modify: `knowledge_service/models.py`
- Test: `tests/service/test_ingest_runs.py`
- Test: `tests/service/test_api.py`
- Test: `tests/service/test_diagnostics.py`

- [ ] **Step 1: Write failing state-machine tests**

Test `document_ready`, backend-specific readiness, `search_ready=keyword_ready or semantic_ready`, `candidate_ready`, `graph_ready=true` with pending reviews, `graph_ready=false` with any failed chunk, successful/failed chunk counts, search availability before extraction, extraction failure preserving search readiness, restart interruption, `attempt`/`retry_of`, cross-attempt stage reuse, and the consumed stage-output keys recorded by the new attempt.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_ingest_runs.py tests/service/test_diagnostics.py -q`

- [ ] **Step 3: Implement durable runs and stage outputs**

Use `(document_version_id, chunk_id, stage, input_hash)` output keys and deterministic Assertion IDs. Make synchronous and job responses share `run_id`, `job_id`, readiness, stage, status, and failure fields.

- [ ] **Step 4: Write a failing minimal FTS readiness test**

Require current entity/chunk rows to be inserted into FTS in the same repository transaction, deletion/supersession to remove them, and a rebuild to restore a deliberately cleared index.

- [ ] **Step 5: Implement current-record FTS maintenance only**

This task provides keyword indexing and readiness, not public ranking/RRF. Add repository `keyword_candidates()` and `rebuild_fts()` primitives used by ingest; Task 8 adds the full retrieval engine.

- [ ] **Step 6: Refactor ingest into durable document/index and graph phases**

Commit document/chunks first, build FTS and embeddings independently, then run discovery or formal extraction. Preserve the existing document receipt on all failures.

- [ ] **Step 7: Verify focused tests**

Run: `pytest tests/service/test_ingest_runs.py tests/service/test_api.py tests/service/test_diagnostics.py -q`

- [ ] **Step 8: Inspect task-owned diffs and preserve baseline hunks**

### Task 5: Three-band entity resolution and review

**Files:**
- Create: `knowledge_service/entity_resolution.py`
- Modify: `knowledge_service/formal_writes.py`
- Modify: `knowledge_service/repository.py`
- Modify: `knowledge_service/reconciliation.py`
- Modify: `knowledge_service/reviews.py`
- Modify: `knowledge_service/models.py`
- Test: `tests/service/test_entity_resolution.py`
- Test: `tests/service/test_reconciliation.py`

- [ ] **Step 1: Write failing resolution tests**

Cover exact alias alignment, high-score mention alignment, review-band creation of a separate entity plus persisted review, low-score separate entity, incompatible types, temporal incompatibility, review listing, and review decision-version CAS.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_entity_resolution.py tests/service/test_reconciliation.py -q`

- [ ] **Step 3: Add resolution-review schema and repository methods**

After the persistence tests fail, add create/list/decide methods with `decision_version`, actor, timestamps, and project isolation.

- [ ] **Step 4: Implement `EntityResolver`**

Return explicit `aligned`, `review`, or `separate` outcomes. Keep request-level thresholds with validation `0 <= review_threshold < merge_threshold <= 1`.

- [ ] **Step 5: Integrate with `FormalFactWriter`**

Automatic mention alignment changes only the source Assertion mapping. It never records an entity merge.

- [ ] **Step 6: Verify and refactor**

Run the command from Step 2.

- [ ] **Step 7: Inspect task-owned diffs and preserve baseline hunks**

### Task 6: Atomic merge ledger, Fact collision, and reversal

**Files:**
- Modify: `knowledge_service/governance.py`
- Modify: `knowledge_service/formal_writes.py`
- Modify: `knowledge_service/repository.py`
- Modify: `knowledge_service/parity_api.py`
- Test: `tests/service/test_entity_merges.py`
- Test: `tests/service/test_governance.py`

- [ ] **Step 1: Write failing merge tests**

Cover atomic review decision, entity/relation revisions, Assertion reassignment, colliding Fact consolidation, ledger contents, successful compensating reversal, and reversal refusal after a later edit.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_entity_merges.py tests/service/test_governance.py -q`

- [ ] **Step 3: Implement `merge_rewrite` transaction**

Use immutable Fact IDs and transactional `fact_keys` updates. Select the lexicographically smaller colliding Fact ID and ledger every reassignment/version.

- [ ] **Step 4: Implement append-only reversal**

Require record, Assertion, review, and ledger expectations. Create `reversal_of` operation; never overwrite history.

- [ ] **Step 5: Verify focused tests**

Run the command from Step 2.

- [ ] **Step 6: Inspect task-owned diffs and preserve baseline hunks**

### Task 7: Source retraction, attribute reconstruction, and snapshot compensation

**Files:**
- Modify: `knowledge_service/formal_writes.py`
- Modify: `knowledge_service/governance.py`
- Modify: `knowledge_service/explorer.py`
- Modify: `knowledge_service/repository.py`
- Modify: `knowledge_service/evidence.py`
- Test: `tests/service/test_source_retraction.py`
- Test: `tests/service/test_explorer.py`
- Test: `tests/service/test_evidence.py`

- [ ] **Step 1: Write failing support-lifecycle tests**

Test two-source Fact survival, final-support retirement and key release, entity unsupported marking, supported-only attribute reconstruction, unresolved competing attributes, terminal Assertion compensation, snapshot restore retaining later audit history, and migration followed by deleting one legacy source without retiring a still-supported Fact.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_source_retraction.py tests/service/test_explorer.py -q`

- [ ] **Step 3: Implement `retract_source`**

Atomically transition source Assertions, retire/reactivate Facts, update Fact keys, reconstruct attributes, and append audit events.

- [ ] **Step 4: Implement `restore_snapshot`**

Create new compensating Assertions for terminal historical claims and a restore ledger; do not rewind ingest or audit history.

- [ ] **Step 5: Replace metadata-derived evidence reads**

Make `evidence()` return accepted, contradicting, rejected, and superseded Assertions with source anchors and decision state. Retain a migration-version-gated legacy fallback only for databases that have not completed backfill.

- [ ] **Step 6: Verify focused tests**

Run: `pytest tests/service/test_source_retraction.py tests/service/test_explorer.py tests/service/test_evidence.py -q`

- [ ] **Step 7: Inspect task-owned diffs and preserve baseline hunks**

### Task 8: Hybrid retrieval with FTS5 and RRF

**Files:**
- Modify: `knowledge_service/repository.py`
- Modify: `knowledge_service/retrieval.py`
- Modify: `knowledge_service/answers.py`
- Modify: `knowledge_service/models.py`
- Modify: `knowledge_service/api.py`
- Test: `tests/service/test_hybrid_retrieval.py`
- Test: `tests/service/test_retrieval.py`
- Test: `tests/service/test_api.py`

- [ ] **Step 1: Write failing keyword, fusion, and history tests**

Cover exact identifier recall, deterministic RRF (`k=60`), metadata/valid-time scoping before ranking, `known_at` historical lexical fallback, one-backend degradation, response diagnostics, and QA hybrid seeds before graph expansion.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_hybrid_retrieval.py tests/service/test_retrieval.py -q`

- [ ] **Step 3: Complete lexical ranking on Task 4's FTS primitives**

Add relation/current fallback scoring, metadata and valid-time prefiltering, and correctness-first version scanning whenever `known_at` is supplied. FTS remains derived and is never exported as truth.

- [ ] **Step 4: Implement `RetrievalEngine`**

Separate content channels from keyword/semantic backends. Generic `k` is final total; QA passes entity/chunk quotas. Report requested/active/degraded modes, backends, and content channels.

- [ ] **Step 5: Route search and QA through the engine**

Default to `hybrid`; retain explicit `semantic` and `keyword` modes.

- [ ] **Step 6: Verify focused tests**

Run: `pytest tests/service/test_hybrid_retrieval.py tests/service/test_retrieval.py tests/service/test_api.py -q`

- [ ] **Step 7: Inspect task-owned diffs and preserve baseline hunks**

### Task 9: Durability surfaces and compatibility

**Files:**
- Modify: `knowledge_service/repository.py`
- Modify: `knowledge_service/legacy_import.py`
- Modify: `knowledge_service/explorer.py`
- Modify: `knowledge_service/neo4j_store.py`
- Test: `tests/service/test_governance_export.py`
- Test: `tests/service/test_legacy_import.py`
- Test: `tests/service/test_neo4j_store.py`
- Test: `tests/service/test_project_management.py`

- [ ] **Step 1: Write failing durability tests**

Verify export/import/snapshot/project deletion include governance state and migration version, backward imports grandfather support, invalid legacy Fact keys become reviews, and FTS rebuilds after restore/import.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_governance_export.py tests/service/test_legacy_import.py tests/service/test_neo4j_store.py tests/service/test_project_management.py -q`

- [ ] **Step 3: Extend export and projection snapshots**

Serialize governance rows in stable ID/time order plus `schema_version`; exclude FTS. Update Neo4j fingerprint tests before adapting the projection consumer.

- [ ] **Step 4: Extend backward import**

Restore governance rows when present; when absent, create grandfathered Assertions and Fact keys through `legacy_import`. Rebuild FTS after the truth transaction commits.

- [ ] **Step 5: Extend project deletion and snapshot capture**

Delete every project-scoped governance row atomically. Snapshot truth rows needed by compensating restore, never derived FTS.

- [ ] **Step 6: Verify focused tests**

Run the command from Step 2.

- [ ] **Step 7: Inspect task-owned diffs and preserve baseline hunks**

### Task 10: UI integration and full-chain acceptance

**Files:**
- Modify: `knowledge_service/web/workbench.js`
- Modify: `knowledge_service/web/task-review.js`
- Modify: `knowledge_service/web/evidence-inspector.js`
- Modify: `knowledge_service/web/record-dialog.js`
- Modify: `knowledge_service/web/ontology-manager.html`
- Test: `tests/service/test_governed_pipeline.py`
- Test: `tests/service/test_workbench.py`
- Test: `tests/service/task_review_ui.test.cjs`
- Test: `tests/service/ingest-input.test.cjs`

- [ ] **Step 1: Write failing UI-contract and end-to-end tests**

The scenario ingests the same source in documents/discovery/ontology modes, adopts a discovery Assertion, creates a resolution review, merges and reverses it, searches keyword/semantic/hybrid, deletes one of two supporting sources, and confirms the Fact survives the remaining support.

Also add an exhaustive routing test that monkeypatches `FormalFactWriter.apply` and exercises every formal mutation endpoint/operation from the specification matrix. The test fails if any path writes a canonical entity/relation or ontology without declaring its operation type.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/service/test_governed_pipeline.py tests/service/test_workbench.py -q`

Run: `node --test tests/service/task_review_ui.test.cjs tests/service/ingest-input.test.cjs`

- [ ] **Step 3: Add additive UI controls and status rendering**

Expose Hybrid/Semantic/Keyword in advanced search, render backend degradation, readiness, Assertion evidence, resolution reviews, merge history, and reversal conflicts without removing existing workflows.

- [ ] **Step 4: Run the full Python suite**

Run: `pytest -q`

- [ ] **Step 5: Run the full browser suite**

Run: `node --test tests/service/*.test.cjs`

- [ ] **Step 6: Run syntax/packaging checks**

Run: `python -m compileall -q knowledge_service`

Run: `git diff --check`

- [ ] **Step 7: Inspect final diff against the specification invariant-by-invariant**

Confirm all formal entry points use `FormalFactWriter`, discovery remains open, support counts are source-safe, readiness is orthogonal, merges are reversible, historical retrieval is correct, and lifecycle exports are complete.

- [ ] **Step 8: Inspect the complete diff and confirm no pre-existing hunk was lost**
