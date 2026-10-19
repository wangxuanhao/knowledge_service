# GraphRAG / Semantica 产品重设计参考核对

日期：2026-10-16。方法：官方文档、固定发布标签的源码和发布说明；未部署 Microsoft GraphRAG、未复现框架质量基准。下文明确区分外部库能力、项目已有约定与本次设计建议。

## 1. 口径与本地基线

- 本项目文档中的 OAG 是 **Ontology-Augmented Generation（本体增强生成）**，不是 Open Academic Graph。已有分析把它理解为本体参与构建、召回、查询与验证的工程方向，并未宣称存在统一行业合格标准。[项目 OAG 定义](../../2026-10-14-oag-open-source-research.md)
- 当前 `pyproject.toml` 的可选运行依赖为 `semantica[parse-docling]>=0.7,<0.8`；本次使用 `D:/anaconda/envs/llm_model/python.exe` 的 `importlib.metadata.version('semantica')` 核验，实际安装 **0.7.0**。旧报告的 0.6.8 能力判断需要按版本更新。[本地依赖](../../../pyproject.toml)
- 官方 v0.7.0（2026-09-22）确实新增层级社区、社区报告、Global Map-Reduce 与 DRIFT。**安装了 0.7.0 不等于本项目已调用这些入口**；现有集成情况须由项目代码审计确认。[官方发布](https://github.com/semantica-agi/semantica/releases/tag/v0.7.0)
- 本项目当前约定是 PostgreSQL 唯一权威存储，Milvus/Neo4j 为派生能力；既有文档已经区分本体发布与知识修订。新设计应复用这条边界，不照搬框架自带第二套存储/版本控制面。[项目约束](../../../AGENTS.md)、[双版本设计](2026-10-08-ontology-knowledge-version-context-design.md)

## 2. Microsoft GraphRAG 真正值得借鉴的结构

其索引不是一张图：文本切片、实体关系、层级社区和社区摘要共同构成可查询产物；查询引擎消费已完成的索引。[索引概览](https://microsoft.github.io/graphrag/index/overview/)、[查询概览](https://microsoft.github.io/graphrag/query/overview/)

| 方式 | 官方行为 | 对本项目的产品含义（建议） |
| --- | --- | --- |
| Basic | 对 text units 做 top-k 向量检索，作为普通 RAG 对照 | 保留文档问答入口；图抽取不完整时仍能引用原文 |
| Local | 先匹配相关实体，再取实体、关系、原文片段及可选报告，排序并压入上下文预算 | 支持“这条规则涉及谁/条件与关联是什么”，展示实际使用的事实与证据 |
| Global | 对社区报告做 Map-Reduce，回答全库主题问题；官方明确提示资源开销较大 | 面向“选定资料主要有哪些主题/差异”，不作为精确条款或数值全集查询 |
| DRIFT | 用社区信息提供起点，再发起局部追问，形成层次化问题与答案 | 面向跨主题探索；需要轮次、成本、取消和可检查的中间结果 |

来源：[查询概览](https://microsoft.github.io/graphrag/query/overview/)、[Local](https://microsoft.github.io/graphrag/query/local_search/)、[Global](https://microsoft.github.io/graphrag/query/global_search/)、[DRIFT](https://microsoft.github.io/graphrag/query/drift_search/)。这些是检索策略，不是逻辑证明；多跳路径存在不能单独证明一个业务结论。

界面可以用“文档查找 / 关联解释 / 全局归纳 / 深入探索”等任务名称，默认按问题路由并显示本次策略与原因。首期只有已接通且通过验收的策略可选择，不能用相同后端换四个标签。

### 可追溯产物

| 产物 | 官方关键关联 | 本项目需保留的联系（建议） |
| --- | --- | --- |
| documents | 原文、标题、text_unit_ids | 逻辑来源 → 不可变文档修订 → 解析产物 |
| text_units | document_id、实体/关系/可选 covariate ID | 原文页/章节/span → chunk 修订 → assertion |
| entities / relationships | 描述、text_unit_ids | 规范事实修订 → 各来源断言；允许多来源，不把 source_id 强塞回规范实体 |
| communities | 层级、成员实体/关系/片段 | 固定输入集合与图构建版本 → 社区产物 |
| community_reports | 社区摘要、发现、解释与结构化内容 | 报告 → 成员/事实/原文证据；报告自身也是可失效的派生产物 |
| embeddings | 按目标字段写入向量存储 | 输入修订、模型/维度、切片策略 → 向量产物 |

来源：[输出表](https://microsoft.github.io/graphrag/index/outputs/)、[v3 迁移说明](https://github.com/microsoft/graphrag/blob/v3.2.0/breaking-changes.md)。官方 `human_readable_id` 是每次运行生成的短编号，不适合跨索引充当稳定业务身份；v3 的 `text_units.document_id` 已是单值，不能照抄旧版 `document_ids` 示例。

官方支持 Bring Your Own Graph：已有实体/关系/证据映射可以只运行社区与报告工作流，其他搜索再补需要的 embedding。对本项目，优先从已治理事实构建可重建投影，避免让另一轮抽取产生第二套权威实体。[BYOG](https://microsoft.github.io/graphrag/index/byog/)

## 3. Metadata 应定义检索资格，而不是一排字段

官方输入文档的 metadata prepending 是把字段复制到 chunk 文本中，改善上下文信息；它**不能作为业务范围、权限或有效期已被执行的证据**。当前同一页顶部使用 `chunking.prepend_metadata`，后面的示例仍含旧 `chunks` 配置，落地要按固定版本配置模型核验。[输入与切片](https://microsoft.github.io/graphrag/index/inputs/)

本项目建议把查询范围收成一个可复用对象，UI 只暴露常用业务条件，其余按需展开：

1. **硬边界**：当前项目、访问资格、允许的来源文档修订、软删除/审核资格。
2. **业务范围**：平台、业务、适用对象；继续区分具体、通用、未知，不能把空值自动解释成通用。
3. **时间与版本**：业务有效时点 `valid_at`、系统获知时点 `known_at`、事实绑定本体、允许的索引代次。
4. **内容精确条件**：文档/知识类型、已治理类与谓词、允许检索的业务 metadata；类型化运算符及显式 AND/OR。
5. **排序参数**：top-k、分数、重排、hop/token 预算；与资格条件分开，低相似度不等于无权限或失效。

以上为设计建议，业务语义承接[统一 Metadata 设计](2026-10-16-unified-metadata-and-graph-path-design.md)、[来源与文档问答设计](2026-10-16-source-scope-document-qa-design.md)。条件应在召回前尽量下推，在补全原文/邻域/报告/缓存命中后再次核验；所有入口共用一个范围契约。

**社区摘要有额外边界**：如果生成时混入了不合格平台、权限或历史来源，查询后删除几条引用不能消除摘要正文中的信息。报告需绑定明确的输入 manifest 与适用范围；不满足本次范围时，选用已匹配范围的报告、重建范围内报告，或回退文档/Local。任意动态过滤不能承诺总有可用 Global 产物。此结论是从报告聚合机制推导的产品要求，不是声称框架已自动提供隔离。

## 4. Semantica 0.7.0：可复用能力与限制

| 已核对的库能力 | 限制及建议 |
| --- | --- |
| `ContextRetriever.retrieve(mode=local/global/drift/hybrid)` | global/drift 需要报告/层级等输入；API 名存在不等于业务证据、权限与时间契约已接通 |
| `truth_filter` 对 session snapshot 验证依赖 | 源码明确只支持 `local`，其他模式传入会报错；不能假定 Global/DRIFT 继承相同真实性维护 |
| Global 报告 Map-Reduce、来源社区引用 | 无 LLM 或调用失败时存在确定性抽取/拼接 fallback；需记录实际执行状态，不能宣称都完成了模型综合 |
| provenance 跟踪文档、chunk、实体/关系和多来源属性，导出 PROV-O | 官方明确 provenance 不保证来源为真；自定义写入需主动接入，调用可返回失败，不能仅凭引入模块宣称全链完整 |
| 图/本体快照、diff、checksum 与恢复 | 官方明确“tracks nothing automatically”；快照时机由调用方控制，默认版本存储在内存，不解决本项目发布/索引生命周期 |

来源：[ContextRetriever v0.7.0](https://github.com/semantica-agi/semantica/blob/v0.7.0/semantica/context/context_retriever.py)、[Global 实现](https://github.com/semantica-agi/semantica/blob/v0.7.0/semantica/context/global_retriever.py)、[provenance 指南](https://github.com/semantica-agi/semantica/blob/v0.7.0/docs/guides/provenance.md)、[change management](https://github.com/semantica-agi/semantica/blob/v0.7.0/docs/reference/change_management.md)。README 中“完整审计”“生产/合规”等宽泛表述不构成本项目的实测结论。

建议把 Semantica 保持为算法适配层：解析/受控抽取/质量检查/社区构建等逐项接入，项目继续负责审核、不可变修订、范围、事务与任务状态。不以模块覆盖数量作为验收，也不整体迁移到其 Explorer 或 SQLite 版本仓库。

## 5. 来源、本体、索引、快照的关系应显式化

下表是**建议的目标契约**；新增索引/问答 manifest 不代表项目当前已有全库快照。它应延续既有“两条独立版本轴”，避免再造一个可编辑的“知识库 vN”。

| 身份/版本 | 含义与变化规则 |
| --- | --- |
| 本体 `ontology_id` / 本体 vN | 不可变发布结构；知识修订绑定创建/迁移所依据的发布，发布新本体不自动改写旧知识 |
| 来源逻辑 ID / 文档修订 ID + hash | 同一来源可有多次内容修订；重新解析和同名上传不能覆盖既有引用目标 |
| 处理运行 `ingest_run_id` | 固定来源修订、抽取本体、解析/切片/模型/prompt/profile；同一文件可以复跑多次 |
| assertion / 规范事实 `version_id`、修订 rN | 多来源断言支持规范事实；审核或迁移产生新修订，保留旧证据 |
| 索引代次 `index_generation_id` | 固定输入修订 manifest、范围、处理配置、embedding/图/社区产物；可落后、失效、重建，不是权威知识历史 |
| 问答快照 / `retrieval_run_id` | 固定本次 query、范围、双时点、索引代次、实际入模事实/片段版本与引用；用来复核当时回答 |

关系：`来源修订 → 处理运行（本体发布）→ 断言/事实修订 → 索引代次 → 问答快照 → 答案引用`。文档原文可以独立支撑文档说明问答，无须先成为正式图事实；文档说明仍需遵守现有有效性分层。

既有[双版本设计](2026-10-08-ontology-knowledge-version-context-design.md)是本体/记录语义依据；索引和问答 manifest 为本次建议。索引失效不能标成来源失效，本体历史预览不能标成切换生产知识；历史回答的查看仍需当前访问资格校验。

## 6. 增量更新必须由产品自己兜住

Microsoft GraphRAG **v3.2.0** 的 `get_delta_docs()` 按 **title** 比较新旧输入，`load_update_documents()` 只返回 `new_inputs`，没有处理返回的 `deleted_inputs`。因此同标题内容修改不被该差量选择，删除也不能据此承诺已从索引清除；这是固定版本源码核对，不依赖 issue 描述。[差量实现](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/index/update/incremental_index.py)、[更新工作流](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/index/workflows/load_update_documents.py)

建议用来源逻辑 ID + 内容/metadata/profile 指纹判断新增、替换与重跑；撤回或修订立即影响权威检索资格，并使依赖图/报告/缓存进入待重建。旧索引在资格核验失败时回退或明确不可用。重建完成后再切换可用索引指针，失败保留可检查任务，不显示“全部同步”。

官方还要求次版本升级刷新配置、大版本升级使用迁移或重建；索引 manifest 应记录框架版本与 schema 版本，升级需验证现有样本与引用映射。[官方版本说明](https://github.com/microsoft/graphrag)、[breaking changes](https://github.com/microsoft/graphrag/blob/v3.2.0/breaking-changes.md)

## 7. 对上传流与页面结构的落地建议

- 上传围绕来源对象展开：选项目/资料集合 → 文件与同源修订识别 → 解析预览 → 来源/平台/时间 metadata 核对 → 处理配置与本体版本 → 执行 → 查看原文、断言审核与索引就绪情况。批量上传允许共用默认值和逐文件覆盖，展示文件级失败与可恢复位置。
- 把“原文可检索”“抽取完成”“断言待审核”“图投影就绪”“社区摘要就绪”分开；不让图抽取失败阻断已合格原文问答，不把待审核字段升级为正式事实。任务状态从真实后端能力得出，缺少接口时先实现接口或明确限制。
- 工作台按对象与任务组织：来源资料及文档详情、知识与证据、实体关系探索、本体治理、问答。版本/任务/索引状态属于相关对象的上下文和详情；不要把上传、本体、台账、图、统计、系统配置同时平铺在一个首页。
- 优先打通：上传一个真实规则文档 → 明确 metadata 与本体 → 查看原文/待审和正式知识 → 按同一范围问答 → 点击引用定位冻结原文。再增加社区 Global/DRIFT；使用固定问题集比较文档 RAG、Local 和社区模式的证据正确性、范围泄漏、延迟与成本，不凭演示判断提升。

以上为设计推导，不表示这些新增流程和验收已在当前项目完成；本次仅新增此参考文件。
