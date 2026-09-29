# Record Detail Read Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve off-graph entity details through a repository-backed temporal GET endpoint while keeping graph expansion state committed, race-safe, and explicit.

**Architecture:** Add a thin record-read route that delegates `valid_at` and `known_at` semantics to `Repository.get_record`. The browser keeps one committed entity selection and one pending detail request; cancellation restores committed controls, and successful resolution commits through `inspect` without redrawing the graph.

**Tech Stack:** FastAPI, repository temporal queries, vanilla JavaScript, pytest, Playwright.

---

### Task 1: Temporal record read API

**Files:**
- Modify: `knowledge_service/api/knowledge.py`
- Test: `tests/service/test_api.py`

- [ ] Add a failing API test that creates two repository versions with distinct system times and verifies GET `/records/{id}?known_at=...` returns the visible historical version.
- [ ] Run `D:\workspace\knowledge_service\.venv\Scripts\python.exe -m pytest tests/service/test_api.py -q -k "get_record"` and confirm the route is missing.
- [ ] Add the GET route beside PUT, accepting optional `valid_at` and `known_at`, delegating to `repository.get_record`, and returning `public(row)`.
- [ ] Re-run the focused API test and confirm it passes.

### Task 2: Explicit pending detail state

**Files:**
- Modify: `knowledge_service/web/workspace.js`
- Test: `tests/service/test_frontend_retrieval_flow.py`

- [ ] Update off-graph, failure, and race browser tests to intercept GET `/records/{id}` instead of history.
- [ ] Make race tests hold the routed request deterministically, perform relation click or drawer close, release the request, and assert drawer, selector, hidden ID, Expand enabled state, and target.
- [ ] Add pending-state assertions that Expand is disabled until resolution and cancellation restores the last committed entity.
- [ ] Run the focused browser tests and confirm RED against the history fallback/current pending behavior.
- [ ] Replace client history selection with GET `/records/{id}` plus encoded `valid_at`/`known_at` query parameters.
- [ ] Centralize pending start, success, cancellation, and failure so epoch invalidation restores committed controls and enables Expand.
- [ ] Preserve relation detail as selection-neutral and full redraw as selection-preserving.
- [ ] Re-run focused browser tests and confirm GREEN.

### Task 3: Verification and commit

**Files:**
- Verify: `knowledge_service/api/knowledge.py`
- Verify: `knowledge_service/web/workspace.js`
- Verify: `tests/service/test_api.py`
- Verify: `tests/service/test_frontend_retrieval_flow.py`

- [ ] Run the focused graph/API tests.
- [ ] Run the complete frontend retrieval-flow file.
- [ ] Run the relevant complete API test file.
- [ ] Run `git diff --check` and review call-site/state regressions.
- [ ] Amend `fix: keep graph clicks detail-only` and confirm the worktree is clean.
