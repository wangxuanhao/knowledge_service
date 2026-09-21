# Unified Provenance Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable, version-pinned provenance chain from answer citations back to retrieval, record versions, exact assertion decisions, chunk versions, document versions, and ingest runs, with a project-native right-side drawer UI.

**Architecture:** SQLite remains the single source of truth. A new authoritative `record_version_assertions` mapping is written inside `FormalFactWriter` transactions; answer-time activities and frozen graph edges are stored in a small provenance ledger. `ProvenanceService` owns lifecycle and graph assembly, the API exposes one project-scoped read endpoint, and the existing static workbench renders the result without introducing another UI framework.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Pydantic, pytest/TestClient, vanilla JavaScript/CSS, Playwright tests, Node contract tests.

**Execution note:** Implement in the current working tree. The user has an active uncommitted package-layer refactor (`api/`, `services/`, `repository/`, etc.); a new Git worktree would start from committed pre-refactor paths and would not contain the source being extended. Touch only the files named below and preserve unrelated edits.

---

## File map

### New files

- `knowledge_service/repository/provenance_store.py` — persistence primitives for mappings, activities, edges, terminal transitions, and project-scoped reads.
- `knowledge_service/services/provenance.py` — lifecycle orchestration, citation validation, graph freezing, response assembly, and integrity warnings.
- `knowledge_service/api/provenance.py` — the project-scoped provenance GET route.
- `knowledge_service/web/provenance-drawer.js` — drawer state, safe citation buttons, fetch cancellation, graph/API views, focus restoration.
- `knowledge_service/web/provenance-drawer.css` — styles using the current workbench palette, spacing, borders, and mobile behavior.
- `tests/service/test_provenance_repository.py` — migration, transaction, invariant, export, and deletion tests.
- `tests/service/test_provenance.py` — service/API/SSE provenance contract tests.
- `tests/service/provenance-drawer.test.cjs` — pure JavaScript citation tokenization and state-shape tests where DOM is unnecessary.

### Modified files

- `knowledge_service/repository/core.py` — migration 12, store composition/forwarders, exact version reads, export/delete integration.
- `knowledge_service/repository/__init__.py` — export any migration/store symbols used by tests.
- `knowledge_service/services/formal_writes.py` — write exact record-version/assertion/event mappings inside the existing transaction.
- `knowledge_service/services/answers.py` — begin/complete/fail activity lifecycle around existing retrieval and SSE flow.
- `knowledge_service/api/__init__.py` — install provenance routes and advertise the capability.
- `knowledge_service/api/parity.py` — make the compatibility export return the same provenance structure.
- `knowledge_service/web/index.html` — load the new CSS/JS with cache-busting versions.
- `knowledge_service/web/workbench.js` — persist lightweight evidence summaries and hand completed turns to the drawer module.
- `knowledge_service/web/evidence-inspector.js` — expose the existing source dialog through a safe frozen-excerpt interface.
- `knowledge_service/web/record-dialog.js` — expose the existing history dialog entry point for provenance record nodes.
- `tests/service/test_explorer.py` — keep existing SSE assertions and add ID/backward-compatibility assertions if best located here.
- `tests/service/test_frontend_retrieval_flow.py` — Playwright coverage for drawer UX and project-switch cancellation.
- `tests/service/test_frontend_static_contract.py` — asset loading and accessible drawer hooks.

---

## Task 1: Migration 12 and repository ledger primitives

- [ ] Add failing repository tests

Create `tests/service/test_provenance_repository.py` covering:

- a fresh database reports schema version 12;
- a v11 database upgrades once and repeated startup is idempotent;
- migration failure rolls back all three tables/indexes;
- migration 12 performs deterministic mapping backfill before recording v12, accepts the exact timestamp/canonical/event case, and rejects ambiguous legacy candidates without guessing;
- composite foreign keys reject cross-project activity edges and mismatched mapping triples;
- activity transitions permit `running → completed|failed|cancelled` exactly once;
- duplicate identical edges are idempotent, conflicting terminal writes fail;
- `get_record_version(project_id, version_id)` returns the exact historical version after a later revision.

- [ ] Run the narrow test and confirm RED

Run:

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_provenance_repository.py -vv -x
```

Expected: failures for missing migration 12 and missing repository methods.

- [ ] Implement migration and store

In `knowledge_service/repository/core.py`:

- add migration 12 with the three tables and required composite unique indexes/checks/JSON validation;
- perform deterministic legacy mapping backfill inside migration 12 itself, before the migration version is committed; do not defer backfill to later application code;
- ensure foreign keys use `ON DELETE CASCADE` exactly as the design specifies;
- construct `ProvenanceStore` in `Repository.__init__`;
- add `get_record_version(project_id, version_id)` without falling back to the current record;
- add thin forwarding methods only; keep SQL in `repository/provenance_store.py`.

In `knowledge_service/repository/provenance_store.py` implement:

- mapping insert/list;
- begin activity;
- complete/fail/cancel activity using compare-and-set status;
- atomic activity completion plus edge insert;
- one composition primitive `complete_retrieval_and_begin_answer(...)` that owns the outer transaction and performs retrieval completion, answer creation, and all retrieval/answer/offered/source edge inserts atomically; store-private `_insert_*` helpers must not open nested transactions;
- project-scoped activity/edge lookup;
- defensive JSON normalization through the repository `_json` helper.

- [ ] Run the narrow test and confirm GREEN

Use the same command. Do not proceed while any assertion fails.

## Task 2: Exact record-version support mapping in formal writes

- [ ] Add failing formal-write tests

Extend `tests/service/test_formal_writes.py` to prove:

- a newly accepted source assertion maps to the exact `version_id` returned by `_put` and the exact event that accepted it;
- revising the record creates a distinct mapping and does not retarget the earlier mapping;
- transaction rollback removes the record, assertion, event, and mapping together;
- an event for a different assertion or canonical record cannot be mapped.
- new records, deduplicated-current records, pending assertion approval for an existing record (`records=[] + assertion_decisions`), idempotent assertion replay, and merge/rebind events all map only to the version and event selected inside that transaction.

- [ ] Run tests and confirm RED

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_formal_writes.py tests/service/test_provenance_repository.py -vv -x
```

- [ ] Implement the mapping write

Update `knowledge_service/services/formal_writes.py`:

- track `canonical_record_id → saved version_id` for both newly written and deduplicated-current records;
- for assertion-only approvals, explicitly select the intended current record version inside the same FormalFactWriter transaction before applying the decision; pass that exact version to the mapping write;
- after an assertion reaches accepted, locate its exact decision event by `(assertion_id, decision_version)`;
- insert the mapping before the surrounding repository transaction commits;
- never infer a mapping after the transaction from current state.
- on replay, reuse the existing exact event/mapping only when all IDs match; merge/rebind events create a new mapping only when the transaction explicitly chooses the target version.

- [ ] Run tests and confirm GREEN

Use the previous command.

## Task 3: Provenance service freezes the full source graph

- [ ] Add failing service tests

Create `tests/service/test_provenance.py` with direct service/repository fixtures for:

- `begin_retrieval` creates a project-scoped running run;
- `complete_retrieval` atomically completes retrieval, begins answer, freezes offered citation → record version and every mapped assertion branch;
- inject a failure during offered/source edge insertion and assert retrieval remains running, no answer exists, no new edge exists, and the SSE layer cannot emit `evidence`;
- entity/relation, direct chunk, manual record, and ambiguous legacy record use their distinct completeness rules;
- chunk nodes use `chunk-version:{version_id}` and historical document excerpts;
- resolve assertion source chunks only by `(project_id, assertion.chunk_id, metadata.source_version_id == assertion.document_version_id)` across record versions; zero or multiple matches produce a warning and never fall back to the current chunk;
- obtain ingest run only from the frozen chunk version's `metadata.run_id`, then verify project, document ID, and document version ID; a mismatch is a warning, not a current-run fallback;
- freeze the ingest run's `attempt`, `status_at_capture`, `created_at`, and `updated_at_at_capture` in the source edge payload; the read response must not re-read mutable run status;
- revising a chunk or retrying the same document ingest later cannot change a previously frozen answer graph;
- a later status transition of the same ingest run does not change the earlier provenance response;
- later assertion withdrawal, canonical rebind, merge, or document revision does not alter an existing answer graph;
- multi-source facts preserve all branches;
- no hidden provider request, authorization header, or chain-of-thought field is stored;
- unknown and duplicate LLM citations never create dangling edges.
- completed answers that offered but did not cite an item return `citation_status=uncited`; failed/cancelled answers keep offered evidence readable with the matching answer status.

- [ ] Run tests and confirm RED

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_provenance.py -vv -x
```

- [ ] Implement `ProvenanceService`

In `knowledge_service/services/provenance.py`:

- generate `rr_` and `ans_` IDs internally;
- normalize request scope and retrieval metadata into bounded public payloads;
- freeze graph nodes/edges at retrieval completion using only exact version reads and `record_version_assertions`;
- apply the exact chunk/ingest matching algorithm above and freeze the resulting version/run refs immediately;
- include all mapped support branches without an implicit cap;
- build fixed node detail shapes and structured warnings;
- validate citations with `E[1-9][0-9]*`, uppercase only, and offered-set membership;
- assemble a deterministic `nodes`/`edges` response sorted by ordinal/ref;
- represent running/failed/completed answers as documented.

- [ ] Run tests and confirm GREEN

Use the same command.

## Task 4: Integrate lifecycle into the SSE answer stream

- [ ] Add failing SSE tests

Extend `tests/service/test_provenance.py` and, where appropriate, `tests/service/test_explorer.py`:

- retrieval activity begins before `question_context` and becomes failed if retrieval raises;
- `evidence` contains `answer_id`, `retrieval_run_id`, and per-row `provenance_ref` only after offered edges commit;
- provenance endpoint is readable while answer status is running;
- evidence-only completion writes cites edges before `done`;
- LLM unknown citation yields warning without an edge;
- completion transaction failure emits `error` and never emits `done`;
- generator cancellation marks answer cancelled;
- successful `done` always includes `provenance_complete: true`; a successful done event may not omit or negate it;
- all pre-existing SSE keys and single scoped-query behavior remain unchanged.

- [ ] Run tests and confirm RED

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_provenance.py tests/service/test_explorer.py -vv -x
```

- [ ] Update `services/answers.py`

Wrap existing logic without changing retrieval ranking:

- begin retrieval before calling `question_context`;
- complete retrieval and offered graph before yielding `evidence`;
- accumulate answer as today;
- complete answer transaction before yielding `done`;
- distinguish retrieval failure, answer failure, and generator cancellation;
- keep public error sanitization and existing logging.

- [ ] Run tests and confirm GREEN

Use the same command.

## Task 5: HTTP contract and project lifecycle

- [ ] Add failing API/export/delete tests

Cover:

- `GET /api/projects/{p}/answers/{answer_id}/evidence/{citation}/provenance` returns schema `1.0`, fixed shapes, structured warnings, and exact frozen versions;
- cross-project answer access and unknown offered citation return 404 with stable codes;
- running/failed answers return 200 with status;
- `export_projection` and compatibility export add `provenance.record_version_assertions`, `activities`, and `edges` without removing old top-level fields;
- project deletion removes/counts all three provenance tables.

- [ ] Run tests and confirm RED

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_provenance.py tests/service/test_provenance_repository.py tests/service/test_project_management.py tests/service/test_explorer.py tests/service/test_api.py -vv -x
```

- [ ] Implement route and lifecycle integration

- add `knowledge_service/api/provenance.py` with a read-only project-scoped handler;
- install it in `knowledge_service/api/__init__.py`;
- add `unified_provenance` to `/api/health.capabilities`;
- extend repository export and deletion in `repository/core.py` while preserving existing fields and count names.
- update `api/parity.py` `/projects/{p}/export` to delegate to the same `export_projection` shape instead of rebuilding a provenance-less subset.
- add a failing health-capability assertion before adding `unified_provenance`.

- [ ] Run tests and confirm GREEN

Use the same command.

## Task 6: Project-native drawer UI and safe citation rendering

- [ ] Add failing JavaScript/static tests

Create `tests/service/provenance-drawer.test.cjs` and extend `test_frontend_static_contract.py` for:

- citation tokenizer returns text segments and allowed citation tokens without using HTML;
- history summaries truncate to 240 characters and whitelist fields;
- index loads `provenance-drawer.css` after `workbench.css`, and loads `provenance-drawer.js` after `ingest-mode.js` but before `workbench.js`;
- drawer has accessible label, tabs, status region, close control, and API `<pre>` target;
- no model answer path assigns to `innerHTML`.
- drawer module is available before `workbench.js` history hydration, or an explicit ready/hydrate handshake makes restored citation decoration deterministic;
- old assistant history without `answer_id` renders “该历史回答生成于溯源记录启用前”.

- [ ] Run tests and confirm RED

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_frontend_static_contract.py -vv -x
node --test tests/service/provenance-drawer.test.cjs
```

- [ ] Implement drawer markup, styling, and module

In `knowledge_service/web/provenance-drawer.js`:

- expose a small `window.ProvenanceDrawer` interface used by `workbench.js`;
- expose `window.openFrozenSourceEvidence(excerpt)` from `evidence-inspector.js`; it accepts only the fixed historical excerpt fields and renders through the existing escaped source dialog;
- expose a stable read-only history entry point from `record-dialog.js` for provenance record nodes instead of reaching private functions;
- turn only offered/known citation tokens into native buttons after `done`;
- render graph branches with DOM APIs and `textContent` only;
- provide evidence-chain and raw-interface tabs;
- reuse the source evidence dialog data shape for exact historical excerpts;
- use an AbortController, cancel on close/project switch, and verify the captured project/answer before rendering;
- restore focus to the triggering citation and support Escape.

In `knowledge_service/web/provenance-drawer.css`:

- directly reuse the existing `var(--accent)`, `var(--line)`, surface variables, and button font rules where available; add only provenance-specific layout variables;
- use existing button typography and restrained borders/radii;
- use a fixed-width right drawer on desktop and an in-flow panel below the answer on narrow screens;
- include loading, partial-chain warning, failed, and empty states.

Update `index.html` to include the drawer CSS and load `provenance-drawer.js` before `workbench.js`, with a new cache-busting suffix.

- [ ] Run tests and confirm GREEN

Use the same commands.

## Task 7: Wire the chat history and exercise the full browser flow

- [ ] Add failing Playwright tests

Extend `tests/service/test_frontend_retrieval_flow.py`:

- after a completed streamed turn, `[E1]` in both answer and evidence list is clickable;
- immediately after the `evidence` event and while the answer is still running, evidence-list `[E1]` is clickable and opens status=running provenance; only answer-body citations wait for `done` tokenization;
- clicking sends exactly one provenance request and opens the right drawer without navigating away;
- evidence-chain/API tabs work;
- exact historical source excerpt opens in the existing source dialog;
- Escape closes and restores focus;
- close/project switch aborts a pending request and stale response cannot repaint;
- refresh restores provenance-enabled turns from lightweight local history;
- old history without `answer_id` stays readable and non-clickable;
- old history displays the explicit pre-provenance note;
- server-provided `<img onerror>`/HTML remains inert text.

- [ ] Run the focused browser tests and confirm RED

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_frontend_retrieval_flow.py -k "provenance or knowledge_chat" -vv -x
```

- [ ] Update `web/workbench.js`

- retain evidence/IDs from stream events per assistant turn;
- persist only the whitelisted evidence summary;
- wire offered evidence-list citations immediately on the evidence event; after `done`, call the drawer module to decorate answer-body citation text safely;
- decorate restored history only when it contains `answer_id`;
- notify the drawer module on project changes/clear.

- [ ] Run focused tests and confirm GREEN

Use the same command.

## Task 8: Full verification and implementation review

- [ ] Run the complete Python suite

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest -q
```

- [ ] Run all Node tests

```powershell
Get-ChildItem tests/service/*.test.cjs | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { throw "Node test failed: $($_.Name)" } }
```

- [ ] Run targeted high-signal suites verbosely

```powershell
D:\anaconda\envs\llm_model\python.exe -m pytest tests/service/test_provenance_repository.py tests/service/test_provenance.py tests/service/test_formal_writes.py tests/service/test_frontend_retrieval_flow.py -vv
```

- [ ] Manually inspect the running UI

Create a task-owned temporary directory, set `KG_DATABASE` to its SQLite path, and start the existing app on an isolated port (for example `D:\anaconda\envs\llm_model\python.exe -m knowledge_service --host 127.0.0.1 --port 8876`). Seed only disposable test data through the API, then open the knowledge Q&A page, submit an evidence-only query, click `[E1]`, switch both drawer tabs, open the frozen source excerpt, test Escape/focus, and resize below the mobile breakpoint. Never point manual verification at the default business database. Stop the isolated server after inspection.

- [ ] Request a final code review

Review only this feature's diff for:

- SSOT violations or current-state inference;
- transaction gaps between evidence SSE and frozen edges;
- project-scope data leaks;
- unsafe model-text rendering;
- missing cancellation/focus/accessibility behavior;
- accidental changes to unrelated user refactor files.

- [ ] Re-run every affected verification after review fixes

Do not claim completion from earlier output. Record the fresh pass counts in the final response.
