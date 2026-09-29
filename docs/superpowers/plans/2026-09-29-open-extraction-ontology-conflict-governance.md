# Open Extraction Ontology Conflict Governance Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make open extraction and cumulative ontology draft generation tolerate entity/relation/attribute name collisions without losing evidence, leaving empty drafts, or blocking non-conflicting ontology changes.

**Architecture:** Add a deep `DiscoveryVocabularyNormalizer` module between discovery candidates and Semantica induction. It owns canonical-name normalization, baseline vocabulary indexing, same-kind reuse, conflict quarantine, deterministic discovery-run fingerprints, and candidate bindings. The discovery route consumes its result, persists one idempotent discovery-run artifact, creates the governed draft and initial diff command atomically, and exposes a no-ontology-change finalization path; the OWL compiler remains the final kind-safety guard and existing pySHACL validation remains unchanged.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, RDFLib, Semantica 0.6.7 adapter contract, pySHACL, vanilla JavaScript, pytest, Playwright.

---

## File map

- Create `knowledge_service/services/discovery_vocabulary.py`: canonical-name, baseline registry, conflict normalization, read-only formal-vocabulary audit, fingerprints, candidate bindings, and generated-graph kind checks.
- Modify `knowledge_service/integrations/semantica_adapter.py`: reserve extracted entity-type names and route colliding predicates to discovery exceptions.
- Modify `knowledge_service/services/service.py`: load active and retired baseline class names before chunk discovery and pass them as extraction-level reservations.
- Modify `knowledge_service/services/ontology_discovery.py`: induce only accepted candidates, accept normalized mappings, validate generated OWL kinds, and materialize explicit skipped/conflict outcomes.
- Modify `knowledge_service/services/ontology_drafts.py`: add atomic create-plus-initial-command interface.
- Create `knowledge_service/repository/discovery_run_store.py`: insert-only immutable discovery-run snapshots and expected-status compare-and-swap transitions over the artifacts table.
- Modify `knowledge_service/repository/core.py`: expose discovery-run store forwarding methods and filter publication materialization by approved required operations in the publication transaction.
- Modify `knowledge_service/api/ontology_discovery.py`: create/read/finalize discovery runs, expose read-only formal-vocabulary audit, make draft creation idempotent and atomic, and return `draft`, `mapping_only`, or `diagnosed_no_change` results.
- Modify `knowledge_service/web/ontology-workbench.js`: show conflict diagnostics and handle all three discovery result kinds.
- Modify `knowledge_service/web/ontology-details.js` only if legacy discovery details need conflict/outcome rendering; do not duplicate workbench state logic.
- Create `tests/service/test_discovery_vocabulary.py`: focused module-interface tests.
- Modify `tests/service/test_semantica_adapter.py`: extraction reservation and exception tests.
- Modify `tests/service/test_ontology_discovery.py`: route, atomicity, mapping-only, materialization, and regression tests.
- Modify `tests/service/test_ontology_drafts.py`: atomic create-plus-command rollback tests.
- Modify `tests/service/test_ontology_workbench_ui.py`: conflict diagnostics and result-kind navigation tests.

## Test environment rule

The current shared virtual environment lacks the optional `semantica` package. Baseline is `130 passed, 18 failed, 11 skipped`, with all 18 failures caused by `ModuleNotFoundError: semantica`. New normalizer, route, and UI tests must inject a deterministic fake `_induce`/generator and must not depend on installing Semantica. Run Semantica-dependent legacy tests separately when the optional runtime becomes available; never change assertions merely to hide the missing dependency.

The following new Semantica-independent node IDs are the minimum proof set and must remain runnable as one explicit command (implementers may add more cases):

```powershell
& 'D:\workspace\knowledge_service\.venv\Scripts\python.exe' -m pytest `
  tests/service/test_discovery_vocabulary.py `
  tests/service/test_ontology_discovery.py::test_formal_vocabulary_audit_reports_cross_kind_names_without_candidates `
  tests/service/test_ontology_discovery.py::test_formal_vocabulary_audit_reports_dual_kind_iris_without_draft `
  tests/service/test_discovery_run_store.py `
  tests/service/test_semantica_adapter.py::test_open_fact_predicate_matching_entity_type_becomes_exception `
  tests/service/test_semantica_adapter.py::test_relation_attribute_collision_quarantines_both_predicates `
  tests/service/test_service.py::test_open_discovery_reserves_active_and_retired_baseline_classes `
  tests/service/test_ontology_discovery.py::test_collision_run_keeps_unrelated_candidates_in_draft `
  tests/service/test_ontology_discovery.py::test_all_conflict_input_creates_diagnosed_no_change_run_without_draft `
  tests/service/test_ontology_discovery.py::test_mapping_only_finalize_is_idempotent `
  tests/service/test_ontology_discovery.py::test_publication_materializes_only_approved_required_bindings `
  tests/service/test_ontology_discovery.py::test_discovery_run_lifecycle_rolls_back_with_failed_publication `
  tests/service/test_ontology_drafts.py::test_create_with_command_rolls_back_invalid_initial_diff `
  -q
```

### Task 1: Build the vocabulary normalization module

**Files:**
- Create: `knowledge_service/services/discovery_vocabulary.py`
- Create: `tests/service/test_discovery_vocabulary.py`

- [ ] **Step 1: Write failing canonical-name and same-kind merge tests**

Cover NFKC, trim, whitespace collapse, Unicode case-fold, stable candidate order, merged evidence refs, and mutually exclusive diagnostic counts.

```python
def test_canonical_name_normalizes_nfkc_whitespace_and_case():
    assert canonical_name('  Ａ  b  ') == 'a b'


def test_same_kind_candidates_merge_without_losing_evidence():
    result = DiscoveryVocabularyNormalizer().normalize([
        candidate('a', 'attribute', ' 发布日期 '),
        candidate('b', 'attribute', '发布日期'),
    ], baseline_turtle=None)
    assert [row['id'] for row in result.accepted_candidates] == ['a', 'b']
    assert result.diagnostics['merged_count'] == 1
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
& 'D:\workspace\knowledge_service\.venv\Scripts\python.exe' -m pytest tests/service/test_discovery_vocabulary.py -q
```

Expected: collection/import fails because `discovery_vocabulary` does not exist.

- [ ] **Step 3: Implement canonical names and immutable result types**

Use small dataclasses with JSON-safe conversion:

```python
@dataclass(frozen=True)
class NormalizationResult:
    accepted_candidates: tuple[dict, ...]
    conflicts: tuple[dict, ...]
    merged_groups: tuple[dict, ...]
    candidate_bindings: tuple[dict, ...]
    diagnostics: dict


def canonical_name(value):
    normalized = unicodedata.normalize('NFKC', str(value or ''))
    return ' '.join(normalized.split()).casefold()
```

Keep candidate payloads copied rather than mutated in place.

- [ ] **Step 4: Add failing baseline registry and conflict-matrix tests**

Test all required precedence cases:

- unique active same-kind alias reuses its IRI;
- class/property collisions quarantine the property and accept the class proposal;
- relation/attribute collision quarantines both;
- multilingual labels matching multiple IRIs produce `ambiguous_baseline_name`;
- explicit IRI hits a retired same-kind term and produces `retired_term_reuse_blocked`;
- explicit IRI with different kind produces `existing_term_kind_collision`;
- an explicit IRI whose local/label canonical name disagrees with the candidate name produces `candidate_iri_name_mismatch`;
- multiple candidate kinds that explicitly claim the same IRI are quarantined deterministically, even when their display names differ;
- a baseline IRI declared with two kinds raises `InvalidBaselineVocabulary`;
- cross-kind collision wins over the low-frequency-attribute counter.

- [ ] **Step 5: Implement baseline registry and deterministic normalization**

Index local name, every `rdfs:label`, and summary-compatible name into `dict[str, list[BaselineTerm]]`. Apply precedence exactly as the spec; never use `dict[key] = term` to silently discard ambiguous terms.

```python
class DiscoveryVocabularyNormalizer:
    def normalize(self, candidates, baseline_turtle=None):
        registry = build_baseline_registry(baseline_turtle)
        # validate explicit IRIs -> group cross-kind names -> merge same-kind
        # -> apply attribute frequency to remaining candidates
        return NormalizationResult(...)
```

- [ ] **Step 6: Add a read-only formal-vocabulary audit**

Write tests and implement `audit_formal_vocabulary(baseline_turtle)` with no candidates and no draft dependency. It returns every canonical name used by more than one governed kind, and every IRI declared as more than one governed kind, including the involved IRIs, kinds, names/labels, active/retired state, and stable audit codes. This function reports malformed existing vocabulary; unlike the generation guard it does not mutate or publish anything.

- [ ] **Step 7: Add and implement discovery fingerprint helpers**

Test that candidate order does not change the fingerprint, while base ontology, document version, candidate payload, normalizer version, generator contract, Semantica runtime version, attribute threshold, `request.name`, or any other generation-affecting request option does. The route must translate `InvalidBaselineVocabulary` to HTTP 422 with stable code `invalid_baseline_dual_kind`.

```python
def discovery_source_fingerprint(project_id, base_ontology_id, candidates,
                                 *, normalizer_version='v1',
                                 generator_contract='semantica-0.6.7',
                                 runtime_version, attribute_threshold,
                                 generation_options):
    payload = {...}
    return 'sha256:' + hashlib.sha256(canonical_json(payload).encode()).hexdigest()
```

Use the actual configured/runtime values; do not hard-code a future Semantica version. Canonicalize `generation_options` and include every option that can change induced Turtle or initial operations.

- [ ] **Step 8: Run focused tests**

Expected: all `test_discovery_vocabulary.py` tests pass.

- [ ] **Step 9: Commit**

```powershell
git add knowledge_service/services/discovery_vocabulary.py tests/service/test_discovery_vocabulary.py
git commit -m "feat: normalize discovery vocabulary conflicts"
```

### Task 2: Prevent entity/predicate collisions during open extraction

**Files:**
- Modify: `knowledge_service/integrations/semantica_adapter.py:189-302`
- Modify: `knowledge_service/services/service.py:413-431`
- Modify: `tests/service/test_semantica_adapter.py`
- Modify: `tests/service/test_service.py`

- [ ] **Step 1: Write failing reserved-name routing tests**

Call `_route_open_facts` directly with model-independent dictionaries/fakes:

```python
def test_open_fact_predicate_matching_entity_type_becomes_exception():
    accepted, exceptions = _route_open_facts(
        '用户实施发布行为',
        [entity('用户', '用户'), entity('发布行为', '用户行为')],
        [fact('用户', '用户行为', '发布行为', 'relation')])
    assert accepted == []
    assert exceptions[0]['reason_code'] == 'entity_property_name_collision'
```

Also test a later relation and attribute using the same canonical predicate: both become `relation_attribute_name_collision`, while unrelated facts stay accepted.

Add a service-level test proving the discovery chunk loop loads both active and retired baseline class local names/labels and forwards them to `SemanticaExtractor.discover(..., reserved_class_names=...)` before any chunk is extracted.

- [ ] **Step 2: Run focused adapter tests and verify failure**

Run only the new node IDs so the missing optional Semantica package does not affect the result.

- [ ] **Step 3: Pass reserved vocabulary to the prompt and post-processor**

Extend `SemanticaExtractor.discover(text, include_attributes=False, *, reserved_class_names=())`. Add normalized current-batch entity `proposed_type` values plus the caller-provided active/retired baseline class names to the LLM payload and prompt. In `_route_open_facts`, collect facts first, classify them, then quarantine colliding predicate groups. Reuse `canonical_name`; do not duplicate normalization rules.

In `knowledge_service/services/service.py`, resolve the current ontology before the discovery chunk loop, build the reserved set from active and retired class local names and labels, and pass the same immutable set into every chunk call. This is an early extraction filter only; the normalizer remains authoritative.

Ensure exceptions preserve subject/object/value/evidence and use stable codes:

- `entity_property_name_collision`
- `relation_attribute_name_collision`

- [ ] **Step 4: Run new and existing model-independent adapter tests**

Expected: new routing tests and existing `_route_open_facts` tests pass.

- [ ] **Step 5: Commit**

```powershell
git add knowledge_service/integrations/semantica_adapter.py knowledge_service/services/service.py tests/service/test_semantica_adapter.py tests/service/test_service.py
git commit -m "feat: reserve entity names in open fact extraction"
```

### Task 3: Make induction consume accepted candidates and enforce OWL kind safety

**Files:**
- Modify: `knowledge_service/services/discovery_vocabulary.py`
- Modify: `knowledge_service/services/ontology_discovery.py:362-510`
- Modify: `tests/service/test_discovery_vocabulary.py`
- Modify: `tests/service/test_ontology_discovery.py`

- [ ] **Step 1: Write a failing reproduction for the reported collision**

Use a fake Semantica generator result and candidates containing `用户行为` as class and attribute plus `属于` as relation and attribute. Assert normalization excludes conflicting properties and `_induce` returns Turtle where every IRI has exactly one governed kind.

- [ ] **Step 2: Write failing generated-graph invariant tests**

```python
def test_validate_generated_term_kinds_rejects_dual_declaration():
    with pytest.raises(GeneratedVocabularyConflict, match='one ontology kind'):
        validate_generated_term_kinds('''
          @prefix owl: <http://www.w3.org/2002/07/owl#> .
          <urn:x> a owl:Class, owl:DatatypeProperty .
        ''')
```

Add independent cases proving the guard also rejects:

- one IRI declared as more than one governed kind, including class/object-property and object/datatype-property combinations;
- `rdfs:subClassOf` whose subject or object is not a class;
- object-property `rdfs:domain` or `rdfs:range` targets that are not classes;
- datatype-property `rdfs:domain` targets that are not classes;
- datatype properties with zero, multiple, non-XSD, or unsupported datatype ranges (exactly one supported XSD datatype is required).

Each failure must raise `GeneratedVocabularyConflict` with a stable reason code suitable for the API's `generated_vocabulary_conflict` envelope.

- [ ] **Step 3: Run the two tests and verify failure**

Expected: missing guard/normalizer integration.

- [ ] **Step 4: Integrate accepted candidates into induction**

Keep `_induce` responsible for Semantica adaptation only. The route must call the normalizer before `_induce`; `_induce` must additionally call `validate_generated_term_kinds` before constructing `Ontology(turtle)` so bypass callers fail with a domain-specific error rather than a later domain/range exception.

When building lookup maps, use the normalizer-provided `reuse_iri` binding before generating `_iri(base, source)`. Do not create kind-prefixed IRIs.

- [ ] **Step 5: Preserve low-frequency and conflict outcomes separately**

Extend `_materialize_candidates` input to accept `candidate_outcomes`. Conflicts must be returned as skipped with `reason_code='ontology_term_conflict'`; low-frequency attributes use `low_frequency_attribute`. No conflict candidate may appear in provisional records.

- [ ] **Step 6: Run focused discovery tests**

Expected: reported collision reproduction passes; existing non-Semantica materialization tests remain green.

- [ ] **Step 7: Commit**

```powershell
git add knowledge_service/services/discovery_vocabulary.py knowledge_service/services/ontology_discovery.py tests/service/test_discovery_vocabulary.py tests/service/test_ontology_discovery.py
git commit -m "fix: quarantine cross-kind discovery terms before induction"
```

### Task 4: Create discovery runs and initial draft operations atomically

**Files:**
- Create: `knowledge_service/repository/discovery_run_store.py`
- Modify: `knowledge_service/repository/core.py`
- Modify: `knowledge_service/services/ontology_drafts.py:477-518,790-865`
- Modify: `knowledge_service/api/ontology_discovery.py:40-149`
- Create: `tests/service/test_discovery_run_store.py`
- Modify: `tests/service/test_ontology_drafts.py`
- Modify: `tests/service/test_ontology_discovery.py`

- [ ] **Step 1: Write failing immutable discovery-run store tests**

Test a dedicated `DiscoveryRunStore` over the existing `artifacts` table:

- deterministic run ID plus identical canonical payload/fingerprint returns the existing immutable snapshot;
- the same run ID with a different payload or fingerprint raises `DiscoveryRunConflict` after catching `sqlite3.IntegrityError`;
- creation is insert-only and never delegates to generic `Repository.save_artifact`, because that method is an upsert;
- lifecycle changes and terminal materialization outcomes use one expected-status compare-and-swap; an invalid or concurrent transition changes neither status nor outcomes.

Expose narrow repository forwarding methods for create/get/list/transition so callers never mutate discovery runs through the generic artifact upsert.

- [ ] **Step 2: Implement the run-specific store and repository forwarding methods**

Keep source/base/candidate/fingerprint/binding data immutable. Permit only the documented lifecycle/status and terminal outcome fields to advance through compare-and-swap transitions. Use the repository's current connection/transaction; do not create a second connection.

- [ ] **Step 3: Write failing atomic-create service tests**

Add `OntologyDrafts.create_with_command(...)` tests:

- valid diff creates draft and initial operations in one outer repository transaction;
- invalid diff raises and leaves no draft or operations;
- source/base changes inside the transaction raise stale errors without partial state.

- [ ] **Step 4: Implement the atomic interface**

```python
def create_with_command(self, project_id, base_ontology_id_or_none, source,
                        title, actor, *, source_context, summary, command):
    with self.repository._transaction():
        draft = self.create(...)
        return self.command(project_id, draft['id'], draft['revision'], command)
```

Rely on the repository's nested savepoints; do not add a second transaction implementation.

- [ ] **Step 5: Write failing discovery-run API tests**

Monkeypatch `_induce` with deterministic Turtle and verify:

- mixed conflict/non-conflict input returns `result_kind='draft'`, stores one `ontology_discovery_run`, creates one governed draft, and includes conflicts/outcomes;
- all-conflict input returns `diagnosed_no_change` and creates no governed/legacy empty draft;
- repeated requests with the same source fingerprint return the same run/draft;
- changed source, base, normalizer/generator/runtime version, threshold, name, or other generation input creates a new run whose `supersedes_run_id` points to the prior stale or closed run, without mutating that prior snapshot;
- forced failure in the initial command leaves neither run, draft, operation, nor legacy artifact.

- [ ] **Step 6: Implement discovery-run construction in the required order**

In `create_draft`, snapshot candidates/current base, normalize, and induce outside the write transaction. Then enforce this exact outer transaction order:

1. enter `Repository._transaction()` (`BEGIN IMMEDIATE`);
2. re-read the current base plus candidates and recompute the complete source fingerprint;
3. return the existing deterministic run before creating a draft when the immutable snapshot matches; otherwise resolve the latest stale/closed predecessor and set the new snapshot's `supersedes_run_id`;
4. create the governed draft and its initial command first through `create_with_command`, so compiled operation IDs exist;
5. derive each candidate binding's required and optional operation IDs from those compiled operations;
6. freeze/update draft source context so `source_context.publication_effects` contains only `discovery_run_id` plus a compact summary, never a second copy of bindings/outcomes;
7. insert the final immutable discovery-run snapshot through `DiscoveryRunStore`;
8. save the legacy compatibility artifact last, only when a governed draft exists.

Any exception in steps 1-8 must roll back the draft, operations, frozen effects, run, and compatibility artifact together. A different payload for the same deterministic ID is a `DiscoveryRunConflict`, never an update. Changed generation inputs produce a distinct deterministic ID and immutable successor run; they never rewrite the predecessor.

The immutable run snapshot is the sole source of truth for candidates, conflicts, bindings, and outcomes. Both governed draft source context and the legacy compatibility payload store only `discovery_run_id` and a compact summary.

- [ ] **Step 7: Add controlled error translation**

Map invalid generated vocabulary to HTTP 422 with stable code `generated_vocabulary_conflict`, invalid dual-kind baseline vocabulary to HTTP 422 `invalid_baseline_dual_kind`, discovery-run conflicts to 409, and source/base changes to 409. Confirm no raw “domain/range 只能用于...” escapes for known candidate collisions.

- [ ] **Step 8: Run store/service/API tests**

Expected: atomic rollback and idempotent run tests pass without importing Semantica.

- [ ] **Step 9: Commit**

```powershell
git add knowledge_service/repository/discovery_run_store.py knowledge_service/repository/core.py knowledge_service/services/ontology_drafts.py knowledge_service/api/ontology_discovery.py tests/service/test_discovery_run_store.py tests/service/test_ontology_drafts.py tests/service/test_ontology_discovery.py
git commit -m "feat: create discovery drafts atomically"
```

### Task 5: Implement mapping-only finalization and deterministic materialization

**Files:**
- Modify: `knowledge_service/services/discovery_vocabulary.py`
- Modify: `knowledge_service/services/ontology_discovery.py:35-129`
- Modify: `knowledge_service/repository/core.py::_apply_ontology_source_effects`
- Modify: `knowledge_service/api/ontology_discovery.py`
- Modify: `knowledge_service/api/ontology_drafts.py`
- Modify: `tests/service/test_ontology_discovery.py`

- [ ] **Step 1: Write failing mapping-only API tests**

Start with a baseline ontology containing an active class/relation/attribute. Candidates that uniquely reuse them should return:

```json
{
  "result_kind": "mapping_only",
  "run": {"status": "ready_to_finalize"},
  "draft": null
}
```

Test `POST /ontology-discovery/runs/{run_id}/finalize` for:

- successful materialization against the existing ontology without creating a new ontology version;
- idempotent retry;
- base change -> `stale_base` 409;
- document/candidate change or already-processed candidate -> `stale_source` 409;
- SHACL failure -> rollback, status remains `ready_to_finalize`;
- conflicts remain skipped with `ontology_term_conflict`.

- [ ] **Step 2: Add required/optional candidate bindings**

After the atomic initial diff is compiled, match candidate target IRIs to the generated operations:

- existing active term: no required operations;
- new class/relation: its `create_term` is required;
- new attribute: `create_term` plus datatype operation is required;
- parent/domain/range suggestions are optional.

Persist operation IDs in the run bindings. Publication effects include only records whose required operations survive the final approved overlay; validation remains the last check.

- [ ] **Step 3: Write failing read and lifecycle tests**

Cover `GET /ontology-discovery/runs`, `GET /ontology-discovery/runs/{run_id}`, `GET /ontology-discovery/audit`, and the discovery overview's latest-run summary. The audit endpoint must report current formal-vocabulary cross-kind canonical-name conflicts and dual-kind IRIs even when the project has no discovery candidates and no draft. Assert these terminal and intermediate paths:

- all-conflict/no-change creation ends at `diagnosed_no_change`;
- mapping-only creation is `ready_to_finalize`, then success is `finalized_no_change`;
- a governed run is `draft_created`, draft close transitions it to `closed`, and publication transitions it to `published`;
- draft close assigns `draft_closed` only to bindings that do not already have an outcome, preserving existing `ontology_term_conflict`, `low_frequency_attribute`, and other diagnostic outcomes;
- stale base/source outcomes use the documented stale statuses without destroying the immutable snapshot;
- a failed finalize, close, or publish transaction rolls back to the prior status.

- [ ] **Step 4: Implement finalize endpoint transaction**

Recompute source fingerprint and base inside `repository._transaction()`. Reuse `_materialize_candidates` and `_validated_materialization`, write records with the current ontology ID, update run status with expected-status CAS semantics, and never call ontology publication.

- [ ] **Step 5: Test and implement partial approval materialization in publication**

For a mixed draft, reject one required create-term operation and one optional domain operation. Publish and assert:

- candidate depending on rejected create-term is skipped;
- candidate whose optional domain was rejected is materialized;
- conflict candidate is skipped;
- unaffected candidate is materialized.

Modify `Repository._apply_ontology_source_effects` because this is the transaction that receives `prepared['operations']` from `OntologyDrafts.publish_preflight`. Build the approved operation-ID set from those final approved operations, read `discovery_run_id` from `source_context.publication_effects`, then load bindings and prior outcomes from the immutable run snapshot. Materialize a record only when every `required_operation_id` is approved. Missing/rejected/superseded required operations produce stable terminal skip reasons; rejected optional parent/domain/range operations do not gate materialization. Persist final materialized/skipped outcomes through the run's expected-status CAS and transition `draft_created -> published` in this same transaction, so publication rollback restores both prior status and prior terminal outcomes. Do not write authoritative terminal outcomes only to the legacy compatibility artifact.

- [ ] **Step 6: Wire draft close and run read APIs**

Use expected-status transitions for draft close (`draft_created -> closed`) and publication (`draft_created -> published`). In the close transaction, add `draft_closed` only for unresolved bindings while preserving every existing conflict/low-frequency outcome. Return immutable run snapshots plus current lifecycle metadata from the list/detail endpoints, expose the read-only formal-vocabulary audit, and include the latest run summary in the existing overview response.

- [ ] **Step 7: Run focused end-to-end service tests**

Expected: mapping-only and partial-approval cases pass.

- [ ] **Step 8: Commit**

```powershell
git add knowledge_service/services/discovery_vocabulary.py knowledge_service/services/ontology_discovery.py knowledge_service/repository/core.py knowledge_service/api/ontology_discovery.py knowledge_service/api/ontology_drafts.py tests/service/test_ontology_discovery.py
git commit -m "feat: finalize discovery mappings without ontology changes"
```

### Task 6: Expose diagnostics and no-change completion in the workbench

**Files:**
- Modify: `knowledge_service/web/ontology-workbench.js:6-201`
- Modify: `knowledge_service/web/ontology-details.js:126-143` only if the compatibility detail view is still reachable
- Modify: `tests/service/test_ontology_workbench_ui.py`

- [ ] **Step 1: Extend the Playwright API fixture**

Mock run list/detail, overview latest-run summary, draft, mapping-only, and diagnosed-no-change responses plus the finalize endpoint. Add assertions for conflict name, involved kinds, reason, evidence, lifecycle status, and diagnostic counts.

- [ ] **Step 2: Write failing result-navigation tests**

Verify:

- `draft` selects the governed draft and enters Design;
- `mapping_only` stays in Discovery, shows “沿用当前本体并提交知识”, and finalizes once confirmed;
- `diagnosed_no_change` stays in Discovery with diagnostics and does not unlock empty draft stages;
- 409 stale responses show refresh/reanalyze guidance.

- [ ] **Step 3: Render conflict metrics and cards**

Extend workbench state with `discoveryRun`. Render accepted/merged/low-frequency/conflict counts and conflict cards. Use `textContent`/the existing `el()` helper for all names and evidence; never interpolate untrusted evidence into `innerHTML`.

- [ ] **Step 4: Handle all create-draft result kinds**

Replace the unconditional `state.draftId = ...; setStage('design')` branch with explicit `result_kind` handling. Add the finalize button only for `ready_to_finalize` runs.

- [ ] **Step 5: Prevent invalid attribute range commands**

In the new-object flow, attributes emit `create_term`, `add_domain`, and `set_datatype`; they must not emit `add_range`. Relations keep domain/range, classes keep parents. Add a UI contract assertion.

- [ ] **Step 6: Run UI tests**

Run:

```powershell
& 'D:\workspace\knowledge_service\.venv\Scripts\python.exe' -m pytest tests/service/test_ontology_workbench_ui.py -q
```

Expected: all workbench UI tests pass, or skip only when the already-documented Playwright browser runtime is unavailable.

- [ ] **Step 7: Commit**

```powershell
git add knowledge_service/web/ontology-workbench.js knowledge_service/web/ontology-details.js tests/service/test_ontology_workbench_ui.py
git commit -m "feat: show discovery conflicts in ontology workbench"
```

### Task 7: Verify the complete flow and documented boundaries

**Files:**
- Modify: `tests/service/test_ontology_discovery.py`
- Modify: `tests/service/test_ontology_operations.py` only if an uncovered builder invariant needs a direct regression
- Modify: `docs/superpowers/specs/2026-09-29-open-extraction-ontology-conflict-governance-design.md` only for implementation-discovered corrections, never to weaken requirements

- [ ] **Step 1: Add the named project regression**

Create a fixture equivalent to `test_0928_开放` with:

- class/attribute: `用户行为`, `违规内容类别`, `通知渠道`;
- relation/attribute: `属于`, `禁止出现`;
- at least one unrelated class/relation/attribute.

Assert the unrelated terms form a draft, collisions are diagnosed, no IRI is dual-kind, and the request never raises the old domain/range exception.

- [ ] **Step 2: Run focused backend suites**

```powershell
& 'D:\workspace\knowledge_service\.venv\Scripts\python.exe' -m pytest tests/service/test_discovery_vocabulary.py tests/service/test_ontology_drafts.py tests/service/test_ontology_operations.py -q
```

Expected: pass.

- [ ] **Step 3: Run the explicit Semantica-independent proof set, then broader discovery/adapter tests**

First run the exact new-test node-ID command in the Test environment rule. It must pass independently of the optional runtime. Then run:

```powershell
& 'D:\workspace\knowledge_service\.venv\Scripts\python.exe' -m pytest tests/service/test_ontology_discovery.py tests/service/test_semantica_adapter.py -q
```

If Semantica is still unavailable, report only the pre-existing `ModuleNotFoundError` cases and separately demonstrate that every newly added test passes.

- [ ] **Step 4: Run SHACL and governance regressions**

```powershell
& 'D:\workspace\knowledge_service\.venv\Scripts\python.exe' -m pytest tests/service/test_ontology.py tests/service/test_shacl_review.py tests/service/test_governance.py -q
```

Expected: pass; no SHACL behavior changes.

- [ ] **Step 5: Run the full test suite**

```powershell
& 'D:\workspace\knowledge_service\.venv\Scripts\python.exe' -m pytest -q
```

Record exact pass/fail/skip totals. Do not claim a clean suite if optional Semantica remains missing.

- [ ] **Step 6: Inspect final diff and invariants**

Run:

```powershell
git diff --check
git status --short
git log --oneline --decorate -8
```

Confirm no SQLite, `.env`, cache, generated database, or unrelated user file is tracked.

- [ ] **Step 7: Commit any final regression-only changes**

```powershell
git add tests/service/test_ontology_discovery.py tests/service/test_ontology_operations.py
git commit -m "test: cover cumulative discovery name collisions"
```

## Completion checklist

- [ ] Known cross-kind candidates are quarantined before Semantica induction.
- [ ] Source evidence and stable diagnostic codes remain queryable in a discovery run.
- [ ] Non-conflicting candidates continue into a governed draft.
- [ ] All-conflict input creates no empty draft.
- [ ] Initial draft plus operations are atomic.
- [ ] Mapping-only candidates can be finalized against the current ontology without publishing a no-op version.
- [ ] Published or finalized records obey the final OWL/SHACL graph.
- [ ] Workbench explicitly handles draft, mapping-only, diagnosed-no-change, and stale outcomes.
- [ ] No IRI is emitted as more than one governed OWL term kind.
- [ ] SWRL remains outside this implementation.
