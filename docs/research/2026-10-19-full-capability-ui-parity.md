# 完整能力、入口与验收清单

## 当前交付逐项台账（2026-10-19）

本节记录当前隔离工作区的产品接线与已有证据；下方“原始规划审计”原样保留历史判断，不能用其“本地可体验”“草图待补”等标记代表当前产品状态。操作步骤见[新手册](../2026-10-20-知识平台完整操作手册.md)，最终运行结果统一以[改造验收记录](2026-10-19-redesign-acceptance.md)为准。

本台账的证据口径：

- **源码已接通**表示已核对真实 UI 模块、API 与服务守卫，不等于本轮端到端验收通过。表中 `P` 代表 `/api/projects/{p}`，所有项目 ID、发布 ID、修订号均使用实际服务身份。
- **Node 契约**指表中引用的 `.test.cjs` 前端契约测试属于协调者最新重跑的前端套件，结果为 **247/247**；它们验证前端行为或契约，不能替代数据库、真实模型、权限矩阵或产品容量测试。表中的 Python 测试链接仅表明已有可复核用例，最终执行状态仍按该行和验收记录判断。
- **真实应用小规模证据**见[运行记录](2026-10-19-live-ui-validation.json)：启用认证、隔离 PostgreSQL/Milvus，8 个页面可打开、正式实体创建回执 201，未记录页面脚本错误。协调者另已完成“浏览器上传资料 → documents 批次完成”；这不证明开放/受控抽取、OCR、融合、发布等所有写入流程均已端到端通过。
- **真实工作流证据**见[单独运行记录](2026-10-19-live-workflows-validation.json)：关闭 Metadata 不扫描、展开才请求且可再次关闭；关系/属性真实创建与共享编辑准确追加一版且旧历史 ID 不变；显式索引重建及一次租约重试完成；keyword/semantic/hybrid 均返回请求模式与真实 Milvus 后端；五变体真实执行并导出五个结果。这不表示所有外部模型质量或故障矩阵已通过。
- **最终完整回归**已完成，逐项状态按下表区分服务/浏览器回归与真实外部运行；计数、跳过项和既有预期失败见[最终记录](2026-10-19-final-regression-validation.json)。未单独评测的模型质量不因回归通过而补写成功。
- **后端容量已取得独立通过证据**：[后端报告](2026-10-19-backend-capacity.json)的 `accepted=true` 限定于该脚本的后端指标：150k 实体 / 300k 关系完整枚举 **20.1887s**；Milvus 逻辑总数及 active/模型身份数均核验为 **450,000**，分通道配额核对通过。暖态引擎检索 P95 为 semantic **0.2708s**、hybrid **0.2778s**，均**不含查询编码与 HTTP**；高级 Metadata 筛选为 **8.622s**，单独报告。该次快照的 **17 个索引仍为 InProgress**，不能将逻辑覆盖核验或检索成功描述为所有物理索引均已构建完成。
- **真实 HTTP 检索证据**：[产品长测](2026-10-19-product-capacity-validation.json)中的暖态检索包含查询编码，semantic P95 **1.031s**、hybrid P95 **1.484s**；另一次无 pytest 并行的[重复测量](2026-10-19-product-capacity-isolated-loads.json)为 **0.797s / 0.704s**，均使用 Milvus。首请求与并发样本单列；不把暖态分位数代表冷编码、原文质量、全部高级范围或故障资格回归。
- **真实产品大图完整但时限未达**：HTTP → PostgreSQL → Worker → WebGL 的 150k/300k 计数、唯一 ID、端点与实际绘制正确。首轮 **127.89s** 是历史诊断；[审计锁阻塞](2026-10-19-product-graph-latency-diagnosis.json)已修复，单次探针降为 17.781s，但最终五轮完整加载 P95 为 **46.906s**（共享宿主回归负载）及 **48.187s**（无 pytest 并行），**30s 目标仍未通过**，不能以较快单轮替代分布。
- **真实产品长稳与绘图层证据分别保留**：真实产品经过 **1871s / 72 次交互采样**，切换、内存、节点共享详情、正式修订、旧清单 409 与重载 150001 实体均已通过；绘图层的 1k/10k/50k/150k、300k/1m 关系及 30 分钟记录另列。单条大库修订隔离对照 **144.093 → 125.454s**，仍然偏慢。完整容量规格覆盖不作通过结论。

| 能力（对应原始清单） | 既有入口 → 当前产品入口 | API / 状态与权限守卫 | 行为证据与当前验收状态 |
| --- | --- | --- | --- |
| 仅文档处理 | 知识写入的 documents 模式 → 资料库“添加文件”中的“仅文档处理” | `POST P/ingest-batches` 与 files/items；提交 `ontology_mode=documents`，不要求已发布本体，不执行实体关系抽取；写入需管理员 | [上传模式契约](../../tests/service/ingest-ontology.test.cjs)、[输入契约](../../tests/service/ingest-input.test.cjs)；真实浏览器 documents 批次已完成；真实 documents 上传完成；来源、检索与冻结证据的最终回归通过。 |
| 非开放抽取 | 原 ontology 上传 → 添加文件“按已有本体抽取” | 批次固定展示的 `ontology_id`；读取本体失败或无发布时阻止提交；不静默改 discovery/documents；不限制后续建模 | [上传本体绑定契约](../../tests/service/ingest-ontology.test.cjs)、[批次契约](../../tests/service/ingest_batches_ui.test.cjs)；受控抽取、本体绑定、关系约束及失败分支的服务/前端回归通过；外部抽取模型的业务质量未单独评测。 |
| 开放抽取 | 原 discovery 上传 → 添加文件“开放抽取”及任务候选结果 | 批次明确 discovery；候选经发现运行、草案与正式事实治理；已有发布仍可作为当前基线 | 上传本体绑定契约；[发现运行测试](../../tests/service/test_ontology_discovery.py)；发现运行、候选、草案与发布回归通过；模型替身验证流程，不据此宣称真实抽取质量。 |
| 已有本体继续发现 | 本体发现页/任务结果 → 本体目录“继续发现项目候选”、资料详情“继续本体建模”、任务结果后续导航 | `POST P/ontology-discovery/drafts`、`/runs/{id}/finalize`；历史预览禁止写入；请求固定项目/草案状态；不覆盖另一个待处理草案。资料按钮仅转项目候选流程，未提供精确单份来源修订 manifest | [本体刷新契约](../../tests/service/ontology-model-refresh.test.cjs)、[结果导航契约](../../tests/service/ingest_results_ui.test.cjs)；继续发现、来源变化及草案/基线冲突回归通过；精确来源修订 manifest 仍属独立阶段。 |
| 发现父类 | 原画布“发现父类” → 本体建模工具栏“发现父类” | `POST P/ontology-drafts/{id}/discover-parents`；至少两个有效类，预期草案修订与发布基线；重复、继承环和请求后变更由服务复核 | 本体刷新契约覆盖无新候选时重复发现、请求互斥与项目切换；可达入口、修订/基线及重复/继承环服务守卫回归通过；真实模型建议质量未单独评测。 |
| 手工新建类/上级类 | 原新建类表单 → 目录“新建类”、画布插入及空态检查器 | `POST P/ontology/terms`；IRI 由服务治理；已有发布先准备草案；历史预览禁写 | [目录契约](../../tests/service/ontology-catalog-delivery.test.cjs)、[画布契约](../../tests/service/ontology-canvas-interaction.test.cjs)；空项目三类术语写入、逐条审核发布及旧基线/修订拒绝的真实 PG 流程与最终完整回归均通过。 |
| 手工新增子类 | 新建类加父类 → 类型卡片“＋子类”、插入/检查器的父类字段 | 同术语 API，预填父类可修改；草案命令保留多父类，服务校验身份与继承环 | 目录契约检查父类与独立术语；画布契约；父类预填、多继承写入及继承环校验的前端/服务回归通过。 |
| 改父类连线/移除父类 | 原两步连线和父类字段 → 画布两步确认、检查器增删父类 | 草案 `/commands` 的 `add_parent` / `remove_parent`；修订/基线守卫，自引用、重复及循环由服务阻断 | 画布契约覆盖两步操作、拖动与连线隔离、项目重挂载；[本体操作测试](../../tests/service/test_ontology_operations.py)；两步连线、删除父类及自引用/循环/并发守卫回归通过。 |
| 新建业务关系/连关系 | 原术语 API/两步画布 → 目录“新建关系”、卡片“＋关系”、两步连线 | `POST P/ontology/terms`；domain/range 与继承分别表达，先草案后治理，不直接覆盖发布 | 画布契约覆盖从发布准备草案后再连关系；目录契约；发布版本准备草案、关系术语创建与审核发布回归通过。 |
| 新增数据属性 | 原属性术语命令 → 目录“新建属性”、卡片“＋属性”、检查器 | 术语 API 与草案 `/commands`；所属类、datatype、约束由服务校验；历史预览禁写 | 目录契约；[本体操作测试](../../tests/service/test_ontology_operations.py)；属性术语、datatype、所属类与草案审核的服务/前端回归通过。 |
| 名称/说明/稳定 IRI | 原术语编辑 → 目录选中对象后的定义检查器 | 草案注解命令；显示标签与稳定 IRI 分开；改标签不重建身份，预期修订/基线仍检查 | 目录契约和[历史只读/恢复契约](../../tests/service/ontology-governance-delivery.test.cjs)；注解修改、稳定 IRI、发布身份及历史只读回归通过。 |
| 多 domain/range、未指定端点 | 原术语表单/目录 → 检查器端点集合、目录及类型卡片 | 术语 API、草案 `add_domain/remove_domain/add_range/remove_range`；无端点关系保留独立目录身份；不以画线有无判断术语存在 | 目录契约保留缺端点定义；[记录创建契约](../../tests/service/record-create.test.cjs)保留多/未限定端点；[端点服务测试](../../tests/service/test_ontology_relation_endpoints.py)；多/未限定端点、独立目录身份与服务约束回归通过。 |
| 图形、层级树、RDF | 原本体图/树/源码 → 本体页“类型卡片 / 图谱 / 层级树”、目录“RDF 源码 / 导出” | 图形/布局切换仅改变视图；RDF 通过 ontology/draft 读取；源码修改另走草案，不把保存布局当发布 | 画布契约覆盖布局、拖动、视口与层级分带；目录契约；目录、画布/层级、RDF 与布局契约回归通过，真实本体页截图另列。 |
| 约束与 SHACL | 原底层草案约束/校验 → 对象检查器约束字段、校验/审核/发布清单及 Turtle 差异 | `/ontology-drafts/{id}/commands`、`/validate`、`/review-plan`、`/publish-readiness`；服务分别给出语法、约束与发布资格，未执行不当作通过 | [草案治理契约](../../tests/service/ontology-governance-delivery.test.cjs)；[审核计划测试](../../tests/service/test_ontology_review_plan.py)存在；语法/约束/实例影响、审核计划与发布资格的服务回归通过。 |
| 停用类/关系/属性 | 原 retire 命令/影响面 → 三类对象检查器“停用这个术语” | `GET P/ontology/term-impact` 后草案 `retire_term`；原因必填，影响读取失败阻止提交；历史发布只读，逐项审核后生效 | 历史只读/恢复契约；[本体操作测试](../../tests/service/test_ontology_operations.py)；依赖影响、停用原因、草案审核和历史保留回归通过。 |
| 从历史恢复术语 | 原 restore 命令 → 停用对象检查器“恢复来源”及字段勾选 | 草案 `restore_term` 明确提交同项目 `source_ontology_id` 与非空合法 `selected_fields`；修订、基线及发布审核守卫 | 历史只读/恢复契约验证来源发布/字段必填与请求；来源发布、字段选择、预期修订与恢复发布回归通过。 |
| 本体源码编辑/导入 | 原 Turtle 写草案 → 目录“RDF 源码 / 导出”中的“编辑 Turtle” | `GET P/ontology` 或 draft；`diff_turtle` 编译差异进入草案。编辑窗口保存前复核项目、草案、修订与发布身份；历史禁写；未承诺任意格式导入 | 目录契约覆盖陈旧窗口拒写；[高级工具契约](../../tests/service/ontology-advanced-tools.test.cjs)；Turtle 差异编译、草案治理及陈旧编辑窗口拒写回归通过；不扩展为任意格式导入承诺。 |
| 草案创建/切换/关闭 | 原草案服务 → 目录“选择待处理草案 / 新建草案 / 关闭当前草案”与版本管理历史新草案 | `GET/POST P/ontology-drafts`、`/{id}/close`；选择返回的准确草案；保留其他未发布草案及审计；历史预览禁写 | 本体刷新契约覆盖新建草案不替换另一草案、切项目旧响应丢弃；[历史抽屉契约](../../tests/service/ontology-history-lifecycle.test.cjs)；草案选择、新建、关闭与审计保留回归通过。 |
| 校验、逐条/安全批量审核 | 原审核服务 → 本体页“校验并审核”、逐条审核窗口、发布清单 | draft `/validate` → `/submit` → `/decisions` 或 `/decisions/batch-approve`；服务给出可批准集合，携带 validation/operation fingerprint 与警告确认；高风险不由前端降级 | 草案治理契约；审核计划/发布规划服务测试存在；高风险、冲突、逐条/安全批量及发布资格 PG/前端回归通过。 |
| 基线对齐/来源刷新 | 原 rebase 服务/UI → 草案过期提示中的“重新基线 / 刷新来源” | `POST P/ontology-drafts/{id}/rebase`；预期修订与基线复核，保留命令，显示重放冲突并重新校验 | 本体刷新与草案治理契约；[草案服务测试](../../tests/service/test_ontology_drafts.py)；基线冲突、来源刷新、命令重放及重新校验回归通过。 |
| 不可变发布/同号标注修订 | 原发布/版本列表 → 本体页发布清单与版本管理 | draft `/publish`；使用真实 publication ID、服务版本号与发布说明；同 vN 可有不同身份；未发布草案不修改既有发布 | [版本显示契约](../../tests/service/record-version-context.test.cjs)、历史抽屉契约；发布身份、不可变记录、幂等重试与并发发布回归通过。 |
| 历史查看/历史新草案/差异 | 原历史查看/revert/draft diff → 版本管理“查看历史 / 以历史为基线建草案”、草案变更差异 | `GET P/ontologies`、`GET P/ontology?ontology_id=...` 与创建草案；预览不切线上发布，历史不直接写入；任意两版细粒度比较仍未作为本轮能力 | 本体刷新契约、历史抽屉契约、历史只读/恢复契约；历史只读、历史新草案、差异与重新发布回归通过。 |
| 关联知识与受控迁移 | 原知识归属/迁移预演执行撤销 → 版本管理“关联知识 / 受控重分类”、台账“检查本体归属” | `/ontologies/{id}/knowledge-context`、`GET/POST P/reclassify`、`/reclassify/preview`、`/operations/{id}/undo`；真实映射、原发布、预期修订及后续依赖守卫 | [迁移契约测试](../../tests/service/test_reclassify_migration_contract.py)、[迁移 UI 测试](../../tests/service/test_reclassify_migration_ui.py)存在；映射预演、执行、版本/依赖守卫及撤销的服务/浏览器回归通过。 |
| 实体/关系/属性 CRUD | 原记录服务/实体编辑 → 台账三类“新增”、实体详情“新增关系/属性”、共享编辑/历史/删除恢复 | `POST P/records`、`PUT P/records/{id}`、`GET .../history`、`POST P/delete`、`P/restore`；独立 ID/修订/来源；绑定发布和端点校验；管理员写入；409 保留输入比较最新版本 | 记录创建契约、[创建 UI 契约](../../tests/service/record-create-ui.test.cjs)、[分页台账契约](../../tests/service/records-pagination-delivery.test.cjs)、[属性编辑契约](../../tests/service/record-attribute-editor.test.cjs)；真实实体创建 201；真实关系/属性创建、共享编辑准确追加修订且前史不变；最终 CRUD/冲突/权限回归通过。 |
| 别名/查重/重复组 | 原台账消歧条 → 台账“实体消歧与融合”、按需“组疑似重复”、别名按钮 | `POST P/resolve`、`P/aliases`、`GET P/duplicate-groups`；同名/评分只作候选，不自动把多个精确匹配认作同一对象；别名写入需管理员 | [实体治理契约](../../tests/service/entity-governance-delivery.test.cjs)；[实体匹配测试](../../tests/service/test_entity_matching_contracts.py)；别名、同名候选、多精确匹配与服务端分页匹配回归通过。 |
| 融合与冲突选值 | 原 merge/attribute_winners → 台账明确保留/并入对象、服务端影响预演、逐组 winner 确认 | `POST P/merge/preview`、`P/merge`；预期实体版本、预览 fingerprint、属性冲突与依赖复核；类型/业务区间/范围兼容由服务判定，不能仅按同号版本限制跨发布融合 | 实体治理契约验证显式 winner、同一预览身份；[治理服务测试](../../tests/service/test_governance_delivery.py)存在；属性 winner、预览指纹、融合事务、来源与并发守卫回归通过。 |
| 消歧复核 | 原 resolution-reviews → 台账融合区“消歧复核”的合并/保持独立/驳回 | `GET P/resolution-reviews`、`POST .../{id}`；原因、decision_version；合并时携带确认的 winner、实体版本、预览 fingerprint；拒绝不删除来源 | 实体治理契约验证真实复核修订和 winner/fingerprint 请求；[实体消歧服务测试](../../tests/service/test_entity_resolution.py)；合并/独立/驳回、decision_version 与 winner/fingerprint 回归通过。 |
| 治理撤销/软删除恢复 | 原 operations/delete/restore → 台账“可撤销的操作”、共享详情历史与恢复 | `GET P/operations`、`POST .../{id}/undo`、`P/delete`、`P/restore`；后续修订/依赖变化阻断覆盖，恢复生成新修订，不删除审计 | [治理测试](../../tests/service/test_governance.py)、[治理交付测试](../../tests/service/test_governance_delivery.py)存在；删除/恢复/撤销及后续依赖/修订阻断的服务与界面回归通过。 |
| 上传/解析/OCR/ZIP/切片 | 原文件队列/解析预览/OCR → 资料库“添加文件”、逐文件选项/预览、固定原文阅读器 OCR 操作 | `GET /api/ingest/capabilities`、批次 files/items、documents preview/upload preview；OCR retry/ignore 走 source-documents；按真实格式/限额执行，写入及 OCR 变更需管理员 | 输入/批次契约、[原文阅读器契约](../../tests/service/source_reader_ui.test.cjs)；真实 documents 上传完成；ZIP/切片/解析/OCR资格与失败恢复契约回归通过；本轮真实上传为 TXT，未对全部解析器/OCR 模型执行业务质量评测。 |
| 批次/任务/索引 | 原批次、jobs、索引任务 → 添加文件批次详情、“构建与任务”、检索“构建语义索引”；本体索引修复在手册保留明确高级 API 路径 | `/ingest-batches/{id}/{action}`、items retry、`/api/jobs/{id}/{action}`、`POST P/indexes/rebuild`、`GET P/ontology-index`、repair/tasks retry；仅当前状态允许的操作，活动任务复用与修复由服务决定 | 批次契约；[索引同步服务测试](../../tests/service/test_ontology_sync_worker.py)存在；真实 documents 批次完成；450k Milvus 逻辑/active/模型覆盖核验通过，但报告快照 17 个索引仍 InProgress；真实 documents 批次、显式 Milvus 重建及租约重试完成；repair/retry/活动任务复用回归通过，物理索引状态以各测量快照为准。 |
| Metadata/时间/版本范围 | 原公共 scope → QA/检索图谱/资料/台账默认关闭的本页筛选、应用标签、高级 JSON 与历史/版本入口 | Scope 随项目+页面保存；管理/上传/本体/任务/备份不继承内容过滤；显式应用/复制；服务裁决 applicability、时间与本体身份，ticket 拒绝陈旧响应 | [工作区范围契约](../../tests/service/workspace-redesign.test.cjs)、局部图契约、分页台账契约；管理与消费页面真实打开；150k 实体库的高级 Metadata 筛选正确性已核对，耗时 8.622s（引擎层、不含编码）；完整范围回归与实际浏览器应用/清空/高级 JSON 通过；关闭 rail 零 facet 请求、管理页不继承内容范围。 |
| keyword/semantic/hybrid | 原检索模式 → 检索与问答模式选择、本体扩展开关 | `POST P/search`、`P/qa`、`P/qa/stream`；semantic/hybrid 只经 Milvus，编码器/身份/维度/投影不就绪明确失败；keyword 为显式策略，禁止自动关键词/本地向量替代 | 工作区范围契约验证所选向量模式失败；[通道一致性测试](../../tests/service/test_retrieval_channel_parity.py)、[混合检索测试](../../tests/service/test_hybrid_retrieval.py)存在；450k 逻辑库实体/关系通道配额与模式核验通过，暖引擎 P95 0.2708/0.2778s、含编码 HTTP P95 1.031/1.484s（依次 semantic/hybrid），4 并发正确返回；真实 Milvus 三策略/配额测量及最终范围/故障协议回归通过；HashingEncoder、无 Milvus 和旧投影明确失败，显式 keyword 独立保留。 |
| 图谱邻域/脑图/路径 | 原 subgraph、脑图、当前加载图 BFS → 显式种子的局部探索、实体脑图、加载图内路径工具与共享检查器 | `POST P/graph-neighborhood` 带范围、跳数、方向、max_nodes/max_edges；实体选择器 `/records/page` 服务端分页/精确 ID；超预算明确截断；`/mindmap` 与加载图内 BFS 不声称全库寻路 | [局部图契约](../../tests/service/local-graph-neighborhood.test.cjs)、[选择器契约](../../tests/service/local-graph-picker.test.cjs)、[路径契约](../../tests/service/graph-paths.test.cjs)；深页实体→共享编辑/局部图/脑图的真实浏览器回归通过；局部预算截断明确，不宣称全库寻路或签名续传。 |
| 15 万全库检索与同图绘制 | 旧局部图无双目标证明 → “全量大图”与独立全库检索/分页定位 | `POST P/graph-sessions`、`/{id}/page` 固定项目/范围/水位、owner、签名 cursor、过期及变更失效；WebGL 完整实体/关系缓冲，不以标签或聚合数凑实体数 | [完整图契约](../../tests/service/full-graph-renderer.test.cjs)、[Worker 错误契约](../../tests/service/full-graph-worker-errors.test.cjs)；[renderer 报告](2026-10-19-full-graph-renderer-capacity.json)含 1m 边/30min；[后端报告](2026-10-19-backend-capacity.json)150k/300k 枚举 20.1887s；[真实产品长测](2026-10-19-product-capacity-validation.json)已完成 1871s/72 交互样本，计数/端点/共享详情/实际修订/历史2版/旧会话409/重载150001实体均通过。帧 P95最大16.8ms、点击P95 188ms、浏览器RSS峰2.786GiB；并行回归负载下五轮加载P95 46.906s，30s未达。初轮127.89s和审计锁修复证据保留；真实产品计数、绘制、交互与共享编辑通过；独立五轮完整加载 P95 48.187s 未达 30s，单条大库修订对照后仍为 125.454s，完整容量规格未全通过。 |
| OAG 与 Local QA | 原词汇扩展/空图证据普通问答 → 普通分组 QA 实际实体锚点、限定邻域/有向路径与来源上下文 | `/qa`、`/qa/stream`；图与来源在服务端按项目/范围/时态资格进入上下文；结构待定单列；关系端点显式进入提示，不把邻接当逻辑证明 | [图提示测试](../../tests/service/test_answer_prompt_graph.py)、通道一致性测试存在；普通默认分组、图事实/有向端点及 JSON/SSE 回归通过；上下文与引用使用受控模型替身验证，真实模型回答的语义质量未单独评分。 |
| 社区 Global/DRIFT | 原实现缺完整社区链 → 无伪造可执行策略，手册“独立后续阶段” | 本轮未注册社区报告/Global/DRIFT 执行 API；不能用 keyword/semantic/hybrid 或 Local/OAG 代称 | 明确后续阶段，未作为本轮交付或缺失既有能力；明确独立后续阶段；没有注册伪造可执行策略，也未作为本轮已交付能力。 |
| 抽取质量/问答消融 | 原 evaluate 精确抽取评测 → “质量评测”的“抽取质量 / 检索与问答消融”两个入口 | `POST P/evaluate` 使用自身范围；`P/retrieval-experiments` 创建/运行/取消/重试/导出；固定 manifest、实际 omitted_stages、owner/当前账号和水位复核；无标准/无模型不造分数 | [消融前端契约](../../tests/service/retrieval-experiments.test.cjs)、[消融服务测试](../../tests/service/test_retrieval_experiments.py)存在；真实五变体执行及页面导出五个结果已通过；真实五变体执行及页面导出五个结果通过；账号失效/来源变化、固定输入与取消/重试回归通过，不将完成状态当作模型质量分数。 |
| 知识备份/导出/恢复 | 原 snapshots/导出/旧项目导入 → “构建与任务 → 知识备份”、项目设置备份入口、项目管理“导入旧项目” | `P/snapshots`、`/{id}/restore`、`GET P/export`、`/api/local-projects` 与 import；恢复前快照、只写记录新修订；管理员变更，不称原文件/本体发布/索引全项目回滚 | 源码已接通 `workbench.js` / `projects-view.js`；[服务工作台测试](../../tests/service/test_workbench.py)存在；备份/恢复与旧格式导入服务回归通过；Windows 缺少符号链接创建能力的越界分支跳过，详见最终跳过清单。 |

新增管理要求另列，不替代以上原始能力行：

| 能力 | 既有入口 → 当前产品入口 | API / 守卫 | 行为证据与当前状态 |
| --- | --- | --- | --- |
| 项目管理与快速切换 | 原项目下拉/项目页 → 固定“项目管理”、设置抽屉及原快速切换器 | projects CRUD；创建显式 `{name, ontology_mode:'discovery', use_default_ontology:false}`；删除当前/最后项目清上下文，PostgreSQL 删除与外部 cleaned 回执分别展示 | 工作区范围契约；真实项目页打开；项目创建/切换/删除、当前/最后项目上下文清理与外部投影清理异常回归通过；真实项目页已验收。 |
| 人员管理与个人口令 | 原隐蔽账号入口 → 固定“人员管理”与个人账号“改口令” | `/api/users` CRUD、`/api/auth/password`；admin/superadmin/self/启用状态及凭据失效守卫；当前为全局系统角色，不提供项目成员 ACL | 工作区范围契约、[权限测试](../../tests/service/test_auth_rbac.py)存在；真实人员页打开；系统角色、账号变更/会话失效、自我保护及个人口令服务/浏览器回归通过；项目成员 ACL 仍属独立阶段。 |

独立后续阶段保持原批准边界：精确来源修订/候选 manifest、跨草案合入、项目成员 ACL、社区 Global/DRIFT。现有来源版本、断言、草案、系统角色及治理能力仍按上表保留，不能因这些后续阶段未建设而将其降为只读说明。


## 原始规划审计（历史保留，不代表当前交付状态）

日期：2026-10-19。用于完成产品规划与草图审阅；不代表产品实现已完成。沿用 [既有能力审计](2026-10-16-existing-capability-preservation-audit.md) 的源码证据与限制，本轮补核发现父类、画布新增/连线和抽取模式。当前项目 graph MCP 未及时返回时按 AGENTS 允许的受限源码回退核对；没有读取密钥、运行数据库测试或改业务数据。

用户最终口径：**开放/非开放是本批抽取策略；两者都允许后续继续建模。发布记录保持不可变，新修改进入草案再发布。** 不扩展为按类型新增白名单的额外产品需求。

追加硬性要求：全库至少 15 万实体可检索，同时必须有同图加载/绘制 15 万实体的全量模式。当前清单不是完成证明，较早审计对旧工作区的源码判断须结合 [最新图谱审计](2026-10-19-graph-scale-and-ui-parity-audit.md)、[最新检索审计](2026-10-19-retrieval-completeness-and-scale-audit.md) 与 [容量验收规格](../superpowers/specs/2026-10-19-search-graph-scale-and-capability-acceptance-design.md) 阅读。

标记：本地可体验＝独立 HTML 的示例状态；说明入口＝只能阅读流程；待补＝需要产品接线或新增服务。所有既有能力必须保留，草图未覆盖不构成删减授权。

| 能力 | 当前产品静态核对 | 新入口与本轮草图状态 | 实施验收要点 |
| --- | --- | --- | --- |
| 仅文档处理 | 已有 documents 模式 | 上传用途，本地可体验 | 不调用图谱抽取，不要求本体；原文资格独立判断 |
| 非开放抽取 | 已有 ontology 模式与发布 ID 绑定 | 上传“按已有本体抽取”，本地可体验 | 类/关系/属性按既有词汇；不能映射的内容有去向；不暗改开放模式 |
| 开放抽取 | 已有 discovery 模式 | 上传“开放抽取”，本地任务/候选可体验 | 新词汇先候选，正式事实写入走治理；保留来源与依据 |
| 已有本体继续发现 | create_run 以当前发布为基线 | 本体页、资料详情、任务结果 | 不要求删除本体或重传资料；精确来源/预期基线协议仍需补 |
| 发现父类 | **已有前后端** | 已补独立按钮及本地建议采纳 | ≥2 有效类；保留已有边、去重、防环、修订/基线守卫，失败不假成功 |
| 手工新建类/上级类 | 已有，新本体也可创建 | 插入菜单、目录添加，本地可体验 | IRI 身份由服务治理；中文名可用；空本体也有可达入口 |
| 手工新增子类 | 已有新建类 + add_parent 可组合 | 插入“为选中类型添加子类”，本地可体验 | 预填父类但可修改，保留多继承，写草案 |
| 改父类连线/移除父类 | 已有两步连线与父类字段编辑 | 已补两步确认，检查器保留多选 | 不静默删除原父类；移除、循环、自引用与重复有行为验证 |
| 新建业务关系/连关系 | 已有术语 API 与画布两步流程 | 插入菜单、表单、两步连线可体验 | domain/range 与继承分开，确认后写草案 |
| 新增数据属性 | 已有术语命令 | 插入菜单与类型检查器可体验 | 所属类、值类型、约束受服务校验 |
| 名称/说明/稳定 IRI | 已有 | 对象检查器可体验 | 改标签不重造 IRI，不把英文标识变成强制条件 |
| 多 domain/range、未指定端点 | 既有受限 union/可选端点能力 | 目标保留；草图端点表单尚未覆盖全部情况 | 无端点关系保留在目录；不能将无画线误作无关系 |
| 图形、层级树、RDF | 现有图谱/层级树；源码入口可发现性不足 | 卡片/圆点/RDF 可体验；独立层级树布局仍需补 | 布局与语义修改分离，保存布局不生成发布 |
| 约束与 SHACL | 后端与底层命令已有，专用控件部分缺 | 常用字段可体验，高级约束为说明 | 语法/SHACL/质量/实例影响分别出结果；未运行不标通过 |
| 停用类/关系/属性 | 已有治理命令与影响面 | 属性可体验；类/关系完整流程仍需补草图 | 原因、替代、依赖影响、审核；历史保留 |
| 从历史恢复术语 | 后端已有，当前 UI 缺参数 | 来源发布/字段选择可体验 | source_ontology_id + 合法 selected_fields；发布前逐项审核 |
| 本体源码编辑/导入 | Turtle 草案写入已有；多格式 imports 不完整 | 查看/导出可体验，写入/导入差异待补 | 保留原图、未支持表达；差异编译和治理，不直接覆盖发布 |
| 草案创建/切换/关闭 | 已有 | 当前/历史新草案可体验；草案列表/关闭未完整画出 | 保留既有编辑，不因新发现覆盖；关闭不删除审计 |
| 校验、逐条/安全批量审核 | 已有服务 | 本地字段检查/示例发布可体验；完整审核操作待补 | 风险由服务重算；高风险不能经安全批量批准绕过 |
| 基线对齐/来源刷新 | 已有服务与 UI | 治理抽屉说明 | 展示差异和冲突、重放命令、重新校验；不丢草案 |
| 不可变发布/同号标注修订 | 已有 | 版本记录和演示发布 | 记录真实发布身份；标注发布可能同 vN，不按列表序号造版本 |
| 历史查看/历史新草案/差异 | 历史查看、revert、草案 diff 已有 | 本地可体验；任意两版细粒度比对待补服务 | 查看不切线上状态；回到历史通过新草案，不覆盖历史 |
| 关联知识与受控迁移 | 已有版本归属/预演/执行/撤销 | 治理抽屉说明，完整操作待补 | 实际本体依据、预期修订、映射和失败逐项解释 |
| 实体/关系/属性 CRUD | 已有服务，部分表单不足 | 实体主流程和部分关系/属性本地可体验 | 独立属性/关系记录 ID、修订、来源与删除/恢复，不被实体表单替代 |
| 别名/查重/重复组 | 已有 | 台账与实体详情可体验 | 同名仅作为候选线索，多精确匹配需明确复核 |
| 融合与冲突选值 | 融合已有；winner 前端接线不足 | 显式选值与影响确认可体验 | 类型/业务区间/范围兼容，跨本体发布可融合；保留断言和来源 |
| 消歧复核 | 后端已有，复核 winner 参数桥接缺 | 合并/保持独立/驳回可体验 | 原因、预期修订、冲突选值，拒绝不删除来源 |
| 治理撤销/软删除恢复 | 已有 | 本地可体验 | 后续修订/依赖守卫，恢复新修订，审计不删除 |
| 上传/解析/OCR/ZIP/切片 | 已有，按真实 capabilities | 三步上传和部分高级设置可体验 | 支持/限额读服务；复用解析收据，失败与分段重试可定位 |
| 批次/任务/索引 | 已有批次、控制与派生索引 | 任务、重试、索引详情主要路径可体验 | 只呈现状态允许的操作；核验/修复/幂等活动任务复用需逐项验收 |
| Metadata/时间/版本范围 | 现有丰富范围协议 | 按页侧栏、已应用标签、历史入口可体验 | 页间隔离；范围/时点/权限仍由服务裁决，不只做前端过滤 |
| keyword/semantic/hybrid | 已有 | 检索设置与问答 | 记录真实模式、降级和证据资格 |
| 图谱邻域/脑图/路径 | 已有，路径是当前加载图 BFS | 主要浏览与路径说明可体验 | 不宣称全库寻路；图操作与事实编辑区分 |
| 15 万全库检索与同图绘制 | 未有双目标完成证据；原生加速不等于所有范围可扩展 | 已加入必做目标，草图明确未验收 | 150k 必须为实体数；完整 ID/边/范围清单、实际绘制与交互/内存、编辑回归均通过 |
| OAG 与 Local QA | 词汇扩展部分已有；正常分组 QA 图证据链尚需补 | 目标保留，不能以静态图证明已接通 | 实体/邻域/路径实际进入上下文，引用支持、范围与时点通过 |
| 社区 Global/DRIFT | 当前项目缺完整接入 | 后续能力，不伪造可用策略 | 社区/报告 manifest、范围资格、成本、失败恢复可验证 |
| 抽取质量/问答消融 | 抽取精确 P/R/F1 已有；消融执行缺失 | 抽取评测保留，消融配置/报告结构可体验 | 真实固定输入对照，逐题证据、取消/重试/导出，未测不造分数 |
| 知识备份/导出/恢复 | 已有，恢复仅记录新修订 | 项目菜单入口与确认说明 | 不称为整项目/本体/原始文件/索引回滚 |

## 本轮补核的源码锚点

- `web/ontology-model.js:331–335,373,617–640`：发现父类按钮、状态与 API 调用；`api/ontology_drafts.py:132–137`；`services/ontology_drafts_commands.py:29–84`：已有类查找、模型建议、重复/继承环过滤、请求后再次核对修订/基线、写入 diff_turtle 草案命令。并不是发布后只能看不能改。
- `web/ontology-model-canvas.js:588–716`：新建类、改父类、连关系、两步确认；`web/ontology-model-panel.js:285`：新建 class/relation/attribute，父类/domain/range 可留空。插入中间父类并重接旧边未核到单独原子操作，不能凭措辞假定已有。
- `models.py:145–171,195`：ontology/discovery/documents、extract_attributes、relation_constraint_mode、resolve_entities/auto_merge 等独立参数；`web/ingest-ontology.js` 为 ontology 模式绑定已发布版本。词汇开放程度、关系约束处置和实体融合是不同控制面。
- `services/service.py:925–1016`：受控路径加载明确本体并保留复核候选；`:1016` 起为开放发现分支。`services/ontology_discovery_workflow.py:394–435`：已有发布仍以其为归纳基线。
- `services/document_ingest_runner.py:112` 起合并批次 settings 与 item.options；有逐文件选项基础，但本次用户并未要求新增复杂分层权限/类型白名单。UI 优先清楚表达每次抽取方式。

## 实施顺序

1. **既有能力不退化**：按清单恢复入口、固定开放/非开放语义、持续建模、父类发现与手工插入；补恢复/winner/复核等契约错配。逐项关联现有前后端验证。
2. **完整资料与本体流程**：精确来源/预期基线、草案管理/合入、导入差异、完整迁移/索引修复交互。只有接通并验证后才开放写操作。
3. **检索增强与评测**：接通正常 Local/OAG 图证据链及可复现实验；之后社区 Global/DRIFT。既有本体与实例治理不以第三步完成为前提。

每项验收登记“既有入口 → 新入口 → 服务协议 → 状态守卫 → 行为证据”。能力表完成不等于产品全部完成；草图仍有明确列出的交互覆盖差距。
