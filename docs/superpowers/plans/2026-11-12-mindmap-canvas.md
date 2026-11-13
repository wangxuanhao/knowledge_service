# Entity mindmap canvas implementation plan

**Goal:** Make entity mindmaps easy to pan, fit into the canvas and give the drawing more space. User approved this scope in chat.

**Architecture:** Keep the existing ECharts tree, data controller and loaded-node explorer. Add viewport-wide pan handling with pointer capture, explicit fit using visible card bounds, and compact collapsible generation settings. Preserve manually chosen views on resize; refit on resize only while the camera is in fit mode.

**Tech stack:** Native JavaScript/CSS/HTML, ECharts, Node tests and Playwright/Edge.

## Tasks

- [x] Extend `tests/service/entity-mindmap-view.browser.cjs`: reproduce dragging from blank margins, horizontal/vertical movement beyond the chart bounds, fitting large visible trees, auto fit on resize and settings collapse.
- [x] Run new tests before changing production code and verify meaningful failures.
- [x] Update `knowledge_service/web/entity-mindmap-view.js`: use pointer capture to pan anywhere, suppress branch clicks after dragging, fit all visible card bounds with padding, start fitted, preserve 100% reset separately, clean up listeners on destroy.
- [x] Update `knowledge_service/web/entity-mindmap-explorer.js`, `mindmap.css` and `index.html`: add fit and collapsible settings controls, reduce top spacing, allocate responsive canvas height without a desktop maximum. Bump modified asset versions while preserving existing unrelated index changes.
- [x] Run browser interaction tests and frontend contracts. Inspect screenshots at desktop and phone sizes; verify no overflow and existing search/path/export still work.

## Acceptance criteria

Drag may begin in any blank canvas margin, continues outside the host and does not toggle branches. Fit includes complete visible cards and centers them, including large/deep graphs and small screens. Manual pan/zoom is not undone by resize. Settings can be collapsed and reopened with keyboard-accessible controls. No backend or graph-data behavior changes.

## Verification (2026-11-13)

- Node frontend contracts: 1083 passed.
- Real Edge/Playwright mindmap interactions: 20 passed, covering mouse and touch, fit, resize, collapse, search, paths and exports at desktop/phone sizes.
- JavaScript syntax and git diff whitespace checks passed.
- Python static UI contracts: 56 passed, 2 failures already present in HEAD (old ingest-mode asset-version expectation and menu-hierarchy grouping expectation); unrelated files left unchanged.
- Independent review: no remaining important findings after restoring two-finger pinch zoom.
