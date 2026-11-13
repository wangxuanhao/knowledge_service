# 实体身份、属性与来源索引升级

本说明对应 `feat/entity-identity-attributes`。2026-11-13 已按用户授权同步业务库：备份归档校验通过，schema 达到 40，3 个项目来源回填和 Milvus 重建完成且 ready。当前 161 条来源上下文（160 条活跃），当前缺失/非法支撑均为零；重建后的项目向量数为 160、0、72。业务记录修订仍为 292 条、断言仍为 478 条，没有执行实体合并或文档重提取。

本次备份及逐项目验收记录保存在忽略目录 `.benchmarks/entity-attributes-upgrade-20261113T064322Z/`；该备份采集时 schema 已为 39，之后通过编号迁移 0040 修复旧删除标记的兼容性。API 已恢复于原端口 8100。

## 日常操作

- 在“本体建模层”选中实体类，在类详情配置身份规则：“跨文档统一身份”用于确认属于长期对象的类，“按业务区间区分”用于事件、合同等需保留时间身份的类。缺少声明的旧类保持保守策略。修改先进入草案，审核发布后用于新匹配；已有重复数据仍须扫描、预览、确认。
- 本体定义属性的 IRI、所属类型、数据类型、描述和约束；图谱侧栏展示实例的具体值。一个属性可列出多个值，每个值独立显示来源数量、业务期间与证据。长段落按需展开，不变成属性节点或连线。
- 图谱和脑图悬浮仅展示少量摘要，点击实体进入同一个属性侧栏；检索属性命中直接定位相应事实，并核对命中修订。过期命中须显式刷新后才能阅读当前值。
- 上传/重处理中的“属性提取”默认关闭；启用后使用已发布 TTL 的属性定义。已有 TTL 不代表已有文档会自动重提取。仅存文档模式不能提取属性。
- 已有重复实体在知识台账的实体治理区域点击“扫描已有重复实体”。逐组检查冻结来源、原始时间、受影响事实和隔离冲突，预览后勾选组并明确确认。页面加载不会启动全库扫描或自动合并。
- 单值冲突进入统一“知识审核”集合抽屉。先阅读完整值及分页证据，再为每个成员明确选择接受/拒绝，可修正生效期间；没有默认胜出值。驳回所有值仍须满足活跃实体的必填约束。实体来源全部撤回时仅允许拒绝关闭。

TTL 导入使用同一套类声明，示例：

```turtle
@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix kg: <https://knowledge.local/schema/> .
ex:Platform a owl:Class; kg:identityPolicy "reference" .
ex:Event a owl:Class; kg:identityPolicy "scoped" .
```

可选 `kg:identityScope` 固定该类的业务身份范围；`kg:identityKey "code"` 声明业务唯一键字段。具体实体的键值保存在 `metadata.identity_keys.code`，不是文档日期，也不是属性定义的名称。强键冲突不会被同名或向量相似度覆盖。

## 发布前准备

先备份 PostgreSQL（含迁移台账）及 Milvus；保留当前应用版本和配置。安排维护窗口暂停业务写入及旧 worker，避免新旧写入语义混用。所有命令在项目根目录执行，使用现有 Python 环境；凭据由应用配置加载，不在终端打印或作为命令参数传递。

```powershell
& D:/anaconda/envs/llm_model/python.exe -m knowledge_service.repository.migrate --status
& D:/anaconda/envs/llm_model/python.exe -m knowledge_service.repository.migrate --check
```

新版本期望 schema **40**：0036 添加事件化来源上下文和身份键注册；0037 添加来源/窗口向量回执及冻结投影输入；0038 添加属性冲突、重复扫描支持；0039 添加属性原生值指纹；0040 保留空字符串、零、空集合等旧删除标记的 JSON 假值语义。0036 不会在迁移中扫描并回填全部历史断言。API 启动仅检查 schema，不替代迁移器。

确认发布窗口后才执行：

```powershell
& D:/anaconda/envs/llm_model/python.exe -m knowledge_service.repository.migrate
& D:/anaconda/envs/llm_model/python.exe -m knowledge_service.repository.migrate --check
```

不要用 `--rewrite-checksums` 掩盖已经应用的迁移差异，也不要手改迁移台账。迁移失败应先定位原因，保持旧服务停止写入并按备份恢复方案处理。

## 按项目分批回填来源

使用现有应用角色连接，先只读取诊断。以下 Python 片段中的项目 ID 是明确选定的目标；回填函数会改写该项目的派生来源上下文，执行前应完成数据操作确认。

```python
from knowledge_service.core.config import load_environment
from knowledge_service.repository import Repository
from knowledge_service.repository.connection import resolve_dsn

load_environment()
repo = Repository(resolve_dsn())
project_id = "已确认的项目ID"
print(repo.source_context_diagnostics(project_id))
# 每次至多一批；保存返回的 after，下次传入，可中断续跑。
result = repo.backfill_source_contexts(project_id, batch_size=500,
                                      max_batches=1, after=None)
print(result)
repo.close()
```

重复执行至 `complete=True`；未完成时把上一轮 `after` 传给下一轮。回填按不可变断言和决策事件恢复各次接受/撤回期间，可安全重试，不会合并实体、选择属性胜者或改写原始文档/断言。

`missing_active`、`invalid_active` 必须为零；否则来源索引构建和跨时间身份归并保持关闭。检查未映射的当前接受决定、缺失冻结文档版本、非法期间/适用范围；通过正式审核修复或撤回不可信支撑后再回填。`missing`、`invalid` 包含已关闭历史问题，仅供诊断，不能据此改写历史。`support_count`、`active` 可用于前后数量核对。

## 重建索引与启用

启动匹配 schema 的新版 API/worker。在目标项目现有“向量化”入口提交显式重建，或通过已认证 API 提交 `POST /api/projects/{project_id}/indexes/rebuild`。这是项目级任务；不要直接删除共享 `knowledge_vectors` 集合。

当前 Milvus 投影 schema 为 **4**，固定列为 **27**。显式重建可向符合旧固定 schema 的集合添加六个允许的 nullable 上下文字段，普通查询/增量投递不会偷偷改 schema。属性正文按 6000 字窗口、400 字重叠保存；每条事实有 20000 向量行、估算 16 MiB 的显式预算。超出预算会报错，不会静默只索引开头。

职责保持分离：当前关键词、语义、混合检索及可检索字段筛选在 Milvus 执行，业务事实、审核、来源及版本保存于 PostgreSQL，命中按 ID/修订回填并复核权威状态。Milvus 不是业务数据的唯一副本。本次对 3 个真实项目的 9 次检索调用共观察到 36 次原生检索；禁用 PostgreSQL 的关键词排序方法后仍能完成，属性通道均参与。

Milvus 3.0 的旧封闭分段可能在追加字段后仍停留在旧 schema，表现为新索引一直 InProgress。Compose 已启用 `DATACOORD_COMPACTION_BUMPSCHEMAVERSION_ENABLED`；显式升级会在有进度计数且需要时执行有界分段压缩，再刷新加载及等待新索引。未知/失败进度不作完成。本次验证 255 条旧索引记录保留，4/4 分段升级且新增筛选索引完成。参见 [Milvus 字段升级说明](https://milvus.io/docs/add-fields-to-an-existing-collection.md)。

任务完成后读取 `GET /api/projects/{project_id}/indexes/status`，检查新代已追平、审计通过且处于 ready。跨时间写入另要求 `source_contexts_v1`、`entity_source_contexts_v1`、`relation_source_contexts_v1`、`entity_fact_contexts_v1` 能力齐备，并通过当前来源诊断。旧索引、构建中断、未追平、审计失败或不确定写入均不能启用跨时间归并。

最后验证目标项目：关键词/语义/混合检索命中段落尾部；时间/平台组合不能拼接不同来源的范围；撤回来源后不再引用该来源；图谱与脑图均不出现属性节点/线；侧栏和统一审核能分页读取证据。旧文档若要提取属性，仍需显式选中后重处理。

## 中断、恢复与回退

回填可从保存的 `after` 续跑；索引失败从同一个项目任务入口重试，索引状态和回执决定是否续跑或重新捕获快照。不要手动把 failed 改为 ready，也不要修改能力字段绕过门槛。

发布未验收时保持业务写入暂停。共享集合仍可能含其他项目和其他索引代，不得全删。需要回退应用版本时采用匹配的数据库备份/配置和对应版本的正规索引恢复或重建流程；旧应用不能直接运行在不匹配的 schema 上。旧索引能力缺失时，跨时间身份写入继续关闭，不能为了回退而伪造已验证状态。

已经确认的实体融合可从既有可撤销操作记录执行反向操作；这与部署回退是两件事。不要直接删除合并台账或来源事件来“撤销”。


## 验收记录

最终后端关键回归两组分别 214、153 项通过；前端 1139 项通过；真实 Edge 桌面/手机浏览器 32 项通过。保留 1 项既有严格预期失败：同一答案在 READ COMMITTED 下两次读取并发变化的摄取运行状态可能不同（`test_same_answer_captures_consistent_frozen_ingest_state`），未将其宣称为本次已修复。具体命令、日志和审查结论见同日实施计划的 Final execution evidence。

现有业务项目目前没有已接受的实体属性事实；侧栏显示空状态属于正确结果。已加载 TTL 的属性定义会指导后续提取；旧文档需开启属性提取后显式重处理，新值经审核后才参与展示和检索。
