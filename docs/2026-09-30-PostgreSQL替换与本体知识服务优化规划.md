# PostgreSQL 替换与本体知识服务优化 Implementation Plan

> **For agentic workers:** 实施时使用 `subagent-driven-development`（具备子智能体能力时）或 `executing-plans` 技能；按本文复选框记录进度。本文是规划，不是已完成的改造记录。不得把未执行的测试勾为通过。

**Goal:** 在不削弱本体治理、双时态、证据和审核语义的前提下，将业务主库完全替换为 PostgreSQL，并让 10 万实体、数千文档场景下的常用交互达到可验证的 500ms 级目标。

**Architecture:** PostgreSQL 是唯一业务真值库；Milvus 是可重建的检索投影，Neo4j 暂保留为可选投影。API、解析/抽取 worker、向量 worker 分离；所有正式变更走受本体约束的业务提交入口，通过事务内 outbox 驱动派生索引。Semantica 仅提供受控能力，不成为第二个真值系统。

**Tech Stack:** Python 3.12+、FastAPI、Pydantic、PostgreSQL、psycopg 3/连接池、Milvus、RDFLib/pySHACL、Semantica/Docling、现有 JavaScript/ECharts 前端，以及仅用于本体工作台的 React + `@xyflow/react` + `elkjs` island。

---

## 1. 执行摘要与不可变决策

1. **确定替换 SQLite，而不是维护两个数据库适配器。** 用户所说的“psql 对接”按 PostgreSQL 对接实施；`psql` 是管理客户端，不是应用连接方式。应用使用 psycopg 连接池。
2. **不迁移旧数据。** 新 PostgreSQL 从空业务库开始；不开发 SQLite → PostgreSQL 数据搬运、旧表回填、双写、双读或失败时回退 SQLite。开发环境、集成测试和正式环境统一 PostgreSQL。纯算法单元测试可使用假对象，但不能用 SQLite 模拟 PostgreSQL 事务。
3. **不在本次规划阶段删除任何旧文件、启动替换服务或修改业务代码。** 正式切换时隔离旧实例、旧数据和旧索引命名空间。新系统开始写入后，新数据必须备份，不能以“不保留历史数据”为由再次清空。
4. **10 万实体本身不是 SQLite 必然失效的证据。** 当前更直接的瓶颈是全量读取、Python 过滤/遍历、长临界区、模型争用、无界返回与投影一致性。换 PostgreSQL 的价值是并发事务、索引查询、持久任务和运维能力；只替换连接字符串不会达成目标。
5. **不以删约束、丢候选、减少证据或静默截断换速度。** 本体校验、端点可见性、审核及历史查询必须有回归测试。复杂操作可异步完成，不能把“受理成功”冒充“正式发布成功”。
6. **保留现有业务边界，不整套重写为 Semantica 应用。** 正式知识由本项目的治理流程产生；开放抽取只开放候选发现能力，不开放任意工具、数据库写入或本体自动发布权限。
7. **本轮不同时引入 Elasticsearch、Redis、AGE、pgvector 或在线 Neo4j。** 先用 PostgreSQL + 现有 Milvus 达到验收；新组件必须有当前方案不能满足的实测证据和独立决策记录。
8. **本体工作台单独采用 React island，不重写其他原生页面。** 使用 React + `@xyflow/react` + `elkjs` 实现 Dify 风格的可操作本体画布；现有 `index.html` 仍是正式入口，非本体页面继续使用原生 JavaScript。确认无运行时引用且设计文档已标记不再维护的 `web/ontology-manager.html` 在新入口验收后直接删除，不保留第二套编辑页。
9. **审核采用“系统整批自动审查，人工一次确认安全项”的 A 方案。** 服务端对整个草案生成稳定的 `review_plan`，自动把操作分为 `safe/manual/blocked` 并默认选中全部安全项；审核人只需一次点击“批准全部安全项”，服务端在同一事务中记录真实认证 actor。系统策略不直接冒充人工写入最终 approve；人工只逐项处理风险、警告、AI 来源和阻断项。

### 1.1 状态与依据

- 分析基线：`0644843b7850f17a52ca777488e55e3491dfdb71`，日期 2026-09-30。后续执行先检查新提交和工作区变化，不能按旧行号机械修改。
- 已做：按代码知识图谱检查 API、仓库、业务服务、集成、前端和测试/部署配置；追踪关键热路径及本体发布边界。
- 未做：10 万实体压测、生产并发复现、安全渗透测试、PostgreSQL 实现、线上切换。本文件中的耗时预算是目标，不是实测结果。
- 当前 `compose.yml` **已有 PostgreSQL 服务**，声明 `postgres:18.6-bookworm`、持久卷和部分观测配置；应用仍从 `KG_DATABASE` 打开 SQLite。容器存在不代表业务已接入，也不能仅凭配置证明镜像/角色/扩展已经可用。
- `.venv` 未安装 Semantica；本机 `D:\anaconda\envs\llm_model\python.exe` 的包元数据为 Semantica 0.7.0、Docling 2.130.0。实施时统一锁定运行环境，不能把缺依赖导致的 skip 当成功。

## 2. 性能目标：先定义什么叫 500ms

### 2.1 验收口径

以**常用交互端到端 p95 ≤ 500ms**为首轮发布门槛，p99 ≤ 1,000ms 为暂定尾延迟门槛。端到端指用户发起操作到首屏结果可读、可交互，包含请求、传输、JSON 解析和必要渲染。没有硬件、负载和返回规模边界时，不能承诺所有请求绝对小于 500ms。

| 操作 | 首轮边界/目标 | 超出边界的产品行为 |
|---|---|---|
| 项目、文档、任务、审核列表 | 每页默认 50、上限 100；返回摘要；p95 ≤ 500ms | 游标翻页，详情另取；不默认计算昂贵总数 |
| 实体/事实详情、局部证据 | 单实体和分页关联；p95 ≤ 500ms | 大原文分块获取，不在摘要中嵌入所有断言 |
| 关键词检索 | top-k 默认 20、上限 100；无模型调用；p95 ≤ 500ms | 拒绝超预算条件或转异步，而非扫描全库 |
| 语义/混合检索 | 已预热模型与索引、受控过滤、top-k ≤ 100；p95 ≤ 500ms | 语义能力不可用时显式报降级；远程模型尾延迟单列 |
| 图谱首屏/邻域展开 | 默认 ≤ 200 节点/400 边，硬上限 500/1,000，默认 1 跳、上限 2 跳；p95 ≤ 500ms | 返回 `truncated`、继续展开入口；不下载 10 万节点做力导布局 |
| 仪表盘/来源统计 | SQL 聚合或按修订号缓存；p95 ≤ 500ms | 大范围精确历史统计用异步任务 |
| 小范围事实修订 | 受限批次、可证明局部的约束；p95 ≤ 500ms | 大批次、全局约束/发布走任务；正式生效前完成全部校验 |
| 上传/解析/抽取/批量入库/本体全量校验/导出/重建 | 任务创建和状态读取 p95 ≤ 500ms；完成耗时单独统计 | 展示阶段、失败原因、可恢复进度；上传时间不算成后台解析时间 |
| 生成式问答、任意复杂 SPARQL | 不承诺生成完成/查询完成 500ms | 问答先返回检索证据再流式生成；SPARQL 受限或异步 |

文件传输依赖大小和网络：只能要求元数据预检/已收完文件后的任务受理快速，不能承诺 25MB 文件从点击上传到完成传输在 500ms 内。

建议预算：常规接口服务端 ≤ 200ms，语义检索服务端 ≤ 300ms，传输/调度/首屏渲染占其余预算；必须以端到端实际分位数验收，不能直接相加各组件 p95。当远程 embedding 单次请求已超过预算时，查询编码必须本地化/专用化或调整该功能承诺，缓存不能作为唯一解法。

### 2.2 容量和混合负载

- L1：1 万实体、3 万关系、3 万属性、1,000 文档、2 万 chunk，用于快速回归。
- L2 发布验收：10 万实体、30 万关系、30 万属性、3,000 文档、10 万 chunk；含多版本、别名、断言、证据与历史记录。历史版本按当前事实的 3 倍建模，同时覆盖少量 20+ 版本热点记录。
- L3 增长观察：30 万实体，关系/属性同比增长；不作为首轮硬门槛，但记录拐点。
- 大项目与多项目分别测；图分布包含高出度节点和孤立点；文档包含中文、英文、混合语言、长文及解析失败样本。
- 暂定 L2 常态 20 RPS、50 并发虚拟用户，读请求占 80%；同时运行 2 个解析/抽取任务及 1 个受限向量批处理任务。并发用户采用有思考时间模型，报告实际 RPS、队列时间和拒绝率，不把 50 并发等同 50 RPS。
- 每场预热后持续 ≥ 30 分钟，另测冷启动、1 小时持续导入、突发流量和故障恢复。错误率 < 0.1%（预期的权限拒绝/限流另列），无静默漏检、无跨项目泄露。
- 报告固定 CPU、内存、磁盘、GPU、浏览器、网络、模型/索引版本、数据分布、压测提交号。硬件未确定前这些是待验证基线，不是容量保证。

## 3. 全项目现状与问题清单

代码链接相对本文件；行号随实现变化，以符号名称为准。**“已确认”表示源码路径存在，不代表已测出对应线上耗时；“待验证风险”不等于已经证实可利用的安全漏洞。**

### 3.1 模块范围

| 范围 | 当前职责 | 规划重点 |
|---|---|---|
| `repository/` | 项目、版本、断言、溯源、摄取、发现、草案、审核、账本 | 全量替换 SQLite；事务与约束保持；查询下推 |
| `services/service.py`、`formal_writes.py` | 统一知识写入和正式事实提交 | 减少锁/扫描，事务原子性，CAS 与 outbox |
| `ontology*.py`、`discovery_vocabulary.py` | 本体、词表、草案、发现与发布 | 双时态校验、不可变历史、受控异步发布 |
| `retrieval.py`、`entity_resolution.py`、`reconciliation.py` | 检索、消歧与融合 | 候选完整性、类型/时间过滤、批处理、准确率 |
| `explorer.py`、`answers.py`、`evidence.py`、`provenance.py` | 图/统计/问答上下文/证据 | 有界邻域、分页、精确来源与历史快照 |
| `jobs.py`、`document_uploads.py`、`chunking.py` | 异步任务、上传和分块 | 持久任务、故障恢复、资源隔离与断点 |
| `integrations/` | Semantica、Docling、embedding、Milvus、Neo4j | 超时、限额、适配契约、投影一致性 |
| `api/`、`models.py` | HTTP 契约与输入验证 | 身份/权限、分页、错误契约、预算 |
| `web/` | 工作台、发现、本体、审核、图谱与证据 UI | 首屏轻载、分页/虚拟列表、请求取消、局部布局 |
| `core/`、`utils/`、部署/脚本/测试 | 配置、日志、过滤、启动和验证 | PG-only 配置、可观测性、依赖一致性、运维 |

### 3.2 已确认的性能与逻辑缺口

| ID/优先级 | 源码依据 | 问题与影响 | 对应任务 |
|---|---|---|---|
| F01 / P0 | [core.py](../knowledge_service/repository/core.py)，`Repository.__init__`、`_transaction` | 单 SQLite 连接和共享锁；写事务与业务层长锁限制并发。开启 WAL 不消除单写者和应用锁 | T02–T04 |
| F02 / P0 | 同上 `query`；[service.py](../knowledge_service/services/service.py) `scoped/search` | 时间/元数据过滤部分在 Python；`scoped` 未把 kinds 下推，搜索先加载项目全量范围；JSON 解码和内存随规模增长 | T05–T06 |
| F03 / P0 | `service.write/_latest`、`ontology_labels` | 写入锁内加载当前项目、校验、编码并同步 Milvus；标签缓存访问也会受该服务锁影响。不同项目的在线读可能被批量任务拖累 | T04、T08 |
| F04 / P0 | [milvus_store.py](../knowledge_service/integrations/milvus_store.py) `_filter_expr`，约 193 行 | `candidate_ids[:5000]` 静默截断；超过 5,000 个允许版本时可能漏召回；空列表与未设置过滤的契约也不明确 | T06 |
| F05 / P1 | [retrieval.py](../knowledge_service/services/retrieval.py) `_search_via_milvus/search` | 关键词模式也编码 query；先统一 top-k 再按 kind 分配可能饿死某类结果；FTS 后仍全候选词法扫描 | T06 |
| F06 / P0 | `service._sync_milvus/ingest`；`milvus_store.upsert` | 索引失败仅记录日志，业务可能仍表示 semantic ready；每次 flush；按 version_id 写入不主动清理旧版本，依赖候选筛选遮蔽旧数据 | T07 |
| F07 / P1 | [entity_resolution.py](../knowledge_service/services/entity_resolution.py)、[reconciliation.py](../knowledge_service/services/reconciliation.py)、[governance.py](../knowledge_service/services/governance.py) | 每个 mention/合并候选重复扫描实体；实体消歧依赖本地向量，但仓库已剥离向量，可能静默失去语义候选；融合把来源列表持续复制进 metadata | T06、T09 |
| F08 / P1 | [explorer.py](../knowledge_service/services/explorer.py) `graph/dashboard`；[answers.py](../knowledge_service/services/answers.py) `question_context` | 全量 scoped 后统计/BFS；每跳扫描所有边；无中心节点时 graph 返回全图；部分上限只限制最终输出 | T10 |
| F09 / P1 | [knowledge.py](../knowledge_service/api/knowledge.py)、[workspace.js](../knowledge_service/web/workspace.js) | records/query 全量读取后切片是假分页；图谱入口自动加载全图并 force 布局；fitGraph 重取/复制配置；多个历史/审核列表缺分页 | T05、T10 |
| F10 / P0 | [jobs.py](../knowledge_service/services/jobs.py) | 单线程执行器、内存闭包任务；重启标记中断而非租约恢复；任务状态带大结果与最多 2,000 行日志反复写入 | T08 |
| F11 / P1 | [embeddings.py](../knowledge_service/integrations/embeddings.py) `LocalEncoder` | 同一锁保护整次 encode；批量编码可能阻塞查询编码；冷启动成本未独立建模 | T08 |
| F12 / P1 | [ontology.py](../knowledge_service/services/ontology.py) `validate_timeline/query` | 每个有效时间边界重复建图/SHACL；SPARQL 虽限制输出，仍全图构建且无硬计算时限 | T09、T11 |
| F13 / P1 | [workspace.py](../knowledge_service/api/workspace.py) 重建路径 | 读取全量、积累全部编码；无 Milvus 时仍可做无效编码；破坏式重建没有影子代际切换保障 | T07 |
| F14 / P1 | [document_uploads.py](../knowledge_service/services/document_uploads.py)、[document_parser.py](../knowledge_service/integrations/document_parser.py) | 有 25MB/文本长度限制和失败清理，但解析后才检查文本长度；缺少进程级 CPU/内存/展开量/硬超时；临时文件生命周期不满足任务重启恢复 | T08、T11 |
| F15 / P1 | `Repository.get_ontology/list_artifacts`、发现与摄取元数据 | 查询单个本体前读全部版本；列表常返回大 JSON；候选数组/重复来源嵌入文档和衍生记录产生放大 | T05、T09 |
| F16 / P0 | `FormalFactWriter` 与各 store | 多处依赖 `_db/_lock` 和 SQLite 事务，不能只换 `Repository.__init__`；漏改会破坏跨事实/断言/溯源/审核原子性 | T03–T04 |
| F17 / P1 | `ontology_discovery._induce` 末尾 | 对本轮关系 IRI 从合并后的 baseline 图移除 domain/range。需区分旧推断约束与人工正式约束；不能把用户明确的约束按推断噪声自动移除 | T09 |

已有的正确优化必须保留：`list_artifacts` 已在 SQL 限制 project；仓库层支持部分 kinds 下推；本体标签已有版本缓存；前端已有防旧请求覆盖的 generation gate；mindmap 已有输出上限。这些不是“完全缺失”，问题在于上游仍全量工作或缓存粒度/锁边界不合适。

### 3.3 安全与部署风险

| ID/级别 | 已见证据/范围 | 规划处理 |
|---|---|---|
| S01 / P0 已确认缺口 | `api/__init__.py` 未见全局身份与项目权限边界；project_id 只是数据分组，不是用户授权。默认回环监听不等于已有公网暴露 | API 统一认证、项目角色授权；管理员路由单独授权；部署前必须验收 |
| S02 / P0 已确认缺口 | 本体/消歧审核请求接受客户端 actor；全局 jobs 接口可返回日志和结果 | actor 从认证身份产生；actor 输入最多是标注，不得当审计主体；任务按项目授权及摘要化 |
| S03 / P1 已确认构造风险 | Milvus filter 直接插值项目和版本字符串 | 使用 SDK 支持的安全参数/受控表达式构造器；对引号、反斜杠、特殊 ID 做测试；不称其为已证实 SQL 注入 |
| S04 / P1 待验证 | 文档 HTML、解析器依赖、Turtle/标签/证据富文本、LLM 来源文本 | 验证外部资源加载/SSRF、压缩炸弹、XSS、提示注入；未复现前不宣称漏洞已存在 |
| S05 / P1 已确认边界不足 | 过滤器已有深度 16 和每组 100 限制，但缺总 AST 节点/IN 长度预算；SPARQL 已拒绝 SERVICE/FROM/更新语句，仍缺计算隔离 | 补总成本预算、deadline 与取消；保留已有防护 |
| S06 / P1 部署配置 | `compose.yml` 的 Milvus 19530/9091、Attu 3000 绑定所有地址；PostgreSQL 则限回环。配置中有 `seccomp:unconfined` | 默认回环/内网、鉴权、防火墙；管理 UI 不对业务用户暴露；审查必要能力，不直接删除兼容配置导致启动失败 |
| S07 / P1 运维验证项 | 健康响应含解释器路径；日志可能含正文/供应商异常；env 只有示例，未读取真实密钥 | 对外健康摘要化；结构化日志脱敏、权限与保留期；密钥禁止进入审计/UI/trace |

本次不是完整渗透审计。认证中间件、SQL 编译器和解析沙箱实施后，需要负向测试及独立安全验收。

### 3.4 现有测量的适用范围

旧 SQLite 的一次只读检查：文件约 160MiB、2,413 条版本，当前约 1,164 实体、911 关系、306 chunk、23 文档、10 项目。旧日志有几百条记录读取约 400ms、图谱约 2.9 秒的样本，但来自 2026-09-29、非受控实验。它们只能说明曾有慢请求，**不能代表当前提交的 p95，更不能线性外推 10 万实体**。先完成 T00/T01 再建立新基线。

## 4. 本体业务不变量：优化不可跨越的底线

以 [CONTEXT.md](../CONTEXT.md) 和现有治理测试为领域契约，不把知识系统退化为只有文本和向量的普通 RAG。

1. 实体实例与 `owl:Class` 分开；实体—实体事实用 `owl:ObjectProperty`；实体—标量事实用 `owl:DatatypeProperty`，保留 datatype/language/value 类型，不把 `true`、`1`、`"1"` 混为同值。
2. 项目内规范名称的跨术语种类冲突必须隔离；别名/显示标签不等同稳定 IRI。不同本体版本中同一稳定 IRI 的实体不能仅因版本号变化被重复拆分。
3. 候选事实、候选词汇、草案、正式本体、正式事实分别保存状态和来源。开放发现允许新词进入候选区，不允许直接成为正式类/关系/属性。
4. 草案是基于 base ontology revision 的原子操作集合；审核决定、发布请求、发布本体和溯源必须一致。已发布本体不可原地编辑；旧 base 发布冲突返回明确冲突，不覆盖新版本。
5. 正式写入须通过类型解析、端点、有效时间、本体/SHACL、事实键与并发版本检查。只允许经过确认的本体词汇；任何兼容入口都必须汇入同一提交服务。
6. OWL 开放世界语义不等同业务完整性约束。必填、唯一、数量上限等由明确的 SHACL/业务规则执行；不能把“未推导出矛盾”当成校验通过。
7. 系统时间（何时记录/修订）与业务有效时间分开；采用当前契约的半开区间 `[start,end)`。未知有效时间不是自动补成今天；NULL/无界的查询行为以现有时间测试冻结。
8. 关系/属性在查询时间点的端点必须可见、属于同项目且满足规则。`kinds=['relation']` 的优化不能因未加载 entity 就误删关系，也不能省略端点检查；用 SQL JOIN/EXISTS 保留语义。
9. 来源文档版本、chunk/位置、断言、正式记录版本、活动和边可追溯；原文更新不改写旧证据。撤回一个来源不自动撤销仍有独立有效证据的事实。
10. 合并必须保存重定向、冲突决策、事实改写和可逆账本；不同类型/有效时间不允许仅凭向量分数自动融合。属性 maxCount 和冲突赢家的现有逻辑必须保留。
11. 多轮发现的候选快照、已物化 ID、binding 与重试幂等保持；二轮不得重复写事实、生成空本体版本或重新绑定已冻结候选。
12. 读模型、缓存、Milvus/Neo4j 不是事实权威。它们失败/落后不改变 PostgreSQL 真值；必须返回可解释的状态，而不是伪造“完整/就绪”。
13. 本体草案中的父级、domain、range、datatype 和结构化 SHACL 约束必须是类型化命令，不允许靠自由文本或任意 Turtle 绕过。前端可以提前阻止无效连接，但服务端始终重新校验；安全项自动审查、人工决定、最终校验和发布必须绑定同一 revision、策略版本及 operation fingerprint。

建议为上述条款建立 `tests/contracts/` 的行为测试，测试公开业务结果而不是 SQLite SQL 文本；所有优化均运行这些测试。范围定义、历史快照、权限应使用同一只读 `QueryScope` 契约，避免搜索/图谱/问答各自解释一套规则。

## 5. PostgreSQL 目标设计

### 5.1 连接、配置与边界

- 统一 `KG_DATABASE_URL`（PostgreSQL DSN）；旧 `KG_DATABASE` 若仍配置，应给明确迁移提示并拒绝当数据库路径使用。配置错误直接失败，不创建本地 SQLite。
- 采用 psycopg 3 + `psycopg_pool`，沿用同步服务形态；不为更换数据库顺便全站 async 重写。同步接口不在 event loop 内做阻塞 CPU/DB 工作。
- 每个请求/worker unit-of-work 独享借出的连接；显式事务上下文，不跨线程共享 connection/cursor。所有 composed store 加入同一个 unit-of-work。
- 初始池预算可用每 API 进程 min=2/max=10、worker max=4，**总连接数按实例数相加**，留出维护连接；测量后调参。设置 pool acquire timeout、`statement_timeout`、`lock_timeout` 和 idle-in-transaction timeout，超时映射明确错误。
- 应用角色无建库/DDL/superuser 权限；迁移角色单独使用；诊断只读角色最小授权。Compose 的 `POSTGRES_APP_USER` 环境变量本身不会自动创建应用角色，必须有经验证的 bootstrap SQL/脚本。
- schema 采用编号 SQL migration + checksum/版本表；用 advisory lock 串行迁移，独立部署步骤执行；API 启动只检查兼容版本，不让所有实例同时建表/修复历史。
- migration SQL 必须进入安装包的 package-data；在源码目录之外安装 wheel 后也应能执行迁移，不能只在开发 checkout 中可用。
- 不设计通用多数据库抽象。保留 Repository 对外导入路径，在内部按事实/本体/治理/投影/任务职责组织，删除依赖 `_db` 的跨层访问。

### 5.2 数据布局和约束

下表是目标结构，最终 DDL 在 T02 落地。现有语义必须逐项映射，不等于所有表原样复制。

| 目标对象 | 数据/索引 | 必须保持的约束 |
|---|---|---|
| `projects`、`service_settings` | 项目状态、head/revision；配置 JSONB | 项目状态/权限一致；密钥不落通用 settings 明文 |
| `project_memberships` | 经认证的 issuer/subject、project_id、role；项目与主体联合索引 | 仅管理员可更改成员权限；不相信请求正文声明的角色；保留权限变更审计 |
| `record_versions` | record_id/version_id/project_id、kind/type IRI、subject/object/source、版本号、系统/有效时间、deleted、typed value、metadata JSONB | 当前版本部分唯一；版本号唯一；历史不可任意覆盖；端点和来源同项目 |
| `records_current`（事务内当前投影） | 热字段和 current_version_id；实体名称/别名、关系邻接索引、文档摘要 | 指针与历史同事务；只代表系统当前状态，历史查询不从该表伪造 |
| `ontologies` + 当前词汇目录 | 原始 Turtle、内容摘要、稳定 IRI、版本/base、发布时间；目录含 kind/canonical_name/labels | 发布快照不可变；规范名种类约束；目录可重建而非另一个编辑入口 |
| 草案/操作/审核/发布表 | 对应 `ontology_drafts/operations/review_decisions/publish_requests` | base CAS、操作顺序、幂等请求、已发布历史保护 |
| 断言及事件 | `assertions/assertion_events/record_version_assertions/fact_keys` | 候选与正式状态转换原子；支持关系指向精确 record version；事实键类型化 |
| 溯源和账本 | `provenance_activities/edges`、`record_operation_reservations`、`merge_operations/resolution_reviews` | 活动与业务提交同事务；合并可恢复；孤儿/跨项目引用拒绝 |
| 摄取与发现 | `ingest_runs/ingest_stage_outputs`；发现 run/candidate/binding 的逻辑数据 | 阶段输出和候选可独立分页；多轮快照/已物化集合不丢失；从目前 store/artifact 布局显式映射 |
| 制品/原文 | `artifacts`、raw asset 引用/摘要/hash | 大文本独立取；原文持久卷或对象存储可寻址、不可静默替换 |
| 新增任务/投影设施 | `jobs/job_events/outbox/projection_checkpoints` | lease/fencing、幂等键、顺序修订、水位线与错误可追踪 |

关键 DDL 决策：

- ID 用 text 或严格匹配现有 ID 形式的类型，**不能假设历史业务 ID/IRI 都是 UUID**。新增系统任务 ID 可以 UUID；业务 ID 与语义 IRI 分开。
- 时间用 `timestamptz`，内部规范化为 UTC；API 序列化行为冻结。原始自然语言时间保留证据，不硬转。
- 所有业务查询带 project_id；外键优先 `(project_id, target_id)`/精确 version 外键，杜绝仅凭全局 ID 通过跨项目引用。应用事务内验证“版本存在”和“在时间点可见”两层条件，普通 FK 不能代替时态语义。
- 至少包括当前唯一索引 `(project_id,record_id) WHERE superseded_at IS NULL`，版本唯一 `(project_id,record_id,version)`，按 `(project_id,kind,type_iri,record_id)` 的当前读取索引，以及关系两向 `(project_id,subject_id,type_iri,record_id)`/`object_id` 索引。若当前表承担主要读取，避免重复建无效索引。
- 历史查询用系统区间/有效区间索引与稳定排序；是否采用 `tstzrange`/GiST 由 EXPLAIN 验证，不为所有列无差别建索引。禁止通过时间字符串比较替代数据库时间类型。
- `metadata` 用 JSONB 扩展，kind/type/端点/source/deleted/时间等热字段必须类型化。只对实测常用路径建表达式/GIN 索引；不要把整个原文、候选数组和来源履历堆在每条 metadata。
- 保护不可变历史的触发器/角色权限要移植；系统时间版本关闭等受控更新例外必须明确。禁止通用 UPDATE 绕过发布、审核或证据历史。旧 `ontology_history_repairs` 不做历史回填，新系统若保留修复能力必须单独审计，不提供任意改历史后门。
- `fact_keys` 要包含业务定义所需的谓词、端点/typed literal、有效期等维度；先冻结现有语义再唯一化，不能只用显示文本去重。

### 5.3 正式写入和并发

建议业务入口是 `FormalFactWriter.apply(command, expected_versions, expected_project_revision)`；输入包含验证过的事实、支持断言、活动、幂等键和本体版本，不接收外部任意 SQL/图更新。接口细节以现有公共契约兼容为先。

执行序列：读取所需快照和 revision → 事务外完成模型/解析及校验准备 → 短事务检查 project head 与受影响版本 → 按统一顺序锁定受影响记录 → 关闭旧版本/插入新版本/更新当前表 → 更新断言、fact key、溯源、账本 → 插入 outbox → 提交。

发生 revision 改变时，不能使用过时校验结果强行提交；重建受影响快照并有限重试，或返回 409/任务重试。发布本体和复杂全局约束初期使用短时 project publication lock + head CAS 保证正确性；不在锁内调用 LLM/embedding/Milvus。多个项目不共用 Python 全局业务锁。

增量校验无法证明安全时，后台对固定 revision 完整验证，提交时再次检查 revision。持续写入导致反复失效时，对该项目设置有界发布屏障或排队写入，读仍可服务；不得“抽样通过”。死锁/序列化失败有限退避重试，外部副作用只由 outbox 执行。

### 5.4 过滤、分页与中文检索

- 新增参数化过滤编译器，复用 `utils/filters.py` 的语义契约：missing 与 JSON null 不同、`exists` 判断键存在、bool 与 number 不混淆、数组 contains 与字符串 contains 不混同；时间比较规范化。用差分测试比对旧纯函数和新 SQL 结果。
- 操作符/顶层字段采用白名单，JSON 路径做类型校验并参数化；单请求限制总 AST 节点（初始 200）、IN 长度（初始 500）、分页大小和字符串长度。现有深度 16 限制保留；总预算另行收紧并返回 422，不默默截断。
- keyset cursor 使用稳定排序 `(sort_key,record_id/version_id)`，包含 project/scope hash/必要快照 revision，签名防篡改；跨项目/条件不一致拒绝。翻页一致性通过固定 known_at/revision，或明确标注实时列表语义。
- 列表只选摘要列；详情、原文、完整证据和候选单独请求。总数按需查询，热点可按项目 revision 缓存；历史统计成本超限走任务。
- PostgreSQL 默认全文分词不能直接等价替换中文 FTS。第一版使用受控中文分词（锁定词典/版本）生成 tsvector + GIN，英文使用明确配置；以 pg_trgm 支持名称/别名的有限模糊匹配。先测混合中文、专名、IRI、短词、标点召回；新分词失败不能悄悄丢失关键词能力。

## 6. 检索、投影与前端

### 6.1 候选召回必须摆脱全项目列表

关键词：授权/QueryScope → PG 索引候选 → top-k 摘要 → 必要详情。整个路径不调用 embedding。

语义/混合：授权/QueryScope → query embedding（缓存/专用推理）→ Milvus 索引内预过滤 → 返回精确 version_id → PG 批量核验权限、版本、端点和时间 → 融合排序 → 有界返回。模型、维度、归一化和索引 generation 必须相符。

- 常见 project/kind/type/deleted/有效区间/namespace/generation 放进 Milvus 可过滤字段；未来有效事实不能只靠“当前时刻有效”一个布尔量表示。
- 复杂元数据先由 PG 得到可控候选；小集合安全传 ID，大集合使用已声明的可索引过滤字段或专用异步检索路径。**禁止复制 `[:5000]`，也不能以全局 top-k 后过滤作为唯一召回策略。** 大范围精确过滤暂不支持时返回明确能力/预算错误，不返回貌似完整的少量结果。
- PG 回查是防越权/过期的安全重验，不是替代预过滤；过滤后不足可以有界补召回，并暴露 partial/reason。`None`=未提供 ID 限制，`[]`=不允许任何候选，必须分别测试。
- entity/chunk 等有配额时分路检索再融合；若采用 RRF，应做离线相关性验证。当前 Milvus 使用 WeightedRanker，不应按已有 RRF 写测试。
- 语义不可用默认给出 `semantic_unavailable`；若调用方显式接受降级，可返回 keyword 模式并标明 `degraded=true/reason/effective_mode`。不再声称无本地向量的“本地语义回退”。
- 系统历史 `known_at` 的第一版保证 PG 精确过滤+关键词；历史语义检索若没有历史向量代际则明确拒绝，不返回当前语义结果冒充历史结果。现有已移除本地向量，不能把旧文档描述当作能力。
- 消歧复用同一受控候选服务：名称/别名精确检索 → 类型/时态限定 ANN → Semantica 建议/业务判定。保持审核阈值和禁止自动合并的规则，不能因本地向量消失就降低阈值补偿。

### 6.2 Outbox 与索引生命周期

事务内 outbox 记录 event_id、project、record/version、project revision、action、model version、namespace。消费者至少一次投递，目标端幂等；同记录事件按版本顺序处理，过期任务不能覆盖新投影。删除/撤回使用 tombstone/受控移除，重试不可复活已删除记录。

`db_committed`、`keyword_ready`、`semantic_pending/ready/failed` 分开表示；semantic_ready 取自实际成功写入且可查询的 projection checkpoint，包含索引 generation 和落后 revision。索引不可用时数据库提交仍可成功，但 UI 必须显示待索引，不伪装为全部可检索。

批量 upsert 有上限，常规写不每条强制 flush；选择并记录 SDK 支持的一致性/可见性方式。全量重建按数据库游标分批 → 新 generation 构建 → 捕获期间 outbox 增量 → 一致性和召回检查 → 原子切换 active generation → 宽限后回收旧 generation。重建失败保留旧 active；切换期间不能混用两个模型维度。

首次 PG 空库使用全新 logical namespace，避免旧 Milvus/Neo4j 数据被新实例检索。用户授权不迁移不代表授权删除宿主机共享向量目录；旧 namespace 清理由单独运维动作执行。

### 6.3 通用图谱与工作台

- 默认图谱展示按本体类型聚合/统计概览或小规模代表性子图，清晰标明不是全部实体。必须先在后端限制遍历工作量，再限制返回量。
- 邻域用当前表的双向邻接索引；逐层有界 SQL/递归查询配合 visited、每节点/总边预算和 deadline。高出度节点返回可继续分页的边，不扫描所有边再截断。
- 返回最少节点字段与边字段，不夹带原文、全 metadata、embedding、候选或整个审计链；目标常用首屏 JSON ≤ 200KB，压缩传输；超限有明确分页。
- 通用知识图谱的 ECharts 默认不对大图持续 force；局部布局/复用坐标、合并增量、停止静态布局动画。超过限额展示概览/展开，不让 UI 卡死。
- 保留 generation gate，增加 AbortController、输入 debounce、同 scope 请求合并、按 project revision 的缓存；导航切换卸载图实例/监听器，避免内存泄漏。
- 本体词表、审核、任务、证据、历史列表同样分页/必要时虚拟列表；不能只优化 graph 页面。证据保持精确冻结来源，不以当前文档摘要替代历史原文。

### 6.4 本体工作台：React 画布与阶段化界面

本体工作台只迁移自身，使用 Vite 构建独立 React bundle，由现有 `web/index.html` 的本体入口挂载；其他页面和路由保持原生 JavaScript。依赖固定为 React、`@xyflow/react` 和 `elkjs`，锁文件纳入版本控制，构建产物由现有静态资源服务加载。画布采用 Dify 类布局：左侧是可创建/定位的术语面板，中间是可缩放、拖拽和连线的画布，右侧是当前节点或边的属性检查器；顶部只保留草案状态、撤销/重做、自动布局、提交审核和“版本治理”入口。

- `Class` 是实体类型节点；`ObjectProperty` 是独立的关系节点；`DatatypeProperty` 显示为 Class 节点内的属性行，并具有独立的选中、编辑和连接端口。父级不是节点，而是带明确语义的 `parent` 边。
- 新建、重命名、停用、移动父级、设置 domain/range/datatype、修改结构化约束均先形成草案命令。一次拖拽若产生多条命令，调用批量事务接口，必须全部成功或全部回滚；客户端不得连续发多个独立请求制造半完成状态。
- 父类编辑同时提供画布连线和可搜索下拉框；下拉框只列可用 Class，排除自身、后代、重复父级和无权限项，并在禁用项旁显示原因。自由文本只用于创建新类的显式流程，不能偷偷充当父类 IRI。
- `elkjs` 负责分层自动布局；用户坐标作为草案视图偏好保存。小草案可加载全部节点；超过 200 节点时默认加载当前焦点及 1 跳邻域，允许逐层扩展到 2 跳并显示“当前仅为局部视图”，禁止为了布局下载全本体。
- 删除原来含义不清的“矩阵”。需要比较结构时，在节点检查器中显示父级、domain、range、约束和受影响对象；需要批量影响分析时进入审核摘要，不制造第二套矩阵概念。
- 设计、审核、校验、发布是四个独立工作区，各自维护筛选和布局状态；切换阶段时不残留上一步左右栏。所谓“搜索候选”拆成：设计态的“术语定位”、审核态的“异常/操作筛选”、校验态的“问题筛选”、版本治理的“版本筛选”，不共享一个含义不明的搜索框或 query state。
- “版本治理”是顶部单独按钮，但在当前工作台内打开抽屉/全屏面板，展示当前 head、草案 base、不可变版本时间线、差异和冲突；不得再跳转到隐藏的旧 `tab-ontology` 或废弃页面。已发布版本只能查看/基于其创建新草案，不能原地编辑。

### 6.5 画布端口与连接类型契约

连接合法性由端口类型、前端即时检查和服务端权威检查共同保证。前端禁用无效端口并解释原因；服务端不能信任节点类型或客户端生成的命令。

| 起点 → 终点 | 语义/命令 | 允许条件 | 禁止示例 |
|---|---|---|---|
| Class → Class | `add_parent(target=子类,parent=父类)` | 两端均为可见 Class；无重复、自环或间接继承环 | Relation→父级、子类→自身、连接到后代 |
| Class → ObjectProperty | `add_domain(target=关系,domain=类)` | 起点为 Class、终点为关系 domain 端口 | Relation→Relation、接到 range 端口 |
| ObjectProperty → Class | `add_range(target=关系,range=类)` | 起点为关系 range 端口、终点为 Class | Relation→Relation、Attribute→Class range |
| Class → DatatypeProperty 行 | `add_domain(target=属性,domain=类)` | 属性行属于当前草案且起点为 Class | Attribute 作为实体关系端点 |
| DatatypeProperty → XSD datatype | `set_datatype` | 只能从服务端支持的 XSD 枚举中选择一个 | 自由文本 datatype、Attribute→Class range |

删除边分别映射为 `remove_parent/remove_domain/remove_range`。已发布术语不做物理删除，只能生成停用命令；停用前检查被父级、domain、range、shape 和正式事实引用。V1 的结构化属性约束至少覆盖 datatype、required、`minCount/maxCount`、枚举、pattern 和数值边界，并生成受控 SHACL 操作；高级 Turtle 仅作为只读预览或管理员受控入口，不能成为常规编辑方式。

### 6.6 整批自动审查、人工例外与发布门禁

提交草案时冻结 draft revision、base/source 版本和全部 operation fingerprints，服务端一次完成图结构、新写入约束、历史数据影响和风险评估，返回稳定的 `review_plan`：

- `safe`：无 error/warning、低风险、来源与版本仍有效的操作。页面默认全部选中，审核人点击一次“批准全部安全项”；服务端在一个事务中复核 plan fingerprint，写入所有 approve 决定，actor 必须来自认证上下文，并记录策略版本、时间和幂等键。操作数量不能靠前端拆成多个请求，现有 100 条限制若保留应由服务端内部分页但对外仍保持一次原子业务动作。
- `manual`：高风险、带 warning、AI 建议、影响超过阈值或证据不足的操作。页面默认只展示这些例外，按问题聚合操作和影响对象；批准必须填写理由/确认 warning，也可拒绝或要求调整。
- `blocked`：结构错误、循环、非法类型、缺失依赖或无法满足约束的操作。服务端拒绝 approve，只能拒绝或要求调整；“要求调整”回到设计态并立即使旧 review/validation fingerprint 失效。

截至本规划基线，审核/发布后端不是空壳，但尚未形成上述产品化闭环。源码核对结果如下，实施时必须在现有不变量上增量扩展，不能另造一套前端判断：

| 能力 | 当前状态 | 源码基线与边界 |
|---|---|---|
| 提交时整批校验 | 已有 | `OntologyDrafts.submit` 对全部有效操作调用 `_compute_validation`，保存 report 与 fingerprint；校验覆盖定义图、正式记录时间线和依赖影响 |
| 校验快照重算 | 已有 | `OntologyDrafts.validate` 重新计算整份草案快照并 CAS 更新，不能把旧结果当新结果 |
| 批量决定安全门槛 | 已有但需封装 | `OntologyDrafts.decide` 接受 1–100 个决定；多项请求只允许无 warning 的低风险 approve，阻断项不能 approve，高风险/warning 批准要求理由 |
| 退回修改 | 已有 | `request_changes` 把草案退回 editing，并清空旧 validation report/fingerprint |
| 发布前复核 | 已有 | `publish_preflight` 检查 revision、当前本体、validation fingerprint、最终决定和批准子集；`publish` 使用 idempotency key 并委托原子发布边界 |
| 类型化定义图校验 | 已有 | `apply_operations/_validate_definition_graph` 已检查术语 kind、父类存在、继承 DAG、domain/range owner、Class 引用、Attribute 单 datatype、SHACL target/path 和停用依赖 |
| 稳定 `review_plan`、问题聚合、只读 `publish_readiness` | 待新增 | 当前前端仍需拼接/猜测状态，缺少稳定 reason codes、策略版本和完整禁用原因契约 |
| 画布批量命令事务、常用结构化 SHACL 操作 | 待新增 | 当前一次 UI 动作可能拆成多请求，常用基数/枚举/pattern 仍缺正式类型化命令 |

`review_plan` 必须由服务端返回 `classification`、稳定 reason codes、risk、关联 operation IDs、影响统计、policy_version 和 fingerprint，前端不得自行猜测安全等级。最终决定完成后，对“最终批准子集”重新执行校验并生成 validation fingerprint。

发布前提供只读 `publish_readiness`，逐条返回 `code/message/severity/operation_ids/remediation`，至少检查：草案状态、base/head 冲突、source 版本、每条操作最终决定、review/validation fingerprint、warning 理由、发布人权限和幂等键。发布按钮可禁用，但按钮旁必须始终显示全部未满足条件，并能定位到对应异常；不得只显示灰色按钮。发布时再次服务端复核并以事务原子提交本体、版本、决定和 provenance，只发布最终批准子集。

## 7. 批量上传与可靠任务

1. API 接收并校验文件 → 持久化 raw asset/hash → 创建 PG job/ingest run → 返回 job_id；禁止把进程内闭包作为可恢复任务的唯一参数。
2. Worker 用 `FOR UPDATE SKIP LOCKED` 认领；保存 lease_until/heartbeat/attempt/max_attempts/fencing token。认领事务立即提交，不在任务运行期间占着行锁或连接。
3. 原文解析、分块、抽取、候选治理、正式物化、索引分别记录阶段输出、输入摘要、模型/本体版本；只重跑无有效 checkpoint 的阶段。
4. 租约过期重领采用新 fencing token；旧 worker 即使恢复也不能提交/更新新状态。重启 API 不再把所有进程的 running 任务统一标记失败。
5. 幂等范围包含 project、document version/content hash、解析/模型/本体配置与显式重跑意图；不同项目不共享授权数据。重试和并发提交不能重复生成事实/活动。
6. 解析器在独立进程运行，设输入字节、页数、压缩展开量、文本字符、内存/CPU/总时间预算；超时终止进程并记录可诊断失败。原文引用在任务完成/失败保留期内有效，不按临时目录年龄误删正在使用的文件。
7. 抽取 worker 与 API 不争同一线程池；查询 encoder 与批量 encoder 使用独立服务/队列和资源预算。有 GPU 时需显存/优先级配额，单纯复制模型可能 OOM，先测专用查询实例成本；无 GPU 则保留查询 CPU 配额。
8. 向量批量初始每批 32 条、SQL 插入初始每批 200 条，按 bytes/token/时间同时限制并测优；这不是固定最优值。大文档 chunk 去重和稳定排序，来源定位不丢。
9. 按项目配额和公平调度防大客户占满队列；重试退避、永久失败队列、取消和限流可观察。job 日志追加到事件表并分页，不反复重写大 JSON。
10. 取消检查在阶段边界和长批次中执行；已提交正式事实不能假装未发生，后续撤回应走现有治理动作。

## 8. Semantica 复用白名单与治理边界

### 8.1 当前已用与后续允许范围

| 能力 | 当前证据 | 允许复用方式 | 禁止/必须防范 |
|---|---|---|---|
| Docling 解析 | `document_parser.py` 经 `semantica.parse.DoclingParser` | 保留适配器，返回结构化文本、页/位置、告警；独立资源隔离 | 解析器直接访问业务数据库、无界外链/下载 |
| LLM 实体/关系/属性抽取 | `semantica_adapter.py`、`attribute_extraction.py` | 输入固定本体摘要/源文本，输出受 Pydantic 校验的事实建议；保留严格错误传播 | 抽取失败静默变启发式成功、把提示词当证据、任意工具执行 |
| 开放发现 | `SemanticaExtractor.discover`、`_route_open_facts` | 产生候选与异常隔离记录，保留名称冲突和证据分类 | 开放词汇直接发布正式 schema/事实 |
| 本体归纳 | `ontology_discovery._induce` 调 `OntologyGenerator` | 提供候选类/属性/层级建议，由本项目稳定 IRI/词汇/草案审核控制 | 观察到的端点类型自动变成正式 domain/range；自动删除人工约束 |
| 去重/融合建议 | `Governance.resolve` 用 `DuplicateDetector`；`reconcile` 用 `EntityMerger` | 仅在经过项目、类型、时态过滤的候选集合内运行；业务代码持有最终决策/账本 | 对全库做两两比较、向量相似即自动合并、Semantica 自行写真值 |
| 冲突检测/质量/评估 | 尚不能依据旧文档声称已接入 | 后续可作为离线报告或审核建议，先证明 API/行为兼容和收益 | 输出直接撤回事实或改写正式本体 |
| 通用 pipeline/storage/provenance/change management/自动 agent 推理 | 不是本项目权威流程 | 本轮不引入第二套；必要算法以窄适配器复用 | 与 PG 账本双真值、绕过权限/事务/审核、开放任意框架配置给前端 |

### 8.2 适配器契约

- 客户端只选择有限业务模式，例如“本体引导抽取/开放候选发现”，不透传 provider 任意 kwargs、模块路径、工具名、执行代码或数据库凭证。
- 每次调用记录组件版本、模型、模板版本、输入摘要、本体版本和结果来源；日志脱敏，不把密钥/完整供应商异常回传用户。
- 结构化输出逐项检查证据、类型/谓词、端点、typed value、时间和名称冲突；不能落证者进入 review/quarantine，不偷偷丢弃，也不直接正式化。
- 保留当前 StrictNER/StrictRelations 避免上游静默启发式降级的行为；空抽取结果与调用失败必须可区分。
- Docling 关闭 OCR 的适配目前使用私有 `_converter`；这是版本敏感点。保留 disabled/auto 的真实解析回归，升级时优先公共 API，不能未经验证直接移除兼容逻辑。
- `_induce` 对已有关系约束的移除必须记录来源：仅可将已知旧自动推断约束作为待审核修改建议；人工正式约束默认保留。开放候选不能自动收紧或放松已发布规则。
- 精确锁定并测试 Semantica/Docling/模型相关依赖组合；`>=0.7,<0.8` 是依赖范围，不等于每个环境安装同一构建。已有 `uv.lock` 应更新，不重复创建第二套依赖真值。
- Semantica 不保证本业务的唯一真值、双时态或发布原子性；这些继续由 PostgreSQL 和本项目业务服务实现。

## 9. 安全、可观测性和运维

### 9.1 发布前安全门槛

- 统一认证依赖：生产基线为受验证的 OIDC/JWT 主体，检查签名、允许算法、issuer、audience、有效期，通过 issuer/subject 查询 `project_memberships`；部署身份网关只有提供同等验证契约时才替代该验证入口。不得信任任意 `X-User`/actor。项目角色至少 reader/editor/reviewer/admin，审核与管理动作逐项授权；首次管理员经受控 bootstrap 建立，不提供匿名提权接口。单机开发可有显式 dev 模式，只允许本机，不作为生产默认。
- 所有 project、record、artifact、job、draft、run、assertion、provenance ID 均做项目归属检查；拒绝跨项目复用引用。浏览器若采用 cookie，会话写请求须防 CSRF；若 bearer token，约束存储和 CORS。选择一种明确实现，不混合隐式信任。
- LLM/文档内容是不可信数据；不能改变系统权限、过滤条件或触发数据库/网络工具。供应商 endpoint 只允许管理员配置，限制外连目的，验证解析库是否会加载 HTML/文档外部资源。
- HTML 展示使用 textContent/安全转义；Turtle/Markdown/证据渲染实施明确允许列表。测试标签、属性值、错误消息及来源 URL 的脚本/危险协议输入。
- 管理端口限内网/回环，Milvus 鉴权和网络访问控制；生产 TLS/反代 body/time/rate limits；数据库备份、原文持久卷和日志访问受控。
- `/health/live` 不依赖远程模型；`/health/ready` 检查 PG/schema/必要 worker 能力并以摘要呈现；深诊断仅管理员可读。

### 9.2 指标和故障诊断

每个请求/任务贯穿 request_id、project_id（可脱敏）、job/run/activity_id。采集数据库 pool 等待、SQL 次数/耗时/行数、过滤后候选数、embedding 排队/执行、Milvus 耗时、序列化字节、浏览器 parse/layout/render、outbox lag 和 lease 重试。

指标标签不放原文/自由查询文本/无限 ID，防止隐私泄漏和高基数爆炸。慢查询用 `pg_stat_statements`、受控 EXPLAIN；生产不随意执行会修改数据的 `EXPLAIN ANALYZE`。错误日志区分业务校验失败、资源超时、投影失败和程序异常。

PG 备份从首次新写入开始：建议初始 RPO ≤ 15 分钟、RTO ≤ 1 小时，配合 WAL/PITR 或经验证的替代方案；实际值按磁盘/备份设施验收。定期恢复演练，验证本体、断言、溯源、原文引用完整；向量索引可重建，不代替主库备份。

## 10. 实施工作包与顺序

下面所有工作项初始均未完成。`新增` 路径是实施目标，不表示当前已有；若基线后出现同职责文件，应复用并更新本计划映射。避免一边迁库一边无关重构。

每个工作包执行小循环：先添加所列失败测试 → 单独运行确认因缺行为失败（不是缺依赖）→ 最小实现 → 通过对应测试和相关契约 → 更新本文件记录 → 在用户允许的提交工作流中做一个独立提交。不在未请求时自动推送或部署。

依赖主线：T00 → T01 → T02 → T03 → T04 → T05 → T06/T07/T08 → T09/T10 → T11 → T12 → T13。T11 的认证/网络边界设计从 T01 即开始，不能等到最后才考虑。T07/T08 的队列表可以在 T02 建立，但业务接入分别验收。

### T00 — 冻结范围和现有业务契约（P0）

**修改：** `tests/service/test_repository.py`、`test_formal_writes.py`、`test_ontology_drafts.py`、`test_discovery_run_store.py`、`test_governance.py`、`test_evidence.py`。**新增：** `tests/contracts/test_ontology_invariants.py`、`tests/contracts/test_scope_semantics.py`、`docs/performance/baseline.md`。

- [ ] 记录当前 HEAD、运行环境、路由清单、全部 store 与当前 SQLite schema/触发器到 baseline；不复制真实密钥和用户正文。
- [ ] 将第 4 节不变量变成公开行为测试，包括双时态端点、候选隔离、多轮物化、合并撤回和 typed literal。
- [ ] 保存当前可运行测试的通过/失败/skip 明细；修复环境缺失再记录。新 PG 契约测试可先因未实现失败，不把现有 SQLite 通过当 PG 通过。
- [ ] 给第 3 节问题逐项绑定测试/指标；每个变化明确是否影响 API 契约。

**验证：** `uv run --no-sync python -m pytest tests/contracts tests/service -q`（见第 11 节环境准备）。预期：当前业务契约可运行；PG 新能力缺失作为后续 red，不宣称全通过。

### T01 — 可观测基线与容量夹具（P0）

**修改：** `knowledge_service/core/logging.py`、`knowledge_service/api/__init__.py`。**新增：** `scripts/bench_seed.py`、`scripts/bench_load.py`、`tests/performance/test_query_shape.py`。

**分两段执行：** T02 前完成当前系统的只读基线、指标和容量夹具/脚本契约定义；T03 完成后再连接隔离 PostgreSQL 压测库，补齐并运行容量生成器与负载脚本。当前 SQLite 基线不新增大规模用户数据，也不开发用于长期维护的 SQLite 压测后端。T01 的 PG 部分不是开始 T02 的前置阻塞条件。

- [ ] 给请求/数据库/编码/投影阶段添加可关联耗时与返回字节，不记录正文。
- [ ] 实现可重复、固定随机种子、ontology-valid 的 L1/L2/L3 数据生成；默认只允许明确标记的 benchmark DB/项目。
- [ ] 实现混合负载客户端，记录完整延迟样本、错误、partial/degraded、任务积压和运行配置；不要只输出平均值。
- [ ] 为 query/search/graph 建立“不能全量装载项目”守卫测试，并保存基线 EXPLAIN/浏览器性能结果。

**验证：** `uv run --no-sync python -m pytest tests/performance/test_query_shape.py -q`；基线允许暴露现有扫描失败，后续任务须转绿。压测脚本禁止对默认用户项目生成/清除数据。

### T02 — PostgreSQL 基础设施、DDL 与迁移（P0）

**修改：** `pyproject.toml`、`uv.lock`、`.env.example`、`compose.yml`、`knowledge_service/core/config.py`。**新增：** `knowledge_service/repository/connection.py`、`knowledge_service/repository/migrate.py`、`knowledge_service/repository/migrations/0001_postgresql.sql`、`scripts/bootstrap-postgres.ps1`、`tests/postgres/conftest.py`、`tests/postgres/test_schema.py`。

- [ ] 添加 psycopg/pool 依赖和锁文件；确认现有 PostgreSQL 镜像可用并固定版本/必要 digest；不因已有 Compose 就另起冲突服务。
- [ ] 创建迁移/应用角色、正确 schema 权限与连接池；bootstrap 从受控环境读取凭据，禁止在日志打印 DSN 密码。普通启动不执行 bootstrap。
- [ ] 编写第 5.2 节表/索引/触发器与迁移 checksum；将现有 schema 每一类映射列入测试，特别是发现 store 与制品中的数据；验证安装包包含 SQL 资源及项目成员角色约束。
- [ ] 建立 PG 空库测试隔离，每次测试用独立 schema/事务策略；真并发测试使用不同连接，不被外层测试事务掩盖。
- [ ] 覆盖重复运行迁移、两个迁移器竞争、缺权限、schema 版本不匹配、跨项目 FK、不可变历史和应用角色不能 DDL。

**验证：** `uv run --no-sync python -m knowledge_service.repository.migrate`；`uv run --no-sync python -m pytest tests/postgres/test_schema.py -q`。预期：迁移幂等、checksum 校验有效、约束真实生效，无 SQLite 文件产生。

### T03 — 完整替换 Repository 与所有 store（P0）

**修改：** `knowledge_service/repository/core.py`、`__init__.py`、`assertions_store.py`、`ingest_store.py`、`review_store.py`、`provenance_store.py`、`ontology_draft_store.py`、`discovery_run_store.py`、`snapshot_validation.py`；`knowledge_service/api/__init__.py`、`__main__.py`；`scripts/smoke_service.py`、`sync_local_neo4j.py`、`verify_parity.py`。**新增：** `tests/postgres/test_repository_contract.py`、`tests/postgres/test_governance_stores.py`。

- [ ] 将连接/事务生命周期改为 unit-of-work，替换占位符、row factory、JSON/日期处理、ON CONFLICT、SQLite 专属 pragma/FTS/trigger/savepoint 用法。
- [ ] 移植项目/记录/本体/制品/配置，随后断言/摄取/发现/审核/溯源/草案；每类运行现有业务回归，不遗漏 project delete/export/restore 的事务和引用校验。
- [ ] 移除所有外部 `_db/_lock` 直接访问；保留必要的公开 Repository 路径，但不保留 SQLite 后端。
- [ ] 将现有临时 sqlite 路径 fixtures 和 sqlite 异常断言改为真实 PG fixtures/业务异常；运行时缺 PG 必须失败，不自动 fallback。
- [ ] 删除 SQLite schema migration/vector blob 兼容运行逻辑；旧数据导入/回填代码若无新业务职责则移除，受治理的普通文件导入能力不得误删。

**验证：** `uv run --no-sync python -m pytest tests/postgres/test_repository_contract.py tests/postgres/test_governance_stores.py tests/service/test_project_management.py tests/service/test_provenance_repository.py tests/service/test_discovery_run_store.py -q`。预期：全绿，数据只入指定测试 PG，历史/引用语义不变。

### T04 — 正式提交原子性与并发控制（P0）

**修改：** `services/formal_writes.py`、`service.py`、`ontology_drafts.py`、`ontology_discovery.py`、`governance.py`、`reviews.py`、`repository/core.py`（均位于 `knowledge_service/`）。**新增：** `tests/postgres/test_concurrent_writes.py`、`tests/postgres/test_publish_atomicity.py`。

- [ ] 写同实体同时修订、相同 fact key 并发、两个草案发布、合并与撤回竞争测试。
- [ ] 实现预期版本/CAS、固定锁顺序、幂等操作和短事务；网络/模型调用移出事务与全局锁。
- [ ] 正式事实、断言支持、活动、账本、当前投影和 outbox 同事务提交；在每个边界注入异常并检查无半提交。
- [ ] 复杂校验使用 revision 快照和提交复验；发生冲突清晰返回 409 或重试状态，不能盲目覆盖。

**验证：** `uv run --no-sync python -m pytest tests/postgres/test_concurrent_writes.py tests/postgres/test_publish_atomicity.py tests/service/test_formal_writes.py tests/service/test_governance.py -q`。预期：并发者最多一个旧版本写入成功，失败方无孤儿记录，跨项目操作互不阻塞。

### T05 — 有界查询、过滤与真实分页（P0）

**新增：** `knowledge_service/repository/query_scope.py`、`query_compiler.py`、`read_models.py`、`tests/postgres/test_filter_parity.py`、`tests/postgres/test_pagination.py`。**修改：** `repository/core.py`、`utils/filters.py`、`services/service.py`、`api/knowledge.py`、`api/evidence.py`、`api/provenance.py`、`api/reviews.py`、`api/ontology_discovery.py`、`api/ontology_drafts.py`、`models.py`（均位于 `knowledge_service/`）。

- [ ] 添加 filter missing/null/bool/array/time 差分测试及 scope 端点可见性测试。
- [ ] 实现热字段过滤、参数化 SQL、总预算和版本化 QueryScope；只加载必要列/必要记录。
- [ ] 列表、历史、来源、断言、任务、发现候选采用游标分页；详情按主键/版本键读取，不先加载所有版本。
- [ ] API 输出 cursor/truncated/scope_revision；保留有意支持的旧 limit/offset 输入适配到 SQL，但限制深 offset，前端迁完后明确弃用策略。
- [ ] 建立 EXPLAIN/行数守卫，验证读路径不依赖项目全量 JSON decode。

**验证：** `uv run --no-sync python -m pytest tests/postgres/test_filter_parity.py tests/postgres/test_pagination.py tests/contracts/test_scope_semantics.py tests/service/test_metadata_facets.py -q`。预期：新 SQL 与冻结语义一致，翻页无跨项目、无丢失/重复（固定快照条件下）。

### T06 — 检索召回完整性、中文索引与实体解析（P0）

**修改：** `knowledge_service/services/retrieval.py`、`entity_resolution.py`、`reconciliation.py`、`governance.py`、`integrations/milvus_store.py`。**新增：** `knowledge_service/services/search_candidates.py`、`knowledge_service/repository/text_search.py`、`tests/postgres/test_keyword_search.py`、`tests/service/test_retrieval_scope_complete.py`。

- [ ] 构造第 5,001 个之后才命中的实体，以及空候选集、引号 ID、kind 配额不平衡、历史查询和中文别名测试。
- [ ] 移除候选 ID 静默截断和全量预载；实现第 6.1 节的预过滤/分路召回/PG 复验/有界补召回。
- [ ] 关键词路径断言 encoder 调用次数为零；中文索引从空 PG 自动生成并可重建。
- [ ] 把实体消歧的本地向量假设替换为 candidate service；批量名称/别名匹配，Semantica 只运行有限候选。
- [ ] 明确语义降级/历史语义不支持契约；冻结相关性评估集，防“更快但查不准”。

**验证：** `uv run --no-sync python -m pytest tests/postgres/test_keyword_search.py tests/service/test_retrieval_scope_complete.py tests/service/test_entity_resolution.py tests/service/test_hybrid_retrieval.py tests/service/test_search_budgets.py -q`。预期：命中集合不因候选 >5,000 丢失，跨项目/过期结果为零；mock 与真实 Milvus 契约分别运行。

### T07 — 可靠索引同步和无中断重建（P0）

**新增：** `knowledge_service/repository/outbox_store.py`、`knowledge_service/services/projection_worker.py`、`tests/postgres/test_outbox.py`、`tests/integration/test_milvus_projection.py`。**修改：** `services/service.py`、`api/workspace.py`、`integrations/milvus_store.py`、`integrations/neo4j_store.py`（位于 `knowledge_service/`）。

- [ ] 以索引宕机、重复/乱序事件、删除后重试、编码失败、worker 重启测试建立 red。
- [ ] 实现 outbox 消费、水位线、幂等与受控批次；索引失败不撤销已提交 PG，也不标 semantic ready。
- [ ] 重建流式处理，新增 generation 并追平增量；原子切换后核验查询版本和模型一致。
- [ ] 初次部署全新 namespace，历史 namespace 不可被新 QueryScope 选中；Neo4j 同样不能泄漏旧投影。

**验证：** `uv run --no-sync python -m pytest tests/postgres/test_outbox.py tests/integration/test_milvus_projection.py -q`。预期：断网恢复最终追平；重建失败 active 不变；无旧版本复活；发布测试不得因未启动真实 Milvus 而整体 skip。

### T08 — 持久任务、解析沙箱与模型资源隔离（P0）

**新增：** `knowledge_service/repository/job_store.py`、`knowledge_service/worker.py`、`knowledge_service/services/parse_worker.py`、`tests/postgres/test_job_leases.py`、`tests/integration/test_worker_recovery.py`。**修改：** `services/jobs.py`、`document_uploads.py`、`service.py`、`integrations/document_parser.py`、`embeddings.py`、`api/knowledge.py`、`api/workspace.py`、`compose.yml`。

- [ ] 写两个 worker 竞争、租约超时、旧 worker 晚提交、取消和阶段幂等测试。
- [ ] 实现序列化任务参数、SKIP LOCKED/lease/fencing、阶段 checkpoint、日志事件分页和退避重试。
- [ ] 原文持久化并按引用/保留期清理；重启可重新定位输入；解析资源超限失败不拖垮 API。
- [ ] 解析/抽取/编码 worker 配额分离；查询 encoder 预热/专用预算；移除服务全局模型长锁的跨业务影响。
- [ ] UI 接入阶段与 readiness，而不是把 HTTP 202 当 ingest 完成。

**验证：** `uv run --no-sync python -m pytest tests/postgres/test_job_leases.py tests/integration/test_worker_recovery.py tests/service/test_document_parser.py tests/service/test_ingest_runs.py -q`。预期：进程被终止后可恢复，过期 fencing token 拒绝写入，重复处理不重复物化。

### T09 — 本体校验优化与 Semantica 边界加固（P0/P1）

**新增：** `knowledge_service/services/validation_scope.py`、`knowledge_service/services/ontology_review_policy.py`、`knowledge_service/integrations/semantica_contracts.py`、`tests/contracts/test_incremental_validation.py`、`tests/contracts/test_ontology_review_plan.py`、`tests/contracts/test_semantica_boundary.py`。**修改：** `services/ontology.py`、`ontology_discovery.py`、`ontology_adapters.py`、`ontology_drafts.py`、`review_validation.py`、`api/ontology_drafts.py`、`models.py`、`integrations/semantica_adapter.py`、`document_parser.py`（位于 `knowledge_service/`）。

- [ ] 建立完整校验作为 oracle，覆盖逆向约束、数量约束、关系端点、继承、全局/SPARQL shape 和跨有效期切片。
- [ ] 对可证明局部的 shape 计算受影响闭包；其他 shape 后台全量校验。缓存按本体内容/项目 revision/有效时间切片，不能仅按文件路径缓存。
- [ ] 候选/来源 metadata 拆成可分页引用，验证精确证据、断言支持和合并撤回仍完整。
- [ ] 保护 `_induce` baseline 中人工正式约束；修改只能形成待审 operation。保留多轮发现完成规划已修复的冻结 ID/无空发布行为。
- [ ] 增加服务端 `review_plan`，以版本化策略稳定返回 `safe/manual/blocked`、reason codes、影响统计与 fingerprint；前端不得复刻或猜测风险规则。
- [ ] 增加原子“批准全部安全项”命令：复核 draft/plan fingerprint 后一次写入安全项决定，actor 只取认证上下文；并发重试幂等，任一项失效则整批拒绝且不产生部分决定。
- [ ] 增加按问题聚合的审查摘要、只读 `publish_readiness` 和画布批量命令事务接口；禁用原因须稳定、可定位、可恢复，结构化 SHACL 命令覆盖常用属性约束。
- [ ] 对 Semantica 允许输入/输出建立严格 DTO；验证异常/空结果区分、无启发式暗降级、OCR 兼容和版本锁。

**验证：** `uv run --no-sync python -m pytest tests/contracts/test_incremental_validation.py tests/contracts/test_ontology_review_plan.py tests/contracts/test_semantica_boundary.py tests/service/test_ontology.py tests/service/test_ontology_drafts.py tests/service/test_ontology_discovery.py tests/service/test_semantica_adapter.py tests/service/test_relation_constraint_modes.py -q`。预期：局部校验和完整 oracle 同判定；安全项一次确认全成或全败；manual/blocked 不会被批量批准；每个发布禁用原因可复现；正式约束不被发现过程自动删除。

### T10 — 本体画布、审核发布与其余工作台首屏（P0/P1）

**修改：** `knowledge_service/services/explorer.py`、`answers.py`、`evidence.py`、`api/workspace.py`、`web/index.html`、`web/workspace.js`、`web/ontology-workbench.js`、静态资源构建/服务配置及相关列表模块。**新增：** 前端包清单与锁文件、独立本体工作台 React 源码、`tests/contracts/test_ontology_canvas_commands.py`、`tests/ui/test_ontology_workbench.py`、`tests/service/test_bounded_graph.py`、`tests/ui/test_large_workspace.py`。**删除：** 确认无运行时引用的 `knowledge_service/web/ontology-manager.html`。

- [ ] 写高出度图、循环关系、历史 scope、无中心全图入口的有界行为测试。
- [ ] 用 SQL 邻接查询和聚合代替全库遍历；应用节点/边/字节/计算预算，返回可继续展开信息。
- [ ] 先写画布命令与 UI 失败测试，再引入 React、`@xyflow/react`、`elkjs`；只挂载本体工作台 island，不迁移其他页面。实现 Class/Relation 节点、属性行、分层自动布局、局部展开、草案撤销/重做和右侧类型化检查器。
- [ ] 冻结端口/连接契约：Class→Class 父级、Class→Relation/Attribute domain、Relation→Class range、Attribute→受支持 XSD datatype；前后端共同拒绝错误 kind、自环、重复边、继承环和非法 range。
- [ ] 父类改为可搜索下拉框并与画布边双向同步；删除“矩阵”；设计/审核/校验/发布使用独立布局和筛选状态，版本治理在当前工作台面板内完成而非跳转旧 tab。
- [ ] 审核首屏默认异常队列，并提供一次“批准全部安全项”；manual 显示理由/证据/影响，blocked 不显示可批准动作。发布按钮旁实时展示 `publish_readiness` 全部原因，并可跳转到对应问题。
- [ ] 结构化属性编辑覆盖 datatype、required、min/max count、枚举、pattern 和数值边界；多命令操作只调用原子批量接口。已发布术语只允许停用，不允许物理删除。
- [ ] 前端采用分页摘要、局部更新/布局、请求取消和 scope 缓存；证据详情延迟加载；保留 generation gate。
- [ ] 测量网络、JSON parse、布局、可交互时间和反复导航内存；静态 DOM 测试不能代替真实浏览器性能。
- [ ] 新入口和深链 smoke 通过后删除 `ontology-manager.html` 及其孤立引用；静态引用扫描和浏览器测试证明不存在旧页面跳转或双入口。

**验证：** `uv run --no-sync python -m pytest tests/contracts/test_ontology_canvas_commands.py tests/service/test_bounded_graph.py tests/service/test_explorer.py tests/test_web_ui_contract.py tests/ui/test_ontology_workbench.py tests/ui/test_large_workspace.py -q`；运行前端 typecheck、单元测试和生产构建；`node --test tests/service/graph-types.test.cjs`。预期：错误连线在前后端均被拒绝；安全项一次确认、异常人工处理、发布禁用原因和版本治理可真实操作；废弃页无引用且不存在；超大项目只返回有界子图，首屏满足第 2 节预算。

### T11 — 权限、输入/资源安全与运维（P0）

**新增：** `knowledge_service/api/auth.py`、`knowledge_service/services/access_control.py`、`tests/security/test_project_authorization.py`、`test_input_boundaries.py`、`test_resource_limits.py`（后两者也在 `tests/security/`）、`docs/operations/postgresql-runbook.md`。**修改：** `api/__init__.py` 及全部路由、`models.py`、`core/config.py`、`core/logging.py`、`compose.yml`、`.env.example`（位于对应仓库路径）。

- [ ] 建立角色/路由矩阵与恶意跨项目 ID、actor 冒用、全局任务越权测试。
- [ ] 统一 auth/context 与项目检查；审核 actor 从服务器身份生成；所有新增 cursor/job/evidence 入口同样授权。
- [ ] 测试 XSS/危险 URL、Milvus 字符串、过滤 AST 预算、SPARQL 计算取消、解析器展开/外部资源、prompt injection；记录已证实与未证实项目。
- [ ] 缩小网络暴露、密钥/健康/日志脱敏；配置数据库超时、备份、恢复和 worker 告警；不凭 env 名称假设角色存在。

**验证：** `uv run --no-sync python -m pytest tests/security -q`；在隔离恢复库执行运行手册的恢复演练。预期：无权限读写被拒绝且无副作用；资源攻击被限制；完整数据链可恢复。

### T12 — 全量容量、准确率与故障验收（P0）

**新增：** `tests/performance/test_slo_report.py`、`tests/fixtures/retrieval_gold.json`、`docs/performance/acceptance.md`；完善 T01 脚本。

- [ ] 生成 L2，包括 10 万实体之外的边、属性、chunk、版本、断言；不能只测没有关系的 10 万行表。
- [ ] 按第 2 节执行空闲/混合导入/高出度/复杂过滤/冷热/故障场景；保存原始样本和 EXPLAIN。
- [ ] 金标精确名称/IRI/约束过滤正确率 100%，授权/时态违规结果 0；ANN 召回相对受过滤精确向量基线 Recall@20 ≥ 0.95（初始门槛）；人工相关性集 nDCG@10 不低于冻结基线超过 2 个百分点。
- [ ] 验证上传期间在线延迟、任务恢复、outbox 追平、模型冷启动、PG/Milvus 短暂中断以及备份恢复；队列积压须有背压而非内存无限增大。
- [ ] 输出真实 p50/p95/p99、错误/限流/降级率、各阶段耗时、DB 锁/连接等待、内存峰值、召回指标与未达标项。失败项回到对应任务，不降低语义标准掩盖失败。

**验证：** 按第 11 节 bench 命令运行；`uv run --no-sync python -m pytest tests/performance/test_slo_report.py -q`。预期：读取实际报告并校验门槛，不用固定常数/模拟延迟让测试通过。

### T13 — 空库切换、清理和交接（P0）

**修改：** `README.md`、`.env.example`、`scripts/start-service.ps1`、`docs/milvus-向量化改造设计.md`、`docs/neo4j接入.md`；更新本文件状态。**新增：** `tests/integration/test_pg_only_startup.py`。

- [ ] 停止旧写入实例；确认目标 PG 是预期空业务库和正确 schema/namespace，不导入旧 SQLite。
- [ ] 新系统先以 smoke 项目验证本体发布→上传→候选/审核→正式事实→关键词/语义→图谱→证据→撤回全流程，再开放真实写入。
- [ ] 删除运行路径和活动测试中的 SQLite 依赖，更新启动/诊断/示例/投影文档；旧 SQLite 文件暂留隔离区，不当作 fallback，不自动删除共享数据目录。
- [ ] 关闭旧运行入口。切换失败在新写入前可停新实例修复；新写入后只能使用兼容 PG 的应用/schema 回滚或恢复 PG 备份，绝不重新启用旧 SQLite 接收新写入。
- [ ] 将验收报告、运行手册、依赖版本、未完成风险和任务状态移交；仅所有 P0 通过后标记完成。

**验证：** `uv run --no-sync python -m pytest tests/integration/test_pg_only_startup.py -q`；全量测试和真实 smoke。预期：缺 DSN 明确失败、SQLite 路径不能启动、业务运行不产生 `.sqlite` 文件、新索引不混入旧知识。

## 11. 统一执行/验证命令约定

以下是**未来实施时**的命令，不表示本次已经运行。新增脚本/测试先按对应任务创建。所有命令从仓库根目录执行；部署/备份/压测只指向明确隔离环境。

```powershell
# 实施新增依赖并更新 uv.lock 后，重建一致环境；不打印真实 .env。
uv sync --locked --extra test --extra semantica-runtime --extra milvus --extra local

# 通过环境/安全凭据机制配置 KG_DATABASE_URL、KG_TEST_DATABASE_URL。
# 测试配置必须拒绝测试库与业务库相同，不允许静默使用业务 DSN。
uv run --no-sync python -m knowledge_service.repository.migrate
uv run --no-sync python -m pytest tests/contracts tests/postgres tests/service tests/security tests/integration tests/ui tests/test_web_ui_contract.py -q

# 下列脚本是 T01/T12 的待实现入口；KG_BENCH_DATABASE_URL 只能指隔离压测库。
uv run --no-sync python scripts/bench_seed.py --profile L2 --seed 20260930
uv run --no-sync python scripts/bench_load.py --profile mixed --duration-seconds 1800 --rps 20 --users 50 --output .benchmarks/pg-l2-mixed.json
uv run --no-sync python -m pytest tests/performance -q

# 文档/代码卫生，不等于业务验收。
git diff --check
```

测试 fixture、bench 脚本要分别从 `KG_TEST_DATABASE_URL`、`KG_BENCH_DATABASE_URL` 注入仓库，禁止沿用业务 DSN；迁移命令使用当前明确指定的目标库。schema 清理必须验证 schema 名称/库标记，只清理本次运行创建的资源。真实模型、Milvus、浏览器测试按能力分组，但最终发布报告必须列出未执行组，不允许“全部 skip，套件通过”。

若使用 Conda 复现当前运行环境，可将 Python 命令替换为 `conda run -n llm_model python ...`；必须先同步同一锁定依赖集并记录差异，不能同时把两套环境都称为唯一验证环境。浏览器性能测试固定 Chromium/Edge 版本和 viewport，测试不访问用户现有个人浏览器会话。

## 12. 完成标准与智能体交接

### 12.1 最终完成标准

- [ ] 应用/worker/测试均以 PostgreSQL 为唯一业务存储；缺 PG 不回退；旧数据未迁入。
- [ ] 第 4 节全部领域不变量通过，包括多轮发现、人工约束保留、历史来源、类型化事实和合并撤回。
- [ ] 正式提交与 outbox 原子，任务重启可恢复，过期 worker 无权提交，索引状态真实。
- [ ] 无 5,000 候选截断、全项目预载检索、假分页和默认全图渲染；限制均可见、可继续访问。
- [ ] 本体工作台通过 React 画布直接编辑 Class、Relation、属性、父级和结构化约束；所有连接遵守第 6.5 节类型契约，错误连接不能形成草案命令。
- [ ] `review_plan` 可稳定重放；安全项由系统整批审查、审核人一次确认，manual/blocked 只走例外流程；任何失效都不会产生部分批准。
- [ ] 发布按钮始终展示完整 `publish_readiness` 原因；版本治理不跳转旧入口；`ontology-manager.html` 及孤立引用已删除且正式入口回归通过。
- [ ] L2 混合负载常用交互达到第 2 节目标，并同时通过召回/授权/时态正确性验收。
- [ ] 权限/输入/解析资源隔离、备份恢复、索引重建、限流告警已验收。
- [ ] Semantica 只在白名单内复用，不能绕过正式治理；模型异常/降级不伪装成功。
- [ ] README、环境示例、部署和投影文档与实际一致；未完成项和执行证据可追踪。

### 12.2 下一位执行者的起点

1. 阅读本文第 1、4、8 节与 `CONTEXT.md`，确认 PostgreSQL-only/no-import 和本体边界。
2. 检查工作区/HEAD；使用代码知识图谱定位符号，图谱落后时重新索引；不要使用旧的扁平目录项目索引代替当前模块化代码。
3. 从 T00 开始，先冻结语义再迁库。每完成工作包，在下表补提交号、测试命令/报告、剩余风险并勾选对应项。
4. 不应直接实施的动作：删除旧 DB/宿主机 Milvus 数据、把 Neo4j 改为权威库、让 Semantica 自动发布、跳过 SHACL、伪造性能报告、未经范围确认把公开 API 扩展成任意框架执行入口。
5. 如新证据改变方案，记录“原决策→新证据→影响的不变量→替代方案/验收”，再更新计划；不能仅因为实现方便删掉业务能力。

| 工作包 | 当前状态 | 完成证据要求 |
|---|---|---|
| T00–T01 | 未实施 | 业务契约、可复现基线与容量夹具 |
| T02–T04 | 未实施 | PG DDL/完整 store 替换/并发原子性报告 |
| T05–T07 | 未实施 | 查询语义/完整召回/可靠索引测试 |
| T08–T09 | 未实施 | 故障恢复/本体校验等价/Semantica 边界测试 |
| T10–T11 | 未实施 | 真实浏览器性能/安全与恢复报告 |
| T12–T13 | 未实施 | L2 验收/空库切换/PG-only smoke/运维交接 |

规划质量记录：2026-09-30 原 PostgreSQL 规划已完成一次独立文档审查，结论为 Approved，并已吸收 T01 分阶段执行建议；同日新增的本体工作台、类型化画布和自动审查方案按用户要求由主 agent 自审，未派发子 agent。该记录仅表示规划可交接，不表示代码改造、容量测试、安全验收或工作台验收已经完成。

## 13. 参考资料与冲突处理

- [本体领域语言](../CONTEXT.md)：当前领域契约。
- [2026-09-30 多轮发现完成规划](superpowers/plans/2026-09-30-discovery-workbench-completion.md)：保留已完成的多轮治理行为；历史测试数字不当成本次验证。
- [Semantica 唯一真相源机制](2026-09-21-Semantica唯一真相源机制.md)：理解框架不自动提供本业务 SSOT；所引旧版本源码需按当前锁版本复核。
- [Semantica 能力覆盖与借鉴分析](2026-09-21-Semantica能力覆盖与TrustGraph借鉴分析.md)：背景材料；其中“parse 未接入/溯源缺失/依赖未锁”等旧判断不再作为当前事实。
- [Milvus 改造设计](milvus-向量化改造设计.md)：历史路径参考；SQLite 保留历史向量的旧描述与当前剥离向量实现不一致，T13 更新。
- [Neo4j 接入](neo4j接入.md)：保持可选投影，不使其参与当前正式写入原子事务。

**发生冲突时：用户本次明确决策和本文领域不变量优先；当前代码/测试提供现状证据；旧规划不得反向要求保留 SQLite、迁移旧数据或恢复已经修复的治理缺口。**
