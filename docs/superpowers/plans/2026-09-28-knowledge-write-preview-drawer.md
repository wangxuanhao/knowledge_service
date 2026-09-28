# Knowledge Write Preview Drawer Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inline first-document chunk preview with an accessible right-side drawer and clarify the structured JSON record import as a default-collapsed advanced entry.

**Architecture:** Keep the existing preview endpoints and document-ingest data flow unchanged. Move `#chunk-preview` to a body-level overlay host, render the drawer from `workbench.js`, and add focused drawer styles to `workbench.css`; retain the existing structured write IDs and handler inside a semantic `details` wrapper. Protect the zero-build frontend with static UI contract tests and manual browser verification.

**Tech Stack:** Static HTML, vanilla JavaScript, CSS, Python/pytest contract tests.

---

## File structure

- Modify `knowledge_service/web/index.html`: move the preview host to a body-level sibling of the app shell, add accessible drawer markup hooks, wrap structured import in a default-collapsed advanced `details`, and bump `workbench.js` / `workbench.css` cache versions.
- Modify `knowledge_service/web/workbench.js`: render preview state, switch selected chunks, open/close the drawer, isolate background roots, trap/restore focus, and handle loading/empty/error states.
- Modify `knowledge_service/web/workbench.css`: style the overlay, drawer, chunk navigation, responsive layout, loading state, scroll lock, and reduced-motion behavior.
- Modify `tests/service/test_workbench.py`: add a small static contract for preserved IDs, copy, and cache-version bumps.
- Create `tests/service/test_chunk_preview_ui.py`: run the real application with Playwright and verify DOM structure, drawer interactions, accessibility state, escaping, replacement, empty/error states, and responsive behavior.

### Task 1: Lock the HTML and browser behavior with failing tests

**Files:**
- Modify: `tests/service/test_workbench.py`
- Create: `tests/service/test_chunk_preview_ui.py`
- Test: `tests/service/test_workbench.py`
- Test: `tests/service/test_chunk_preview_ui.py`

- [ ] **Step 1: Write the small static contract test**

Append a focused test that reads `index.html` and asserts only stable shipping contracts:

```python
def test_document_preview_uses_accessible_drawer_and_structured_import_is_advanced():
    html=(ROOT/'index.html').read_text(encoding='utf-8')

    assert html.count('id="chunk-preview"') == 1
    assert '结构化记录导入（高级）' in html
    assert '不是批量上传文档' in html
    assert html.count('id="batch"') == 1 and html.count('id="write-batch"') == 1
    assert '/assets/workbench.css?v=document-upload-2' in html
    assert '/assets/workbench.js?v=document-upload-2' in html
```

- [ ] **Step 2: Create the real-browser contract fixture**

Create `tests/service/test_chunk_preview_ui.py`, following the existing in-process uvicorn/Playwright pattern in `tests/service/test_frontend_retrieval_flow.py`:

- use `create_app(tmp_path / 'preview-drawer.sqlite', HashingEncoder())`;
- create one project through the repository and save the default ontology;
- start uvicorn on an ephemeral localhost port in a daemon thread;
- launch installed Edge/Chrome headlessly;
- navigate to `/`, select the project, and open `[data-tab="ingest"]`;
- collect page errors and always stop the isolated server in fixture teardown.

Add browser tests that intercept the preview endpoints with deterministic payloads and assert:

1. `#chunk-preview` is a direct child of `body`; the drawer has dialog name/description attributes; the structured-import `details` is closed by default and contains `#batch` / `#write-batch`.
2. A successful preview opens the drawer, sets `inert` on both `body > aside` and `body > main`, focuses the close button, renders two native chunk buttons, marks one with `aria-current="true"`, switches正文 without another request, and renders `<img ...>` input as text rather than DOM.
3. Repeating preview replaces the old buttons instead of appending; `Escape` closes, clears both `inert` attributes, and restores focus to `#preview-chunks`; clicking the backdrop also closes.
4. An empty successful response shows the empty state without a chunk button; a failed response leaves the drawer closed, reports the error, and leaves `#preview-chunks` enabled through the existing `bind()` wrapper.
5. `Tab` / `Shift+Tab` cycle inside the drawer, and 850px/520px viewports keep the drawer and close button within the viewport without horizontal document overflow.
6. File mode sets a small in-memory `.txt` file on `#doc-file`, intercepts `**/documents/upload/preview`, verifies the upload-preview request is used instead of the text endpoint, and opens the same drawer with the first file's returned chunks.

- [ ] **Step 3: Run both tests and confirm the intended failures**

Run: `python -m pytest tests/service/test_workbench.py::test_document_preview_uses_accessible_drawer_and_structured_import_is_advanced -q`

Expected: FAIL because the copy and cache versions do not exist.

Run: `python -m pytest tests/service/test_chunk_preview_ui.py -q`

Expected: FAIL because the current preview host remains inline and has no drawer behavior. If Playwright or a supported browser is unavailable, the module skips with its explicit environment reason.

- [ ] **Step 4: Commit the red tests**

```bash
git add tests/service/test_workbench.py tests/service/test_chunk_preview_ui.py
git commit -m "test: define knowledge write preview drawer contract"
```

### Task 2: Add the drawer shell and clarify structured import

**Files:**
- Modify: `knowledge_service/web/index.html:2,8-15,48`
- Modify: `knowledge_service/web/workbench.css:3-18`
- Test: `tests/service/test_workbench.py`

- [ ] **Step 1: Update the HTML structure**

Make these exact structural changes:

1. Remove the inline `<div id="chunk-preview"></div>` from `.document-upload-panel`.
2. Wrap the structured importer in `<details class="structured-import">` without an `open` attribute. Use a `<summary>` containing “结构化记录导入（高级）” and “面向 API 或业务系统的 JSON 记录导入，不是批量上传文档。”
3. Keep `#batch` and `#write-batch` exactly once inside the expanded content, and add the documented 1–1000 record/type/atomic-write guidance.
4. Add a body-level host after `</main>`:

```html
<div id="chunk-preview" class="chunk-preview-overlay" hidden>
  <section class="chunk-preview-drawer" role="dialog" aria-modal="true"
    aria-labelledby="chunk-preview-title" aria-describedby="chunk-preview-description">
    <header class="chunk-preview-header">
      <div><small>CHUNK INSPECTION</small><h2 id="chunk-preview-title">首份切片预览</h2>
      <p id="chunk-preview-description">仅预览切片结果，未调用模型、未写入知识。</p></div>
      <button id="chunk-preview-close" type="button" class="secondary" aria-label="关闭切片预览">×</button>
    </header>
    <div id="chunk-preview-summary" class="chunk-preview-summary"></div>
    <div class="chunk-preview-layout">
      <nav id="chunk-preview-list" class="chunk-preview-list" aria-label="预览片段"></nav>
      <article id="chunk-preview-content" class="chunk-preview-content" tabindex="0"></article>
    </div>
  </section>
</div>
```

5. Change both workbench asset query strings to `document-upload-2`.

- [ ] **Step 2: Add the minimal drawer and advanced-import styles**

Add cohesive rules for the fixed overlay, right drawer, header, summary chips, two-column content, selected chunk button, long-text scrolling, structured import summary/content, `body.chunk-preview-open`, the `850px`/`520px` responsive states, and `prefers-reduced-motion: reduce`.

The desktop drawer should use `width:clamp(620px,58vw,940px)` and the mobile layout should use the full viewport with a horizontally scrolling chunk-button list.

- [ ] **Step 3: Run the static test and confirm the browser test still fails on behavior**

Run: `python -m pytest tests/service/test_workbench.py::test_document_preview_uses_accessible_drawer_and_structured_import_is_advanced -q`

Expected: static test PASS; browser test still FAIL on opening/rendering/closing behavior.

- [ ] **Step 4: Commit the semantic shell and styles**

```bash
git add knowledge_service/web/index.html knowledge_service/web/workbench.css
git commit -m "feat: add knowledge write preview drawer shell"
```

### Task 3: Implement preview rendering and modal behavior

**Files:**
- Modify: `knowledge_service/web/workbench.js:201-221`
- Test: `tests/service/test_workbench.py`
- Test: `tests/service/test_chunk_preview_ui.py`

- [ ] **Step 1: Add focused preview helpers before the existing preview binding**

Implement small functions with the following boundaries:

- `renderChunkPreview(result, title)`: cache normalized chunks, render escaped summary/list/content, and produce the empty state when `chunks` is empty.
- `selectChunkPreview(index)`: update `aria-current`, the selected class, metadata, and escaped正文 for one chunk.
- `openChunkPreview()`: unhide the host, add the body scroll-lock class, set `inert` on `body > aside` and `body > main`, remember the trigger, and focus the close button.
- `closeChunkPreview()`: reverse all open state, clear `inert`, and restore trigger focus.
- `trapChunkPreviewFocus(event)`: on `Tab`, cycle through enabled buttons and `[tabindex="0"]` inside the drawer.

Bind close behavior to the close button, backdrop-only clicks, and `Escape`. Keep the chunk list as native buttons with `aria-current="true"` only on the selected item and `aria-controls="chunk-preview-content"` on every item.

- [ ] **Step 2: Replace the current inline success rendering**

In `bind('preview-chunks', ...)`:

1. Keep the existing file/text endpoint branches unchanged.
2. After the current-project guard, call `renderChunkPreview(result, doc.title)` and `openChunkPreview()`.
3. Preserve the existing success status message.
4. Do not add a second loading-state path: the shared `bind()` wrapper already disables the clicked button and restores it in `finally` for both success and failure.

- [ ] **Step 3: Run the targeted test**

Run: `python -m pytest tests/service/test_workbench.py::test_document_preview_uses_accessible_drawer_and_structured_import_is_advanced -q`

Expected: PASS.

Run: `python -m pytest tests/service/test_chunk_preview_ui.py -q`

Expected: all browser interaction tests PASS, or the file skips for an explicitly missing Playwright/browser dependency.

- [ ] **Step 4: Run the complete workbench contract file**

Run: `python -m pytest tests/service/test_workbench.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit the behavior**

```bash
git add knowledge_service/web/workbench.js tests/service/test_workbench.py tests/service/test_chunk_preview_ui.py
git commit -m "feat: open chunk previews in accessible drawer"
```

### Task 4: Verify regressions and the rendered experience

**Files:**
- Verify: `knowledge_service/web/index.html`
- Verify: `knowledge_service/web/workbench.js`
- Verify: `knowledge_service/web/workbench.css`
- Verify: `tests/service/test_workbench.py`
- Verify: `tests/service/test_chunk_preview_ui.py`

- [ ] **Step 1: Run related frontend contracts**

Run: `python -m pytest tests/service/test_workbench.py tests/service/test_chunk_preview_ui.py tests/test_web_ui_contract.py -q`

Expected: all tests PASS or the legacy contract file is skipped by its existing skip condition.

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff HEAD~3 --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: only pre-existing user files and `.superpowers/brainstorm` artifacts remain uncommitted.

- [ ] **Step 4: Perform browser verification**

Start an isolated manual-verification database from PowerShell:

```powershell
$previewDb = Join-Path $env:TEMP ("knowledge-service-preview-" + [guid]::NewGuid().ToString() + ".sqlite")
$env:KG_DATABASE = $previewDb
python -u -m knowledge_service --port 8100
```

Open `http://127.0.0.1:8100/`, create/select a disposable project, open “知识写入”, and check:

- structured import is collapsed and clearly labelled as an advanced JSON/API path;
- preview opens on the right without changing upload form state;
- chunk switching updates metadata and escaped正文;
- close button, backdrop, `Escape`, focus trap, and focus restoration work;
- background cannot be focused while open;
- layout remains usable at desktop, 850px, and 520px widths;
- reduced-motion mode removes drawer transitions.

- [ ] **Step 5: Commit any verification-driven corrections**

```bash
git add knowledge_service/web/index.html knowledge_service/web/workbench.js knowledge_service/web/workbench.css tests/service/test_workbench.py tests/service/test_chunk_preview_ui.py
git commit -m "fix: polish knowledge write preview drawer"
```
