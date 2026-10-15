# Native Applicability Filtering V1 Implementation Plan

**计划编号：** `NATIVE-APPLICABILITY-V1-PLAN`

**状态（2026-10-15）：** Task 8 工具与文档已实现；真实 PostgreSQL/Milvus/Neo4j 迁移及容量验收待专用环境执行，不能标记为已通过。

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单项目 5000+ 规则文档、10 平台规模下，把平台/业务/对象和有效时间下推到 PostgreSQL、Milvus 与 Neo4j，并让检索和问答按平台独立排名、避免候选 ID 过滤。

**Architecture:** `domain.applicability` 是统一范围语义的深模块，对外只暴露规范化、匹配和存储 adapter 所需的中立条件。PostgreSQL 保存历史版本的结构化范围，Milvus 保存当前版本的冗余范围并在 ANN/BM25 前过滤，Neo4j 保存原生范围属性。旧三数组请求自动升级为 `specific/unknown`，新上传界面明确选择“指定/全部/暂不确定”。

**Tech Stack:** Python 3.12、FastAPI/Pydantic、PostgreSQL 18、Milvus 3.0.2/pymilvus 3、Neo4j 5.26+/driver 6、原生 JavaScript、pytest、Node test runner。

**Execution constraint:** 当前工作区包含用户的既有改动；不 reset、不清理、不提交。pytest 串行运行，避免共享测试数据库互删。

---

### Task 1: 统一三态范围模型和中立谓词

**Files:**
- Modify: `knowledge_service/domain/applicability.py`
- Modify: `knowledge_service/models.py`
- Modify: `knowledge_service/domain/formal_facts.py`
- Test: `tests/service/test_applicability.py`

- [ ] **Step 1: 写失败测试**

覆盖旧数组到新模型的兼容、`specific/all/unknown` 校验、每维最多 64 值、同维 OR/跨维 AND、查询未指定维度不限制、显式查询不匹配 unknown、all 匹配显式查询、v1 指纹不变、all 使用 v2 指纹。增加兼容访问器 `dimension_values(scope, name)` / `dimension_mode(scope, name)`，让现有调用方在迁移期间不直接假设字段是列表。

增加 `ApplicabilityVocabulary` 和精确最长词匹配测试：词典由项目中已经规范化的平台、业务、对象值与平台固定别名组成；只命中完整中文词或英文 token，不使用 LLM。问题“直播规则”解析 `businesses=直播` 且平台未指定；“美团直播商家”解析三个维度。显式选择与问题精确命中取交集，冲突报错。

期望接口：

```python
scope = normalize_applicability(raw)
assert scope['platforms'] == {'mode': 'specific', 'values': ['美团']}
assert dimension_values(scope, 'platforms') == ['美团']
predicate = build_scope_predicate(scope, valid_at='2026-10-14', include_unknown=False)
assert predicate.matches(record)
```

- [ ] **Step 2: 验证 RED**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_applicability.py -q`

Expected: 新三态断言失败，旧测试继续运行。

- [ ] **Step 3: 实现最小模型**

在 `applicability.py` 增加 `normalize_dimension`、`dimension_values`、`dimension_mode`、`ApplicabilityVocabulary`、`ScopePredicate`、`build_scope_predicate`，保留 `matches_applicability` 和 `resolve_query_applicability` 兼容接口。`Applicability` 接受旧列表或 `{mode, values}`；序列化统一输出三态对象。`scope_identity` 对仅 specific/unknown 的输入继续按旧三数组 payload 计算，对 all 使用带 schema version 的 payload。

- [ ] **Step 4: 跑定向回归**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_applicability.py -q`

Expected: 单元契约 PASS；Search/QA 集成在 Task 5 完成消费方迁移后统一转绿。

### Task 2: PostgreSQL 历史版本原生范围过滤

**Files:**
- Create: `knowledge_service/repository/migrations/0013_native_applicability.sql`
- Modify: `knowledge_service/repository/core.py`
- Modify: `knowledge_service/repository/migrate.py` only if migration discovery requires it
- Modify: `tests/service/test_postgres_schema.py`
- Modify: `tests/service/test_repository.py`

- [ ] **Step 1: 写失败 schema/query 测试**

要求 `record_versions` 每个历史版本都有：三个 mode、三个 text[]、scope_identity、四个时间投影字段；包含 GIN/组合索引。写入范围修订后，`known_at` 修改前返回旧平台、修改后返回新平台。显式美团查询不能读取京东或 unknown；all 可以匹配。增加 `distinct_applicability_values(project_id)` 和 `records_by_version_ids(project_id, ids)` 测试，后者保持输入顺序、只读取少量命中版本。

- [ ] **Step 2: 验证 RED**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_postgres_schema.py tests/service/test_repository.py -q`

- [ ] **Step 3: 添加迁移和回填**

迁移从 `payload::jsonb #> '{metadata,applicability}'` 回填。旧非空数组为 specific，空或缺失为 unknown。时间使用 timestamptz 权威列或 epoch 毫秒投影；所有历史行都回填，不只当前行。

- [ ] **Step 4: 写入结构化列并下推 query**

`Repository._put` 在同一 INSERT 写入规范范围和时间。`Repository.query(..., applicability=None)` 将范围、业务有效期、kinds 下推 SQL；`known_at` 仍选择当时可见的历史版本。实现项目范围词典的 DISTINCT 查询和按 `version_id = ANY(...)` 的少量批量回填。JSON payload 保持不变以保护哈希和回放。

- [ ] **Step 5: 验证 GREEN**

串行运行 schema、Repository 范围过滤和历史版本集成测试；Search/QA 集成统一留到 Task 5 转绿。

### Task 3: 投影 outbox、一致性水位与恢复

**Files:**
- Create: `knowledge_service/repository/migrations/0016_projection_outbox.sql`
- Create: `knowledge_service/repository/projection_store.py`
- Create: `knowledge_service/services/projection_sync.py`
- Modify: `knowledge_service/repository/core.py`
- Modify: `knowledge_service/api/__init__.py`
- Modify: `knowledge_service/services/service.py`
- Test: `tests/service/test_projection_outbox.py`
- Test: `tests/service/test_projection_sync.py`

- [ ] **Step 1: 写失败一致性测试**

覆盖：同一项目并发写入通过 `SELECT ... FOR UPDATE` 得到提交顺序一致的 `projection_seq`；每次 record version 写入在同一事务产生 upsert/tombstone outbox；consumer 只能推进最高无缺口水位；失败重试幂等；范围修改、删除、supersede 未投影时项目标记 stale，检索不得读取旧投影。

覆盖重建恢复：记录快照水位 S，重建后重放 S+1，追平项目 W 才把新投影标为 ready；中断后从 artifact/cursor 继续；alias 切换前再追平；旧投影记录 `rollback_until=切换时间+7天`。

- [ ] **Step 2: 验证 RED**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_projection_outbox.py tests/service/test_projection_sync.py -q`

- [ ] **Step 3: 实现 repository interface**

迁移增加项目序列表、`projection_outbox(project_id,seq,operation,version_id,payload,created_at)` 和 `projection_cursors(project_id,target,schema_version,applied_seq,status,last_error,rollback_until)`。`Repository._put`、删除和 supersede 在原事务内排号并写 outbox。`ProjectionStore` 提供 `project_watermark`、`claim_contiguous_batch`、`ack_contiguous`、`mark_failed`、`projection_ready`；接口隐藏 SQL 锁和无缺口规则。

- [ ] **Step 4: 实现同步协调器**

`ProjectionSync` 顺序投递到 Milvus/Neo4j adapter，启动时恢复未完成任务。在线检索调用 `projection_ready(project_id,target)`；不相等立即走 PostgreSQL原生关键词/受控本地排名并返回 degradation reason，不使用 candidate ID。第一版允许同步 drain outbox，同时保留后台循环，不允许权威提交后直接绕过 outbox 写投影。

- [ ] **Step 5: 验证 GREEN**

串行运行 projection 与 repository 事务测试，包括故障注入和重启恢复。

### Task 4: Milvus V2 collection 原生过滤与少量回填

**Files:**
- Modify: `knowledge_service/integrations/milvus_store.py`
- Modify: `knowledge_service/services/retrieval.py`
- Modify: `knowledge_service/services/job_handlers.py`
- Modify: `knowledge_service/services/projection_sync.py`
- Modify: `tests/service/test_vector_storage.py`
- Modify: `tests/service/test_vector_dimension.py`
- Create or modify: `tests/service/test_milvus_applicability.py`

- [ ] **Step 1: 写失败 adapter 测试**

用 fake Milvus client 验证 schema 含 ARRAY/mode/time/scope_identity，upsert 写规范字段；单平台组 filter 必须为 `platform_mode == "specific"` + `ARRAY_CONTAINS_ANY`；通用组为 all；unknown 组独立；业务/对象跨维 AND；时间左闭右开；filter 不出现 `version_id in [...]`。正常原生请求不得先调用 `Repository.query/current_records` 加载项目候选，只把 predicate 交给 Milvus，收到 top-k `version_id` 后调用 `records_by_version_ids` 少量回填。

- [ ] **Step 2: 验证 RED**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_milvus_applicability.py -q`

- [ ] **Step 3: 实现 V2 schema 和表达式编译**

collection 名称/alias 带 schema version。ARRAY `max_capacity=64`、元素 `max_length=256`，mode/scope/time建标量索引。Dense、BM25、hybrid 的全部 AnnSearchRequest 使用同一原生 filter。删除在线 `candidate_ids` 分支；空范围由 predicate 直接产生恒假条件。

- [ ] **Step 4: 重建任务支持新 schema**

重建写入所有当前知识版本，包括未来生效或业务已失效但未 supersede 的版本。按 Task 3 契约记录快照水位、重放增量、校验并原子切 alias；状态制品记录 schema version、applied projection seq、记录签名、rollback_until 和重建结果，不切到半成品 collection。

- [ ] **Step 5: 验证 GREEN**

运行 Milvus、向量维度、重建任务相关测试。

### Task 5: 检索与问答的平台分组、原生读取和配额

**Files:**
- Modify: `knowledge_service/services/service.py`
- Modify: `knowledge_service/services/retrieval.py`
- Modify: `knowledge_service/services/answers.py`
- Modify: `knowledge_service/services/answer_evidence.py`
- Modify: `tests/service/test_applicability_integration.py`

- [ ] **Step 1: 写失败行为测试**

覆盖：明确美团只取 specific 美团并单列通用组；多平台逐平台独立排名；只问直播时使用 Repository 项目词典识别业务并枚举全部已知平台、每平台独立额度；unknown 只出现在宽泛查询的待确认组且不进入生成证据；all 不占平台额度；同一多平台记录保留在各组、扁平列表按 version_id 去重。

用 spy repository 证明当前且投影 ready 的请求不调用项目全量 `query/current_records`：检索层将 `ScopePredicate` 直接编译为 Milvus filter，随后只批量回填 top-k `version_id`。`known_at` 查询和 projection stale 查询明确走 PostgreSQL范围下推路径，并在响应中返回 backend/degraded reason。

- [ ] **Step 2: 验证 RED**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_applicability_integration.py -q`

- [ ] **Step 3: 实现分组计划**

新增纯函数生成 group plan。每次请求先用 `distinct_applicability_values` 构造 `ApplicabilityVocabulary` 并解析平台/业务/对象。默认 `k_per_platform=min(k,5)`、最大 20、总结果最大 100；通用组 5、unknown 组 3。10 平台请求使用有界并发 4，保持输出顺序稳定。单平台和多平台查询都单独请求通用组。

将 `service.search` 与 `question_context` 分成 native projection 和 authoritative fallback 两条深模块路径。native 路径只 hydrate top-k；fallback 路径使用 PostgreSQL 已下推范围的结果。本地 BFS 只属于 fallback，不能成为 native 请求全量读取的借口。

- [ ] **Step 4: 调整问答证据**

流式和非流式入口都把 scope/time/query_platform 传给 LLM。system prompt 要求按平台分别给结论，通用与平台特定规则分别陈述，unknown 不生成确定性答案。

- [ ] **Step 5: 验证 GREEN**

运行 applicability、retrieval、answer/provenance 回归。

### Task 6: Neo4j 原生范围投影与读取

**Files:**
- Modify: `knowledge_service/integrations/neo4j_store.py`
- Modify: `knowledge_service/services/answers.py`
- Modify: `knowledge_service/services/projection_sync.py`
- Modify: `tests/service/test_neo4j_store.py`
- Modify: `tests/service/test_applicability_integration.py`

- [ ] **Step 1: 写失败投影测试**

验证当前/版本节点、属性节点、事实关系带 scope/mode/array/time 字段；创建唯一 `KSScope` 和 `KSScopeValue`；索引语句覆盖 `(project_id,dimension,value)` 和 `(project_id,scope_identity)`。

读取测试调用真实 `Neo4jProjection.expand(...)` interface，而非只断言字符串：先通过 scope/value 索引得到 allowed scope ids，再定位 seeds，变长路径每跳要求同 scope 且逐节点/关系检查时间。覆盖过期关系、未来节点、unknown scope、跨 scope 边都不返回；projection stale/不可用时 `question_context` 走 PostgreSQL已过滤的本地 BFS，并标记降级。

- [ ] **Step 2: 验证 RED**

Run: `D:/anaconda/envs/llm_model/python.exe -m pytest tests/service/test_neo4j_store.py -q`

- [ ] **Step 3: 实现投影**

`record_properties` 输出原生范围字段并继续保留 metadata_json/payload_json。同步时批量 MERGE scope/value 节点，记录/版本/边关联 scope。校验摘要加入按 mode/平台计数，检测旧投影缺字段。

新增 `expand(project_id, seed_version_ids, predicate, hops)`：adapter 参数化编译 Cypher，返回少量节点/边 version id，再由 Repository 批量 hydrate。`answers.question_context` 仅在 Neo4j cursor ready 时调用；否则使用同 predicate 的 PostgreSQL fallback。禁止把 metadata_json 解析放进 Cypher 热路径。

- [ ] **Step 4: 验证 GREEN**

运行 Neo4j 测试和 projection digest 回归。

### Task 7: 上传界面三态选择和结果分组

**Files:**
- Modify: `knowledge_service/web/index.html`
- Modify: `knowledge_service/web/ingest-mode.js`
- Modify: `knowledge_service/web/workbench.js`
- Modify: `knowledge_service/web/workspace.js`
- Modify: `tests/service/applicability_ui.test.cjs`
- Modify: `tests/service/test_frontend_static_contract.py`

- [ ] **Step 1: 写失败 UI 契约**

平台/业务/对象分别有“指定范围、全部适用、暂不确定”；specific 才允许填写值；新上传默认 unknown，不把空值发成 all。结果展示平台组、通用规则、范围待确认；unknown 证据显示“未用于确定性回答”。

- [ ] **Step 2: 验证 RED**

Run: `node --test tests/service/*.test.cjs`

- [ ] **Step 3: 实现并验证**

统一 payload builder，批量上传复用同一范围，更新静态资源版本。运行 node syntax、Node tests 和静态契约 pytest。

### Task 8: 迁移、真实存储容量验证和文档收口

**Files:**
- Create: `scripts/rehearse_migration_0013.py`
- Create: `scripts/verify_native_applicability_capacity.py`
- Create: `tests/service/test_native_applicability_acceptance_scripts.py`
- Modify: `docs/applicability-retrieval.md`
- Modify: `docs/2026-10-01-Docker基础设施维护手册.md`
- Modify: `README.md`

- [ ] **Step 1: 迁移演练**

在真实临时 PostgreSQL 构造旧列表、空范围、历史范围修订，执行 0013/0014，验证回填、索引、outbox、水位和幂等。对真实 Milvus 3.0.2 执行新 collection 重建、中断恢复、追平增量和 alias 切换；对真实 Neo4j 执行 scope/value 索引、投影和受限遍历。fake client 仅用于单元测试，不能替代这一验收。

状态：演练脚本已实现并覆盖 0013 至当前最新迁移；真实 disposable PostgreSQL、Milvus 和 Neo4j 尚未在本机会话运行。

- [x] **Step 2: 容量脚本**

生成 5000 文档/10 平台/至少 10 万检索记录，在真实 PostgreSQL/Milvus/Neo4j 上运行跨存储条件矩阵，报告 EXPLAIN、索引命中、范围编译、单平台 hybrid、10平台分组和2跳图扩展 p50/p95；验证 Milvus filter 不含候选 ID。阈值使用规格中的 5ms/800ms/3s/1.5s，范围泄漏必须为 0；环境不可用时脚本明确 skip 并保留待执行说明，不以 fake 结果宣称容量通过。

实现说明：脚本读取专用验收项目中的真实数据并输出 JSON；dry-run 和缺环境均写 `skipped`，只有真实执行可以写 `passed`。实际容量结果仍待执行。

- [ ] **Step 3: 全量验证**

依次运行相关 pytest（禁止并行 pytest）、Node tests、`py_compile`、`git diff --check`。记录真实结果；达不到性能阈值时保留数据和报告，不声称通过。

状态：脚本纯单测、`py_compile` 和 diff check 属于本任务收口；真实重型验证保持未勾选。

- [x] **Step 4: 更新文档**

说明三态含义、空失效时间、未设平台的召回规则、collection 重建步骤、降级表现和容量基线。
