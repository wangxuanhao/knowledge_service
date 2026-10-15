# 市面上 NL2Ontology 与图谱推理的实际机制

调研日期：2026-10-15（Asia/Shanghai）。这是公开文档与源码调研，不是运行效果评测。Microsoft Fabric 相关功能仍标注 preview；不同产品、不同版本不能混用能力结论。

## 结论

实际产品通常把自然语言理解交给 LLM，把取数交给数据库和查询引擎。涉及本体时，本体为模型提供概念、属性、关系、数据绑定等语义背景。“让 LLM 理解问题”“执行多跳查询”“按正式规则推出新事实”是三种不同操作，不能统称为同一种推理。

Ontology Playground 的本地英文规则演示，不能代表以下产品的服务端实现。参见同目录的 `2026-10-15-ontology-playground-source-analysis.md`。

## 1. Microsoft Fabric IQ：官方确认的链路

**官方事实：** Foundry Agent 可将自然语言问题作为工具调用发给 Fabric IQ。Ontology 的 NL2Ontology 层把问题转换为针对企业实体、关系和数据绑定的结构化查询，再将合成结果返回给 Agent；请求遵循连接身份对应的权限。[Microsoft Foundry：Connect agents to Microsoft Fabric with Fabric IQ，How it works](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq#how-it-works)

这可以确认“业务语言 → 本体语义 → 结构化取数 → 回答”，但该文档没有公开 NL2Ontology 服务端的完整提示词、模型权重、内部逻辑计划和纠错算法。不能把示例 Agent 配置的模型名当成 NL2Ontology 内部固定模型名。

**官方事实：** 当前 Ontology Agent 会按 grounding 选择查询引擎：

| 数据/问题类型 | 对应查询 |
|---|---|
| Eventhouse 绑定 | KQL |
| Lakehouse 绑定 | SQL |
| 语义模型内容 | DAX |
| 存在 GraphModel 的关系遍历 | GQL |

它展示执行的查询、有界结果与说明。这比“所有问题都直接变成图查询”更准确。[Use the ontology agent：Query the ontology](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-use-ontology-agent#query-the-ontology)

**重要边界：** 当前文档列明，通过父实体返回继承子实体实例的查询仍有已知问题；`ask_ontology` 当前不在回答中考虑 rules。不能据此宣称它已经完整执行 OWL 继承推理或自动应用所有业务规则。[同页：Current limitations](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-use-ontology-agent#current-limitations)

**是否涉及 LLM：** Ontology Agent 官方称为 AI-powered Copilot，文档明确涉及 Azure OpenAI 数据处理；Foundry 的外部 Agent 示例配置了模型。因此产品链路涉及模型。公开资料不足以还原 NL2Ontology 子模块每个步骤分别调用哪一个模型、几次，以及是否全部采用同一生成流程。

## 2. Fabric 的业务规则不等于已执行规则

**官方事实：** 自然语言业务规则可关联本体概念，并由只读 MCP 工具 `list_ontology_rules` 返回。官方明确：ontology 不执行这些规则，不自动据此对数据运行判定或动作；新体验也没有直接集成 Activator，需另行配置。[Define business rules：Retrieve business rules with ontology MCP；Limitations](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-use-rules#retrieve-business-rules-with-ontology-mcp)

因此，“规则已被写成文本”“Agent 能读取规则”“规则已被编译并确定执行”“业务动作已触发”必须分别核实，不能互相替代。

## 3. MCP 是工具接口，不是推理引擎

**官方事实：** Ontology 可通过专用 MCP endpoint 供外部应用和 Agent 连接；这是一条访问与工具调用通道。[Use ontology MCP server](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-use-ontology-mcp-server)

**架构解释：** MCP 负责让 Agent 发现并调用能力，数据库查询、身份校验、规则执行和模型推理发生在其客户端/服务端实现中。只增加 MCP 接口并不会自动获得 NL2Ontology，也不会自动获得规则闭包推理。

## 4. Neo4j Text2Cypher：能直接检查源码的实现

**源码事实：** 官方 Python GraphRAG 包的 `Text2CypherRetriever`：

1. 接收或读取图 schema，可配置问答/Cypher 示例和自定义提示词；
2. 将 schema、examples、用户问题填入 `Text2CypherTemplate`；
3. 调用 `self.llm.invoke(prompt)`；
4. 从模型输出提取 Cypher；
5. 当前公开源码先执行 `EXPLAIN` 检查 query_type，拒绝非只读查询；
6. 用 Neo4j driver 执行查询，返回 records，并在 metadata 中保存 cypher。

[官方源码文档：text2cypher，Text2CypherRetriever 与 get_search_results](https://neo4j.com/docs/neo4j-graphrag-python/current/_modules/neo4j_graphrag/retrievers/text2cypher.html)

这里模型生成查询，Neo4j 执行模式匹配、路径遍历和聚合。此 retriever 返回检索结果，本身不是完整的自然语言答案生成器，也没有在该函数中实现无限重试或普遍适用的规则推理。不要把其它 Agent 包装层可能添加的纠错闭环算作此类自身已有能力。

源码来源随 `current` 更新；本文描述的是调研日页面展示的版本，没有克隆或固定该包 commit，也未在本机执行。

## 5. GraphDB Talk to Your Graph：LLM + 多种检索工具

**官方事实：** Talk to Your Graph 使用 LLM 处理自然语言、调用配置的查询方法，再用 LLM 处理数据库返回值生成回答。模型需支持工具调用、多轮对话与 SPARQL 生成。

可组合的取数方式包括 SPARQL、全文检索、语义相似检索、ChatGPT Retrieval Connector。本体/schema 为 SPARQL 生成提供上下文；SPARQL 适合封闭问题和聚合，文本/向量检索更适合开放问题。界面可展示使用的查询方法和 SPARQL。[GraphDB：Talk to Your Graph，Model Requirements / Query methods](https://graphdb.ontotext.com/documentation/master/talk-to-graph.html)

因此，“市面上自然语言问图谱”也不一定每次都生成结构化图查询，可以在图查询与文本/向量检索间选择。不能用相似度检索直接代替精确计数。

## 6. GraphDB 的正式推理：独立于 LLM

**官方事实：** GraphDB 使用基于三元组规则的前向链推理，反复应用规则直到不再产生新事实，再存储物化结果。查询读取这些结果，并不要求每次让 LLM 重新演绎规则。[GraphDB 11.3：Reasoning](https://graphdb.ontotext.com/documentation/11.3/reasoning.html)

**官方实例：** 其 inference 文档展示通过 `owl:inverseOf` 从已知亲子关系推出反向关系，并用 proof 插件追踪规则及前提。是否能推出某种结论取决于配置的 ruleset 与正式规则，不是模型自由发挥。[GraphDB 11.4：Inferencing / Simple example with owl:inverseOf](https://graphdb.ontotext.com/documentation/11.4/inference.html)

这里引用版本固定为 11.3/11.4 的推理契约，Talk to Your Graph 使用调研日 master（页面标注 11.5）；没有据此假定不同版本所有功能完全一致。

## 7. 通用架构解释：四层协作，而非模型包办

以下是从这些实现归纳的可采用架构，不是任何单一厂商未公开内部实现的复述。

| 层 | 职责 | 通常由谁完成 |
|---|---|---|
| 语义解析 | “华东”“逾期”“影响客户”映射到实际概念、属性、过滤与时间范围 | LLM + 本体/词表/消歧 |
| 查询执行 | 按真实 ID 连接关系、遍历路径、过滤、计数 | 数据库/图引擎/业务服务 |
| 正式规则 | 根据已批准、可执行的规则推出派生事实或判定 | 规则引擎、RDF reasoner 或明确业务代码 |
| 结果解释 | 用执行结果和证据组织回答，展示路径与依据 | LLM + 受约束结果渲染 |

例：“哪些客户受到逾期订单影响？”——模型可先解释“客户、订单、逾期”的含义；数据库按客户—订单关系取出满足时限/状态条件的订单；如“受影响”需要额外业务定义，应使用显式规则或经用户确认的条件；最后再解释客户名单及其订单证据。不能因为模型能够写出一段解释，就认定规则已运行或结论已经落库。

若实现自己的系统，宜保留结构化计划、实际执行查询、结果记录 ID、证据路径、规则版本与时间条件，让用户能区分“已知事实、规则派生、模型建议”。这是工程建议，不是对当前仓库既有能力的断言。

## 8. 证据范围与未确认事项

完成官方产品文档及 Neo4j 官方渲染源码核对；没有调用这些付费服务、创建 Agent、连接数据源或运行性能/准确率评测。没有证据确认 Fabric NL2Ontology 完整内部算法、固定内部模型、私有提示词、精确召回机制或完整 OWL 推理支持。厂商使用“reasoning”字样时应进一步区分语义规划、图遍历、模型推断与正式逻辑蕴含，不能直接画等号。
