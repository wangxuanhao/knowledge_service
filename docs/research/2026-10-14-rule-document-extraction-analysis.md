# 规则文档：关系、属性抽取与问答方案分析

日期：2026-10-14。静态核对当前项目、本机 Semantica 0.7.0；运行了无 LLM、无数据库的开放事实路由小型复现。本文是调查与建议，没有修改业务实现，没有测定真实业务抽取准确率。开源核对另见同目录 `2026-10-14-rule-document-framework-research.md`。

## 结论

规则文档的核心是适用范围、前提条件、例外、义务/禁止/允许、处理结果与条款引用，不是数值计算。建议继续使用现有本体、混合检索和来源治理，增加条款结构及规则陈述抽取，让原文条款成为主要证据、图谱帮助找齐相关条款。暂不整体替换为 Microsoft GraphRAG，也不把规则直接变成无条件实体边。

## 关系和属性怎么分

关系是对象到对象（owl:ObjectProperty）；属性是对象到字面量（owl:DatatypeProperty）。字面量包括字符串、布尔、日期、状态、编码和数值，不能叫“数值关系”。如“规则适用于商户”可连接规则与商户概念；“规则状态为生效”通常是状态属性。状态也可被领域本体明确建成枚举概念，此时允许对象关系，但必须由建模语义决定，不能因为 NER 恰好识别成实体就自动决定。

统一提取 subject/predicate/object 事实候选、再区分 entity-reference 与 literal 是可行设计；仍需本体谓词种类、宾语语义与人工确认，不能用“字符串是关系、数字是属性”的规则。

## Semantica 与项目各自做了什么

本机安装版本与源码证据：

- `D:/anaconda/envs/llm_model/Lib/site-packages/semantica/semantic_extract/types.py:54`：Relation.subject/object 都是 Entity。
- `.../semantic_extract/types.py:66`：Triplet.subject/predicate/object 都是字符串，没有默认的实体/字面量判别字段。
- `.../semantic_extract/methods.py:1918`：extract_relations_llm 依赖实体清单抽关系。
- `.../ontology/property_generator.py:95`：infer_properties 从 relationships 推断对象属性定义，从 entities 的业务字段推断数据属性定义。
- `.../ontology/property_generator.py:389`：数据类型覆盖 boolean/integer/double/date/dateTime/string。这里推断的是本体属性定义，不等于自动从规则文本抽出了正确属性或规则。

项目本体约束模式：

1. `knowledge_service/integrations/semantica_adapter.py:530` 先抽实体，再抽关系。
2. 关系步骤使用项目自定义 `_extract_relations_guided()` 提示与结构，利用 Semantica provider，不是无改动地调用上游关系提示。
3. `knowledge_service/services/attribute_extraction.py:51` 单独调用 Semantica provider.generate_typed，使用项目自己定义的 AttributeResponse。
4. `models.py:127` extract_attributes 默认 False；开启后属性提案全部进入审核，不直接发布。所以“没有正式属性”也可能是未启用或尚未审核，不能据此判断算法失败。
5. 属性值允许字符串/布尔/数值；提示要求稳定标量特征，排除动作、禁止、职责等。这部分设计本身合理，但不能代替规则陈述抽取。

项目开放发现模式：

1. `_extract_facts_open():257` 在一次模型调用中提取事实，并输出 relation/attribute/ambiguous 与 literal_type。
2. `_route_open_facts():281` 再路由，检查证据、实体匹配与谓词命名冲突。
3. 因此用户提出的“统一事实抽取后分类”，这个模式已经部分具备。

## 已复现的分类风险

输入原文：`规则甲的状态为生效。`；同一模型候选固定为 `fact_kind=attribute, subject=规则甲, predicate=状态, object=生效, literal_type=status`。

直接调用实际 `_route_open_facts()` 的结果：

| 实体清单 | 输出 |
| --- | --- |
| 只有规则甲 | attribute，classification_reason=scalar_literal |
| 额外把“生效”识别成业务状态类型实体 | relation，classification_reason=object_matches_entity |
| 额外把“生效”识别成 DataEntity | relation，classification_reason=object_matches_entity |

这是路由行为的确定性复现，不证明真实 LLM 必然会识别出这些实体。风险原因是 `if source_kind=='relation' or object_entity is not None`：实体匹配覆盖了显式 attribute。开放模式还缺少本体约束模式里的 DataEntity 防护。

约束模式 `semantica_adapter.py:714` 保护已声明数据属性，但未知谓词在值唯一命中非 DataEntity 实体时仍可能改为关系候选；属于待审核风险，而非已经自动写成错误正式知识。

建议路由优先级：明确本体谓词契约优先；明确 literal_type 与标量语义应保留为属性；与实体识别冲突时标记歧义并隔离；未知谓词不得仅因文本匹配而升级。数据类型/值是否合法仍需要独立校验。

## 更大的语义缺口：规则不是普通事实

假设原文：`商品存在质量问题时，消费者可以申请退货；因消费者使用不当导致损坏的除外。`

仅抽出“消费者—申请退货—商品”，会丢掉质量问题前提、使用不当例外以及“可以”的许可语义。放到正式事实里还可能被误读为已经发生的退货。这些内容即使仍保存在来源原文，也没有被当前关系字段明确表达，后续单独用图谱边回答就存在风险。

建议保留一条可追溯规则陈述：原条款、适用对象、前提条件、例外、规范性（允许/必须/禁止）、动作与对象、文档/条款/版本及证据位置。条件需区分 AND/OR 与否定；无法可靠解析时保留完整原文、标记待核，不自动执行。规范性可存为规则属性；actor/action/object 与条件表达式可引用概念节点。不要把全部逻辑塞进一个长谓词，也不要将规则条件写成已发生事实。

## 推荐实现路线

### 条款是主要证据，图是查找辅助

1. **增强现有结构切片。** `services/chunking.py` 已有章节/条编号切片，不从零开发。补条款编号、标题路径、父条款、子项、表格及引用映射。检索命中一个子项时补父级适用范围和紧邻例外；避免长条款被固定长度切开后上下文失联。
2. **新增规则陈述提取。** 复用 Semantica provider.generate_typed，使用专门结构输出条件、例外、规范性和处理动作；与通用实体关系/属性分开，减少对 NER 完整性的依赖。建议先作为可回原文的提案，复用现有审核和证据能力。
3. **双路召回。** 保留关键词+向量原文召回；本体/实体/主题图补召回相关条款。优先沿适用、定义、引用、补充、例外等有含义的边扩展，少用无方向的所有邻居扩展。
4. **完整条款组装与重排。** 查询命中规则后，补关联定义、上位适用范围、被引用条款及例外，重排选出最相关的一组。控制上下文预算，不能递归无界补全。
5. **生成适用性解释。** 回答列明通常要求、适用条件、例外和来源。缺少用户事实时明确缺什么，必要时询问；不把“未提及”当成“不满足”。不同文档冲突先展示依据与范围，不让模型自行决定哪个优先。
6. **针对场景评测。** 检查是否漏条件、漏例外、把禁止改成允许、把规定当事件、断章取义、引用错误；数值准确率只占次要位置。用相同语料比较原文混合 RAG 与增强方案后再决定是否加更重的图索引。

这是一条检索增强与规则解释路线，不宣称已经形成可执行法律/业务规则引擎。只有用户明确需要自动判定时，再把审核过的少量规则转成执行表达式。

## 是否采用 GraphRAG

| 方案 | 适用情况 | 对当前项目的建议 |
| --- | --- | --- |
| 条款混合 RAG | 查具体规定、引用原条款 | 先做可靠基线，成本较低 |
| 条款混合 RAG + 本体/引用图 | 查跨条款定义、前提、例外与范围 | 推荐，复用当前基础与治理 |
| 完整 Microsoft GraphRAG | 跨大量文档总结主题、全局归纳与关联探索 | 可旁路试验 Global/DRIFT；默认实体关系+社区摘要不能保证条件、例外和否定保真 |

具体参考：Semantica 用作类型化抽取与 provider；Microsoft GraphRAG Local 用作实体/关系/原文多源上下文组织的参考，Global 用作主题综述的参考；OG-RAG 用作本体字段参与检索的参考。规则字段设计与条款补全是针对本场景的工程方案，不是声称某开源框架自动提供这些能力。
