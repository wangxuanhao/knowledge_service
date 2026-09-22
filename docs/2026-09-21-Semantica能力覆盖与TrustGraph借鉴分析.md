# Semantica 能力覆盖与 TrustGraph 借鉴分析

调研日期：2026-09-21

> 范围：以当前工作区代码、当前 `llm_model` 环境中的 Semantica 0.6.8，以及截至调研日的 Semantica / TrustGraph 官方文档和官方仓库为准。只使用第一方资料；不把产品宣传语直接当成已经验证的工程事实。

## 0. 结论先行

1. **项目没有使用 Semantica 的全部能力，也不应该以“全部接入”为目标。** Semantica 0.6.8 官方列出 27 个可独立使用的模块；本项目生产代码直接依赖其中 4 个模块族：`semantic_extract`、`ontology`、`deduplication`、`context`，而且都是按本项目边界做的选择性接入。Semantica 官方本身强调模块独立、只导入所需能力，因此模块数量不是成熟度 KPI。[Semantica 模块总览](https://github.com/semantica-agi/semantica/blob/main/docs/modules.md) [Semantica 0.6.8 发布说明](https://github.com/semantica-agi/semantica/releases/tag/v0.6.8)
2. **“没有使用”不等于“缺能力”。** 输入、切片、双时态真相源、Milvus/Neo4j 投影、OWL/SHACL、审核、版本、检索、工作台和任务编排大多已由本项目实现。把 Semantica 的同类模块再并行接入，会制造两个真相源和两套生命周期。
3. **Semantica 最值得补的不是更多抽取器，而是三项治理能力：** 标准化 provenance、事实冲突检测、可重复的质量评测/本体质量门。推理可做后续小试验，前提是本体先拥有足够公理；存储、版本、工作台和流水线不建议替换。
4. **TrustGraph 有可取之处，但当前更适合作为设计参考而不是平台依赖。** 最值得借鉴的是“抽取 provenance + 查询时解释链”贯通、三类 named graph 隔离，以及可复用 flow blueprint / context bundle。当前项目已经具备大量重叠能力，直接部署 TrustGraph 会引入事件总线、对象存储、元数据存储和多容器运维，收益不足以覆盖迁移成本。[TrustGraph 架构](https://docs.trustgraph.ai/overview/architecture.html) [TrustGraph v2.8 changelog](https://docs.trustgraph.ai/changelog/trustgraph.html)

一句话判断：

> **Semantica：核心算法组件已经用到了，但只覆盖了整个框架的小部分；继续“挑缺口接入”，不要追求全家桶。TrustGraph：抄它的可解释性数据模型和运行契约，不要现在换平台。**

## 1. 事实基线

### 1.1 当前版本和代码边界

本机 `D:\anaconda\envs\llm_model` 实测安装 Semantica `0.6.8`。该版本于 2026-09-05 发布，新增了签名发布、跨多种向量库的枚举/迁移、更多 LLM provider、确定性的本体质量门等能力。[Semantica 0.6.8 发布说明](https://github.com/semantica-agi/semantica/releases/tag/v0.6.8)

当前项目以 SQLite 为双时态真相源，Milvus 和 Neo4j 是可重建派生索引/投影；服务还自带 OWL/SHACL、本体版本和审核工作台。这一权威边界在 [README](../README.md#L49-L58) 中已有明确约定。项目的 `semantica-runtime` extra 只列出了当前调用路径所需的一组传递依赖，没有声明 `semantica` 包本身，`uv.lock` 也没有锁定 Semantica；同时安装文档仍写 `semantica==0.6.7`，而当前实测环境为 0.6.8。这是部署复现性缺口，不是“最小依赖”优化，见 [pyproject.toml](../pyproject.toml#L16-L21)、[uv.lock](../uv.lock) 和 [KNOWLEDGE_SERVICE.md](KNOWLEDGE_SERVICE.md#L21-L22)。

生产代码对 Semantica 的直接调用为：

- `semantic_extract`：受控/开放实体关系抽取、typed provider、实体匹配；项目通过严格子类和自定义 prompt 禁用静默启发式回退，并在返回后做 IRI、domain/range 和审核检查，见 [semantica_adapter.py](../knowledge_service/integrations/semantica_adapter.py#L291-L431)。
- `ontology`：候选聚合后调用 `OntologyGenerator` 生成初始结构，再由本项目复用稳定 IRI、补 RDF/OWL 元数据并生成版本差异，见 [ontology_discovery.py](../knowledge_service/services/ontology_discovery.py#L318)。
- `deduplication`：用 `DuplicateDetector` 给候选排序，用 `EntityMerger` 生成合并建议；是否允许合并、版本检查、关系重写和可撤销账本仍由服务层负责，见 [governance.py](../knowledge_service/services/governance.py#L43-L73) 和 [governance.py](../knowledge_service/services/governance.py#L110-L136)。
- `context`：把已由 SQLite 过滤出的记录临时装入 `ContextGraph`，只调用 `state_at()` 得到业务时点快照；它不取代持久化双时态仓库，见 [semantica_adapter.py](../knowledge_service/integrations/semantica_adapter.py#L17-L51)。

属性抽取虽然也经过 Semantica provider，但 prompt、返回 schema、证据校验和“全部进入审核”的策略是本项目实现，不应算成 Semantica 原生成熟属性抽取流水线，见 [attribute_extraction.py](../knowledge_service/services/attribute_extraction.py#L1-L64)。

### 1.2 Semantica 0.6.8 的完整能力面

Semantica 官方把能力分成输入、核心处理、存储、质量、上下文/记忆、输出/编排等层，并列出 27 个独立模块。[官方模块索引](https://github.com/semantica-agi/semantica/blob/main/docs/modules.md)

| 模块 | 官方定位 | 本项目直接使用 | 当前判断 |
|---|---|---:|---|
| `ingest` | 文件、网页、数据库、流、Parquet/XML 等摄取 | 否 | 项目目前只接受较窄的文档输入；若要 PDF/OCR/数据库连接器可单独评估，不应因此替换整条链路 |
| `parse` | 文档文本/表格解析、Docling | 否 | 当前自有解析较轻；复杂 PDF/OCR 是真实缺口，但属于独立输入适配器 |
| `split` | 文本切片 | 否 | 已有中文条款/标题感知切片，Semantica 替换收益不明确 |
| `normalize` | 文本、实体、语言归一化 | 否 | 已有本体解析、别名和本地规则；可借鉴但不能静默改变实体身份 |
| `semantic_extract` | NER、关系、三元组、语义网络和校验 | **是，核心但定制** | 项目最主要的 Semantica 使用面；严格约束和证据治理来自项目适配层 |
| `kg` | 图构建、时间图查询、相似度 | 否 | 图模型、快照和浏览由项目实现；再引一套 KG 对象会重复 |
| `ontology` | 本体生成、SHACL、评估和质量门 | **是，部分** | 只直接用 `OntologyGenerator`；质量门值得增量试用 |
| `reasoning` | 规则、Datalog/RETE、图推理 | 否 | 当前没有 Semantica 推理；本体公理变丰富后再做隔离试验 |
| `embeddings` | embedding 生成 | 否 | 已有 bge-m3/OpenAI 兼容编码器 |
| `vector_store` | FAISS、Qdrant、Milvus 等向量存储 | 否 | 已有 Milvus + 本地降级；替换会破坏现有派生索引契约 |
| `graph_store` | LPG 图存储 | 否 | 已有 Neo4j 可重建投影 |
| `triplet_store` | RDF/SPARQL 三元组存储 | 否 | 目前 SQLite 真相源 + rdflib/SPARQL；是否上独立 triplestore 应由规模驱动 |
| `deduplication` | 重复检测、聚类、实体合并 | **是，部分** | 检测/建议来自 Semantica，安全边界、人工决策和事务由项目负责 |
| `conflicts` | 多来源事实冲突检测和决议 | 否 | 是当前最值得补的 Semantica 缺口之一 |
| `context` | AgentContext、ContextGraph、决策状态 | **是，极小部分** | 只用临时 `ContextGraph.state_at()`，没有 AgentContext/决策图 |
| `provenance` | W3C PROV-O、校验和、lineage | 否 | 项目有证据/来源/合并账本，但尚非统一 PROV-O 链；高价值缺口 |
| `change_management` | KG/本体快照、diff、回滚、校验和 | 否 | 项目已有自己的本体版本、双时态历史和撤销；整体接入会产生双版本系统 |
| `export` | RDF/JSON-LD/Parquet/AQL 等导出 | 否 | 可作为边界能力评估，优先级低于 provenance |
| `visualization` | 图和本体可视化 | 否 | 已有专用业务工作台，通用可视化价值有限 |
| `pipeline` | 工作流编排 | 否 | 已有任务队列和原子写入编排；不宜并行维护第二套 pipeline 状态 |
| `explorer` | 通用 Knowledge Explorer UI | 否 | 已有贴合审核/本体版本的工作台，不建议替换 |
| `llms` | 多家 LLM provider | 否（未直接依赖） | 目前通过 `semantic_extract.providers` 的 OpenAI 兼容接口间接调用；无多 provider 需求时不必改 |
| `mcp_server` | MCP 工具服务 | 否 | 当前产品没有此需求 |
| `seed` | 从结构化来源初始化 KG | 否 | 已有结构化批量写入；可复用性收益有限 |
| `evals` | 可重复的决策/组件评测 | 否 | 值得用于抽取、本体和检索回归集，优先级中高 |
| `core` | 顶层装配、配置、插件和生命周期 | 否 | 项目已有 FastAPI 装配/配置，不应换根容器 |
| `utils` | 通用 ID、日期、校验、日志 | 否 | 已有 `core/utils`，替换收益低 |

按“应用代码直接导入的模块族”计，是 **4 / 27**；这只是依赖覆盖，不是质量评分。四个模块也都没有把其全部子能力打开。

## 2. Semantica：哪些能力值得继续接，哪些不值得

### 2.1 第一优先：统一 provenance，而不是再造抽取

Semantica 的 `ProvenanceManager` 能给实体、关系、片段和属性记录来源、处理活动、原文、置信度、版本链和 SHA-256 完整性校验，并可导出 W3C PROV-O；官方建议在数据进入图谱时记录，而不是事后补账。[Semantica provenance 指南](https://github.com/semantica-agi/semantica/blob/main/docs/guides/provenance.md)

当前项目已经有 `source_id`、片段字符范围、证据文本、审核决定、合并来源和操作账本，基础数据并不差。缺的是把这些局部字段连接成统一、可查询、可导出的链：

```text
source document/version
  -> chunk/range
  -> extraction run + model/prompt version
  -> candidate/assertion
  -> review decision
  -> canonical entity/relation version
  -> retrieval hit
  -> answer citation
```

建议先做 **只读 PROV-O 投影或导出**，不让 Semantica provenance 成为第二真相源。若试验通过，再决定是否在写事务中同步生成完整性记录。

### 2.2 第一优先：事实冲突，而不是 domain/range 冲突

当前校验能发现未知类型、关系端点类型不符和 SHACL 违规，但“同一规范实体在不同来源中属性值互相矛盾”是另一类问题。Semantica 官方 `conflicts` 模块正是放在去重之后、SHACL 之前，支持按可信度、时间、投票或专家审核解决，并保留决议来源。[Semantica 冲突处理指南](https://github.com/semantica-agi/semantica/blob/main/docs/guides/conflict-resolution.md)

适合先做离线报告：对同一 canonical entity 的同一属性生成冲突候选，全部进入现有审核系统，不自动覆盖。这样可复用算法而不改变真相源。

### 2.3 第二优先：本体质量门和回归评测

Semantica 0.6.8 的本体质量门检查类/属性覆盖率、孤立 schema 元素、domain/range 引用和未解析关系端点，输出机器可读 issue code 与通过/失败结果，不自动修数据。[Semantica ontology 参考](https://github.com/semantica-agi/semantica/blob/main/docs/reference/ontology.md)

它适合作为本体草案发布前的**附加报告**，与现有 SHACL、成环检查、影响分析互补。`evals` 则可用于固定留出文档上的实体/关系精确率、未知类型率、证据定位率和回答引用率回归；不要只凭单次演示判断新 prompt 是否更好。[Semantica 模块索引](https://github.com/semantica-agi/semantica/blob/main/docs/modules.md)

### 2.4 后置：推理

Semantica 提供 `Reasoner`、Datalog/RETE 等逻辑推理，但“装上推理模块”不会自动产生有价值的新事实。当前本体主要价值仍在类层级、domain/range 和 SHACL；先补等价/互斥/逆关系/传递等明确公理，再用小语料验证：

- 推导是否可解释；
- 推导事实是否与显式事实分层；
- 推导是否带规则和源事实 provenance；
- 是否只在发布校验/查询时使用，而不污染双时态真相源。

### 2.5 不建议接入或替换

- `change_management`：项目已有双时态版本、本体父版本、diff、事务审核和撤销；官方版本管理也明确要求调用方自己决定何时快照，不会自动解决应用生命周期。[Semantica change management 参考](https://github.com/semantica-agi/semantica/blob/main/docs/reference/change_management.md)
- `vector_store / graph_store / triplet_store`：会与 SQLite 真相源、Milvus、Neo4j 形成重复权威边界。
- `pipeline / explorer / core`：会与当前任务、工作台和 FastAPI 依赖方向形成第二套控制面。
- `ingest / parse` 只应作为独立输入适配器评估；若目标只是支持复杂 PDF/OCR，不应把整条服务迁到 Semantica pipeline。

## 3. TrustGraph 有什么真正可取

截至调研日，TrustGraph 当前发布线为 v2.8，官方 release tag 为 `v2.8.17`（2026-09-08）。它是事件驱动的微服务平台，不是一个轻量 Python 库：默认围绕消息总线、元数据/结构存储、对象存储、图/向量存储和多处理器 flow 运行，支持 Docker/Podman 或 Kubernetes。[TrustGraph v2.8.17 release](https://github.com/trustgraph-ai/trustgraph/releases/tag/v2.8.17) [TrustGraph 架构](https://docs.trustgraph.ai/overview/architecture.html)

### 3.1 高价值：抽取 provenance 与查询解释链贯通

TrustGraph 把数据分为三个 named graph：默认图存事实，`urn:graph:source` 存文档→页→片段→抽取子图的 PROV-O 链，`urn:graph:retrieval` 存 GraphRAG/DocRAG/Agent 查询的推理轨迹。查询轨迹包含 Question、Grounding、Exploration、Focus、Synthesis；Focus 中被选中的边还能继续回溯到原始片段。[TrustGraph explainability 架构](https://docs.trustgraph.ai/overview/explainability) [TrustGraph 存储模型](https://docs.trustgraph.ai/overview/storage.html)

这比“回答中附几个 source_id”完整，最值得当前项目借鉴。可落成项目自己的三类逻辑空间，不要求真的换 RDF 存储：

| TrustGraph 概念 | 本项目建议映射 |
|---|---|
| facts/default graph | 当前双时态 record/assertion 真相源 |
| source graph | 文档版本、chunk、抽取运行、候选、审核、规范事实的 derivation 边 |
| retrieval graph | 每次问答的 query、grounding、候选集、最终选择、模型版本、答案和引用 |

第一阶段只需新增稳定的 `retrieval_run_id` 和结构化阶段事件，并保存“哪些事实/片段最终进入 prompt”；界面再逐步显示检索路径。它同时能帮助排查错答、薄弱证据和新版本回归。

另一个直接可借鉴点是 **先检索相关本体子集，再做受控抽取**。TrustGraph 的 Ontology RAG 不把整个大型本体塞进每个 chunk 的上下文，而是先对本体本身做检索，为当前 chunk 选择相关类和属性。[TrustGraph Ontology RAG 检索架构](https://docs.trustgraph.ai/architecture/retrieval.html) 当前项目的 guided entity/relation prompt 会分别遍历整份 `ontology.summary()`，见 [semantica_adapter.py](../knowledge_service/integrations/semantica_adapter.py#L215-L258)；本体规模增大后会带来 token、延迟和相关性噪声。建议在现有适配层前增加可审计的 ontology-slice retriever，并把选中的术语 IRI 记录进 ingest run，而不是为此迁移到 TrustGraph。

### 3.2 中高价值：Context Core / Knowledge Bundle 的可移植边界

TrustGraph 的 Context Core 把知识图边、schema 和图 embedding 打成可下载、上传、加载/卸载的独立包，用于领域隔离和复用。[Context Cores 官方指南](https://docs.trustgraph.ai/guides/context-cores/)

本项目可借鉴为“项目知识包”，内容至少包括：

- ontology version 与稳定术语 IRI；
- 文档/知识记录的版本清单及校验和；
- RDF/JSONL 图导出；
- 向量模型、维度、索引参数和可重建说明；
- prompt/extractor 版本、质量报告和 provenance manifest。

不建议直接打包 Milvus/Neo4j 的物理数据；它们仍应由真相源重建。注意官方 Context Core 页面明确提示新 UI 尚未支持该流程，CLI 可用，说明这一能力的产品表面仍在演进，不能照文档假设成熟度。[Context Cores 官方指南](https://docs.trustgraph.ai/guides/context-cores/)

### 3.3 中价值：Flow blueprint 与运行配置不可变

TrustGraph 用可复用 flow blueprint 定义处理器网络，可按 Document RAG、Graph RAG、Ontology RAG 等用途启动不同 flow，并把模型、切片等参数作为 flow 配置。[TrustGraph Flows 指南](https://docs.trustgraph.ai/guides/flows/) [Flow CLI 参考](https://docs.trustgraph.ai/reference/cli/tg-start-flow.html)

当前项目不需要事件总线，但可借鉴“命名处理配置”的契约：把抽取模式、本体版本、切片策略、模型、prompt/schema 版本、关系约束模式、embedding 模型固化成不可变 `processing_profile`，每个 ingest run 引用它。这样可以复跑、对比和审计，而不是只依赖运行时环境变量。

### 3.4 中价值但多有重叠：混合检索、工作区、监控

- v2.8 的 Docling 解码链覆盖 PDF、DOCX、XLSX、PPTX、HTML、Markdown 和 CSV，SDL 还提供声明式 CSV/JSON/XML/Excel/Parquet/定宽数据转换、校验和错误处理。这确实能补当前项目以 `.txt/.md` 为主的输入短板；但如果需求只是复杂文档/结构化数据接入，先做独立解析适配器或离线转换比部署整个平台更合算。[TrustGraph v2.8.17 release](https://github.com/trustgraph-ai/trustgraph/releases/tag/v2.8.17) [TrustGraph SDL 参考](https://docs.trustgraph.ai/reference/sdl)
- TrustGraph v2.8 给 Document RAG 加入 BM25 + 向量相似度 + RRF；当前项目已经有 SQLite FTS、向量检索和 Milvus dense/sparse 路径，价值主要是补统一离线评测，而不是照搬实现。[TrustGraph v2.8 changelog](https://docs.trustgraph.ai/changelog/trustgraph.html)
- TrustGraph 的 workspace/IAM 提供用户、角色、API key 和隔离边界；当前项目 README 明确尚无审核员身份、角色权限和多级审批，因此一旦从单机演示走向多人生产，这部分设计值得参考。[TrustGraph 用户与工作区](https://docs.trustgraph.ai/guides/managing-users/) [当前项目说明](../README.md#L295-L301)
- TrustGraph 自带 Prometheus/Grafana/Loki，覆盖 flow、队列、API、token、错误率和延迟；当前项目已有任务日志和读路径计时，在并发/多实例之前不必搬整套，但应提前统一指标名和 run correlation id。[TrustGraph 监控指南](https://docs.trustgraph.ai/guides/monitoring/)

### 3.5 当前不值得：整体替换成 TrustGraph

1. **能力重叠大。** 当前已有双时态、OWL/SHACL、本体发布、审核、实体融合、Milvus、Neo4j、GraphRAG 式检索和工作台。
2. **运行面显著扩大。** TrustGraph 官方架构包含事件总线、多个处理器、Cassandra/结构存储、S3 兼容对象存储、图/向量存储与监控组件；本地试用建议至少 8GB RAM/4 核/20GB，Minikube 建议 16GB/8 核/50GB。[部署选择指南](https://docs.trustgraph.ai/deployment/choosing-deployment.html)
3. **版本文档存在迁移风险。** 当前架构页仍列出 Neo4j/Memgraph/FalkorDB 选项，但 v2.8 changelog 明确写着该版本不提供 Neo4j 和 Memgraph 支持。对本项目这种已有 Neo4j 投影的系统，必须按目标版本做实测，不能只看概览页。[TrustGraph 架构](https://docs.trustgraph.ai/overview/architecture.html) [TrustGraph v2.8 changelog](https://docs.trustgraph.ai/changelog/trustgraph.html)
4. **Ontology RAG 不等于确定性约束。** 官方快速指南表述为 OWL ontology “constrains and guides the LLM”，但不能仅据此推断它等价于当前项目的返回后 IRI 解析、domain/range、SHACL 和人工审核。现有硬校验不能被 prompt 约束替代。[TrustGraph Ontology 指南](https://docs.trustgraph.ai/quickstart/ontologies.html)
5. **平台目标不同。** TrustGraph 的强项是多租户、事件流、Agent/GraphRAG 控制面；当前项目的核心差异化是业务规则知识的双时态真相、受控本体演进和人工治理。
6. **官方文档仍有成熟度警报。** 文档站自己列出待审/需重写页面，生产 HA、备份、容量规划和加固指南仍不完整；监控指南也说明 Cassandra/Pulsar 等基础设施尚未全部接入监控。真正评估时应把这些列为 PoC 验收项，而不是默认平台已覆盖。[官方待审页面](https://docs.trustgraph.ai/pages-for-review.html) [生产部署注意事项](https://docs.trustgraph.ai/deployment/production-considerations.html) [监控指南](https://docs.trustgraph.ai/guides/monitoring/)

## 4. 建议优先级

| 优先级 | 建议 | 是否引入新平台 |
|---|---|---:|
| P0 | 保持现有 Semantica 4 模块选择性接入，不追求 27 模块覆盖率 | 否 |
| P1 | 定义统一 provenance / retrieval trace 数据模型；先以当前 SQLite 为真相源，提供 PROV-O 只读导出 | 否 |
| P1 | 在抽取前按 chunk 检索相关本体子集，并记录所用术语 IRI，避免整份本体进入每次 prompt | 否，借鉴 TrustGraph Ontology RAG |
| P1 | 在本体草案发布前增加 Semantica `ontology_quality_check` 报告，但不自动修改本体 | 仅新增库内调用 |
| P2 | 对 canonical entity 属性做 Semantica conflict 离线扫描，结果全部进入现有审核队列 | 仅新增库内调用 |
| P2 | 建立固定抽取/检索/回答回归集，比较 prompt、模型、本体版本和 processing profile | 否或使用 Semantica `evals` |
| P2 | 设计可导出的“项目知识包”：本体、权威记录、provenance manifest、索引重建 manifest | 否，借鉴 Context Core |
| P3 | 本体公理足够后，小范围验证 Semantica reasoning，推导事实与显式事实严格分层 | 仅试验 |
| 暂不做 | 用 Semantica 存储/版本/Explorer/Pipeline 替换现有实现 | 否 |
| 暂不做 | 部署或迁移到完整 TrustGraph | 否 |

## 5. 何时才应重新评估 TrustGraph

出现以下至少两到三项，再做隔离 PoC，而不是直接迁移：

- 需要多团队、多租户、工作区级 IAM 和 API key；
- 同时运行大量摄取/检索/Agent flow，单进程任务队列已成为瓶颈；
- 必须统一管理 GraphRAG、DocRAG、Agent 工具调用并实时显示解释事件；
- 需要跨环境下载、共享、加载完整知识包；
- 运维团队已经具备 Kubernetes、消息队列、对象存储和集中监控能力；
- 现有 SQLite 真相源、Milvus/Neo4j 派生架构出现明确、可量化的规模瓶颈。

PoC 的验收指标应是：同一批中文规则文档上的抽取质量、引用可追溯率、查询 P95、资源成本、重放一致性、数据删除/回滚能力、Neo4j/Milvus 兼容性和运维复杂度，而不是功能清单长度。

## 6. 最终判断

当前项目对 Semantica 的使用已经从早期“只做 LLM 抽取”扩展到了本体归纳、实体去重/合并建议和时间快照，但仍然只使用了其完整平台的一小部分。这个状态总体合理：**本项目掌握权威数据、审核和版本生命周期，Semantica 提供可替换算法能力。**

下一步最划算的投入，是把现有证据、审核、版本和检索选择连接成统一可解释链，再补多来源事实冲突与质量回归。TrustGraph 在这两点上提供了很好的参考实现和术语体系；但其完整运行栈解决的是更大规模的多租户 Agent 平台问题，目前不应成为本项目的新底座。
