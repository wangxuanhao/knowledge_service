# QA Conversations Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver account-owned, project-scoped conversation management with server persistence and observable ontology expansion.

**Architecture:** A small repository store and service expose private conversation snapshots through an authenticated API. A standalone frontend controller owns list navigation and optimistic saves; the existing QA renderer and retrieval keep their current evidence contracts.

**Tech Stack:** PostgreSQL numbered migrations, FastAPI, native JavaScript/CSS, pytest, node:test and system Edge.

---

Design: `docs/superpowers/specs/2026-10-26-qa-conversations-design.md`. Reuse the current checkout because this feature builds on the user's pending QA fixes. Keep unrelated changes intact.

### Task 1: Private server storage

Files: create `repository/migrations/0028_qa_conversations.sql`, `repository/qa_conversations_store.py`, `services/qa_conversations.py`, `api/qa_conversations.py`, `tests/service/test_qa_conversations.py`; register in `repository/core.py`, `api/__init__.py`, `api/security_middleware.py`.

- [x] Add real-auth failures for ownership, viewer self-service, persistence, version conflicts, deletion, bounded inputs and public snapshot fields.
- [x] Run `D:/anaconda/envs/llm_model/python.exe -X utf8 -m pytest -q tests/service/test_qa_conversations.py -x` and observe a failure before implementation.
- [x] Implement the exact API described in the design with owner/project SQL predicates and version checking inside transactions.
- [x] Repeat focused tests. Only one agent may run pytest at a time.
- [x] Review ownership checks and narrowly scoped viewer exceptions independently.

### Task 2: Browser controller

Files: create `knowledge_service/web/qa-conversations.js`, `tests/service/qa-conversations.test.cjs`.

- [x] Add failing tests for serialized saves, stale project/account responses, version-protected deletion races, conflict preservation, network/401 retry and legacy import confirmation.
- [x] Run `node --test tests/service/qa-conversations.test.cjs` and observe expected failure.
- [x] Implement client/controller/mount with the API contract, typed display values, per-conversation queues, revision checks and accessible dialogs.
- [x] Run the focused tests and review the controller API before integration.

### Task 3: Integrate the workbench

Files: modify `knowledge_service/web/workbench.js`, `index.html`, `qa-page.css`, `qa-presentation.js`; create `qa-conversations.css`; extend relevant Node/browser contracts.

- [x] Add failed contracts for markup, requests, expansion status and controller activation.
- [x] Mount the controller after authentication, replace production local-only persistence with server saves, and preserve existing isolated test fallback.
- [x] Abort old streams on conversation/project change; keep completed answers and frozen evidence when selecting history.
- [x] Add the responsive conversation rail, search, new/rename/delete/clear/import controls and dialogs without moving the input below the viewport.
- [x] Add the independent QA ontology toggle and retain its public diagnostics in saved answer status.
- [x] Bump every changed runtime asset version and its explicit contract assertion.

### Task 4: Expansion diagnosis and validation

Files: `services/service.py`, `services/answers.py`, `tests/service/test_ontology_expansion_diagnostics.py`; live probes in `.benchmarks/`.

- [x] Test real semantic/hybrid queries `美团` and `互联网平台` with expansion on/off and compare exact IDs, class matches and type channel counts.
- [x] Observe flat-hierarchy/QA diagnostic failures, then fix hierarchy detection and context propagation; independent unittest reports 5 passing tests.
- [x] Run all Node contracts and Edge layout/session browser tests; inspect screenshots.
- [x] Run the backend focused suite, apply the numbered migration using the established migration command, then restart the real service.
- [x] Verify normal authenticated server conversation CRUD and cross-client restore, and real QA expansion diagnostics, without changing existing user conversations.
- [x] Independently review the final diff and report validation limitations accurately.

## Final verification

- Runtime migration check: schema 28 matches. Normal service started on port 8100 with the established interpreter.
- Authenticated acceptance: real hybrid QA with ontology expansion, frozen answer save, second-client restore, rename/search, stale save/delete409, and deletion of only the generated acceptance conversation all passed (`.benchmarks/qa-conversations-live-result.json`).
- Focused Python:22 passed, including conversation authentication/ownership, expansion diagnostics and formatting/history browser regression.
- Historical document-QA run:34 passed,9 failed because its test projects had no built Milvus search index (`not_built`). On2026-10-27 the stale test index setup was repaired alongside entity expansion; see2026-10-27-entity-ontology-expansion.md for current verification. This was not historical business-data corruption.
- Final Node:614 passed. Edge browser:7 passed, including real old-history confirmation/import and citation restore. Final results are recorded in `.benchmarks/qa-conversations-node-final.log` and `.benchmarks/qa-conversations-browser-final.log`. Screenshots: `.benchmarks/qa-conversations/desktop.png` and `mobile.png`.
