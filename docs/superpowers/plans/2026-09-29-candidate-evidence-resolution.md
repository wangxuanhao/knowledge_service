# Candidate Evidence Resolution Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every normal discovery candidate open its complete, version-pinned source chunk on demand, while clearly distinguishing exact, recovered, chunk-only, and unlocated evidence.

**Architecture:** Keep the candidate-mindmap response lightweight and put all provenance decisions behind one assertion-evidence interface in `services/evidence.py`. Resolve the immutable document version first, then the historical chunk record by `chunk_id`; never reuse assertion coordinates as chunk bounds because current assertions store exact evidence coordinates while legacy assertions store chunk coordinates. Normal entity/relation/attribute candidates lazy-load this interface; evidence exceptions remain explicitly non-resolvable because the current master intentionally does not create assertions for them.

**Tech Stack:** Python 3.12, FastAPI, SQLite repository records/assertions, vanilla JavaScript DOM APIs, pytest, Playwright.

---

## File structure

- Modify `knowledge_service/services/evidence.py`: add the deep assertion-evidence resolver and adapt the existing formal-record evidence response through the same resolver.
- Modify `knowledge_service/api/evidence.py`: expose the project-scoped read-only assertion evidence route.
- Modify `knowledge_service/services/ontology_discovery.py`: emit stable lightweight source references and truncation metadata.
- Modify `knowledge_service/web/ontology-workbench.js`: lazy-load and render complete source chunks with stale-request cancellation.
- Modify `knowledge_service/web/evidence-inspector.js`: make the existing safe historical-source dialog reusable by candidate evidence.
- Modify `knowledge_service/web/candidate-mindmap.js`: label embedded text as a preview instead of complete evidence.
- Create `tests/service/test_assertion_evidence.py`: resolver and HTTP contract tests.
- Modify `tests/service/test_ontology_discovery.py`: candidate source-reference contract tests.
- Modify `tests/service/test_ontology_workbench_ui.py`: real-browser candidate evidence workflow tests.
- Modify `tests/service/evidence-inspector-frozen-source.test.cjs`: reusable source-dialog projection contract.

### Task 1: Assertion-level evidence resolver and route

**Files:**
- Create: `tests/service/test_assertion_evidence.py`
- Modify: `knowledge_service/services/evidence.py`
- Modify: `knowledge_service/api/evidence.py`

- [ ] **Step 1: Write failing resolver tests**

Create fixtures containing a fixed document version, its historical chunk record, and project-scoped assertions. Cover these behaviors independently:

```python
def test_exact_assertion_returns_complete_chunk_and_exact_highlight(tmp_path): ...
def test_legacy_entity_after_character_500_is_recovered_inside_chunk(tmp_path): ...
def test_duplicate_legacy_entity_degrades_to_chunk_location(tmp_path): ...
def test_legacy_relation_recovers_unique_subject_object_span(tmp_path): ...
def test_legacy_attribute_prefers_attribute_evidence(tmp_path): ...
def test_missing_pinned_version_returns_unlocated_without_current_fallback(tmp_path): ...
def test_source_hash_unavailable_does_not_make_resolved_legacy_chunk_incomplete(tmp_path): ...
def test_source_hash_mismatch_marks_integrity_incomplete(tmp_path): ...
def test_ambiguous_chunk_history_returns_unlocated(tmp_path): ...
def test_invalid_chunk_bounds_return_unlocated(tmp_path): ...
def test_current_document_revision_does_not_replace_pinned_source(tmp_path): ...
def test_formal_and_assertion_evidence_share_location_mode_and_version(tmp_path): ...
def test_assertion_evidence_is_project_scoped(tmp_path): ...
```

Assert the public shape:

```python
assert result['chunk']['text'] == complete_chunk
assert result['location']['mode'] in {'exact', 'recovered_in_chunk', 'chunk', 'unlocated'}
assert result['integrity']['source_hash_status'] in {'matched', 'mismatched', 'unavailable'}
assert [warning['code'] for warning in result['integrity']['warnings']] == expected_codes
```

`integrity.complete` means the pinned document version and historical chunk were resolved and the chunk text matches the document range. A missing optional `source_hash` produces `source_hash_unavailable` but does not by itself set `complete=false`.

- [ ] **Step 2: Run the resolver tests and verify RED**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_assertion_evidence.py -q
```

Expected: FAIL because `resolve_assertion_evidence` and the GET route do not exist.

- [ ] **Step 3: Implement the minimal resolver**

Add this external interface:

```python
def resolve_assertion_evidence(repository, project_id, assertion_id):
    """Resolve one assertion to its immutable document version and complete chunk."""
```

Implementation rules:

1. Read with `repository.get_assertion(project_id, assertion_id)` so cross-project IDs remain 404.
2. Read the immutable document with `repository.get_record_version(project_id, document_version_id)`; never fall back to the current document.
3. Read `repository.history(project_id, chunk_id)` and choose the chunk whose `source_id` equals the assertion document and whose `recorded_at` matches the pinned document version. A sole source-matching historical chunk may be accepted for legacy data, with a stable warning.
4. Take chunk bounds only from chunk metadata. Validate `0 <= start < end <= len(document_text)` and `chunk.text == document_text[start:end]`.
5. Treat stored coordinates as `exact` only when the pinned document substring equals `quote` and the assertion is not recognizably legacy chunk-level evidence.
6. For a legacy full-chunk assertion, recover a unique entity mention, a unique `attribute_evidence`, or a unique minimal subject/object relation span inside the chunk. Never choose the first of multiple matches.
7. Return `chunk` when the complete chunk is valid but a unique highlight is unavailable, and `unlocated` when the pinned version/chunk cannot be verified.
8. Compute warning objects in deterministic pipeline order; do not sort user-facing messages.

Route assertion-backed origins inside `evidence(service, project_id, record_id, scope)` through the same private resolver. Adapt the result to its existing `documents[]` response shape and full-document `before/highlight/after` fields, while exposing the same `exact`, `recovered_in_chunk`, `chunk`, or `unlocated` mode as the assertion endpoint. Keep the existing POST route and top-level formal response compatible. Add a parity test proving both entry points return the same pinned version and location mode for one assertion.

- [ ] **Step 4: Add and test the FastAPI route**

Add:

```python
@app.get('/api/projects/{p}/assertions/{assertion_id}/evidence')
def assertion_evidence(p: str, assertion_id: str):
    return resolve_assertion_evidence(service.repository, p, assertion_id)
```

Test 200 responses for resolved and partial provenance, plus stable 404 behavior for missing and cross-project assertions.

- [ ] **Step 5: Run backend evidence tests and verify GREEN**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_assertion_evidence.py tests/service/test_evidence.py -q
```

Expected: PASS with no new warnings beyond the existing Starlette deprecation warning.

- [ ] **Step 6: Commit the backend evidence slice**

```powershell
git add tests/service/test_assertion_evidence.py knowledge_service/services/evidence.py knowledge_service/api/evidence.py
git commit -m "fix: resolve candidate assertions to source chunks"
```

### Task 2: Lightweight candidate source references

**Files:**
- Modify: `tests/service/test_ontology_discovery.py`
- Modify: `knowledge_service/services/ontology_discovery.py`
- Modify: `knowledge_service/web/candidate-mindmap.js`

- [ ] **Step 1: Write failing candidate-mindmap contract tests**

First add a service/API regression where the current document has been revised after ingestion and its current version ID differs from the assertion's pinned version. Then extend `_candidate_mindmap` tests to assert:

```python
source = result['nodes'][0]['sources'][0]
assert source['assertion_id'] == candidate['id']
assert source['document_version_id'] == assertion['document_version_id']
assert source['evidence_preview'] == candidate['evidence'][:500]
assert source['evidence_preview_truncated'] is True
assert result['nodes'][0]['source_count'] == 11
assert result['nodes'][0]['sources_truncated'] is True
```

Also assert deterministic source ordering and that exception sources have `assertion_id is None` and `resolvable is False`.

- [ ] **Step 2: Run the mindmap tests and verify RED**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_ontology_discovery.py -q
```

Expected: FAIL on the missing lightweight-reference fields.

- [ ] **Step 3: Implement one shared source-reference helper**

Bulk-load project assertions once while `_candidates()` flattens the current document metadata and index them by assertion ID. Preserve the candidate's existing `document_version_id`, because downstream draft stale-source checks and materialization identity deliberately use the current document version. Add distinct provenance-only fields such as `assertion_id`, `assertion_document_version_id`, `assertion_chunk_id`, `assertion_start_char`, and `assertion_end_char` from the assertion row. Source references must use those assertion-prefixed fields, never the current-document field. If a normal historical candidate has no matching assertion, preserve it for review but mark the source `resolvable=false`. Exceptions intentionally have no assertion and remain unresolved.

Replace the four duplicated entity/relation/attribute/exception source dictionaries with a private helper. For normal candidates with a matching assertion return its pinned fields and a preview explicitly marked as truncated. For exceptions return no assertion ID and `resolvable=false`.

Before returning each aggregate:

- sort sources by document title, document ID, chunk start, and assertion ID;
- expose at most 10 source references;
- return the full `source_count` and `sources_truncated` flag;
- retain a compatibility `evidence` preview for the legacy candidate-mindmap page only if required, always with `evidence_preview_truncated` metadata.

- [ ] **Step 4: Make the legacy candidate map call the text a preview**

Render `evidence_preview` and label it “来源预览”; when truncated, tell the user to open the candidate in the ontology workbench for the complete slice. Do not add a second resolver or viewer to this legacy graph.

- [ ] **Step 5: Run discovery and static frontend tests**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_ontology_discovery.py tests/service/test_frontend_static_contract.py tests/test_web_ui_contract.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the lightweight source contract**

```powershell
git add tests/service/test_ontology_discovery.py knowledge_service/services/ontology_discovery.py knowledge_service/web/candidate-mindmap.js
git commit -m "fix: expose candidate source references"
```

### Task 3: Human-readable lazy evidence workflow

**Files:**
- Modify: `tests/service/test_ontology_workbench_ui.py`
- Modify: `tests/service/evidence-inspector-frozen-source.test.cjs`
- Modify: `knowledge_service/web/ontology-workbench.js`
- Modify: `knowledge_service/web/evidence-inspector.js`

- [ ] **Step 1: Write failing browser workflow tests**

Update the browser mock so a candidate contains an assertion-backed source and the new GET returns a chunk whose highlighted term starts after character 500. Test that:

```python
page.click('[data-candidate-id="c1"]')
page.wait_for_selector('[data-evidence-location="recovered_in_chunk"]')
assert '第 500 字之后的候选词' in page.locator('[data-evidence-chunk]').inner_text()
assert page.locator('[data-evidence-chunk] mark').inner_text() == '第 500 字之后的候选词'
```

Add separate cases for `chunk`, `unlocated`, one-source failure among multiple sources, and rapid candidate switching where the stale response must not replace the new inspector.

Assert the initial candidate-mindmap load does not request `/assertions/.../evidence`.

- [ ] **Step 2: Write the failing reusable-dialog contract**

Extend the Node test so `frozenSourceDocument` accepts candidate-specific `reason` and full historical text without changing the existing answer-evidence projection.

- [ ] **Step 3: Run the frontend tests and verify RED**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_ontology_workbench_ui.py -q
node --test tests/service/evidence-inspector-frozen-source.test.cjs
```

Expected: FAIL because candidate evidence is still rendered from the embedded 500-character preview.

- [ ] **Step 4: Implement lazy source-card loading**

In `ontology-workbench.js`:

1. Render candidate structure and source skeletons synchronously.
2. Create a candidate-evidence `AbortController`; abort it on another candidate click, discovery-kind change, project change, and discovery reload.
3. Fetch visible normal sources by assertion ID and update each card independently.
4. Render title/version, chunk ID, absolute chunk range, integrity warning, and the complete chunk with DOM text nodes. Add a `<mark>` only for `exact` and `recovered_in_chunk`.
5. Use human labels: “精确证据位置”, “历史切片内恢复定位”, “仅保存切片级位置”, and “历史来源无法定位”.
6. Show `source_count` separately from the number of visible source cards so ten displayed sources are not mistaken for all sources.
7. Never place source text into `innerHTML`.

Also extend the formal evidence inspector's mode-label mapping for `exact`, `recovered_in_chunk`, and `chunk`, while retaining its legacy labels for non-assertion-backed records.

- [ ] **Step 5: Reuse the historical source viewer on explicit click**

When the user clicks “查看完整历史原文”, request the existing `/records/{document_id}/history` route, choose exactly `document.version_id`, and pass that immutable text and the resolved highlight range into the generalized safe source dialog. Do not fall back to the latest document version.

- [ ] **Step 6: Run frontend tests and verify GREEN**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_ontology_workbench_ui.py tests/service/test_frontend_static_contract.py tests/test_web_ui_contract.py -q
node --test tests/service/evidence-inspector-frozen-source.test.cjs
```

Expected: PASS.

- [ ] **Step 7: Commit the candidate interaction**

```powershell
git add tests/service/test_ontology_workbench_ui.py tests/service/evidence-inspector-frozen-source.test.cjs knowledge_service/web/ontology-workbench.js knowledge_service/web/evidence-inspector.js
git commit -m "fix: show complete candidate source chunks"
```

### Task 4: End-to-end verification against `test_0928_开放`

**Files:**
- Modify only if a verified defect is found in the files listed above.

- [ ] **Step 1: Run all targeted automated tests**

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_assertion_evidence.py tests/service/test_evidence.py tests/service/test_ontology_discovery.py tests/service/test_ontology_workbench_ui.py tests/service/test_frontend_static_contract.py tests/test_web_ui_contract.py -q
node --test tests/service/evidence-inspector-frozen-source.test.cjs
```

Expected: all targeted tests pass; record any unrelated pre-existing failure separately.

- [ ] **Step 2: Verify the real historical project through the service interface**

For project ID `39154db6-60bd-41da-bfb7-7ce225a1915f`, choose a source whose candidate mention occurs after character 500. Verify:

- candidate-mindmap returns an assertion ID without downloading the complete chunk;
- assertion evidence returns all 1800 characters of chunk 0 or the full stored length of chunk 1;
- the candidate is visible/highlighted or explicitly marked chunk-only;
- document version and chunk absolute range match the stored historical records;
- `source_hash_status=unavailable` is reported without losing the resolvable provenance chain.

- [ ] **Step 3: Inspect the final diff and working tree**

```powershell
git diff --check
git status --short
git diff --stat 85e3a37..HEAD
```

Confirm there is no database migration, no history rewrite, and no changes to extraction, ontology drafts, or publishing logic. Preserve runtime changes to `data/service/knowledge.sqlite-shm` and `data/service/knowledge.sqlite-wal` rather than including them in commits.

- [ ] **Step 4: Run final review**

Use @requesting-code-review against the complete implementation, then use @verification-before-completion before reporting the result.
