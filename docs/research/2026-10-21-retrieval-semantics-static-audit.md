# 检索、问答与图证据语义静态审计（2026-10-21）

审计对象是当前工作树，不把早期规划当成已经具备的功能。遵循 AGENTS.md 与 CONTEXT.md：PG 为业务真值；向量排名留在 Milvus；不自动产生 `bf_` 字段。最初阶段仅静态读取，随后按主代理授权先红测、再修复；本审计代理未自行运行 pytest、HTTP、数据库或模型，测试由主代理串行执行。代码图本轮由主代理确认超时且索引陈旧，因此回退已知源码路径。历史发现中的行号指修复前基线，当前实现入口见下表。

## 修复与验证状态

主代理统一运行新7个文件得到 **35 passed / 10.16s**，已实际读取 [production-retrieval-green.log](../../.benchmarks/2026-10-21-production-retrieval-green.log)。对应红基线分别为原QA 6 failed/3 passed、能力恢复17 failed/1 passed（其中1个失败属于其他代理的preflight）、实验/来源接缝9 failed/2 passed。这些是内存适配器契约验证，不能替代真实Milvus/PG/模型质量或规模结论。

| 问题 | 当前实现状态与证据 |
|---|---|
| 默认QA被误判为显式历史 | 已恢复。`freeze_qa_request`先捕获ready generation/watermark再冻结；当前Milvus排名省略隐式known_at，PG资格/证据保留冻结时点；前后守卫。JSON/SSE共用该链。见 [answer_validity.py:20](../../knowledge_service/services/answer_validity.py#L20)，12个QA用例绿。 |
| 任意metadata筛选整体拒绝 | 已恢复完整资格ID路径。公共AST先校验，native无法表达时由PG保留完整AST、流式遍历全部资格版本，Milvus逐批排名，dense/BM25全局归并后只做一次RRF。见 [scoped_ranking.py:20](../../knowledge_service/services/scoped_ranking.py#L20)，包含5002版本、三模式、全局RRF反例和复杂AST用例。 |
| 本体按type补召回丢失 | 已恢复独立业务类型通道，完整scope加独立type资格，不依赖正文词或编码器。见 [scoped_ranking.py:69](../../knowledge_service/services/scoped_ranking.py#L69)、[scoped_reads.py:176](../../knowledge_service/repository/scoped_reads.py#L176)。正文无任何类词、业务范围负例和keyword零模型用例绿。 |
| unrestricted显式通道配额被global k夹小 | 已恢复独立配额。见 [service.py:585](../../knowledge_service/services/service.py#L585)。独立审查发现的platform组预算也已修：仅group_k非None时夹到每组预算。真实service→engine分组反例先红，随后统一集成运行已绿。 |
| 后续平台/QA丢失complete | 已聚合各组complete/incomplete_reason并传入QA覆盖告警及SSE完成事件。见 [service.py:693](../../knowledge_service/services/service.py#L693)、[answers.py:233](../../knowledge_service/services/answers.py#L233)。 |
| 来源被记录id/type/source_id过滤误拒 | reader与classify共用残余AST绑定，业务metadata仍约束来源。独立审查发现的QA再次过滤与ontology归属接缝也已实施修复：由服务端request map绑定artifact/原命中exact版本/同组，QA先重验原命中再重算来源scope；源artifact无需绑定该本体发布。5项真实reader→enrich→QA→apply_validity集成用例已绿，复制tag反例保留。最后同一version跨平台flat hits去重接缝已实施小修，最终整组复验待主代理执行。见 [evidence_scope.py](../../knowledge_service/domain/evidence_scope.py)、[retrieval_context.py](../../knowledge_service/services/retrieval_context.py)、[answer_validity.py](../../knowledge_service/services/answer_validity.py)。 |
| 实验manifest时间被各变体重新冻结 | 已在创建manifest前pin索引、固定时间，variant完整传pin；旧implicit无pin清单明确失效。见 [retrieval_experiments.py:35](../../knowledge_service/services/retrieval_experiments.py#L35)、[199](../../knowledge_service/services/retrieval_experiments.py#L199)。真实create→variant→freezer用例绿。 |

补充集成测试为 [test_qa_source_context_authorization.py](../../tests/service/test_qa_source_context_authorization.py)（id/type/source_id/ontology发布选择下保留固定来源与邻接、复制tag不能授权其他artifact、跨平台同版本去重）及 [test_explicit_channel_quota_restore.py](../../tests/service/test_explicit_channel_quota_restore.py) 新增分组真实引擎用例。最近已读取的统一集成结果为 [production-retrieval-integration-green.log](../../.benchmarks/2026-10-21-production-retrieval-integration-green.log)：**1 failed / 41 passed**，唯一失败是最后新增的跨平台去重用例。对应生产小修已实施、尚待最终42项复验。实现先验证原命中完整scope，再由服务端绑定exact anchor/artifact版本及同组身份；不全局删除identity/ontology条件，也不信任材料自报残余AST。

## 修复前的确定问题与复现依据

### P1：默认问答被当作显式历史查询，hybrid/semantic 直接失败

`answer_validity.freeze_request()` 总会补 `known_at=now`，同时用 `_explicit_known_at` / `_qa_current_snapshot` 区分用户显式时间与默认冻结时间（[9–17](../../knowledge_service/services/answer_validity.py#L9)）。`answers._retrieved_question_context()` 保留该 known_at 传给 `service.search`；这里只移除非显式 valid_at（[228–232](../../knowledge_service/services/answers.py#L228)）。新引擎对任意 known_at 的 hybrid/semantic 拒绝，对 keyword 转 PG 历史关键词（[249–258](../../knowledge_service/services/retrieval.py#L249)）。因此默认 hybrid 问答失败，默认 keyword 问答也绕过当前 Milvus 索引。JSON 与 SSE 都经 `prepare_answer → question_context → _retrieved_question_context`，分别见 [service.answer](../../knowledge_service/services/service.py#L1188) 与 [stream_events](../../knowledge_service/services/answers.py#L538)。旧 HEAD 的引擎明确接受 `_qa_current_snapshot=True`，该分支已移除。

新增尚未执行的最小回归：[test_current_qa_snapshot_retrieval.py](../../tests/service/test_current_qa_snapshot_retrieval.py)。使用内存仓储/索引桩，覆盖 JSON/SSE 默认 keyword/hybrid、默认 semantic、显式历史 keyword、显式历史向量拒绝以及末尾索引切换守卫。它还要求 native 排名省略默认 known_at，但 PG hydration、资格核对及冻结证据保留该时间和精确 version_id。

正确修复需要区分两种时点，不能把所有 known_at 删除。还需处理 freeze(T0) 与捕获索引(T1)之间的并发写：若 v1 在 T0 后更新为 v2且已投影，仅末尾 `is_current` 不能发现此边界，PG按T0回填v1会与Milvus v2不匹配并漏召回。建议 JSON/SSE 在首次冻结前共同捕获 ready index 的 `(index_version,watermark)`，随后冻结时间并传递预期索引身份；初次读索引和返回前都校验，变化时明确重试。显式历史请求不捕获当前索引。

### P1：任意 metadata 筛选由真实支持变为整体拒绝

公共 PG 谓词支持任意 metadata 叶子、AND/OR、存在性、结构相等/数组成员及精确十进制；见 [utils/filters.py:30](../../knowledge_service/utils/filters.py#L30)、[scoped_reads.py:55](../../knowledge_service/repository/scoped_reads.py#L55)。旧 HEAD 引擎会按完整 filters 读取候选，再执行排名。当前所有 current 请求必须通过 `compile_scope`（[retrieval.py:263](../../knowledge_service/services/retrieval.py#L263)），但默认目录永远为空，构建也不扫描 metadata（[search_filter_catalog.py:226](../../knowledge_service/domain/search_filter_catalog.py#L226)），未知 metadata 直接抛错（[190–197](../../knowledge_service/domain/search_filter_catalog.py#L190)）。这属于真实能力收缩；整棵拒绝不会泄漏范围，但不能当作功能等价完成。

最小红测：同一项目两条相同文本/不同 `metadata.region` 的记录，分别验证三个 current 模式、`and`/`or` 组合、嵌套叶子、missing/null、数组 contains、结构 eq、大整数及 Decimal 范围。结果只能含满足完整 AST 的记录，且不得调用本地向量计算。现有 native 单测人为注入 `metadata.amount` 目录，不能证明实际空目录构建可用（[test_native_page_retrieval.py:17](../../tests/service/test_native_page_retrieval.py#L17)）。

### P1：本体扩展丢失按类型补召回

公开模型承诺：父类检索可按类型召回正文没有父/子类词的子类实例（[models.py:114–121](../../knowledge_service/models.py#L114)）。旧 HEAD 有 `_rank_ontology_types`，按 `type_iris` 选择实例并加入类型通道。当前只把 labels 拼进关键词/编码查询（[retrieval.py:244–247](../../knowledge_service/services/retrieval.py#L244)），`type_iris` 仅返回解释信息（[295–299](../../knowledge_service/services/retrieval.py#L295)），没有参与实际召回。keyword 模式下，一条 type 为子类、text 与所有扩展标签都无匹配的实例不会被召回。

最小红测：沿用 [test_ontology_query_expansion.py:88–124](../../tests/service/test_ontology_query_expansion.py#L88) 的实例，改成当前真实 Milvus 接口桩，确认开关打开得到子类实例、关闭不得得到、禁用类不扩展，平台/本体/时态/metadata 负例仍被排除。不能只测拼接后的字符串或返回台账。

### P2：显式三通道预算仍被 k 夹小

引擎现已按三个独立批次执行显式配额（[retrieval.py:242–248](../../knowledge_service/services/retrieval.py#L242)），但应用层仍传 `min(quotas[kind],limit)`（[service.py:580–586](../../knowledge_service/services/service.py#L580)）。默认 k=10 时用户明确请求每通道50只得到每通道10。这段夹小在旧 HEAD 已存在，应归为既有契约缺陷，而非本轮新增回归。现有 [test_retrieval_channel_parity.py:476–491](../../tests/service/test_retrieval_channel_parity.py#L476) 将 engine 整体替换为返回150条的桩，无法发现实际传入配额被夹小。

最小红测：在应用层桩 engine.search 捕获 `content_k`，请求 `k=1,k_entities=2,k_relations=1,k_chunks=2`，应收到原样独立配额；再以真实 engine + 小型 Milvus 桩验证总计5条而不是3条。平台分组的 `k_per_platform` 是否明确是每通道上限必须单独固定契约。

### P2：有界补读的不完整标志在多平台/QA链路丢失

引擎补读最多4次/512条并返回 `complete=False,incomplete_reason=authority_refill_budget`（[retrieval.py:166–218](../../knowledge_service/services/retrieval.py#L166)、[292–293](../../knowledge_service/services/retrieval.py#L292)）。应用层只继承第一个分组 template；platform_groups 不保存 complete/incomplete_reason，最终也未聚合各组完整性（[service.py:634–647](../../knowledge_service/services/service.py#L634)、[681–691](../../knowledge_service/services/service.py#L681)）。第一平台完整、第二平台耗尽时，总响应可以仍宣称 complete=True。QA上下文完全不传播检索完整性（[answers.py:317–348](../../knowledge_service/services/answers.py#L317)），现有覆盖告警只包含图预算等，见 [answer_validity.py:325](../../knowledge_service/services/answer_validity.py#L325)。

最小红测：两平台 engine 桩分别 complete=True/False，要求总结果 False，第二组保留原因；QA JSON/SSE evidence 事件和最终覆盖告警都应包含检索不完整状态。不能以已返回若干合法证据代表完整召回。

## 能力限制与既有行为，不能误报成新增回归

- **NOT AST 从未在当前公共语法或旧 HEAD 中支持。** `validate_filter`、`_matches`、PG SQL 和 Milvus 编译器只有 AND/OR；`ne` 是叶子不等，不等于任意 NOT（[filters.py:30–64](../../knowledge_service/utils/filters.py#L30)）。恢复原能力不需要假装已有 NOT；新增 NOT 要明确 missing/null 的补集语义。
- **source_id 是 chunk 所属文档，不是正式事实多来源。** 固定目录将它映射 `chunk_owner_id`（[search_filter_catalog.py:67–74](../../knowledge_service/domain/search_filter_catalog.py#L67)），投影对非 chunk 明确视为缺失（[search_projection.py:112–117](../../knowledge_service/domain/search_projection.py#L112)）。AGENTS.md 已说明正式事实顶层 source_id 被移除，多对多来源在 assertion 映射中。旧普通 filters 同样读取顶层 source_id，不能证明以前 search 就具备事实的 assertion 来源筛选。事实多来源检索是需要补齐的业务能力；不能把第一个来源写回 source_id 冒充完整支持。
- **显式历史 known_at 只有 PG keyword；semantic/hybrid 明确拒绝。** 不能用 PG 关键词命中替代历史语义功能。若产品要求历史向量，需要具备历史版本向量索引或明确 Milvus 历史资格ID排名方案；当前 current-only projection 不具备它。
- **当前 keyword 检索只索引正文，别名覆盖跨页面不一致。** native text 是 record.text（[search_projection.py:107–110](../../knowledge_service/domain/search_projection.py#L107)）；record-page SQL与历史keyword另行搜索 metadata.aliases（[scoped_reads.py:198–205](../../knowledge_service/repository/scoped_reads.py#L198)、[320–321](../../knowledge_service/repository/scoped_reads.py#L320)）。旧 native 路径也有正文限制，不能把它全部归因于本轮；应补“仅别名出现查询词”的 current/历史/列表对照验收。
- **QA 关系证据由实体种子邻域补充。** 直接检索只保留 entity/chunk（[answers.py:228–229](../../knowledge_service/services/answers.py#L228)），没有实体种子则不会从仅关系文本命中出发遍历。当前邻域默认最多500节点/200边/3跳且仅 forward（[263–265](../../knowledge_service/services/answers.py#L263)、[scoped_reads.py:364](../../knowledge_service/repository/scoped_reads.py#L364)）。这是应说明的覆盖边界，不等于图证据组件存在就证明复杂关系问答正确。

## 核对后保留的正确性机制

- native 返回 record_id/version_id 后，PG `by_ids` 复核项目、可见性、适用范围、本体、时态、完整 canonical AST 及端点；只接受 version_id 精确相同的行（[retrieval.py:199–212](../../knowledge_service/services/retrieval.py#L199)、[scoped_reads.py:112–197](../../knowledge_service/repository/scoped_reads.py#L112)）。返回前还有索引身份/水位守卫。它们有价值，不应为修复召回删除。
- 来源上下文沿正式 record_version_assertions + assertion event 找固定文档版本与切片，保留同来源版本/范围的相邻片段，且复核文档权威资格（[scoped_reads.py:430–496](../../knowledge_service/repository/scoped_reads.py#L430)）。不能恢复基于 metadata.chunk_id 或记录ID前缀猜来源。
- 全图会话保存 scope/owner/watermark，并以 HMAC 绑定游标；每页重新核对水位，connected页复核同scope端点（[graph_sessions.py:38–130](../../knowledge_service/services/graph_sessions.py#L38)）。这不等于证明15万实体前端体验，但静态代码中没有以采样替代完整分页的逻辑。
- QA有效性按冻结来源版本与已映射事件核对，路径要求同平台/来源组内关系及端点都有效；模型只接收已冻结且具有服务端 E 引用的有向 graph_facts（[answer_validity.py:62–162](../../knowledge_service/services/answer_validity.py#L62)、[283–310](../../knowledge_service/services/answer_validity.py#L283)、[answers.py:53–109](../../knowledge_service/services/answers.py#L53)）。最终引用审计只检查编号是否属于提供集合，不能证明每项结论真的被引用文本支持（[491–501](../../knowledge_service/services/answers.py#L491)）。
- 共享详情对删除发送 expected_version，异步详情有项目/请求/范围票据校验（[evidence-inspector.js:64](../../knowledge_service/web/evidence-inspector.js#L64)、[122–140](../../knowledge_service/web/evidence-inspector.js#L122)）；来源冻结展示另有独立历史文本路径（[22–59](../../knowledge_service/web/evidence-inspector.js#L22)）。

## 已采用的完整 metadata 恢复方案与成本

当前保留固定列 native 快路径，并已增加 **PG资格ID门禁 + Milvus排名** 的完整慢路径，不恢复自动bf目录，也不把向量搬回应用。

1. PG使用完整原AST和端点/适用范围/本体/时态资格；复用现有 [ScopedReads.version_id_batches](../../knowledge_service/repository/scoped_reads.py#L231)，流式遍历全部合格版本ID，单批最多1000。不能只取第一页、前5000或关键词候选。
2. 在同一明确索引水位下，每批把有界 version_id 集合交 Milvus。dense/BM25分别以全局可比较分数合并固定top-L；应用只保存少量命中，不读取向量。
3. hybrid必须先合并各批 dense 与 sparse 的全局排名，再做一次统一RRF。不得归并每批已经RRF的结果：每批名次从1重置会改变语义。三内容通道配额分别处理，最后PG按精确版本回填。
4. 全程保持索引/水位守卫；任意写入变化明确重试。成本为合格ID SQL扫描与 `ceil(Nqual/1000)` 级RPC（hybrid两路），大范围条件需要任务化或明确超时/不完整状态，不得偷偷截断。

固定JSON metadata列并非最小安全实现：Milvus JSON数字类型、缺失与null、结构对象比较、嵌套数组以及精确Decimal必须先证明与公共PG语义等价。没有等价证明前，不应以其为由删掉AST叶子或把不支持条件放宽。事实多来源筛选可作为PG资格谓词的assertion join扩展，必须使用全部合法来源并核对来源版本及当前治理状态。

## 未被本次证明的质量

本审计代理没有自行执行测试；主代理已统一验证上述35项契约。仍没有真实ANN/BM25召回率、MRR/nDCG、模型答案忠实性、15万实体并发延迟或完整图浏览器绘制结论。资格门禁广泛匹配时的RPC次数、读快照时长和并发资源尚无真实容量证据。native与资格批次当前采用的候选深度不同，混合排序是否满足相同质量目标也需固定真实查询金标后验证，不能因都叫RRF就假定排名等价。

许多旧parity测试仍调用已移除的 `projection_readiness` / `search_dense` / `search_hybrid` 接口；旧API测试常假设无Milvus也自动本地检索。应更新测试适配器和ready索引夹具，不应在生产恢复隐式fallback来迁就旧假设。小桩测试只能证明分支与边界，不能替代真实质量/规模验收。

## 建议最小扩跑集合（由主代理串行，不由本审计代理启动）

1. **排名与完整谓词**：`test_native_page_retrieval.py`、`test_historical_keyword_sql.py`、`test_milvus_business_filters.py`；`test_retrieval_channel_parity.py`只选仍有效的PG AST/分页/端点/版本ID流式读取用例，跳过已废弃接口桩并先更新它们。
2. **冻结证据与关系图**：`test_canonical_evidence_contract.py`、`test_temporal_reference_provenance.py`、`test_answer_prompt_graph.py`。后者整链夹具需要真实ready pin协议；纯prompt投影用例可先跑，不能为旧无索引夹具回退生产逻辑。
3. **来源与邻域**：`test_canonical_source_context.py`、`test_local_graph_scoped_reads.py`、`test_graph_progressive_session.py`；当前修改的source scope集成文件必须同时进入最终回归。
4. **实验生命周期**：`test_retrieval_experiments.py`，先给旧mock Repository添加固定ready索引pin契约，保留owner隔离、取消/重试、manifest不可变、输入改变失效及真实失败不得伪造指标的断言；新增pin链测试补充而非取代这些生命周期用例。
5. **固定schema接缝**：`test_fixed_milvus_fields.py`，确认任意metadata恢复没有重新产生bf列、改变Milvus固定schema或在keyword请求启动编码器。
