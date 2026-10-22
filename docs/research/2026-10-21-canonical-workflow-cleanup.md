# 当前工作流收敛与旧兼容清理

环境：`D:/anaconda/envs/llm_model/python.exe`，现有 PostgreSQL / Milvus Docker 服务，本地 8100。沿用现有工作区，未重置、覆盖或提交其他已有修改。

## 本轮删除的实现

| 原实现 | 当前唯一入口或行为 |
|---|---|
| discovery 全量自动生成、旧审核/提交、mapping-only finalize、旧工件补读 | 明确选批、只读预览、确认复用或加入指定工作草案 |
| 旧本体直写、术语写适配器、响应兼容包装 | 当前草案 commands、submit、decisions、publish；批量命令同事务提交 |
| `/records/query` 全量读取后切片 | `/records/page` 范围下推和游标分页 |
| 旧项目迁入模块、路由、任务、按钮和同步脚本 | 当前文档/文件摄取；Turtle 差异仍经草案治理 |
| 从 source_id、候选 metadata 或旧 anchor 猜正式来源 | accepted assertions 指向 canonical 记录；文档切片的 source_id 保留其实际归属语义 |
| edge-v0 读取时迁移、缺冻结事件时借用当前状态、无效位置伪造摘录 | edge-v1 冻结来源和审核事件；无法核验时明确 unresolved |
| extraction 的重复开关、旧 actor 静默丢弃 | extraction_mode 唯一输入；认证会话提供审计身份，未知字段 422 |
| 本体摘要读取时反复解析 RDF | 发布时保存规范摘要，读取直接复用 |
| 隐藏旧编辑器、ontology-details、死样式和 properties 属性回退 | 当前建模检查器、正式 attribute 记录、明确缺少答案标识提示 |
| 旧问答上下文、属性抽取全局注册代理/列表结果 | 当前统一检索上下文、显式注入并返回 batch |
| PostgreSQL 向量编解码/空存储方法/失效查询参数 | Milvus 真实向量与投影协议 |
| 旧列表适用范围和双 hash、隐藏 DOM 的启动迁移 | `{mode, values}` 维度、统一非空范围身份、独立的项目/页面已应用范围 |
| 草案 legacy_artifact_id 和多版快照行形状 | 数据库迁移 0023；快照只导出、接受当前格式 18 |

当前主链路：候选选择 → 只读预览 → 确认复用，或加入工作草案 → 命令/校验/审核 → 原子发布本体、正式事实和来源断言 → 异步外部投影。

保留来源撤回、CAS、幂等、多批绑定、依赖裁决、双时态历史、不可变发布、受控重分类、真实异常处理及可选外部服务。PostgreSQL 内部 SQL/行访问适配仍服务于单一 PostgreSQL 后端，不提供 SQLite 存储。未以关键词删除有效领域能力或第三方运行库的适配。

## 已有数据与一次性维护

- 只读核对现有 8 份本体版本，存储摘要与当前 RDF 计算结果完全一致，无需读时补建或摘要迁移。
- 两个已审计项目没有旧 list 范围数据。统一范围身份后，一次性刷新 152 个历史版本中的 94 个派生 scope_identity，以及 30 个 active fact_keys。
- 维护前预检零键冲突；30 次真实 planner 查询都解析到原 canonical 事实。使用 SERIALIZABLE、既有正式写锁序与项目锁，验证前后权威数据指纹一致。payload、记录 ID、版本、时间戳、审计和 retired keys 均未修改。
- 已应用迁移 0023，数据库当前版本 23。外部投影使用现有按项目 generation/alias 重建，不清空其他项目。
- 停服维护使用已核验的工作目录、解释器、模块及 8100 PID。补充全量只读空闲核查：3 个 job、8 个 batch 均 completed，无活动 ingest run、租约或任务；不会依赖 API 最近 100 条列表判断空闲。

旧数据不触发运行时迁移、双写或猜测恢复；新协议明确拒绝旧输入。

## 验证状态

前端最新全套 Node 契约：423 通过、0 失败。真实 Chrome 验证未登录业务请求为 0；鉴权策略未返回前 body/main/aside 均隐藏。该次带验证负载的登录导航为 4.33 秒，只用于流程正确性，不作为无负载性能结论。

Python 扩展回归、当前实服务协议/导出检查、投影重建和最终页面响应采样仍在收尾；完成结果将写入本文件和配套 JSON。早期失败日志保留，用于区分旧测试契约、已发现的真实缺陷与最终复测结果。

设计与执行清单：

- `docs/superpowers/specs/2026-10-21-canonical-workflow-cleanup-design.md`
- `docs/superpowers/plans/2026-10-21-canonical-workflow-cleanup.md`

上一轮数据库和页面冗余的测量见 `docs/research/2026-10-21-page-read-audit.md`。当前证据支持应用读取/渲染职责和旧执行路径冗余问题；不把低数据量页面慢直接归结为 PostgreSQL 引擎性能不足。
