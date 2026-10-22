# 统一知识写入、本体工作草案与新版图文手册实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 把资料、事实与结构写入按三条已批准操作路径落地，候选可安全加入明确的工作草案；用真实页面重写完整图文手册后移除已被替代的旧操作手册。

**Architecture:** 保持批次/事实与本体草案边界。新增候选预演、指定草案加入、正式术语复用确认服务；每次加入保留不可变来源运行，多次加入可绑定同一草案，发布聚合所有绑定并按最终获批操作计算效果。前端单一结构创建入口及独立待完善结构抽屉；保存与完整发布校验继续分离。

**Tech Stack:** FastAPI、现有 PostgreSQL 权威存储与事务/CAS、Semantica、Milvus、原生 JS/CSS、Node node:test、现有 conda llm_model Python、离线 HTML 图文手册。

**Approved spec:** `docs/superpowers/specs/2026-10-20-unified-knowledge-intake-operation-design.md`；详细依赖与预演语义继承 `2026-10-20-pending-modeling-flow-design.md`。用户已明确要求开始实施，无需重复询问实施许可。

## 0. 工作区与交付保护

- [x] 比较运行目录与现有工作树；44 个较新文件按运行目录内容更新工作树，差异和双方副本保存到本任务私有目录。
- [x] 当前实施目录：`C:/Users/Thinkpad/.codex/worktrees/knowledge-workspace-redesign`；运行目录：`D:/workspace/knowledge_service_v2/knowledge_service`。
- [x] 按文件所有权实施，不重置任何既有改动。集成前检查运行目录是否又有变化；只同步本任务及已保留基线的变更。
- [x] 真实密钥不进入输出、文档或代码；本机验收登录使用既有认证配置。不重建容量项目，不重复启动编码器/应用。Milvus 保持唯一向量后端。
- [x] PostgreSQL pytest 由主代理串行运行；子任务只运行独立 Node/纯 Python 检查。

## 1. 后端候选写入与聚合发布

**Owned files**

- Create `knowledge_service/services/ontology_intake.py`（按需拆纯预演与事务适配，避免继续扩巨型门面）。
- Modify `knowledge_service/api/ontology_discovery.py`, `services/ontology_discovery_workflow.py`（保留旧路径兼容）。
- Modify `services/ontology_drafts.py`, `ontology_drafts_review.py`, `ontology_drafts_commands.py` 的小型接缝。
- Modify `repository/ontology_draft_store.py`, `repository/core.py` 的原子追加、来源核验和复合发布接缝；保持既有 discovery-run 存储校验。
- Add `tests/service/test_ontology_intake.py` 及必要的旧发现/发布回归用例。

**共享接口约定**

所有路由前缀 `/api/projects/{p}/ontology-discovery`，参数和返回字段通过路由 Pydantic 明确定义，actor 来自服务端审计身份。

1. `GET /candidates`：读取待处理候选、来源文档/版本、证据、建议术语种类、当前状态、分页和可用筛选。稳定候选 ID 用于精确确认事实范围。
2. `POST /selection-preview`：选择 `candidate_ids`、正式 `expected_ontology_id`、明确 `target_draft_id + expected_revision` 或 `create_new_draft:true`；可带 `confirmed_mappings`。返回只读预演指纹、来源指纹、目标身份、结构建议/差异、复用/冲突、关联事实范围、缺失依赖和警告。没有任何草案/操作/运行/回执写入。
3. `POST /add-selection`：重发原选择、预演指纹、来源指纹与 `idempotency_key`；服务端重新计算并核对。一次事务保存目标草案操作、绑定/来源、不可变选中运行与请求回执；返回准确草案身份/修订和各项结果。
4. `POST /confirm-reuse`：只允许明确映射至正式发布的术语，选择和来源受预演指纹约束；选中事实校验、写入及幂等回执原子完成，不创建本体草案或发布。

字段细节由后端实施者在编码前提供给前端实施者；前端不得猜缺失字段或自己启用发布。通用返回对象应包含 `target`、`candidate_ids`、`suggestions`、`associated_fact_scope`、`new`、`reused`、`conflicts`、`warnings`，以及已保存操作的 `draft` 与独立 `receipt`。

**实施步骤**

- [x] 写只读预演、精确范围、保留手工操作、多个来源运行、来源变化和 CAS 回滚的失败测试。
- [x] 实现对当前草案投影的追加式补丁；不得将基于正式版本生成的整份 Turtle 覆盖到已有手工草案。
- [x] 显式展示关系/属性所需实体事实依赖。未选依赖阻止确认或返回可理解诊断，不能暗中扩大所确认范围。
- [x] 保留草案原始来源身份；每条追加操作保存自己的来源/证据。以来源绑定而非草案 origin 决定来源核验。
- [x] 多来源效果采用集合，不覆盖已有 `publication_effects`；兼容既有单运行发现草案。
- [x] 请求重放先核对已完成回执再核对陈旧修订；相同 key 不同意图返回冲突。新草案请求也不能因重试产生第二份草案。
- [x] 聚合发布先统一检查事实快照，再执行效果；最终获批/有效/未停用结构决定哪些候选可写。替换、撤销、拒绝和来源撤回保留逐项诊断。
- [x] 关闭草案时关闭所有附属运行，来源候选可回查且不会自动入库。
- [x] 回执分别报告本体发布、逐来源事实新增/复用/跳过与实际持久索引任务状态。
- [x] 不牺牲原子性、序列化依赖核验、CAS、操作与决定指纹、发布警告或幂等性来简化实现。

**必要验证（由主代理选择正确测试文件串行运行）**

```text
python -X utf8 -m pytest tests/service/test_ontology_intake.py -q
python -X utf8 -m pytest tests/service/test_ontology_drafts.py tests/service/test_ontology_draft_api.py tests/service/test_ontology_discovery.py tests/service/test_multiround_ontology_ingestion.py -q
```

若新增数据库结构，使用下一空闲编号迁移，补权限与快照兼容；先在隔离测试库验证，再由主代理运行正式迁移。优先复用已有不可变运行和事务能力，不创建平行状态机。

## 2. 本体操作入口与抽屉

**Owned files**

- Create `web/ontology-intake.js`, `web/ontology-intake.css`。
- Modify `web/ontology-model.js`, `ontology-model-panel.js`, `ontology-model-catalog.js`, `ontology-model-canvas.js` 及其对应 CSS。
- Modify/add `tests/service/ontology-model-refresh.test.cjs`, `ontology-catalog-delivery.test.cjs`, `ontology-governance-delivery.test.cjs`, `ontology-canvas-interaction.test.cjs` 与新的 intake 控制器测试。
- 主代理负责 `web/index.html` 的依赖顺序/缓存版本以及来源页/批次结果/知识台账的上下文跳转，避免与独立前端任务冲突。

- [x] 用失败测试覆盖多草案未选择时不可偷偷挑第一份、列表读取失败不可当成无草案、新接口预演与保存分离。
- [x] 顶部一个“新增定义”菜单；保留上下文子类/属性/关联关系快捷入口与继承/关系编辑。
- [x] 明确正式发布、当前工作草案 ID、修订、基线；不把草案启用状态描述成已正式可用。
- [x] 草案切换/另建/关闭归“本轮修改”，源码导出归高级入口；原有版本/回退/源码/SHACL/父类发现均保留。
- [x] 待完善结构抽屉提供候选选择、来源证据、准确目标/新建意图、只读预演、明确保存与回执。
- [x] 多份草案且没有明确选择时显示目标选择；已有选择则沿用，首项实际结构写入才自动准备新草案。
- [x] 同一请求未知结果重试复用 key；项目、草案、修订或来源变化后旧预演不可提交。
- [x] 仅复用正式术语时独立确认事实范围，明确不新建草案或本体版本。
- [x] 发布面板显示关联知识效果，成功提示包含实际事实结果与索引状态；无绑定的手工发布显示 0 条。
- [x] 保持画布刷新和成功提示不等待完整发布校验，后台审核状态只更新动作栏，不重置未保存表单。

验证：Node 精确文件回归后运行 `node --test tests/service/*.test.cjs`。主代理进行真实页面验证，不用模拟截图代替。

## 3. 来源批次与知识页面衔接

**Owner:** 主代理。

- [x] 在 `web/sources-view.js`、`ingest-batches.js`、`ingest-results.js`、`records-view.js` 的相关结果入口传准确文档/候选范围，打开同一 intake 抽屉。
- [x] 展示当前候选是否已关联草案，能够往返来源证据与目标草案；不依赖未填充的本地 candidates 数组。
- [x] 沿用现有添加文件与批次抽屉，不恢复平铺上传区。
- [x] 相关静态契约随真实行为更新，不以删除断言掩盖能力丢失。

## 4. 新版完整图文手册与旧手册清理

**Owned files**

- Create `docs/2026-10-20-知识平台完整操作手册.md`；更新稳定输出 `docs/知识平台完整操作手册.html`。
- Modify `scripts/verify_redesign_manual.py`, `scripts/verify_redesign_manual_browser.py`, `tests/service/test_docs_contract.py`。
- Update README、活动文档链接及旧生成器入口，避免重新生成旧版手册。
- 主代理提供真实实施后的 `docs/manual-redesign/` 截图，文档任务负责叙述、标注引用与覆盖映射。

- [x] 新版覆盖全部现有功能，建议 29 章：总路线、三条写入路径、项目人员、资料与证据、事实审核/CRUD、建模/源码/SHACL、草案发布、版本迁移、融合/消歧/撤销、范围、检索问答、局部脑图/全量图、消融评测、任务/索引、备份导入、版本示例、容量边界、排错与入口/API映射。
- [x] 每章保留场景、为什么与何时、前置条件、详细步骤、正确结果、失败恢复、下一步；截图配解释性图注和“先看哪里/操作后哪里改变”。
- [x] 先以实现回执更新三条路径，不将原交互稿当成上线功能，不虚构大库性能或全功能对标通过。
- [x] 生成并检查离线 HTML、锚点、链接、截图、API 与章节覆盖。
- [x] 新版通过后再删除四份已替代旧操作手册：2026-10-19 完整 MD、知识图谱操作手册 HTML、2026-10-01 本体工作台 MD、2026-10-07 登录账号权限用户手册 MD。
- [x] 保留运维/基础设施文档、规格/计划、research 历史证据及仍被引用的旧截图。删除使用精确文件清单，不递归清理目录。

## 5. 集成、实际验收与收尾

- [x] 规格符合性和代码质量分别审查；修复实际发现后再复核。
- [x] 主代理单次串行测试会话，针对受影响 Python 契约和前端全套 Node；通过后只在有新改动或疑点时补测。
- [x] 如需迁移，检查容器和正确数据库后执行；不碰既有 15 万测试项目内容。
- [x] 检查 8100 监听进程的命令和解释器后重启这一应用一次；不同时启动第二个编码器/服务。
- [x] 在独立保留验收项目跑三条路径、同一草案两个来源再手工编辑、草案关闭/历史、来源往返、失效预演和明确事实回执。
- [x] 正常 HTTP 产品页面截图，当前用户业务草案只读核对。客户端截图访问失败时记录真实限制，不绕过浏览器安全策略。
- [x] 收集实际 API/UI 回执、服务时间观察和文档覆盖证据；不把静态检查当作业务验收。
- [x] 新手册可打开、旧活动链接已替换、删除清单精确、新页面资产版本/字节与交付一致后完成交付。

## 6. 实际交付验收（2026-10-21）

- 最终串行 Python：`test_ontology_intake.py`、`test_ontology_intake_validation.py`、`test_discovery_run_store.py`、`test_docs_contract.py`，196 passed。隔离测试库 `knowledge_test_intake_20261020`；这份结果不代表未经运行的其他测试文件。
- 前端全套 `node --test tests/service/*.test.cjs`：368 passed，0 failed。
- 规格复核与独立质量复核通过；补齐多来源及已有事实联合校验、完整必填属性数据集校验、最终停用术语及其依赖事实排除。
- 真实 UI/HTTP：手工 r2 → 来源 A r3 → 来源 B r4 → 手工继承/关系端点 r8 → 正式 v1 新增5条；仅复用正式客户术语另新增1条，无新草案/版本。来源改变的旧预演409且无额外写入；关闭另一草案未写事实或版本；已验证来源返回与历史只读返回。
- 运行目录已交付并重启唯一8100应用；本地1024维编码器、Milvus和集合就绪；严格语义检索命中云帆软件，`degraded=false`。索引任务均completed；发布回执中的pending仍为发布时快照。
- 新手册29章、每章7项说明、286锚点、22张实际截图；运行目录静态校验及12项纯文档契约通过。四份替代旧手册按精确路径移除，私有目录有备份。
- 保留合成验收项目；原业务草案身份/修订/状态和容量项目150001实体/300000关系前后相同。本轮未重新认证15万图谱性能、P95、OCR/解析或模型抽取质量。
- 详细回执见 `docs/research/2026-10-21-unified-intake-acceptance.json`；代码与工作树保留，不代用户提交、推送或合并。
