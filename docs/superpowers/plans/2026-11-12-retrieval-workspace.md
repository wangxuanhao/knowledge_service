# Retrieval Workspace Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan. Steps use checkbox syntax for tracking.

**Goal:** 将已认可整页设计落到真实检索图谱页面，增加图标导航收缩，同时保持全部原有功能。

**Architecture:** 维持原生 JS 模块的查询/渲染/权限拥有者，用呈现模块移动原控件、统一 CSS 和本地线性 SVG 图标。桌面导航偏好与移动端菜单状态独立。复用原有 DOM 节点与事件，而不是重建业务控件。

**Tech Stack:** Native JS/CSS, FastAPI static assets, Node test runner, existing Python Playwright fixtures.

---

### Task 1: Collapsible icon navigation

Files: new `knowledge_service/web/workspace-icons.js`, new `knowledge_service/web/navigation-shell.js` / `.css`, new `tests/service/navigation-shell.test.cjs`; register/version assets in `index.html` (root integration owns this file).

- [x] Read current menu grouping, role visibility, mobile menu and session rendering. Keep existing tab buttons and loaders intact.
- [x] Write and run behavior tests for collapse/expand, persisted state, denied localStorage, labels, role-hidden destinations, project access and mobile independence.
- [x] Implement local icon factory with allowlisted paths (no CDN); annotate navigation without replacing original buttons. Add desktop collapse toggle; show project selector/session access on compact rail with accessible names.
- [x] Run tests and hand integration requirements to root; do not commit or edit shared index.html.

### Task 2: Unified graph workspace presentation

Files: new `knowledge_service/web/retrieval-workspace.js` / `.css`; minimal hooks to existing presentation only if required; new `tests/service/retrieval-workspace.test.cjs`; `index.html` asset registration/version bumps.

- [x] Inventory and record existing controls across workspace, scope, local graph, full graph, neighbor/path, timeline, details, maintenance and full screen. Inspect actual current layout overrides.
- [x] Add behavior tests asserting original controls retain identity/listeners; mode navigation, common settings appearance, original range/apply, all full-load actions, search expansion/settings survive; run to establish missing implementation failure.
- [x] Compose title/search/range into separate rows; move mode switch to consistent left header; unify settings and graph type entry, retain original controls and dynamic states. Use local icons without replacing dynamic text nodes managed by other modules.
- [x] Add CSS scoped to retrieval page, using theme variables. Shared button dimensions, scroll/overflow/focus and consistent canvas tools, accessible settings. Preserve current adaptive pane and fullscreen behavior.
- [x] Register assets after their dependencies with new versions. Do not overwrite concurrent ontology changes to index.
- [x] Run focused Node contracts, then full Node suite; fix regressions.

### Task 3: Browser completeness and visual verification

Files: extend current `tests/service/test_graph_workspace_layout_browser.py` or add focused browser test using its HTTP fixture; outputs under `.benchmarks/retrieval-workspace/`.

- [x] Use actual HTML/assets and bounded fixture APIs (not login or live credentials). Verify two graph modes, original advanced controls, drawer closing and Escape, filter apply state and existing function contracts.
- [x] Verify expanded/compact desktop navigation, tablet/mobile menu and layout, role-hidden entries, project switching access, graph zoom/fullscreen and no viewport overflow. Capture screenshots.
- [x] Run relevant static/Python UI tests with `D:/anaconda/envs/llm_model/python.exe -m pytest`, one session only; no `.env` loading.
- [x] Request independent spec then code-quality review, resolve important issues, rerun affected checks; report evidence and limitations. No commit unless requested.

## Verification record

- Full Node contracts: 1060 passed.
- Latest focused browser regressions: 17 passed (collapse/reload, project access, shared settings, restart after moving original full controls, candidate notice hit testing, 320/390/768/1024/1440 layout, mode selector labels, desktop detail geometry).
- Screenshots inspected under `.benchmarks/retrieval-workspace/`; actual localhost:8100 assets return HTTP 200.
- All 275 original HTML IDs and all 12 navigation destinations retained without duplicate IDs.
- Broader initial Python run: 86 passed / 13 failed. Six layout failures were corrected and covered by the latest passing run. Seven existing static failures were verified against original HEAD `1996d4114452a3aca053d1c39f966bbe4c2bd2b6`; evidence `.benchmarks/retrieval-baseline-static.json`. They concern retired literal/version assertions in untouched modules; unrelated functionality was not rewritten.
- Spec and quality review passed after fixing legacy CSS specificity and candidate notice clipping.
- Final focused full-screen/path/source/keyboard regressions: 5 passed. Latest browser verification total: 22 passed across the two focused runs; no skipped tests.
