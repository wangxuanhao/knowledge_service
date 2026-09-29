# Empty Ontology Draft Stage Guard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent ontology workbench stages that require a draft from being entered when the selected project has no drafts, and replace the permanent loading state with an actionable empty state.

**Architecture:** Keep draft availability as UI state owned by `ontology-workbench.js`. Synchronize native stage-button disabled states whenever that availability changes, and enforce the same rule inside `setStage` so programmatic callers cannot bypass it. Preserve existing automatic draft selection when drafts exist.

**Tech Stack:** Browser JavaScript, pytest, Playwright with system Edge.

---

### Task 1: Add the empty-draft browser regression

**Files:**
- Modify: `tests/service/test_ontology_workbench_ui.py`

- [ ] **Step 1: Make the existing fetch fixture expose a mutable draft list**

Initialize `window.mockDrafts = [draft]` and return that value from `/ontology-drafts`.

- [ ] **Step 2: Write the failing test**

Add a test that sets `mockDrafts` to an empty list before opening the workbench, waits for the successful empty response, and asserts that draft-dependent stages are disabled, direct `setStage('design')` remains in discovery, and the canvas does not contain `正在加载草案`.

- [ ] **Step 3: Run the focused test and verify RED**

Run: `python -m pytest tests/service/test_ontology_workbench_ui.py::test_empty_draft_list_keeps_draft_stages_locked_without_loading_forever -q`

Expected: FAIL because the design button is enabled and direct stage switching enters the permanent loading state.

### Task 2: Implement the stage guard

**Files:**
- Modify: `knowledge_service/web/ontology-workbench.js`
- Test: `tests/service/test_ontology_workbench_ui.py`

- [ ] **Step 1: Track and synchronize draft availability**

Add a tri-state draft-availability field, a helper that disables non-discovery stage buttons only when availability is known to be empty, and reset it to unknown on project change.

- [ ] **Step 2: Guard stage transitions and settle the empty response**

Make `setStage` reject draft-dependent stages when no draft exists. Update every successful draft-list load, draft selection, and draft creation to synchronize availability. When a draft-list response is empty, render an actionable no-draft state instead of leaving the loading node.

- [ ] **Step 3: Run the focused test and verify GREEN**

Run: `python -m pytest tests/service/test_ontology_workbench_ui.py::test_empty_draft_list_keeps_draft_stages_locked_without_loading_forever -q`

Expected: PASS.

### Task 3: Verify the workbench contract

**Files:**
- Verify: `knowledge_service/web/ontology-workbench.js`
- Verify: `tests/service/test_ontology_workbench_ui.py`
- Verify: `tests/service/test_frontend_static_contract.py`

- [ ] **Step 1: Run the full ontology workbench browser test file**

Run: `python -m pytest tests/service/test_ontology_workbench_ui.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run related static frontend contracts**

Run: `python -m pytest tests/service/test_frontend_static_contract.py -q`

Expected: all tests pass.

- [ ] **Step 3: Inspect the final diff**

Confirm only the empty-draft guard, its regression test, and the implementation plan were added; preserve all pre-existing uncommitted work.

