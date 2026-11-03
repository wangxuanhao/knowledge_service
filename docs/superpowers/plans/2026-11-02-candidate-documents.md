# Candidate Documents Implementation Plan

> **For agentic workers:** Use subagent-driven-development for implementation and independent spec/quality review. Preserve existing local changes; no commits or live business writes. Use current checkout on a task branch.

**Goal:** Tell users exactly how many source documents and candidate contents need review, with one right drawer and collapsible source groups.

**Architecture:** Extend the existing snapshot-backed overview/inventory with lightweight counts and document metadata. Retain OntologyIntake's selection/preview/confirmation boundaries; add bounded document reads and view state to its current controller/UI without a second writer or new dependencies.

**Tech Stack:** Native JS/CSS, Python/FastAPI/PostgreSQL, node:test, offline Playwright with system Edge.

## Task 1: Scope statistics

Files: `knowledge_service/services/ontology_discovery_workflow.py`, `knowledge_service/services/ontology_intake.py`, `tests/service/test_ontology_intake.py` (or a focused pure read-model test), and narrowly required discovery tests.

- [x] Add failing assertions for pending document count excluding draft/published/materialized/exception candidates; inventory directory covers full eligible scope before offset/limit, per-document counts and names; source/run/batch restrictions remain authoritative.
- [x] Keep lifecycle states from `_candidate_lifecycle` in overview; derive `pending_document_count` from pending normal candidates' distinct document IDs. In inventory group final eligible `items` before pagination into `documents` metadata with counts matching the UI status rules. Include a compact overall summary if useful. Keep all existing response fields.
- [x] Run targeted red/green tests with `D:/anaconda/envs/llm_model/python.exe -m pytest -q`; only one pytest session at a time. Never source `.env`.

## Task 2: Document interaction and copy

Files: `knowledge_service/web/ontology-intake.js`, `ontology-intake.css`, `ontology-model.js`, `ontology-model-panel.js`, `index.html`; `tests/service/ontology-intake.test.cjs`, related model/panel contracts.

- [x] First add meaningful failing UI/controller tests: multi-doc collapsed and one-doc open; unseen document metadata visible; opening a document loads its own page with exact source scope; global/doc-page overlap deduplicated; collapse and selection survive rerender/reopen; stale requests rejected; old responses without directory use loaded-only qualified counts.
- [x] Add document metadata, expansion preferences and per-document loading to existing scope sessions. Implement `loadDocument` with bounded 100-row reads, preserving batch/run parameters; pinned scopes must not widen to full-document rows. Merge rows by ID; only global reads advance global offset. Reuse current generation/busy/preview invalidation rules.
- [x] Render compact document accordion headings for entire eligible directory, status counts and selected counts, all expand/collapse, per-doc load more and explicit loaded-only group selection. Preserve evidence/detail and no auto writes. Filters and empty states label loaded-only results correctly.
- [x] Rewrite project guide and repeated entry descriptions to “来自 X 份资料，共 N 条候选内容待核对” and “按资料核对候选”; use precise pending lifecycle counts and unknown-count fallback.
- [x] Remove centered workspace/result modes; all stages stay in right drawer. Ensure responsive bounded list/detail and keyboard `aria-expanded`/controls/focus behavior. Bump only affected asset versions without replacing prior index.html edits.
- [x] Run related Node contracts red/green and adjust changed wording/structure assertions. Run all `node --test tests/service/*.test.cjs` after focused pass.

## Task 3: Delivery verification

- [x] Review implementation against spec, then independently for correctness/races/accessibility. Fix material issues and rerun affected checks.
- [x] Use actual production CSS/JS with mocked API responses at 1440/900/390 px. Verify right docking through loading/preview/result, all document headers before scrolling long candidates, lazy loading, group selection, close/reopen and no accidental writes; inspect screenshots in `.benchmarks/candidate-documents/`.
- [x] Run related Python read-model/intake and frontend static suites together in one pytest session, respecting existing test isolation. Record skips or unrelated failures honestly.
- [x] Run `git diff --check`, inspect final diff and preserve user's pre-existing modifications. Update this checklist with actual results. Deliver a concise Chinese explanation of new copy, document actions and validation.

## Verification completed

- Full Node contracts: 865 passed, zero failures or skips.
- Python intake/read-model/static suites: 97 passed; final read-model guard check: 2 passed. One existing AnyIO deprecation warning.
- Production JS/CSS offline browser acceptance passed at 1440, 900 and 390 px: all document headers shown before long candidates, lazy loading, source bounds, selection restoration, global/doc-page deduplication, evidence, fresh-project next-step guidance and fixed drawer heading across all stages. All confirmations used fixture APIs only.
- Independent specification and final code-quality reviews approved. Bounded body scrolling fixes the discovered header/close-button displacement and respects reduced motion.
- git diff --check passed. Snapshot comparison confirms index.html preserves every prior edit and changes only the four candidate asset versions for this task.
- Local service reloaded only after all persisted jobs, batches, items and runs were idle; original interpreter, command, cwd and environment preserved. Health HTTP 200 and all four candidate assets plus index.html match disk.
