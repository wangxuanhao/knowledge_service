# First-Class Attribute Facts Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make entity attributes first-class `attribute` records backed by the existing version/assertion ledger, then expose them consistently through review, discovery, lifecycle operations, RDF/Neo4j and the Explorer UI.

**Architecture:** SQLite and `FormalFactWriter` remain the source of truth. Attribute records use the existing record/version/assertion transaction boundary; `Ontology` owns datatype, domain, cardinality and RDF behavior; Semantica remains an extraction/induction adapter; Neo4j and Explorer are derived views.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, SQLite, RDFLib/pySHACL, Neo4j adapter, vanilla JavaScript, pytest and Node's existing test runner.

---

## File map

- `knowledge_service/models.py`, `knowledge_service/repository/core.py`: accept and validate the new record shape.
- `knowledge_service/services/formal_writes.py`, `knowledge_service/services/service.py`: canonical key, ledger support, legacy property conversion and source retraction.
- `knowledge_service/services/ontology.py`, `knowledge_service/services/review_validation.py`: attribute schema/cardinality validation and RDF literals.
- `knowledge_service/services/reviews.py`, `knowledge_service/api/reviews.py`, `knowledge_service/web/task-review.js`: controlled approval and explicit single-value conflict decisions.
- `knowledge_service/services/ontology_discovery.py`: materialize every discovered property independently and shape Semantica induction input.
- `knowledge_service/services/governance.py`, `knowledge_service/integrations/neo4j_store.py`: merge and projection behavior.
- `knowledge_service/services/explorer.py`, `knowledge_service/api/parity.py`, `knowledge_service/web/workspace.js`, `knowledge_service/web/evidence-inspector.js`: compact table plus selected-entity graph expansion.
- Existing related files under `tests/service/`: minimal regression assertions only; no new test scripts.

### Task 1: Establish the formal attribute write seam

**Files:**
- Modify: `knowledge_service/models.py`
- Modify: `knowledge_service/repository/core.py`
- Modify: `knowledge_service/services/formal_writes.py`
- Modify: `knowledge_service/services/service.py`
- Modify: `knowledge_service/services/ontology.py`
- Test: `tests/service/test_formal_writes.py`
- Test: `tests/service/test_ontology.py`

- [ ] **Step 1: Add a failing ledger assertion in the existing formal-writes test**

  Write one entity plus an attribute with `value=20` and XSD integer datatype, then assert the attribute is a current record, has an accepted source assertion and reuses the same record when the same fact arrives from a second source.

- [ ] **Step 2: Run the focused test and verify it fails at model validation**

  Run: `python -m pytest tests/service/test_formal_writes.py -q`
  Expected: failure because `attribute`, `value` or `datatype` is not accepted.

- [ ] **Step 3: Implement the minimal record and fact-key support**

  Add `attribute` to `RecordWrite.kind`, `Scope.kinds` and repository kind checks. Add optional `value: str | bool | int | float | None` and `datatype: str | None`; repository validation must require both for attributes, reject `None`, containers and non-finite floats, and require `subject_id`/`type`.

  Use one deterministic primitive mapping everywhere: `str -> xsd:string`, `bool -> xsd:boolean`, exact `int -> xsd:integer`, finite `float -> xsd:double`. If the ontology declares a range, require that declared datatype to accept the primitive instead of silently coercing it. Review, discovery and legacy-property conversion all call the same helper.

  In `formal_writes.py`, add `canonical_attribute_key(record)` based only on `[subject_id, type, value, datatype, valid_from, valid_until]` using deterministic JSON, then route both relation and attribute facts through existing `fact_keys`, automatic assertions, version support and last-support tombstoning. Include the canonical typed value (`datatype` plus deterministic JSON value) in `_source_assertion` raw terms and `_manual_assertion_id`, so different values cannot collide even when their surrounding text is equal.

- [ ] **Step 4: Validate through the existing ontology seam**

  Extend `Ontology.validate`/`validate_timeline` so attribute subjects exist in the same prospective batch, the predicate is a DatatypeProperty, its domain accepts the subject entity type, its range accepts the shared primitive-to-datatype mapping, and RDFLib/SHACL receives that typed literal. Add `attribute_max_count_one(...)` by reading matching SHACL property shapes rather than introducing a second configuration model.

  In `KnowledgeService.write`, convert non-empty primitive entity `properties` into sibling attribute records in the same batch and persist the entity with an empty property bag. Reject container values. Keep already-stored legacy property bags readable.

- [ ] **Step 5: Run focused tests and commit**

  Run: `python -m pytest tests/service/test_formal_writes.py tests/service/test_ontology.py -q`
  Expected: PASS.

  Commit: `feat: add formal attribute records`

### Task 2: Move controlled attribute review onto the ledger

**Files:**
- Modify: `knowledge_service/services/reviews.py`
- Modify: `knowledge_service/api/reviews.py`
- Modify: `knowledge_service/models.py`
- Modify: `knowledge_service/web/task-review.js`
- Test: `tests/service/test_typed_reviews.py`
- Test: `tests/service/test_reviews.py`

- [ ] **Step 1: Replace the existing property-bag expectations with formal-record assertions**

  In the current typed-review test, approve an attribute candidate and assert an `attribute` record exists while the entity version and `properties` remain unchanged. Add one `sh:maxCount 1` case: a different overlapping value becomes `contradicting`; an explicit `approve_replace` supersedes old accepted supports, tombstones the old attribute and accepts the candidate.

- [ ] **Step 2: Run the focused review tests and verify failure**

  Run: `python -m pytest tests/service/test_typed_reviews.py tests/service/test_reviews.py -q`
  Expected: the old code mutates `entity.properties` and has no replacement action.

- [ ] **Step 3: Implement approval, conflict and replacement in one write transaction**

  Build `reviewattr_<candidate_id>` records from the candidate's `entity_id`, predicate, typed value, evidence and ontology. Same-value approval relies on the formal fact key. For a different overlapping value on a max-count-one predicate, transition the candidate assertion to `contradicting` without changing current facts.

  Extend the review action literal with `approve_replace`. That action submits assertion decisions that supersede all accepted supports of the conflicting current attribute records, writes their tombstones with expected versions, creates/accepts the new attribute and updates the candidate status in the same `KnowledgeService.write` call.

- [ ] **Step 4: Expose only the three useful reviewer actions**

  Make the review API list current formal attribute values and conflict state. In `task-review.js`, show “保留旧值/拒绝候选” and “接受新值” for a conflict; preserve the ordinary approve/reject flow otherwise.

- [ ] **Step 5: Run focused tests and commit**

  Run: `python -m pytest tests/service/test_typed_reviews.py tests/service/test_reviews.py -q`
  Expected: PASS.

  Commit: `feat: review attributes as formal facts`

### Task 3: Materialize ontology-discovery attributes without overwrite

**Files:**
- Modify: `knowledge_service/services/ontology_discovery.py`
- Test: `tests/service/test_ontology_discovery.py`
- Test: `tests/service/test_semantica_adapter.py`

- [ ] **Step 1: Add a repeated-property regression to the existing discovery test**

  Publish two candidates for the same entity/predicate with different values and assert two attribute records survive. Assert the induction payload also exposes stable top-level primitive fields to Semantica.

- [ ] **Step 2: Run the focused tests and verify the current dictionary overwrite**

  Run: `python -m pytest tests/service/test_ontology_discovery.py tests/service/test_semantica_adapter.py -q`
  Expected: only the last property is materialized or the induction input omits it.

- [ ] **Step 3: Change materialization and validation, not the extractor contract**

  Emit one deterministic attribute record per candidate from `_materialize_candidates`; validate entity records first and then each attribute/relation against the prospective set. Count published attributes from records, not dictionary keys. Flatten stable property names into the entity sample passed to the existing Semantica `OntologyGenerator`, while retaining current adapter fault isolation.

- [ ] **Step 4: Publish the validated record set through the existing writer and commit**

  Submit entities, attributes and relations through `KnowledgeService.write`; do not add a Semantica storage path.

  Run: `python -m pytest tests/service/test_ontology_discovery.py tests/service/test_semantica_adapter.py -q`
  Expected: PASS.

  Commit: `feat: publish discovered attributes independently`

### Task 4: Preserve attributes through merge, retraction and projections

**Files:**
- Modify: `knowledge_service/services/governance.py`
- Modify: `knowledge_service/api/parity.py`
- Modify: `knowledge_service/services/ontology.py`
- Modify: `knowledge_service/integrations/neo4j_store.py`
- Test: `tests/service/test_governance.py`
- Test: `tests/service/test_formal_writes.py`
- Test: `tests/service/test_ontology.py`
- Test: `tests/service/test_neo4j_store.py`

- [ ] **Step 1: Add narrow lifecycle assertions to existing tests**

  Assert entity merge rewrites attribute subjects, equal values converge through the fact key and unconstrained distinct values remain distinct. For `sh:maxCount 1`, assert an unresolved merge is rejected without partial writes and an explicit `attribute_winners` selection commits the selected value. Assert retracting one of two sources keeps the fact, retracting the last source tombstones it. Assert RDF emits typed literals and Neo4j emits `KS_ATTRIBUTE` edges.

- [ ] **Step 2: Run the four focused files and verify failures**

  Run: `python -m pytest tests/service/test_governance.py tests/service/test_formal_writes.py tests/service/test_ontology.py tests/service/test_neo4j_store.py -q`

- [ ] **Step 3: Extend the existing lifecycle loops**

  Add `attribute_winners: list[str] = []` to the existing `Merge` request and pass it through `api/parity.py` to `Governance.merge`. Each item is an existing attribute record ID selected to win one conflicting max-count-one group; preflight requires exactly one selected member for every conflict group and rejects unrelated IDs.

  Include attribute subjects when `Governance.merge` collects and rewrites affected records. Reuse the fact-key collision behavior; never merge distinct values by label. During preflight, group the prospective keep-subject attributes by predicate and overlapping interval. If a max-count-one group has different values without exactly one selected `attribute_winners` member, return the conflict before opening the write. With a selection, keep the chosen fact accepted; in the same ledger transaction transition every losing accepted support through `contradicting` to `superseded`, tombstone its record, and rewrite the chosen drop-side fact to the keep subject when needed. Extend source retraction's relation tombstone branch to attributes. In RDF, emit formal typed literals first and skip duplicate legacy property triples. In Neo4j, keep attribute facts as nodes and rebuild `(Entity)-[:KS_ATTRIBUTE]->(AttributeFact)` alongside existing relation edges.

- [ ] **Step 4: Run focused tests and commit**

  Expected: all four files PASS.

  Commit: `feat: preserve attributes across lifecycle projections`

### Task 5: Add compact entity detail and selected-node graph expansion

**Files:**
- Modify: `knowledge_service/services/explorer.py`
- Modify: `knowledge_service/api/parity.py`
- Modify: `knowledge_service/web/workspace.js`
- Modify: `knowledge_service/web/evidence-inspector.js`
- Test: `tests/service/test_explorer.py`
- Test: `tests/service/graph-types.test.cjs`
- Test: `tests/service/evidence-inspector-frozen-source.test.cjs`

- [ ] **Step 1: Add Explorer assertions for summary and expanded modes**

  Assert `summary` attaches grouped attributes to entity rows without graph nodes. Assert `expanded` plus a selected `node_id` creates only that entity's virtual attribute nodes and `KS_ATTRIBUTE` edges; `none` omits both.

- [ ] **Step 2: Run Explorer tests and verify the new request field/output is absent**

  Run: `python -m pytest tests/service/test_explorer.py -q`

- [ ] **Step 3: Implement the small query/view adapter**

  Add `attribute_mode: Literal['none','summary','expanded']='summary'` to the subgraph request. Query current attributes once, group by subject, expose value/datatype/validity and accepted-support count. Separately join current `contradicting` assertions (using their payload subject/type/value) into the same groups so a rejected candidate that never became a record still appears as a conflict. Only synthesize graph nodes for the selected entity in expanded mode. Do not persist those display nodes.

- [ ] **Step 4: Render structured values without changing the graph library**

  Show grouped attribute rows in `evidence-inspector.js`. Make `workspace.js` request expanded mode on selection, render attribute nodes with a distinct compact shape, exclude them from entity-type controls and keep default graph summary uncluttered.

- [ ] **Step 5: Run current backend and frontend tests and commit**

  Run: `python -m pytest tests/service/test_explorer.py -q`
  Run: `node --test tests/service/graph-types.test.cjs tests/service/evidence-inspector-frozen-source.test.cjs`
  Expected: PASS.

  Commit: `feat: display formal attributes in explorer`

### Task 6: Verify the integrated current capability

**Files:**
- Modify only if verification exposes a defect in files already listed above.

- [ ] **Step 1: Run the targeted Python regression set**

  Run: `python -m pytest tests/service/test_formal_writes.py tests/service/test_typed_reviews.py tests/service/test_reviews.py tests/service/test_ontology_discovery.py tests/service/test_semantica_adapter.py tests/service/test_governance.py tests/service/test_ontology.py tests/service/test_neo4j_store.py tests/service/test_explorer.py -q`
  Expected: PASS.

- [ ] **Step 2: Run the repository's existing frontend test command**

  Run: `Get-ChildItem tests/service/*.test.cjs | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { throw "Node test failed: $($_.Name)" } }`
  Expected: PASS.

- [ ] **Step 3: Check scope and repository cleanliness**

  Run: `git diff --check` and `git status --short`.
  Confirm there is no new attribute database, migration framework, unit engine, standalone test script or unrelated refactor.

- [ ] **Step 4: Commit only any verification fixes**

  Commit: `fix: complete first-class attribute integration` (only when fixes were necessary).
