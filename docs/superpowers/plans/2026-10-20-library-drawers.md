# Library Drawers Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the isolated sources module and independent review. Steps use checkbox syntax.

**Goal:** Restore list-first source browsing and move file/record input and document details into right-side drawers without losing existing operations.

**Architecture:** A small shared WorkspaceDrawers module owns native-dialog focus/one-active-drawer behavior. LibraryWorkspace wraps the existing ingest section and reuses all original nodes/handlers; the sources module uses the same shell for document details. Existing APIs and project/Milvus data remain unchanged.

**Tech Stack:** Vanilla JavaScript/CSS, native HTML dialog, Node tests, Playwright/Edge browser verification.

## Tasks

- [x] Shared shell + upload: create `knowledge_service/web/workspace-drawers.js`, `library-workspace.js`, `library-workspace.css`; add assets in `index.html`; special-case the existing ingest navigation in `app.js`; close owned drawers on page/project changes. Preserve original IDs and form nodes. Add behavior tests for routing, lifetime, input preservation and project guards before implementation.
- [x] Sources: update `sources-view.js`, create `sources-library.css` and behavioral regression coverage. Full-width compact list, no implicit row click, shared-shell details, request sequence guard, preserve four detail tabs and evidence/deep links.
- [x] Record creation: dock existing `record-create-dialog` to right edge via `record-create.css`; preserve current submission/session/permission checks, add focus restoration where supported.
- [x] Batch deep links: `ingest-batches.js` opens `LibraryWorkspace.open('batches')`, then locates the original batch card. Upload final step and failure/retry flows still use original queue/handlers.
- [x] Verify original failure in browser; run focused Node tests then all frontend regressions, real desktop/mobile drawer flows and a small isolated-project upload. Review spec/implementation; update manual and screenshots, guarded sync to the original checkout; keep the main app running normally.

## Ownership and interface

Sources implementer only edits `sources-view.js`, `sources-library.css`, and its tests. Root owns shared shell, upload, app/navigation, record CSS, docs and integration.

`WorkspaceDrawers.create({id,title,description,content,onClose})` returns `{dialog,body,open({trigger}),close({restoreFocus=true})}`. `title` may be a string or existing heading node; content is moved, never cloned. The shell is appended to body; `open` closes the previously active owned drawer. `onClose` runs on every actual close (including project/page changes) so sources can invalidate pending detail requests. Module loads before sources-view. `WorkspaceDrawers.close({restoreFocus:false})` is used before navigation or external reader/record overlays.

`LibraryWorkspace.open('upload'|'batches'|'structured')` leaves the current page visible beneath a modal; legacy ingest navigation delegates to it. Upload and structured views enforce admin access; batch view remains readable. Closing hides the compatibility ingest section and retains current-project inputs. Project changes close, clear queue/form data and invalidate previous UI context; upload writes remain guarded by existing service handlers.

## Commands

`D:/nodejs/node.exe --test tests/service/workspace-drawers.test.cjs tests/service/library-workspace.test.cjs tests/service/sources-library.test.cjs`

`D:/nodejs/node.exe --test --test-reporter=dot tests/service/*.test.cjs`

`D:/anaconda/envs/llm_model/python.exe scripts/verify_redesign_manual.py --build`

Browser verification must check the running main app and save screenshots/evidence under `docs/research/2026-10-20-library-*`; no large corpus reset or performance reseeding.

## Completed evidence

All frontend Node tests passed. Eight isolated browser lifecycle cases passed (seven together plus the completed-batch direct-switch case). Actual main-app TXT batch completed in the retained UI acceptance project. Delivered assets match all 12 worktree frontend hashes; an unmodified real browser load of the delivered app passed list/drawer/focus/mobile checks. The 150001-entity / 300000-relation capacity project is unchanged. See `docs/research/2026-10-20-library-drawers-delivery.md`, `2026-10-20-library-main-browser.json`, and `2026-10-20-library-delivered-check.json`. Manual: 27 chapters, ten real UI images, static and browser reader checks passed.
