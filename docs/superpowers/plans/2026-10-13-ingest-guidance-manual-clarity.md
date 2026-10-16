# 知识写入结果引导与操作手册清晰化实施计划

> **执行说明：** 按任务逐项实施；每项先补会失败的契约测试，再做最小改动使其通过。使用 `D:/anaconda/envs/llm_model/python.exe`，不要加载 `.env`。前端脚本变化同步更新 `index.html` 缓存版本。

**目标：** 在不增加上传状态机、不改变本体建模台直接编辑方式的前提下，让知识写入结果明确说明“发生了什么、下一步去哪里”，并把问答术语和操作手册统一到当前真实能力。

**范围边界：** 不实现 OWL 2 RL 或其他 OWL 推理；不改变后端 `reasoning_paths` 字段；不处理非流式 JSON 问答兼容；不复制审核、重试、台账加载逻辑。

**设计依据：** `docs/superpowers/specs/2026-10-13-ingest-guidance-manual-clarity-design.md`

**Architecture：** 保持当前无框架前端的全局模块结构。`app.js` 提供通用页签激活能力，`knowledge-navigation.js` 负责“校验/切换项目 → 打开目标视图”，各业务模块只公开必要的薄入口。上传结果模块只计算该任务可用的下一步动作，不持有目标页面的加载实现。

**Tech Stack：** 原生 JavaScript、DOM API、Node `node:test`、Python/pytest、静态 HTML/CSS、服务端生成的离线 HTML 手册。

**执行方式：** 使用 `subagent-driven-development`，每个实现任务由新的 implementer 完成，随后依次做规格符合性审查和代码质量审查；最终由主代理统一回归和真机验收。

---

## 任务 1：建立项目安全的统一导航契约

**文件：**

- 新增：`knowledge_service/web/knowledge-navigation.js`
- 修改：`knowledge_service/web/app.js`
- 修改：`knowledge_service/web/sources-view.js`
- 修改：`knowledge_service/web/index.html`
- 修改：`knowledge_service/web/task-review.js`（仅在缺少公开 `open()` 时补公开入口）
- 新增：`tests/service/knowledge_navigation_ui.test.cjs`
- 修改：`tests/service/test_frontend_static_contract.py`

### 1.1 先写失败测试

在 Node 测试中构造最小 DOM 和模块桩，锁定：

1. `KnowledgeNavigation.open('records', {projectId})` 会验证项目选项、必要时切换 `#project`，派发 `change`，等待切换完成后激活 `records` 页签，并让现有 records 页签点击监听恰好刷新一次。
2. `reviews` 调用审核模块公开入口；`ontology-model` 激活对应页面并让现有画布页签监听恰好刷新一次；`sources` 调用原文模块公开入口并加载任务所属项目原文；`jobs` 先激活 runtime，再调用 `RuntimeView.show('jobs')`。
3. 目标项目不存在时 Promise 拒绝，错误包含“该任务所属项目已不存在”，且不降级到当前项目。
4. 未知 target 明确拒绝。

运行：

```powershell
node --test tests/service/knowledge_navigation_ui.test.cjs
```

预期：新模块不存在，测试失败。

### 1.2 实现最小导航模块

实现 `window.KnowledgeNavigation.open(target, {projectId})`：

- 从 `#project` 的 option 校验项目存在。
- 必要时设置项目并派发 `change`；等待一次事件循环，使现有项目切换监听完成。
- 把 `app.js` 现有的通用页签显隐代码提取为内部 `activateTab(name, button)`；`window.KnowledgeTabs.open(name)` 对有真实 `[data-tab]` 按钮的页面调用该按钮的 `click()`，从而保留 `records-view.js`、`ontology-model.js` 等模块已有的点击刷新监听。只有 `sources` 这类无菜单按钮的内部页才直接使用显隐实现。
- `records`、`ontology-model` 和 `runtime` 通过 `KnowledgeTabs.open()` 激活；测试除了检查显隐，还必须断言各自现有的公开/点击刷新桩恰好调用一次，防止只开页面不加载数据。
- `reviews` 只调用 `TaskReview.open()`；若当前模块只有内部入口，则在 `task-review.js` 暴露一个薄包装。
- `jobs` 使用公开 `RuntimeView.show('jobs')`。
- 当前侧栏已把“原文数据源”并入“知识写入”，不存在 `[data-tab="sources"]` 按钮；因此在 `sources-view.js` 增加 `window.SourcesView.open()`，由它调用 `KnowledgeTabs.open('sources')` 后复用现有 `load()`。统一导航的 `sources` target 只调用这个公开入口，不查找不存在的侧栏按钮，也不复制原文加载请求。
- 错误交给调用方展示，不静默吞掉。

把脚本放在 `ingest-results.js` 之前加载，并增加唯一缓存版本。

### 1.3 验证

```powershell
node --test tests/service/knowledge_navigation_ui.test.cjs
D:/anaconda/envs/llm_model/python.exe -X utf8 -m pytest tests/service/test_frontend_static_contract.py -q
```

---

## 任务 2：让上传结果保留所属项目和写入模式

**文件：**

- 修改：`knowledge_service/web/workbench.js`
- 修改：`knowledge_service/web/ingest-results.js`
- 修改：`knowledge_service/web/ingest-results.css`
- 修改：`knowledge_service/web/index.html`
- 新增：`tests/service/ingest_results_ui.test.cjs`

### 2.1 先写失败测试

测试 `IngestResults.watch(jobId, title, {projectId, mode})` 与渲染行为：

1. 任务模型保存提交瞬间的 `projectId` 和 `mode`。
2. `loadAssertions` 请求 `/api/projects/{taskProjectId}/assertions?...`，即使全局项目后来改变也不串项目。
3. `documents` 空结果显示“主动未执行图谱知识抽取”。
4. `ontology`/`discovery` 空结果显示“已执行抽取但没有识别出知识”，不把原因混写成仅文档模式。
5. 模式名称分别显示“使用项目本体 · 正式入图”“开放本体发现 · 候选暂存”“仅文档检索 · 不抽取图谱”。

运行：

```powershell
node --test tests/service/ingest_results_ui.test.cjs
```

预期：旧 `watch` 签名和全局 `endpoint()` 取项目逻辑导致失败。

### 2.2 传递并使用任务上下文

在 `workbench.js` 提交成功处改为：

```js
window.IngestResults?.watch(job.id, document.title, {
  projectId: project,
  mode,
});
```

在 `ingest-results.js` 中：

- `watch` 保存上下文；重复 watch 不覆盖已保存的任务归属。
- 明细 URL 由任务的 `projectId` 明确构造。
- 卡片显示写入模式徽标和一句准确说明。
- `renderAsserts` 接收任务上下文并按模式区分空结果。
- 任务所属项目缺失时，明细读取失败文案保留在任务卡，不读取当前项目。

### 2.3 增加局部快捷动作

按任务状态和断言状态生成按钮：

- 任意 accepted → “去知识台账核对” (`records`)
- ontology + pending → “去知识审核” (`reviews`)
- discovery + pending → “去本体画布处理候选” (`ontology-model`)
- documents → “去原文数据源” (`sources`)
- failed/cancelled/interrupted 或明细读取失败 → “查看任务日志” (`jobs`)

按钮只调用 `KnowledgeNavigation.open(target, {projectId: d.projectId})`。失败时用现有全局 `status()` 显示错误。不要增加阶段条、自动跳转或新的后端状态。

为动作区补轻量样式，保持它属于结果卡片，不把页面改造成流程向导。

### 2.4 验证

扩展测试覆盖动作集合、跨项目读取、项目删除错误传播：

```powershell
node --test tests/service/ingest_results_ui.test.cjs tests/service/knowledge_navigation_ui.test.cjs
node --check knowledge_service/web/ingest-results.js
node --check knowledge_service/web/knowledge-navigation.js
node --check knowledge_service/web/workbench.js
```

---

## 任务 3：把问答“推理路径”改为准确术语

**文件：**

- 修改：`knowledge_service/web/workbench.js`
- 修改：`knowledge_service/web/index.html`
- 修改：`tests/service/test_frontend_static_contract.py`

### 3.1 先写失败契约测试

测试要求：

- 用户可见模板包含“图谱关系路径”。
- 解释包含“命中实体沿现有关系边可到达的多跳路径”。
- 用户可见模板不再渲染标题“推理路径”。
- 代码仍读取 `reasoning_paths`，保证接口兼容。

### 3.2 改文案，保持接口不变

只改问答渲染标题与说明，不改请求参数、响应字段或后端行为。同步 bump `workbench.js` 缓存版本。

### 3.3 验证

```powershell
D:/anaconda/envs/llm_model/python.exe -X utf8 -m pytest tests/service/test_frontend_static_contract.py -q
node --check knowledge_service/web/workbench.js
```

---

## 任务 4：把操作手册改成可以照着完成的流程

**文件：**

- 修改：`scripts/build_knowledge_service_manual.py`
- 生成：`docs/知识图谱操作手册.html`
- 修改：`docs/2026-10-01-本体工作台操作手册.md`
- 修改：`tests/service/test_docs_contract.py`

### 4.1 先补文档契约测试

测试锁定：

- 三种知识写入模式的名称、用途、是否入图和结果去向。
- 六步操作：选模式 → 选文件 → 预览切片 → 上传并处理 → 打开结果工作区 → 按结果动作继续。
- 五类结果分支：已入图、待审核、待建模、仅文档、失败。
- “图谱关系路径不是 OWL 推理”和“本阶段暂不实施 OWL 推理”。
- 三个排错项：仅文档没有实体、抽取结果为空、待确认去哪里处理。
- 运行环境使用 `llm_model`，不再出现 `model_agent`。

先运行契约测试确认失败：

```powershell
D:/anaconda/envs/llm_model/python.exe -X utf8 -m pytest tests/service/test_docs_contract.py -q
```

### 4.2 修改权威 HTML builder

在“写入固定材料”章节前加入模式对照和选择依据；把演示步骤改成界面上可执行的动作。结果分支统一用“看到什么 / 点击哪里 / 完成标志”三列或同等清晰结构。

问答章节使用“图谱关系路径”，明确它只展示现有图边的多跳可达路径，不是 OWL 蕴含。排错章节加入上述三种情况。

### 4.3 更新 Markdown 手册

在开头增加“当前界面入口”，只补本次相关的模式、结果去向和术语。保持本体建模台直接编辑，并说明校验/发布是治理边界，不写成强制步骤状态机。

### 4.4 重建和验证 HTML

```powershell
D:/anaconda/envs/llm_model/python.exe -X utf8 scripts/build_knowledge_service_manual.py
D:/anaconda/envs/llm_model/python.exe -X utf8 -m pytest tests/service/test_docs_contract.py -q
```

浏览器打开生成 HTML，核对目录、模式对照、六步流程、结果分支、OWL 说明和截图标注。纯文案变化复用既有截图；只有布局导致 `S04b` 标注失真时才更新该截图。

---

## 任务 5：回归验证与 8100 真机验收

**文件：**

- 不新增产品能力；只修复验收发现的本次范围问题。

### 5.1 自动化回归

```powershell
node --test tests/service/knowledge_navigation_ui.test.cjs tests/service/ingest_results_ui.test.cjs tests/service/task_review_ui.test.cjs
D:/anaconda/envs/llm_model/python.exe -X utf8 -m pytest tests/service/test_frontend_static_contract.py tests/service/test_docs_contract.py tests/service/test_records_ledger_ui.py -q
node --check knowledge_service/web/knowledge-navigation.js
node --check knowledge_service/web/ingest-results.js
node --check knowledge_service/web/workbench.js
git diff --check
```

同一时刻只运行一个 pytest 进程。

### 5.2 8100 真机验收

在 `http://127.0.0.1:8100/` 核对：

1. 三种写入模式切换后，上传控件仍可直接操作，本体建模层仍可直接编辑，没有新增流程状态条。
2. 结果工作区显示模式说明和正确空结果文案。
3. 至少一个结果卡的快捷动作会切回任务所属项目并打开正确页面。
4. 问答结果标题显示“图谱关系路径”。
5. 离线 HTML 手册可打开，步骤和页面名称与真机一致。

若既有项目没有可代表的任务数据，只使用隔离演示项目生成验收任务；验收后不删除或改动用户业务项目。

### 5.3 最终报告

逐条报告：没增加什么、实际改了什么、影响什么、UI 如何变化、流程前后差异、测试和真机证据。明确仍未实施 OWL 推理和非流式 JSON 问答溯源统一。
