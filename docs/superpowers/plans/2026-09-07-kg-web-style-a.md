# KG Web Style A Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing knowledge-graph Web UI to the approved A “research workbench” design while preserving every current API and business workflow.

**Architecture:** Keep the current zero-build static frontend. Add semantic shell controls to `index.html`, centralize visual tokens and responsive states in `style.css`, and isolate three UI-only state features in `app.js`; existing API, rendering, and domain functions remain unchanged.

**Tech Stack:** FastAPI static files, HTML5, CSS3, vanilla JavaScript, ECharts, Python `unittest` contract checks, in-app browser regression testing.

---

## File map

- Create `tests/test_web_ui_contract.py`: static contract tests for required IDs, design-state hooks, and API preservation.
- Modify `data/kg_web/static/index.html`: navigation grouping, SVG icon language, shell controls, accessibility labels.
- Modify `data/kg_web/static/style.css`: design tokens, A-style shell, component styling, responsive and focus-mode states.
- Modify `data/kg_web/static/app.js`: persisted navigation, graph focus and result-panel states; chart resize coordination.
- Read-only compatibility check `apps/16_kg_web_server.py`: confirm routes/static mounting remain unchanged.

The workspace is not a Git repository. Steps that would normally commit instead verify the timestamped rollback copy in `backups/16_web_before_style_a_20260907_1135/`.

### Task 1: Add frontend contract tests

**Files:**
- Create: `tests/test_web_ui_contract.py`
- Test: `tests/test_web_ui_contract.py`

- [ ] **Step 1: Write baseline ID and script-reference tests**

Use `unittest` and `html.parser.HTMLParser` to assert that every ID queried by `app.js` exists exactly once in `index.html`, `/assets/style.css` and `/assets/app.js` are referenced, and the nine `data-view` values remain present. Also assert that the existing API path families used by the frontend remain present in `app.js`: projects, project load/delete/graph/node, search, categories, ontology/SPARQL, dashboard, mindmap/roots, sources, QA, aliases/merge/resolve, append/history/restore, and eval.

- [ ] **Step 2: Write failing tests for new UI hooks**

Assert the document contains `btn-nav-collapse`, `btn-graph-focus`, and `btn-results-collapse`; assert `app.js` contains storage keys `kg.ui.navCollapsed`, `kg.ui.graphFocus`, and `kg.ui.resultsCollapsed`. Add CSS contract assertions for balanced braces and the required `.nav-collapsed`, `.graph-focus`, `.results-collapsed`, and `prefers-reduced-motion` selectors; these assertions should initially fail on missing state selectors, not on the brace check.

- [ ] **Step 3: Run tests and confirm only new-hook assertions fail**

Run: `python -m unittest tests.test_web_ui_contract -v`

Expected: existing-ID/reference tests pass; new UI hook tests fail because the controls do not exist yet.

### Task 2: Upgrade semantic shell and controls

**Files:**
- Modify: `data/kg_web/static/index.html`
- Test: `tests/test_web_ui_contract.py`

- [ ] **Step 1: Replace the navigation brand and menu presentation**

Keep all existing menu buttons and `data-view` values. Add visual navigation group labels, stable icon spans, text spans, and accessible titles. Do not change IDs used by JavaScript.

- [ ] **Step 2: Add shell controls**

Add:

- `#btn-nav-collapse` at the navigation footer with `aria-label="收折导航"` and `title="收折导航"`.
- `#btn-graph-focus` beside graph scope/reset controls with `aria-label="切换图谱专注模式"`, `title="切换图谱专注模式"`, and `aria-pressed="false"`.
- `#btn-results-collapse` in the search-result header with `aria-label="收折检索结果"`, `title="收折检索结果"`, and `aria-expanded="true"`.

- [ ] **Step 3: Improve top-bar semantics**

Keep `#top-search` and `#topbar-info`; wrap the current project status in a styled project-context element and add a static “服务正常” state label without introducing an API call.

- [ ] **Step 4: Run the HTML contract tests**

Run: `python -m unittest tests.test_web_ui_contract -v`

Expected: ID/view/hook tests pass; JavaScript storage-key tests and CSS state-selector tests still fail until Tasks 3–4.

### Task 3: Implement the A visual system

**Files:**
- Modify: `data/kg_web/static/style.css`
- Test: `tests/test_web_ui_contract.py`

- [ ] **Step 1: Replace global design tokens**

Define cold-gray surfaces, deep-navy navigation, teal primary accent, blue information color, border/shadow/radius/spacing tokens, and 120–220 ms transitions. Preserve existing label colors required by graph business categories.

- [ ] **Step 2: Style the application shell**

Implement grouped deep navigation, active teal rail, compact top bar, project-context treatment, visible `:focus-visible`, and `.nav-collapsed` widths. Collapsed navigation must retain icons and titles while hiding text/group labels.

- [ ] **Step 3: Restyle reusable components**

Unify panels, cards, buttons, inputs, chips, tables, empty states, status blocks, toolbars, QA cards, source panes, forms, ontology/evaluation panes, and scrollbars. Danger actions retain red semantics.

- [ ] **Step 4: Rebalance dashboard and graph workspace**

Reduce KPI-card height, update ECharts container framing, refine the three-column graph layout, strengthen the inspector hierarchy, and make the results panel visually secondary.

- [ ] **Step 5: Add state and responsive selectors**

Add `.graph-focus`, `.results-collapsed`, `.nav-collapsed`, and responsive rules at 1180px and 900px. Under 1180px responsive collapse overrides saved expansion; above it the saved preference applies. Add `prefers-reduced-motion` overrides.

- [ ] **Step 6: Check CSS parse integrity**

Run the brace-balance/static-selector contract created in Task 1 from `tests/test_web_ui_contract.py`.

Expected: PASS with no unbalanced blocks and required state selectors present.

### Task 4: Implement persistent UI behavior

**Files:**
- Modify: `data/kg_web/static/app.js`
- Test: `tests/test_web_ui_contract.py`

- [ ] **Step 1: Add safe local preference helpers**

Add `readUiPref(key, fallback)` and `writeUiPref(key, value)` using `try/catch` so unavailable storage never blocks startup.

- [ ] **Step 2: Add navigation state**

Implement `applyNavState()`, toggle `.nav-collapsed` from `#btn-nav-collapse`, update `aria-expanded`/title, persist `kg.ui.navCollapsed`, and respond to crossing the 1180px breakpoint without overwriting the saved desktop preference.

- [ ] **Step 3: Add graph-focus state**

Implement `applyGraphFocusState()`, toggle `.graph-focus` on `#view-graph`, update `aria-pressed`, persist `kg.ui.graphFocus`, then schedule `chart.resize()`.

- [ ] **Step 4: Add search-result collapse state**

Implement `applyResultsState()`, toggle `.results-collapsed`, update `aria-expanded`, persist `kg.ui.resultsCollapsed`, then schedule `chart.resize()`.

- [ ] **Step 5: Initialize UI preferences before data loading**

Apply all three preferences during existing startup initialization without changing project/API initialization order.

- [ ] **Step 6: Run contract and JavaScript syntax checks**

Run:

```powershell
python -m unittest tests.test_web_ui_contract -v
node --check data/kg_web/static/app.js
```

Expected: all contract tests pass and Node exits 0.

### Task 5: Browser regression and visual verification

**Files:**
- Verify: `data/kg_web/static/index.html`
- Verify: `data/kg_web/static/style.css`
- Verify: `data/kg_web/static/app.js`
- Verify: `apps/16_kg_web_server.py`

- [ ] **Step 1: Reload the running app at 1440×900**

Verify dashboard, loaded-project status, graph workspace, node inspector, and all nine navigation destinations. Confirm browser console has no errors.

- [ ] **Step 2: Verify the three new interactions**

Toggle navigation collapse, graph focus, and results collapse. Reload and confirm preferences restore. Expand again and confirm ECharts resizes without clipping.

- [ ] **Step 3: Run read-only functional smoke checks**

Verify project load, graph scope selection/reset, graph search, source-document opening, mind-map load, QA controls, increment/history display, ontology controls, and evaluation page initialization.

- [ ] **Step 4: Run reversible write-workflow checks in a disposable project**

Create a disposable project through the existing built-in demo parse flow and record its exact returned project name. In that project only, add an entity, create a relation involving it, add an alias, then delete that entity and verify it and its incident relation disappear. Next trigger a disposable demo increment to create history, restore that generated history version, then delete the disposable project through the existing UI/API. Verify the original four projects and backup directory are unchanged. If disposable project creation fails, report the write-workflow regression as unverified rather than testing against user projects.

- [ ] **Step 5: Verify responsive layouts**

Inspect 1280×800 and 1024×768. Confirm 1180px auto-collapse precedence, no top-bar overlap, graph remains usable, and under-900 fallback does not crush the canvas.

- [ ] **Step 6: Run final checks and compare backup hashes**

Run:

```powershell
python -m unittest tests.test_web_ui_contract -v
node --check data/kg_web/static/app.js
python -m py_compile apps/16_kg_web_server.py
```

Expected: all commands exit 0. Confirm backup files remain unchanged and report changed production files separately from `.superpowers` artifacts.
