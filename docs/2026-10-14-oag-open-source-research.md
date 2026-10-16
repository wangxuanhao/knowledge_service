# OAG 开源能力对照

调研日期：2026-10-14。范围：官方文档、官方 GitHub README/发布说明和部分源码静态核对；未部署这些框架或复现其成绩。本文是开源对照，当前项目代码结论见主分析。

## 先明确 OAG 的口径

这里将 Ontology-Augmented Generation 理解为：领域类型、属性、关系及规则真正参与数据构建、查询和答案生成，而不是仅存了一份 OWL 文件或给实体加类型。这个判断口径是工程分析框架，不是统一行业标准。

Palantir 官方 Ontology 还包含 Functions、Actions 和动态安全，覆盖数据、逻辑、业务行动。其单独的 OAG 文档却主要讲 chunk 对象上的检索、HyDE、关键词、查询扩展及混合搜索。因此不能断言所有称为 OAG 的系统都必须有 OWL 推理器，也不能把“本体问答”和“完整业务操作平台”混为一谈。[Ontology overview](https://www.palantir.com/docs/foundry/ontology/overview)、[OAG 检索文档](https://www.palantir.com/docs/foundry/ontology/ontology-augmented-generation)

## 项目对照

| 项目 | 实际侧重 | 值得参考的能力 | 判断边界 |
| --- | --- | --- | --- |
| OpenSPG / KAG | 专业领域知识构建、检索与混合推理 | Schema 约束抽取；实体/属性规范化和语义对齐；图与 chunk 互索引 | Schema-free 和 schema-constrained 两种方式并存，需检查业务实际启用配置，不能把所有 KAG 查询都称作本体推理 |
| microsoft/ograg2（OG-RAG） | 本体约束的内容映射和上下文检索 | JSON-LD 本体映射；Hypergraph 表示；相关上下文组合；RAGAS 评估脚本 | 主要是知识检索研究实现，未据此证明具有完整业务规则推理或 Actions 平台 |
| Microsoft GraphRAG | 从文本抽取实体关系、社区摘要和图检索 | Local 混合实体关系与原始文本；Global 对社区报告 map-reduce；DRIFT 结合社区线索展开后续问题 | 图扩展/问题分解可以改善多跳回答，但不自动等于本体类型检查或可执行符号规则 |
| HKUDS/LightRAG | 图与向量结合的轻量知识检索 | 来源引用、返回检索上下文、RAGAS、Langfuse、文档删除后图更新 | 官方 README 所核对的检索能力不足以证明一般 OWL 语义或本体规则执行 |

来源：[KAG README](https://github.com/OpenSPG/KAG)、[OpenSPG](https://github.com/OpenSPG/openspg)、[OG-RAG](https://github.com/microsoft/ograg2)、[GraphRAG Local](https://microsoft.github.io/graphrag/query/local_search/)、[GraphRAG Global](https://microsoft.github.io/graphrag/query/global_search/)、[GraphRAG DRIFT](https://microsoft.github.io/graphrag/query/drift_search/)、[LightRAG](https://github.com/HKUDS/LightRAG)。

## 可以复核的源码与能力

### KAG：重点参考规划和执行分层

官方 0.7 发布说明描述 Static / Iterative Planner 将问题分成有依赖的 Executor DAG，提供 `kag_hybrid_executor`、`math_executor` 和 `cypher_executor`，并按 Schema 约束知识、Schema-free 知识、原始上下文分层检索。评测提供 TwoWiki、HotpotQA、MuSiQue、AffairQA / PRQA，以及 EM、F1 和 LLM Accuracy。成绩属于项目发布者报告，本次未复现。[0.7 发布说明](https://github.com/OpenSPG/KAG/releases/tag/v0.7)

源码静态核对固定到 `fdab15b3929d2ee40dfcdd388f90233096a6afc9`：

- [SchemaConstraintExtractor](https://github.com/OpenSPG/KAG/blob/fdab15b3929d2ee40dfcdd388f90233096a6afc9/kag/builder/component/extractor/schema_constraint_extractor.py)：读取项目 Schema，处理实体、属性、关系和 chunk 图连接。
- [KAGStaticPlanner](https://github.com/OpenSPG/KAG/blob/fdab15b3929d2ee40dfcdd388f90233096a6afc9/kag/solver/planner/kag_static_planner.py)：独立的查询改写、任务规划和完成判断接口。
- [PyBasedMathExecutor](https://github.com/OpenSPG/KAG/blob/fdab15b3929d2ee40dfcdd388f90233096a6afc9/kag/solver/executor/math/py_based_math_executor.py)：LLM 生成 Python，子进程执行并根据错误重试。可借鉴执行层接口；不能据此认为它是形式化数学证明器或安全沙箱。
- [基准目录](https://github.com/OpenSPG/KAG/tree/fdab15b3929d2ee40dfcdd388f90233096a6afc9/kag/open_benchmark)：实际位于 `kag/open_benchmark`，不要仅按旧发布说明的“根目录 open_benchmark”寻找。

KAG 0.7 还公开了来源关联和图索引展示；可用作证据产品形态参考，但“能引用来源”不能替代逐个事实验证。[KAG README 发布说明](https://github.com/OpenSPG/KAG#3-release-notes)

### OG-RAG：重点参考属性进入上下文

其配置显式指定 ontology_path 和 `ontohypergraph-rag`，评测配置包含正确性、忠实度、相关性及 context precision / recall。源码核对固定到 `768048b92bd74049d2c64627fab7327f6d5ff2bd`：[README](https://github.com/microsoft/ograg2/blob/768048b92bd74049d2c64627fab7327f6d5ff2bd/README.md)。

- [OntologyMapping](https://github.com/microsoft/ograg2/blob/768048b92bd74049d2c64627fab7327f6d5ff2bd/ontology_mapping/src/ontology_mapping.py)：把文档按本体上下文映射为 JSON-LD。
- [Ontograph query engine](https://github.com/microsoft/ograg2/blob/768048b92bd74049d2c64627fab7327f6d5ff2bd/query_engine/ontograph_query_engine.py)：存在 HyperNode / HyperEdge / OntoHyperGraph，节点以 key/value 表示属性，组合后形成上下文。
- [Knowledge graph generator](https://github.com/microsoft/ograg2/blob/768048b92bd74049d2c64627fab7327f6d5ff2bd/knowledge_graph/knowledge_graph.py)：处理嵌套属性与三元组。

因此对本项目最有价值的借鉴是“属性和值、关系语义以及来源共同进入生成证据”，不是简单增加遍历跳数。此处为基于源码结构的建议，非本次性能测试结果。

## 当前项目应检查/补齐的能力清单

结合主分析已经查到的静态代码事实，优先关注以下差距：

1. **答案证据结构**：把正式实体属性及其数据类型、单位、时间范围纳入 evidence；保留关系方向、谓词、来源 chunk / span、本体版本。仅返回 chunk 文本和 citation 会丢失图中最有价值的结构语义。
2. **本体进入查询计划**：将问题绑定到类型、属性和关系，形成可校验计划；按谓词和方向约束路径，支持过滤、聚合、排序、比较和跨实体 join。无向 BFS 邻域不能称为执行了逻辑推理。
3. **执行层**：把图查询、本地 SPARQL、数值/日期计算和规则执行接到 QA 主链路，并保留每一步输入、输出和证据。先做可验证算子再扩展复杂 LLM 规划。
4. **生成验证**：向 LLM 提供结构化事实和约束，核对结论是否受证据支持；对证据不足、矛盾或查询无解保留明确状态。Schema 校验和 SHACL 中 RDFS 推断不能替代查询时的规则执行。
5. **基准与可观测性**：建立同一数据和模型下的普通 RAG / 图检索 / 本体检索消融；覆盖关系反向、实体属性、多跳依赖、数值聚合、缺失信息、规则冲突；记录答案与证据准确性、规划成功率、约束违反率、延迟和 token。
6. **治理与生命周期**：本体版本变化后的索引失效、重新构建、迁移；事实更新和删除传播；来源权限同步到检索/证据/答案。若目标是 Palantir 式业务 OAG，再另增具类型约束的 Action、权限校验、审批、幂等及审计闭环。

这些是针对当前已观察主链路的工程建议；是否已有其他入口或部署插件补齐，需要主分析继续核查，不应仅凭文件名或目录存在判定端到端实现。

## 不确定项

- `ontology-driven.dev` 搜索未找到可核实的官方仓库，直接访问工具返回失败；本次不把它列入已验证对照。
- 框架称自己支持推理，不等于所有配置都执行符号推理；需要在本项目同一领域数据上运行验证。
- LightRAG 的工程能力按当前官方 README 核对；没有对所有 prompt 和扩展插件做穷尽审计，因此用“未证明本体规则执行”，不宣称绝对不存在。
- OG-RAG README 的安装命令仍含占位 GitHub 地址，落地前需检查依赖、样例配置和维护状态。
