# 生产 Milvus 固定结构与 PG 边界静态审计（2026-10-21）

本报告只读核查实现与既有证据，不运行数据库、HTTP、模型或 pytest，不执行迁移、重建、删集合或数据清理。根任务正在串行运行基线测试；本报告不把静态推断写成实测结果。图谱工具对 `knowledge_service_v2` 的相关文件查询返回空，结合根任务本轮 `list_projects` 超时，按已知文件回退只读源码。

当前现场来自 [前次验收报告](2026-10-21-fixed-fields-and-capacity-report.md)：Milvus 3.0.2，共享 `knowledge_vectors`、1024 维、动态字段关闭、21 明列；两个普通项目 active G2 共 37+5 条有效投影。容量项目 `46249548-2189-4ad7-8f23-70ee2bcc7f6f` 有 150001 实体、300000 关系，本轮未建向量，不能把普通项目指标推广为其向量容量认证。

## 可以保留的生产结构

`domain/search_filter_catalog.py:SYSTEM_COLUMN_DEFS` 的 21 列是检索投影，不是业务表。它们没有测试模型名、合成数据键、任意 metadata JSON 或 `bf_` 自动列。`default_catalog()` 为空业务扩展目录；`discover_page_catalog()` 不扫描 metadata；显式业务扩展必须声明可读列名，不能自动扩列。

| 用途 | 列 | 生产含义 |
| --- | --- | --- |
| 隔离、代际与版本 | `vector_id,index_version,version_id,project_id,record_id` | `project_id` 为 partition key，查询/删除同时约束 project 与 generation；主键为 project+generation+PG version_id 的 SHA256 |
| 检索内容 | `kind,type,ontology_id,text,embedding,sparse` | 当前可搜索实体、关系、片段的派生正文、dense COSINE 与原生 BM25 |
| 适用范围 | 三组 `platform/business/audience_mode/values` | 区分 all/specific/unknown；空数组不能替代 mode |
| 当前业务有效时间 | `valid_from_us,valid_until_us` | 可空 INT64 微秒，半开区间，不承担系统历史真值 |
| 条件辅助 | `chunk_owner_id,filter_present` | 前者只表达片段所属文档；后者区分缺失与显式 null |

`build_projection()` 排除 superseded/deleted/audit 与不可检索种类，不展开端点，不保存断言/治理状态。`projection_fingerprint()` 排除向量，回执属于 PG。`MilvusStore.search()` 三种模式均参数化 project+index_version、Strong 一致性；dense HNSW COSINE，sparse 原生 BM25，中国分词。普通 upsert 不逐条 flush，RPC 以 64 行和 24 MiB 双预算切批。

`services/retrieval.py:_rank_current()` 只按 Milvus 返回 ID 向 PG 有界回表，用 PG 当前 version_id、可见性、端点及完整条件复核；返回前再次核对 generation/watermark。历史 known_at 关键词由 PG 承担。PG 仍拥有版本、断言、多来源证据、本体、治理和业务查询真值。当前 `source_id` 的检索含义及任意 metadata 条件能力是否满足产品需求，由功能审计单独判断，不能用物理 schema 审计宣布功能等价。

无需为了“生产化”重建或 drop 当前共享集合；无需给每行增加模型名、租约、投递 seq、active 控制列或任意 metadata。generation 对应的模型/维度/目录保存在 PG cursor，搜索与增量会检查模型 identity。模型制品换版必须改变稳定 identity；本地 identity 默认是路径+normalized-v1，同一路径替换同维权重不会自行检测，部署应固定制品版本并显式配置 `KG_EMBEDDING_IDENTITY`。

## 必须修复的结构问题

### 1. 普通增量缺续租心跳

位置：`services/projection_sync.py:_apply_milvus_claimed()`（约 381–401），`repository/projection_store.py:claim_contiguous_batch()/renew_claim()`，`services/projection_runtime.py:MilvusProjectionAdapter.apply()`。

普通增量只在 `adapter.apply()` 前 renew 一次，租约默认 30 秒；编码和 RPC 阻塞期间没有 heartbeat。显式构建已有 `_rebuild_lease_guard()`/`LeaseHeartbeat`。前次报告记录冷查询编码 43.156 秒，说明冷模型工作可能超出租约；静态上会在编码后拒写、无法 ack，并等待显式重建，不能断言每次增量都能维持 ready。

**实施子任务 A（独立、小改动）：**

1. 统一普通 Milvus 事件与构建事件的外部工作守卫，复用现有 `_rebuild_external()` 或更名的通用 `_claim_external()`，都启用 10 秒 heartbeat；不用靠扩大租约掩盖问题。
2. `MilvusProjectionAdapter.apply(..., lease_is_current=None)` 接收守卫回调。在编码前后、delete/upsert RPC 前后检查回调及现有 `_check(project,seq,fence)`。回调要同时检查 heartbeat 丢失标志并同步 renew，不能仅凭后台线程最后一次成功放行。
3. 不把编码或 Milvus RPC 放在 PG 事务/激活锁内；整个批次外部工作成功后才执行 `ack_contiguous()`。失租、token 被替换或续租失败都不得推进游标。RPC 已发出而结果未知仍沿用 `uncertain_write` 和新 generation 隔离规则，不在同代际自动补偿。
4. 测试使用可控 waiter/Event，不实际 sleep 30 秒：编码跨过原租约但续租成功；编码中续租失败零 upsert；RPC 后失租零 ack；旧 token 不能 ack；tombstone 同样受守卫；后台与 write-through nudge 均走此路径。

### 2. 全项目回执嵌入 cursor，增量确认 O(N)

位置：`repository/projection_store.py:ack_contiguous()`（约 403–419），`services/projection_sync.py:_milvus_manifest()/_advance_milvus_manifest()/_rebuild_milvus()`，`MilvusStore.validate_projection()`。

每次增量确认复制 `state.expected_projection` 全项目 dict，然后把全部回执序列化回 `projection_cursors.rebuild_state`。单条修订也随已向量化项目规模增长。显式构建还同时物化 records、rows、expected dict 与验证 actual dict。容量项目没有向量化，所以既有大库文字修改结果不覆盖此成本。

**实施子任务 B（PG 内部结构迁移）：**

- 新表 `milvus_projection_receipts(project_id,index_version,record_id,version_id,fingerprint)`，主键 `(project_id,index_version,record_id)`；generation 为正 bigint，fingerprint 为 64 位 hex。业务记录、向量、来源账本不搬家。项目删除可级联移除 PG 回执；是否加 version FK 要兼顾未来历史保留策略，不能让派生回执成为业务历史删除权限。
- 小模块建议 `repository/projection_receipts.py`：`apply_events(claimed,events)`、`replace_page(claimed,rows)`、`for_record_ids(project,index,ids)`、`count(project,index)`、`delete_generation(project,index)`。mutation 必须位于调用方事务，使用 `_locked_claim()` 检查 owner/token/expiry、schema、build_id 和 generation；禁止从 stale claim 修改新代际。
- `ack_contiguous()` 在现有 CAS 事务中仅对本批 tombstone 删除目标 record receipt，对 upsert 写 version/fingerprint，再推进 applied_seq。无 envelope/row 的非投影事件保持既有语义；不得为每批执行全表 COUNT 或重新合成全项目 JSON。cursor 只保留 catalog/model/dim/generation/phase/watermarks/小汇总。
- 新 SQL 迁移以 `jsonb_each(rebuild_state->'expected_projection')` 拆出现存回执；逐项校验 record/version/fingerprint 和 generation，核对插入数后，**同一事务**移除旧 JSON key。异常则回滚保留原状态；不能把错误内容忽略为“成功迁移”。显式 GRANT 应用角色表 DML 权限，遵循迁移器持 DDL 权限约束。
- 现存两个 active G2 的 `index_version/status/applied_seq/schema_version/model/dim/catalog` 不变；不连接 Milvus、不重新编码、不重置 watermark、不触容量项目。迁移只改变 PG 派生回执形态。
- 显式构建快照按页写 receipt，完成全部页后才标记 snapshot 完成；半途崩溃以 build_id/phase 识别不完整状态，清洁重试只重置本 generation receipt。捕获/预检/加载应从现有 `projection_rebuild_snapshots` 按 ordinal/版本页读取，避免完整 records+rows 长驻内存；保留“全部容量/类型预检完成后才编码/外部写”的两遍过程。
- 原生验证按 Milvus Strong iterator 的 ≤512 行页取 PG 对应 record receipts，逐项比对 version+fingerprint，最后比总数；不先构建 actual/expected 全项目 dict。物理 `vector_id` 唯一且 fingerprint 包含 vector_id，结合逐项比对与总数核对可发现旧版本、多行、漏行与篡改。禁止把未知/缺失 receipt 当作可跳过结果。
- 测试：已有 G2 SQL 迁移不换代；NULL/坏回执使迁移整体回滚；单条变更只修改目标 receipt；tombstone/upsert 与 applied_seq 同事务回滚；失租零变更；跨项目/跨代际不可修改；分页面校验检测漏行/错版/改值；大批构建消费惰性页且保持类型预检先于编码。

### 3. 现有 schema 校验不够严格

位置：`integrations/milvus_store.py:_schema_issues()`（约 221–263）、`check_collection()/ensure_collection()`。

现状只拒动态字段和 `bf_`，不拒其他多余列，不检查 `project_id` partition key、auto_id、text analyzer；BM25 函数只核对名称和 sparse 输出；索引主要按名称存在性、metric 字符串包含判断，不核对绑定字段、index_type 或就绪状态。错误物理结构可能被误标为可用。

最小方案：保持 21 列声明，在单一规范化 helper 中比较 SDK 返回的精确字段集合、类型、nullable、主键/auto_id/partition key、维度、容量、ARRAY 元素类型、text analyzer、BM25 function_type/input/output；按索引名称读取结构化 `field_name/index_type/metric_type`，精确匹配。HNSW M/efConstruction、查询 ef 属调参，不应与字段/metric 错误混为一类。索引构建中应明确不可用/等待，不能伪装 schema 已 ready，也不应自动 drop/重建。查询只读校验，显式构建只补允许的缺失标量索引。

失败测试：意外普通列、缺 partition key、auto_id、BM25 输入错绑或函数类型错误、错误 analyzer、同名索引绑错字段、metric 包含伪匹配、索引未就绪；确认均在编码前阻止，并且零 drop/add-field/upsert。SDK 元数据可能是顶层/params 或字符串 JSON，应规范化后比较，不靠 str(dict) 检查。

### 4. 旧、失败代际清理没有持久重试

位置：`services/projection_sync.py:_rebuild_milvus()`（约 480–488），`ProjectionWorker.run_once()`（milvus 被排除在 rollback 维护外），`MilvusStore.delete_versions()`，`ProjectionStore.begin_rebuild()/activate_index()`。

activate 后只对 immediate previous generation 尝试一次 delete，异常直接 pass。进程在 activation 后崩溃、删除失败、连续失败构建或晚到旧 RPC 都能留下永不被查询但持续占空间的行。项目删除已有独立持久 `projection_cleanup_outbox`，不能据此认为代际清理已经覆盖。

最小方案：新增 `milvus_generation_cleanup_outbox`，主键 `(project_id,index_version)`，含 not_before/attempts/owner/token/until/last_error。`activate_index()` 在同一 PG CAS 事务 enqueue 已退役旧代际；`begin_rebuild()` 在明确放弃失败/不确定代际并分配新 generation 时，先把旧 generation 与先前 previous 信息持久登记，不能等覆写 state 后才猜。

后台入口复用 `ProjectionWorker.run_once()`，新增独立 bounded drain，不借用会整项目删除的 handler。claim 用 `FOR UPDATE SKIP LOCKED`+唯一 token+租期；执行前再次检查 cursor：目标不是当前 generation、不是可恢复的在建 generation，且没有可继续写目标 generation 的有效租约。删除必须调用 `delete_versions(project,[version])`，永远不能调用 cleanup_project 或不带 project 的 delete。失败释放并保留错误，指数退避重试；ack 必须 owner+token+未过期 CAS；PG receipt 同代际删除在确认原生删除之后进行。

旧 RPC 可能在本地 timeout 后迟到，第一次删除确认不保证永远不会再出现旧行。对 `uncertain_write` 代际设置保守宽限，并保留退役 tombstone/周期性复扫以最终收敛；不能一次成功即抹除所有重试资格。此扫尾只读/删已登记退役代际，不生成向量、不恢复旧代际，也不枚举删除未知业务数据。

失败测试：activation 后进程崩溃仍有任务；删除失败重试；连续 G1→G2失败→G3激活不会漏 G1/G2；租约超期旧 token 不能 ack；active/在建代际拒删；其他项目同代际不受影响；迟到 RPC 后再次清理；空/不存在集合幂等；项目删除整体清理与代际清理可重复执行。

## 只属部署与调参的事项

`docker-compose.yml` 的单机 Milvus 3.0.2、10 GiB 限额、external etcd/MinIO、绑定本机端口、60 秒 session TTL 都是部署选择，不是业务 schema。单机不等于高可用；450k×1024×4 约 1.72 GiB 原始 dense 向量，不含 HNSW、growing segment、索引构建及加载副本，不能仅凭该数字承诺现有资源满足容量延迟目标。HNSW M/efConstruction、查询 ef、分区数、CPU/内存、batch_size、索引等待预算及 Strong 的成本需在明确数据规模/召回质量下调优。

生产可用性不应靠降级、跳过 PG 回表、跳过治理、舍弃过滤分支或改成弱一致性换取。前次报告容量项目未向量化的边界仍成立。

## 旧测试适配结论

`tests/service/test_milvus_v2.py` 依赖已移除 V2_FIELDS、per-project collections/alias、active/fence control collection、自动权威修复和回退；恢复旧接口会把废弃架构带回生产，可删除该旧适配测试文件。

约束替代：`test_flat_milvus_store` 覆盖 21 列/参数化三模式 project+generation/删除范围/iterator；`test_search_projection` 覆盖身份、可见性与精确时间；`test_milvus_business_filters` 覆盖参数化、条件和半开时态；`test_shared_milvus_pipeline` 覆盖增量、追平、旧 RPC、失租、模型变化；`test_projection_explicit_build` 覆盖不自动建/不自动重试/过期未知写/项目隔离；`test_native_page_retrieval` 覆盖有界 PG 回表、精确 AST、补充预算与返回前切代拒绝。

仍建议补当前版显式测试：Milvus 返回旧 version_id 而 PG 同 record_id 已是新版本时必须丢弃；空 generation/watermark=0 可 Strong 验证并激活。上文 A/B/schema/cleanup 的新失败测试不能用旧 V2 控制集合测试替代。

上述审计结论先于实施形成，不把静态风险和接口方案本身当作修复通过证据。

## 后续实施检查点

根任务授权后，源代码已加入普通增量续租守卫、独立 PG receipt 表（迁移 0025）、按页捕获/预检/加载/原生验证、精确物理 schema 校验、PG 资格版本 ID 参数化检索接口，以及持久 generation 清理（迁移 0026）。保留共享集合的 21 明列，未增加测试模型或 metadata 自动列。

根任务串行验证首批新测试 **33 passed**，证据：[production-storage-green.log](../../.benchmarks/2026-10-21-production-storage-green.log)。首轮红测分别发现无心跳、无 receipt 表/迁移、物理 schema 校验不足、无清理 outbox、manifest 全量比较等预期缺陷；迁移测试另外暴露并修正了 SQL 适配层将 JSONB `?` 运算符识别为占位符的问题，现使用 `jsonb_exists()`，坏回执用明确 `P0001` 原因和回滚断言验证。

此检查点仍等待索引 pending 就绪与清理队尾失租的新增边界测试，以及原有投影/检索回归。尚未据此声称现场已迁移、现有项目已重建或容量项目已向量化。本代理没有运行 pytest、操作数据库、重启现场或提交代码；测试与部署由根任务统一串行控制。
