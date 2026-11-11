# People Management Readability Implementation Plan

**Goal:** Improve account/role clarity, toolbar alignment, table readability and edit drawer styling, then commit to local master.

**Architecture:** Keep user-admin.js and user-admin.css responsible for the account screen. Retain existing WorkspaceContext.accountPolicy and all API payloads. Use scoped CSS after shared theme rules. Stage only the two asset version changes from index.html, preserving concurrent media-workflow edits.

**Tech Stack:** Native JavaScript/CSS, existing Python Playwright fixtures, Node frontend contracts.

- [x] Review scope and baseline account browser fixtures.
- [x] Add meaningful browser regressions for filter result counts and visible role explanations; observe failure.
- [x] Implement scoped page, toolbar, table and edit-side-panel presentation; retain all policy guards and request logic.
- [x] In parallel, improve all runtime/quality surfaces with scoped CSS: normal-size compact empty states, aligned controls, restrained metrics and consistent panel spacing; preserve operations.
- [x] Bump only user-admin.js / user-admin.css resource versions in index.html.
- [x] Run targeted account browser regressions, frontend contracts and three-width full-style screenshots.
- [x] Review the scoped diff, stage only task files and version changes, commit on master and verify commit contents and remaining unrelated edits.

## Validation

- Frontend Node suite in the current working tree: 1038 passed, 0 failed.
- Personnel browser regressions: 8 passed for account guards, creation/edit entries, filter count and role explanations; separate refresh failure/recovery regression: 1 passed. Browser fixtures use local mock API responses and do not write business data.
- Personnel full-style screenshots at1560/1280/390px: controls38px, no page horizontal overflow, list and create/edit drawer checked. Artifacts: .benchmarks/people-layout/.
- Runtime/quality: 15 empty-view/viewport combinations at1440/900/390px, plus populated overview/task/backup restore-confirmation/quality screens. Empty messages13px, headings16px, normal metrics20–22px. Artifacts: .benchmarks/runtime-quality-layout/.
- Final review approved after restoring wrap on long retrieval evidence buttons.
- Only task assets/tests/docs and six resource cache versions are staged. Concurrent media-workflow changes remain outside this commit.

- Final390px long evidence check passed:104CJK characters wrap within the button and page; no horizontal overflow.
