# 多平台规则的适用范围与检索

同一个知识项目可以保存美团、淘宝、京东等平台的规则。上传时请填写适用平台；还可以填写适用业务和适用对象。检索与问答指定平台后，只使用与该范围相交的资料，未标范围的历史资料不会被当作该平台规则。

上传接口示例：

```json
{
  "title": "美团直播管理规范",
  "text": "……",
  "applicability": {
    "platforms": ["美团"],
    "businesses": ["直播"],
    "audiences": ["商户"]
  },
  "published_at": "2026-09-01T00:00:00Z",
  "valid_until": null
}
```

- 同一维度的多个值表示“任一匹配”，不同维度之间要求同时匹配。
- 显式填写 `valid_from` 时以它为准；否则使用 `published_at` 作为生效时间。
- `valid_until` 为空表示没有设置失效时间。系统不会根据下一份文档的发布时间猜测旧规则何时失效。
- 多平台检索会按平台分别筛选、排名和保留额度；没有资料的平台仍返回空分组。
- 相邻切片只用于补足命中条款的前后文，限定在同一文档版本和适用范围内，不参与原始排名。固定长度、段落和章节切片都支持。

历史资料需要人工或通过独立迁移任务补录适用范围。系统不会批量猜测并改写历史资料。范围过滤能阻止跨平台证据混用，但语言模型的表述仍应通过返回的证据、平台和有效时间核对。

## 大规模项目约束

生产容量按单项目至少 5000 份规则文档、约 10 个平台规划。文档切片和图谱派生后，在线记录数会达到数万至数十万，因此正式架构必须在 PostgreSQL、Milvus 和 Neo4j 中保存可查询的规范范围字段，并在向量排名和图遍历之前执行原生过滤。

当前“主存储先过滤，再把候选版本 ID 交给 Milvus”的路径只用于兼容和迁移，不作为目标架构。目标实现不向 Milvus 传递数千个候选 ID；平台、业务、对象、有效时间由统一条件模块编译为各存储的原生过滤表达式。详细设计见 `docs/superpowers/specs/2026-10-14-native-applicability-filtering-design.md`。

PostgreSQL 原生范围列分三阶段上线：0013 只增加 nullable 列，0014 回填并安装约束，0015 并发创建索引。0013 不设置 `unknown` 等语义默认值，避免旧实例在迁移窗口把真实范围静默写错；0014 安装非空检查后，尚未升级的旧实例写入会明确失败。滚动发布时应先把所有写流量切到已支持原生范围双写的新版本，再执行 0014；迁移 DDL 默认最多等待锁 5 秒，可通过 `KG_MIGRATION_LOCK_TIMEOUT_MS` 调整，超时后不会登记迁移，可安全重跑。

没有指定平台、但指定或识别出“直播”等业务时，系统应检索全部平台的该业务规则，并按实际平台分别排名和保留额度。明确适用于所有平台的规则、范围未知的历史规则和平台特定规则必须分组展示，不能把空范围默认解释为通用规则。

## 投影状态与降级

PostgreSQL 是范围与版本的权威来源。Milvus 和 Neo4j 只在对应项目的 cursor 为 `ready`、schema version 与当前代码一致，并且 `applied_seq` 等于项目 `projection_seq` 时参与在线读取。只要范围修改、删除或 supersede 尚未投影，检索立即降级到 PostgreSQL 原生范围过滤；响应会标记降级原因，不会继续读取陈旧投影，也不会向 Milvus 发送候选 ID 列表。

后台 projection worker 默认每 2 秒扫描一次，每个项目每个目标最多连续处理 10 个 batch，并每 60 秒执行一次权威状态审计。多进程可同时运行，claim token、lease 和 fence 会阻止旧 worker 提交迟到写入。schema 不匹配或 failed rebuild 不会被普通 drain 绕过，而是进入可恢复 rebuild。

重建按“快照 → 增量追平 → 校验 → alias 切换”执行。切换前必须再次确认水位，旧 destination 保留 7 天并继续接收增量；过期后由显式 rollback maintenance 清理。运维人员应同时检查 PostgreSQL `projection_cursors`、Milvus alias 和 Neo4j 投影，不应只根据 collection 存在就判断可用。

## 真实容量验收

容量验收工具默认只生成计划，不连接服务：

```powershell
python scripts/verify_native_applicability_capacity.py --dry-run `
  --report artifacts/native-applicability-capacity-plan.json
```

真实执行必须显式提供专用验收项目及三个真实存储，项目中至少有 5000 份 source document、10 个平台和 100000 条当前 record：

```powershell
$env:KG_CAPACITY_POSTGRES_DSN = 'postgresql://...'
$env:KG_CAPACITY_MILVUS_URI = 'http://127.0.0.1:19530'
$env:KG_CAPACITY_MILVUS_COLLECTION = 'knowledge_records_v2_or_project_alias'
$env:KG_CAPACITY_NEO4J_URI = 'neo4j://127.0.0.1:7687'
$env:KG_CAPACITY_NEO4J_USERNAME = 'neo4j'
$env:KG_CAPACITY_NEO4J_PASSWORD = '...'
$env:KG_CAPACITY_PROJECT_ID = 'capacity-project'
python scripts/verify_native_applicability_capacity.py --execute `
  --report artifacts/native-applicability-capacity.json
```

JSON 报告只有在真实三库均连接，projection watermarks ready，范围编译、单平台 hybrid、10 平台 grouped、2-hop 的 p95 均达标，PostgreSQL `EXPLAIN` 无 `Seq Scan`，Milvus filter 无 `candidate_ids/version_id`，且范围泄漏为 0 时才会写 `status: passed`。显式 dry-run 返回 0 但写 `status: skipped`；execute 缺环境返回 3；真实执行失败或阈值不达标返回 1。fake client、纯单测和 dry-run 都不能作为容量通过证据。

迁移演练使用独立的、可丢弃的 PostgreSQL database，名称必须以 `kg_acceptance_` 开头：

```powershell
$env:KG_ACCEPTANCE_MIGRATION_DSN = 'postgresql://owner:...@127.0.0.1:5432/kg_acceptance_applicability'
python scripts/rehearse_migration_0013.py --execute --allow-disposable-database `
  --report artifacts/migration-0013-rehearsal.json
```

脚本先执行 0001–0012，插入旧范围样本，再执行 0013 至当前最新迁移；它检查范围回填、并发索引、projection outbox、rebuild snapshot、cleanup outbox 和第二次执行幂等。不要把生产库 DSN 传给该脚本。

截至 2026-10-15，本机开发会话只验证了脚本参数、报告 schema、dry-run、Python 编译和静态 diff；没有执行真实 Milvus、Neo4j 或重型容量验收，因此没有真实容量通过结论。
