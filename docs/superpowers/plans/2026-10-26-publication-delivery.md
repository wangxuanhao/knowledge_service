# Publication delivery and compact draft transport

The user approved compact version history, clear publication stages, immediate saved feedback with independent refresh, and investigating/reducing the 2.6 MB draft response. Continue in this checkout on `codex/ontology-publication-delivery`; do not publish or approve real business data.

**Goal:** Make publication completion unambiguous and remove redundant transport/display costs without weakening governance.

**Architecture:** Preserve full legacy draft responses and persistence. Add an optional review projection that deduplicates repeated evidence arrays; a small browser transport adapter hydrates shared arrays for existing consumers. OntologyModel remains the owner of draft/formal projections. A confirmed publish response applies the returned formal version immediately; subsequent read-only reconciliation has separate loading/error/retry state and cannot overwrite a newly selected project, draft, revision or history preview. History defaults to compact summaries and lazily renders candidate/technical sections.

**Stack:** Native JS/CSS, FastAPI, PostgreSQL, Node contracts, offline Edge browser fixtures. Use test-first development and independent reviews.

## Measured baseline

- Actual draft: 188 operations / 60 definitions / 118 linked facts. Already published as v1, revision 7. Never replay publication while testing.
- Full draft response: 2,613,299 bytes; operations 2,308,877 bytes. Most definition operations repeat 120 evidence references (about 11.5 KB), accounting for most of the size. Existing canvas projection is 393,520 bytes but omits evidence and sources.
- Version list about 0.03 s; full draft about 1.2 s; full readiness about 1.1 s. These are current read timings, not the original POST duration.
- Reproduction `.benchmarks/version-publish-ui-probe.cjs --assert-compact` shows 118 default-expanded rows and a publish button still busy after successful POST while waiting for page refresh.

## Task 1 — Compact, lossless review projection (backend owner)

Files: `services/ontology_drafts.py`, `api/ontology_drafts.py`, new focused Python projection tests.

- [x] Write/observe failing API/service tests for `view=review`: unchanged draft identity, revision, ontology, current operations/fingerprints, decisions, warnings, source snapshots and bindings; no persistence writes.
- [x] Project evidence into one top-level `evidence_sets` dictionary; each operation has `evidence_set_id` and `evidence_count`, rather than repeating its array. Equal complete arrays share a set. Preserve raw order, duplicate references, mixed object/string evidence and unresolved references exactly. Omit duplicate nested `draft` and Turtle from this browser projection; full legacy responses remain unchanged.
- [x] Expose optional `view=review` on GET and the workbench create/command/validate/submit/decision/batch/definition/rebase/close responses. Nested `{draft,batch|definition}` envelopes retain their shape. Keep projection in the service, route layer only delegates. Preserve all mutation guards and server actors.
- [x] Test realistic 188×120 references with substantial byte reduction and exact evidence rehydration, including distinct evidence lists and ordinary full/canvas compatibility. Run a single isolated pytest process; no parallel pytest or live writes.

## Task 2 — Browser draft transport and publication lifecycle (root)

Files: new `web/ontology-draft-data.js`, `ontology-model.js`, `ontology-model-panel.js`, `ontology-model-workspace.js`, `ontology-draft-history.js`, `ontology-review-workspace.js`, relevant Node tests.

- [x] Test/implement the adapter: append review view to appropriate draft API requests, hydrate shared arrays without altering fingerprints or losing sources, decode wrapped responses, keep full-response compatibility.
- [x] Use compact transport for workbench draft reads/writes and history/review loads. Keep canvas's explicit projection. Do not reintroduce repeated network evidence copies.
- [x] Remove redundant full-draft GET before publication; use the revision/fingerprint from the current draft and fresh readiness, keeping backend final source/validation/CAS/idempotency checks intact. Do not cache validation by draft revision alone.
- [x] Tests: successful POST stops publish busy state before delayed history/page reads; official publication timestamp and facts receipt show immediately. A failed follow-up read remains “published, refresh failed”, retries only reads, and never generates a second POST/key. Ambiguous POST failures preserve key and require reconciliation before resubmission.
- [x] Add model-owned adoption/reconciliation seams: apply exact returned version and summary only in matching context. Background reads do not blank the confirmed graph, block new work, overwrite a newer draft/history/project, or clear unsaved inspector values. Keep historical read-only behavior.

## Task 3 — Compact version history (root)

Files: panel JS/CSS (extract a small receipt/history helper if needed), Node/browser tests.

- [x] Default version detail shows version, official timestamp, publisher, publication note, definition/fact summary and clear primary actions. Candidate results, IDs, write path, RDF counts, source internals and index snapshot go behind explicit details.
- [x] Per-batch/candidate rows are closed and not built until requested. Preserve all candidate IDs/outcomes and exact version/draft navigation. Avoid eager current-knowledge requests; load expanded sections with visible loading/error/retry states and context guards.
- [x] Keep controls reachable and no horizontal overflow at 1440/900/390; loading and saved/refreshing/error states remain distinct.

## Task 4 — Delivery

- [x] Bump changed resource versions and pinned contracts. Run focused tests first, all Node contracts, relevant Python projection/API/publication suites in one session, and offline browser acceptance with real production assets.
- [x] Independent spec review followed by quality review; resolve substantive findings and reverify changed concerns.
- [x] Measure actual draft full/review/canvas response bytes and GET times, and verify equivalent evidence with read-only requests after an idle-audited service reload. Preserve existing published version/facts/decisions and service command/cwd/environment.
- [x] Update manual, commit, merge master and push as already requested; verify remote/local revisions and clean worktree. Report measured improvements and any unmeasured server POST timing honestly.

## Verification and measured results

- Lossless real-data comparison: full response 2,613,299 bytes; review response 465,315 bytes (82.2% reduction). Two distinct evidence arrays retain 21,600 expanded operation-reference entries. Complete projection roundtrip matches full content except deliberately omitted duplicate draft/Turtle fields.
- One read-only sample: full GET 1.527 seconds, compact GET 1.356 seconds. This is a transport improvement; no claim is made about unmeasured original POST duration or removal of required server validation.
- Related Python/API/static/browser verification: 94 passed, one existing Starlette deprecation warning. Production-asset offline browsers passed 1440/900/390 widths with delayed saved/sync reads, separate failed-read retry, no repeated publication POST, and 118 lazy candidate rows.
- Targeted Node tests cover original frozen request retry, context changes, dependent-cache publication events, new-draft/history protection, candidate status reconciliation, and receipt dismissal that does not cancel page synchronization. Independent spec and quality reviews completed; race findings were fixed with red/green regressions.
- Service reloaded only after all persisted jobs/batches/items/active artifacts were idle, preserving the original interpreter/command/environment/cwd. Published business draft remains accepted at revision 7, with 188 operations/decisions and v1's 118 added facts. Verification made no business publication or decision writes.

- Final full Node verification: 551 passed. Original red-capable UI probe now shows zero default candidate rows, no redundant pre-publication draft GET, and no publish busy lock after confirmed success while page synchronization is delayed. Final mobile browser and read-only resource hashes also passed.
