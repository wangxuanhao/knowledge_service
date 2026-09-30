# Discovery Cluster Expansion Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make discovery distribution cards show Top 10 by default and independently expand into a bounded, scrollable full list with an accurate total.

**Architecture:** Keep the backend summary contract unchanged. Extend the existing `cluster` renderer to create a dedicated rows container, render either 10 or all sorted rows, and manage one local expanded state per card; CSS marks expanded containers as scrollable. Browser tests exercise the real DOM and interaction.

**Tech Stack:** Vanilla JavaScript, CSS, pytest, Playwright with Edge.

---

## File map

- `tests/service/test_ontology_workbench_ui.py`: browser-level regression covering collapsed, expanded, scrolled, and re-collapsed behavior.
- `knowledge_service/web/ontology-workbench.js`: distribution card rendering and independent expand/collapse state.
- `knowledge_service/web/ontology-workbench.css`: bounded expanded list and toggle styling.

### Task 1: Add the failing browser contract

**Files:**
- Modify: `tests/service/test_ontology_workbench_ui.py:77-82`
- Modify: `tests/service/test_ontology_workbench_ui.py:223-234`

- [ ] **Step 1: Expand the mocked entity distribution to 12 deterministically sortable rows**

Keep `Alpha`, `Gamma`, and `Beta`, then add nine uniquely named rows so the expected Top 10 and full ordering are explicit. Keep relation and attribute distributions below 10.

- [ ] **Step 2: Replace the existing cluster assertion with the interaction contract**

Assert:

```python
assert entity_rows.count() == 10
toggle = clusters.nth(0).locator('.ontology-workbench__cluster-toggle')
assert toggle.inner_text() == '展开全部（共 12 类）'
assert toggle.get_attribute('aria-expanded') == 'false'
assert clusters.nth(1).locator('.ontology-workbench__cluster-toggle').count() == 0
assert clusters.nth(2).locator('.ontology-workbench__cluster-toggle').count() == 0

toggle.click()
assert entity_rows.count() == 12
assert toggle.get_attribute('aria-expanded') == 'true'
assert 'is-expanded' in clusters.nth(0).locator('.ontology-workbench__cluster-list').get_attribute('class').split()

toggle.click()
assert entity_rows.count() == 10
assert toggle.get_attribute('aria-expanded') == 'false'
```

Retain exact ordering assertions for the collapsed and expanded rows.

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_ontology_workbench_ui.py::test_discovery_clusters_are_complete_and_stably_sorted -q
```

Expected: FAIL because the current renderer has no `.ontology-workbench__cluster-toggle` and never exposes rows 11-12.

### Task 2: Implement the expandable, scrollable cluster list

**Files:**
- Modify: `knowledge_service/web/ontology-workbench.js:51-60`
- Modify: `knowledge_service/web/ontology-workbench.css:59-68`

- [ ] **Step 1: Refactor `cluster` around a sorted array and rows container**

Create `.ontology-workbench__cluster-list`, preserve the current comparator, and render `sorted.slice(0, expanded ? sorted.length : 10)` into it. Keep row click filtering unchanged.

- [ ] **Step 2: Add the conditional toggle**

For more than 10 rows, append a `.ontology-workbench__cluster-toggle` button. It owns a closure-local `expanded` boolean, sets `aria-expanded`, changes between `展开全部（共 N 类）` and `收起`, toggles the list's `is-expanded` class, rerenders rows, and resets `scrollTop` when collapsing.

- [ ] **Step 3: Add bounded scrolling and control styles**

Use a concrete `max-height: 320px` with `overflow-y: auto` only for `.ontology-workbench__cluster-list.is-expanded`. Style the toggle as a full-width secondary action consistent with the workbench.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the focused pytest command from Task 1. Expected: `1 passed`.

### Task 3: Verify related UI contracts

**Files:**
- No additional code expected.

- [ ] **Step 1: Run the full ontology workbench browser suite**

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_ontology_workbench_ui.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run the frontend static contract suite**

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest tests/service/test_frontend_static_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Inspect the final diff**

Confirm only the approved spec/plan, the two frontend assets, and the focused test changed; preserve the pre-existing SQLite WAL/SHM modifications.

- [ ] **Step 4: Commit implementation**

```powershell
git add -- knowledge_service/web/ontology-workbench.js knowledge_service/web/ontology-workbench.css tests/service/test_ontology_workbench_ui.py docs/superpowers/plans/2026-09-29-discovery-cluster-expand.md
git commit -m "feat: expand discovery category distributions"
```
