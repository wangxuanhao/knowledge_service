# 知识服务分层、Milvus、本体能力与问答证据闭环设计

## 1. 背景与结论

当前系统的领域能力已经覆盖本体治理、双时态知识、混合检索、证据问答和统一溯源，但实现边界没有完全遵守仓库声明的 `api → services → repository` 单向依赖：开放发现路由直接开启仓储事务并调用私有 `_put`，repository 与 integrations 还会反向导入 services。本体发布后的 Milvus 同步也没有形成可靠状态机；问答虽然保存了原文溯源，但主页面默认折叠，用户无法直观看到答案依据。

本设计一次性收口四条关键链路：

1. 建立可执行的分层规则与模块接口，而不是只调整目录名。
2. 让 PostgreSQL 权威写入与 Milvus 派生索引具备可观测、可重试的一致性状态。
3. 把登录身份绑定到审核与发布审计，禁止客户端伪造 actor。
4. 在问答主页面直接展示来源文档和原文摘录，并继续保留完整溯源抽屉。
5. 使用 Semantica 0.7.0 的本体质量门补充现有 SHACL/结构校验，并建立可审计的 Ontology RAG 接口。

本次不建设分布式任务平台，不替换 PostgreSQL、Milvus 或现有本体版本模型，也不让 Semantica 成为第二真相源。

## 2. 分层目标

### 2.1 允许的依赖方向

```text
api ───────────────→ services ───────────────→ repository
 │                       │                         │
 │                       ├──────────────→ integrations
 │                       └──────────────→ domain
 ├──────────────────────────────────────→ domain
 repository ────────────────────────────→ domain
 integrations ─────────────────────────→ domain
 core / utils：无业务层反向依赖
```

含义：

- `api` 路由模块只做 HTTP 参数解析、身份提取、调用 service、异常到 HTTP 的映射；不得导入 repository 或 integrations。
- `services` 拥有业务流程、事务用例、状态转换、投影同步编排和读模型组装。
- `repository` 只持久化和执行窄查询，不知道 HTTP、页面、service 类或外部模型。
- `integrations` 是 Semantica、Milvus、Neo4j、embedding、文档解析等适配器，不导入 services。
- `domain` 保存无 I/O 的领域值、纯校验、命名规则和协议类型，可被 services/repository/integrations 共同使用。

增加 `knowledge_service/bootstrap.py` 作为唯一 composition root。它可以同时导入 services、repository 和 integrations，负责构造 adapter、service 与应用容器；`api/__init__.py` 只接收该容器并挂载路由。除 `bootstrap.py` 外不允许横跨多个实现层做装配。

事务由 service 用例拥有，但通过 repository 的公开 `unit_of_work()` seam 执行。UoW 只暴露完成该用例所需的公开 stores/methods，并统一 commit/rollback；service 不接触 `_transaction`、`_db` 或 `_put`。多写流程不能靠多个独立 repository 调用碰运气地保持原子性。repository 接收 service 已构造的不可变 domain command/plan，在事务内复核 CAS、idempotency key 与 command fingerprint；它不重新导入 service 业务类。纯本体规则下沉到 domain 后，repository 可以调用 domain 校验，但不能调用 service。

增加自动化架构测试：扫描 Python import，禁止 repository/integrations 导入 services，禁止 services 导入 api，禁止所有 `api/*.py`（composition root 除外）导入 repository/integrations，禁止 api 调用任何 repository 属性；同时全库禁止 API/service 调用 `_transaction`、`_put`、`_db`。现有 repository→services 的 `Ontology` 运行时导入由“service 生成 domain publish plan + repository 原子复核”替换；`valid_application_iri` 与 `canonical_name` 下沉 domain 后分别替换 repository/integrations 的反向导入。

### 2.2 新模块

| 模块 | 责任 | 对外接口 |
|---|---|---|
| `domain/ontology_names.py` | IRI 与规范名称纯规则 | `valid_application_iri`、`canonical_name` |
| `services/ontology_discovery_workflow.py` | 开放发现 run 的创建、完成、并发检查和物化 | `create_run()`、`finalize_run()` |
| `repository/projection_jobs_store.py` | 持久化投影 outbox、CAS 领取与租约 | `enqueue()`、`claim()`、`heartbeat()`、`complete()`、`fail()` |
| `services/projection_sync.py` | PostgreSQL→Milvus 投影执行、重试和恢复 | `run_pending()`、`retry()`、`status()` |
| `services/runtime_capabilities.py` | 运行能力探测与健康读模型 | `snapshot()` |
| `services/answer_evidence.py` | 组装问答主页面需要的来源摘要 | `summaries()` |
| `integrations/semantica_ontology.py` | Semantica 质量门适配与稳定 DTO | `quality_report()` |
| `services/ontology_slice.py` | 为 chunk 选择相关本体术语 | `select_terms()` |

`api/ontology_discovery.py` 中的纯转换函数可以留在 services 或 domain；所有事务、运行状态和写入必须迁出路由。`Repository._put` 继续作为 repository 内部原语，但不再被 API 或外部 service 直接调用；service 使用公开、窄而原子的仓储方法。

## 3. Milvus 与投影一致性

### 3.1 运行配置

`.env` 是默认运行配置。`--demo` 仍允许用于隔离演示，但健康接口必须明确报告：

```json
{
  "status": "ok|degraded",
  "embedding": {
    "backend": "local|openai|demo",
    "identity": "...",
    "semantic": true,
    "dimension": 1024,
    "ready": true,
    "error": null
  },
  "vector_index": {
    "backend": "milvus|disabled",
    "configured": true,
    "connected": true,
    "collection_ready": true,
    "dimension": 1024,
    "error": null
  }
}
```

`status=ok` 表示所有配置为必需的能力可用；Milvus 显式配置但连接失败、local/openai encoder 初始化失败或维度不匹配时为 `degraded`。健康输出不得包含密钥、完整 DSN 或远端响应体。

启动脚本不得无提示清空 `KG_VECTOR_BACKEND`。`--demo` 启动时日志和健康接口都必须清楚显示“非语义演示模式”。

### 3.2 可靠同步

新增窄表 `projection_jobs` 作为通用持久 outbox；不再把普通摄取、发布和 rebuild 塞进语义不同的 `ontology_sync_job` artifact。旧 artifact 在启动迁移/兼容读取时转换为新 job，保留原 id 和状态，不删除历史。状态固定为：

```text
pending → running → completed
             ├──→ retry_wait(next_attempt_at) → running（自动重试）
             ├──→ failed → pending（人工重试）
             └──→ superseded（代际或顺序栅栏淘汰）
```

不变量：

1. 本体发布事务只写权威数据和 `pending` 收据，不执行 embedding/Milvus 网络 I/O。
2. worker 在事务提交后领取收据，写 `running`、attempt、started_at 和租约时间。
3. 成功必须记录 encoder identity、dimension、indexed count、completed_at。
4. Milvus 未配置或不可用不能标记 completed；可重试时必须是 retry_wait，耗尽后是 failed，并保存脱敏错误和 remediation。
5. 启动时扫描遗留 pending、到期 retry_wait，以及租约过期的 running；幂等重放。
6. 同一个 ontology/project/record version 的 upsert 可重复执行，不产生重复权威数据。
7. 提供项目级同步状态和重试接口；UI 的“项目与运行”显示 pending/retry_wait/failed/superseded 及被失败头任务阻塞的状态，而不是静默成功。

普通知识摄取同样通过统一的 projection sync 接口更新 Milvus，避免 ontology publish、ingest、rebuild 各自维护不同错误语义。全量 rebuild 仍是显式运维动作。

`projection_jobs` 至少包含：`id`、`dedupe_key UNIQUE`、`project_id`、`generation`、`project_sequence`、`operation`（upsert/delete/replace_project）、`payload`、`status`、`attempt`、`max_attempts`、`manual_retry_count`、`next_attempt_at`、`lease_owner`、`lease_token`、`leased_until`、`encoder_identity`、`dimension`、`indexed_count`、`error`、`created_at/started_at/completed_at/updated_at`。dedupe key 由 operation + project + 权威 record version 集合或 rebuild generation 的规范 JSON SHA-256 生成。另设 `projection_project_state(project_id, current_generation, active_generation, next_sequence, updated_at)` 作为 PostgreSQL 权威的项目级投影游标；generation 和 sequence 都只能在 enqueue 权威事务内由该行原子分配。“最小未解决任务”直接由 jobs 状态与 sequence 判定，不维护第二套 applied-sequence 游标。rebuild 入队时只推进 current_generation，active_generation 在 Milvus staging 数据校验成功后的完成事务才切换，因此重建期间查询继续读取旧的完整代际。

worker 由 FastAPI lifespan 启动、关闭，是同一应用进程里的后台线程，不依赖 HTTP 请求存活；每 2 秒轮询或被 enqueue 事件唤醒。`claim()` 使用单条 PostgreSQL 事务和 `FOR UPDATE SKIP LOCKED`/CAS，只考察某项目当前 generation 中 project_sequence 最小的未解决任务（completed/superseded 才算已解决）；该头任务为到期 `pending`、`retry_wait` 或租约过期 `running` 时才可领取并设为 running，failed 头任务会阻塞后续 sequence，直至人工 retry 或明确 supersede。claim 生成随机 lease token。默认租约 5 分钟，执行中每 30 秒 heartbeat 续租；complete/fail 必须同时匹配 job id + lease token，过期 worker 无权提交。多进程实例可以并发处理不同项目，但同一项目严格按 project_sequence 执行。

worker 在调用 Milvus 前获取项目级 PostgreSQL session advisory lock，并持有到 Milvus 副作用和最终状态事务都完成。拿锁后先校验 lease token 仍匹配且 leased_until 尚未过期，再次读取 `projection_project_state`：job generation 小于 current_generation 时不访问 Milvus，终结为 `superseded`；sequence 不是当前最小未解决任务时释放锁并回到可领取状态。等待锁导致租约失效的 worker 必须放弃，不能访问 Milvus。这样已经开始的旧 generation 操作会先完成，新 generation 的 rebuild 随后整体替换；尚未开始的旧 generation 操作会被淘汰，不能在 rebuild 后污染集合。该锁是 correctness 要求，不只是吞吐优化。

执行异常且 `attempt < max_attempts` 时转为 `retry_wait` 并设置有限指数退避的 `next_attempt_at`；达到上限才进入终态 `failed`。显式 retry 只允许终态 failed，执行时清空 error/lease、把 attempt 重置为 0、`manual_retry_count += 1`，max_attempts 保持不变并回到 pending。显式 supersede 只允许淘汰旧 generation 的未开始任务；当前 generation 的失败 rebuild 不能跳过，只能 retry，或显式创建更高 generation 的新 rebuild 整体替代。关闭服务只停止领取新任务并等待当前任务到可中断点，不无限阻塞。

发布、摄取和显式 rebuild 都在其 PostgreSQL 权威写事务中 enqueue 对应 job，因此不存在“数据已提交但 outbox 没写”的窗口。普通 upsert/delete 读取并捕获当前 generation；请求 `replace_project` 时，在 enqueue 事务中把 current_generation 加一，以该事务可见的全部权威 record version id 形成不可变 rebuild snapshot，并把 rebuild 分配为新 generation 的第一条 sequence。随后进入的新增量任务捕获新 generation 且 sequence 更大，所以必须等待 rebuild 完成。旧 generation 的 pending/retry_wait 任务可在同一事务标记 superseded；旧 running 任务由上述 advisory-lock 二次检查与串行 cutover 收口。Milvus 中每条投影同时保存 project_id、generation、record_version_id；upsert/delete 只作用于 job generation，`replace_project` 先写 generation 隔离的 staging collection/partition，校验计数与 encoder 后在完成事务中切换 PostgreSQL `active_generation` 指针，再异步清理旧 generation。查询只读取 `active_generation`；因此即使进程在 Milvus 成功后、数据库 complete 前崩溃，幂等重放也不会把旧版本重新暴露。

## 4. 权威审计身份

所有 HTTP 写请求的 actor 来自 `request.state.user`，以稳定 user id 为主键，同时冻结 username/display_name 快照。客户端请求体不再接受可作为权威审计字段的 `actor`。canonical DTO 定义在 `domain/audit.py`：

```json
{"version":1,"kind":"user|system|legacy","id":"稳定用户或系统 id","username":"登录名或 null","display_name":"当时显示名"}
```

`kind=user/system` 必须有 id；legacy 的 id/username 可空。service/repository 的新写接口只接受 `ActorRef`，拒绝裸字符串。

迁移规则：

- API 请求模型删除 actor；路由调用 service 时传入服务端 actor DTO。
- service 仍允许测试、离线迁移器通过显式 actor DTO 调用，不能接受任意裸字符串。
- 自动任务使用结构化 system actor，例如 `{kind:'system', id:'projection-sync'}`。
- 现有 TEXT actor 列保持 schema，不做高风险全表改型。新值使用 `actor:v1:<base64url(canonical-json)>` 编码；decoder 只认该前缀，其他字符串一律解析成 legacy ActorRef，因而旧记录保持可读且无需回写。带 `actor:v1:` 前缀但 base64/JSON/schema 损坏的值也不能中断列表读取：解析为稳定的 `kind=legacy` 损坏读模型，显示值为原始值的脱敏摘要，并附 `decode_error=malformed_actor_v1`；新写路径绝不产生该形态。
- API/读模型保持兼容字段 `actor`（显示字符串），并新增 `actor_ref` 对象；界面展示 `actor_ref.display_name || actor_ref.username || actor`。快照导入导出保留原 storage value，不丢失结构化身份。
- 前端删除 actor 输入/默认值；展示当前登录身份作为“将以谁的名义执行”。

必须在一个发布中原子覆盖草案创建、审核决定、关闭、批量批准、发布、消歧决定、重分类和治理操作；不允许一部分入口仍接受客户端 actor。架构测试列举所有包含 actor 的 API 请求模型并要求为零（登录/用户管理内部身份对象除外）。

## 5. 问答检索与原文展示

### 5.1 后端证据

问答继续同时检索 entity 与 chunk，并沿图关系扩展。进入 LLM prompt 的每条正式证据必须带不可变 `version_id`，并生成稳定引用 `E1...En`。

主页面证据摘要新增；一个证据可以对应多个原文来源，因此使用 `sources` 数组而不是单一 source：

```json
{
  "citation": "E1",
  "kind": "entity|relation|chunk",
  "record_version_id": "...",
  "retrieval": {"channel": "vector|keyword|graph", "ranker_version": "..."},
  "evidence_text": "进入回答模型的证据文本",
  "sources": [{
    "document_id": "...",
    "document_version_id": "...",
    "title": "员工制度.md",
    "quote": "张三是一名员工……",
    "start_char": 120,
    "end_char": 168,
    "complete": true,
    "reason": null
  }],
  "source_status": {"complete": true, "reason": null},
  "provenance_ref": "answer:...#E1"
}
```

来源解析只能遍历本次答案已经冻结的 `offered` edge branch，不能重新按 record id 查询当前断言/当前文档，也不能回退到文档当前版本。实体/关系沿该 branch 的 record-version→assertion→chunk-version→source-occurrence/document-version 找原文；直接 chunk 使用同一 branch 的冻结版本。

多来源按 provenance edge ordinal 排序，再以 document version id、start_char、end_char 作稳定次序；相同 document version + span 去重。页面默认展示第一条完整 source，同时标明“另有 N 个来源”，展开后全部可见。手工知识返回 `sources=[]` 和 evidence 级 `source_status={complete:false,reason:'manual_record'}`。缺失/越界/不唯一 span 必须返回 `complete=false` 和稳定 reason，不得用前 1000 字、当前文档搜索或任意匹配合成摘录。edge-v1 新答案禁止调用 legacy `_legacy_source_span`；旧答案可以继续在完整溯源抽屉走明确标记的 legacy 展示，但不能被升级成“完整来源”。

### 5.2 页面

答案正文下默认显示“答案依据”摘要，不需要先点“查看 N 条依据”：

- 默认展开前 3 条。
- 每条显示 `[E1]`、文档标题、最多 240 字原文摘录、检索通道/版本。
- 点击引用编号打开现有完整溯源抽屉。
- 超过 3 条时显示“展开其余 N 条”。
- 结构待定知识继续单独显示为 P 编号，明确“不作为正式证据”。
- 页面显示当前实际检索模式、Milvus/关键词通道与降级原因；不得只显示“混合检索”文案。

## 6. 本体能力与 Semantica 0.7.0

### 6.1 发布前质量报告

现有校验继续作为硬门禁：RDF/OWL 结构不变量、SHACL、domain/range、DAG、影响分析和人工审核。新增的 Semantica `ontology_quality_check` 是补充报告，输出稳定 DTO：

- passed
- metrics
- issue code / severity / message / affected term
- thresholds
- competency-question results（有配置时）

默认 error 阻断发布，warning 进入现有 acknowledged warning 流程。Semantica 调用失败本身不得把质量报告伪装成通过；应产生 `quality_engine_unavailable` 阻断项。报告和 Semantica 版本写入 validation fingerprint，避免校验后升级依赖却沿用旧审核。

### 6.2 Ontology RAG

受控抽取不再把整份本体 summary 无差别塞入每个 chunk。`OntologySliceSelector` 先根据 chunk 文本从当前已发布本体选择相关：

- 类与其祖先/子类邻域
- 相关对象属性及 domain/range
- 相关数据属性及 datatype/约束
- 明确配置的保底根类

选择结果包含 term IRI、命中表面词、选择原因、ontology id 和 selector version，写入 ingest run。抽取 prompt 只接收该 slice；如果 selector 失败，受控模式不得静默改为开放抽取，可显式回退到完整本体并记录 degraded 原因。

首版使用本项目确定性词汇索引与 embedding 检索，不引入 Semantica 存储。接口允许后续替换选择算法。

### 6.3 暂不纳入本轮写路径

- Semantica provenance：现有 PostgreSQL provenance 是权威链；只评估未来 PROV-O 导出。
- Semantica conflicts：先形成只读离线扫描设计，不在本轮自动改知识。
- Semantica reasoning：本体公理覆盖不足前不生成推导事实。
- Semantica pipeline/storage/change_management：不替换现有生命周期。

## 7. 操作手册

`docs/知识图谱操作手册.html` 是生成物，权威源为 `scripts/build_knowledge_service_manual.py` 与 `docs/assets/manual-v110/`。界面改动后必须：

1. 更新生成器中的问答章节，展示默认可见的原文依据、完整溯源入口和检索状态。
2. 更新项目运行章节，解释 Milvus configured/connected/indexed/degraded 与重试。
3. 更新本体发布章节，解释 Semantica 质量报告、warning 确认和 Ontology RAG 所用术语记录。
4. 使用隔离数据库和真实页面重新截图；禁止在业务库制造手册数据。
5. 重新生成自包含 HTML，并通过文档契约、链接、图片尺寸与浏览器渲染检查。

## 8. 错误处理与兼容性

- Milvus 失败不回滚已提交的 PostgreSQL 权威数据，但同步状态必须 failed/degraded 且可重试。
- 本体质量引擎不可用时阻断新发布，不影响读取既有发布版本。
- Ontology RAG 失败时保留可解释回退，不切换成开放发现。
- provenance 缺失时答案仍可展示证据文本，但必须标记“原文链不完整”。
- 旧前端保存的聊天记录没有 source 时继续显示旧版提示。
- 现有公开 HTTP 路径尽量保持；删除 actor 字段属于有意的请求契约变更，旧客户端收到 422 后必须升级。

## 9. 测试与验收

### 9.1 自动化

- 架构 import 规则与 API 不得访问任何 repository 属性的测试。
- actor 伪造回归：请求体 actor 被拒绝，审计记录只能等于登录用户。
- projection outbox 状态、失败、重试、启动恢复、幂等和无 Milvus 假完成测试；显式覆盖同项目多 worker、等待 advisory lock 超过租约、旧任务与 rebuild 竞争、Milvus 成功后数据库提交前崩溃、retry_wait 耗尽及人工重试。
- health 对 local/demo、Milvus configured/unavailable、维度不一致的矩阵测试。
- 问答 chunk、实体、关系、手工记录和缺失来源五类证据摘要测试。
- 前端默认三条原文摘要、其余展开、引用抽屉、降级状态测试。
- Semantica quality report DTO、错误阻断、warning acknowledgement、fingerprint 漂移测试。
- Ontology slice 确定性、邻域完整性、失败回退与 ingest receipt 测试。
- 操作手册生成和 HTML 合同测试。

### 9.2 全量验证

1. `python -m pytest -q`
2. `node --test tests/service/*.test.cjs`
3. `python scripts/verify_all_chains.py --url http://127.0.0.1:8100`
4. Compose 配置与 PostgreSQL/Milvus/etcd/MinIO 健康检查。
5. 隔离项目端到端：本体质量检查→发布→上传→Ontology RAG 抽取→索引同步→问答→页面可见原文→完整溯源。
6. 浏览器检查问答、项目与运行、本体审核台和生成后的 HTML 手册。

任何无法在当前主机完成的验证必须明确列出命令、阻塞原因和已完成的替代证据，不能用部分测试声称全量通过。

## 10. 实施顺序

1. 锁定架构测试与 domain 纯规则，解除 repository/integrations 反向依赖。
2. 提取开放发现 workflow，移除 API 的事务与 `_put`。
3. 绑定权威 actor。
4. 实现 runtime capability 与 projection outbox 状态机。
5. 增加问答来源摘要和默认可见 UI。
6. 接入 Semantica 本体质量报告。
7. 实现 OntologySliceSelector 并接入受控抽取收据。
8. 更新手册生成器、隔离截图和 HTML。
9. 执行全量自动化、真实基础设施和浏览器验证。
