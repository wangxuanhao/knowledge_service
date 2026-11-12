# 本体类型演进与实例迁移调研

日期：2026-11-12。范围：类型改名、合并、停用、删除，以及对实体实例、关系、约束和历史的影响。本文只记录调研与建议，不代表已批准实施。

后续状态：用户同日要求整合并优化，已据此增加实体类型合并预览/草案命令、停用替代选择和安全替代链。下文“当前行为/缺口”记录的是修改前基线；新能力与边界见 [类型纠正操作说明](../2026-11-12-类型纠正与合并操作说明.md)，设计见 `docs/superpowers/specs/2026-11-12-ontology-type-correction-design.md`。

## 结论

本项目已经实现术语停用、替代映射、依赖检查和发布后的受控重分类，并非完全没有类型迁移。缺口是面向用户的完整类型合并流程：尚无独立类型合并命令，停用界面不提供替代目标选择，也没有将本体引用改写与实例迁移组成一份统一演进计划。

“开放发现参考已有类型”只改善新知识进入系统时的对齐，不能解决已有类型如何演进、已有事实如何迁移的问题。两者需要分别治理，并共享稳定术语身份与替代映射。

## 开源项目证据

### ROBOT

- 官方 rename 文档明确说明：多个旧 IRI 映射到同一新 IRI 时，相关实体会合并。此处 OWL entity 包括类等术语，不能误解成只合并业务实体实例。
- rename 操作使用明确的 IRI 映射；它不会判断两个名称相近的类型是否应当等价。
- repair 支持把引用 deprecated class 的公理改为其 replacement class；默认不迁移 annotation axioms，可按属性显式启用。
- merge 命令是聚合本体文件或导入闭包，不是自动识别并合并近义概念。
- 这些操作的边界是输入/加载的本体；本项目独立 PostgreSQL 记录不会因处理一个 OWL 文件而自动变化。

来源：

- https://robot.obolibrary.org/rename.html
- https://robot.obolibrary.org/repair
- https://robot.obolibrary.org/merge

### Protégé / OWLAPI

- 官方源码确有 MergeEntitiesAction，调用 MergeEntitiesChangeListGenerator；不只是 Merge ontologies。
- 将源术语 IRI 引用改为目标，源标签转为目标别名；当前该 UI action 默认使用删除源术语策略。
- 使用 root ontology 的 imports closure 生成变化。已加载实例的分类断言可随 class IRI 改写，这是 OWLAPI 通用公理替换实现的源码推导，本次未运行 Java 实验。
- 不覆盖独立 PostgreSQL 实例表；导入本体的文件保存、远程写回和只读权限边界未实测。
- 不应照搬其删除源策略到本项目有证据和时间历史的业务数据中。

固定提交与逐条证据见 [独立源码复核](2026-11-12-ontology-refactoring-sources.md)。不要把 Protégé 的 Merge ontologies 与 Merge entities 混为一谈。

ROBOT repair 的替代属性是 OBO `IAO_0100001`，与本项目 `dcterms:isReplacedBy` 不同，集成时须显式适配。

### TerminusDB

- 区分不影响已有数据的兼容性变化，以及必须转换实例数据的破坏性变化。
- migration API 接收有序操作；顺序可能改变实例转换结果，支持 dry-run。
- MoveClass 会改类名及其关联实例数据的 URI；它不是只改显示标签。
- DeleteClass 迁移会删除该类的实例数据，但不会自动修正其他属性指向该类的 range；必须先处理这些引用。
- 普通 schema CRUD 删除与 migration 删除不同：普通删除要求先移除已有实例和引用，不能混称为同一行为。
- 版本化设计以 commit 保存 schema 与 data 的对应状态；官方推荐先增加替代项、标记弃用、迁移数据，最后再移除，并在分支检查后应用。
- 未由这些材料证实一个能自动判断概念等价并合并所有类语义的通用 MergeClass 功能。

来源：

- https://terminusdb.org/docs/schema-migration-reference-guide
- https://docs.terminusdb.org/docs/schema-crud-operations
- https://terminusdb.com/docs/ontology-versioning/
- https://terminusdb.org/blog/2023-04-24-schema-migration

### VocBench / Semantic Turkey

- 提供协作验证、历史、发布流程；待批准新增与删除通过待验证三元组表达，弃用另使用 owl:deprecated。
- Resource View 文档明确区分：删除指向资源的连接，并不删除被指向资源；替换一个资源值也不等于全库资源合并。
- 已查官方材料不足以确认一个会自动处理所有类实例及全部约束的类型合并事务。不能仅凭界面有 Replace with existing resource 就推断具备该功能。
- 官方版本比较页自述不是最新功能清单，适合作为治理模型证据，不适合作为当前版本全部功能的穷举。

来源：

- https://vocbench.uniroma2.it/doc/vb2_vs_vb3.jsf
- https://vocbench.uniroma2.it/doc/user/data_view.jsf
- https://vocbench.uniroma2.it/doc/

## 本项目当前行为

| 情况 | 当前代码行为 | 证据 |
|---|---|---|
| 草案编辑 | 变更草案中的操作和预览，不因此重写现有实例 | ontology_drafts_commands.py；reclassify.py 独立迁移入口 |
| 改 label/comment，IRI 不变 | 修改术语标注，不代表实例身份变化；仅标注发布可复用版本号，但发布快照仍有独立身份 | ontology_drafts_review.py:is_annotation_only；test_ontology_version_management.py |
| 改父类、domain/range、datatype、SHACL | 生成相应原子操作并校验；新本体不是自动对历史数据做转换 | ontology_operations.py；ontology_drafts.py:920-944 |
| 物理删除已发布术语 | Turtle diff 明确拒绝，要求 retire_term | ontology_operations.py:canonical_turtle_diff |
| 停用类型 A | 设置 owl:deprecated true，保留声明、标注及自身结构 | ontology_operations.py:_apply_operations |
| 停用并指定 A→B | 支持 dcterms:isReplacedBy；目标必须是其他活动且同种类术语 | ontology_operations.py:679 |
| 仍有活动子类或约束依赖 A | 检查子类、domain/range、SHACL 等依赖并阻止不合法停用；不会自动把所有引用改为 B | ontology_operations.py:retirement_dependencies |
| 历史记录不符合拟发布本体 | 在 historical_impact 中报告，作为 info；与新写入结构契约分开 | ontology_drafts.py:920-944 |
| 发布后迁移旧知识 | plan / preview / apply，逐组选择；有替代映射则改 type，没有去处则阻止迁移 | reclassify.py:319、410 |
| 迁移审计 | 产生记录新版本，保留正文和有效期；整批可通过治理层撤销 | reclassify.py:217、410 |
| 业务实体合并 | 合并两个实例及其关联事实；要求相同稳定类型 IRI、适用范围和有效区间 | governance.py:_merge_inputs |
| 类型合并专用操作 | 当前原子操作分支中未实现 merge_class / merge_term | ontology_operations.py:_apply_operations |

### 产品交互缺口

`web/ontology-model-panel.js:635` 的 retireSection 有影响面和原因输入，但提交的 retire_term 不携带 replacement，也没有替代目标选择器。后端支持替代映射，当前主要停用界面却未完整暴露它。

因此，用户停用一个类型后，现有实体不会被删除；若没有通过其他受控方式补充替代映射，重分类面板会将这组知识列为没有可用迁移目标。

### 迁移能力的边界

`Reclassify._updates` 只更新所选记录的 ontology_id，并在 rename 处置下更新 type，不改实体记录 ID、正文、有效时间，也不自动合并同名实例。类合并本来就不意味着它的所有实例应合并。

迁移预演将所选更新并入现有数据，调用同一 timeline 校验；关系约束不兼容时可能阻止迁移。这是已有保护，但不等于系统已经自动生成所有关联关系与属性的依赖迁移计划。

## 建议的完整语义

1. 区分显示名修改、稳定 IRI 替换、等价声明、父子关系调整、类型合并、类型拆分、停用与物理删除。
2. 类型合并为显式 `A → B` 演进计划，不按名称相似度自动执行。选择保留的稳定 IRI，旧 IRI 留下弃用和替代记录。
3. 计划需要分别列出本体定义变化（父类、domain/range、SHACL、标注）和实例变化（type、关联关系与属性、候选映射）。结构冲突先给出决策，不机械并集所有约束。
4. 草案阶段只预览这些影响；发布前确认旧数据处置策略，不能发布完成就暗示所有历史事实已经迁移。
5. 可保留本项目现有两阶段模型，但必须关联同一演进计划，显示待迁移、已迁移、受阻数量及目标版本；或者在有界规模下提供明确审核后的原子结构与数据提交。
6. 实体迁移保留实例 ID、证据与历史；类型合并后出现疑似重复实例，再走独立实例消歧/合并流程。
7. 无替代目标的停用不得默认级联删除实体。允许历史保留，但要明确后续是否需要人工重分类；物理删除继续作为高风险独立能力。
8. 拆分类通常不能用单一替代 IRI 表达，需逐记录或按明确规则分流，作为后续能力而非假装已有。

以上为依据调研提出的建议，不是已实施或用户已批准的方案。

## 验证与限制

运行：

```powershell
D:/anaconda/envs/llm_model/python.exe -m pytest -q tests/service/test_reclassify.py tests/service/test_ontology_operations.py tests/service/test_ontology_version_management.py
```

结果：187 passed，1 个 Starlette/AnyIO 弃用警告，79.88 秒。测试收尾出现 projection_runtime 后台线程读取已关闭数据库连接的日志；进程退出码 0，未将其视为本体演进链路已经全面无缺陷的证明。

未启动外部项目做实机实验；外部结论来自上述官方文档与独立源码复核。Parallel web_research 因 OAuth 未连接而未返回研究结果，后续使用可用的网页搜索与提取完成查证。仓库 Hindsight 服务不可连接，未据此引用任何记忆事实。未修改业务实现。
