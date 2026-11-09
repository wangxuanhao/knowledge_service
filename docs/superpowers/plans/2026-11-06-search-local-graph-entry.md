# Search Local Neighborhood Entry Implementation Plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development with one production implementer for the coupled navigation/UI task and independent spec then quality reviews. Root owns all pytest/Playwright/performance runtimes; exactly one pytest process, no performance run alongside pytest. Steps use checkbox tracking.

**Goal:** Search results explicitly open local neighborhoods using the page's existing selected hops, retain a separate bounded global action, verify functional protection and measured performance, and integrate the completed change locally.

**Architecture:** Extend the existing record-navigation/authority-read path with explicit local/global intent while retaining legacy auto behavior for other callers. Reuse the single `#graph-hops`, existing neighborhood and graph-session APIs, epoch/abort guards and renderer/layout owners. Keep source/history/type scope, exact relationship identity, branch ownership, progressive loading and prior ledger changes.

**Tech Stack:** Native JS/CSS, existing ECharts/WebGL, Node26 contracts, existing Playwright/pytest and PostgreSQL. Python only `D:/anaconda/envs/llm_model/python.exe`; no dependencies, build system or backend schema changes.

**Spec:** `docs/superpowers/specs/2026-11-06-search-local-graph-entry-design.md`, approved by user after clarifying a single shared current hop setting. Label visibility remains unchanged.

## Preparation

- [x] Hindsight search/list/capture attempted before implementation; backend unavailable, no memory facts used.
- [x] Managed worktree creation failed to validate the checkout and the retry stalled without creating a worktree. Reuse the now-clean existing checkout on `feat/search-local-neighborhood-20261106`, based on4821fd4; earlier ledger edits are committed in that base. Do not alter unrelated worktrees.
- [x] Raw snapshot of the existing web directory and135 asset hashes saved under ignored `.benchmarks/search-local-neighborhood/`; native baseline **958 passed, 0 failed, 0 skipped**.
- [x] Independent review of this plan against the approved spec: Approved. Added explicit loading/duplicate/truncation, late-default and A→B→A coverage to the implementation checklist.

## Task 1: Explicit navigation and shared-hop UI (one coupled implementation)

**Modify:** `knowledge_service/web/workspace.js`, `full-graph-view.js`, `graph-overview.js`, `index.html`; `graph-workspace-layout.js`/`.css` only if needed for existing fixed regions. Related existing Node fixtures/contracts may be adapted only where UI behavior actually changes.

**Create:** `tests/service/test_graph_search_navigation_browser.py`. Add focused native contracts in existing navigation/picker files if there is a real behavioral seam; do not add source-text mirror tests.

- [x] Write browser regressions first: two distinct result actions; preselected2/3 (and advanced0/10) hops reach local requests; old rendered result buttons use newly changed hop value; no reset across targets/modes; one hop input; no request on hop edit alone; in-flight stale hop result rejected; exact relation plus two endpoints; local action works with cached full target and never creates a full session; global action reuses a matching view or performs bounded loading with unloaded-target guidance. Cover owner readiness, authority scope, history readonly, endpoint/type exclusion, stale versions and failure preserving the previous scene.
- [x] Root runs representative new browser cases before production edits and records the intended RED result: **2 intended failures**, old action label and relation remaining in full mode (`navigation-red.xml`). Native RED separately reproduced3 local-navigation and2 explicit-overview gaps. Implementer may run Node tests, but no pytest/Playwright runtime without root coordination.
- [x] Add explicit intent to the existing navigation API, e.g. `openKnowledgeRecord({...record, view:'local', hops:readGraphHops()})` and `view:'full'`; existing callers without intent retain auto semantics. Resolve/qualify the exact record under its original scope before painting. Validate hops as an integer0–10; never silently fallback to1 or shrink the user's choice.
- [x] Local intent reads the requested neighborhood even when the full cache contains the target. Entity uses its ID; relation uses both qualified endpoint IDs, retains its exact edge/revision and camera fit. Use configured direction/budgets; do not use the old0-hop `maxNodes:1/2` limits for genuine expansion. Failed/excluded/stale reads leave the old valid graph intact.
- [x] Use the existing single `#graph-hops` at click time. Derive result button text `展开N跳邻域` from it, with a separate `全局查看` button. Keep the input and expand action reachable in the local toolbar. An input/change edits labels and invalidates only relevant pending local work; it must not start a query, reset the current graph, invalidate an unrelated complete full scene, or relabel the displayed graph as newly expanded. Canvas inspection stays separate from explicit expansion; expansion uses the visibly selected entity, not an old hidden picker value.
- [x] Full intent may reuse only the correct scope/version cache. Otherwise create the same-scope bounded overview (batch200, large manifest one page, certified small max3 pages), preserve exact history/filters and show the actual view scope. An unloaded target has honest guidance and an explicit local action; no automatic load-all, no arbitrary additions into a frozen full-session dataset, no silent local fallback. Returning to local retains its valid graph/branches/camera.
- [x] Keep other navigation callers, branch expand/collapse, result-scroll retention, edit permissions, layout height/fixed pane, source actions, label budgets, legacy settings and progressive controls intact. Bump every modified asset URL; preserve the ledger cache changes in the base.
- [x] Run targeted new tests and relevant native contracts; self-review; freeze production files for root verification. Do not commit until root approves the reviewed file set.
- [x] Independent spec review, fix substantive findings, then independent code-quality review and fix findings. Root rechecks affected cases before accepting the task.

## Task 2: Independent performance evidence (no product changes)

**Create:** `tests/service/test_graph_search_navigation_performance.py` for bounded real-PG measurements and ignored `.benchmarks/search-local-neighborhood/measure_navigation.py` for paired frontend evidence. A separate implementer may prepare these while Task1 runs; root exclusively executes runtimes after production freeze.

- [x] Prepare deterministic synthetic/test-project data only, using the existing test DB isolation. No normal business project writes or normal-service publication/review/fusion actions. Use `Repository.put_batch` and existing scoped neighborhood operations; no external model/index dependency.
- [x] Measure real-PG 1/2/3/10-hop requests on a controlled branching/high-degree graph under current node/edge budgets. Record medians, returned entity/relation counts, truncation and bounded frontier/limit behavior; verify no full-corpus graph/export path. Timing is evidence, not a fragile exact-ms CI assertion. If unexpected cost is found, diagnose before optimizing backend behavior.
- [x] Prepare a frontend harness against both the saved baseline assets and frozen candidate. Use identical scene/data/viewport/browser flags, selected hops, direction and budgets. Baseline manual local draw and candidate explicit search-neighborhood draw must produce the same graph. Report neighborhood-read/render stage and complete action time separately; cached global focus is a different operation and must not be used to claim the new data read is faster.
- [x] Check workload: search/hop edit/pointer interactions do not prefetch neighborhoods; one explicit local action issues only its necessary qualification/neighborhood reads and zero graph-session reads; global cached focus does not reload; uncached large global remains bounded. Record request counts and payload sizes plus repeatable timing samples. Keep limitations (synthetic transport vs PG vs actual renderer, CPU vs end-to-end) explicit.
- [x] Root runs measurements sequentially with browser tests; reviews meaningful regressions and fixes actual issues without reducing requested hops, dropping filters/records or disabling functionality.

## Task 3: Verification and integration

- [x] Root runs full native `node --test tests/service/*.test.cjs`; counts from actual output. Serialize new browser module plus existing overview/layout, relevant graph/search/entity/scope portions of retrieval flow, unified scope/path, version/temporal and click-target checks. Run existing neighborhood-hop/session backend guards and the real-PG performance module in the sole pytest process; do not run an unrelated whole backend suite.
- [x] Adapt only obsolete UI expectations; retain exact IDs, history/scope, stale-response, no-unrequested-read and no-rebuild guards. Recheck modified shared fixture behavior, not only failures.
- [x] Inspect desktop/narrow/fullscreen screenshots and the actual service assets/health. Verify candidate source hashes, preservation of ledger changes and previous hit-target/label work, cache URLs, no dependencies/prototype data/secrets in the commit, and `git diff --check`.
- [ ] Final integrated review after functional and performance evidence. Update spec/plan with truthful measured results, commit only this feature/docs/tests, then integrate to local master within the user's standing local integration authorization. No remote push or unrelated worktree cleanup. Handle actual concurrent branch movement without discarding work; verify the resulting source tree and meaningful post-integration checks.
- [ ] Report concise Chinese result, actual commit/service URL, measurements with context, and any material limits. Do not claim zero functional risk or production-wide FPS from a synthetic benchmark.

## 验收记录（2026-11-09）

最终合并前 Node 全量契约 **964 passed、0 failed、0 skipped**。新增导航的29项浏览器回归已通过，涵盖0/2/3/10跳、实体/精确关系、同范围全局概览、历史只读、失败回滚、迟到响应、项目往返及桌面/窄屏操作。

原有兼容组97项初跑93项通过、4项失败。其中键盘激活结果时，按钮禁用导致焦点提前丢失，已先复现失败再修复并验证通过。另三项按已批准的行为修订，关系详情清除隐藏的旧实体起点，检索主入口显式读取所选跳数的邻域。2026-11-09补跑这四项及相邻的关闭侧栏/失败恢复保护，共 **6 passed、0 failed、0 skipped**，保留精确版本、无额外读取和异步响应保护。

独立规格、代码质量及最终集成审查均 Approved。已查看8张桌面、窄屏、全屏和历史范围截图。2026-11-09服务健康检查200，7个线上静态资源与当前磁盘哈希一致，包含此前台账资源。修改限定为6个现有前端文件及相关测试/设计计划。

### PostgreSQL 邻域读取

真实隔离测试库使用1000实体、2072关系，默认预算500实体/1000关系。每项预热1次、串行采样5次，包含实际API、服务、PG读取和JSON响应。

| 请求跳数 | 中位耗时 ms | P95 ms | 返回实体/关系 | 达到上限 |
| --- | --- | --- | --- | --- |
| 1 | 99.9 | 106.3 | 101 / 100 | 否 |
| 2 | 370.3 | 495.6 | 500 / 694 | 是 |
| 3 | 566.9 | 589.3 | 500 / 694 | 是 |
| 10 | 684.0 | 713.8 | 500 / 694 | 是 |

独立20节点链验证10跳实际遍历十轮，返回11实体/10关系。高连接图在预算用尽时提前停止并明确截断。测试同时禁止全库query/export和全局session路径，校验每轮frontier与LIMIT上限。证据在`.benchmarks/search-local-neighborhood/postgres.json`，可用新增性能pytest模块复跑。

### 前端同图对照

同一数据、视口和软件渲染Edge下，旧版手动绘制局部图与新版检索展开的节点/关系哈希完全一致。2026-11-09相邻复测各预热1次、采样5次，单位ms。

| 跳数 | 旧版读取/绘制中位 | 新版读取/绘制中位 | 新版完整检索展开中位 |
| --- | --- | --- | --- |
| 1 | 219.3 | 315.5 | 441.6 |
| 2 | 491.3 | 596.2 | 727.1 |
| 3 | 487.2 | 577.9 | 693.9 |
| 10 | 809.8 | 545.5 | 660.5 |

前端样本存在显著调度/绘制波动，旧版3跳最大2444ms，新版2跳最大2585ms。11月6日最终版对应绘制中位为244.7/548.2/611.3/584.8ms。当前证据能确认图谱内容、预算与请求量符合设计，前端整体提速仍需在稳定负载下评估。新版完整动作还包含精确记录资格检查和详情选中工作。

检索只发检索请求，修改跳数不读取邻域，普通选择只读取详情。一次局部入口包含一次精确资格读取、一次邻域读取及原有时间线/候选/详情辅助读取，全局session读取为0。缓存全局定位的session读取为0，大图未缓存入口只创建一个session并读取一页200条。原始证据在`frontend-before-recheck.json`、`frontend-after-recheck.json`及此前各轮JSON，均位于同一忽略的基准目录。

上述PG测试使用本机合成项目，前端测试使用合成传输及实际渲染器。实际业务库规模、网络、模型与索引耗时需按各自链路评估。当前生产资源与冻结候选的哈希一致，最终提交及本地master集成待执行。
