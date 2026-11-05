# Workflow Performance Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development. Steps use checkbox (`- [x]`) syntax for tracking. Run implementers sequentially; review spec compliance before code quality. Do not commit or push.

**Goal:** Reduce measured publication, intake and review work while preserving all authoritative data and guards.

**Architecture:** Batch provenance edge persistence inside the existing transaction; reuse request-local graph/record state for a confirmed intake batch; batch project-scoped assertion reads in each source guard. Preserve existing API payloads, source/CAS checks, evidence, rollback, service locking and projection behavior.

**Tech Stack:** Python 3.12 (`D:/anaconda/envs/llm_model/python.exe`), PostgreSQL, rdflib, pytest; existing native JS contracts.

**Spec:** `docs/superpowers/specs/2026-11-03-workflow-performance-design.md`.

## Task 1: Batch frozen provenance edges

Files: `knowledge_service/repository/provenance_store.py`; tests in `tests/service/test_provenance_repository.py` or a focused adjacent file. Inspect `pg_engine.py` for the actual SQL/transaction API and the provenance migration constraints before coding.

- [x] Add regression(s) exercising complete activity edge sets of 1, 100 and more than 1000 edges with quantified bounded SQL growth, exact persisted logical edge/payload equivalence, strict conflict/replay behavior, collision and rollback across batch boundaries. Count SQL calls rather than asserting machine timing.
- [x] Run focused new tests using `D:/anaconda/envs/llm_model/python.exe -m pytest -q <testfile>`; confirm expected performance assertion failure before implementation.
- [x] Normalize/validate the complete proposed set once; reuse the existing edges lookup; persist only new edges in bounded multi-row SQL batches. Preserve stable domain errors and transaction atomicity. Retain existing single-edge behavior for unrelated callers.
- [x] Run provenance and ontology publication focused regressions; report exact commands/results and concerns.
- [x] Independent spec review, then independent quality review; address findings before Task 2.


Task 1 evidence: expected RED at 9/306/3009 SQL for 1/100/1001 edges; GREEN 44 targeted tests, earlier 138 passing paths plus one existing xfail; spec PASS, independent quality APPROVE. The sole remaining broad-run stale SSE assertion passed after correction. No commit or production-data actions.

## Task 2: Reuse graph work across an intake batch

Files: `knowledge_service/services/ontology_drafts_commands.py`, `knowledge_service/services/ontology_intake.py`; operation helpers only if necessary; adjacent intake/command tests.

- [x] Inspect the command compiler, operation application, impact calculations and existing intake contracts. Identify a shared private graph-based application seam instead of duplicating RDF mutation rules.
- [x] Add a regression demonstrating that an explicitly confirmed suggestion batch produces equivalent ordered operation fingerprints, evidence, impacts and preview graph while full graph parses and full-project record reads stay bounded for increasing suggestion counts. Include dependent commands, a selected subset, invalid intermediate prefixes despite a valid final graph, and a precheck-to-final-guard revision/source mutation that must roll back.
- [x] Run new regression and observe the expected work-budget failure.
- [x] Implement request-local compiler state and shared incremental graph application. Keep single-command API, generic destructive/restore behavior, source/CAS/preview guards and final whole-graph validation. Do not use cross-request cache or skip applying an invalid prefix that the old API rejected.
- [x] Preserve legacy Turtle-derived impact ordering without full serialization on the ordinary fast path; retain a narrow incoming-BNode compatibility fallback and a shared records snapshot. Cover that fallback separately and scope constant-parse claims to ordinary commands.
- [x] Run intake, draft command, validation and publication suites with one pytest process.
- [x] Independent spec review, then independent quality review; fix findings.


Task 2 evidence: expected RED at parse/read counts68/18,124/34,390/110 for1/5/24 classes plus dependent property/relation; ordinary whole-add budgets parse<=28/read<=10 green;328 focused cases passed;12 final tests passed including a real post-precheck revision advance and exact rollback. Spec PASS after added race test; independent quality APPROVE. Incoming-BNode compatibility exception documented.

## Task 3: Batch source assertion guards

Files: `knowledge_service/repository/assertions_store.py`, `knowledge_service/repository/core.py` (public forwarding and `_recheck_ontology_publish_source` assertion reads), and source guard/freezing paths in `knowledge_service/services/ontology_intake.py`; relevant source guard and repository tests. Do not edit unrelated formal execution or locking code.

- [x] Find or add a small project-scoped `get_assertions` batch read that preserves full decoded `get_assertion` records and missing-record handling, with bounded parameter batches.
- [x] Add failing quantified query-budget and source-change regressions (duplicate IDs across runs, batch boundary, missing/withdrawn/changed assertion, project isolation, empty input). Explicitly verify source/revision changes after an initial successful check still fail the final guard and leave no partial operations/run/decisions.
- [x] Batch reads once per source guard call; compare each expected fingerprint in original order, retaining all transaction boundary checks. Use it for freezing intake assertion snapshots if semantics match.
- [x] Apply the same fresh-per-invocation batch read to the repository's independent final publication source guard; preserve run/document/assertion failure ordering and verify an assertion mutation after earlier successful service checks is rejected there with full rollback.
- [x] Run source guard/intake/governance tests and review spec then quality.


Task 3 evidence: original guard assertion SELECT counts2/200/2000/2002 for1/100/1000/1001 shared IDs; ordinary batches1/1/1/2. First focused run131passed. Independent review found malformed snapshot error-priority regression, fixed with legacy fallback;25 negative cases RED17failed8passed then final61passed. Spec rereview PASS and quality APPROVE on2026-11-04. No production-data actions or missing final source/CAS guards.

## Task 4: End-to-end validation and timing comparison

Files: ignored `.benchmarks/performance-audit/*`; durable sanitized reports under `docs/research/`.

- [x] Preserve original timing files before rerunning scripts; run no two pytest sessions and no benchmark concurrently with pytest.
- [x] Run the same isolated anonymous governance and write/fusion benchmarks. Verify exact logical edge sets/payloads (or normalized hashes), 249 operations, 83 definitions, 154 formal facts, 177 selected/154 added/23 reused/0 skipped, replay idempotency and fusion undo. Correctness failures block performance claims. Schema31 repeat completed all18stages on2026-11-05; full strict payload and business assertions passed.
- [x] Run full Python suite, then native Node contract suite where applicable; investigate new failures and state any unrelated failures honestly. Fresh full2953collected:2940passed,12skipped,1existingstrictxfail,0failed; Node913passed,0failed/skipped. Source fingerprints confirm unchanged code; no reused Python prefix.
- [x] Verify temporary DB cleanup, `git diff --check`, edited file ownership and no unexpected dependencies. Write before/after table with measurement limits, explain remaining index/lock/LLM scope. Final independent artifact audit passed; all3benchmarkDBs absent; finalreport2026-11-05 states write/fusion not proven faster and all validation limits.
- [x] Final independent review of all implementation changes. Do not commit/merge/push without user request.
- [x] If local backend is running from this checkout and is idle, safely restart with existing entry/config and verify health; otherwise explicitly state restart pending. No real business mutations for verification. Port8100wasfree; normalconfig backendstarted2026-11-05;HTTP200/statusok,local1024embeddingready,Milvusready,authenabled;schema31checkpassed.

### Integration follow-ups discovered by full regression

- [x] Repair baseline-proven QA fixture drift without production guard changes: explicit historical prompt scope, real per-test PG index readiness with only external keyword I/O substituted, PG-first ingest stage/concurrency fault seam, and current visible-toggle browser interaction.79 affected tests passed; independent fixture intent/quality review approved.
- [x] Resolve existing API dependency boundary violations through established ApplicationData facade; preserve exception identity, index response/read order/rebuild enqueue and metadata generator/snapshot scope. Existing architecture2RED/newfacade11RED; architecture7+facade11+metadata2+intake1GREEN. Independent spec and quality reviews approved.
- [x] Repair remaining baseline test interfaces: removed Milvus private methods/current eligibility contract, Explorer current-index setup and actual primary/reference read seams, and generation-rebuild fake result protocol. Keep all business assertions and negative guards meaningful.55 focused checks passed, then both strengthened Explorer cases passed; independent intent/quality review approved.
- [x] Re-run whole Python suite after integration follow-ups; do not interpret targeted passes or baseline failures as a full-suite pass. Full default collection completed with zero deselection and0failures;12skips/1knownxfail explicitly retained.
- [x] Fix the existing dashboard covering index rejecting valid long relation endpoints. Add append-only migration31, preserve grouping/filter/data semantics, and verify actual repository writes plus old-index upgrade/data preservation/idempotency.37 focused checks passed; independent SQL/test review approved.
- [x] Run a fresh complete Python collection under31, with zero deselection; earlier939-prefix results are diagnostic only after this schema change. Recheck isolated performance under31, then apply the reviewed migration and start the local backend. Completed2026-11-05;finalmeasurementandreportuseNov05paths;0031appliednormallyandversionchecked31/31.
