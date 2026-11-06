# Knowledge Ledger Readability Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Apply the approved table layout, alignment, typography and expandable long text design.

**Architecture:** Keep records-view.js as the ledger renderer and records-view.css as its scoped presentation. Load ledger CSS after shared styles so it owns the ledger layout without changing other pages. Preserve backend pagination and existing handlers.

**Tech Stack:** Native JavaScript/CSS, Python Playwright, Node test runner.

## Task 1: Scoped table and toolbar presentation

Files: knowledge_service/web/records-view.css, knowledge_service/web/index.html.

- [x] Review existing selectors and full stylesheet cascade.
- [x] Set scoped 14px body / 12px metadata, 1.6 line height, top aligned cells, deliberate six/five column widths, nonbreaking operation labels and local horizontal scrolling.
- [x] Align the intro, statistics, tabs, action area and search toolbar. Avoid duplicated borders and excess vertical gaps.
- [x] Place records-view.css after shared CSS and bump its cache version.

## Task 2: Readable full text

Files: knowledge_service/web/records-view.js, tests/service/test_records_ledger_ui.py.

- [x] Add a browser regression for long content expansion and literal HTML safety; observe it fail before implementation.
- [x] Remove irreversible display slicing, add native details for long entity/chunk/value text, preserve relation context and original handlers.
- [x] Bump records-view.js cache version in index.html.

## Task 3: Verify

- [x] Run focused Python ledger browser tests with D:/anaconda/envs/llm_model/python.exe -m pytest -q tests/service/test_records_ledger_ui.py.
- [x] Run Node frontend contracts: node --test tests/service/*.test.cjs.
- [x] Use actual stylesheet order for screenshots at 1560px, 1280px and 390px; inspect four ledger views and local overflow.
- [x] Review diff and report results with any limitations. Do not commit or publish without a user request.

## Validation results

- Frontend Node suite: all 958 passed.
- Ledger browser suite before the final overflow refinement: 24 passed, 3 graph-navigation tests failed. All three failures reproduced with HEAD records-view.js, confirming existing test/API drift (selectEntityDetail versus openKnowledgeRecord). These unrelated graph contracts were left unchanged.
- Final long-text regression: 4 passed, including actual overflow under 80 CJK characters, resized short content, complete entity/chunk/attribute content and HTML safety. Screenshot verification: 14 captures, zero page errors, no page-level horizontal overflow at 1560px, 1280px and 390px; reports recorded in .benchmarks/ledger-layout/.
- The three-width/four-view screenshots use all current stylesheets in index.html order and an admin session fixture; no live backend data was changed.
