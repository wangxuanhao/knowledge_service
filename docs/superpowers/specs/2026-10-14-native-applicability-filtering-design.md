# 大规模规则库原生适用范围过滤设计

**设计编号：** `NATIVE-APPLICABILITY-V1`

**状态（2026-10-15）：** Tasks 1–7 的实现由对应任务验收；Task 8 的真实验收工具和运维文档已落盘，真实 PostgreSQL/Milvus/Neo4j 容量运行仍待专用环境执行。本状态不表示容量阈值已经通过。

## 背景与容量目标

同一项目保存至少 5000 份规则文档，覆盖约 10 个平台。每份文档会派生多个 chunk、实体、关系、属性和版本记录，在线检索规模按数万到数十万条记录规划，而不是按 5000 条向量规划。

系统必须保证：

- 指定平台、业务、对象和有效时间时，先过滤范围，再执行 BM25、向量排序和图扩展。
- 只问业务、没有指定平台时，可以检索所有平台，并按平台分别保留结果额度。
- 不同平台的同名实体、关系和证据不得因向量相似或图连接而混用。
- 在线请求不得传输数千个候选 ID，也不得因为候选超过上限而回退为无范围检索。
- 范围语义在 PostgreSQL、Milvus、Neo4j 和本地测试适配器中一致。

## 方案比较与决定

### 方案 A：应用层过滤后传候选 ID

这是当前兼容方案。PostgreSQL/Repository 先过滤记录，再把 `version_id in [...]` 交给 Milvus。它容易验证语义，但请求表达式随数据量增长，现有安全上限为 5000 个候选 ID，无法作为目标架构。

### 方案 B：只在 Milvus 保存范围

Milvus 可以高效完成向量前过滤，但 PostgreSQL 的历史视图和 Neo4j 的图扩展仍需重复实现或回读 Milvus。它会让 Milvus成为范围事实源，不符合现有双时态主存储的职责。

### 方案 C：统一范围模型，各存储原生过滤

采用此方案。PostgreSQL 保存权威范围和双时态版本；Milvus冗余当前活跃版本的范围字段以执行 ANN/BM25 前过滤；Neo4j冗余范围字段以执行遍历前过滤。一个纯范围模块生成中立条件树，再由各 adapter 编译为 SQL、Milvus filter 和 Cypher predicate。

## 统一范围模型

三个范围维度为 `platforms`、`businesses`、`audiences`。每个维度不能只用空数组表达，因为空数组无法区分“适用于全部”和“尚未标注”。目标模型为：

```json
{
  "platforms": {"mode": "specific", "values": ["美团"]},
  "businesses": {"mode": "specific", "values": ["直播"]},
  "audiences": {"mode": "all", "values": []}
}
```

`mode` 取值：

- `specific`：只适用于 `values` 中的值；`values` 必须非空。
- `all`：明确适用于该维度的所有值；`values` 必须为空。
- `unknown`：没有可靠范围信息；`values` 必须为空。

兼容旧接口 `{platforms: [], businesses: [], audiences: []}`：非空列表转为 `specific`，空列表转为 `unknown`。不得把历史空列表自动解释为 `all`。

每个维度最多 64 个值；单值规范化后最多 128 个 Unicode 字符且 UTF-8 不超过 256 字节，超限请求在写入前拒绝。目标运行版本为 Milvus 3.0.2 / pymilvus 3.x 和 Neo4j 5.26+ / Python driver 6.x。

规范化后的范围生成稳定 `scope_identity`。该指纹用于融合、事实键、邻接上下文和一致性校验，但不替代各维度字段的在线过滤。兼容身份规则如下：

- 只包含 `specific` 与 `unknown` 的范围继续用旧的三数组 JSON 计算 v1 指纹，因此现有非空范围的事实键不变。
- 三个维度均为 `unknown` 时继续返回空指纹，历史无范围事实键不变。
- 首次出现 `all` 时使用带 `schema_version: 2` 和 mode 的规范 JSON 计算 v2 指纹。
- 将旧范围人工修正为 `all` 或其他新范围是一次业务版本变更；PostgreSQL事务内写入新版本、更新关系引用并 supersede 旧版本，outbox 再让两个投影删除旧 `version_id`、写入新版本。不得在迁移中静默重键既有 `specific/unknown` 事实。

## 查询范围与匹配语义

查询解析生成 `QueryScope`，保留每个条件的来源：用户显式选择、问题精确别名、本体词典或未指定。只有显式选择和精确词典命中可以产生硬过滤；LLM 可以提出候选，不得直接生成硬过滤条件。

同一维度的查询值使用 OR，不同已指定维度使用 AND：

```text
(platform = 美团 OR platform = 京东)
AND business = 直播
AND audience = 商家
```

记录维度匹配规则：

- 查询未指定该维度：不限制。
- 记录为 `all`：匹配任意该维度查询。
- 记录为 `specific`：记录值与查询值有交集时匹配。
- 记录为 `unknown`：不能满足该维度的显式查询。

查询“直播规则”只生成业务条件，不生成平台条件，因此检索所有平台的直播规则。结果根据实际平台分组并为每个平台保留基础额度；`all` 平台规则进入“通用规则”组，`unknown` 进入“范围未标注”组并显式提示。查询“美团直播规则”同时生成平台和业务条件。

## 深模块与接口

新增深模块 `domain/applicability.py` 的目标接口：

```python
scope = normalize_applicability(raw)
query_scope = resolve_query_applicability(query, explicit, vocabulary)
predicate = build_scope_predicate(query_scope, valid_at=...)
matched = predicate.matches(record_scope)
milvus_filter = predicate.compile_milvus(project_id, kinds)
sql_filter = predicate.compile_sql()
cypher_filter = predicate.compile_cypher(alias)
```

调用方只理解规范范围和 `ScopePredicate`，不手写 OR/AND。adapter 负责目标存储的转义、参数绑定和表达式限制。编译器输出参数和值分离；不得通过字符串插值接收用户原文。

## PostgreSQL

PostgreSQL继续是权威事实源，保留完整 metadata 和双时态记录。当前记录表和不可变版本表都增加以下字段并回填；历史 `known_at` 必须从当时的版本行读取范围和有效期，禁止用当前记录的范围过滤历史版本：

```text
platform_mode, business_mode, audience_mode
platforms[], businesses[], audiences[]
scope_identity
valid_from, valid_until
```

数组字段建立 GIN 索引，mode、project/current 状态和时间建立组合或部分索引。Repository 查询直接执行范围谓词，不在读取整个项目后使用 Python 全量过滤。历史 `known_at` 查询仍只依赖 PostgreSQL。

业务时间统一转换为 UTC epoch milliseconds，时间区间为左闭右开。Milvus/投影字段使用：

```text
valid_from_known: BOOL
valid_from_epoch_ms: INT64   # unknown 时固定为 0
valid_until_known: BOOL
valid_until_epoch_ms: INT64  # 开放结束时固定为 0
```

在查询时刻 `t`：

```text
(not valid_from_known or valid_from_epoch_ms <= t)
and (not valid_until_known or t < valid_until_epoch_ms)
```

`include_unknown=false` 时第一项改为 `valid_from_known and valid_from_epoch_ms <= t`；未设置失效时间始终表示开放结束，不作为未知记录排除。数据库中的“当前版本”是尚未被新知识版本 supersede 的版本，包含未来生效和已经业务失效的规则；是否在业务时刻有效只由上述谓词决定。

## Milvus

Milvus collection 增加：

```text
platform_mode, business_mode, audience_mode: VARCHAR
platforms, businesses, audiences: ARRAY<VARCHAR> max_capacity=64, element max_length=256
scope_identity: VARCHAR
valid_from_known, valid_until_known: BOOL
valid_from_epoch_ms, valid_until_epoch_ms: INT64
```

范围数组和 mode 字段建立标量索引。Dense、BM25 和 hybrid 的每个 `AnnSearchRequest` 都带相同 filter。示意表达式：

```text
project_id == "..."
and platform_mode == "specific"
and ARRAY_CONTAINS_ANY(platforms, ["美团"])
and (business_mode == "all" or ARRAY_CONTAINS_ANY(businesses, ["直播"]))
and (valid_from_known == false or valid_from_epoch_ms <= 1791907200000)
and (valid_until_known == false or 1791907200000 < valid_until_epoch_ms)
```

这是“美团平台组”的表达式，所以明确要求 `platform_mode == "specific"`。通用组单独使用 `platform_mode == "all"`，范围未知组单独使用 `platform_mode == "unknown"`；三者不得共用一个会让通用记录占用平台额度的 filter。业务和对象没有单独分组时仍按正常的 `all OR overlap` 语义过滤。

历史视图、迁移未完成或 Milvus schema 不支持目标字段时，使用 PostgreSQL过滤后的受控本地排名兼容路径。不得继续使用超过阈值的 `version_id in [...]`；空候选必须返回空结果。

Milvus collection schema 变化通过新 collection + rebuild + alias 切换上线，避免原地修改不可变字段。重建按项目分批，可重复执行并记录 schema version。所有当前知识版本都进入投影，包括未来生效和业务上已经失效的记录；只排除已被新知识版本 supersede 的版本。

## Neo4j

Neo4j 的当前节点、版本节点、事实关系和属性节点都保存原生范围字段及 `scope_identity`，同时保留 `metadata_json` 用于完整回放。为了避免列表成员谓词退化为项目扫描，每个规范范围还投影为一个 `KSScope` 节点，并连接可索引的 `KSScopeValue`：

```text
(:KSScope {project_id, scope_identity, platform_mode, business_mode, audience_mode})
-[:HAS_VALUE]->
(:KSScopeValue {project_id, dimension, value})
```

`KSScopeValue(project_id, dimension, value)` 和 `KSScope(project_id, scope_identity)` 建组合索引。查询先从 scope/value 索引求出允许的 `scope_identity`，再通过 `KSRecord(project_id, scope_identity)` 组合索引定位起点。

证据图中的关系和两个端点必须拥有同一个 `scope_identity`；它们各自还必须匹配查询范围和有效时间。通用规则在 `all` scope 的独立子图内遍历，平台特定规则在其 scope 子图内遍历，两类证据只在回答聚合层组合，不能用通用节点作为桥跨到另一个平台。多平台 specific 记录保持一个完整 scope，不拆成能互相连通的平台节点。

变长路径使用每跳约束：

```cypher
MATCH (seed:KSRecord {project_id: $project_id})
WHERE seed.scope_identity IN $allowed_scope_ids
MATCH p=(seed)-[:KS_FACT*1..3]->(target)
WHERE all(n IN nodes(p) WHERE n.project_id = $project_id
          AND n.scope_identity = seed.scope_identity
          AND ($include_unknown OR n.valid_from_known)
          AND (NOT n.valid_from_known OR n.valid_from_epoch_ms <= $at_ms)
          AND (NOT n.valid_until_known OR $at_ms < n.valid_until_epoch_ms))
  AND all(r IN relationships(p) WHERE r.project_id = $project_id
          AND r.scope_identity = seed.scope_identity
          AND ($include_unknown OR r.valid_from_known)
          AND (NOT r.valid_from_known OR r.valid_from_epoch_ms <= $at_ms)
          AND (NOT r.valid_until_known OR $at_ms < r.valid_until_epoch_ms))
RETURN p
```

`$allowed_scope_ids` 由索引化的 `KSScope/KSScopeValue` 查询产生，只负责范围匹配；有效时间必须对路径中的每个节点和每条关系分别检查，因为相同 scope 的事实可以拥有不同有效期。显式范围查询不包含 `unknown` scope。

图投影使用批量同步。知识图谱关系用于解释和扩展，不作为向量范围过滤的唯一事实源。

## 摄取与同步

上传范围是权威值，依次继承到 document、chunk、entity、relation、attribute、review candidate 和 assertion。抽取器不得覆盖上传范围。

一次逻辑写入采用 outbox/rebuild 可恢复语义。每个项目在 PostgreSQL 中维护 `projection_seq`；知识写事务先对项目计数行执行 `SELECT ... FOR UPDATE`，递增序号，再同时写权威版本和 outbox，因此同一项目的序号与提交顺序一致。Milvus和Neo4j分别保存 `schema_version`、最高无缺口 `applied_projection_seq` 和最近错误；消费者必须按项目顺序确认，任何缺口都会阻止水位前进。范围修改、supersede 和删除都产生显式 tombstone 事件，adapter 先按旧 `version_id` 删除，再幂等 upsert 新版本。投影失败记录状态并重试，不回滚已经持久化的文档。

重建流程固定为：

1. 记录快照水位 `S`，从 PostgreSQL 在同一快照读取所有当前知识版本并重建新投影。
2. 从 outbox 重放 `S+1` 之后的增量，直到新投影的无缺口 `applied_projection_seq >=` PostgreSQL 项目水位 `W`。
3. 校验数量、按平台/业务/mode 汇总、抽样内容和新旧匹配矩阵。
4. 短暂阻止 alias 切换动作本身的并发，重新读取水位并追平，然后原子切换 alias；写入仍继续进入 outbox。
5. 消费器继续处理 alias 指向的新投影。旧 collection 和旧图投影继续消费增量 7 天，作为回滚窗口，之后才删除。

在线检索先读取项目的权威 `projection_seq`。只有投影 schema version 正确且 `applied_projection_seq == projection_seq` 时才使用原生投影；只要有一个未应用事件，整个受影响项目立即进入 PostgreSQL 原生关键词检索或受控本地排名，直到请求所需的投影追平。这样范围修改、删除或 supersede 提交后，不存在旧投影继续对外返回的窗口。默认 100 个事件、30 秒只用于告警和运维目标，不是允许带陈旧范围提供结果的阈值。在线兼容路径不得向 Milvus 发送候选 ID 列表，也不得扩大范围；语义通道不可用时要在响应中明确标记降级。

## 全平台检索与问答

- 明确单平台：`platform_mode=specific AND overlap(平台)` 独立排名，同时用 `platform_mode=all` 独立召回通用组；`unknown` 不进入证据。
- 明确多平台：每个平台用 `specific AND overlap` 独立 filter、排名和额度，保留空组；通用组只召回一次，未知组默认排除。
- 未指定平台但命中业务：首期必须从项目范围目录枚举活跃平台并逐平台独立排名，不允许先做一次宽 top-k 再分组。默认每个平台 `k_per_platform=min(request.k, 5)`、最大 20，并发上限 4；10个平台默认最多返回 50 条平台结果。通用规则组独立取 5 条，范围未知组独立取 3 条、只提示且默认不进入生成证据。调用方可下调总额度，上调时总结果硬上限 100。
- 一条 `specific` 记录同时适用多个平台时保留在每个对应平台组中，并带相同 `version_id`；组内排名独立。扁平兼容列表按 `version_id` 去重，但分组响应不去重。`all` 记录只进入“通用规则”组，不复制到十个平台组。
- 问答证据携带平台、业务、对象、时间和范围来源。生成提示要求按平台分别给结论，冲突规则不得合并。
- 通用规则与特定平台规则始终分别展示和引用；通用规则不能占用平台组额度，也不能覆盖平台特定例外。只有未指定平台的探索查询才展示范围未知组，明确单/多平台查询不召回未知组。

## 迁移与兼容

1. 增加范围 schema version 和规范化读写，不改变旧请求格式。
2. 增加 PostgreSQL结构化字段并从 metadata 回填；空范围回填为 `unknown`。
3. 创建新版 Milvus collection，按当前活跃版本重建，校验数量与抽样范围后切 alias。
4. Neo4j 全量重投影并校验节点、关系、属性数量和范围摘要。
5. 开启双读校验：使用理解 v1/v2 mode、时间和 `all/specific/unknown` 的独立参考匹配器，与三种原生 adapter 的结果对比；不得使用未经升级的旧 Python 匹配器作为真值。
6. 差异清零后切换原生过滤为默认；旧投影只保留短期回滚开关，之后删除。

迁移不会猜测历史文档的平台、业务或对象。`unknown` 资料通过独立治理任务补录。

## 性能与验收

基准数据至少包含 5000 份文档、10 个平台、10 万条当前向量记录以及历史版本和派生图。固定基准环境记录 CPU、内存、Milvus/Neo4j/PostgreSQL版本；验收要求：

- 单平台和平台+业务过滤在向量排序前发生，Milvus请求不含大规模候选 ID。
- 查询结果不因某平台数据量较大而挤掉其他平台的全部额度。
- 指定平台查询不返回 `unknown` 或其他平台证据；`all` 范围按定义匹配。
- PostgreSQL、Milvus、Neo4j和内存 adapter 对同一条件矩阵结果一致。
- 图遍历不跨 scope，实体融合和事实键继续按完整规范范围隔离。
- collection rebuild 可中断恢复，alias 切换前后无无范围窗口。
- 记录候选数、过滤后数量、各平台命中数、Milvus延迟、图扩展延迟和降级原因，便于容量回归。
- 范围条件编译 p95 不超过 5ms；单平台 hybrid 检索 p95 不超过 800ms；10平台受限并发检索 p95 不超过 3s；2跳图扩展 p95 不超过 1.5s。连续 1 万次请求的非计划降级率低于 0.1%，范围泄漏为 0。
- PostgreSQL执行计划不得出现跨项目全表扫描；Milvus请求不得携带候选 ID 列表；Neo4j起点必须命中 scope/value 与 record 组合索引。
- 修改某条规则的 scope 后，`known_at` 在修改前返回旧范围、修改后返回新范围；当前查询只返回未 supersede 版本。
- 通用、特定、多平台和未知 scope 的节点/边组合均有遍历测试，任何路径的节点或关系 scope 不一致时必须被拒绝。

首期不引入 Microsoft GraphRAG，不建设通用规则执行器，也不使用 LLM 自动改写历史范围。原文检索仍是主通道，图谱负责关系扩展、解释和治理。

## Task 8 验收制品

- `scripts/verify_native_applicability_capacity.py`：设计编号 `NATIVE-APPLICABILITY-V1-A8-CAPACITY`。默认 dry-run；只有真实三库、目标规模、全部正确性检查和四项 p95 阈值通过时才能生成 `status: passed` 的 JSON。
- `scripts/rehearse_migration_0013.py`：设计编号 `NATIVE-APPLICABILITY-V1-A8-MIGRATION`。只接受名称以 `kg_acceptance_` 开头的 disposable database，覆盖 0013–0017 以及后来新增的迁移，并验证第二次执行不再应用任何迁移。
- `tests/service/test_native_applicability_acceptance_scripts.py`：只验证 CLI、skip 语义和报告 schema，不替代真实存储验收。

本地未配置真实验收环境时，Task 8 状态保持“工具完成、真实验收待执行”。
