# Graph-first Workspace Implementation Plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development, with sequential implementers and independent spec then quality review. Root owns all pytest/Playwright runtime sessions; exactly one pytest, no benchmark alongside it. Steps use checkbox tracking.

**Goal:** Ship the approved graph-first page with a compact toolbar, remaining-height canvas, one results/detail pane, bounded real overview and exact scoped record focus; verify and merge into local master.

**Architecture:** Keep authority, retrieval and graph-session APIs unchanged. A page-owned layout module moves existing DOM controls without cloning IDs or replacing scope ownership. A separate overview coordinator reuses FullGraphView/SessionLoader and existing qualified record/neighborhood reads, with explicit navigation taking priority over default loads. Keep both graph renderers, source/history/permission rules, and previous hit-target fixes.

**Stack:** Native JS/CSS, existing ECharts/WebGL, Node26 contracts, existing Playwright/pytest with `D:/anaconda/envs/llm_model/python.exe`. No dependencies or build system.

**Approved spec:** `docs/superpowers/specs/2026-11-05-graph-workspace-layout-design.md`; user chose the recommended canvas preview and explicitly authorized implementation/local master merge on2026-11-05. No remote push.

## Preparation and ownership

- [x] Snapshot all preexisting dirty files as raw bytes in ignored `.benchmarks/graph-workspace-implementation/before.zip` and hashes in `before.json`; reuse current checkout on `feat/graph-first-workspace-20261105`.
- [x] Attempt Hindsight search/list/initiative capture before code; backend refused connection, so no memory facts used.
- [x] Independently review this plan against the approved spec; address substantive gaps. graph_workspace_plan_review: Approved, no substantive gaps.
- [x] Preserve all earlier graph hit-target work. Existing graph-surface/workspace/index edits concern this page: retain their behavior, do not overwrite unrelated changes; include relevant graph changes in the final reviewed commit. The four retained appearance/surface/workspace/workbench files match the initial raw-byte snapshot.

## Task 1: Compact shell and one fixed sidebar

**Create:** `knowledge_service/web/graph-workspace-layout.js`, `knowledge_service/web/graph-workspace-layout.css`, `tests/service/test_graph_workspace_layout_browser.py`.

**Modify:** `knowledge_service/web/index.html`, `knowledge_service/web/workspace-context-ui.js`; current `graph-presentation.js` only if necessary for fullscreen compatibility. Do not change data loading, backend, graph hit targets or scope semantics in this task.

- [x] Add real-browser regressions using the existing isolated UI fixture: one compact toolbar containing existing query/search/scope controls, no duplicate IDs, scope control still moves correctly on QA/page switches; initially hidden sidebar; search results and details occupy the same pane; opening details preserves canvas height and fixes pane width; 1440/1366/1024/768/390/320 layouts and fullscreen remain operable.
- [x] Root runs representative new tests before production edits; capture the intended RED result under `.benchmarks/graph-workspace-implementation/`. Three representative cases failed on the missing header before production edits; additional settings/focus regressions also reproduced before their fixes.
- [x] Implement the layout as a new scoped stylesheet loaded after existing graph styles. Main workspace fills remaining viewport height; compute its available top offset through the actual element/viewport (resize/visibility updates, not a continuous loop). No `100dvh - 550px` canvas budget on this page. Keep controls/fonts usable and allow the right pane to scroll independently. On narrow screens use a bounded bottom pane.
- [x] Reparent existing `#scope`, search form/row, `#hits`, and inspector contents without duplicating IDs or losing listeners. Provide `window.GraphWorkspaceLayout` with `placeScope(panel)`, `openResults()`, `openDetail()`, `closePanel()`, `setState(kind, detail)`, `resize()`, and `settled()`; optional read-only `snapshot()` for behavioral checks. `placeScope` returns whether it placed the unique scope panel for the active search page; other pages retain current `restore()` behavior. `settled()` resolves after a finite layout/renderer resize update so Task2 focuses after pane sizing.
- [x] Add one lifecycle-safe MutationObserver/event adapter for actual results/detail updates, scoped to existing elements. Closing a panel must not immediately reopen from the module's own tab/visibility changes; clearing old details should not blank results or canvas. Keep result scroll state, source reader, path tools and focus restoration. Avoid broad per-frame DOM scans.
- [x] Keep all existing IDs/actions used by graph controls, timeline, drawers and scope code. Cache-bump every changed resource. Reuse the brand and compact visual direction from the approved preview; do not copy throwaway preview data/logic into production.
- [x] Root runs the full new layout browser module; fix actual failures. Update only genuinely obsolete layout assertions in affected old contracts, retaining original intent/negative guards.
- [x] Independent spec review, then independent code-quality review, address findings and reverify affected tests before Task2.

Example observable contract:

```javascript
GraphWorkspaceLayout.openResults(); // existing #hits visible, details not a second column
GraphWorkspaceLayout.openDetail();  // same fixed pane, canvas height unchanged
GraphWorkspaceLayout.closePanel();  // canvas regains width, existing graph stays loaded
GraphWorkspaceLayout.setState('loading', {message: '正在读取当前范围的图谱'});
```

Task1 evidence: root `layout-final.xml` reports **13 passed** (465.55s); independent spec and quality reviews Approved after fixing legacy cascade, mobile navigation budget, settings clipping and keyboard focus.

## Task 2: Real bounded initial overview and explicit record focus

**Create:** `knowledge_service/web/graph-overview.js`, `tests/service/graph-overview.test.cjs`, `tests/service/test_graph_overview_browser.py`.

**Modify:** `knowledge_service/web/full-graph-view.js`, `knowledge_service/web/workspace.js`, `knowledge_service/web/full-graph.js`, and `graph-workbench.js` only for a local focus/fit helper; `workbench.js` only for an explicit existing-owner readiness hook if needed; `index.html` for ordering/cache versions; minimal `records-view.js` canonical entity/relation navigation where the prior ledger action discarded relation identity. Preserve earlier node/edge sizing/picking. No new API/table/model dependency.

- [x] Add behavioral tests before implementation for default entry and scope/project changes, known_at/history, no-index graph reads, graph-free project, isolated entities, large manifests, incomplete/failed pages, manual cancel, restore/re-entry, A→B→A, and an explicit record arriving during default loading. Use the real SessionLoader where possible and faithful bounded API stubs. Root runs representative browser RED cases; Node RED may be owned by the single implementer with root awareness.
- [x] Wait for the existing project/scope owner to finish restoring the new project's context and ontology controls before default loading. Add a narrow explicit readiness event/hook if necessary; do not poll or guess with timeouts. An old readiness completion must not start a new request for the wrong project.
- [x] Implement a request-owned overview coordinator. Key successful views by project plus exact applied scope and ontology/history context. Abort/invalidate obsolete work, preserve a valid view on tab re-entry, and remember manual cancellation for that same key. Applied scope changes or meaningful user retry may start a new view. Never silently remove metadata/time/ontology filters.
- [x] Reuse existing FullGraphView and SessionLoader with batchSize200 for automatic overview: one page by default; if manifest<=200entities/400relations, complete within at most3page requests total. A relation page has at most200relations/400endpointentities. Stop automatic reading for large projects; retain next/load-all/cancel controls. No requirement for Milvus readiness on PostgreSQL graph reads.
- [x] Initial/empty/error/partial/complete states use authoritative counts and explicit copy. An error is not zero knowledge. Pending candidates are not formal graph data. Distinguish loaded totals from a type-filtered visible projection. Existing manual progressive loads, pause, cancellation and invalidation remain functional.
- [x] Treat record opening/navigation separately from a canvas selection: ordinary canvas clicks only inspect the selected record, while explicit '在图谱查看'/ledger/source actions ensure it is actually displayed and focused. Avoid recursion/double authority reads from a renderer focus helper calling its onSelect callback.
- [x] For an already-loaded same-scope matching-version entity or relation, focus without rebuilding/reloading. For an unloaded object, use the existing exact qualified record and bounded neighborhood reads under its exact effective context. A relation must have both qualified endpoints, its exact edge ID and version, and a camera that fits both endpoints; do not invent endpoints or broaden conditions. Source chunks/docs continue through the source reader; virtual attributes resolve to their entity/record correctly.
- [x] A historical/fixed scope passed to `openKnowledgeRecord` must be used for the canvas read, not replaced by the current controls. Current context changes still invalidate old results. Type/predicate restrictions must be handled explicitly; if they exclude required endpoints, show the scoped reason instead of clearing them silently.
- [x] Root runs new overview browser module and targeted existing scope/history/graph entry checks. Shared browser fixture defaults must return valid empty graph-session manifests/pages rather than `{}`. Adapt prior manual-load tests through public restart/user actions when a default overview is now present; do not disable automatic behavior to hide failures.
- [x] Independent spec review followed by quality review; fix findings and rerun relevant cases.

Key outcomes to assert, not implementation text:

```javascript
// Large project: only manifest + first bounded page, with partial=true.
// Small project: <=3 page requests and exact dataset counts.
// Explicit selection invalidates older default request before it can repaint.
// A relationship's visible endpoints and picked edge match the authority record.
// Same frozen history/scope is sent by both record and canvas requests.
```

Task2 evidence: overview module **13 passed**; exact scope/empty guidance fixes **4 passed**; final v4 lifecycle/filter regressions **4 passed**. Independent spec and quality reviews Approved after addressing exact history navigation, actionable empty guidance, abandoned pre-paint navigation and active scene-filter races. Final integrated review Approved; serialized compatibility cohort remains in progress below.

## Task 3: Integrated verification, commit and local master merge

**Modify:** related existing Node/browser fixtures only when the accepted behavior changed; final spec/plan status. Keep implementation reports in ignored benchmark directory, concise durable results in spec/plan or `docs/research` if useful.

- [x] Run full native contracts: `node --test tests/service/*.test.cjs`; frozen v4 reports **958 passed, 0 failed, 0 skipped**, including version/load-order and dense-render/filter guards (`native-final-v4.log`).
- [x] Run one serialized Python/browser cohort covering new layout/overview, `test_graph_hit_targets_browser.py`, `test_workspace_redesign_ui.py`, `test_frontend_retrieval_flow.py`, `test_unified_scope_path_browser.py`, `test_graph_version_roundtrip.py`, `test_temporal_scope_browser.py`, and actual relationship pixels. Final aggregation confirms **121 distinct cases passed**, including setup/call/teardown, with no skipped cases. Resumes retained earlier passes; the final fixture recheck reports **15 passed**. Software WebGL pixel checks passed at DPR **1/1.25/2**.
- [x] Inspect five rendered states: desktop overview/detail, phone detail, empty guidance and fullscreen-to-sources navigation. Desktop canvas stays **550px** high when the **320px** pane opens; phone has no horizontal overflow. Normal service health is **HTTP200**, and all **16** changed asset responses match their local bytes/cache URLs. The service was stopped at resumption and was started normally; no business project was modified for verification.
- [x] Final independent integrated review Approved; final fixture adaptations separately Approved. All **119** tested top-level page assets retain their frozen v4 SHA256 hashes; the four prior appearance/surface/workspace/workbench files also retain the initial snapshot bytes. No backend/dependency/migration changes or prototype data are included. `git diff --check` passed.
- [ ] Commit only the reviewed feature, its earlier graph hit-target fixes, relevant retained page styles, tests and approved spec/plan. User expressly authorized committing/local master merge; do not push. Keep read-only preview and temporary files ignored.
- [ ] Fast-forward local master when still an ancestor. Use guarded same-tree switching/preservation to avoid rewriting the running source or losing pending edits; otherwise inspect and handle any real conflict. Verify branch/commit/tree and a meaningful post-merge check; clean up only this feature branch, not unrelated worktrees.
- [ ] Report concise Chinese outcome, local master commit, relevant checks/limits and actual service URL. No 'all functionality flawless' claim from frontend tests.

Final verification evidence (2026-11-06): ignored `final-verification.json`, `integrated-v4-fixturefix1.xml`, `native-final-v4.log`, `visual-live-verification.json` and `.benchmarks/graph-sharpness/after.json`. Earlier failed runs record obsolete manual-load/layout fixtures and occasional Edge page-creation stalls; the final aggregate requires a passing latest setup/call/teardown for every selected case. Fixture changes retain delayed-success stale-response guards, exact entity/relation identity, history/scope exclusions and no-rebuild checks. Hindsight remained unavailable, so no memory claims informed the implementation.
