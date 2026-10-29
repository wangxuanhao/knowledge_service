# Ontology Attribute Editing Implementation Plan

> **For agentic workers:** Execute in the current authorized checkout; use focused review agents for this plan and the final code. Steps use checkbox syntax for tracking.

**Goal:** 从实体类型直接管理属性，补齐新增属性数据类型与说明、已有属性名称说明编辑。

**Architecture:** 复用 OntologyModel.createTerm/sendCommand 和既有原子批量命令。新增 ontology-model-attributes.js 负责属性视图和差异编译；服务端摘要只增加可编辑注解的精确信息，不新增写通道。保留其他正在进行的工作。

**Tech Stack:** 原生 JS/CSS，Node node:test，Python 3.12、rdflib，Playwright/Edge。

---

### Task 1: 精确文本编辑与 RDF 注解

Files: `knowledge_service/services/ontology.py`, `knowledge_service/services/ontology_drafts_readmodels.py`, `knowledge_service/web/ontology-model-attributes.js`, `tests/service/ontology-attribute-editing.test.cjs`, `tests/service/test_ontology_attribute_editing.py`.

- [x] 写失败测试：属性摘要提供完整 label/comment 字面量注解；修改中文名、说明生成精确移除与新增，保留英文与其他语言；名称必填，说明允许清空并只移除对应语言注解，历史只读拒绝编辑。
- [x] 运行 Node 专项，确认因缺失模块/能力失败。
- [x] 实现属性文本摘要与纯差异编译；现有 annotation/batch 命令负责落库。描述选择与摘要一致，语言/数据类型不丢失。
- [x] 用 `D:/anaconda/envs/llm_model/python.exe -m unittest discover -s tests/service -p test_ontology_attribute_editing.py` 验证 RDF 重放，避免并行 pytest。

### Task 2: 类型属性管理与新建表单

Files: `knowledge_service/web/ontology-model-attributes.js`, `knowledge_service/web/ontology-model-panel.js`, `knowledge_service/web/ontology-model-panel.css`, `knowledge_service/web/ontology-model.js`, `tests/service/ontology-attribute-editing.test.cjs`, `tests/service/ontology-term-commands.test.cjs`.

- [x] 写行为测试：直属/继承属性、来源、搜索勾选关联，原子 add_domain/remove_domain；失败保留，历史只读，过期视图拒绝提交。
- [x] 实现类型属性区、添加入口与已有属性关联。共享属性在界面明确编辑范围。
- [x] 新建属性表单预选实体类型，加入数据类型和说明，同一 batch 包含声明、标签、说明、domain、datatype。初次添加默认文本，既有未知数据类型保留。
- [x] 属性详情挂载名称说明表单，现有所属类型与数据类型编辑继续工作。
- [x] 运行 `node --test tests/service/ontology-attribute-editing.test.cjs tests/service/ontology-term-commands.test.cjs tests/service/ontology-panel-clarity.test.cjs tests/service/ontology-catalog-delivery.test.cjs`。

### Task 3: 交付与回归

Files: `knowledge_service/web/index.html`, `tests/service/ontology-attribute-editing.browser.py`, relevant existing browser harness.

- [x] 注册新模块并 bump 改动资源版本，保留 index.html 其他在途修改。同步受影响测试。
- [x] 在 1280/390 宽度真实浏览器验证新增、关联、编辑、只读与布局，截图放 `.benchmarks/ontology-attributes/`。
- [x] 运行全 Node 契约，Python 属性/命令/摘要专项，以及 `git diff --check` 和 JS 语法检查。
- [x] 独立代码审查并处理实际问题，记录验证结果；不提交其他在途工作。


## 2026-10-28 共享工作区验证

- 属性编辑及相关 Node 专项：38 passed，0 failed。
- Python 本体、摘要、命令、属性编辑、草案投影专项：24 passed，0 skipped（1 条现有 Starlette 弃用警告）。
- 独立浏览器：1280 与 390 宽度通过，包含实际前端命令路径的新增、数据类型、说明、归属、继承、保存失败重试与历史只读；不写业务数据库。
- 独立代码审查通过。修复了多中文语言标签被误删、新建空 endpoints 数组覆盖 domain、跨上下文提交、重渲染后的提交锁与成功状态清理。
- JS 语法、Python 编译、资源顺序/缓存版本、改动文件 diff 检查通过。
- 最后一次全 Node 回归：751 passed / 2 failed，共 753。失败均在另行修改中的 full-graph-renderer.test.cjs 的 relation picking 用例（3 和 5001 条关系），测试预期橙色高亮，当前 full-graph.js 实际为青色。该轮未修改这些文件；前一轮全 Node 752 项曾全通过，当前两项差异单独记录，不将其计为本体属性编辑通过项。
- 截图与输出位于被忽略的 .benchmarks/ontology-attributes/ 和 .benchmarks/attribute-editing-*。


## 2026-10-29 master 发布验证

本次以 origin/master 的 fbc7c15 为基线，在独立工作树中只纳入属性编辑相关文件。当前资料阅读分支和其他在途功能保持原状。

- 干净基线全 Node：711 passed，0 failed。
- 属性编辑待发布版本全 Node：728 passed，0 failed。
- Python 本体、持久化摘要、命令批次、属性编辑、草案投影、规范 API 与草案 API：52 passed，0 skipped。存在 1 条现有 Starlette 弃用警告。
- 浏览器 1280/390：新增属性、数据类型和说明、归属差异、继承来源、失败重试、历史只读、页面边界均通过。
- 补齐旧已发布版本缺失注解的只读兼容投影，API 回归验证原存储摘要不被改写；单元与独立审查均通过。
- 发布版本的本体前端资源统一更新为 attribute-editing-20261029-1。JS 语法、Python 编译、暂存差异检查通过。
- 隔离目录首次运行因没有数据库连接配置而跳过 52 项，该次不计入通过；后续仅加载 PostgreSQL 连接键，保持测试业务配置隔离，实际数据库复跑已全部通过。
- 输出与截图保存到原工作区被忽略的 .benchmarks/ontology-attribute-release-20261029/。
