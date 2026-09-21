# 统一 Provenance 设计

## 目标

为知识问答建立一条可点击、可审计、可长期复现的来源链：

`答案引用 → 检索运行 → 规范知识记录版本 → 一条或多条审核/断言来源 → 原文片段版本 → 文档版本`

SQLite 仍是唯一真相源。Milvus、Neo4j 和前端展示都只是派生能力；Provenance 只记录“事实如何形成和被使用”，不复制一套业务真值。

## 已确认的页面方案

采用 A「侧栏钻取」：用户在答案或证据列表中点击 `[E1]`，不离开问答上下文，在右侧打开溯源抽屉。

抽屉包含两个页签：

- **证据链**：按照答案、检索、记录版本、审核、断言、片段、文档版本的顺序展示。
- **接口数据**：展示同一接口返回的结构化数据，便于开发和审计。

页面沿用现有项目的浅绿色、低圆角、细边框、紧凑字号和原生按钮风格，不引入新的 UI 框架。窄屏时抽屉变为问答内容下方的纵向面板。

## 三种实现方式及选择

### 1. 仅从现有表实时拼装

优点是零新增存储；缺点是当前系统没有持久化检索运行和答案，因此无法复现“当时为什么选中 E1”。不采用。

### 2. 复制完整业务对象到独立 provenance 库

查询简单，但会产生第二套事实和同步一致性问题，违背 SQLite 唯一真相源。拒绝。

### 3. 追加式活动与边 + 现有业务表解析（采用）

只持久化系统当前缺失的活动事实：检索运行、答案生成和它们引用的稳定版本标识。记录、断言、审核、摄取运行、原文仍从现有 SQLite 表解析，不复制内容。

这使 provenance 成为一层很薄的审计账本和读取模型，而不是新的业务数据库。

## 稳定引用

所有链路节点使用带类型前缀的稳定引用：

- `answer:{answer_id}`
- `answer:{answer_id}#{citation}`
- `retrieval-run:{run_id}`
- `record-version:{version_id}`
- `record:{record_id}`
- `assertion:{assertion_id}`
- `review-event:{event_id}`
- `ingest-run:{run_id}`
- `chunk-version:{version_id}`
- `document-version:{version_id}`

接口不要求前端理解数据库主键关系；前端只使用 `ref`、`type` 和明确的展示字段。

## 持久化模型

新增迁移 12，创建三张表。`record_version_assertions` 是正式写入时产生的权威版本支撑关系；`provenance_activities` 是可做一次终态转换的运行头；`provenance_edges` 是只追加、不可改写的答案冻结关系。

### `record_version_assertions`

- `project_id`
- `record_id`
- `record_version_id`
- `assertion_id`
- `assertion_event_id`
- `created_at`

`FormalFactWriter` 在同一正式写入事务中保存规范记录版本、接受 assertion、产生 assertion event，并写入这一关联。它明确回答“哪个 assertion 的哪次决定支撑了哪个不可变 record version”，不再依赖 `canonical_record_id` 推测。

对于迁移前的历史数据，只在 `assertion.created_at == record_version.recorded_at`、canonical ID 一致且能唯一定位接受事件时做确定性回填；其余不猜测。检索到这类记录时返回 `record_version_support_ambiguous` warning，并将 `integrity.complete=false`。

### `provenance_activities`

- `id`：活动 ID，例如 `rr_...` 或 `ans_...`
- `project_id`
- `kind`：首期为 `retrieval`、`answer`
- `status`：`running`、`completed`、`failed`、`cancelled`
- `payload`：JSON；保存可观察输入/输出摘要
- `started_at`、`completed_at`

检索 payload 保存 query、scope、请求模式、实际模式、后端、降级原因、候选数量、通道统计和 embedding 标识。答案 payload 保存最终文本、生成模式、offered citation 和实际采用 citation。不得保存模型隐藏思维链。

运行头只允许以 compare-and-set 方式从 `running` 转换一次到终态；终态不可再次改写。终态转换、终态 payload 和该终态产生的边必须在同一事务提交。重复提交相同内容幂等，冲突内容返回版本冲突。

### `provenance_edges`

- `id`
- `project_id`
- `activity_id`
- `source_ref`
- `relation`
- `target_ref`
- `ordinal`
- `payload`
- `created_at`

首期关系：

- `retrieval-run:* --considered--> record-version:*`
- `answer:* --used--> retrieval-run:*`
- `answer:*#E1 --offered--> record-version:*`
- `answer:*#E1 --cites--> record-version:*`
- `record-version:* --supported-by--> assertion:*`
- `assertion:* --decided-by--> review-event:*`
- `assertion:* --extracted-from--> chunk-version:*`
- `chunk-version:* --sourced-from--> document-version:*`
- `document-version:* --processed-by--> ingest-run:*`

边 payload 保存 rank、score、channel、selected 等审计属性。检索完成时把当时所有可确定的来源分支冻结为边；后续 assertion 撤回、改绑、合并或文档修订均不会改变旧答案的链路。

数据库约束：

- `record_version_assertions` 使用两组复合外键：`(project_id,record_id,record_version_id)` 指向同项目记录版本；`(project_id,assertion_id,assertion_event_id,record_id)` 指向同项目、属于该 assertion 且 canonical target 等于该 record 的审核事件。迁移同时建立被引用列的唯一索引。映射随 project、record version 或 assertion event 删除而级联删除，并以 record version、assertion、event 组成唯一键。
- `provenance_activities.project_id → projects(id) ON DELETE CASCADE`，activity 使用 `UNIQUE(project_id,id)`。
- `provenance_edges(project_id,activity_id) → provenance_activities(project_id,id) ON DELETE CASCADE`，保证边和 activity 属于同一项目且随其删除。
- `kind`、`status`、`relation` 使用 CHECK；payload 使用 `json_valid`。
- `(project_id,activity_id,source_ref,relation,target_ref,ordinal)` 唯一，保证重试幂等。
- 为 `(project_id,source_ref)`、`(project_id,target_ref)`、`(project_id,activity_id)` 建索引。
- 不对多态 ref 建业务外键，但 ref 只能由服务端生成，并采用固定前缀 + UUID/版本 ID；不解析用户可控 record ID 中的 `:` 或 `#`。

## 深模块接口

新增 `services/provenance.py`，对调用方暴露五个操作：

1. `begin_retrieval(project_id, request) -> run_id`
2. `complete_retrieval(project_id, run_id, context, evidence) -> answer_id`
3. `complete_answer(project_id, answer_id, run_id, answer, mode, evidence)`
4. `fail_activity(project_id, activity_id, status, public_error)`
5. `trace_answer_evidence(project_id, answer_id, citation) -> response`

`Repository` 只提供通用的活动/边写入读取、原子终态转换，以及按 `version_id` 读取记录版本的方法。跨表现状解析发生在检索完成时；其结果以版本化边冻结。读取接口只读冻结边和被引用的不可变版本，不根据当前 assertion 状态重新猜测。

## 问答流调整

1. `stream_events` 在检索前创建 running retrieval activity。
2. 现有 `question_context` 完成检索；失败则将 retrieval 原子转为 failed，再发送 `error`。
3. 检索成功后，在一个事务内完成 retrieval、创建 running answer、写 offered evidence 边，并冻结每条证据在此时刻的全部可确定来源分支。
4. 事务提交后才发送 `evidence` SSE，因此生成期间点击 E1 也能读到 status=running 的溯源。
5. `evidence` 事件新增：
   - `answer_id`
   - `retrieval_run_id`
   - 每条证据的 `provenance_ref`
6. LLM 或 evidence-only 输出完成后，只接受本次 offered 集合中符合 `E[1-9][0-9]*` 的引用；大小写固定为大写，重复编号去重，未知编号写 warning 而不建悬空边。
7. 在一个事务内完成 answer 并写实际 `cites` 边；提交成功后才发送 `done`。因此 `done.provenance_complete` 恒为 true，不提供弱成功语义。
8. 若答案生成、provenance 完成写入或流式处理失败，answer 标记 failed 并发送 `error`，不发送 `done`。客户端断开/生成器取消时标记 cancelled。
9. 错误只保存稳定类型和可公开消息，不保存密钥、请求头或供应商完整响应。

## HTTP 接口

新增只读接口：

`GET /api/projects/{project_id}/answers/{answer_id}/evidence/{citation}/provenance`

成功返回：

```json
{
  "schema_version": "1.0",
  "subject": {
    "type": "answer_evidence",
    "ref": "answer:ans_123#E1",
    "answer_id": "ans_123",
    "citation": "E1"
  },
  "retrieval": {
    "run_ref": "retrieval-run:rr_123",
    "query": "退款需要什么凭证？",
    "requested_mode": "hybrid",
    "active_modes": ["keyword", "semantic"],
    "rank": 1,
    "score": 0.94,
    "selected": true
  },
  "answer": {"status": "completed", "citation_status": "cited"},
  "nodes": [],
  "edges": [],
  "integrity": {
    "complete": true,
    "warnings": []
  }
}
```

`nodes` + `edges` 明确表达多来源分支，不能压平成单链。所有 node 统一包含 `type`、`ref`、`label`、`status`、`occurred_at`，并有稳定的最小展示字段：

- record version：`record_id`、`version`、`kind`、`text_preview`、`valid_from`、`valid_until`、`recorded_at`
- assertion：`assertion_id`、`kind`、`status_at_capture`、`quote`、`start_char`、`end_char`
- review event：`event_id`、`from_status`、`to_status`、`actor`、`reason`、`created_at`
- chunk version：`record_id`、`version_id`、`text_preview`、`start_char`、`end_char`
- document version：`document_id`、`version_id`、`version`、`title`、`excerpt_before`、`highlight`、`excerpt_after`
- ingest run：`run_id`、`attempt`、`status_at_capture`、`created_at`、`updated_at`

warning 固定包含 `code`、`node_ref`、`message`。缺失旧数据时仍返回 200，并通过 `integrity.complete=false` 表达；answer 存在但尚在生成时返回 200、`answer.status=running`、`citation_status=offered`。只有 answer 或 offered citation 不存在时返回带稳定 `code` 的 404。

兼容性约束：现有 SSE 字段保持不变，只追加字段；现有客户端不会受影响。

## 链路解析规则

链路在 `complete_retrieval` 时解析并冻结，读取时不重算：

1. citation 的 offered 边钉住检索返回的不可变 `record-version:{version_id}`。
2. 若证据是 entity/relation，从 `record_version_assertions` 捕获所有明确支撑该版本的 assertion 及精确 assertion event；每个 assertion 独立形成一个来源分支。没有权威映射的旧数据只报告歧义，不按当前 canonical ID 猜测。
3. assertion 的片段必须解析为 `chunk-version:{version_id}`，不能只保存 chunk 业务 ID；再连接精确 document version 和 ingest run。
4. 若证据本身是 chunk，直接走 `chunk-version → document-version → ingest-run`，缺少 assertion/review 不视为异常。
5. 合法的手工 entity/relation 可在 record version 节点终止，标记 `terminal_reason=manual_record`，不视为不完整。
6. 只有声称存在上游来源却无法解析时才产生 warning，例如 `assertion_event_missing`、`chunk_version_missing`、`document_version_missing`、`legacy_unversioned_source`。

## 前端交互

- 答案中的 `[E1]` 转为按钮；证据列表的引用编号也可点击。
- 点击后请求 provenance 接口，打开右侧抽屉并显示加载骨架。
- 节点按时间链纵向排列，颜色只表达状态，不为每种节点使用不同颜色。
- 文档片段节点提供“查看原文并定位”，复用现有 source evidence dialog 的展示组件，但数据必须来自冻结的历史 document version。接口直接返回经过长度限制和 HTML 转义前原始值的 `excerpt_before/highlight/excerpt_after`，不跳转到当前文档版本。
- 记录版本节点提供“查看知识记录”，复用现有 evidence inspector / record dialog。
- 接口数据页签展示格式化 JSON；不显示密钥、完整模型请求或隐藏思维链。
- 关闭后焦点回到触发引用；支持 Escape；窄屏改为内联面板。
- `kg_qa_v1_*` key 保持不变；assistant 历史对象追加 `answer_id`、`retrieval_run_id` 和轻量 `evidence` 摘要，旧对象照常读取。摘要只保存 `citation`、最多 240 字的 `text_preview`、`kind`、`version`、`provenance_ref`，不保存 embedding、metadata、properties 或完整文档。历史没有 `answer_id` 时引用保持普通文本，并提示“该历史回答生成于溯源记录启用前”。
- 流式期间继续只向 text node 追加文本；`done` 后以文本 token 化方式把合法 citation 替换为 button，禁止把模型答案写入 `innerHTML`。
- 抽屉请求使用独立 AbortController；关闭抽屉或切换项目时取消，响应落地前再次核对 project ID 和 answer ID，防止旧响应污染新页面。

## 安全、性能与保留策略

- 所有查询必须带 `project_id`，防止跨项目读取。
- query 和 answer 属于业务数据，首期随项目删除而级联删除；项目删除响应追加 `record_version_assertions`、`provenance_activities`、`provenance_edges` 计数。
- 不存储 API key、Authorization header、模型隐藏思维链或完整供应商响应。
- 每个答案写入复杂度为 `O(k + 来源分支总数)`。首期完整保存全部已映射来源分支，不设静默上限；若未来增加限额，必须返回 `lineage_truncated` warning 且 `integrity.complete=false`。
- 链路接口使用主键/索引查找，不扫描全项目记录。
- `export_projection` 在现有顶层结构追加 `provenance: {record_version_assertions, activities, edges}`，不改变已有字段；兼容导出接口使用同一结构，能够完整重建权威映射和答案冻结链。

## 测试策略

### Repository

- 迁移 12 可从真实 v11 库安全升级、重复启动幂等、失败完整回滚且外键在升级后生效。
- record-version/assertion/event 的权威映射与正式写入同事务提交；测试锁定同项目、event 属于 assertion、event canonical target 等于 record 三个不变式，任一失败全部回滚。
- 活动和边追加、失败状态、项目隔离、activity/project 级联删除。
- 按 `version_id` 读取历史记录，不会误取当前版本。

### Service / API

- evidence-only 和 LLM 模式均持久化 run/answer。
- SSE 追加 ID 字段且保持旧字段兼容。
- `[E1]` 只连接对应记录版本。
- 历史版本修改后仍返回当时引用版本。
- assertion 后续撤回、改绑、合并后，旧答案仍返回捕获时的 assertion 和精确审核事件。
- 多 assertion 支撑保留全部分支；直接 chunk 和手工记录按各自终止规则判断完整性。
- 缺少 assertion / review / document 的旧数据返回部分链和 warnings。
- 跨项目 answer ID 返回 404。
- 流失败将活动标记失败。
- evidence 发出后、answer 完成前可读取 offered provenance；客户端断流标记 cancelled。
- LLM 未引用、重复引用、引用 E99 不产生悬空边，并返回稳定 warning。
- answer 完成事务回滚时不发送 done。
- 历史文档定位始终读取冻结版本。
- 导出保持旧字段兼容并包含 mappings，删除返回三张表计数。

### Frontend

- 点击答案 citation 或证据编号只发起一次 provenance 请求。
- 抽屉打开、切换证据链/API、Escape 关闭、焦点恢复。
- 加载、404、部分链警告均有明确状态。
- 刷新后带 `answer_id` 的历史记录仍可打开溯源。
- 页面视觉属性复用现有设计 token/选择器，不改变其他工作台布局。
- 项目切换和关闭抽屉会取消旧请求；模型返回 HTML 只作为文本展示，citation 转换不引入 XSS。

## 首期边界

首期覆盖问答闭环与已存在的摄取/审核数据解析。不会：

- 新建独立图数据库或 Semantica 存储；
- 保存模型思维链；
- 对所有历史问答进行推测性回填；
- 将 Milvus/Neo4j 提升为 provenance 真值源；
- 在首期提供跨项目全局溯源搜索。
