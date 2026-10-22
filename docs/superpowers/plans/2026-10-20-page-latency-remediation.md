# Page latency remediation implementation plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Recheck and fix the still-present login flash and page latency identified in `docs/research/2026-10-20-page-latency-diagnosis.json` without weakening permissions, temporal visibility or publication validation.

**Architecture:** Keep the current PostgreSQL authority and writer transactions. Isolate bounded HTTP read workloads and authentication from the shared writer connection; replace page-summary scans with scope-preserving bounded reads. Frontend loaders belong to visible views and share in-flight work. Preserve the newly delivered editing-readiness implementation.

**Tech Stack:** Python 3.12 in `D:/anaconda/envs/llm_model`, FastAPI, psycopg 3, PostgreSQL, native JS, node:test, Playwright.

User authorization: the preceding diagnosis proposed these changes; the user requested confirming and fixing remaining issues. Work in this dirty checkout so existing uncommitted features are retained. Do not commit unrelated work, restart Docker, or modify business records.

### Task 1: Establish the current baseline

- [x] Run `.benchmarks/page_latency_probe_20261020.py http` against 8100 and save a before-fix copy.
- [x] Compare current files with running-process start time; identify already fixed ontology paths.
- [x] Record current browser request counts and authentication shell behavior.

### Task 2: Login and frontend workload ownership

Files: `knowledge_service/web/auth.js`, `index.html`, `ontology-model-reclassify.js`, `ingest-ontology.js`, `workspace.js`, `sources-view.js`, `records-view.js`, `ontology-model-catalog.js` and focused JS/browser tests.

- [x] Add failing behavior tests for a hidden unauthenticated shell, hidden-view expensive reads, duplicate sources loading, bounded ledger lineage, and hidden ontology card work where still present.
- [x] Put auth bootstrap before vendor work and reveal the shell only after confirmed authorization; retain auth-disabled test support.
- [x] Gate heavy loaders by view visibility; share requests and stop stale supplementary work.
- [x] Batch ledger lineage using the new bounded endpoint in Task 4; preserve evidence access and page totals.
- [x] Avoid rebuilding hidden ontology cards and cache per-summary indexes if needed.
- [x] Bump edited frontend asset versions in `index.html` and update exact-version contracts.
- [x] Run `node --test tests/service/*.test.cjs` and appropriate browser checks.

### Task 3: Isolate read/authentication queueing

Files: `knowledge_service/repository/pg_engine.py`, `core.py`, `users_store.py`, `api/security_middleware.py`, `api/__init__.py`, focused Python tests.

- [x] Add failing concurrency coverage: a slow main-connection scan must not block authentication/public event-loop progress or a bounded read on another project.
- [x] Use explicit independent read units/snapshots for HTTP reads and authentication without globally removing writer locks or weakening transaction consistency.
- [x] Move synchronous authentication I/O off the async event loop; retain immediate disable/password-fingerprint checks.
- [x] Verify pool/connection lifetime and transaction-local reads; close resources on shutdown.
- [x] Run the relevant repository/auth/read integration tests in one pytest session, coordinated with Task 4.

### Task 4: Remove whole-project work from page summaries

Files: focused new repository read module if useful, `services/explorer.py`, `ontology_discovery_workflow.py`, `api/knowledge.py`, `repository/assertions_store.py` or a focused batch reader; `tests/service/test_page_read_latency.py`.

- [x] Add failing tests showing discovery overview can find candidates without materializing all normal facts, sources ignores unrelated facts and preserves assertion-based source associations, and batch lineage respects project and record bounds.
- [x] Implement bounded/candidate-specific SQL reads with current/history/filter visibility preserved; reuse existing scope compiler.
- [x] Add `POST /api/projects/{project_id}/assertions/batch` with a bounded list of record IDs, and add it to the explicit read-only authorization policy. Return `assertions_by_record` for Task 2.
- [x] Handle missing optional validity bounds in retained legacy/capacity rows without rewriting fixtures.
- [x] Retain full explicit validation/publication checks and editing readiness.
- [x] Run focused Python tests only after Task 3's pytest session has ended; never run two pytest sessions concurrently.

- [x] Recheck the active modeling view: replace the remaining full-scan reclassification plan with exact SQL groups and bounded samples; preserve full migration validation.

### Task 5: Integration, independent review, and runtime delivery

- [x] Review each task against scope and correctness, including permissions, temporal scope, source many-to-many associations, stale-result handling and connection cleanup.
- [x] Run one combined relevant Python regression session and the JS suite. Repair regressions introduced by the change.
- [x] Restart only the verified idle local knowledge-service process on 8100 using the same conda interpreter and hidden background launch; first check no running ingestion/write operation would be interrupted.
- [x] Rerun the same HTTP/browser/concurrency probes, compare matching workloads, and report measured improvements with remaining limits.
- [x] Save evidence in a new `docs/research/2026-10-20-page-latency-remediation.json`; distinguish single samples from percentile claims.
