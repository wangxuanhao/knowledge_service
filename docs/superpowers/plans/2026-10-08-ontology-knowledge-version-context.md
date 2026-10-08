# Ontology–Knowledge Version Context Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ontology releases and per-record revisions visibly and consistently linked across ontology history, the knowledge ledger, record history, graph, and mind map without inventing a global knowledge-base version.

**Architecture:** PostgreSQL remains the source of truth: repository ontology reads resolve one authoritative `version`/`version_reused`, `KnowledgeService.scoped` applies ontology ownership before endpoint-integrity filtering, and a focused `VersionContextService` calculates aggregate impact without returning record bodies. The vanilla-JavaScript UI keeps one non-persistent `wb.versionContext` that every version-aware view reads and updates; record revisions remain immutable `rN` entries linked by `ontology_id`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, repository SQL adapters for PostgreSQL/SQLite tests, vanilla JavaScript, pytest, Playwright, Docker Compose.

---

## File map and boundaries

- `knowledge_service/models.py`: validates the public three-state ontology scope contract only.
- `knowledge_service/repository/core.py`: resolves authoritative ontology display versions and exposes one narrow historical-association aggregate.
- `knowledge_service/services/service.py`: applies ontology ownership to the current temporal record set before dangling-edge cleanup.
- `knowledge_service/services/version_context.py`: new orchestration boundary for one ontology release's current, migration, and historical counts.
- `knowledge_service/services/reclassify.py`: consumes repository-resolved version numbers; keeps migration policy unchanged.
- `knowledge_service/api/knowledge.py`: thin route wiring for ontology history and knowledge context.
- `knowledge_service/web/workbench.js`: owns the non-persistent cross-view `wb.versionContext`, project reset, and navigation helpers.
- `knowledge_service/web/ontology-model.js`, `ontology-model-panel.js`, `ontology-model.css`: show current/viewed ontology context and related knowledge in history details.
- `knowledge_service/web/records-view.js`, `workspace.css`: ontology filter, ownership/status labels, and `修订 rN` ledger presentation.
- `knowledge_service/web/record-dialog.js`, `record-dialog.css`: immutable revision timeline and read-only historical selection.
- `knowledge_service/web/workspace.js`: forwards the shared ontology scope into graph/mind-map calls and renders the active scope banner.
- `knowledge_service/web/index.html`: adds stable, accessible hosts for the filter, context banners, and timeline.
- `tests/service/test_ontology_version_context.py`: focused repository/service/API contract tests for resolved versions and aggregate context.
- `tests/service/test_api.py`: Scope request validation and query/graph integration tests.
- `tests/service/test_reclassify.py`: regression coverage that reclassification labels consume authoritative versions.
- `tests/service/test_ontology_version_management.py`: history response and reused structural version tests.
- `tests/service/test_records_ledger_ui.py`: ledger filter, wording, failure, and navigation browser tests.
- `tests/service/test_frontend_retrieval_flow.py`: graph/mind-map scope propagation and project-reset browser tests.
- `tests/service/ontology-details.test.cjs`: deterministic frontend version-label helper coverage where DOM is unnecessary.
- `docs/2026-10-01-本体工作台操作手册.md`: user-facing version semantics and before/after/migration examples.
- `docs/assets/ontology-version-context.png`, `docs/assets/ledger-version-filter.png`, `docs/assets/record-revision-timeline.png`: refreshed Chinese-labelled screenshots captured from the running service.

Do not modify or stage the user's existing `docker/postgres/initdb/10-app-role.sh` or `.gitattributes` changes.

### Task 1: Make ontology display versions authoritative at the repository boundary

**Files:**
- Modify: `knowledge_service/repository/core.py:1008-1027,1756-1761`
- Modify: `knowledge_service/api/knowledge.py:155-176`
- Modify: `knowledge_service/services/reclassify.py:130-182,254-262`
- Test: `tests/service/test_ontology_version_management.py`
- Test: `tests/service/test_reclassify.py`

- [ ] **Step 1: Write failing tests for stored, legacy, and reused structure versions**

Add tests that create three ontology rows where insertion/`seq` order intentionally differs from `created_at, id` order, the first release lacks `metadata.version`, and the next two have `metadata.version == 2`. Assert the public order and flags use `created_at, id` and only the later same-version publication is marked as the annotation revision:

```python
versions = repo.list_ontologies(project_id)
assert [(v['version'], v['version_reused']) for v in versions] == [
    (1, False), (2, False), (2, True),
]

response = client.get(f'/api/projects/{project_id}/ontologies')
assert [(v['version'], v['version_reused']) for v in response.json()['versions']] == [
    (1, False), (2, False), (2, True),
]
```

Also assert `Reclassify.plan()['current_version']` contains `本体 v2`, not `v3`. Assert every reused-version list label contains `short_id(ontology_id)` so two immutable publications sharing `v2` remain distinguishable.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_ontology_version_management.py tests/service/test_reclassify.py -q -k "version_reused or authoritative_version"
```

Expected: FAIL because `list_ontologies` does not return `version_reused` and the API/reclassifier still enumerate rows.

- [ ] **Step 3: Implement one resolver inside `Repository`**

Add a private helper and call it from `list_ontologies`:

```python
@staticmethod
def _resolve_ontology_versions(rows):
    resolved = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        item = dict(row)
        stored = (item.get('metadata') or {}).get('version')
        try:
            version = int(stored)
            if version < 1:
                raise ValueError
        except (TypeError, ValueError):
            version = index
        item['version'] = version
        item['version_reused'] = version in seen
        seen.add(version)
        resolved.append(item)
    return resolved
```

Change the ontology query to the specified stable `ORDER BY created_at, id`; do not assume `seq` is equivalent. Make `_current_ontology_version` resolve the last row from this same ordered sequence rather than maintaining a second rule.

- [ ] **Step 4: Remove row-index numbering from downstream code**

In `ontology_history`, forward `item['version']` and `item['version_reused']`; in `Reclassify._version_labels`, build labels from those fields and append `· 标注修订 · {ontology_id短标识}` only to a row whose own `version_reused` is true. The original structure publication for that number remains plain `本体 vN`; where a UI list can show more than one immutable release with the same `vN`, append the short ID to every colliding list item so labels are unique. Do not change migration decisions.

- [ ] **Step 5: Re-run focused tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 6: Commit only Task 1 files**

```powershell
git add knowledge_service/repository/core.py knowledge_service/api/knowledge.py knowledge_service/services/reclassify.py tests/service/test_ontology_version_management.py tests/service/test_reclassify.py
git commit -m "feat: unify ontology version numbering"
```

### Task 2: Add the validated ontology ownership Scope

**Files:**
- Modify: `knowledge_service/models.py:65-70`
- Modify: `knowledge_service/services/service.py:334-362`
- Test: `tests/service/test_api.py`

- [ ] **Step 1: Add failing validation tests for all legal and illegal combinations**

Cover `all/null`, `ids/non-empty`, and `unknown/null`, plus these 422 cases:

```python
@pytest.mark.parametrize('body', [
    {'ontology_scope': 'all', 'ontology_ids': ['ontology-a']},
    {'ontology_scope': 'ids', 'ontology_ids': None},
    {'ontology_scope': 'ids', 'ontology_ids': []},
    {'ontology_scope': 'unknown', 'ontology_ids': ['ontology-a']},
])
def test_query_rejects_inconsistent_ontology_scope(client, project_id, body):
    response = client.post(
        f'/api/projects/{project_id}/records/query', json=body)
    assert response.status_code == 422
```

Assert duplicate IDs normalize to first-seen order, and defaults preserve the old result set.

- [ ] **Step 2: Add failing service tests for exact IDs, unknown IDs, and endpoint integrity**

Create two project ontologies, records bound to each, one missing `ontology_id`, one bound to another project's ID, and relations/attributes whose subject is filtered out. Assert:

```python
ids_rows = service.scoped(project_id, {
    'ontology_scope': 'ids', 'ontology_ids': [old_id]})
assert {row['ontology_id'] for row in ids_rows} == {old_id}
assert not any(row['id'] == 'dangling-relation' for row in ids_rows)

unknown = service.scoped(project_id, {
    'ontology_scope': 'unknown', 'ontology_ids': None})
assert {row['id'] for row in unknown} == {'missing-id', 'foreign-id'}
```

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_api.py -q -k "ontology_scope"
```

Expected: FAIL because Scope forbids the new fields.

- [ ] **Step 4: Implement Pydantic validation**

Extend `Scope` with:

```python
ontology_scope: Literal['all', 'ids', 'unknown'] = 'all'
ontology_ids: list[str] | None = None

@model_validator(mode='after')
def check_ontology_scope(self):
    if self.ontology_scope == 'ids':
        ids = list(dict.fromkeys(self.ontology_ids or []))
        if not ids or any(not item for item in ids):
            raise ValueError('ontology_scope=ids 需要非空 ontology_ids')
        self.ontology_ids = ids
    elif self.ontology_ids is not None:
        raise ValueError('只有 ontology_scope=ids 可以传 ontology_ids')
    return self
```

- [ ] **Step 5: Filter after soft-delete and before endpoint cleanup**

In `KnowledgeService.scoped`, preserve old callers by defaulting to `all`. For `ids`, keep rows whose `ontology_id` is selected. For `unknown`, obtain the project's valid ontology IDs once and retain missing or non-project IDs. Then compute `entity_ids` and remove dangling relations/attributes exactly as today.

- [ ] **Step 6: Re-run API and graph tests**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_api.py -q -k "ontology_scope or graph"
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```powershell
git add knowledge_service/models.py knowledge_service/services/service.py tests/service/test_api.py
git commit -m "feat: filter knowledge by ontology scope"
```

### Task 3: Add ontology knowledge-context aggregation and API

**Files:**
- Create: `knowledge_service/services/version_context.py`
- Modify: `knowledge_service/repository/core.py:303-322,696-757`
- Modify: `knowledge_service/api/knowledge.py:155-190`
- Test: `tests/service/test_ontology_version_context.py`

- [ ] **Step 1: Write failing aggregate tests with record-count-weighted migration groups**

Create current records of all kinds, superseded revisions, migrated-away records, and mock `Reclassify.plan()` with one migratable group of `record_count=4` and one blocked group of `record_count=3`. Assert the complete shape:

```python
assert context == {
    'ontology': {
        'id': old_id, 'version': 1, 'is_current': False,
        'created_at': old_created_at,
    },
    'current_bound': {
        'total': 3,
        'by_kind': {'entity': 1, 'relation': 1, 'attribute': 1},
    },
    'migration': {'pending': 4, 'blocked': 3},
    'history': {
        'revision_count': 7, 'record_count': 5, 'migrated_away': 2,
    },
}
```

Add separate tests for current ontology (`0/0` without planning), plan failure (`None/None`), unknown ontology, and cross-project ontology (`KeyError`/404).

Add a filter-order test where a relation/attribute is bound to the selected ontology but its subject entity is bound to another ontology; the selected version's `current_bound` must exclude those dangling records, matching `/records/query` with the same ontology scope. Add a repository spy/concurrency fixture that verifies ontology list, current rows, migration plan reads, and history aggregate execute inside one `read_snapshot()` context.

- [ ] **Step 2: Run the new test module and verify RED**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_ontology_version_context.py -q
```

Expected: collection/import failure because `VersionContextService` does not exist.

- [ ] **Step 3: Add a narrow repository history method**

Expose a public `read_snapshot()` context manager backed by the repository's existing re-entrant lock and `_transaction()`. `VersionContextService.get()` must hold this context around ontology-list, current-record, migration-plan, and history-aggregate reads so they share one repository connection/transaction snapshot.

Expose `ontology_history_stats(project_id, ontology_id, current_rows)` which reads only `id`, `version`, and payload/decoded `ontology_id` from `record_versions`, then returns:

```python
{
    'revision_count': number_of_matching_revisions,
    'record_count': number_of_distinct_matching_ids,
    'migrated_away': number_of_matching_ids_whose_current_revision_has_a_different_ontology_id,
}
```

For `migrated_away`, compare each historically matching record ID with the current row's `ontology_id`; never compare record IDs to ontology IDs. Keep JSON extraction adapter-neutral by decoding the selected payload through the repository's existing row conversion; do not return record text from this method.

- [ ] **Step 4: Implement `VersionContextService`**

Use constructor injection so migration failures can be tested:

```python
class VersionContextService:
    KINDS = ('entity', 'relation', 'attribute')

    def __init__(self, service, reclassify=None):
        self.service = service
        self.repository = service.repository
        self.reclassify = reclassify or Reclassify(service)

    def get(self, project_id, ontology_id):
        with self.repository.read_snapshot():
            versions = self.repository.list_ontologies(project_id)
            selected = next((v for v in versions if v['id'] == ontology_id), None)
            if selected is None:
                raise KeyError(ontology_id)
            all_current = self.service.scoped(project_id, {
                'kinds': list(self.KINDS),
                'ontology_scope': 'all', 'ontology_ids': None,
            })
            bound = self.service.scoped(project_id, {
                'kinds': list(self.KINDS),
                'ontology_scope': 'ids', 'ontology_ids': [ontology_id],
            })
            # Count bound rows, migration groups, and history in this snapshot.
```

The scoped `ids` call is authoritative for `current_bound`, so ownership filtering happens before relation/attribute endpoint cleanup. Use `all_current` only to decide `migrated_away` in `ontology_history_stats`. Migration counts must sum each group's `record_count`, not group count. Return `None` values on `Reclassify.plan()` failure and do not swallow project/ontology ownership errors. The current-ontology shortcut (`0/0`) and migration-plan read must stay inside the same `read_snapshot()` block.

- [ ] **Step 5: Wire the thin GET route**

Instantiate/call the service from `install`:

```python
@router.get('/api/projects/{project_id}/ontologies/{ontology_id}/knowledge-context')
def ontology_knowledge_context(project_id: str, ontology_id: str):
    return VersionContextService(service).get(project_id, ontology_id)
```

- [ ] **Step 6: Run aggregate, API, and reclassification tests**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_ontology_version_context.py tests/service/test_reclassify.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```powershell
git add knowledge_service/services/version_context.py knowledge_service/repository/core.py knowledge_service/api/knowledge.py tests/service/test_ontology_version_context.py
git commit -m "feat: expose ontology knowledge context"
```

### Task 4: Introduce one non-persistent frontend version context

**Files:**
- Modify: `knowledge_service/web/workbench.js:1-60`
- Modify: `knowledge_service/web/ontology-model.js:536-563`
- Modify: `knowledge_service/web/index.html`
- Test: `tests/service/test_frontend_retrieval_flow.py`

- [ ] **Step 1: Add failing browser tests for state changes and project reset**

Assert that previewing a historical ontology produces:

```javascript
window.wb.versionContext === {
  ontologyScope: 'ids',
  ontologyIds: [oldOntologyId],
  ontologyVersion: 1,
  source: 'ontology-history'
}
```

Then switch projects and assert `all/null/null/null`; reload the page and assert the context is not persisted.

- [ ] **Step 2: Run the focused browser test and verify RED**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_frontend_retrieval_flow.py -q -k "version_context"
```

Expected: FAIL because `wb.versionContext` is undefined.

- [ ] **Step 3: Add state helpers to `workbench.js`**

Define the initial value and mutation API once:

```javascript
const emptyVersionContext=()=>({
  ontologyScope:'all', ontologyIds:null,
  ontologyVersion:null, source:null,
});
wb.versionContext=emptyVersionContext();
wb.setVersionContext=(next)=>{
  wb.versionContext={...emptyVersionContext(),...next};
  document.dispatchEvent(new CustomEvent('version-context:changed', {
    detail:wb.versionContext,
  }));
};
wb.clearVersionContext=()=>wb.setVersionContext(emptyVersionContext());
```

Call `clearVersionContext()` in the existing project-change wrapper before view refreshes. Do not use localStorage/sessionStorage.

- [ ] **Step 4: Synchronize ontology preview/exit**

`previewVersion(id, version)` sets `ids/[id]/version/'ontology-history'`; `clearPreview()` restores `all/null/null/null`. Keep the existing canvas read-only behavior and draft state untouched.

- [ ] **Step 5: Re-run the browser test and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```powershell
git add knowledge_service/web/workbench.js knowledge_service/web/ontology-model.js knowledge_service/web/index.html tests/service/test_frontend_retrieval_flow.py
git commit -m "feat: share ontology version context across views"
```

### Task 5: Show current/viewed ontology and linked knowledge in version management

**Files:**
- Modify: `knowledge_service/web/ontology-model.js:285-292,536-563`
- Modify: `knowledge_service/web/ontology-model-panel.js:1313-1429`
- Modify: `knowledge_service/web/ontology-model.css`
- Test: `tests/service/test_ontology_version_management.py`
- Test: `tests/service/test_frontend_retrieval_flow.py`

- [ ] **Step 1: Add failing browser tests for the context strip and detail counts**

Mock ontology releases including two immutable rows sharing `v2`, plus the new context endpoint. Verify the version list distinguishes colliding releases with their ontology-ID short identifiers. Verify historical preview displays `当前生效本体：v2`, `正在查看：v1（历史·只读）`, current-bound totals, entity/relation/attribute breakdown, pending/blocked, historical revisions, and migrated-away count.

Add a 500 response case asserting `读不到关联知识` and `—`, never `0`.

- [ ] **Step 2: Add failing navigation test**

Click `在知识台账查看`, assert the records tab opens and `wb.versionContext` still carries the selected immutable ontology ID.

- [ ] **Step 3: Run focused UI tests and verify RED**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_frontend_retrieval_flow.py tests/service/test_ontology_version_management.py -q -k "knowledge_context or context_strip or ledger_navigation"
```

- [ ] **Step 4: Render the ontology context strip**

Use API-provided `version`; do not fall back to array position. When multiple immutable releases share the same number, render the API `version_reused` annotation marker and an ontology-ID short identifier in the version list/detail header. Keep the existing yellow history banner and all edit/publish guards. If no ontology exists, render `尚未发布本体`, never `v0`.

- [ ] **Step 5: Load and render related-knowledge context in `renderVersionDetail`**

Create a stable host with loading/error/success states. Render `migration.pending === null` as `— / 迁移状态未知`. The button calls:

```javascript
wb.setVersionContext({
  ontologyScope:'ids', ontologyIds:[v.id],
  ontologyVersion:v.version, source:'ontology-history',
});
showTab('records');
```

It must not call migration APIs.

- [ ] **Step 6: Add responsive styles and re-run tests**

Run Step 3's command. Expected: PASS at desktop and the existing mobile viewport fixture.

- [ ] **Step 7: Commit Task 5**

```powershell
git add knowledge_service/web/ontology-model.js knowledge_service/web/ontology-model-panel.js knowledge_service/web/ontology-model.css tests/service/test_frontend_retrieval_flow.py tests/service/test_ontology_version_management.py
git commit -m "feat: show knowledge impact for ontology versions"
```

### Task 6: Add ledger ontology filtering, status, and revision wording

**Files:**
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/records-view.js:296-465`
- Modify: `knowledge_service/web/workspace.css`
- Test: `tests/service/test_records_ledger_ui.py`
- Test: `tests/service/ontology-details.test.cjs`

- [ ] **Step 1: Add failing ledger request and display tests**

Cover options `全部本体`, every API version (current/history/reused annotation revision), and `未知本体`. Assert the request always sends both fields:

```json
{"ontology_scope":"ids","ontology_ids":["ontology-old"]}
```

Assert unknown sends `unknown/null`, reset sends `all/null`, and a navigation from Task 5 preselects the version.

- [ ] **Step 2: Add failing copy/status tests**

Assert each row shows `修订 r4` and `依据本体 v2（当前|历史）`; old records map through reclassification groups to `待迁移` or `迁移阻塞`. If the plan fails, show `迁移状态未知`; a missing/foreign ID shows `未知本体`.

- [ ] **Step 3: Run focused frontend tests and verify RED**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_records_ledger_ui.py -q -k "ontology or revision"
node --test tests/service/ontology-details.test.cjs
```

- [ ] **Step 4: Render and synchronize the filter**

Populate from `/ontologies` using `version`/`version_reused`; on selection call `wb.setVersionContext`, then reload `/records/query`. Listen for `version-context:changed` so ontology-history navigation and ledger controls remain synchronized without duplicated state.

- [ ] **Step 5: Replace the version cell and add truthful state mapping**

Use an immutable ID-to-version map:

```javascript
const versionCell=()=>{
  const cell=node('td');
  const release=ontology?.byId.get(row.ontology_id);
  cell.append(
    node('b',null,`修订 r${row.version}`),
    node('small',null,release
      ? `依据本体 v${release.version}${release.isCurrent?'（当前）':'（历史）'}`
      : '依据本体：未知本体'),
    migrationBadge(row, release),
  );
  return cell;
};
```

Do not create a `兼容` state and do not infer migration status when the plan is absent.

- [ ] **Step 6: Re-run frontend tests and verify GREEN**

Run Step 3's commands. Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```powershell
git add knowledge_service/web/index.html knowledge_service/web/records-view.js knowledge_service/web/workspace.css tests/service/test_records_ledger_ui.py tests/service/ontology-details.test.cjs
git commit -m "feat: clarify ontology ownership in knowledge ledger"
```

### Task 7: Add the immutable record revision timeline

**Files:**
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/record-dialog.js:23-88`
- Modify: `knowledge_service/web/record-dialog.css`
- Test: `tests/service/test_records_ledger_ui.py`

- [ ] **Step 1: Add failing timeline, error, and read-only tests**

Open a record with three revisions bound to two ontology IDs. Assert descending `r3, r2, r1`, short `version_id`, recorded time, kind/deleted state, resolved ontology label, `r3 · 当前`, and `r2/r1 · 已取代`. Click an old revision and assert its body is displayed while save controls remain hidden/disabled. Force history load failure and assert the current detail remains usable with a retry action.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_records_ledger_ui.py -q -k "revision_timeline"
```

- [ ] **Step 3: Separate current detail state from historical selection**

Keep the record used for editing unchanged. Add `loadRevisionTimeline(projectId, record)` that calls the existing `/records/{id}/history` and `/ontologies` endpoints, sorts by numeric revision descending, and renders a button per immutable revision. Mark the highest/current visible revision `当前` and every lower revision `已取代`; preserve the independent deletion badge. Selecting a node calls a read-only renderer only.

- [ ] **Step 4: Use explicit revision terminology**

Replace dialog copy that calls record history “版本” with “修订”; retain “保存会产生 rN+1，旧修订不变”. Display unknown ontology IDs as `未知本体`, never the current ontology.

- [ ] **Step 5: Re-run timeline and full ledger tests**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_records_ledger_ui.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 7**

```powershell
git add knowledge_service/web/index.html knowledge_service/web/record-dialog.js knowledge_service/web/record-dialog.css tests/service/test_records_ledger_ui.py
git commit -m "feat: add record revision timeline"
```

### Task 8: Carry ontology scope into graph and mind map

**Files:**
- Modify: `knowledge_service/web/workspace.js:37-47,442-444`
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/workspace.css`
- Test: `tests/service/test_frontend_retrieval_flow.py`

- [ ] **Step 1: Add failing request-body tests for graph and mind map**

For exact version, unknown, and reset states, intercept all relevant POSTs and assert both fields are present. Also assert graph expansion and redraw preserve the chosen version scope.

- [ ] **Step 2: Add failing banner/reset test**

Assert both views show `知识范围：本体 v1` and a `恢复全部本体` action; clicking it sends `all/null`, updates the banner, and redraws without stale nodes.

- [ ] **Step 3: Run focused browser tests and verify RED**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_frontend_retrieval_flow.py -q -k "ontology_scope and (graph or mindmap)"
```

- [ ] **Step 4: Merge the shared context into every Scope request**

Add one helper:

```javascript
const ontologyScope=()=>({
  ontology_scope:wb.versionContext?.ontologyScope||'all',
  ontology_ids:wb.versionContext?.ontologyIds||null,
});
```

Spread it into `graphScope`, `detailScope`, and mind-map requests after the base `scope()` so both fields are always emitted. Do not filter nodes again in JavaScript; backend endpoint-integrity rules are authoritative.

- [ ] **Step 5: Render the active-scope banner and reset action**

Read `ontologyVersion` only for display. Reset through `wb.clearVersionContext()` and trigger the existing redraw loaders.

- [ ] **Step 6: Re-run graph/mind-map tests and verify GREEN**

Run Step 3's command plus the existing graph expansion race tests. Expected: PASS.

- [ ] **Step 7: Commit Task 8**

```powershell
git add knowledge_service/web/workspace.js knowledge_service/web/index.html knowledge_service/web/workspace.css tests/service/test_frontend_retrieval_flow.py
git commit -m "feat: scope graph views by ontology version"
```

### Task 9: Update the Chinese operation manual and screenshots

**Files:**
- Modify: `docs/2026-10-01-本体工作台操作手册.md`
- Create: `docs/assets/ontology-version-context.png`
- Create: `docs/assets/ledger-version-filter.png`
- Create: `docs/assets/record-revision-timeline.png`

- [ ] **Step 1: Update terminology and lifecycle examples**

Add a short matrix distinguishing `服务 1.1.0`, `本体 vN`, and single-record `修订 rN`; explicitly state there is no global knowledge version. Include one end-to-end example:

```text
发布本体 v2
→ 原有知识仍是 r3 / 依据本体 v1 / 待迁移
→ 受控迁移
→ 同一知识新增 r4 / 依据本体 v2
→ r3 仍可在修订历史查看
```

Document historical read-only preview, “基于此版本创建草案”, ledger filters, migrated-away counts, plan-unavailable states, and graph/mind-map inheritance. State that properties remain property definitions/records rather than ontology canvas nodes in this release.

- [ ] **Step 2: Verify the running service before capture**

```powershell
Invoke-RestMethod http://127.0.0.1:8100/api/health
```

Expected: healthy JSON response. Sign in with the provided local account only in the local browser session; do not write credentials to files.

- [ ] **Step 3: Capture correctly framed Chinese-labelled screenshots**

Capture the complete context strip, history-detail related-knowledge block, ledger ontology filter plus at least one row, and the full revision timeline. Crop to the application content, retain the control labels and selected values, and avoid cutting off the element being explained.

- [ ] **Step 4: Link each screenshot immediately after its matching procedure**

Use relative Markdown image links under `docs/assets/` and verify every referenced file exists.

- [ ] **Step 5: Commit Task 9**

```powershell
git add docs/2026-10-01-本体工作台操作手册.md docs/assets/ontology-version-context.png docs/assets/ledger-version-filter.png docs/assets/record-revision-timeline.png
git commit -m "docs: explain ontology and knowledge revision context"
```

### Task 10: Full regression, live smoke test, and clean handoff

**Files:**
- Verify: all files changed in Tasks 1-9
- Preserve: `.gitattributes`
- Preserve: `docker/postgres/initdb/10-app-role.sh`

- [ ] **Step 0: Record the feature comparison base**

Before Task 1's first commit, record the current `HEAD` SHA as `FEATURE_BASE` in the execution notes. Use that immutable SHA for the final whole-feature diff; do not infer the base from the working tree after commits exist.

- [ ] **Step 1: Run focused backend suites**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_ontology_version_context.py tests/service/test_ontology_version_management.py tests/service/test_reclassify.py tests/service/test_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run complete frontend suites**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_records_ledger_ui.py tests/service/test_frontend_retrieval_flow.py -q
node --test tests/service/ontology-details.test.cjs
```

Expected: PASS.

- [ ] **Step 3: Run the full Python suite**

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest -q
```

Expected: PASS with no new warnings attributable to this feature.

- [ ] **Step 4: Run static diff checks and inspect staged scope**

```powershell
git diff --check
git status --short
git diff --name-only $FEATURE_BASE..HEAD
```

Expected: no whitespace errors; the complete committed feature file list is visible; `.gitattributes` and `docker/postgres/initdb/10-app-role.sh` remain unstaged and absent from `$FEATURE_BASE..HEAD`.

- [ ] **Step 5: Perform authenticated live smoke checks on port 8100**

Verify in the browser: current ontology default; historical read-only preview; correct related counts; jump to prefiltered ledger; `rN` rows; immutable timeline; graph/mind-map banner; restore-all action; project switch reset. Confirm no console errors and inspect intercepted request bodies for the paired ontology fields.

- [ ] **Step 6: Request a final code review and fix only in-scope findings**

Use `@requesting-code-review` against the pre-feature base commit. Re-run the smallest affected suite after each correction, then repeat Steps 1-4.

- [ ] **Step 7: Create a final verification commit only if fixes/docs remain**

```powershell
git add <only-in-scope-files>
git commit -m "test: verify ontology knowledge version context"
```

If there are no remaining changes, do not create an empty commit.
