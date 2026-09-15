# Semantica 抽取与本体约束机制

> 基于 Semantica 0.6.8 源码核查与 `knowledge_service/` 实际调用链路。代码路径以本地安装包为准；本服务文件为仓库相对路径。

## 一句话结论

Semantica 的 LLM 抽取是**开放式**的，类型列表只是提示而非硬约束；本体约束由本服务在后处理阶段落实，未联动不等于不生效。

---

## 分层架构

| 层 | 职责 | 约束力 |
|---|---|---|
| Semantica 抽取层 | 传入类型提示，LLM 自由输出实体/关系 | **软提示**，不拒绝偏题类型 |
| Semantica 验证层 | 置信度阈值、空文本、非法主/客体 | **格式检查**，不查本体 |
| 本服务后处理层 | 类型解析、domain/range 校验、SHACL 验证 | **确定性约束**，按配置决定放行/审核/拒绝 |

换言之：Semantica 负责"尽量多抽"，本服务负责"按规矩筛"。

---

## Semantica 模块地图

Semantica 安装在 `D:\anaconda\envs\llm_model\Lib\site-packages\semantica\`，主要子模块：

| 子模块 | 功能 | 与本服务的关系 |
|---|---|---|
| `semantic_extract/` | LLM / NER / 集成抽取 | 直接调用（`methods.extract_entities_llm`、`extract_relations_llm`） |
| `ontology/` | OWL 本体构建、评估、校验、版本管理 | **未自动联动**；本服务自行管理本体 |
| `kg/` | 图完整性校验（悬空边、自环、孤儿检测） | 未在默认链路调用 |
| `reasoning/` | 演绎/归纳/Datalog/RETE/SPARQL 推理 | 未在默认链路调用 |
| `deduplication/` | 实体消歧与合并 | 本服务自建消歧，未接入 |
| `context/` | ContextGraph 状态管理 | 仅调用 `state_at` |
| `data_models/` | Entity、Relation、Document 等数据结构 | 贯穿使用 |

**要点**：本服务实际只用了 `semantic_extract` 的 LLM 抽取和 `data_models` 的数据结构，其余模块未启用。

---

## 抽取为什么是"宽"的

### 实体抽取

`semantic_extract/methods.py`，`extract_entities_llm`（~行 970）：

- 实体类型作为 `preferred_entity_types` 参数传入。
- 提示词（~行 1064-1070）原文大意：*"Preferred entity types: {list}. You may also use related or similar entity types… use the most appropriate type from the preferred list or a closely related type."*
- 完整提示词在 ~行 1092-1115 组装，类型列表出现在"建议"位置，不是"禁止"位置。

**结论**：LLM 看到类型提示，但被显式告知"也可以用相关类型"。

### 关系抽取

`semantic_extract/methods.py`，`extract_relations_llm`（~行 1708）：

- 同样模式，关系类型作为提示传入。
- 提示词（~行 1843-1846）明确允许 *"related or similar relation types… or a closely related type"*。
- 完整提示词在 ~行 1864-1884。

**结论**：与实体抽取一致，关系类型是建议而非白名单。

### 抽取后验证

`semantic_extract/extraction_validator.py`：

- `validate_entities` / `validate_relations` 检查：置信度阈值、空文本、无效主/客体。
- **不查本体**。不在本体里的类型不会被拒绝。

### NER 集成方法（供参考）

`semantic_extract/ner_extractor.py` 文档字符串描述了一个 `Type_Similarity_Score` 机制（Exact=1.0，Synonym=0.95，Embedding cosine），用于将输出映射到最近的提供类型。这是软匹配，不是拒绝。本服务使用的是 LLM 方法，不经过此机制。

---

## 本体能力在哪，为什么默认不联动

Semantica 的 `ontology/` 子模块是**独立的本体工程工具包**：

| 类 | 功能 |
|---|---|
| `OntologyEngine` | `from_data` / `from_text` 生成 OWL 本体 |
| `OntologyEngine.infer_classes` / `infer_properties` | 从数据推断类和属性 |
| `OWLGenerator` | 生成 OWL 文件 |
| `OntologyEvaluator` | 本体质量评估 |
| `OntologyValidator` | 本体结构校验 |
| `OntologyQualityGate` | 抽取前质量门控 |
| `VersionManager` | 本体版本管理 |

这些工具用于**构建、评估和管理本体本身**，不用于约束 LLM 抽取的输出。

`kg/graph_validator.py` 做图完整性检查（必填字段、数据类型、悬空边、自环、循环/孤儿检测），也不是抽取约束。

**为什么默认不联动？** Semantica 的默认抽取路径（`extract_entities_llm` → `extract_relations_llm`）不调用 `ontology/` 或 `kg/` 中的任何校验器。这是设计选择：抽取层追求召回率，本体约束交给上层应用按需接入。

---

## 本服务如何后处理约束

### 调用链概览

```
Semantica 返回 entities/relations
    │
    ▼
semantica_adapter.py  ─── 实体类型解析 ─── ontology.resolve(entity.label, ontology.classes)
    │                                          ↓ 未识别 → review_candidates
    │
    │                ─── 关系谓词解析 ─── ontology.resolve(relation.predicate, ontology.relations)
    │                                          ↓ 未识别 → review_candidates
    │
    │                ─── domain/range 检查 ─── ontology.relation_constraint_issues(...)
    │                                          ↓ 冲突 → 按 relation_constraint_mode 处理
    │
    ▼
service.py  ─── SHACL 验证 ─── ontology.validate_timeline(...)
                                     ↓ 违规 → 按 shacl_mode 处理
```

### 关键调用点

| 步骤 | 文件 | 位置 | 行为 |
|---|---|---|---|
| 实体类型解析 | `knowledge_service/semantica_adapter.py` | ~行 140 | `ontology.resolve(entity.label, ontology.classes)` |
| 关系谓词解析 | `knowledge_service/semantica_adapter.py` | ~行 160 | `ontology.resolve(relation.predicate, ontology.relations)` |
| domain/range 检查 | `knowledge_service/semantica_adapter.py` | ~行 169 | `ontology.relation_constraint_issues(...)` |
| 关系提示注入 | `knowledge_service/semantica_adapter.py` | ~行 99-101 | 将 domain/range 写回抽取提示词（仅提示） |
| SHACL 写时验证 | `knowledge_service/service.py` | `write()` ~行 105 | `ontology.validate_timeline(...)` |

### domain/range 四种模式

| `relation_constraint_mode` | 冲突关系 | 其他合法知识 | 说明 |
|---|---|---|---|
| `review`（默认） | 不入图，转为候选 | 正常提交 | 最常用 |
| `advisory` | 入图，metadata 留警告 | 正常提交 | 降低噪音 |
| `strict` | 整份文档失败 | 不提交 | 外部 API 默认 |
| `off` | 不检查 | 正常提交 | 仍要求类型存在 |

### 关系提示注入（不等于约束）

`semantica_adapter.py` ~行 99-101 将本体的 domain/range 信息写回关系抽取提示词。这是"建议"，模型可以无视。真正的约束在上面的解析和校验步骤。

---

## 现象、根因与对策

### 观察到的现象

一次实际运行中：43 个实体 + 49 条关系被抽取，其中 40+ 条关系进入审核候选。原因是模型产出了大量本体未定义的谓词：

| 模型产出的谓词 | 本体中是否存在 |
|---|---|
| `related_to` | 否 |
| `responsible_for` | 否 |
| `providesAuthorization` | 否 |
| `prohibits_behavior_involving` | 否 |
| `prohibits_impersonation_of` | 否 |
| `prohibits_false_association_with` | 否 |

本体仅定义了 24 种关系。一个实体类型被误判后，它的所有关联关系都会变成"等待实体审核"的候选，产生级联效应。

### 根因

1. **抽取层的开放性是设计意图**：Semantica 希望模型尽可能发现新类型，用于本体迭代。
2. **提示词明确允许偏题**：类型列表是"preferred"，不是"allowed only"。
3. **后处理做约束但有门槛**：未识别的类型进入审核队列而非直接丢弃，保证不丢失潜在有用信息，但会造成审核堆积。

### 可选对策

| 对策 | 原理 | 代价 |
|---|---|---|
| **（a）替换提示词** | 绕过 `methods.extract_entities_llm` / `extract_relations_llm`，直接调用 provider，写更严格的 prompt | 需自维护抽取逻辑 |
| **（b）丰富本体** | 将模型常用谓词（`related_to` 等）正式加入本体 | 可能引入模糊关系 |
| **（c）切换为 advisory 模式** | domain/range 冲突不阻断，只留警告 | 放松约束 |
| **（d）模糊映射** | 将 off-ontology 名称通过同义词/嵌入相似度映射回本体术语 | 需要额外实现，可能误映射 |

对策（a）是最彻底的方案：用更严格的提示替换 Semantica 的开放式默认提示，从源头减少偏题输出。对策（b）和（c）是低成本缓解。对策（d）需要额外开发。

---

## 附录：本服务涉及的关键文件

| 文件 | 角色 |
|---|---|
| `knowledge_service/semantica_adapter.py` | Semantica 抽取适配、类型解析、domain/range 检查、候选生成 |
| `knowledge_service/service.py` | 写入编排、SHACL 验证入口 |
| `knowledge_service/ontology.py`（或等价本体模块） | 本体加载、`resolve()`、`relation_constraint_issues()`、`validate_timeline()` |

Semantica 库路径：`D:\anaconda\envs\llm_model\Lib\site-packages\semantica\`
