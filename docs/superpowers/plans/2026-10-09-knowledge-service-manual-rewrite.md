# Knowledge Service Operation Manual Rewrite Implementation Plan

> **For the primary agent:** Implement this plan in the current session without delegating implementation. Execute tasks in order and use the checkbox (`- [ ]`) steps for tracking.

**Goal:** Replace the existing Chinese HTML operation manual with an offline, screenshot-led, end-to-end guide that accurately explains ontology modeling, attributes, mind-map/ledger effects, controlled migration, and the three independent version dimensions.

**Architecture:** Keep the deliverable as one self-contained HTML file. Add a small deterministic builder that owns the manual structure, inline CSS, inline SVG explanatory figures, and base64 screenshot embedding; keep captured PNG originals under `docs/assets/manual-v110/` so annotations can be reviewed separately from the final HTML. Protect the content with focused document-contract tests and verify the rendered artifact in a real Chromium browser.

**Tech Stack:** Python 3 standard library, semantic HTML/CSS, inline SVG, PNG screenshots, pytest, Chromium browser automation.

---

## File map

- Create `scripts/build_knowledge_service_manual.py`: deterministic source of the manual HTML; validates required screenshots and embeds them as data URLs.
- Create `scripts/capture_knowledge_service_manual.py`: starts an isolated demo app/database, seeds only synthetic walkthrough data, drives the actual UI with Playwright, and captures the exact screenshot map.
- Create `docs/assets/manual-v110/*.png`: unannotated screenshots captured from the actual service v1.1.0 UI.
- Create `docs/assets/manual-v110/manual_employee_v2.txt`: UTF-8 capture fixture; its contents are also embedded in the HTML as copyable text and a `data:text/plain` download.
- Modify `docs/知识图谱操作手册.html`: generated offline manual consumed by users.
- Modify `tests/service/test_docs_contract.py`: content, terminology, workflow, version, attribute, and image/annotation contracts.
- Modify `docs/superpowers/plans/2026-10-09-knowledge-service-manual-rewrite.md`: check off completed steps during execution.

## Safety baseline

- [ ] Before editing, save `git status --porcelain=v1` as the execution baseline and inspect whether any target file already has user changes.
- [ ] Before every staging or commit action, compare current status with that baseline. This plan does not commit by default; commits are optional only when the user explicitly asks.
- [ ] Do not edit, stage, restore, move, or delete any baseline path unrelated to this manual. If a target file gains overlapping external changes during execution, stop and reconcile with the user instead of overwriting it.

### Task 1: Lock the manual content contract

**Files:**
- Modify: `tests/service/test_docs_contract.py`
- Test: `tests/service/test_docs_contract.py`

- [ ] **Step 1: Add failing assertions for the required lifecycle**

Require the generated manual to contain all of the following exact concepts:

```python
required = [
    '本体建模层',
    '属性不会单独画成节点',
    '实体脑图当前不展示属性分支',
    '知识台账可以切换到“属性”视角',
    '发布本体不会自动修改已有知识',
    '服务 v1.1.0',
    '本体 v1',
    '本体 v2',
    '本体 v3',
    '记录修订 rN',
    '受控重分类不会自动把“员工”改成“产品经理”',
]
```

Also assert that the obsolete UI name `本体建设台` is absent. Parse the HTML rather than searching raw strings so the manual may still display `http://127.0.0.1:8100` as instructional text while forbidding external `<img src>`, `<script src>`, stylesheet, font, and CSS `url(...)` dependencies.

Define the exact screenshot ID tuple `S01a` through `S10c` from the approved spec and require each ID exactly once. For every ID, assert one `<figure>`, one embedded PNG `<img>` with non-empty `alt`, one completion-sign element, and one to three numbered markers. Decode each embedded source and assert PNG signature plus 1440×900 dimensions. Validate every percentage box with `0 <= x,y,w,h <= 100`, `x+w <= 100`, and `y+h <= 100`. Add a builder test that copies the screenshot manifest to `tmp_path`, points one entry at a missing temporary path, and confirms generation fails with that screenshot ID and path; never move, rename, or delete a real file under `docs/assets/manual-v110/`.

For every core chapter, assert one `.completion-sign` and one `.common-misunderstanding`. For every troubleshooting item, assert separate “看到什么”“原因”“下一步” fields so prose cannot silently omit the recovery action.

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
D:\Anaconda3\envs\model_agent\python.exe -m pytest tests/service/test_docs_contract.py -q
```

Expected: FAIL because the current manual lacks the new version/attribute/migration contract or still contains the obsolete name.

- [ ] **Step 3: Recheck working-tree scope**

Compare `git status --porcelain=v1` with the saved baseline. Keep the known failing test and continue without committing.

### Task 2: Build the new manual structure and wording

**Files:**
- Create: `scripts/build_knowledge_service_manual.py`
- Modify: `docs/知识图谱操作手册.html`
- Test: `tests/service/test_docs_contract.py`

- [ ] **Step 1: Create the deterministic builder**

Implement helpers with one responsibility each:

```python
def data_url(path: Path) -> str: ...
def screenshot(figure_id: str, path: Path, alt: str, markers: list[dict]) -> str: ...
def step(number: int, title: str, action: str, expected: str, image_html: str = '') -> str: ...
def render_manual() -> str: ...
```

The screenshot helper must place numbered markers in an absolutely positioned overlay outside control text; marker descriptions live below the image in semantic ordered lists. The builder must fail with a clear message when an expected screenshot is missing.

- [ ] **Step 2: Implement the chapter spine**

Generate these user-facing chapters:

1. 阅读说明与完整流程图。
2. 启动、登录、角色和项目创建。
3. 默认本体 v1 与三种版本的区别。
4. 在“本体建模层”创建员工、部门、属性、关系并发布本体 v2。
5. 使用固定材料写入张三、产品部、关系和属性，处理知识审核。
6. 分别核对实体脑图、实体台账、关系台账、属性台账。
7. 新增产品经理并发布本体 v3，展示发布后知识不自动变化。
8. 受控重分类只迁移本体归属；再单独编辑张三类型。
9. 编辑属性、软删除关系、撤销和修订历史。
10. 开放提取、确认性提取和实体类型关闭附录。
11. 常见失败状态与处理办法。

Every core chapter is generated through a chapter helper that requires both a “完成标志” block and a “常见误解” block. Every troubleshooting item is generated through a helper requiring non-empty `symptom`, `cause`, and `next_step` values rendered as “看到什么 / 原因 / 下一步”.

- [ ] **Step 3: Add the version and page-effect comparisons**

Include an explicit matrix:

```text
服务 v1.1.0：软件发布版本
本体 vN：项目结构发布版本
记录修订 rN：单条实体/关系/属性/片段自己的修订
```

Include before/after tables for “publish v3” and “migrate/edit Zhang San”, making clear that ontology canvas, mind map, and ledger react differently.

- [ ] **Step 4: Add attribute behavior callouts**

State in both the ontology and verification chapters:

- ontology canvas: attributes are definitions attached to class details, not standalone nodes;
- entity mind map: attributes are excluded in v1.1.0;
- knowledge ledger: attributes are first-class records in the attribute view with their own revisions.

- [ ] **Step 5: Create the fixed walkthrough fixture**

Create `docs/assets/manual-v110/manual_employee_v2.txt` as UTF-8 with the exact approved two-sentence sample. In the HTML, display it in a copyable `<pre>` and add a self-contained `data:text/plain;charset=utf-8,...` download link so readers do not depend on the repository asset.

- [ ] **Step 6: Generate the preliminary HTML**

Run:

```powershell
D:\Anaconda3\envs\model_agent\python.exe scripts/build_knowledge_service_manual.py
```

Expected: either a complete HTML artifact or a precise list of screenshot files still missing.

- [ ] **Step 7: Run the focused contract**

Run the Task 1 pytest command. Expected: wording assertions pass; image assertions may remain pending only until Task 3.

- [ ] **Step 8: Recheck working-tree scope**

Confirm that only the planned manual files changed in addition to the saved user-owned baseline. Do not stage or commit automatically.

### Task 3: Capture and annotate actual service screens

**Files:**
- Create: `scripts/capture_knowledge_service_manual.py`
- Create: `docs/assets/manual-v110/*.png`
- Modify: `scripts/build_knowledge_service_manual.py`
- Modify: `docs/知识图谱操作手册.html`

- [ ] **Step 1: Implement an isolated capture runner**

Create `scripts/capture_knowledge_service_manual.py` using the repository's PostgreSQL admin DSN loader, migration runner, app factory, and Playwright from `D:\Anaconda3\envs\model_agent`. Reuse only the already running local PostgreSQL cluster, but create a unique database named `knowledge_manual_<12 hex chars>` via an admin connection to `postgres`; reject any generated name that does not match `^knowledge_manual_[0-9a-f]{12}$`. Apply the repository migrations to that exact DSN and inject only that DSN into the capture app. Start the demo server on `127.0.0.1` with an unused local port and use a dedicated temporary Chromium profile. Never connect the app to the default service database, current browser profile, or port 8100.

The script must set `NO_PROXY=127.0.0.1,localhost`, create only synthetic accounts/projects in the temporary database, and terminate the server/browser in `finally`. After startup call its own `/api/health`; require `status == 'ok'` and version `1.1.0` before any capture. In `finally`, first stop the app and close all connections, then reconnect to `postgres`, revalidate the exact database-name regex, and execute `DROP DATABASE <exact-name> WITH (FORCE)`. It must not install packages, create a virtual environment, start Docker, connect to any non-generated database, or call the shared test-suite teardown.

- [ ] **Step 2: Define the executable screenshot manifest and capture primitive**

Add an explicit map from every ID to one filename, stable locator, and expected UI text, for example:

```python
SHOTS = {
    'S01a': Shot('S01a-users-form.png', '#tab-users', '新建账号'),
    'S02a': Shot('S02a-create-project.png', '.project-modal', '创建项目'),
    # ... every approved ID through S10c exactly once
}
```

For each checkpoint call a helper that waits for the expected locator and text, waits for `document.fonts.ready`, waits for two stable animation frames, then saves `page.screenshot(path=..., full_page=False, animations='disabled')`. Create the context with viewport 1440×900, device scale factor 1, locale `zh-CN`, and a fresh profile. The script prints each captured ID and fails if any manifest entry was not written.

Use actual frontend pages and supported APIs in the isolated app. Where the external LLM result would be nondeterministic, use a deterministic capture-only extraction adapter that returns the approved two entities, one relation, and two attributes through the normal ingest/review workflow; do not modify frontend HTML or draw a fake interface. The manual must label these figures “隔离演示环境截图”，state that real LLM wording and counts can vary, and use the screenshots only to teach where to click and how to verify results—not as proof that production extraction always returns the same facts.

- [ ] **Step 3: Capture the workflow interleaved with mutations**

Run:

```powershell
$env:NO_PROXY='127.0.0.1,localhost'
D:\Anaconda3\envs\model_agent\python.exe scripts/capture_knowledge_service_manual.py --output docs/assets/manual-v110
```

Expected: one `captured Sxx -> ...png` line for every manifest entry, followed by `captured 32/32`; any missing locator, wrong UI text, health/version mismatch, or incomplete output is a hard failure.

Use Chromium at 1440×900 CSS pixels, 100% zoom, DPR 1. At each checkpoint below, save the PNG before performing the next mutation:

1. Verify/capture S01a, perform account action, capture S01b, then log in and capture S01c.
2. Capture S02a before project creation, capture S02b immediately after creation, then capture default v1 as S02c.
3. Add classes and capture S03a; add attributes and capture S03b; add the relation and capture S03c; run validation and capture S03d; publish and capture S03e.
4. Select `manual_employee_v2.txt` and capture S04a; process it and capture S04b; accept pending records and capture S04c.
5. Before continuing, verify the ledger contains exactly the required demonstration facts: two target entities, one target relation, and two target attributes. If not, stop the run, discard the temporary database/profile, adjust the deterministic seed or fixed fixture, and restart the complete capture in a new temporary environment. Do not mix screenshots from different runs.
6. Capture S05a, S05b, S05c, and S05d by switching read-only views without mutation.
7. Add product manager and capture S06a; publish v3 and capture S06b.
8. Before migration, capture S07a and S07b.
9. Open the migration plan and capture S08a; select groups and preview, then capture S08b; execute and capture S08c; inspect Zhang San and capture S08d.
10. Edit Zhang San and capture S09a; open history and capture S09b; open mind map and capture S09c.
11. Edit the employee number and capture S10a; soft-delete the relation and capture S10b; undo and capture S10c.

Every PNG must preserve the relevant project/version context and contain no unrelated real business data.

- [ ] **Step 4: Define marker geometry in the builder**

For every screenshot, add at most three marker dictionaries:

```python
{'n': 1, 'x': 12.5, 'y': 18.0, 'w': 24.0, 'h': 9.0, 'label': '选择本体建模层'}
```

Percent-based boxes must include at least six visible pixels of padding and must not cover labels, values, buttons, or result counts. Keep Chinese sentences in the caption list, not burned into the bitmap.

- [ ] **Step 5: Regenerate the final HTML**

Run the builder. Expected: all PNG files are embedded as local data URLs and the HTML has no external asset dependency.

- [ ] **Step 6: Render every final figure for overlay acceptance**

Open the generated manual in Chromium and render each `figure[data-shot-id]` to a temporary PNG. Compare each rendered figure with its unannotated source at the same viewport. Verify the red target outline has visible padding, markers do not cover labels/values/buttons/result counts, and the Chinese explanation remains below the image. Correct geometry and regenerate until all figures pass.

- [ ] **Step 7: Prove isolation cleanup**

After successful capture, let the runner close the dedicated browser context/server and drop only the regex-validated generated PostgreSQL database plus its temporary Chromium profile in `finally`. Confirm the default port 8100 process, default database, current browser profile, shared test database, and the complete Git baseline were never used or modified.

- [ ] **Step 8: Recheck working-tree scope**

Compare current `git status --porcelain=v1` with the saved baseline. Do not stage or commit automatically.

### Task 4: Validate the generated artifact

**Files:**
- Verify: `docs/知识图谱操作手册.html`
- Verify: `docs/assets/manual-v110/*.png`
- Test: `tests/service/test_docs_contract.py`

- [ ] **Step 1: Run structural HTML checks**

Use a short read-only validation command to parse the HTML, count headings/figures/images, decode every image data URL, and confirm no duplicate anchors or missing alt text.

- [ ] **Step 2: Run the focused document tests**

```powershell
D:\Anaconda3\envs\model_agent\python.exe -m pytest tests/service/test_docs_contract.py -q
```

Expected: PASS.

- [ ] **Step 3: Open the manual in Chromium and inspect desktop layout**

Check the cover, sticky table of contents, full workflow strip, all before/after tables, screenshot numbering, captions, and error-state appendix. Confirm Chinese text renders normally and no marker covers the underlying UI.

- [ ] **Step 4: Inspect print layout**

Use print preview or CSS emulation. Confirm figures do not split from captions and tables remain readable.

- [ ] **Step 5: Check git scope**

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; every path from the saved baseline remains untouched unless it is an explicitly planned target with reconciled changes. In particular, the known `.gitattributes` and `docker/postgres/initdb/10-app-role.sh` changes remain untouched and unstaged.

- [ ] **Step 6: Preserve the verified working tree**

Leave the verified manual changes unstaged for user review. Commit only if the user explicitly asks for a commit later.
