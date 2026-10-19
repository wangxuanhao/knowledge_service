# 现有能力保留审计（2026-10-16）

本审计用于修订产品设计和原型：实体消歧/融合、检索消融对比实验是用户已同时确认的需求。设计扩展前应保留当前真实存在的知识维护、本体编辑与版本治理能力；不能把已有能力全部写成“待建设”，也不能用一个后端类名代表已完成的用户流程。

## 范围与证据口径

- 审计工作区：`D:/workspace/knowledge_service_v2/knowledge_service`；读取时 HEAD 为 `6637804`。结论针对当前工作区源码，包含未提交文件可能已有的实现，不代表该提交或运行中的 8100 服务已具有同样行为。
- 已读取仓库 `AGENTS.md`。优先尝试 codebase-memory：`list_projects` 15 秒、直接 `search_graph(project="D-workspace-knowledge_service_v2-knowledge_service")` 12 秒均未返回；按“工具结果不足可回退”规则使用限定目录源码搜索。未读取 `.env`，未执行数据库查询、PostgreSQL pytest、服务重启或业务写入。
- **已有**：可定位到明确实现和入口；**部分**：底层支持、用户入口或关键契约尚有缺口；**缺失**：在相关 API、服务和当前加载前端中未找到实现。本审计是静态证据核对，没有将源码推断表述为运行验证通过。
- 以下路径均相对仓库根；`文件:行号 / 符号`为复核入口，行号按本次读取的工作区计算。

## 一、实例知识维护与实体治理

| 能力 | 状态 | 后端入口与实现 | 当前前端入口 | 语义、限制与设计应保留内容 |
|---|---|---|---|---|
| 实体/关系/属性创建 | 后端已有；表单入口部分 | `api/knowledge.py:29`，`POST /api/projects/{p}/records`；`services/service.py:181 / write`；`models.py / RecordWrite` | `web/index.html:38` 结构化 JSON 导入，支持 document/entity/relation/attribute/chunk；`web/evidence-inspector.js:50–77` 从实体详情“新增关系” | 已有批量原子写入与本体校验。独立实体、属性的新增表单未在当前主入口中发现，主要由抽取、审核或 JSON 导入创建；“已有 CRUD”不能自动解读为每种都有完整可视化新增表单。 |
| 实体编辑 | 已有 | `PUT /api/projects/{p}/records/{id}`，`api/knowledge.py:50` | `web/record-dialog.js:106` 结构化实体表单；图谱详情 `web/evidence-inspector.js:128–150`；台账 `web/records-view.js:299` | 编辑姓名、实体类型、业务有效期等，保存产生新知识修订。可用类型来自该记录**绑定的本体**，并保留原始记录字段及 `expected_version`。 |
| 关系编辑 | 已有 | 与实体共用修订 API；`services/ontology.py:411 / validate` 校验端点与 domain/range | `web/record-dialog.js:108–110,149` | 可改关系类型、起点、终点、显示文本、有效期；这是实例关系维护，不是修改本体关系定义。应继续从边详情和台账进入。 |
| 正式属性编辑 | 已有 | `RecordWrite` 要求 subject_id/type/value/datatype；`services/ontology.py:430–453` 校验主体、domain 与原生值类型 | 台账属性页 `web/records-view.js:26–37`；`web/record-dialog.js:111–149`；`web/record-attribute-editor.js` | 正式属性是独立记录，不应降级为任意 metadata。支持原生字符串/布尔/整数/浮点；日期/JSON 在表单中按字符串处理。主体选项取当前范围且最多 1000 条，未加载的原主体会保留。 |
| 删除与级联 | 已有 | `POST /delete`，`api/parity.py:318`；`services/governance.py:255 / delete`；来源撤回规则在 `services/formal_writes.py:426 / _plan_source_retraction` | 编辑弹窗软删除；`web/index.html:39`；台账操作 | 软删除产生修订并保留历史。实体删除处理关联关系和正式属性；来源撤回还应看 assertions，不能仅凭顶层 source_id 判断来源支持。设计需要影响预览、软删除状态、历史入口。 |
| 双时态查询、修订历史 | 已有 | `GET /records/{id}?valid_at=&known_at=`，`GET /records/{id}/history`，`api/knowledge.py:34,69`；`services/service.py:375 / scoped` | `web/record-dialog.js:164–246` 修订时间线；统一范围控件 | 知识修订 rN 与本体 vN 是两条独立轴。每个修订保留 `ontology_id`；普通编辑省略归属时保留原绑定，不自动迁到当前本体（`api/knowledge.py:61–66`）。 |
| 恢复单条历史修订 | 已有 | `POST /restore`，`api/parity.py:321`；`services/governance.py:313 / restore` | 编辑/历史弹窗“恢复为新修订”，`web/index.html:39` | 以选中旧修订内容产生新修订并检查当前 expected_version，不覆盖历史。单条恢复与整次治理操作撤销是两个入口。 |
| 别名保存 | 已有 | `POST /aliases`，`services/governance.py:23 / alias` | 台账融合向导“新增别名/存别名”，`web/records-view.js:73–75`、`web/workbench.js:103` | 别名 1–500 字；casefold 检查其他有效实体名/别名占用，冲突要求显式解析或合并；写成实体新修订。 |
| 手工查重/消歧 | 已有 | `POST /resolve`，`services/governance.py:41 / resolve` | 台账“实体消歧与融合”，`web/workbench.js:101` | 先精确名/别名，多个精确命中返回 exact_duplicates；再 Semantica DuplicateDetector 和项目 embedding 候选，按分数最多返回 12 条。输出是候选建议，不应把相似度直接等同“同一实体”。 |
| 重复组与计数 | 已有 | `GET /duplicate-groups`，`services/governance.py:280 / duplicate_groups` | 台账“组疑似重复”统计、点组填入合并选择，`web/records-view.js:135,240–252` | 只统计名称规范空白后忽略大小写且**稳定类型相同**的多实体组；排除软删除，无模糊聚类。计数不按有效期、适用范围、本体归属分组，所以疑似组不等于可直接合并组。 |
| 摄取过程实体对齐/自动融合 | 已有 | `services/entity_resolution.py:25 / resolve`；`services/reconciliation.py:8 / reconcile`；`services/service.py:1120` | 知识写入高级设置 `web/index.html:36`：同名/别名消歧、语义自动合并、阈值 | Resolver 对精确唯一命中直接 aligned；有存储向量时按 merge/review 两阈值分为高分对齐、灰区 review、低分独立。注意 reconcile 的前置精确匹配用 `next()` 取第一个 eligible 同名/别名候选，未将多精确候选强制转审核（`:23–25`）；不能宣传为所有歧义都需人审。默认 resolve_entities=true、auto_merge=false、review=.72、merge=.88（`models.py / IngestSettings`）；auto_merge 控制语义高分档，不关闭精确对齐。候选资格要求类型、业务起止区间、适用范围相同；没有 `ontology_id` 同版本硬条件。 |
| 人工融合 | 已有 | `POST /merge`，`api/parity.py:313`；`services/governance.py:148 / merge` | 三步合并向导、保留/合并实体卡、确认勾选、局部影响面，`web/records-view.js:65–131` | 要求两个有效实体、准确双实体 expected_versions、相同稳定类型 IRI、相同 applicability、相同业务时间区间。允许兼容的跨本体发布记录融合；保留本体版本轨迹。保留方 properties 优先，收集别名与 merged_sources，停用另一方，重写关联关系和属性主体；来源断言迁移走 formal ledger。 |
| 融合后的来源、撤销与并发保护 | 已有 | `services/governance.py:76 / _commit`、`:301 / undo_merge`；`services/formal_writes.py:524 / _plan_ledger`；`POST /operations/{id}/undo` | 台账可撤销操作、单条操作“撤销”，`web/records-view.js:449–470` | 操作记录保存 before_state/重定向/断言移动；撤销是整批生成新修订。受影响记录的 `_operation_id` 已改变时拒绝撤销，防止覆盖后续编辑。也用于软删除、重分类；不能宣传为任意时间无条件一键回滚。 |
| 融合的单值属性冲突选择 | **部分：后端有，UI 缺** | `api/parity.py:44 / Merge.attribute_winners`；`services/governance.py:105 / _attribute_conflicts`、`:171–249` | 当前 `web/workbench.js:104` 合并请求没有 attribute_winners；全当前前端未发现胜出值选择控件 | 对声明 `sh:maxCount 1`、时间重叠且值不同的属性分组，要求每组恰好选择一个已有属性记录 ID；不允许无关/重复 winner。败者软删除、断言记 contradicting→superseded。需要新增“值/证据/有效期/保留记录”决策 UI，并把后端拒绝细节转为可读对比。不是通用自动冲突裁决器。 |
| 灰区消歧审核队列 | **部分：后端有，UI 缺** | `GET/POST /resolution-reviews`，`api/knowledge.py:95–117`；决策 merged/separate/rejected，强制原因/版本；`GET /merge-operations` | 当前加载的 web 模块未找到 `/resolution-reviews` 调用 | 摄取产生的灰区审核不能只靠手工查重替代。现有 TaskReview 是事实/约束异常审核（`web/task-review.js:19`），不是这个消歧队列。新设计应显式列出“候选两实体、相似依据、来源、合并/保持独立/拒绝、操作历史”。还需补 DTO 桥接：`ResolutionReviewDecision` 没有 attribute_winners，`:111` 审核合并调用也不传 winner，遇单值属性冲突时该路径无选值参数。 |

融合文案复核：`index.html:39` 的静态旧文案写“同本体版本”，但加载的 `web/menu-hierarchy.js:56–60` 会替换为“稳定类型 IRI 与业务区间兼容时可跨本体版本融合”。应按当前服务校验与动态最终文案保留能力，不能据 HTML 初始文本误判跨版本融合缺失。

## 二、本体建模、命令与版本治理

| 能力 | 状态 | 后端入口与实现 | 当前前端入口 | 语义、限制与设计应保留内容 |
|---|---|---|---|---|
| 读取/保存本体源码 | 已有；旧 UI 契约待核对 | `GET /ontology?ontology_id=`、`GET /ontologies`、`POST /ontologies`，`api/knowledge.py:165–218`；`services/ontology.py:160` 固定 Turtle 解析 | `index.html:40` 留有 `tab-ontology`；`web/workspace.js:674–680` 高级历史/Turtle 区；`web/app.js:101–105` | 首次空本体兼容 bootstrap；之后旧保存路由转换为受治理 Turtle 草案，而非直接覆盖已发布版本。当前侧栏只保留 ontology-model（`menu-hierarchy.js:10`）；新画布未见明确源码入口，旧 app.js 保存不带 expected_ontology_id 且仍提示直接生效，不能把遗留控件存在当作完整可用链路。 |
| 任意本体格式导入/外部 imports 闭包 | **缺失完整产品链路** | 当前公共本体写 DTO `OntologyWrite` 只有 turtle；`Ontology.__init__` 固定 `format="turtle"`；pySHACL `do_owl_imports=False`（`services/ontology.py:461`） | 未发现 RDF/XML、JSON-LD、多文件 imports 包、URL 导入闭包的当前主入口 | 可通过 Turtle 输入已有 RDF，但不可声称任意 OWL 文件、远程依赖、本体对齐和冲突合并均已支持。Semantica 上游解析器是否丰富是另一问题，不等于本项目已接入这些产品入口。 |
| 类、对象关系、数据属性新建 | 已有 | `POST /ontology/terms`，`api/workspace.py:153`；适配为草案命令 | `web/ontology-model-panel.js:285 / newTermSection`：新建术语，选择 class/relation/attribute；新建类画布入口 | 稳定 IRI 由后端生成；名称、中文名、类归属/父类在表单输入；写草案后审核发布，不直接改生效版本。属性是 DatatypeProperty；关系是 ObjectProperty。 |
| 标签、中文名、定义说明编辑 | 已有 | `PUT /ontology/term`，`api/workspace.py:215`；命令 add/remove_annotation（`services/ontology_drafts_commands.py:48`） | `ontology-model-panel.js` 类/关系/属性详情编辑 | 与稳定 IRI 分开，改名称不应重造术语身份。纯 annotation 发布可新增不可变发布记录但沿用结构版本号；不能用列表行数充当 vN。 |
| 父子类、多继承、domain/range、datatype | 已有 | `services/ontology_operations.py:569–622` add/remove_parent、add/remove_domain/range、set_datatype | `web/ontology-model-panel.js:591–658` 当前值移除/下拉新增；`ontology-model-canvas.js` 两步连关系、改父类 | 后端防重复父边、继承环、失效引用；对象关系多 domain/range 使用项目支持的 union 表达；DatatypeProperty 的 range 用 set_datatype，不能按对象类处理。前端保留图谱/层级树、左右列表与详情联动。 |
| 基数及 SHACL 约束编辑 | **部分：校验/底层命令有，专用表单缺** | `services/ontology.py:411–486` pySHACL 与时间轴分段校验；`ontology_operations.py:439 / _apply_scoped_shacl_patch`、`:699` advanced_rdf_patch；`:1255` Turtle 差异编译 shape 子图 | 当前本体主画布未发现 minCount/maxCount 等专用控件；事实异常详情可读 SHACL 限制（`web/task-review.js:67`） | SHACL patch 必须限于目标 shape 子图，不能任意替换全 Turtle；采用 inference=rdfs、advanced=False、do_owl_imports=False。SHACL min/maxCount 不应称为已实现完整 OWL cardinality/推理器。 |
| OWL 复杂表达与逻辑推理 | **缺失完整能力；有受限子集** | `ontology_operations.py:1013 / _validate_supported_bnodes` 拒绝 owl:Restriction 和其他未支持复杂匿名类；支持集合见 `ontology_shape.py` | 无完整推理配置入口 | 类祖先闭包和简单 union 支持不等于 OWL DL、HermiT/Pellet、一致性/可满足性或等价类推理。当前约束支持边界应在高级编辑入口明确表达。 |
| 统一草案、命令账本、撤回/调整 | 已有 | `/ontology-drafts` 创建/列表/详情；`POST /{draft}/commands`；`ontology_drafts_commands.py:307`，支持 withdraw_operation/supersedes_operation_id；`:190` Turtle 差异编译 | `web/ontology-model.js` 自动建草案/刷新 overlay；`ontology-model-panel.js` 底栏/审核对话框 | 命令为可审核的结构化操作，风险/指纹由服务端重算；修改使原校验/提交快照失效。请求当前为 pending/accepted/rejected 三态（`ontology_drafts_core.py:20–35`），不是把编辑/已提交/基线陈旧各做独立生命周期状态。 |
| 校验、影响面与质量门禁 | 已有 | `/validate`、`/stage-availability`、`/publish-readiness`、`/review-plan`；`ontology_drafts.py:820 / _compute_validation`；`GET /ontology/term-impact` | 本体底栏校验并审核；详情停用前影响面；发布清单 | 校验包含结构不变量、Semantica 质量引擎、新写入契约、历史影响；引擎不可用/严重错误阻断。历史记录不符合新结构单列 impact/info，不等于自动迁移或全部阻止发布。指纹冻结本体基线、来源与操作快照。 |
| 审核、批量安全批准、人工高风险处理 | 已有 | `/submit`、`/decisions`、`/decisions/batch-approve`、`/review-plan`；`api/ontology_drafts.py:136–185` | `ontology-model-panel.js:1160–1285` 逐条变更前后、理由、收下/不收；一键只收安全集 | 安全/人工/阻断由后端裁定，前端不能复写策略。停用、恢复、advanced_rdf_patch 不可通过批量安全批准绕过人工审核（`ontology_drafts_core.py:18`）。 |
| 发布、不可变版本、索引一致性 | 已有 | `/publish` 要求 expected_revision、expected_ontology_id、validation_fingerprint、告警确认、idempotency_key、发布 note；`ontology_drafts_review.py:691`；`/ontology-index`、repair/retry | `ontology-model-panel.js:1300–1355` 发布说明、告警、发布；版本列表 | 发布原子形成不可变记录，身份绑定登录用户。索引异步投影有对账/修复，不应把“发布后本体可读”夸大为所有 Milvus/Neo4j 投影立即强一致。发布本体不自动改写旧实例绑定。 |
| 基线变更、来源变更后重对齐 | 已有 | `/rebase`，`ontology_drafts.py:1250 / rebase`；来源/基线陈旧由 needs_rebase 推导 | `ontology-model-panel.js:1037–1138` 重新基线/刷新来源快照 | 保留变更，对冲突标记并重新校验审核；不是丢弃全部草案后让用户重做。应保留 stale source/base 的可行动提示。 |
| 术语停用 | 已有 | retire_term；依赖分析 `ontology_operations.py:787 / retirement_dependencies` | 详情“停用这个术语”，`ontology-model-panel.js:477` | 走影响面、原因、命令、审核发布；新写入排除停用术语，历史知识与术语轨迹保留。不是物理删除术语。 |
| 术语从历史定义恢复 | **部分：后端有，UI 契约错配** | `ontology_operations.py:908 / build_restore_operation`；要求 source_ontology_id 和非空 selected_fields（annotations/parents/domain/range/datatype），冻结 template；当前术语须已停用 | `ontology-model-panel.js:534` 有恢复按钮，但仅发 action/target_iri/reason | 全当前 web 未发现 source_ontology_id/selected_fields；命令编译器 `ontology_drafts_commands.py:220` 会调用严格 builder，静态可推断此按钮缺参数。设计必须补来源版本、恢复字段、恢复后影响预览、逐项审核；未做实际运行复现。 |
| 历史版本查看与回到旧版 | 已有 | `GET /ontologies` 返回版本/同号复用/发布时间/发布人/说明；`POST /ontology-drafts` source_kind=revert | `ontology-model-panel.js:1396–1599` 版本抽屉、“查看这一版（只读）”“回到这一版”；`ontology-model.js:638 / forkFrom` | 查看只改画布；回到旧版是以历史发布为基线开新草案，再审核发布新版本。不是切一个“当前指针”、删除后续版本或覆盖历史；需要显式确认 revert_drops_later_versions。 |
| 本体差异/任意两版对比 | **部分** | Turtle 编辑可编译成原子差异（`ontology_operations.py:1123 / canonical_turtle_diff`）；草案有操作 before/after；发现草案也有 diff | `ontology-model-panel.js:1200` 查看变更前后；`ontology-details.js:138` 有草案术语增删保留差异展示器 | 已有“草案相对基线的可审核差异”，但未发现当前主入口的任意 A/B 两个已发布版本结构对比、字段级并排对比、变化影响汇总的完整 API/UI。不能把二者都判为完全没有，也不能用草案 diff 代替用户要的历史双版本 compare。 |
| 版本关联知识/归属过滤 | 已有 | `GET /ontologies/{id}/knowledge-context`，`services/version_context.py`；`services/service.py:392–409` ontology_scope/ontology_ids | 版本管理“关联知识”与跳转台账 `ontology-model-panel.js:1467–1515`；`web/record-version-context.js`，台账/图谱/脑图统一上下文 | 归属筛选表示所选时态范围内绑定该发布的知识，不等于历史整库快照。结构同号发布仍需具体 ontology_id 区分。 |
| 受控重分类/迁移 | 已有 | `GET /reclassify`、`POST /reclassify/preview`、`POST /reclassify`，`api/parity.py:339–366`；`services/reclassify.py:130,185,208` | `web/ontology-model-reclassify.js` 选组、预演、确认迁移，台账有引导 | 默认只读不跑；按旧本体+kind+type 分组，旧类型仍可用则 carry，有明确替代映射则 rename，未映射阻止。预演与写入共享时间轴/domain-range/SHACL 校验；每条产新修订，整批一条操作，可走统一 undo。 |
| 项目快照 | 已有；不是本体回退 | `services/explorer.py:339–360`；`/snapshots` 创建/列表/restore | 项目与运行→快照与评测，`index.html:48–55` | 快照保存当前记录及本体列表；恢复函数实际重建**记录修订**、软删快照之外记录并先自动备份，没有重新发布/切换保存的本体列表。不要把“快照内有 ontologies”宣传为恢复整个本体治理状态。 |

## 三、当前检索、OAG/GraphRAG 与消融实验

| 能力 | 状态 | 源码与入口 | 准确含义及边界 |
|---|---|---|---|
| keyword / semantic / hybrid | 已有 | `models.py / Search`；`services/retrieval.py:283 / search`；`POST /search`；`index.html:26–27` 检索/问答模式 | 关键词、向量及 RRF 融合已有。可按实体/关系/原文通道配额，查询范围先于排名；返回 requested/active mode、后端与降级错误。语义失败可降级关键词，实验必须保存实际模式，不能只按请求标签比较。 |
| 本体感知扩召回（OAG 的已落地部分） | 已有但范围有限 | `services/service.py:509 / ontology_expansion`、`:533 / search`；`services/ontology.py:262 / expansion_index`、`:107 / match_query_expansion`；`retrieval.py:301,426,513`；UI “本体扩展” | 显式 bool 开关，默认关；只读当前最新本体，以类名/标签/别名匹配，再按父子闭包找子类，增加词法扩展和类型召回通道；不放宽权限、时态和适用范围。属于可解释本体查询扩展，不是完整 OWL 推理，也不是社区 GraphRAG。 |
| 图谱浏览、邻域、脑图、当前图最短路径 | 已有 | `/subgraph`、`/mindmap`、`/explore`；`services/explorer.py:168,321,373`；`web/graph-paths.js`、`graph-workbench.js` | 可视化邻域与端点完整性过滤已有；路径工具在当前已返回图内 BFS，不能声称全库寻路。交互拖动只改布局，不能让用户误以为拖节点会修改事实/继承。 |
| 图邻域增强 QA | **部分/当前正常路径受限** | `services/answers.py:266 / _retrieved_question_context`；`:306–359` groups 分支；`:361–415` 后备邻域分支；`services/service.py:598–623,678` 构造 platform_groups | 源码有 hops≤3、200 条边的邻域/路径代码，但当前 service.search 会构造 platform_groups（空结果也有兜底组），QA 的 groups 分支提前返回 `reasoning_paths=[]`、`graph_evidence=0`。因此不能把旧/后备分支存在描述为当前正常问答完整使用图推理链；需要单独实现并验收分组内图证据扩展。此处是控制流静态判断。 |
| 社区式 GraphRAG | **缺失** | 检查 models.Search/Question、api、services、当前 web；仅存在上述三模式、本体扩展和局部邻域 | 未找到社区发现任务/持久化、层级社区、社区摘要、global map-reduce/local community retrieval 或配置入口。Neo4j 同步或能画图不等于 GraphRAG community 已落地。若设计要提供社区搜索，必须列为真实新增后端能力。 |
| 当前标准答案评测 | 已有 | `api/parity.py:64 / Evaluate`、`:383`；`services/explorer.py:362 / evaluate`；`web/workbench.js:429`；`index.html:58–65` | 将当前范围图实体**名称集合**、关系**名称三元组（谓词短名）集合**与输入 gold 精确比较，输出 precision/recall/F1、missing/extra。会折叠重名实体和重复三元组；不评估实体身份消歧准确率、属性正确率、检索相关性和答案事实性。现有入口应继续保留。 |
| 检索消融比较实验 | **缺失完整能力；已有可复用开关** | 当前 Evaluate 没有 query/questions/runs/variants 配置；未发现 experiment/ablation 的服务/API/持久化/前端 | 可用现有三模式、ontology_expansion、hops 等做新实验的组件，但尚无固定问题集/gold evidence、相同数据/本体快照、变体编排、真实请求执行、run/result 持久化、Recall@K/MRR/nDCG、延迟/成本、逐题差异及报告。不能把“抽取 PRF”页改名成消融实验后视为完成。 |

建议新设计将“实体治理”和“检索消融实验”各自成完整任务流：前者承接现有真实接口并补齐冲突/灰区审核 UI；后者新增实验定义与运行模型，保留请求模式、实际模式/降级、数据与本体版本、问题集版本、embedding/ranker 版本、范围、参数和结果。GraphRAG 社区变体在实现前应明确不可用，不能生成看似真实的比较结果。

## 四、新设计/原型至少要显式保留的入口

1. 实体、关系、正式属性的列表与详情，结构化编辑、新关系、原文证据、有效期、绑定本体、知识修订时间线、恢复、软删除与影响面。
2. 实体治理：疑似重复组→查重候选→保留/合并实体→别名与来源→属性冲突保留值→确认融合→操作审计/撤销；另有摄取灰区消歧待审核队列。
3. 本体编辑：新建类/对象关系/数据属性、名称说明、多父类、domain/range/datatype、停用及依赖影响；高级 SHACL/源码入口与支持边界。
4. 本体治理：当前发布/草案 overlay/历史只读三种上下文、命令差异、校验、质量/历史影响、逐项审核、安全批量审核、发布说明、基线/来源刷新、索引状态。
5. 版本管理：发布人/时间/说明、同号标注修订与实际发布 ID、历史查看、按历史基线新建回退草案、版本关联知识与跳转台账、受控重分类预演/执行/撤销；明确与项目快照的差别。
6. 实验评测：保留已有实体/关系精确 PRF，新增检索消融实验定义、运行状态、实际执行配置、结果比较与逐题证据；“实体消歧”和“检索消融”均进入范围。

## 五、真正需要补齐的实现，按性质区分

| 性质 | 待补齐项 | 为什么不是单纯重画 UI |
|---|---|---|
| UI/契约补齐 | maxCount 1 融合冲突 winner 选择；resolution-reviews 灰区决策及 winner 请求桥接；术语恢复的来源版本和字段选择；源码编辑可发现性与旧保存契约 | 主体服务已有，但当前入口缺失或请求不满足服务要求。 |
| 读模型与 UI 新增 | 任意两个已发布本体的细粒度结构对比和实例影响汇总 | 已有草案差异可复用，但没有完整双版本 compare 产品链路。 |
| 后端主能力新增 | 可复现实验/数据集/变体/运行/结果模型，检索指标、逐题记录、报告 | 现有 evaluate 只对当前图做集合精确比较。 |
| 后端能力新增 | 社区构建、分层摘要与社区检索（如果纳入 GraphRAG 变体） | 当前没有社区 GraphRAG 执行链；图数据库投影不能替代它。 |
| 后端控制流补齐 | 当前分组 QA 的图邻域证据和路径 | 旧邻域函数存在，但正常 groups 分支直接返回零图证据。 |
| 独立规划 | 多格式/依赖闭包本体导入、复杂 OWL 推理、跨本体概念对齐 | 当前是 Turtle 与受限表达/约束治理，不应把上游模块可用当作系统已集成。 |

## 文档与当前源码的差异

- `docs/2026-10-12-ontology-capability-gap.md` 可作历史背景，不能把其中运行通过记录当作本审计验证；其“全量评测仍缺业务样本”“无完整 OWL 推理/本体对齐”的边界与当前源码一致。
- `docs/superpowers/specs/2026-10-15-graph-maintenance-interaction-design.md` 说明实体/关系结构化维护既有、属性编辑/交互/当前图路径工作；当前源码已包含相应模块。设计修订不能再次省略这些现成能力。
- `docs/superpowers/specs/2026-10-08-ontology-knowledge-version-context-design.md` 和 `2026-10-14-record-ontology-version-consistency-design.md` 保留了两条版本轴、普通编辑不迁移、按绑定本体解释的约束。当前知识历史 UI 已有时间线，不能沿用早期“还没有时间线”的现状描述。
- 早期本体多状态流程已收敛为 pending/accepted/rejected；编辑/提交/来源陈旧/审核情况通过快照、指纹、决定与派生状态表示。旧页面残留“保存直接生成生效新版本”的文案不代表当前服务可绕过审核发布。

本文件只记录审计结论，没有修改产品代码、主规格、原型、数据或 Git 暂存区；未执行运行验收。
