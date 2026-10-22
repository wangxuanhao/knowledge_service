# 检索完整性与 15 万实体规模审计

日期：2026-10-19。源码基线：`724c10d039595a8276c4fb5f7ae8ee26ee0aafa2`。本次以当前磁盘源码为准；MCP 图谱用于发现符号和调用链，其旧行号已经与磁盘核对。只新增本研究文档，没有改产品代码、数据或运行配置。

## 判断

**现有应用的检索入口、三种检索模式、范围治理和证据问答已经形成可用链路；不能据此认定“检索能力完整”，也不能认定已达到 15 万实体全库检索与 15 万实体同屏图谱的目标。** 功能保留、算法覆盖、召回正确性、运行可用性和规模性能必须分别验收。

| 维度 | 当前结论 | 主要依据 |
|---|---|---|
| 既有功能保留 | 三通道、模式开关、本体扩展、适用范围分组、原文/证据入口、图谱探索均有实现；跨后端仍有行为差异 | `service.search`、`RetrievalEngine`、`workspace.js`，详见下文 |
| 算法覆盖 | 具备 BM25/词法、dense、融合和类层级扩展；正常 QA 分组链路没有关系多跳证据 | [检索后端](../../knowledge_service/services/retrieval.py#L190-L281)、[QA 分组提前返回](../../knowledge_service/services/answers.py#L306-L359) |
| 召回与正确性 | 有范围先于排名、版本回填和问答资格核对；通道配额不一致，尚无本次基线的全库相关性金标结果 | [范围选择](../../knowledge_service/services/retrieval.py#L323-L345)、[配额处理](../../knowledge_service/services/retrieval.py#L253-L259)、[资格核对](../../knowledge_service/services/answer_validity.py#L218-L310) |
| 运行可用性 | 有投影就绪门控和回退代码；本次未检查真实服务、索引覆盖、模型或数据库健康，不能声称现场语义检索可用 | [就绪检查](../../knowledge_service/services/retrieval.py#L175-L188)、[Milvus available](../../knowledge_service/integrations/milvus_store.py#L131-L133) |
| 15 万规模 | 当前范围快路径已避免全项目预载；复杂/历史/扩展回退与完整图仍有全范围开销，缺少目标规模报告 | [Milvus 小量回填](../../knowledge_service/services/retrieval.py#L190-L196)、[权威回退](../../knowledge_service/services/retrieval.py#L355-L399)、[完整/局部图读取](../../knowledge_service/services/explorer.py#L133-L215) |

这里的 15 万是**实体数**。容量数据集还必须声明关系数、属性数、文档数、切片数、历史版本数、向量维度、文本长度和度分布；15 万条混合记录不等于 15 万实体。

## 已有检索能力及必须保留的语义

1. **接口与模式。** `/api/projects/{project_id}/search` 和 `/qa` 分别调用主服务；支持 `keyword`、`semantic`、`hybrid`。搜索查询最长 10,000 字符、`k` 为 1–100，三通道上限各 0–50；问答支持 JSON 与共用上下文的 SSE、证据模式和可选模型生成。[请求契约](../../knowledge_service/models.py#L83-L128)、[HTTP 接线](../../knowledge_service/api/knowledge.py#L142-L148)、[JSON 问答](../../knowledge_service/services/service.py#L1228-L1282)、[SSE](../../knowledge_service/services/answers.py#L546-L627)
2. **内容通道。** 搜索排名支持实体、关系、原文切片；文档和属性是其他读取/图谱能力，并非独立检索通道。共享 `Scope.kinds` 虽允许 `document/attribute`，`RetrievalEngine` 对这两类通道会拒绝，不能把属性台账可见误称为属性全文/语义检索已覆盖。[共享模型](../../knowledge_service/models.py#L83-L91)、[支持通道校验](../../knowledge_service/services/retrieval.py#L283-L319)、[图谱属性模式](../../knowledge_service/services/explorer.py#L178-L202)
3. **当前 Milvus V2 快路径。** project、active、kind、平台/业务/人群及显式业务时间在 Milvus 内先过滤，再查询 dense/BM25，最后只按命中的 `version_id` 向 PostgreSQL 回填仍为 current 的版本；没有在线传全量 `candidate_ids`。集合有中文 analyzer、BM25 sparse、HNSW dense 及范围/时间标量索引。纯关键词路径不调用 embedding。[快路径](../../knowledge_service/services/retrieval.py#L190-L251)、[过滤表达式](../../knowledge_service/integrations/milvus_store.py#L748-L803)、[物理索引](../../knowledge_service/integrations/milvus_store.py#L288-L343)、[current 回填](../../knowledge_service/repository/core.py#L1257-L1278)、[禁止关键词编码回归](../../tests/service/test_oag_performance.py#L38-L48)
4. **本地权威回退。** 先读取 PostgreSQL 范围，再分别关键词/向量排名。当前关键词为 `pg_trgm` 子串/英文全文索引，加 Python 词法兜底；关系关键词靠词法路径。显式 `known_at` 用历史可见版本的词法扫描；中文问题精确命中失败时还会尝试较低权重的字符对匹配。向量回退用精确余弦，校验模型标识、维度和有限值；semantic 不可用时可退关键词。[关键词实现](../../knowledge_service/repository/core.py#L967-L1024)、[词法兜底](../../knowledge_service/services/retrieval.py#L114-L137)、[当前/历史关键词分支](../../knowledge_service/services/retrieval.py#L439-L471)、[精确余弦](../../knowledge_service/services/retrieval.py#L33-L73)、[逐通道降级](../../knowledge_service/services/retrieval.py#L473-L519)
5. **本体扩展。** 显式开关默认关；命中类的 label/name 后，通过祖先闭包找全部子类，以实体 type 和扩展关键词 OR 补召回。同条关键词记录取最高分而非累加；扩展仍受候选范围约束。匹配为至少两个字符的子串/最长匹配，命中类及扩展关键词各有 `max_terms=8` 预算，子类类型集合本身未设同样上限。服务使用最新发布本体，缓存随版本签名失效；历史/指定旧本体的查询扩展并不是历史本体推理。[本体索引](../../knowledge_service/services/ontology.py#L262-L307)、[匹配与预算](../../knowledge_service/services/ontology.py#L107-L156)、[服务缓存](../../knowledge_service/services/service.py#L509-L531)、[OR 合并](../../knowledge_service/services/retrieval.py#L140-L157)、[类型补召回](../../knowledge_service/services/retrieval.py#L427-L432)
6. **范围语义。** 查询文本只表达相关性，不自动转为平台过滤。显式选平台时每个平台搜 specific，并独立搜通用 all；未选平台时做一次全局排名，再按结果 metadata 分组，保持全局相关性顺序。显式选平台不会带入平台未知资料。最多取前 20 个平台，平台查询最多 4 并行，共享一次查询编码和权威回退缓存。[范围计划](../../knowledge_service/services/service.py#L533-L625)、[无隐式范围限制回归](../../tests/service/test_unrestricted_retrieval_scope.py#L5-L40)、[平台分组回归](../../tests/service/test_native_qa_grouping.py#L97-L134)
7. **时态与来源资格。** 搜索未指定业务时点时允许返回已失效和未来资料；QA 冻结业务/系统时点，再把正式、历史、未来、未知和结构待定材料区分，核对来源文档版本与适用范围。结构待定可查可看原文但不作为正式证据；E/R/F/U 引用和冻结来源链是已有价值，必须保留。[搜索无隐式时点回归](../../tests/service/test_unrestricted_retrieval_scope.py#L43-L70)、[冻结时点](../../knowledge_service/services/answer_validity.py#L9-L17)、[可见性核对](../../knowledge_service/services/answer_validity.py#L35-L59)、[时态/来源资格](../../knowledge_service/services/answer_validity.py#L62-L162)、[冻结证据](../../knowledge_service/services/answers.py#L437-L470)
8. **筛选与权限边界。** 支持嵌套 and/or、类型化比较、存在性、包含、时间和 metadata 路径；本体范围还能去除悬空关系/属性。现有认证为 Bearer + 账号复查 + 全局 viewer/admin/superadmin 读写策略，检索/问答/图谱是只读 POST 白名单。project_id 是存储隔离键；当前 search 路由不传用户主体，不能将平台适用范围描述为逐用户/逐文档 ACL 已实现。[过滤器](../../knowledge_service/utils/filters.py#L8-L47)、[范围与悬空事实](../../knowledge_service/services/service.py#L375-L421)、[角色白名单](../../knowledge_service/api/security_middleware.py#L42-L76)、[认证授权](../../knowledge_service/api/security_middleware.py#L139-L191)、[search 路由](../../knowledge_service/api/knowledge.py#L142-L144)
9. **可操作入口。** 检索结果分匹配实体、原文片段、关系链路；支持查看来源、实体/关系定位、扩展解释和降级提示。选中详情与显式展开图谱是独立操作，搜索不应隐式覆盖当前图；QA 还显示证据、来源、适用范围、推理路径入口。[结果操作](../../knowledge_service/web/workspace.js#L402-L439)、[检索状态](../../knowledge_service/web/workspace.js#L440-L480)、[独立图谱操作](../../knowledge_service/web/workspace.js#L490-L538)、[QA 证据入口](../../knowledge_service/web/workbench.js#L557-L745)

## 当前必须正视的完整性缺口

### P0：正常 QA 链路没有关系多跳证据

`_retrieved_question_context` 固定用 `kinds=['entity','chunk']` 搜索。`service.search` 总会构造非空 `platform_groups`：显式范围含 specific/all；无范围即使没有命中也放入 unrestricted 组。QA 一旦有 groups 就提前返回，明确设置 `reasoning_paths=[]`、`channels.graph_evidence=0`。下方 0–3 跳、最多 200 条关系的 BFS 位于这个 return 之后，正常真实服务链路不会进入；参考候选也只搜 entity/chunk，不能补回关系通道。[QA 输入](../../knowledge_service/services/answers.py#L266-L292)、[必有分组](../../knowledge_service/services/service.py#L598-L639)、[提前返回](../../knowledge_service/services/answers.py#L306-L359)、[后置 BFS](../../knowledge_service/services/answers.py#L361-L419)、[参考通道](../../knowledge_service/services/answer_validity.py#L165-L196)

因此，“能浏览图/找路径”“有旧 BFS 函数”“参数接受 hops”均不能证明当前 QA 已完成 GraphRAG 的多跳证据获取。需要在分组路径内真正执行受范围、时点、方向和预算约束的关系检索，并把关系及端点/来源版本纳入资格核对与冻结。当前也没有在这条排名链路中实现社区摘要全局检索、查询分解/多跳计划、神经重排或自适应补检索；这些属于目标算法覆盖，而非保留旧按钮即可达成。[实际排名分支](../../knowledge_service/services/retrieval.py#L434-L596)、[当前 QA 主链](../../knowledge_service/services/answers.py#L266-L434)

### P0：原生快路径与本地路径的三通道配额/总 k 不一致

Milvus 对全部 requested kinds 只搜一次跨通道 `k`，回填后才分别截取通道配额，最后再次 `[:k]`。被实体挤出 top-k 的原文/关系没有机会补取；配额为 0 的通道也仍可消耗前面的 Milvus top-k。相反，本地先按通道融合/截配额，并没有在该分支最终截全局 k；既有预算测试明确期待默认 `k=10` 时返回三通道各 5、总 15 条。[原生行为](../../knowledge_service/services/retrieval.py#L225-L259)、[本地行为](../../knowledge_service/services/retrieval.py#L499-L557)、[现有预算契约](../../tests/service/test_search_budgets.py#L37-L51)

本次做了一次**无数据库、无产品模块导入、无模型/网络**的内存验证：从当前 `retrieval.py` 的 AST 提取类/纯函数，提供只读仓储与 Milvus 桩。数据为 20 个实体、5 个 chunk、5 条关系；Milvus 桩按统一相关性列表返回前 k 个，实体排在前面。相同 `keyword, k=10, content_k={entity:5, relation:5, chunk:5}` 的实测输出：

```json
{"native":{"hits":5,"counts":{"entity":5,"relation":0,"chunk":0},"candidate_count":10},"local":{"hits":15,"counts":{"entity":5,"relation":5,"chunk":5},"candidate_count":30}}
```

这是算法分支差异复现，不是 Milvus ANN 质量或规模测试。下一步必须明确 `k` 与独立通道预算的统一契约，再让快路径与回退同时满足；不能以修复性能为由静默删除任一原有通道。

### P1：融合算法、计数及退化状态尚未统一

- 本地融合是 RRF（constant=60）；Milvus `search_hybrid` 的默认参数为 `dense_weight=0.5`，实际走 `WeightedRanker(0.5,0.5)`，只有 weight 不大于 0 才走 RRFRanker。调用方没有覆盖默认权重。函数/注释中的“原生 RRF”不是实际默认行为，应测试相关性差异，而不能假定后端切换排名等价。[本地 RRF](../../knowledge_service/services/retrieval.py#L91-L111)、[调用](../../knowledge_service/services/retrieval.py#L225-L228)、[原生融合](../../knowledge_service/integrations/milvus_store.py#L883-L901)
- `candidate_count` 在 native 是返回的 top-k version ID 数，在 local 是整个候选集数；UI 都显示为“候选”。二者无法直接比较，更不能用它计算召回覆盖率。native hybrid 的 keyword/semantic hit 计数还固定为 0，不能当作两路真实贡献统计。[native 返回](../../knowledge_service/services/retrieval.py#L261-L280)、[local 返回](../../knowledge_service/services/retrieval.py#L558-L573)、[UI 计数](../../knowledge_service/web/workspace.js#L473-L480)
- native RPC 失败仅记录日志并落入本地路径，没有在该 catch 中保存 Milvus 错误；若本地关键词成功且模式不变，最终可能仍 `degraded=False`。投影 stale 有单独明确标记，但传输故障与 stale 的可见性并不等价。需要区分“策略不支持而回退”“索引未就绪”“服务故障”“语义降级”。[异常处理](../../knowledge_service/services/retrieval.py#L323-L345)、[本地状态](../../knowledge_service/services/retrieval.py#L558-L581)

### P1：native 的来源关联与前后文补充存在行为空洞

纯 native 的 `service.search` 直接设 `rows=[]`；随后来源 chunk 关联只从 rows 建索引，`adjacent_chunks` 也只从 rows 取锚点和邻居，因此 native 模式不会生成这两类补充。直接命中的 chunk 仍可返回，QA 仍有独立冻结/溯源机制，不能据此说“所有来源丢失”；具体缺口是**搜索里的命中事实来源原文与相邻条款补充**不具备和本地回退相同的覆盖。[空 rows](../../knowledge_service/services/service.py#L690-L691)、[来源补充](../../knowledge_service/services/service.py#L705-L737)、[相邻补充](../../knowledge_service/services/service.py#L738-L749)、[邻居条件与最多 12 条](../../knowledge_service/services/retrieval_context.py#L7-L58)

已有测试覆盖 mixed backend 复用 fallback rows 得到来源/邻居，却不能证明纯 native 也具备补充能力。应以有界 source/chunk ID 批量读取补齐，并保留同来源版本、同适用范围及邻居预算；不要恢复全项目预载。[mixed 回归](../../tests/service/test_native_qa_grouping.py#L326-L375)

## 15 万实体全库检索的真实风险

| 路径 | 当前行为 | 目标规模必须解决/验证 |
|---|---|---|
| 当前、原生支持范围、投影 ready | Milvus filtered ANN/BM25 + 小量 current 回填 | 证明索引包含全部符合条件的实体/关系/切片，而非只搜可见画布；检验 top-k 通道一致性、ANN 对受过滤精确余弦基线的 recall、写入后投影追平与并发回填版本变化 |
| 指定本体范围、复杂 filters、显式 known_at、命中本体扩展 | 不满足 native_scope，读取权威范围并在 Python 处理；本体 scope 预载 `service.scoped` | 不得把这些既有功能移除来换速度；应下推可索引条件、按 ID 有界读取/排名，或者提供清晰可衡量的慢路径预算 |
| 本地关键词 | FTS 虽有 LIMIT，调用前已形成完整候选；每次查询仍对所有候选做词法兜底，并把该类全部候选 ID 展成 SQL IN 占位符 | 15 万实体候选会生成约 15 万 ID 绑定，存在语句/参数开销及驱动协议上限风险；用有界/数组/关系式范围方案验收，不能把 SQL 最终 LIMIT 当作全请求有界 |
| 本地 semantic/hybrid | 缺向量时按全部候选 version ID 批取 Milvus 向量，然后构造完整矩阵、精确余弦与排序 | 时间至少随候选数和维度增长；150k×1024×4 的原始 float32 数据约 586 MiB，尚未含列表、复制、文本、矩阵临时量或多请求；这是尺寸计算，不是实测峰值 |
| 本体扩展 | 至多 8 个词/类命中，但全部子类实体集合可很大；扩展词重复关键词/词法排名 | 扩展父类接近全库时测延迟/内存及相关性，限定成本不得破坏“父类召回子类实例” |
| QA 参考补检索 | 数据库先关键词匹配，再按稳定 ID/版本取最多 501；复杂 metadata 和相关性排序在后 | 可能在相关性/复杂筛选前截断，只能声明有界参考覆盖；历史/未来/未知资料的稀有证据不能用小样本成功推断齐全 |
| 显式多平台 | 前 20 个平台、最多 4 workers，随后通用组；共享编码/回退缓存 | 固定组预算不等于完整跨平台目录；大候选排名仍可能重复，需测高频词及回退并发的资源与锁等待 |

来源：[native eligibility/回退](../../knowledge_service/services/retrieval.py#L323-L399)、[本体范围预读](../../knowledge_service/services/service.py#L563-L567)、[关键词候选/兜底](../../knowledge_service/services/retrieval.py#L437-L471)、[IN 展开](../../knowledge_service/repository/core.py#L999-L1016)、[范围查询实际读取与过滤](../../knowledge_service/repository/core.py#L1347-L1452)、[全部候选向量/排名](../../knowledge_service/services/retrieval.py#L479-L510)、[矩阵构造](../../knowledge_service/services/retrieval.py#L33-L73)、[501 参考预算](../../knowledge_service/services/answer_validity.py#L165-L196)、[参考 SQL LIMIT](../../knowledge_service/repository/core.py#L1417-L1438)、[平台执行计划](../../knowledge_service/services/service.py#L553-L608)。

仅针对当前投影快路径说“已不再全项目预载”是准确的；将它扩展为“所有检索都已无全范围成本”不准确。反过来，沿用旧审计说“所有搜索都先读全项目”也不符合本基线。

## 15 万实体同屏图谱必须独立验收

用户要求的是**全库搜索和同屏完整图两者都要**。检索返回 top-k 与局部邻域不等于完整图加载；局部展开/聚合/采样可以保留为操作模式，但不能代替 150k 真实实体的同屏验收。

当前 Neo4j 原生图快路径限有种子点、0–2 跳、支持的当前范围、投影 schema 3 ready；未指定种子、复杂 filters/本体范围/类型/谓词/历史或更高 hops 会退 PostgreSQL。回退先读整个 scoped 实体/关系范围，再按边扫描展开；无种子请求直接输出整个范围。`/graph` 也直接返回全范围实体/关系。[Neo4j eligibility](../../knowledge_service/services/explorer.py#L133-L166)、[回退/展开](../../knowledge_service/services/explorer.py#L168-L215)、[全图 API](../../knowledge_service/api/knowledge.py#L150-L157)

主画布当前一次映射全部 nodes/edges 到 ECharts graph，位置由主线程遍历生成；现有优化包括静态布局、缓存位置、帧合并平移和避免无用 redraw。它们有价值，但没有证明全库传输、JSON 解析、全部图元/标签、类型筛选和命中定位在 15 万实体上可用。[生产渲染](../../knowledge_service/web/workspace.js#L344-L392)、[布局位置](../../knowledge_service/web/graph-workbench.js#L14-L31)、[帧合并平移](../../knowledge_service/web/graph-workbench.js#L170-L211)。更完整的同屏与交互审计见[同日图规模报告](2026-10-19-graph-scale-and-ui-parity-audit.md)。

两类容量报告应使用同一明确快照：搜索报告证明查询覆盖完整语料且保持相关性/范围；完整图报告证明 150k 实体和声明的关系集合实际完整加载与绘制，并测首次可交互、全部加载、主线程长任务、内存、拾取/平移/缩放/搜索定位和编辑后更新。不能只证明 API 返回 100 条结果快或某节点 2 跳快。

## 现有证据能证明什么

- 有明确的行为回归：范围先排名、逐后端退化、三通道独立配额、历史关键词、复用候选、配额防互相挤占；本体扩展开关关闭等价、父类/子类与禁用类；平台显式/无范围计划、current 版本回填和未知范围隔离。**本次只读了源码，没有重跑这些测试，不能把文件存在记作当前全部通过。** [混合检索测试](../../tests/service/test_hybrid_retrieval.py)、[本体扩展测试](../../tests/service/test_ontology_query_expansion.py#L53-L225)、[原生过滤/回填测试](../../tests/service/test_milvus_v2.py#L36-L135)、[平台测试](../../tests/service/test_native_qa_grouping.py)
- `test_search_budgets` 的数据是 60 实体 + 60 chunk + 30 关系，约束一次权威 query、<0.75 秒、<100KB、三通道各 5。该证据不能推出 150k 全库性能，且它测的是本地配额契约。[实际数据及断言](../../tests/service/test_search_budgets.py#L9-L51)
- 已有 10,001 **条记录**、只解码 1 个稀有词命中的参考查询报告：仓储调用 408.10ms，后续 EXPLAIN SQL 执行 5.121ms。那是参考候选 SQL，不是 10,001 实体全量 hybrid、15 万实体检索或完整图；报告自身也明确没有生产容量/并发 SLA。[原报告](2026-10-16-oag-performance-validation.md#L36-L59)、[原始结构化结果](../../.benchmarks/oag-scale-final.json)
- 现有图交互 benchmark 默认 500 节点、每节点 3 条边，测 pan/mount/zoom/relayout；没有数据库或用户会话。这不能替代端到端同屏 150k。[脚本数据与渲染](../../scripts/benchmark_graph_interaction.py#L26-L43)、[默认规模](../../scripts/benchmark_graph_interaction.py#L93-L100)、[检查范围](../../scripts/benchmark_graph_interaction.py#L167-L188)
- `/evaluate` 当前对抽取出的实体集合/关系集合算 precision/recall/F1；它没有按查询和排名计算证据 Recall@k、MRR 或 nDCG，不能把抽取 PRF 当作检索质量验收。[实际 evaluator](../../knowledge_service/services/explorer.py#L362-L371)
- 旧真实模型/HTTP 报告是小样本、旧运行状态；本次没有启动服务、加载本地向量模型、调用外部模型、执行产品数据库测试或跑 150k 负载。此次新增的可执行验证仅为上面的内存桩配额复现。

## 必须保留的最小验收清单

- [ ] keyword/semantic/hybrid 三模式和实体/关系/chunk 三通道保留；原生与回退统一 `k`/配额契约，零配额不挤占其他通道，稀有通道不被全局 top-k 静默丢弃。
- [ ] 全库查询不依赖画布已加载节点或候选分页；未显式设置 metadata/时点时保持全局相关性检索，显式平台/业务/人群/来源/本体/复杂过滤仍先约束候选；不存在项目或范围越界结果。
- [ ] 当前/历史 known_at、半开业务有效期、unknown、deleted/audit、悬空关系/属性行为保持；过期/未来/未知能在规定参考区展示，不能用于错误的当前有效结论。
- [ ] 本体扩展默认关闭等价；开启能召回文本无父类词的子类实例，停用类不扩展，解释台账可见，范围不因扩展放宽。
- [ ] JSON/SSE 使用同一检索、资格和冻结链；平台比较不串证据；结构待定可见且不当正式证据；E/R/F/U 来源版本与已提供引用一致。
- [ ] 分组 QA 真实召回关系和多跳路径，并验证 hops、方向、端点/关系/来源资格；无关系证据时如实显示，不能仅因有路径组件就判通过。
- [ ] native 与 local 都有命中事实来源原文及合规邻接上下文；保持来源版本/范围及 12 条补充预算，且无全库预载恢复。
- [ ] 保留搜索、原文、详情、图谱定位、显式展开、类型/关系/属性筛选、路径、历史和证据动作；搜索/图谱/详情状态及旧请求失效机制不互相覆盖。
- [ ] ready、stale、未配置、连接失败、维度/模型不匹配逐项验证；关键词无需向量模型，真实降级原因对 API/UI 可见，用户角色读写边界保留。
- [ ] 在固定 150k **实体**及声明的额外记录规模上，分别提交全库相关性/范围/时态评测、冷/热/并发性能和完整图真实绘制/交互报告；隔离压测并约定硬件/阈值。小库、SQL LIMIT、局部图或抽取 PRF 均不能替代。

优先次序：先冻结并修齐跨后端检索契约与正常 QA 多跳/来源补充；同时建立可复用的查询金标与范围负例；再按支持范围逐步消除大候选回退瓶颈，并把完整图作为独立必达工程项验收。此处是待实施建议，不是已完成实现或性能承诺。
