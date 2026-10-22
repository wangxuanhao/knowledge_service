# Semantica 的本体规则、推理与方法/动作

核对日期：2026-10-21。官方项目为 [semantica-agi/semantica](https://github.com/semantica-agi/semantica)。本次源码固定在 `0934ae80021ac2b9ef756973e91549d26d3f6a8d`；该提交时间为 2026-10-20，包内版本为 0.7.0。另静态核对本机 `D:/anaconda/envs/llm_model` 的已安装包，版本同为 0.7.0，确实包含下述 Action 类。本文不代表当前业务应用已经接入这些能力。

本机最小运行验证通过：加入 `Overdue(invoice_1)` 和上述 IF-THEN Rule 后，`forward_chain()` 得到 `Risky(invoice_1)`；绑定的 CallAction 将参数写入本地列表，连续调用两次推理共执行一次回调、记录一条 action_log。另用缺少 orderNumber 的 RDF 订单和 `sh:minCount 1` shape 调用 `run_shacl_validation()`，得到 `conforms=false`、1 条 violation。验证只操作内存，没有调用业务外部系统或写入项目数据库。

## 结论

Semantica 已有三种需要区分的能力：

1. **校验规则**：生成 SHACL shapes，并通过 pySHACL 校验数据图，产生结构化违规报告。
2. **推理规则**：显式加入事实和规则，用 `Reasoner`、`DatalogReasoner` 或 Rete 等实现匹配、推导。
3. **规则驱动动作**：在 `Rule.actions` 配置断言、撤销、Python 函数调用或事件；匹配时实际执行。动作层于 **0.6.7（2026-08-28）**加入，不能再笼统地说 Semantica 只有推理、不能执行函数。

这里的动作属于应用执行机制，不能据此说 OWL 类天然具有面向对象的成员方法，也不能把 `ontology/methods.py` 的文件名理解为“本体对象方法”。后者是本体生成、导入、算法注册等操作接口。

来源：[版本](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/__init__.py#L13)、[动作层发布记录](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/CHANGELOG.md#L520)、[Action 实现](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/reasoning/reasoner.py#L226)、[ontology methods](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/methods.py)。

## 什么时候使用

| 能力 | 适用时机 | 例子 | 结果 |
|---|---|---|---|
| 结构与约束校验 | 抽取后、写入前、发布前、数据治理审计 | 订单必须有单号；金额为数值；付款方类型正确 | 合规/违规报告 |
| 确定性推理 | 新事实加入后批处理，或查询时按需计算 | A 控股 B、B 控股 C，依据规则得到间接控制；子类实例继承上位类 | 新事实或推导链 |
| 方法/业务动作 | 用户明确触发，或规则匹配后 | 生成审核任务、调用已有风险计算函数、发出领域事件 | 函数结果、状态变更、事件 |

这些触发点是应用集成设计，Semantica 库不会因为数据库中存入了一个实体就自动订阅和执行。应用需要决定在哪个流程加载事实、调用校验/推理、处理动作结果。

## 具体实现

### SHACL 校验

`OntologyEngine.to_shacl()` 调用 `SHACLGenerator`，依据本体类和属性生成 node/property shapes。可表达 datatype、class、min/maxCount、pattern、继承后的属性约束等。

运行路径为 `OntologyEngine.validate_graph(data_graph, ontology=...)` 或传入现成 `shacl=...` → `run_shacl_validation()` → `pyshacl.validate(...)`。真实调用设置 `inference="none"`，所以此入口执行的是 SHACL 约束验证，不会顺带运行完整 OWL 逻辑推理。数据和 shapes 通过 RDFLib 解析，报告包含 focus node、property path、severity、constraint 和 message 等。

来源：[SHACLGenerator](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/ontology_generator.py#L808)、[validate_graph](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/engine.py#L313)、[pySHACL 真实调用](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/ontology_validator.py#L148)。

### 推理规则

`Rule` 包含 `conditions`、`conclusion`、`priority`、`confidence`、`actions` 等字段。`Reasoner.add_rule()` 接受 Rule 或 `IF ... AND ... THEN ...` 字符串，`add_fact()` 加入工作内存，`forward_chain()` 做条件匹配、变量绑定、结论去重，并迭代到没有新结论或达到次数上限。结果保留规则和前提。

例如事实 `Overdue(invoice_1)`，规则 `IF Overdue(?x) THEN Risky(?x)`，可得到 `Risky(invoice_1)`。这里 `Overdue` 应由上游日期计算产生；不能假设任意自然语言业务条文或任意算术表达式都会自动变成可执行规则。

`DatalogReasoner.add_rule()` 支持 Horn clause 风格，如 `ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).`；`derive_all()` 用 semi-naive evaluation，通过增量事实迭代到不动点，适合递归关系与传递闭包。具体语法和内建运算必须以源码支持为限。

来源：[规则定义与加入](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/reasoning/reasoner.py#L325)、[forward_chain](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/reasoning/reasoner.py#L579)、[Datalog](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/reasoning/datalog_reasoner.py#L121)。

### 函数与动作

| 类型 | 实际行为 |
|---|---|
| `AssertAction` | 变量替换后加入事实；`write_back=True` 时尝试写回绑定的图 |
| `RetractAction` | 移除事实；可同步撤销绑定图中的事实 |
| `CallAction` | 直接调用应用传入的 Python callable，签名为 `func(bindings, reasoner)` |
| `EmitEventAction` | 向 `Reasoner.on_event(sink)` 注册的 sink 发出事件和绑定参数 |

`Reasoner.forward_chain()` 发现条件匹配后执行 `Rule.actions`。旧 `Rule.handler` 会包装为 CallAction。执行记录可通过 `provenance=True` 写入 `action_log`。引擎用规则、变量绑定和匹配事实组成 activation key，避免同一引擎实例对相同匹配重复执行动作；即便结论已存在，新的匹配仍可触发动作。

Rete 路径需要 `ReteEngine.bind_reasoner(reasoner)`（或构造时传入 reasoner），之后 `execute_matches()` 才通过绑定的 Reasoner 执行动作。单独匹配不等于已经执行外部函数。

来源：[四类动作](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/reasoning/reasoner.py#L249)、[_fire_actions](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/reasoning/reasoner.py#L427)、[Rete 执行](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/reasoning/rete_engine.py#L599)、[动作测试](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/tests/reasoning/test_rule_actions.py)。

## 限制与不能据文档推断的内容

- `OntologyValidator.validate()` 中 HermiT/Pellet 一致性、可满足性检查仍是占位 `pass`，`check_constraint()` 仍恒返回 True。类说明声称有这些 reasoner，并不能证明该路径已实现。实际 SHACL 验证应走上面的 `validate_graph()` / `run_shacl_validation()`。
- `ontology/methods.py` 含大量顶层 `pass`；实际存在的方法注册查询与导入包装也不是“领域对象自带业务方法”。
- `CallAction` 提供函数执行钩子，未因此自动获得完整的业务方法目录、参数契约、权限判定、审批、事务、持久化幂等、分布式执行和补偿机制。上述机制需要调用方实现。
- 动作异常在 `_fire_actions()` 中记录日志后继续；activation 在执行前已记录，失败动作不会自动重试，测试明确覆盖此行为。引擎内存中的去重不能直接当作外部业务系统的持久化 exactly-once 保证。
- 这些能力需要应用显式接入；安装库、拥有 Rule 模型、抽取出名为“规则”的文本，分别不等于业务系统已运行规则引擎。

来源：[占位的 OntologyValidator](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/ontology_validator.py#L326)、[异常处理](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/reasoning/reasoner.py#L427)、[失败不自动重试的测试](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/tests/reasoning/test_rule_actions.py#L193)。

## 追问：本体生成会不会自动生成关系两端的类型声明

**会尝试生成 domain/range，但它先是依据样本观察得到的模式候选，不等于已经确认业务只允许这些类型，更不等于保存数据时自动拦截。**

- 本机 `ontology_generator.py:435-455` 的第三阶段调用 `PropertyGenerator.infer_properties()`；同文件 `527-546` 的公开接口、`591-620` 的旧兼容类 `PropertyInferencer` 也委托给同一个 PropertyGenerator。
- `property_generator.py:159-222` 按关系谓词分组，默认至少出现 **2 次**才生成属性，可用 `min_occurrences=1` 改写。每组收集来源实体类型作为 domain、目标实体类型作为 range。关系上的显式 `source_type`/`target_type` 优先，否则用实体 ID/名称等别名唯一解析类型；无法判断时回退为 `owl:Thing`。因此传入实体时要有类型，传入关系时要能解析端点。这个过程不是大模型凭空补齐，也不会从一条样例证明完整业务允许范围。
- `infer_domains_and_ranges()`（`property_generator.py:453-475`）并非额外的深层语义推理：它只给缺少的 domain/range 补 `owl:Thing`。
- 从文本生成是另一条路径：`LLMOntologyGenerator` 的 prompt 示例要求模型输出 domain/range，输出规范化只整理 list 等结构，不能保证业务正确性（本机 `llm_generator.py:54-115`）。
- RDF/OWL 导出把这些字段序列化成 `rdfs:domain`、`rdfs:range`，声明本身不会执行数据库写入拦截。OWL/RDFS 的 domain/range 还可用于推出端点所属类型，不能直接当作关系插入的拒绝规则。
- **显式调用 SHACL 生成和验证才会进入约束校验路径**：本机 `ontology_generator.py:1081-1125` 按 domain 把 property shape 附到对应 `sh:targetClass`；`1127-1169` 把对象属性 range 设为 `class_`；`1268-1269` 导出 `sh:class`，再调用 `validate_graph()`/`run_shacl_validation()` 执行。注意它目前对 range 列表只取第一项（`1130-1131`），而 targetClass 只覆盖已有对应类型的来源节点，不能据此声称完整表达了任意多类型两端允许组合。

官方固定提交来源：[属性推断](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/property_generator.py#L159)、[缺省补齐](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/property_generator.py#L453)、[端点类型解析](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/relationship_utils.py#L44)、[SHACL range 转换（官方行号较本机有偏移）](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/ontology_generator.py#L1133)、[SHACL class 输出](https://github.com/semantica-agi/semantica/blob/0934ae80021ac2b9ef756973e91549d26d3f6a8d/semantica/ontology/ontology_generator.py#L1293)。

本机最小运行结果：输入两条带类型实体（`p1:Person`、`c1:Company`）和一条 `p1 --worksFor--> c1`，调用 `generate_ontology(..., build_hierarchy=False, validate=False, min_occurrences=1)`，实际得到 `worksFor.domain=["Person"]`、`worksFor.range=["Company"]`。随后显式 `OntologyEngine.to_shacl(ontology)`，PersonShape 下实际输出 `sh:path .../worksFor; sh:class .../Company`。这验证了自动生成类型声明、再转换 SHACL 的真实代码路径；没有启用业务写入拦截，也未调用任何 LLM。

同次输出还观察到本机已安装版本把伴随的 name 数据属性序列化为 `sh:datatype xsd:xsd:string`，而上面固定提交源码已有不同的 datatype 处理。这次未扩展执行生成 shapes 的验证，因此不能把“两端类型 shape 生成成功”说成“该版本所有自动生成 shapes 都正确”。

## 当前项目的实际接入

主代理继续核对本项目源码，结论如下：

- 已有实体类型、关系、属性和类继承。`Ontology.parents()` 遍历 `rdfs:subClassOf`，`dataset()` 给实体补上父类类型。来源：`knowledge_service/services/ontology.py:159,253,350`。
- 已有真正执行的约束规则：`Ontology.validate()` 校验词汇、端点类型、属性原始类型及 domain/range，然后直接调用 pySHACL；调用参数为 `inference='rdfs', advanced=False, do_owl_imports=False`。因此具有有限 RDFS 处理和 SHACL 校验，不能宣称没有任何规则能力，也不能宣称已有完整 OWL 或通用业务推理。来源：`knowledge_service/services/ontology.py:411`。
- 约束实际进入写入流程，并按事实有效时间分段校验；异常根据入口拒绝写入或进入审核。来源：`knowledge_service/services/ontology.py:468`、`knowledge_service/services/service.py:295`。
- Semantica 已用于抽取、本体发现、本体质量检查等。质量检查边界不等同于规则推理或业务动作执行。来源：`knowledge_service/integrations/semantica_adapter.py:534`、`knowledge_service/services/ontology_discovery.py:535`、`knowledge_service/integrations/semantica_ontology.py:33`。
- 对应用 Python 源码检索 `Reasoner`、`Rule(`、`CallAction`、`forward_chain`、`backward_chain`、`ReteEngine` 等，未发现规则引擎或动作层的实际接入。当前没有查到可配置的通用 IF-THEN 推理链，以及由该规则链触发业务函数的执行入口；应用自身的服务函数不应被称为已经产品化的“本体对象方法”。这是对当前代码的静态核对结论。
- `pyproject.toml:23` 声明可选依赖 `semantica[parse-docling]>=0.7,<0.8`；本机安装 0.7.0 只说明依赖中存在相关能力，不代表应用已启用。

后续若接入，需由应用显式把有效事实映射到规则引擎、选择规则版本与触发点，把推导结果连同规则/前提溯源交给既有正式事实写入和审核机制；需要业务动作时，再登记可调用函数及其参数、权限和执行状态。自然语言规则文档或抽取出的规则实体不会自动成为可执行规则。
