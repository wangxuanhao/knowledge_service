# Progressive Graph and Retrieval Controls Implementation Plan

> **For agentic workers:** Use isolated implementers for graph session/renderer and backend neighborhood bounds, with root owning workspace/picker/settings integration. Review specification and code before final delivery.

**Goal:** Make graph exploration usable before complete loading, restore visible relationships and reliable entity-to-mindmap navigation.

**Architecture:** Extend owner-bound graph session pages with a relation+endpoint page preserving the same scope/watermark. SessionLoader retains resumable cursors and deduplicates endpoint hydration; entity sweep covers isolated nodes. Root adapts page controls and independent picker state without loading a corpus into select options. All input/request boundaries validate actual scopes.

**Stack:** Vanilla JS/CSS, ECharts, native drawer shell, WebGL, FastAPI, PostgreSQL scoped reads, Node and targeted Python tests, Playwright/Edge.

- [x] Root: tests/red-loop for selection/mindmap/paging and settings closure; workspace.js and supporting UI modules, index.html, relevant tests. Numeric 0–10 hops with no hidden clamp.
- [x] Progressive implementer: services/graph_sessions.py, api/graph_sessions.py GraphPage only, web/full-graph.js, full-graph-view.js/css and dedicated tests. Relation-first endpoint hydration in one frozen session; first-batch pause and resumable manual/auto modes; correct alpha and line pixels; complete orphan sweep.
- [x] Backend implementer: repository/scoped_reads.py neighbors hop range, api/graph_sessions.py GraphNeighborhood hop range only; backend brain endpoint scoped/bounded behavior if needed after root evidence. Targeted tests for >3 hops, invalid numbers and unchanged scope/budgets.
- [x] Independent review: spec and code quality, especially scope ownership, watermark/cursor fences, late requests, true line visibility and no false full completion.
- [x] Root integration: focused and broad relevant tests, real retained-project browser first-batch and complete-mode check, docs/manual/screenshots, guarded sync and normal server restart if backend changed; verify delivered UI/API.

No corpus migration/reset. Source/primary checkout contains existing work; integrate only via guarded sync helper. Avoid concurrent encoder processes. Never print env/DSN/token values. Native browser screenshots/tests may use real main APIs but must not mutate the capacity project.

交付证据：docs/research/2026-10-20-progressive-graph-delivery.md。308 项 Node、27 项后端/像素、10 项页面浏览器与 10 项静态契约通过。主应用首批、暂停续载、150001/300000 完整计数及准确实体脑图通过；完整加载 30 秒目标仍未通过，未将其标为性能达标。
