# 开放抽取本体术语冲突治理设计

## 背景

开放抽取已经将关系和属性放在同一次事实抽取中判断，但实体类型提取仍与关系/属性提取分离，发现归纳又对三种术语使用相同的名称到 IRI 映射。一个规范名称因此可能同时成为 `owl:Class`、`owl:ObjectProperty` 或 `owl:DatatypeProperty`。

`test_0928_开放` 的累计草案生成暴露了该问题：同一个 IRI 先被登记为 class，随后又收到 attribute 的 domain 操作，最终触发“domain/range 只能用于 relation/attribute（关系/属性）术语”。这不是已发布历史本体污染，而是候选治理和构建器不变量之间缺少冲突处理。失败路径还会先保存空草案，再编译初始操作。

本设计选择分层治理方案：提取时减少冲突、草案前确定性归一化与隔离、构建器最终保护。来源证据不因术语冲突而删除，无冲突候选可以继续形成累计草案。

## 目标

- 实体类型、关系和属性的候选规范名称在同一项目中跨种类唯一；
- 已发布和已停用正式术语的稳定 IRI 不被其他种类候选复用；
- 已知候选冲突被隔离为可审计的发现冲突，不导致累计草案接口失败；
- 无冲突候选继续生成原子操作、接受审核、校验和发布；
- 草案和初始操作原子创建，失败不留下空草案；
- 保持 Semantica 0.6.7 的 OWL 归纳职责和现有 pySHACL 校验链路；
- 为后续工作台冲突处理保留稳定的数据结构和诊断代码。

## 范围与非目标

本次覆盖：

- 开放实体、关系、属性提取的名称约束和确定性后处理；
- 累计发现候选的归一化、合并和跨类型冲突隔离；
- Semantica 归纳输入与输出的术语种类安全检查；
- 发现草案生成、诊断展示、原子性和回归测试；
- 当前工作台能够看到冲突数量、名称、涉及种类、证据及被隔离原因。

本次不覆盖：

- 不实现 SWRL 规则模型、规则抽取或推理引擎；
- 不新增完整 SHACL Shape 可视化编辑器，继续使用现有 pySHACL、发布校验和受控 SHACL RDF patch；
- 不实现全部 OWL 语言或通用语义消歧模型；
- 不自动重写已有正式术语 IRI、历史知识或历史本体版本；
- 不通过为三种术语添加 kind 路径前缀来隐藏重名；
- 不允许 LLM 自动决定关系与属性的跨事实语义冲突。

## 术语和语义层次

系统必须区分：

| 对象 | 含义 | 正式表示 |
|---|---|---|
| 实体实例 | 原文中的具体对象 | 知识记录 |
| 实体类型 | 实体实例所属概念 | `owl:Class` |
| 关系事实/术语 | 实体到实体的事实及其谓词 | `owl:ObjectProperty` |
| 属性事实/术语 | 实体到标量值的事实及其谓词 | `owl:DatatypeProperty` |
| SHACL Shape | 对知识记录的可执行约束 | `sh:NodeShape` / `sh:PropertyShape` |

OWL domain/range 在 RDF/OWL 中具有推理语义。产品现有“允许端点类型 OR 集合”的写入契约继续保留；实例合法性仍由现有显式校验与 SHACL 执行。本次不改变该兼容语义，只确保三种术语身份不混用。

## 核心不变量

1. 一个 IRI 在所有已发布声明中最多只能属于一种术语种类：class、relation 或 attribute；停用不释放 IRI 或 kind。
2. 同一项目内，正式术语和待归纳候选使用同一套规范名称规则。
3. 不同术语种类不得共享同一规范名称。
4. 同种类、同规范名称的候选合并证据和出现次数，不重复建术语。
5. 已发布或已停用正式术语的规范名称和 IRI 均被保留；停用术语不能被自动复用或隐式恢复，新的不同种类候选也不得复用。
6. 冲突只隔离术语提案，不删除来源候选事实、文档证据或诊断。
7. 被隔离候选不生成 `create_term`、domain、range、datatype 或父类操作。
8. 已知候选冲突不能使整个累计草案失败；意外的无效 RDF 必须产生受控错误并完整回滚。

## 规范名称

新增单一名称规范化函数，供开放提取后处理、发现归纳、基线本体索引和构建器校验复用。首期规则固定为：

1. Unicode NFKC 规范化；
2. 去除首尾空白；
3. 连续空白折叠为一个普通空格；
4. 对有大小写的文字使用 Unicode case-fold；
5. 不自动删除标点、不做分词、不做同义词合并。

显示标签和多语言标签可保留原文；规范名称只用于项目内的候选合并和冲突检测。首期不允许不同种类使用相同显示标签后再靠 IRI 消歧，因为当前解析、映射和工作台尚不能安全表达这种歧义。

基线本体建立名称注册表时，为每个正式术语索引以下规范化别名：

- IRI 的可读 local name；
- 全部 `rdfs:label` 值，不区分语言标签；
- 当前 summary 中作为 `name` 暴露的稳定本地名称。

注册表值是 `(IRI, kind, retired, source)` 集合，不允许用后写值覆盖先写值。候选匹配优先级固定为：

1. 候选显式携带的 IRI 已在基线存在时，以该 IRI 的已发布 kind 为准；kind 不同立即冲突；
2. 显式 IRI 不存在或候选没有 IRI 时，使用规范名称查询注册表；
3. 名称只匹配一个活动、同 kind 正式术语时复用该 IRI；
4. 名称只匹配一个已停用、同 kind 正式术语时隔离为 `retired_term_reuse_blocked`，只建议走人工 restore 草案；
5. 名称匹配不同 kind、多个 IRI 或活动/停用混合结果时隔离为 `ambiguous_baseline_name` 或 `existing_term_kind_collision`，不得任选一个；
6. 显式新 IRI 的规范名称又匹配另一个正式 IRI 时隔离为 `candidate_iri_name_mismatch`；
7. 完全没有匹配时才允许创建新术语。

如果基线中同一 IRI 已声明多个 kind，基线本身不满足结构不变量。发现请求返回受控 422 `invalid_baseline_dual_kind`，不尝试通过候选优先级修复；其他跨 IRI 的重名只使命中该名称的候选隔离，不影响无关候选。

## 分层处理

### 第一层：开放提取减少冲突

开放抽取顺序保持“实体先于事实”：

1. 提取实体实例和建议实体类型；
2. 归一化实体类型名称，形成当前批次保留名称集合；
3. 将已发布/已停用正式实体类型规范名称加入保留集合；
4. 关系和属性继续在一次事实抽取中联合判断；
5. 提示词要求谓词不得复用保留的实体类型名称，并优先使用动词型关系名或字段型属性名；
6. 程序后处理再次检查，不依赖模型遵守提示词。

属性仍必须满足：主体已解析、值为标量、存在来源证据、表示主体的稳定字段、不是动作/禁止/责任/所属/分类/文档结构、值不等于主体。新属性至少出现两次才可归纳为新的 `owl:DatatypeProperty`；低频候选继续留在候选快照中。

关系/属性联合抽取只能避免“同一条事实”双重分类。跨文档或不同事实复用同一谓词名称的问题由第二层处理。

### 第二层：候选术语归一化与隔离

在累计候选进入 Semantica `_induce` 之前新增深模块 `DiscoveryVocabularyNormalizer`。所有发现草案调用方和测试只跨越以下 interface：

```python
class DiscoveryVocabularyNormalizer:
    def normalize(candidates, baseline_ontology) -> NormalizationResult:
        ...
```

`NormalizationResult` 包含：

```json
{
  "accepted_candidates": [],
  "conflicts": [],
  "diagnostics": {
    "accepted_count": 0,
    "merged_count": 0,
    "quarantined_count": 0,
    "low_frequency_attribute_count": 0
  }
}
```

每个冲突组至少包含：

```json
{
  "code": "entity_property_name_collision",
  "canonical_name": "用户行为",
  "candidate_refs": [],
  "kinds": ["class", "attribute"],
  "existing_term": null,
  "evidence_refs": [],
  "resolution": "quarantined",
  "suggestions": []
}
```

`suggestions` 只是展示提示，不得直接形成操作。首期冲突以隔离为终态：用户仍可发布无冲突操作；冲突候选保持未映射并可在后续重新发现或由人工草案以明确的新名称建模。

冲突决策矩阵：

| 场景 | 处理 |
|---|---|
| 同种类、同规范名称 | 合并候选、证据和频次 |
| 新候选与基线活动正式术语同种类同名，且唯一命中 | 复用正式 IRI |
| 新候选与已停用正式术语同种类同名 | 隔离，提示通过高风险 restore 草案恢复 |
| class 与 relation/attribute 候选同名 | class 候选继续归纳；property 候选隔离 |
| relation 与 attribute 候选同名 | 两种 property 候选均隔离 |
| 新候选与基线正式术语不同种类同名 | 正式术语不变；新候选隔离 |
| 多个输入候选显式携带同一 IRI 但 kind 不同 | 全部冲突输入隔离，并产生高严重度诊断 |
| 低频新属性 | 保留快照但不归纳，不记为跨类型冲突 |

分类顺序固定为：确定性无效过滤 → 显式 IRI/基线结构校验 → 跨 kind 名称冲突分组 → 同 kind 合并 → 对剩余新属性应用频次门槛。因此同时“低频且跨 kind 冲突”的属性只计入术语冲突，不重复计入低频暂缓，工作台统计互斥。

class 优先只适用于同一次开放发现中的词汇提案，理由是实体类型先被抽取并已作为事实谓词的保留名称；它不证明 class 语义永久正确。若属性或关系才是正确建模，用户必须在后续人工草案中使用明确名称，或退役/替代错误术语，不能原地改变 kind。

### 第三层：Semantica 输出和构建器保护

Semantica 只接收 `accepted_candidates`。生成结果返回后执行结构检查：

- 每个 IRI 的 class/relation/attribute 声明互斥；
- parent 只能引用 class；
- relation domain/range 只能引用 class；
- attribute domain 只能引用 class，datatype 必须是支持的 XSD 类型；
- relation 观察端点继续只作为证据，不自动固化严格 domain/range；
- 属性 domain/datatype 建议仍作为可审核操作。

本体操作编译器保留最终保护：若任何调用方绕过归一化，尝试对 class 应用 domain/range、改变已发布 kind 或生成双重类型声明，编译必须失败。此类意外失败返回结构化 422 诊断，不保存草案或部分操作。

## 发现运行快照

归一化结果不能只存在于 HTTP 响应或可选草案中。每次累计分析使用通用 artifact 持久化一个 `ontology_discovery_run`，作为候选、冲突和草案之间的审计载体。该 artifact 至少保存：

```json
{
  "id": "由 source_fingerprint 确定性派生",
  "project_id": "...",
  "base_ontology_id": "...",
  "source_fingerprint": "sha256:...",
  "normalizer_version": "v1",
  "generator_version": "semantica-0.6.7",
  "candidate_snapshot": [],
  "accepted_candidate_ids": [],
  "merged_groups": [],
  "conflicts": [],
  "mappings": {},
  "candidate_bindings": [],
  "diagnostics": {},
  "status": "analyzed|draft_created|published|finalized_no_change|stale_base|stale_source|closed",
  "unified_draft_id": null,
  "supersedes_run_id": null
}
```

`source_fingerprint` 由 project、base ontology id、排序后的候选 ID/文档版本/规范化有效载荷、归一化规则版本、Semantica 版本和影响生成结果的请求选项计算。run id 由 project id 与 fingerprint 确定性派生，并由 artifact 主键提供并发唯一性。

候选、冲突和 bindings 快照创建后不可改写。状态、关联 draft id 和终态结果可以通过带期望状态的 Repository 更新推进，但不能替换原始快照。刷新来源或 rebase 不修改旧 run，而是创建带 `supersedes_run_id` 的新 run；旧 run 保持审计可读。草案关闭只把 run 置为 `closed`，发布或无本体变化物化分别置为 `published` / `finalized_no_change`。

因此纯冲突、纯低频、纯复用和混合结果都有持久化载体。工作台刷新后从 discovery run 读取诊断，不依赖是否成功创建本体草案。

## 候选绑定与部分物化

归一化和操作编译必须为每个可接受候选保存 `candidate_binding`：

```json
{
  "candidate_id": "...",
  "target_iri": "...",
  "target_kind": "class|relation|attribute",
  "binding_kind": "existing|proposed",
  "required_operation_ids": [],
  "optional_operation_ids": []
}
```

- 复用唯一活动同 kind 正式术语时，`required_operation_ids=[]`，该绑定不需要制造 no-op 本体操作；
- 新 class/relation 的 `create_term` 是候选物化的必要操作；父级、relation domain/range 建议默认是可选操作；
- 新 attribute 的 `create_term` 和建立受支持 datatype 的操作是必要操作，domain 建议默认是可选操作；
- 编译器若发现某条结构边是目标术语合法存在的必要条件，必须显式放入 required 集合，不能靠发布时猜测。

发布或无变化终结时逐候选计算：

1. target IRI 在最终本体中存在、活动且 kind 正确；
2. 所有 required operations 均为最新有效版本并被批准、实际应用；
3. 候选事实通过最终本体和 SHACL 校验。

满足三项才物化。必要操作被拒绝、被替代或校验失败的候选进入 skipped，原因分别使用稳定代码 `required_operation_rejected`、`required_operation_superseded` 或 `ontology_validation_failed`。可选 parent/domain/range 操作被拒不影响术语本身及事实物化。

冲突候选在 discovery run 创建时即持久化 `ontology_term_conflict` 诊断；在该 run 达到 `published` 或 `finalized_no_change` 时，候选生命周期同步写入相同稳定跳过原因。它们永远不会因为另一个操作获批而被顺带物化。

如果归一化结果没有本体变更操作，但存在指向活动基线术语的有效 bindings，则不创建空本体草案，也不创建新本体版本。兼容的累计草案入口返回 `result_kind=mapping_only` 和 discovery run；显式终结动作在一个事务中按当前 ontology id 重新验证并物化这些候选，将 run 置为 `finalized_no_change`。如果既无操作也无可物化 binding，只返回持久化诊断 run，不提供提交/发布动作。

混合草案发布时，在同一个发布事务里应用批准操作，并只物化满足上述规则的 bindings；部分批准因此具有确定结果。

## 累计草案生成与原子性

累计草案生成顺序调整为：

```text
读取候选与基线本体
  -> 归一化、合并、隔离
  -> 使用 accepted candidates 执行 Semantica 归纳
  -> 校验生成 RDF 和操作
  -> 在一个 Repository 事务内创建 discovery run + 可选 draft + 初始 operations + bindings
  -> 返回草案预览
```

不得先持久化空草案再编译操作。编译在事务外完成，但持久化事务开始后必须重新读取当前 `base_ontology_id`、候选文档版本和候选有效载荷并重算 source fingerprint；任一项变化返回 409 `stale_base` / `stale_source`，不写入 run、draft 或 operation。任何归一化之外的异常均回滚全部写入。仅存在冲突、没有可接受候选时，接口返回持久化 discovery run 和冲突诊断；不创建无操作草案。

创建事务使用确定性 run id 作为幂等键：相同 project + source fingerprint 的并发请求只能插入一次。失败插入的一方读取已存在 run；若其 draft 已创建则返回同一 run/draft，若另一事务尚未完成则等待数据库事务结束后读取，不能另建重复活动草案。相同来源/base 的重复请求返回原结果；刷新/rebase 因 fingerprint 改变而创建新 run。

包含正常候选和冲突候选时：

- 创建包含正常操作的 editing 草案；
- 草案通过 discovery run id 引用不可变冲突、bindings 和诊断快照；来源上下文只保存引用和摘要，不复制第二份可变真值；
- 冲突候选不进入 operations；
- 草案可以继续提交、审核、验证和发布；
- 发布物化时冲突候选保持未映射，并保存 `ontology_term_conflict` 跳过原因；其他候选按 binding required/optional 操作规则决定。

draft 创建后的修改继续遵守现有 revision/idempotency 语义；首次创建由 discovery run fingerprint 提供幂等和并发约束。

## 工作台最小改动

本次不重做完整本体工作台，只保证发现流程可观察、可继续：

- 候选发现阶段显示“已接受 / 已合并 / 低频暂缓 / 术语冲突”数量；
- 冲突列表显示规范名称、涉及种类、已有正式术语、来源证据和隔离原因；
- 明确提示“冲突候选不会进入本草案，不影响其他术语审核”；
- 设计、审核和发布阶段只展示实际生成的 operations；
- 属性创建不得同时生成 attribute `add_range` 和 `set_datatype`；属性使用 domain + datatype；
- 关系使用 domain + range；实体类型使用 parent；
- 页面不提供把已发布术语直接改 kind 的入口。

完整的关系/属性检查器、SHACL Shape 可视化编辑和冲突重命名向导留在后续工作台增强中。

## OWL、SHACL 与 SWRL 边界

- OWL：本次治理 `owl:Class`、`owl:ObjectProperty`、`owl:DatatypeProperty`、`rdfs:subClassOf`、domain/range 和 XSD datatype 的身份与操作安全。
- SHACL：继续与 OWL 一起版本化；现有 pySHACL 校验、异常审核、shape 依赖检查和 scoped patch 均必须回归通过。术语冲突在 shape 生成前解决，SHACL 不承担候选名称消歧。
- SWRL：当前项目没有规则模型和执行引擎，本次不新增。开放抽取不得把自然语言规则自动发布为 SWRL。

## 错误与审核语义

发现诊断分为：

| 类型 | 示例 | 行为 |
|---|---|---|
| 确定性无效 | 证据不在原文、主体未解析、同主体冗余值 | 不进入候选术语，保留诊断 |
| 暂缓 | 低频属性、对象未解析、关系/属性单事实歧义 | 保留候选和证据，不生成术语操作 |
| 发现冲突 | 跨 kind 同名、与正式术语不同 kind 同名 | 隔离冲突组，其他候选继续 |
| 构建错误 | 双重 IRI 声明、非法引用、无效 datatype | 受控失败并回滚，不留空草案 |
| SHACL 违规 | 正式/待发布知识不满足 shape | 沿用现有 validation review |

发现冲突不是 SHACL 违规，也不是操作审核决定。它发生在操作生成之前；本次通过隔离完成处理。人工需要采用冲突词汇时，通过独立手工草案创建更明确的规范名称。

## 兼容与存量处理

- `test_0928_开放` 没有已发布本体；修复后重新生成累计草案即可，现有失败空草案应关闭或通过安全维护路径清理；
- 不自动修改其他项目已发布版本；
- 提供只读审计，列出一个项目内正式术语的跨 kind 规范名称冲突和双重 IRI 声明；
- 已存在冲突的正式本体必须通过人工草案执行“创建正确术语 + 替代标注 + 退役旧术语”，不得原地改 kind；
- 历史知识继续绑定原 ontology version，是否迁移是独立任务。

## 验证策略

### 名称归一化模块

- NFKC、空白折叠和 case-fold；
- 同种类候选合并并保留全部证据；
- class/attribute、class/relation、relation/attribute 冲突矩阵；
- 与活动、已停用基线术语的同种类复用和不同种类隔离；
- 同一显式 IRI 多 kind 的高严重度冲突；
- 低频属性不被误记为跨类型冲突。

### 开放提取

- 实体类型规范名称进入保留集合；
- 关系/属性谓词与实体类型重名时进入诊断而非事实双重分类；
- 关系对象实体解析、标量属性和现有异常代码不回归；
- 模型违反提示词时，程序后处理仍能隔离冲突。

### 发现与草案

- 重现 `用户行为` class/attribute 和 `属于` relation/attribute 冲突；
- 冲突候选不生成 OWL 声明或 domain/range 操作；
- 同批正常候选仍生成可审核草案；
- 全部候选冲突时返回诊断但不创建空草案；
- Semantica 输出双重 kind 时受控失败；
- 草案创建或操作写入失败时事务完整回滚；
- 重试不创建重复活动草案。

### OWL/SHACL 和工作台回归

- class、relation、attribute、父类、domain/range/datatype 现有测试通过；
- pySHACL 约束、时间区间验证、SHACL 异常审核和 scoped patch 测试通过；
- 发现页显示诊断数量和冲突证据；
- 属性编辑不再发送 attribute `add_range`；
- 从发现草案到审核、验证、发布的成功路径端到端通过。

## 完成标准

- `test_0928_开放` 的同类冲突输入不再触发 domain/range kind 异常；
- 候选冲突有稳定诊断，来源证据未丢失；
- 正常候选能够完成“发现 -> 累计草案 -> 审核 -> 验证 -> 发布”；
- 任何失败路径不产生新的空草案或部分操作；
- 一个生成或发布后的 IRI 不会同时成为 class、relation 和 attribute；
- 现有 SHACL 校验与审核能力无回归；
- SWRL 明确保持非目标，不以未实现能力宣称流程完整。
