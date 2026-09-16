# Search Graph and Knowledge Chat Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore search and interactive graph as one lazy-linked workspace and replace the separate evidence form with a polished, DeepSeek-inspired “知识问答” chat experience.

**Architecture:** Keep the existing project-scoped `/search`, `/subgraph`, and `/qa/stream` contracts. Reshape only the frontend information architecture and interaction state: search updates a left result rail without touching the center graph, explicit result/evidence actions load a local subgraph, and each knowledge-chat turn independently consumes one SSE response. Preserve the current app’s plain HTML/CSS/JavaScript stack and real-browser Playwright coverage.

**Tech Stack:** FastAPI static frontend, vanilla JavaScript, CSS Grid, SSE via Fetch streams, ECharts, pytest, Playwright with installed Chromium/Edge.

---

## File map

- `knowledge_service/web/index.html`: navigation labels and semantic page shells.
- `knowledge_service/web/app.js`: tab titles, scope visibility, project-change clearing.
- `knowledge_service/web/workspace.js`: combined search/graph state, lazy graph actions, responsive details behavior.
- `knowledge_service/web/workbench.js`: knowledge-chat transcript, SSE parsing, retry, evidence disclosure/navigation, keyboard behavior.
- `knowledge_service/web/workspace.css`: three-column workbench and focused chat visual system.
- `knowledge_service/web/menu-hierarchy.js`: sidebar ordering and renamed menu entry.
- `tests/service/test_frontend_retrieval_flow.py`: real browser interaction, request-count, state-preservation, keyboard, ARIA, responsive tests.
- `tests/service/test_workbench.py`: static DOM/control regression contract.

### Task 1: Lock the combined-workspace contract in failing tests

**Files:**
- Modify: `tests/service/test_frontend_retrieval_flow.py`
- Modify: `tests/service/test_workbench.py`

- [ ] Add static assertions that the sidebar exposes “检索与交互图谱” and “知识问答”, with no separate “知识检索” or “证据问答”; require one `search-mode`, one `graph-hops`, semantic headings/regions, visible labels, and the approved initial graph instruction.
- [ ] Assert the deleted legacy DOM id `retrieval-mode` is absent and the graph toolbar retains the sole hops selector, entity locator, and reset-view action above the canvas.
- [ ] In the real browser, preload a local graph and details, record their DOM content and selected ID, execute a search, and assert all three remain identical; assert typing alone sends no request.
- [ ] Inspect the `/search` body and require `query`, `retrieval_mode`, `filters`, `valid_at`, `known_at`, `include_unknown`, `k_entities`, `k_chunks`, `k_relations`; assert the response renderer ignores any accidental `nodes`/`edges` and shows all three headings, counts, and independent empty states.
- [ ] Assert the search button and Enter submission each issue exactly one `/search`, while ordinary typing/autocomplete remains request-free.
- [ ] Assert an entity result and a relation result (seeded by `subject_id`) each replace the graph through exactly one project-scoped `/subgraph` whose body contains only `node_id`, `hops`, and scope/time fields.
- [ ] Assert project change clears results, graph, selection and detail without graph loading; scope/time change clears graph, selection and detail but leaves result DOM untouched.
- [ ] Run `py -m pytest tests/service/test_frontend_retrieval_flow.py tests/service/test_workbench.py -q` and verify failures name only the missing combined-workspace behavior.

### Task 2: Rebuild the combined search and graph workspace

**Files:**
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/app.js`
- Modify: `knowledge_service/web/workspace.js`
- Modify: `knowledge_service/web/menu-hierarchy.js`
- Modify: `knowledge_service/web/workspace.css`

- [ ] Replace the separate search and graph navigation entries with one visible “检索与交互图谱” entry backed by a single tab shell.
- [ ] Build the 300px / flexible / 280px result-graph-detail grid, with the single search-mode selector in the result rail and the single graph-hops selector above the graph.
- [ ] Make `runSearch()` update only the result rail and summary; preserve `ui.graph`, canvas, selected node, and details.
- [ ] Keep graph initial/project/scope/time transitions safe: project clears results and graph; scope/time clears graph/detail/chat but preserves results; only explicit entity/relation actions call `/subgraph` and input/autocomplete never searches.
- [ ] Implement >1100px three columns, 701–1100px two columns plus a closable detail drawer, and <=700px stacked search/graph/detail with graph height at least 55vh and no overflow.
- [ ] Run the focused browser/static tests and verify the combined-workspace assertions pass.

### Task 3: Lock the one-shot knowledge-chat contract in failing tests

**Files:**
- Modify: `tests/service/test_frontend_retrieval_flow.py`
- Modify: `tests/service/test_workbench.py`

- [ ] Add a browser test that inspects one project-scoped `/qa/stream` request body for `query`, `generate`, `retrieval_mode` and scope/time fields and asserts there is no `/search`, `/explore`, legacy `/qa/stream/{name}`, `start`, or `token` behavior.
- [ ] Feed SSE chunks split across arbitrary boundaries and CRLF, then assert `evidence → delta* → done` ordering, progressive delta rendering, final-buffer processing, completion, and the valid zero-delta `evidence → done` case. Add explicit malformed JSON, HTTP failure, cancellation, premature EOF, and `event: error` cases.
- [ ] Assert in-flight send is disabled with textual loading state; error preserves the user turn, focuses retry, and retry replays the exact request. Assert transcript is visually multi-turn but every send omits prior messages.
- [ ] Assert evidence disclosure ARIA, default collapsed state, keyboard activation, no action for missing IDs, entity/relation evidence-to-graph focus and one `/subgraph`, and chunk `source_id` navigation to the exact source in “原文数据源”.
- [ ] Assert Enter sends, Shift+Enter inserts a newline, IME composition never sends, focus remains in the composer after success, and the polite live region batches sentences/completion rather than every token.
- [ ] Assert project/scope/time changes clear chat and render a lightweight “范围已变化” notice.
- [ ] Run `py -m pytest tests/service/test_frontend_retrieval_flow.py tests/service/test_workbench.py -q` and verify failures identify only the missing knowledge-chat behavior.

### Task 4: Implement the independent DeepSeek-style knowledge-chat page

**Files:**
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/app.js`
- Modify: `knowledge_service/web/workbench.js`
- Modify: `knowledge_service/web/workspace.css`

- [ ] Rename all user-facing “证据问答” labels to “知识问答” while retaining evidence-grounded backend semantics.
- [ ] Replace the panel form with a borderless centered reading column capped near 900px, exactly three clickable empty-state example questions, assistant/user turns, and a sticky rounded composer whose generation toggle sits in its toolbar and send button sits at the lower-right.
- [ ] Implement one-shot turn state: append the user turn, disable send, consume evidence → delta* → done, batch live-region announcements, and preserve failed turns with an exact-request retry button.
- [ ] Add evidence disclosure with correct ARIA and actions for graph/source navigation.
- [ ] Implement exact-source selection state for chunk evidence, Enter-to-send, Shift+Enter newline, IME composition protection, exact-request retry, robust SSE buffering/error/EOF/cancellation handling, and scope-change chat clearing with notice.
- [ ] Add semantic landmarks/headings, visible focus, keyboard-operable actions, batched `aria-live="polite"`, textual/icon loading and error states, and AA-contrast colors; focus composer/retry/graph heading according to the spec.
- [ ] Implement a desktop content-area-fixed composer and mobile sticky composer with safe-area padding; use `window.visualViewport` resize/scroll offsets (with CSS viewport fallback) to keep it above a reduced soft-keyboard viewport; preserve horizontally scrollable grouped navigation.
- [ ] Run the focused browser/static tests and verify chat assertions pass.

### Task 5: Full verification and live handoff

**Files:**
- Test only; no new production behavior.

- [ ] Run `node --check knowledge_service/web/app.js`, `node --check knowledge_service/web/workbench.js`, and `node --check knowledge_service/web/workspace.js`.
- [ ] Run the real browser from a clean page at 1280px, 1100px, 900px, 700px, and 390px; capture console/page errors; inspect exact request bodies/counts, SSE order/error/retry, graph toolbar, chat geometry/composer controls, graph/source focus, drawer/stack order, graph height, horizontal overflow, and a simulated visual-viewport height reduction for soft-keyboard avoidance.
- [ ] Run `py -m pytest tests/service/test_frontend_retrieval_flow.py tests/service/test_workbench.py tests/service/test_search_budgets.py tests/service/test_hybrid_retrieval.py tests/service/test_explorer.py -q`.
- [ ] Run `git diff --check` and inspect the scoped diff against the approved spec.
- [ ] Resolve the listener with `Get-NetTCPConnection -LocalPort 8100 -State Listen`, verify its command line is exactly the owned `python -m knowledge_service` process, stop only that PID, restart the same executable hidden in `D:\workspace\knowledge_service`, and document the new PID.
- [ ] Verify `/api/health`, the combined page assets, one live keyword `/search`, and one live `/qa/stream`; leave the restarted service running for manual verification.
