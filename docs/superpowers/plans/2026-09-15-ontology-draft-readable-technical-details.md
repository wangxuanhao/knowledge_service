# Ontology Draft Readable Technical Details Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ontology draft history readable in Chinese by default while preserving exact raw diff, Turtle, mapping, and IRI values in clearly explained technical disclosures.

**Architecture:** Add a small dependency-free `ontology-details.js` module that owns IRI presentation, Chinese summary rendering, technical-section markup, and clipboard behavior. `workspace.js` will delegate draft technical-detail rendering and event binding to this module; the repository/API payload and ontology generation remain unchanged.

**Tech Stack:** Browser JavaScript, CommonJS-compatible exports for Node's built-in test runner, HTML/CSS, pytest source-contract tests.

---

## File structure

- Create `knowledge_service/web/ontology-details.js`: pure IRI/summary/render helpers plus clipboard event binding; exports helpers through `module.exports` in Node and `window.OntologyDetails` in browsers.
- Create `tests/service/ontology-details.test.cjs`: executable behavior tests using Node's built-in `node:test`; lightweight fake DOM/event objects avoid adding a package manager or DOM dependency.
- Modify `knowledge_service/web/workspace.js`: replace inline raw `<pre>` block with the module renderer and bind copy controls after render.
- Modify `knowledge_service/web/workspace.css`: style the three readable regions, secondary disclosures, mappings, warnings, focus states, and live status.
- Modify `knowledge_service/web/index.html`: load the new module before `workspace.js` and bump the workspace asset cache key.
- Modify `tests/service/test_workbench.py`: update static asset and Chinese UI contract assertions.

### Task 1: IRI presentation helper

**Files:**
- Create: `knowledge_service/web/ontology-details.js`
- Create: `tests/service/ontology-details.test.cjs`

- [ ] **Step 1: Write failing helper tests**

Test `shortIri(iri)` with representative values:

```javascript
assert.deepEqual(shortIri('urn:test:%E4%BF%9D%E6%8A%A4%E5%AF%B9%E8%B1%A1'), {
  label: '保护对象', malformed: false
});
assert.equal(shortIri('https://example.test/ns#Merchant').label, 'Merchant');
assert.equal(shortIri('https://example.test/ns/Order').label, 'Order');
assert.equal(shortIri('urn:test:a%2Fb').label, 'a/b');
assert.deepEqual(shortIri('urn:test:%E8%B1%A'), {
  label: '%E8%B1%A', malformed: true
});
assert.equal(shortIri('urn:test:').label, 'urn:test:');
assert.equal(shortIri('https://example.test/ns/').label, 'https://example.test/ns/');
assert.equal(shortIri('https://example.test/ns#').label, 'https://example.test/ns#');
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test tests/service/ontology-details.test.cjs`

Expected: FAIL because `knowledge_service/web/ontology-details.js` does not exist.

- [ ] **Step 3: Implement the minimal pure helper**

Implement `shortIri` by locating the final `#`, `/`, or `:` delimiter and inspecting the substring after it. If that terminal substring is empty, return the complete original IRI as the label; otherwise call `decodeURIComponent` exactly once on that substring. Preserve the input separately as `raw`; on decode failure return the original terminal substring with `malformed: true`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `node --test tests/service/ontology-details.test.cjs`

Expected: PASS.

- [ ] **Step 5: Record checkpoint**

This workspace has no Git repository, so note completion in the plan instead of committing.

### Task 2: Chinese-readable detail renderer

**Files:**
- Modify: `knowledge_service/web/ontology-details.js`
- Modify: `tests/service/ontology-details.test.cjs`

- [ ] **Step 1: Write failing renderer tests**

Create a representative draft fixture containing:

- added/retained/removed diff entries;
- classes with zero and multiple parents;
- relations with multiple domain/range values;
- an attribute with a datatype range;
- a mapping with encoded Chinese, duplicate short labels, and malformed encoding;
- exact raw Turtle text.

Assert `renderTechnicalDetails(draft, esc)` returns markup containing:

```text
查看版本变化与技术详情
版本差异
新增、保留或删除
本体定义
名称与技术标识
保护对象
编码格式不完整
查看原始数据
查看 Turtle 原始源码
查看原始映射数据
```

Also assert the regions have unique `aria-describedby` targets, the raw disclosures are closed by default, missing fields render as `未指定`, resolved type references use Chinese labels, and raw values remain escaped but otherwise unchanged.

- [ ] **Step 2: Run tests and verify RED**

Run: `node --test tests/service/ontology-details.test.cjs`

Expected: FAIL because the renderer is not implemented.

- [ ] **Step 3: Implement summary indexing and rendering**

Add focused pure helpers:

```javascript
function ontologyName(item) {
  return item?.label_zh || item?.label || item?.name || '未命名术语';
}

function buildTermIndex(summary) {
  // Map every term id and name to ontologyName(term).
}

function displayRefs(values, index) {
  // Return 未指定 for empty values; otherwise Chinese labels or shortIri fallback.
}
```

Render three labeled `<section>` regions. Show the diff as Chinese grouped chips/lists using only `added`, `retained`, and `removed`. Show class/relationship/attribute fields according to the approved spec. Show mapping rows as Chinese source name, an arrow, and decoded short technical identifier; duplicate identifiers remain separate rows with full raw IRI in their detail.

- [ ] **Step 4: Add exact raw disclosures**

Serialize raw diff and mappings with `JSON.stringify(value || {}, null, 2)` and render `draft.turtle || ''` verbatim after HTML escaping. Add native copy buttons with `data-copy-kind` and unique accessible labels.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `node --test tests/service/ontology-details.test.cjs`

Expected: PASS.

### Task 3: Clipboard behavior and accessible feedback

**Files:**
- Modify: `knowledge_service/web/ontology-details.js`
- Modify: `tests/service/ontology-details.test.cjs`

- [ ] **Step 1: Write failing clipboard tests**

Test `copyText(value, clipboard)` and/or `bindCopyButtons(host, draft, clipboard, timers)` with fakes. Assert:

- Turtle copies exactly `draft.turtle`;
- diff and mappings copy the exact pretty-printed JSON;
- one mapping row copies the unchanged full IRI;
- success sets `已复制`, updates a polite live region, and schedules restoration after 2000 ms;
- missing/rejected Clipboard API produces `复制失败，请手动选择内容` without throwing or changing source content.

- [ ] **Step 2: Run tests and verify RED**

Run: `node --test tests/service/ontology-details.test.cjs`

Expected: FAIL because clipboard binding is absent.

- [ ] **Step 3: Implement minimal clipboard binding**

Use native button click handlers. Resolve copy values from an in-memory per-render map rather than HTML attributes so large Turtle text and special characters are never round-tripped through markup. Use one `role="status" aria-live="polite"` node per draft card.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `node --test tests/service/ontology-details.test.cjs`

Expected: PASS.

### Task 4: Integrate into the ontology discovery page

**Files:**
- Modify: `knowledge_service/web/workspace.js:531-549`
- Modify: `knowledge_service/web/index.html:41`
- Modify: `tests/service/test_workbench.py:5-34`

- [ ] **Step 1: Write failing integration contract assertions**

Update `test_workbench_has_all_control_targets_and_no_native_dialogs` to require:

- `ontology-details.js` loaded before `workspace.js`;
- `OntologyDetails.renderTechnicalDetails` and `OntologyDetails.bindCopyButtons` used by `workspace.js`;
- `/assets/workspace.js?v=ontology-details-1` present and the old `discovery-lifecycle-3` cache key absent;
- the obsolete title `版本差异 · Turtle 与 IRI 映射` absent;
- the new title and three descriptions present in the new module.

- [ ] **Step 2: Run the contract test and verify RED**

Run: `D:\workspace\知识图谱\.venv-service\Scripts\python.exe -m pytest tests/service/test_workbench.py::test_workbench_has_all_control_targets_and_no_native_dialogs -q`

Expected: FAIL on missing asset/module integration.

- [ ] **Step 3: Load and call the module**

Add `<script src="/assets/ontology-details.js?v=1"></script>` immediately before `<script src="/assets/workspace.js?v=ontology-details-1"></script>`. Remove the local `schema(d)` renderer and its `${schema(d)}` insertion so the readable “本体定义” appears exactly once inside `OntologyDetails.renderTechnicalDetails(d, esc)`. Replace the old inline `<details>` construction with that module call. After assigning `host.innerHTML`, call `OntologyDetails.bindCopyButtons` for every rendered draft article.

- [ ] **Step 4: Run focused JS and Python tests**

Run:

```powershell
node --test tests/service/ontology-details.test.cjs
& 'D:\workspace\知识图谱\.venv-service\Scripts\python.exe' -m pytest tests/service/test_workbench.py -q
```

Expected: all tests PASS.

### Task 5: Visual hierarchy and responsive styling

**Files:**
- Modify: `knowledge_service/web/workspace.css`
- Modify: `tests/service/test_workbench.py`

- [ ] **Step 1: Add failing CSS contract assertions**

Require selectors for `.ontology-technical-details`, `.ontology-detail-region`, `.ontology-readable-grid`, `.ontology-mapping-row`, `.ontology-encoding-warning`, `.ontology-copy-status`, and a visible `:focus-visible` rule for copy buttons.

- [ ] **Step 2: Run contract test and verify RED**

Run: `D:\workspace\知识图谱\.venv-service\Scripts\python.exe -m pytest tests/service/test_workbench.py -q`

Expected: FAIL on missing selectors.

- [ ] **Step 3: Implement restrained, consistent styles**

Follow the existing green/neutral workbench palette. Use compact section headers, readable definition lists/cards, monospaced raw panes, warning color only for malformed identifiers, responsive single-column collapse, and strong keyboard focus. Do not introduce new fonts or unrelated page restyling.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
node --test tests/service/ontology-details.test.cjs
& 'D:\workspace\知识图谱\.venv-service\Scripts\python.exe' -m pytest tests/service/test_workbench.py -q
```

Expected: all tests PASS.

### Task 6: Full regression and live verification

**Files:**
- No new files expected.

- [ ] **Step 1: Run the ontology discovery regression suite**

Run:

```powershell
& 'D:\workspace\知识图谱\.venv-service\Scripts\python.exe' -m pytest tests/service/test_ontology_discovery.py tests/service/test_workspace.py tests/service/test_workbench.py -q
node --test tests/service/*.test.cjs
```

Expected: all tests PASS with no warnings attributable to this change.

- [ ] **Step 2: Verify the real project payload through the UI**

Start the service in a dedicated terminal from the repository root:

```powershell
& '.\scripts\start-service.ps1' -Port 8100 -Demo
```

Confirm `GET http://127.0.0.1:8100/api/health` returns 200, then open `http://127.0.0.1:8100/`, select `test_0915_开放`, enter “本体发现,” and expand “查看版本变化与技术详情.” Confirm the default content says “保护对象” rather than leading with `%E4%...`; confirm all three explanations are visible and raw data remains available only inside nested disclosures. If port 8100 is already occupied, verify the existing process health before reusing it instead of starting a duplicate.

- [ ] **Step 3: Verify copy and malformed fallback manually**

Save the response from `GET http://127.0.0.1:8100/api/projects/ebfea8cc-5970-4478-8af8-128d64ddf338/ontology-discovery`. Copy Turtle, formatted diff/mappings JSON, and one IRI in the page; compare each copied string exactly with `drafts[0].turtle`, `JSON.stringify(drafts[0].diff || {}, null, 2)`, `JSON.stringify(drafts[0].mappings || {}, null, 2)`, and the selected raw mapping value. Then exercise the malformed fixture in the executable Node test. Confirm success/failure announcements and keyboard focus behavior.

- [ ] **Step 4: Record final verification**

Report the exact commands and results. Since this workspace has no Git repository, do not claim or attempt a commit.

- [ ] **Step 5: Run the complete Python service suite**

Run:

```powershell
& 'D:\workspace\知识图谱\.venv-service\Scripts\python.exe' -m pytest tests/service -q
```

Expected: all Python service tests PASS. This supplements the focused Node and ontology-discovery checks with the full configured Python suite.
