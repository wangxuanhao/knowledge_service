# 一等属性事实设计（当前 master 落地版）

## 目标

把当前实体 `properties` 中的业务属性改为 `kind=attribute` 的正式记录，复用现有 `record_versions`、`assertions`、`assertion_events`、`record_version_assertions` 和 `FormalFactWriter`。

本次只补齐当前项目已存在但不完整的属性能力：属性独立审核、版本、来源、冲突、撤回、合并、投影和展示。不新增另一套存储，不把 Semantica 变成真相源，也不做通用属性平台。

## 数据模型

```json
{
  "id": "reviewattr_<candidate_id>",
  "kind": "attribute",
  "subject_id": "entity_id",
  "type": "https://example.org/employeeCount",
  "value": 20,
  "datatype": "http://www.w3.org/2001/XMLSchema#integer",
  "text": "测试商户 · 员工数 = 20",
  "ontology_id": "ontology_id",
  "valid_from": null,
  "valid_until": null,
  "metadata": {}
}
```

约束：

- `value` 首期只允许字符串、布尔值、整数和有限浮点数；
- 一条记录只保存一个值，多值使用多条属性记录；
- `subject_id` 必须指向同项目当前存在的实体；
- `type` 必须是当前本体中的 `owl:DatatypeProperty`，domain、range 和有效时间继续由现有 `Ontology` 校验；
- 不增加单位换算、复杂对象值或新的属性主表。

属性事实键由 `subject_id + type + value + datatype + valid_from + valid_until` 规范化计算，继续存入现有 `fact_keys`。同一事实键复用规范记录并增加来源断言。

## 单值、多值和冲突

不增加自定义的 `valueMode` 配置：

- 本体 SHACL 声明 `sh:maxCount 1` 时，同一实体、同一属性、有效时间重叠且值不同，新的候选断言进入 `contradicting`；
- 未声明单值约束时，不同值作为正常多值保存；
- 相同值复用已有事实，只增加来源支撑；
- 审核员可保留旧值、接受新值或拒绝候选；接受新值时旧值进入 `superseded`，全过程复用现有 assertion 状态机和事件账本；
- 任何入口都不能用字典赋值静默覆盖旧值。

## 写入路径

唯一正式写入路径保持为：

`RecordWrite -> KnowledgeService.write -> FormalFactWriter -> record_versions/assertions`

具体改动：

- `RecordWrite.kind`、`Scope.kinds` 和仓储校验增加 `attribute`，`RecordWrite` 同时增加并校验 `value`、`datatype`；
- `FormalFactWriter` 增加属性事实键、自动来源断言、版本支撑和来源撤回；
- 属性批准不再修改实体 `properties`，因此不会为了一个属性创建整个实体的新版本；
- 旧实体上已有的 `properties` 暂时只保留兼容读取，不做大规模历史迁移；新的实体写入若携带非空标量 `properties`，由 `KnowledgeService.write` 在同一批次转换为 attribute 记录，并以空 `properties` 保存实体，避免继续产生第二条权威写入路径；非标量值直接拒绝。

## 审核与本体发现

受控抽取审核：

- 通过属性候选时创建正式 attribute 记录；
- 相同事实把候选 assertion 接到已有记录版本；
- 单值冲突把候选标记为 `contradicting`，由审核动作决定保留旧值或接受新值；接受新值时，把冲突旧事实的全部 accepted 支撑 assertion 转为 `superseded`，最后支撑消失后为旧属性记录写 tombstone，再接受新事实；
- 审核原因、actor、证据和 assertion event 继续使用现有账本。

开放本体发现：

- 每个属性候选物化为独立 attribute 记录，不再写入实体 `properties` 字典；
- 多条同名属性不会互相覆盖；
- 统计直接按 attribute 记录计数；
- Semantica 继续承担候选抽取和本体归纳。传给本体归纳器的属性同时展开为稳定的顶层字段，避免 Semantica 忽略嵌套 `properties`；
- Semantica 不直接写项目数据库，也不决定 accepted/contradicting 状态。

## 关联模块

- 实体合并：把被合并实体的 attribute `subject_id` 改到保留实体；相同事实去重，不同值按 SHACL 单值约束进入冲突，否则保留多值。
- 来源撤回：撤回该来源的 attribute assertion；最后一条 accepted 支撑消失后，为属性记录写 tombstone 版本。
- RDF：正式 attribute 输出为带 datatype 的 literal；兼容 `properties` 仍可读取，但正式属性优先，避免重复三元组。
- Neo4j：正式属性投影为 `(Entity)-[:KS_ATTRIBUTE]->(AttributeFact)`，仍是 SQLite 的可重建投影。

## 页面展示与图谱

- 实体详情以属性表展示：属性名、一个或多个值、有效时间、来源数和冲突状态；
- `/subgraph` 增加 `attribute_mode=none|summary|expanded`；
- 默认返回 summary，属性保留在实体详情中，不把整个画布铺满；
- 用户选中实体时才请求 expanded，并为该实体生成属性值节点和 `KS_ATTRIBUTE` 边；
- Explorer 的属性节点是查询结果中的展示节点，不另行持久化。

## 明确不做

- 不自建 single/set/temporal 等四套属性模式；
- 不做单位换算和复杂对象属性；
- 不增加属性专用数据库或独立向量通道；
- 不做复杂快照治理和全量历史迁移；
- 不用 Semantica 替代 SQLite 账本；
- 不创建独立测试脚本或大而全的测试矩阵。

## 验证

只在现有相关测试文件补最少的直接断言，并运行现有的审核、本体发现、正式写入、实体合并、来源撤回、RDF、Neo4j、Explorer 与前端测试。验证重点是：属性成为正式记录、同值复用、不同值不覆盖、单值冲突可审核、撤回与合并不丢账、详情和图谱展示一致。
