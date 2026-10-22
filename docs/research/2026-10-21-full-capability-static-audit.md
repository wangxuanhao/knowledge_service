# 全能力静态审计（2026-10-21）

## 审计口径与范围

审计目录为 `D:/workspace/knowledge_service_v2/knowledge_service`，读取时 HEAD 为 `724c10d`，结论针对磁盘上的当前源码（包含未提交工作），不是只针对该提交。已读 `AGENTS.md`、`CONTEXT.md`、10/19 完整能力台账、10/19 检索/大图验收规格和 10/20 完整操作手册。按 AGENTS 优先尝试 codebase-memory MCP；`list_projects` 持续无返回，终止等待后按已知文件和 `rg` 回退。未读 `.env`，未启动 pytest、Node 测试、HTTP、DB、模型、服务或工作树，未提交代码。

“已实现”仅指能从当前 UI 调用一路核到注册的 API、真实服务逻辑和可复核测试，**所有运行行为均为本轮未验收**。测试文件存在、测试名称与静态断言不等于本轮通过；历史验收 JSON/手册中的测量仅作为历史证据。没有把纯按钮、未来规划或模型替身的质量当成真实完整能力。

下表 `P` 为 `/api/projects/{project_id}`。文件路径相对仓库根，行号为本次读取锚点；大文件继续改动后行号可能移动。

## 优先结论

1. **不能声称初次审计时“效率优化且真实功能零损失”。** 初次读取时在线检索仅允许 `id/kind/type/source_id/valid_from/valid_until` 过滤，拒绝任意 Metadata 字段；当前关键词检索同样走此限制。只有历史关键词走 PostgreSQL 允许更丰富条件。它与原始能力表中的 Metadata 检索范围存在真实差距。见初次读取的 `web/scope-fields.js:17–47`；应与独立检索审计共同判定恢复后的实现。**NOT 不属于本轮退化**：旧 HEAD 与当前公共 AST 都只支持 AND/OR 和叶子谓词（含 ne），没有 NOT 节点；若要支持 NOT 属新增能力。
2. **本体、候选与事实治理已有实质实现，不能为了提速退化为说明入口。** 候选选择/预演/明确加入、三类术语、父类发现、Turtle 差异、校验/审核/发布、历史恢复与受控迁移，实体/关系/独立属性 CRUD、融合显式选值、消歧和撤销均可核到服务逻辑。不是只剩按钮。
3. **15 万目标只部分达到。** 代码已有全量会话、完整枚举、WebGL 与共享详情；此前测量有完整计数/端点/绘制证据。但手册最新口径仍未通过浏览器全量可交互 P95≤30s；10/21 HTTP 枚举 29.468s 只有单次且不含浏览器绘制。10/21 保留容量项目未向量化，普通项目小样本不能证明当前 15 万向量检索通过。本轮没有重测。
4. **以下是真实未完成的能力边界，不能用相邻功能冒充：** 按用户选定逻辑来源/文档 rN 提交的完整 revision manifest、逻辑来源新修订/撤回的完整独立流水线、跨草案合入、项目成员 ACL、社区报告/Global/DRIFT、任意两发布的细粒度差异比较、通用多格式本体导入。其中前五项在手册已列为独立阶段，不应伪称当前已交付，也不应自动当作“被删除的既有能力”。
5. **10/19 台账存在接口与能力描述漂移。** 当前已用 intake 的 `selection-preview/add-selection/confirm-reuse` 代替旧 `ontology-discovery/drafts`、`runs/{id}/finalize` 写入路线；术语创建用草案 `commands`，不应继续以旧 `POST ontology/terms` 作为当前证据。资料详情如今按 `documentId` 打开候选抽屉，不再仅跳转项目总候选。10/20 手册的新 API 映射更接近当前实现。

## 能力矩阵

### 资料、上传、候选与来源

| 功能 | UI / API / 服务证据 | 测试证据（仅读取，未执行） | 判定及限制 |
| --- | --- | --- | --- |
| documents / 受控 / 开放三种上传策略 | `web/ingest-ontology.js:43–50` 固定受控发布 ID；`api/parity.py:91–131` 批次与文件 API；`models.py:145–161` 独立抽取/融合参数；`services/service.py` 的 ontology/discovery 分支 | `ingest-ontology.test.cjs`、`test_ingest_request_contract.py`、`test_ingest_batch_runner.py:8` | **已实现；本轮未验收。** 开放程度、关系约束和融合是不同参数；documents 不等于实体抽取成功。 |
| 解析预览、格式/大小限额、ZIP、切片、逐文件选项 | `api/parity.py:84–131,230–273`；`services/ingest_batches.py:64` 格式守卫；`services/document_ingest_runner.py:112–173` 批次设置合并 item.options、保留来源与映射切片 | `ingest-input.test.cjs`、`test_document_parser.py`、`test_ingest_mapped_chunking.py`、`test_document_upload_api.py` | **已实现（按服务声明的格式）；本轮未验收。** 不推导为支持所有格式或任意文件。 |
| OCR 读取、重试、人工忽略及证据定位 | `web/source-reader.js`；`api/source_documents.py:24–82` 固定来源/资产/OCR/locate；`document_ingest_runner.py:144–164` OCR 流进入映射切片 | `source_reader_ui.test.cjs`、`test_source_document_api.py:6`、`test_source_object_protocol.py` | **已实现；本轮未验收。** 真实 OCR/解析器业务质量不能由静态测试替代。 |
| 批次开始/暂停/继续/取消、失败文件/分段重试 | `web/ingest-batches.js`；`api/parity.py:127–131`；`services/ingest_batch_runner.py`、`ingest_checkpoints.py`、`document_ingest_runner.py` 的租约/守卫 | `ingest_batches_ui.test.cjs`、`test_ingest_batch_runner.py:49,97,120,142,162,194` | **已实现；本轮未验收。** 检查点复用、配置变化要求重启、取消后晚到输出隔离都有具体用例。 |
| 资料列表、断言、运行、历史与固定原文 | `web/sources-view.js:133–158`；`api/knowledge.py:88–103` assertions / ingest-runs；`api/source_documents.py` 固定版本读取；`services/evidence.py` | `sources-library.test.cjs`、`test_assertions.py`、`test_assertion_evidence.py`、`test_source_document_api.py` | **已实现；本轮未验收。** 正式事实来源在 assertions，不在 canonical 记录的顶层 source_id。 |
| 明确选择候选、分页、只读预演、加入明确草案 | `web/ontology-intake.js:170–212`；`api/ontology_discovery.py:19–60`；`services/ontology_intake.py:191–295,447–585`；目标草案修订/正式基线、来源/预演指纹、幂等键及事务 | `ontology-intake.test.cjs:21,31,37,50,83,100,160`；`test_ontology_intake.py:93,107,141,305,343,387,414` | **已实现；本轮未验收。** 不隐式把全部项目候选纳入，不因超时生成第二份意图。 |
| 只复用已发布术语写入事实 | `api/ontology_discovery.py:58–60`；`ontology_intake.py:588–636`：逐候选明确映射、最终整体 SHACL 校验、正式写事务、来源断言与索引 outbox；不创建结构草案 | `test_ontology_intake.py:280,585,612,703`；`test_ontology_intake_validation.py:209,219,233,248` | **已实现；本轮未验收。** 未确认映射不能凭同名自动正式化。 |
| 草案发布后只写所选且获批结构关联的事实 | `ontology_intake_publication.py`；`ontology_drafts_review.py:74–270`；`ontology_drafts.py:217–289` 再验来源/断言快照 | `test_ontology_intake.py:197,228,325,358,387,517,635,668,727` | **已实现；本轮未验收。** 未选、缺依赖、拒绝结构或最终校验失败有逐项去向，不等于发布就全量补写候选。 |
| 精确来源修订 / 候选 manifest | 当前 `SelectionRequest` 只有 candidate_ids、正式基线、目标草案和修订；候选 GET 只支持 document_id+分页。`ontology_intake.py:87–101,447–516` 已冻结候选和当前来源；`ontology_drafts.py:217–289` 有不可变快照与 stale-source 守卫 | `test_ontology_intake.py:387,517`、`test_ontology_drafts.py:496,879` | **部分实现。** 不能说“没有冻结候选”；缺的是用户按选定文档 rN/逻辑来源修订清单提交与恢复的完整产品协议。 |
| 来源撤回与最后支撑失效 | `Governance.delete` → `_commit` 把 delete 映射为 retract_source（`governance.py:69–90,309–329`）；`formal_writes.py:421–483` 撤断言、保留多来源支撑、最后支撑后停用关系/属性/标实体 unsupported | `test_source_retraction.py:9`；`test_formal_writes.py:166,199,247` | **部分实现。** 记录删除的真实撤回效果必须保留；独立逻辑来源的新修订/撤回流水线、入口和明确关系协议仍未形成，不能用记录删除冒充完整交付。 |
| 受控抽取候选/属性冲突/本体变更提议审核 | `web/task-review.js`；`api/reviews.py:15–24`、`api/ontology_changes.py`；`services/reviews.py:175,237` 冲突和依赖守卫 | `task_review_ui.test.cjs`、`test_typed_reviews.py`、`test_reviews.py`、`test_ontology_change_proposals.py` | **已实现；本轮未验收。** 普通批准不绕过单值冲突，关联实体未入图时不能直接写依赖事实。 |

### 本体建模、治理、发布与迁移

| 功能 | UI / API / 服务证据 | 测试证据（仅读取，未执行） | 判定及限制 |
| --- | --- | --- | --- |
| 新建类/上级类/子类/关系/属性 | `web/ontology-model.js:346–405` createTerm 生成稳定 IRI、批量 commands；`ontology-model-panel.js` 结构化表单；`api/ontology_drafts.py:118–120`；`ontology_operations.py` create_term/父类/domain/range/datatype | `ontology-catalog-delivery.test.cjs`、`ontology-canvas-interaction.test.cjs`、`test_ontology_operations.py:155,161,182`、`test_empty_project_ontology_flow.py` | **已实现；本轮未验收。** 发布后先建草案继续建模；不是发布后只读。 |
| 父类连接/移除、多父类与两步连关系 | `ontology-model-canvas.js:722`；`ontology-model-panel.js:621–718`；`ontology_drafts_commands.py:89–100`；`ontology_operations.py` 防自环/重复/多跳环 | `ontology-canvas-interaction.test.cjs`、`test_ontology_operations.py:108,182` | **已实现；本轮未验收。** 插入中间父类并重接旧边可由命令组合完成，未核到专用单步原子操作，不应额外承诺一键完成。 |
| 模型发现父类 | `ontology-model.js:521,728`；`api/ontology_drafts.py:122–128`；`ontology_drafts_commands.py:29–80`，模型返回后二次核修订/基线并写 diff；`hierarchy_suggestion.py:39,83–162` 只采用清单已有类 | `ontology-model-refresh.test.cjs`、`test_hierarchy_suggestion.py`、`test_ontology_drafts.py` | **已实现（已有类间层级建议）；本轮未验收。** 不会凭空创建新的抽象父类概念；模型建议语义质量未验。 |
| 名称/说明/稳定 IRI，多/空 domain/range | `ontology-model-panel.js:621–718`；`ontology-model.js:369–405`；`ontology_operations.py:210` 等精确注解/union 操作 | `ontology-catalog-delivery.test.cjs`、`test_ontology_relation_endpoints.py`、`test_ontology_operations.py:210,223,377` | **已实现；本轮未验收。** 不以画布有没有边判关系术语是否存在，标签变化不重建术语身份。 |
| 类型卡片/图谱/层级树/布局 | `ontology-model-canvas.js`、`ontology-model-catalog.js`；布局与草案命令分离 | `ontology-canvas-interaction.test.cjs`、`ontology-catalog-delivery.test.cjs` | **已实现；本轮未验收。** 层级显示不是 15 万实例容量验收。 |
| RDF 源码查看/导出、Turtle 差异编辑、只读 SPARQL | `ontology-model-catalog.js:121–165` 复核打开时项目/草案/修订；`ontology_drafts_commands.py:243–285`；`ontology_operations.py:1170–1270` 差异安全编译；`api/knowledge.py:200–228` | `ontology-advanced-tools.test.cjs`、`ontology-catalog-delivery.test.cjs`、`test_ontology_operations.py:1176,1202,1224,1271,1282` | **已实现（受支持的 Turtle 差异）；通用导入缺失；本轮未验收。** 复杂 OWL Restriction/不支持的 blank node 明确拒绝（`ontology_operations.py:1013–1023`），不承诺任意本体格式。 |
| SHACL 约束/语法/质量/实例影响分项校验 | `ontology_drafts.py:827–939`；`ontology_drafts_review.py:273–651`；高级 SHACL 经 shape-scoped advanced_rdf_patch；UI 常规编辑主要为继承/domain/range/datatype，复杂约束经 Turtle | `test_ontology_operations.py:461,597,1052,1106,1202`、`test_ontology_review_plan.py`、`test_ontology_publish_planning.py` | **已实现；专用可视化约束控件覆盖有限；本轮未验收。** UI“约束编辑”不等于全部 SHACL 约束都有独立字段控件；未运行的外部质量结果不可标通过。 |
| 停用三类术语、影响分析、历史字段选择恢复 | `ontology-model-panel.js:494–596`；`api/workspace.py:86–92` term-impact；`ontology_drafts_commands.py:128–198,286–295`；`ontology_operations.py` 原因/替代/依赖矩阵/同项目历史模板 | `ontology-governance-delivery.test.cjs`；`test_ontology_operations.py:386–461,615–752`；`test_ontology_drafts.py:1348,1369,1437` | **已实现；本轮未验收。** source_ontology_id 和 selected_fields 真实进入命令，恢复不改写历史。 |
| 草案创建/切换/关闭、已有发布继续修改 | `ontology-model.js:363–425`；`api/ontology_drafts.py:78–90,153–157`；`ontology_drafts.py:697,1163` | `ontology-history-lifecycle.test.cjs`、`ontology-model-refresh.test.cjs`、`test_ontology_drafts.py` | **已实现；本轮未验收。** 明确选择目标，不靠“最新一份”猜，不删除关闭草案的审计。 |
| 重新基线/来源刷新与重放冲突 | `ontology-model-panel.js:1215–1225`；`api/ontology_drafts.py:148–151`；`ontology_drafts.py:1245` 起 rebase，`ontology_intake` 的 immutable run 不能被刷新解冻 | `test_ontology_drafts.py:655,682`、`test_ontology_intake.py:517,539`、`ontology-model-refresh.test.cjs` | **已实现；本轮未验收。** rebase 不是跨草案合入。 |
| 逐条/安全批量审核、发布清单 | `ontology-model-panel.js:1240–1444`；`api/ontology_drafts.py:132–194`；`ontology_drafts.py:1009–1161`、`ontology_drafts_review.py:369–709` | `ontology-governance-delivery.test.cjs`、`test_ontology_review_plan.py`、`test_ontology_drafts.py:1047,1285` | **已实现；本轮未验收。** 风险、fingerprint、警告确认、最终批准子集由服务裁决，不能用安全批量批准绕过高风险。 |
| 不可变发布、同 vN 标注修订、幂等并发 | `ontology_drafts_review.py:709` 起发布 preflight→repository；`api/knowledge.py:179–200` 发布列表；版本 UI 使用 publication ID | `test_ontology_drafts.py:784,879,902,919,964,1020`、`record-version-context.test.cjs` | **已实现；本轮未验收。** 同版本号可有不同不可变身份，发布不等于索引同步完成。 |
| 历史查看/以历史为基线建草案/当前草案 diff | 版本管理、`GET P/ontologies` / `P/ontology?ontology_id`、draft create/revert/source 操作 | `ontology-history-lifecycle.test.cjs`、`ontology-governance-delivery.test.cjs` | **已实现；本轮未验收。** 任意两发布的细粒度对比没有独立 API/完整交互证据，不能把草案相对基线 diff 冒充它。 |
| 关联知识、受控重分类预演/执行/撤销 | `web/ontology-model-reclassify.js`；`api/parity.py:332–362`；`reclassify.py:137–264` carry/rename/unmapped、选择组、同写入校验、Governance._commit 新修订审计 | `test_reclassify_migration_contract.py:37–62`、`test_reclassify_migration_ui.py`、`test_reclassify.py` | **已实现；本轮未验收。** 无映射不会自动迁，不能默认“一键全迁”，也不迁移正文/有效期。 |
| 跨草案合入 | 当前 drafts API 仅 create/list/get/commands/discover/validate/submit/decide/rebase/close/publish，无 merge；当前 UI 有选择/关闭/基线对齐 | 手册第 27 章明确独立阶段 | **缺失（批准的后续阶段）。** 把不同候选批次加入同一草案已有，但这不是把两份既有草案合并。 |

### 正式知识、实体治理、运行与管理

| 功能 | UI / API / 服务证据 | 测试证据（仅读取，未执行） | 判定及限制 |
| --- | --- | --- | --- |
| 实体/关系/独立属性新增、编辑、历史、冲突比较 | `record-create.js`、`record-dialog.js`、`record-attribute-editor.js`；`api/knowledge.py:39–84` 201/expected_version/kind 不变/沿用实际本体身份；`formal_writes.py` 正式计划/断言/事实键事务 | `record-create*.test.cjs`、`record-attribute-editor.test.cjs`、`test_formal_writes.py:31,48,80,108,128,226,247,270,309,367,385,569` | **已实现；本轮未验收。** 独立属性/关系 ID 和修订真实存在；共享详情不能在大图模式变成只读说明。 |
| 台账服务端分页/精确 ID、深页详情 | `records-view.js`、`record-create.js` selector；`api/graph_sessions.py` 的 records/page；`repository/scoped_reads.py` | `records-pagination-delivery.test.cjs`、`local-graph-picker.test.cjs`、`test_page_summary_reads.py`、`test_records_ledger_ui.py` | **已实现；本轮未验收。** 页面加载子集不是全库，局部图定位也不替代它。 |
| 别名、查重、重复组 | `records-view.js`/`entity-governance.js`；`api/parity.py:295–320`；`governance.py:25–68,334–353` | `entity-governance-delivery.test.cjs`、`test_entity_matching_contracts.py`、`test_entity_resolution.py` | **已实现；本轮未验收。** duplicate-groups 是同名同类型；近名候选需 resolve，候选命中不等于实体相同。 |
| 融合预演、冲突 winner 与提交事务 | `entity-governance.js`；`api/parity.py:301–308`；`governance.py:141–307` 校验类型/范围/业务区间，冻结实体/属性/关系成员及修订，提交再验，逐冲突组选恰一条现有属性 | `test_governance_delivery.py:37,49,65,93,116,134`（该文件使用替身，不能称 PG 实测）；`test_entity_merges.py:47`、`test_formal_writes.py:341,478` | **已实现；本轮未验收。** 跨 publication 的相同稳定类型可融合，不按同号版本机械阻止；不能省掉属性及来源治理换取速度。 |
| 消歧合并/保持独立/驳回 | `entity-governance.js`；`api/knowledge.py:106–130` 真实 review version、entity versions、winner、preview fingerprint；合并委托同一 Governance | `entity-governance-delivery.test.cjs`、`test_entity_resolution.py`、`test_governance_delivery.py:139` | **已实现；本轮未验收。** 驳回不删来源；不把候选全部自动融合。 |
| 软删除/历史恢复/操作撤销 | `workbench.js:126`、共享详情历史；`api/parity.py:311–362`；`governance.py:309–329,355–373` 撤销检查 _operation_id 防覆盖后续修订 | `test_governance.py:36,78,87`、`test_source_retraction.py`、`test_entity_merges.py` | **已实现；本轮未验收。** 恢复增加新修订、审计保留。 |
| 后台任务/租约/取消重试与索引修复 | `workbench.js:130–255`、`storage.js`；`api/parity.py:149–225` jobs；`api/search_indexes.py`；`api/ontology_drafts.py:92–106` reconcile/repair/retry；`services/jobs.py`、`ontology_sync.py`、projection runtime | `test_durable_jobs.py`、`test_task_leases.py`、`test_ontology_sync_worker.py:18,29,44,84`、`test_projection_runtime.py` | **已实现；本轮未验收。** worker 完成、覆盖水位、物理索引状态分别判断；UI 本体索引 repair 仍主要是高级 API 路线。 |
| 知识备份/恢复/导出 | `workbench.js:448–452`；`api/parity.py:365–380`；`explorer.py:331–352` 先存恢复点，再写记录新修订；export 调 repository.export_projection | `test_explorer.py:211`、`test_current_storage_contract.py:51–108`、`test_ontology_draft_repository.py:351,374,480,627` | **已实现（各自契约）；本轮未验收。** UI 快照恢复只恢复记录新修订；不是本体发布、原始文件和派生索引的整项目回滚，不保证任意 JSON 恢复。 |
| 项目创建/切换/改名/删除 | `projects-view.js:87–219`；`api/projects.py:56–101`；删除 authoritative 数据后外部 cleaned 分开回执，失败保留 durable cleanup 事件（`:18–53`） | `workspace-redesign.test.cjs`、`test_api.py:48`、`test_project_management.py`、`test_project_directory.py`、`test_projection_rebuild_lifecycle.py` | **已实现；本轮未验收。** 创建 payload 显式 discovery、不自动加载默认本体；删除当前/最后项目须清上下文。 |
| 系统账号/角色/停启用/改口令与会话失效 | `user-admin.js:17,23–47,63–144`；`api/users.py:63–177`；`api/auth.py`；`security_middleware.py` admin/superadmin/viewer 及凭据指纹回查 | `test_auth_rbac.py:118–452`、`auth-disabled-permissions.test.cjs`、`actor-request-contract.test.cjs` | **已实现；本轮未验收。** 全局系统角色；有 superadmin/self 保护，不代表项目隔离。 |
| 项目成员 ACL | UI 明文“项目成员权限尚未提供”（`user-admin.js:17`）；项目列表 API 无 membership 参数/成员授权链；middleware 判系统角色 | 手册第 27 章明确独立阶段；现有 RBAC 测试不证明成员 ACL | **缺失（批准的后续阶段）。** 不能用系统 viewer/admin 角色宣称某成员只可访问特定项目。 |
| 抽取质量评测 | `workbench.js:456`；`api/parity.py:376`；`explorer.py:354–363` 对实体名称集合和关系三元组算 P/R/F1 | `test_explorer.py:211`、质量评测 UI 契约 | **已实现（精确匹配）；本轮未验收。** 不是语义等价评分；无独立属性抽取 P/R/F1 服务证据，不应把实体/关系分数扩展为全部抽取质量。 |
| 五变体真实执行消融、取消/重试/导出 | `retrieval-experiments.js`；`api/retrieval_experiments.py:57–79`；`retrieval_experiments.py:19,28–64,116–256,275` 固定 manifest、实际执行 omitted stages、owner/账号/水位校验 | `retrieval-experiments.test.cjs`、`test_retrieval_experiments.py:41,62,75,83,102,115,131` | **已实现；本轮未验收。** 支持 basic/document_only/no_graph/no_ontology/combined；评分仅证据 ID 精确 P/R 与答案 exact text match，缺标准或无模型为 null，不是业务语义质量已达标。 |

### 检索、问答与图谱（与独立专题审计交叉）

| 功能 | 当前证据 | 判定及限制 |
| --- | --- | --- |
| keyword / semantic / hybrid 与 Local/OAG | `api/knowledge.py:156–160`、`api/parity.py:383`；`services/retrieval.py`、`answers.py`、`answer_evidence.py`；`test_answer_prompt_graph.py`、`test_retrieval_channel_parity.py`、`test_hybrid_retrieval.py` | **已实现；本轮未验收。** 正常 QA 有图锚点/邻域/有向路径和来源上下文；模型替身不证明真实模型质量。 |
| Metadata/时间/本体归属范围与页面独立状态 | `workspace-context.js`、`workspace-context-ui.js`；初次读取的 `scope-fields.js:17–47`；`retrieval.py` 历史语义/混合明确拒绝 | **初次读取时部分实现且存在在线 Metadata 范围收缩。** 资料/台账/图谱的丰富 PostgreSQL 条件不能替代在线检索的 Metadata 能力。NOT 公共 AST 在旧 HEAD 与当前均不支持，属于尚未实现的新增能力，不是本轮退化。 |
| 局部图/脑图/路径 | `graph-neighborhood` API / `services/graph_neighborhood.py`，`explorer.py:296–329` 脑图预算与截断，`web/graph-paths.js` 加载图内 BFS；`local-graph-neighborhood.test.cjs`、`graph-paths.test.cjs` | **已实现（有界局部和加载图内路径）；本轮未验收。** 无服务端全库寻路的实现证据，不得把局部未找到显示为全库无路径。 |
| 15 万同图完整绘制与全库检索 | `api/graph_sessions.py`、`services/graph_sessions.py`、`web/full-graph.js` / worker / view；`test_graph_sessions.py`、`test_graph_progressive_session.py`、`full-graph-renderer.test.cjs`；手册第 27 章分期测量 | **部分达到；本轮未验收。** 计数/端点/绘制与共享详情已有历史证据；完整浏览器加载 ≤30s、当前 150k 向量库与完整规格仍不能标通过。 |
| 社区报告、Global / DRIFT | 当前 API 未注册社区生成/报告/全局/DRIFT 执行；实验变体也只控制已有 Local/OAG 阶段 | **缺失（批准的后续阶段）。** 三种检索策略、Local/OAG、消融五变体都不能代称社区 GraphRAG。 |

## 必须保留的真实服务不变量

- PostgreSQL 修订、事实键、断言映射和审计是成功依据；GPU 数据更新、Milvus 成功或任务状态不能替代正式写事务。
- 来源固定版本、candidate/assertion 指纹、草案预期修订与真实 publication ID 必须一起保留；不能以“只读快照可复用”为由跳过发布/提交时的重新核验。
- 正式实体/关系/属性的独立身份、历史及来源不能合并进一个仅供展示的实体 properties 表单；多来源关系/属性撤回最后支撑的逻辑不可丢。
- 融合必须保留预演身份、全部参与属性及受影响关系的并发保护、显式 winner 与可撤销审计；迁移必须仍为显式分组、新修订和可撤销操作。
- 页切换/项目切换/权限变化的陈旧响应和结果未知后的幂等重试身份不能因请求合并或缓存而丢失。

## 当前审计不能证明的内容

本轮没有对任何功能作新运行通过声明：外部模型真实抽取/OCR/父类建议/QA 语义质量、权限故障矩阵、全部浏览器写入路径、真实索引修复、当前容量项目向量覆盖及 P95、物理索引全部完成均未验收。旧报告中的 247/247 或回归总数不应用作当前未提交工作通过的证明。后续若以“全功能保留且效率提升”为交付结论，应先把在线 Metadata 的范围差距与当前容量目标逐项验收，不能只验证按钮可见和普通项目暖态延迟。NOT 是否新增应另立需求，不计入本轮退化数量。
