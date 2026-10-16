# 规则文档场景：Semantica、GraphRAG 与实施建议

调研日期：2026-10-14。依据官方文档和公开源码；未运行框架端到端实验。本报告不核对当前项目实现或本机安装版本，二者应单独核对。Semantica 的 Hawksight-AI 仓库 URL 现重定向至 `semantica-agi/semantica`；本次源码快照为 `0bd39ccf7f99cc5fc36f50b86fed0b9ecf635d77`。

## 结论

规则文档的关键不是数值计算，而是保留适用主体、前提条件、义务/许可/禁止、例外、引用条款和生效版本。建议在现有系统上先建设“以完整条款为主的混合检索 + 本体概念与条款引用图补全 + 带出处的回答”。无需为了 GraphRAG 重新建整套系统，也不应把实体连接路径当成规则推导。

这里的 GraphRAG 可以泛指图辅助检索，也可以指微软的具体框架。前者适合本场景；后者不是现阶段必需依赖。

## 1. 不是实体关系和数值关系，而是实体引用和字面量

Semantica 官方本体指南明确区分 object properties 和 datatype properties：前者连接两个实体，后者连接实体与字面量。字面量包括文本、日期、布尔值和数字，所以数值少不意味着属性不重要。

例如：

- `条款 A --引用--> 条款 B`：实体关系。
- `条款 A --适用于--> 外包人员`：若“外包人员”是可复用业务概念，则是实体关系。
- `条款 A --原文--> "外包人员不得访问生产系统，但经授权的运维任务除外。"`：文本属性。
- `条款 A --规范类型--> "禁止"`：可以采用枚举属性；若“禁止”在本体中有独立身份与语义，也可建为实体引用。建模选择应统一，不能仅按字符串是否像数字决定。

来源：[Semantica ontology guide](https://github.com/semantica-agi/semantica/blob/0bd39ccf7f99cc5fc36f50b86fed0b9ecf635d77/docs/guides/ontology.md)。

## 2. Semantica 实际做什么

### 通用抽取接口

官方语义抽取指南展示 `NamedEntityRecognizer.extract_entities(text)`、`RelationExtractor.extract_relations(text, entities)`、`TripletExtractor.extract_triplets(text, entities, relations)`。这是一条从实体、关系到三元组的通用流程，不能据此认为它自动保留规则的完整条件和例外。

源码中的 `Relation` 的两端是 `Entity`；`Triplet.object` 是字符串。`RelationOut.object` 的字段说明是目标实体文本。这些通用类型不是一个包含实体/字面量判别字段和完整规则条件树的统一事实模型。

来源：

- [semantic extraction guide](https://github.com/semantica-agi/semantica/blob/0bd39ccf7f99cc5fc36f50b86fed0b9ecf635d77/docs/guides/semantic-extraction.md)
- [types.py](https://github.com/semantica-agi/semantica/blob/0bd39ccf7f99cc5fc36f50b86fed0b9ecf635d77/semantica/semantic_extract/types.py#L22)
- [schemas.py](https://github.com/semantica-agi/semantica/blob/0bd39ccf7f99cc5fc36f50b86fed0b9ecf635d77/semantica/semantic_extract/schemas.py)

### 本体属性生成接口

`PropertyGenerator.infer_properties(entities, relationships, classes, **options)` 明确走两条路：

1. `_infer_object_properties()` 根据已有关系类型和端点类型，推断 object property 及 domain/range。
2. `_infer_data_properties()` 从已有实体字典的业务字段，推断 data property。

`_extract_data_properties()` 跳过框架控制字段，遍历其他实体字段；`_infer_property_type()` 支持 boolean、integer、double、date、dateTime、string。这一步主要生成属性定义，并不是从原文自动补齐这些属性值。若上游没抽出业务字段，后面无法凭空得到它们。

因此，不能用“Semantica 生成了本体属性”证明“文档属性抽取完整”。也不能把本次 main 分支源码直接当成本机安装版本的行为。

来源：[property_generator.py](https://github.com/semantica-agi/semantica/blob/0bd39ccf7f99cc5fc36f50b86fed0b9ecf635d77/semantica/ontology/property_generator.py#L95)，尤其 L128–142、L285–329、L365–406。

## 3. 规则不能压扁成普通事实

假设条款：“外包人员不得访问生产系统，但经授权的运维任务除外。”

如果仅生成 `外包人员 --不得访问--> 生产系统`，会丢失例外；如果仅生成 `外包人员 --访问--> 生产系统`，则连禁止都丢失了。

建议保留一个完整规则记录，而不是拆成互不关联的关系：

```json
{
  "rule_id": "R-12",
  "subject": "外包人员",
  "modality": "禁止",
  "action": "访问",
  "object": "生产系统",
  "conditions": [],
  "exceptions": [{"text": "经授权的运维任务", "source_span": "原文位置"}],
  "source_clause": "第十二条",
  "source_text": "外包人员不得访问生产系统，但经授权的运维任务除外。",
  "version": "文档版本"
}
```

这是一项面向当前业务的建模建议，并非 Semantica 自带的规则格式。初版条件/例外可以保留原文和片段定位，复杂 AND/OR、嵌套否定在没有可靠抽取验证前不强行转成自动执行规则。例外成立也不自动证明其他权限规则已满足。

## 4. 微软 GraphRAG 的适用范围

官方 Query Overview：Local Search 用抽取图和原始文档片段回答具体实体问题；Global Search 对社区报告做 map-reduce，适合整体主题问题，官方明确其资源开销较大；DRIFT 在 local 检索中加入社区信息并生成后续问题，扩展覆盖范围。

默认索引流程是切分 TextUnits、抽取实体/关系、汇总描述、社区检测和社区报告；可选 claims 抽取默认关闭。Claims 是带状态和时间范围的事实陈述，不能等同于规则的适用条件、例外和法律效力判定。

规则问答的风险在于关键例外或限制可能只出现一次，纯实体图和生成摘要不保证把它们完整带到回答上下文。因此建议：

| 问题类型 | 优先方案 |
|---|---|
| 某角色能否执行某操作？ | 完整条款检索，补全适用范围、定义、例外、引用，基于原文回答 |
| 某流程有哪些要求？ | 条款/章节检索，按步骤合并并保留逐项出处 |
| 两份制度怎么衔接？ | 引用图与概念对齐，比较版本和范围，明确未解决冲突 |
| 全部制度主要涵盖什么？ | 可选社区摘要/章节摘要，适合借鉴 Global Search |

来源：

- [Query overview](https://microsoft.github.io/graphrag/query/overview/)
- [Local Search](https://microsoft.github.io/graphrag/query/local_search/)
- [Default indexing dataflow](https://microsoft.github.io/graphrag/index/default_dataflow/)

以上建议是根据官方机制和本场景风险作出的工程判断，不是对 GraphRAG 在该项目上的实测结论。

## 5. 实施顺序

1. **以条款为检索单元**：保存章节层级、条款编号、原文、版本、效力范围。长条款可分段，但检索到一段必须能恢复完整条款与相关父级适用范围。
2. **增加规则字段抽取**：主体、行为、对象、规范类型、条件、例外、引用和原文定位。允许未知，禁止模型补造前提；每个关键字段应可回到原文验证。
3. **图用于补全证据**：实体/概念用于别名和类层级；条款边用于“引用、定义、例外、修订”等。版本、地域、部门等限制先过滤，再扩展；未被文档明确声明的优先关系不能猜测。
4. **混合召回后做证据闭包**：关键词/BM25 与向量检索候选条款，重排后补齐定义、例外和引用条款。关联条款未找到时，报告依据不足；无条件广泛三跳会引入不适用条款。
5. **按规则回答结构输出**：结论、适用前提、例外、依据条款、不足信息。满足例外需要何种事实必须说清；用户没有提供事实时给条件式结论或追问，不能把信息缺失当成条件不成立。
6. **建立场景验证集**：纯禁止、条件许可、例外、跨条引用、AND/OR、版本冲突、同名不同范围、缺少事实。重点测例外召回、否定保持、来源有效性和拒答，而非实体数量。

首轮交付应是完整条款召回及例外补全闭环；不优先上完整 OWL 推理器、通用规则执行引擎或微软 GraphRAG 全流程。对明确、稳定、可审核的少量规则，以后再建设确定性判定，和文档解释能力分开验收。
