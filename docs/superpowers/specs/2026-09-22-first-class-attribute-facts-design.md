# 一等属性事实设计

## 目标

把实体业务属性从实体记录内的 `properties` 字典提升为正式、可版本化的知识事实，并复用现有 `record_versions`、`assertions`、`assertion_events`、`record_version_assertions` 与 `FormalFactWriter`。

完成后，系统必须能够：

- 为同一实体保存单值、多值和随时间变化的属性；
- 让每个属性值拥有独立的本体类型、有效时间、系统版本、来源断言和审核记录；
- 对相同值聚合多个来源，对冲突值进入显式审核，绝不静默覆盖；
- 在受控抽取、本体发现、手工写入、实体合并、来源撤回、快照恢复和投影中使用同一套属性语义；
- 默认以紧凑属性表展示多值，只在用户选择实体时按需展开属性节点；
- 保持 SQLite 为唯一真相源，Semantica、Neo4j、Milvus 和 RDF 都是计算或派生能力。

## 当前基线与问题

`master` 提交 `2f72925` 中，正式记录只支持 `document`、`entity`、`relation`、`chunk`。属性作为实体的 `properties: dict` 保存。

当前有两条属性进入路径：

1. 受控抽取把属性候选写入 pending assertion；审核通过后修订实体记录，把值写入 `entity.properties`，并把审核摘要追加到 `metadata.attribute_reviews`。
2. 开放发现发布把候选属性折叠进待物化实体的 `properties` 字典，再写入实体记录。

这造成以下结构性问题：

- 同一个属性名称只能占一个字典键，开放发现的后值会覆盖前值；
- 受控审核遇到不同值时只报错，没有可持续处理的冲突状态；
- 属性值无法独立修订、撤回、引用、检索或重定向；
- 一个实体版本承载所有属性变化，小属性变化会制造整实体的新版本；
- Neo4j 和 Explorer 只认识实体/关系，属性只存在于 JSON 载荷；
- 实体合并通过字典优先级合并属性，不能正确表达多来源和冲突；
- 本体发现先提交本体、再分批写实例，发布任务可能形成部分物化状态。

## 方案选择

### 方案 A：继续增强 `entity.properties`

可以把标量改为数组并在 metadata 中保存每个值的来源。改动小，但版本、有效时间、冲突、来源撤回和实体合并都仍需在嵌套 JSON 中重复实现。接口看似简单，复杂度会扩散到每个调用方。拒绝。

### 方案 B：一等属性事实，复用现有账本（采用）

属性成为 `kind=attribute` 的正式记录。每个规范属性值由独立 record version 表达；每次来源出现由 assertion 表达；接受事件通过现有 `record_version_assertions` 精确支撑对应版本。复用当前双时态、审核状态、乐观锁和正式写入事务。

该方案让复杂语义集中在一个属性策略 Module 和现有正式写入 Module 后面，调用方只提交候选或审核决定，获得最大的 Leverage 与 Locality。

### 方案 C：Semantica 存储作为属性真相源

Semantica 的 graph store、triplet store、provenance、version storage 和 conflict 模块相互独立，不能替代当前项目级 SQLite 事务、乐观锁、审核状态和双时态版本。可用于建议和派生投影，不作为真相源。拒绝。

## 领域模型

### 规范属性事实

每条正式属性记录表达：某实体在一个业务有效区间内具有一个经过类型化的值。

```json
{
  "id": "fact_<sha256>",
  "kind": "attribute",
  "subject_id": "entity_123",
  "type": "https://example.org/registeredCapital",
  "value": 100,
  "datatype": "http://www.w3.org/2001/XMLSchema#decimal",
  "unit": "万元",
  "language": null,
  "text": "测试公司 · 注册资本 = 100 万元",
  "ontology_id": "ontology_123",
  "valid_from": "2025-01-01T00:00:00Z",
  "valid_until": null,
  "metadata": {}
}
```

首期 `value` 只允许严格的字符串、布尔值、整数或有限浮点数。数组、对象、二进制、地理结构和自动单位换算不在首期范围内。多值用多条属性事实表达，不能把数组当成一个值。

`text` 是确定性生成的检索/展示文本，不参与事实身份。原始词面、模型置信度、精确证据和规范化说明属于 assertion payload 或 metadata，不参与规范值本身。

### 属性事实身份

`canonical_attribute_key` 由以下字段的规范 JSON 计算 SHA-256：

- `subject_id`
- 属性完整 IRI `type`
- 类型化 `value`
- `datatype`
- 规范化的 `unit`
- 规范化的 `language`
- `valid_from`
- `valid_until`

相同事实键复用已有规范记录，并把新来源连接为另一条 accepted assertion。不同事实键永远不能因为文本相似而自动合并。

### 冲突分组

冲突分组以 `subject_id + type` 为基础；时间和本体策略决定两条不同值是否冲突。单位不同且系统没有显式换算规则时不做数值猜测，进入待审核状态。

### 属性取值模式

本体属性增加项目注解 `ks:valueMode`：

- `single`：一个实体的该属性只允许存在一个已接受值；新值必须显式替代、拒绝或改变本体策略。
- `temporal_single`：同一业务时点只允许一个已接受值；不重叠区间可以并存。
- `set`：同一业务时点允许多个不同值。
- `unknown`：没有足够语义决定基数。首个值可以正常审核；第二个不同值进入冲突审核。

未声明时按 `unknown` 处理，绝不把 OWL 开放世界下“没有 maxCount”误解为“允许多值”。`single` 和 `temporal_single` 同时生成/维护 `sh:maxCount 1` 的 SHACL property shape；两者跨历史区间的差异由属性策略 Module 执行。

本体属性还可以声明 `ks:unit`，但首期只校验和展示单位，不提供通用单位换算引擎。

## Module、Interface 与 Seam

### 属性策略 Module

新增 `knowledge_service/services/attribute_facts.py`，把值规范化、事实键、时间重叠、取值模式和冲突分类集中在一个深 Module 中。

其外部 Interface 只有一个批量规划操作：

```python
plan_attribute_facts(
    ontology,
    entities,
    existing_facts,
    proposals,
) -> AttributePlan
```

`AttributePlan` 返回：

- 可创建或复用的规范属性记录；
- 每个候选对应的事实目标；
- 必须保持 pending/contradicting 的候选及机器可读原因；
- 需要显式预期版本的记录；
- 校验错误。

调用方不自行比较属性值，不自行判断时间冲突，不自行生成事实 ID。该 Seam 同时服务受控审核、开放发现发布、手工写入和旧数据迁移。

### 正式写入 Module

`KnowledgeService.write` 和 `FormalFactWriter` 仍是正式事实写入的唯一事务 Interface。扩展后的实现负责：

- 接受 `kind=attribute`；
- 使用属性事实键做幂等映射；
- 原子提交 record version、assertion、assertion event 和支撑映射；
- 撤回最后一个 accepted 支撑时失效属性事实；
- 在实体合并时重定向属性 subject，并处理事实键碰撞；
- 在发现发布时把本体版本、草案终态、实体、关系、属性和断言作为一个事务提交。

属性策略 Module 不直接写数据库；FormalFactWriter 不重新实现属性语义。二者之间以 `AttributePlan` 为内部 Seam。

### Semantica Adapter

现有 `integrations/semantica_adapter.py` 保持 Adapter 角色：

- 使用 Semantica provider 产生属性候选；
- 为 OntologyGenerator 提供它能正确理解的扁平、类型化属性输入；
- 可调用 Semantica conflict 模块给出可信度、时间、投票等建议；
- 把建议转换为项目自己的机器可读 review hint。

Semantica 不能直接创建、接受、拒绝、覆盖或撤回正式属性事实。所有建议最终通过项目 assertion 状态机和 FormalFactWriter 提交。

## 存储与迁移

### 记录存储

继续使用 `record_versions` JSON payload，不新增平行的 attribute facts 主表。现有版本表已经提供项目隔离、业务有效期、系统时间、历史版本和乐观锁。

迁移新增针对当前属性记录的表达式索引，至少支持：

- 项目 + kind + subject_id + type；
- 项目 + kind + subject_id；
- 项目 + kind + ontology_id。

索引只优化读取，不改变权威数据形态。

### 事实键

复用 `fact_keys` 表，并让它同时保存关系键与属性键。事实键前缀区分 `relation` 与 `attribute`。同项目内当前键唯一。

### 旧 `properties` 回填

迁移按当前活跃实体的每个 `properties` 项生成属性事实：

1. 用实体 `ontology_id` 解析属性 IRI、datatype 和本体策略；
2. 标量生成一条事实，数组的每个元素生成一条事实；
3. 创建 actor=`legacy-property-backfill` 的 accepted assertion；
4. quote 明确标记为“历史属性包回填”，不伪造原文证据；
5. 无法解析的属性保留在 legacy properties，不猜测 IRI，并输出迁移诊断；
6. 回填幂等，重复运行不得创建新事实或新 assertion。

历史实体版本保持不可变。新写入不再修改 `entity.properties`。

### 兼容读取

新接口以 `attributes[]` 为权威展示模型。兼容层为实体派生 `properties`：

- 一个当前 accepted 值：返回标量；
- 多个当前 accepted 值：按稳定排序返回数组；
- 无值：不生成该键；
- 冲突但未接受的值：不混入 `properties`，只出现在 `attributes[].conflicts`。

兼容 `properties` 是读取投影，不能再作为新写入入口。修订接口收到 entity properties 变化时返回明确错误，提示使用属性事实写入。

## 写入和审核数据流

### 受控抽取

1. Semantica Adapter 产生属性候选和精确证据。
2. ingest 在同一完成事务中创建 pending attribute assertion；document metadata 中的 `review_candidates` 暂时保留为兼容读取缓存，但 assertion 是权威状态。
3. 审核批准时，属性策略 Module 校验实体、本体、datatype、单位、取值模式和当前事实。
4. 相同事实键：把 assertion 接受并连接到已有属性 record version。
5. 无冲突的新事实：创建属性记录，接受 assertion，并建立精确版本支撑映射。
6. 不同值冲突：assertion 转为 `contradicting`，不修改当前属性事实。
7. 拒绝：assertion 转为 `rejected`，不创建属性事实。

### 开放本体发现

开放发现不再把属性写进实体字典：

1. 草案审核先决定类型、映射、datatype、domain、`valueMode` 和候选去留。
2. 实体候选先规划稳定实体 ID；属性候选引用这些实体 ID 并逐条进入属性策略 Module。
3. 多个同名属性候选全部保留为独立 assertion，不发生字典覆盖。
4. 发布计划在写事务前完成全部校验。
5. 单个事务提交本体版本、草案 published 状态、实体/关系/属性记录、assertion 决策和版本支撑映射。
6. 任一正式写入失败时，本体和草案状态也回滚；不能留下“本体已发布、实例只写了一半”的状态。

草案终态统计使用 `mapped_entities`、`mapped_relations`、`mapped_attribute_facts` 和 `mapped_attribute_assertions`，不再以实体属性键数量估算。

### 手工属性写入

实体详情页通过专用命令提交属性候选，而不是编辑整份实体 JSON。命令必须包含实体期望版本或当前属性事实期望版本，防止审核期间的并发覆盖。手工写入也创建 assertion，actor 和 reason 必填。

## 冲突决策

属性冲突复用 assertion 状态：`pending`、`accepted`、`rejected`、`contradicting`、`superseded`。

审核界面提供以下显式动作：

- **保留当前值**：新 assertion 转为 rejected。
- **接受新值并替代旧值**：新事实 accepted；旧事实的支撑 assertion 保留历史，旧事实建立新版本并按决定的业务时间失效。
- **两个值都保留**：只允许本体已是 `set`，或同一事务中先批准 `valueMode=set` 的本体变更。
- **调整有效时间后接受**：只允许 `temporal_single`，且调整后区间不重叠。
- **保持冲突**：assertion 保持 contradicting，不改变正式事实。

每个决定需要 reason、actor、expected decision version，并产生 assertion event。Semantica 建议只作为提示显示，不能预选危险动作。

## 本体维护

属性术语的结构化维护表单增加：

- datatype；
- 适用实体 domain；
- `valueMode`；
- 可选单位；
- 说明和标签。

本体变更影响分析必须统计：

- 使用该属性的当前事实数和历史版本数；
- 受影响 assertion 数；
- 变更后将产生的 datatype、domain 或 cardinality 冲突；
- 待审核候选数。

降低兼容性（例如 `set → single`、datatype 变化）属于高风险变更，必须确认影响，并把不再满足约束的事实转入整改/冲突队列，不能静默删除。

## 实体合并、撤回与恢复

### 实体合并

合并实体时，所有属性事实的 `subject_id` 从被合并实体重定向到保留实体：

- 新事实键与已有键相同：保留一个规范事实，所有 accepted assertion 重指向保留事实的精确版本；
- `set` 下不同值：并列保留；
- `single`、`temporal_single` 或 `unknown` 下不同值：事实不丢失，相应 assertion 转入 contradicting，生成审核项；
- 合并事务与关系重写、属性重写和 merge ledger 原子提交。

Semantica EntityMerger 继续提供合并建议，但不能通过字典优先级决定最终属性。

### 来源撤回

文档撤回后，其 attribute assertions 转为 superseded。若属性事实仍有 accepted 支撑则保持；失去最后一个 accepted 支撑时，属性事实生成 tombstone 版本并从当前读取投影中消失。

### 快照恢复

快照包含属性事实及其 record versions。恢复时走 FormalFactWriter，创建新系统版本，不抹除恢复后的历史。兼容 `properties` 由恢复后的属性事实重新派生。

## 查询、检索与 RDF

### 结构化查询

增加按 `subject_id`、属性 IRI、typed value、业务时点和系统时点查询属性事实的读取 Interface。比较操作必须基于 typed value，不把数值转成字符串比较。

### 检索

属性加入独立 `attribute` 内容通道。检索文本为确定性的“实体显示名 + 属性标签 + 格式化值”。FTS 和 Milvus 可重建，不保存额外真值。

数值范围查询走结构化查询，不依赖向量相似度。问答检索可以配置属性 quota，返回属性事实时沿现有 provenance 链追到 accepted assertion 和原文。

### RDF/SPARQL

`Ontology.dataset()` 不再只读取 `entity.properties`。它把当前可见属性事实投影为：

```text
entity IRI -- attribute predicate --> typed RDF Literal
```

多值自然形成多个三元组。datatype、language 和首期支持的字面量类型必须保留。legacy properties 只在尚未回填成功时作为兼容输入，避免与属性事实重复生成三元组。

## Neo4j 投影

SQLite 仍是权威源。Neo4j 使用事实节点保留版本和 provenance 所需身份：

```text
(Entity:KSRecord)-[:KS_ATTRIBUTE]->(AttributeFact:KSRecord)
```

`AttributeFact` 节点保存 id、type、typed value、datatype、unit、有效期、版本和 payload；`KS_ATTRIBUTE` 保存 namespace、project_id、fact key 和属性 IRI。值不伪装成实体节点。

同步核验新增 attributes 组，比较节点数量、边数量、缺失、额外、变化、重复和错误 subject。投影仍可由 SQLite 全量重建。

## Explorer 与前端展示

### 默认展示

图谱默认只显示实体和实体间关系，防止属性节点造成视觉爆炸。实体详情显示结构化属性表：

- 属性标签；
- 一个或多个值 chip；
- datatype / unit；
- 有效期；
- accepted 来源数量；
- 冲突数量和状态；
- 查看证据、历史和审核决定入口。

### 按需展开

`/subgraph` 增加 `attribute_mode`：

- `none`：不返回属性，兼容当前行为；
- `summary`：在实体节点上返回紧凑 attributes 摘要；
- `expanded`：仅对选中的中心实体返回虚拟属性值节点和属性边。

展开节点使用 `node_kind=attribute_value`，ID 由属性 fact ID 派生；边使用 `edge_kind=attribute`。虚拟节点不写回数据库。前端以不同形状和低饱和颜色区分属性值，冲突值使用状态描边，不依赖颜色作为唯一提示。

全图请求不能默认 `expanded`。服务端和前端都设置属性展开数量上限；超过上限时返回 `truncated=true` 和总数，而不是静默遗漏。

## 兼容性与接口版本

- 现有实体、关系、文档和 chunk 写入保持兼容。
- `Scope.kinds`、检索 channel 和导出格式追加 `attribute`，不改变已有枚举含义。
- 实体响应暂时继续包含派生 `properties`，同时新增 `attributes`。
- 旧前端仍可显示 JSON；新前端只使用 `attributes` 编辑和审核。
- 导出包含一等属性记录；导入旧 property bag 时走确定性回填。
- `properties` 的写兼容只保留在 legacy import 和迁移 Adapter 内，公开写接口禁止新增嵌套属性。

## 错误处理与不变式

必须保持以下不变式：

1. attribute 的 `subject_id` 必须指向同项目、当前存在的 entity。
2. 属性有效区间必须位于实体有效区间内。
3. `type` 必须解析为目标 ontology 中的 `owl:DatatypeProperty`。
4. value 必须满足属性 range；domain 必须接受实体类型。
5. 同一事实键只对应一个当前规范属性记录。
6. accepted assertion 必须指向存在的规范记录和精确 record version。
7. 冲突 assertion 不能出现在实体派生 `properties` 或 RDF 当前事实中。
8. 发现发布要么全部提交，要么本体、草案和正式事实全部不变。
9. Neo4j、Milvus 或 Semantica 失败不能回滚已提交的 SQLite 真值；投影通过重建恢复。
10. 任何自动化流程都不能用最后写入覆盖不同属性值。

错误返回稳定机器码与中文消息，至少覆盖：`attribute_subject_missing`、`attribute_type_unknown`、`attribute_domain_mismatch`、`attribute_datatype_mismatch`、`attribute_unit_unsupported`、`attribute_cardinality_conflict`、`attribute_version_conflict`。

## 测试策略

### 属性策略 Module

- 严格标量类型和有限浮点校验；
- 稳定事实键、Unicode 和 JSON 规范化；
- same-value 复用；
- single、temporal_single、set、unknown 的冲突矩阵；
- 时间边界采用 `[valid_from, valid_until)`；
- datatype、domain、单位错误；
- 不支持的单位不进行猜测转换。

### Repository / FormalFactWriter

- 属性记录版本、事实键、assertion event 和版本支撑原子提交；
- 重放幂等；
- 乐观锁失败完整回滚；
- 来源撤回保留多来源事实、撤回最后来源后 tombstone；
- 实体合并后的属性键碰撞、断言重指派和冲突状态；
- 从旧 property bag 幂等回填；
- 发现发布跨本体、草案和事实的事务回滚。

### Service / API

- 受控属性批准不再创建实体新版本；
- 相同值增加支撑来源；
- 不同值按模式进入 accepted 或 contradicting；
- 手工属性写入需要 reason 和 expected version；
- 兼容 `properties` 的标量/数组投影稳定；
- typed value 查询和业务/系统时点过滤；
- provenance 可追到属性 assertion 和原文。

### Semantica Adapter

- 属性抽取坏项隔离和精确证据定位保持现状；
- 本体归纳输入能识别属性，而不是因嵌套 properties 丢失；
- conflict 建议失败不会改变 assertion 状态；
- 未安装 Semantica 时正式属性读写仍可运行。

### Neo4j / RDF / Retrieval

- 属性事实节点和 KS_ATTRIBUTE 边可重建并核验；
- 多值属性形成多个 typed RDF literal；
- FTS/Milvus 只索引当前 accepted 属性；
- 属性 tombstone 会删除派生索引；
- 数值范围查询不走字符串或向量比较。

### 前端

- 单值、多值、历史值和冲突值按结构化表展示；
- 默认图不出现属性节点；
- 只为选中实体展开属性节点；
- 展开上限、截断提示、键盘操作和非颜色状态提示；
- 属性审核动作携带 reason、actor 和 expected version；
- 旧实体 properties 响应仍能显示。

## 发布顺序

1. 增加属性记录模型、属性策略 Module、事实键和正式写入支持；保持旧 UI。
2. 执行 property bag 回填并提供兼容读取投影。
3. 把受控属性审核迁移到一等事实。
4. 把开放发现发布改为原子的一等属性物化。
5. 补齐实体合并、来源撤回、快照恢复和 provenance。
6. 增加结构化属性查询、RDF、Milvus 和 Neo4j 投影。
7. 上线属性表格、多值冲突审核和按需属性节点展开。
8. 观测一个版本周期后，移除公开接口对 entity properties 写入的兼容路径。

每一步都必须产生可运行、可测试的中间状态；不能要求一次性切换所有读取和展示路径。

## 首期边界

首期不会：

- 把 Semantica 或 Neo4j 提升为真相源；
- 实现通用单位换算或量纲推理；
- 支持数组/对象型属性值；
- 自动解决不同来源的冲突值；
- 默认把全部属性渲染成图节点；
- 推测性补造历史原文证据；
- 重构与属性事实无关的项目模块。
