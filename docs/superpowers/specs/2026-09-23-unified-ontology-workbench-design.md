# 统一本体工作台设计

## 背景与结论

当前项目已经分别具备本体发现、结构化术语维护、Turtle 编辑、发现草案审核、知识候选触发的本体变更提案、影响分析、SHACL 校验和版本历史，但这些能力并未形成一条统一的治理链路：

- `POST /ontology/terms`、`PUT /ontology/term`、`POST /ontology/term-retire` 和 Turtle 保存会直接生成正式本体版本；
- 开放发现草案与知识候选本体变更提案使用两种不同 artifact 和审核流程；
- 结构化类编辑只接受单个 `parent`，而 RDF/Turtle 本身允许多个 `rdfs:subClassOf`；
- 当前发现归纳调用 Semantica 的层级构建能力，但生成项目 RDF 时没有完整保留推断层级；
- 停用会移除部分传入的父级/domain/range 引用，但这些依赖变化没有作为独立操作展示和审核；
- 页面以平铺术语卡片和整块草案审核为主，不适合大量实体类的层级浏览和逐边审核。

采用“一个工作台、两条输入链路、一个草案内核、一条审核发布链路”：

1. 开放发现：候选池 → 层级建议 → 证据/置信度 → 统一草案；
2. 通用建模：结构化编辑、Turtle 导入、停用、恢复 → 统一草案；
3. 统一治理：草案 → 影响分析 → 人工审核 → 完整校验 → 原子发布 → 版本与 provenance。

正式本体版本仍是项目运行时唯一真相源。Semantica、发现候选、草案覆盖层、Neo4j、Milvus 和前端画布都不是正式本体真值。

## 目标

- 完整覆盖类、对象关系、数据属性的新增、调整、停用和恢复；
- 支持 0 到多个直接父类、多根本体和有限有向无环层级；
- 所有写入口都经过同一种草案、审核、校验和发布语义，不再存在直接发布旁路；
- 保留现有本体发现、结构化维护、Turtle、影响分析、SHACL、SPARQL 和版本能力；
- 实体类很多时仍能快速浏览、定位和审核；
- 低风险变化可安全批量审核，中高风险和停用必须逐项审核；
- 每个正式术语和语义边可追溯到草案操作、来源证据、审核决定和发布版本；
- 保持现有项目的浅色、深绿导航、绿色主操作和琥珀审核提示设计语言。

## 明确不做

- 不在首期实现全部 OWL 语言；等价类、互斥类、复杂类表达式、逆关系、传递性和完整推理器作为后续扩展；
- 不创建第二套正式本体存储或独立真相源；
- 不把 Semantica 的建议自动发布为正式本体；
- 不允许 AI 助手直接修改或发布正式本体；
- 不物理删除已发布术语、历史本体版本或历史知识；
- 不自动重写绑定旧本体版本的历史事实；
- 不在一个画布上一次渲染全部类、关系、属性和实例数据；
- 不保留旧的独立 `ontology-manager.html` 作为第二个正式维护入口。

## 术语与不变量

### 父类、子类和根类

- 系统只保存 `child rdfs:subClassOf parent` 直接父级边；子类列表通过反向查询派生，不另存一份；
- 子类的实例集合是父类实例集合的子集；实例不是子类；
- 一个类允许 0 到多个直接父类；没有自定义直接父类的类是根类；
- `owl:Thing` 可作为隐式语义根，不强制在业务页面中显示或要求每个根类显式连接；
- 多父类表示该类的所有实例同时属于每个父类，但不声明该类等于父类交集；
- 自引用、重复直接父级和继承循环均为阻断错误。

### 稳定身份

- IRI 是术语稳定身份，创建后不可原地改名；
- “改名”只修改多语言 `rdfs:label`；
- 需要新 IRI 时创建新术语，可通过 `replaced_by` 元数据声明替代关系；
- Class、ObjectProperty、DatatypeProperty 不允许原地互换类型；必须新建并显式迁移。

### 删除语义

- 已发布术语只能停用，不能物理删除；
- 停用表示该术语不再出现在新的活动本体版本中，不能用于新的正式知识写入；
- 旧本体版本和绑定旧版本的历史知识继续保留并可查询；
- 尚未发布的草案术语可从草案中真正丢弃；
- 恢复停用术语也必须通过新草案发布，不能改写旧版本。

## 选择的模块与 seam

新增深模块 `OntologyDrafts`，其 interface 是所有本体变更调用方和测试共同跨越的 seam。复杂性集中在该模块内部，路由、发现、Turtle 和页面不分别实现校验、影响分析或发布。

建议 interface：

```python
class OntologyDrafts:
    def create(project_id, base_ontology_id, source, title, actor) -> Draft
    def command(project_id, draft_id, expected_revision, command) -> DraftPreview
    def submit(project_id, draft_id, expected_revision) -> DraftPreview
    def decide(project_id, draft_id, expected_revision, decisions, actor) -> DraftPreview
    def rebase(project_id, draft_id, expected_revision, new_base_id) -> DraftPreview
    def publish(project_id, draft_id, expected_revision, expected_ontology_id, actor) -> OntologyVersion
```

模块内部负责：

- 命令规范化和原子操作生成；
- base graph + approved/pending operations 的覆盖层预览；
- DAG、引用、数据类型、SHACL 和影响校验；
- 风险分类和批量审核资格；
- 乐观锁、基线冲突和 rebase；
- 原子发布和 provenance 记录；
- 把结构化操作编译为 RDF triple additions/removals。

现有 `Repository` 是本地可替代依赖和持久化 adapter。首期继续复用 artifacts 与现有 ontology versions，不向路由暴露仓储细节。

## 草案模型

统一使用 artifact kind `ontology_draft`。现有 `ontology_discovery_draft` 和 `ontology_change` 通过兼容适配器迁移，不再新增第三种提案模型。

```json
{
  "id": "draft_uuid",
  "project_id": "project_uuid",
  "base_ontology_id": "ontology_uuid",
  "source_kind": "discovery|manual|turtle|import|ai",
  "title": "合作伙伴层级调整",
  "summary": "新增第二父类并补充合作等级属性",
  "status": "editing",
  "revision": 4,
  "operations": [],
  "validation_report": null,
  "provenance": {
    "actor": "local-user",
    "document_refs": [],
    "candidate_refs": [],
    "model": null
  },
  "created_at": "...",
  "updated_at": "..."
}
```

当前项目没有完整身份权限模块；`actor` 首期沿用现有请求/审核字段，本地无身份时使用明确的 `local-user`，不伪造用户账户。

草案只保存相对 base version 的操作，不保存第二份权威完整本体。工作台需要预览时由模块应用操作生成覆盖图；发布时重新从当前 base graph 计算，不信任前端传回的 Turtle。

## 原子操作模型

```json
{
  "id": "operation_uuid",
  "action": "add_parent",
  "target_iri": "urn:...:合作伙伴",
  "before": null,
  "after": {"parent_iri": "urn:...:业务参与方"},
  "status": "pending",
  "reason": "发现候选表现为业务参与者",
  "evidence_refs": ["candidate:...", "document-version:..."],
  "impact": {},
  "risk": "medium",
  "review_note": null,
  "reviewed_by": null,
  "reviewed_at": null
}
```

首期支持：

- `create_term`
- `update_annotations`
- `add_parent`
- `remove_parent`
- `add_domain`
- `remove_domain`
- `add_range`
- `remove_range`
- `set_datatype`
- `retire_term`
- `restore_term`
- `advanced_rdf_patch`

父级、domain 和 range 都是独立边操作，不能用一个新的完整数组覆盖全部现有边。这样逐边审核、并发合并和 provenance 都有稳定粒度。

`advanced_rdf_patch` 只由 Turtle diff 生成，用于首期结构化编辑器未覆盖但现有 Turtle 能力已允许的 RDF/OWL/SHACL 语句。它必须展示增删三元组、标记为高风险并逐项审核；无法安全归组或影响 RDF collection 完整性的 patch 阻断提交。

## 状态机

草案状态：

```text
editing -> submitted -> reviewed -> published
   |           |           |
   |           |           +-> stale（正式基线已变化）
   |           +-> editing（审核要求调整）
   +-> closed（主动丢弃）
reviewed -> closed（全部操作被拒绝）
stale -> editing（成功 rebase）
```

操作状态：

- `pending`
- `approved`
- `rejected`
- `needs_revision`
- `superseded`

规则：

- 编辑中可增加、调整、撤销操作；每次成功命令增加 draft revision；
- 提交后操作不可直接编辑；“调整”产生新 revision，并把相关旧决定标记为 `superseded`；
- 所有非阻断操作有明确决定后，草案进入 `reviewed`；
- 全部拒绝则 `closed`，不创建本体版本；
- 发布只应用 `approved` 操作；
- 发布时 base ontology 必须仍是当前版本，否则变为 `stale`；
- rebase 重新计算 before、impact、risk 和 validation，受影响审核决定失效；
- 发布必须把新 ontology version、draft 状态和 provenance 在一个事务提交。

## 新增、调整、停用和恢复

### 新增

- 类：IRI、标签、说明、0..N 父类；
- 对象关系：IRI、标签、说明、0..N domain、0..N range；
- 数据属性：IRI、标签、说明、0..N domain、一个受支持 datatype；
- 新建术语和每条结构边分别形成操作，审核人可以批准术语但拒绝某条建议父级边；
- 创建后 IRI 稳定，不因标签变化而改变。

### 调整

- 标签和说明使用 `update_annotations`；
- 父级、domain、range 逐边 add/remove；
- datatype 使用 `set_datatype` 并保存 before/after；
- 调整前展示对后代、已有知识、关系约束、属性值、待审核候选和 SHACL 的影响；
- 不支持术语类型原地转换。

### 停用

`retire_term` 首先生成依赖报告：

- 类：直接子类边、关系 domain/range、属性 domain、SHACL/其他 RDF 引用、该类实例、以这些实例为端点的关系、未完成候选；
- 关系：正式关系记录、待审核关系候选、SHACL/其他 RDF 引用；
- 属性：正式属性事实、实体兼容 properties、待审核属性候选、SHACL/其他 RDF 引用。

停用不能静默级联。可处理的依赖被拆成同一草案中的独立 remove edge/patch 操作；无法安全处理的复杂引用阻断提交并指向高级 Turtle 编辑。历史知识不删除、不自动改型。

### 恢复和替代

- 恢复旧定义：以旧版本定义为模板生成 `restore_term` 与必要结构边操作；
- 替代：创建新术语，并在草案 metadata 中记录 `replaced_by`；
- 是否迁移知识事实是独立知识治理任务，不作为本体发布的隐式副作用。

## 校验与风险

校验顺序：

1. IRI、kind、标签和 datatype 语法；
2. 目标、父类、domain/range 引用存在性；
3. 重复边和自引用；
4. 完整多父类 DAG 循环检测；
5. RDF collection 和图结构完整性；
6. relation/attribute 约束；
7. SHACL；
8. 对现有知识、后代和待审核候选的影响；
9. base ontology 与 draft revision 乐观锁。

严重级别：

- `error`：阻断提交或发布；
- `warning`：可提交，但审核和发布必须明确确认；
- `info`：提示范围和证据。

风险分类：

- 低风险：仅标签/说明；无引用的新叶子术语；没有现有记录影响的纯新增定义；
- 中风险：新增父级边、增加 domain/range、影响后代但不删除语义；
- 高风险：停用、删除父级/domain/range、datatype 修改、影响正式知识、影响大量后代、`advanced_rdf_patch`；
- 阻断：循环、缺失引用、非法类型、无法安全拆分的复杂 RDF/SHACL 引用。

只有校验通过的低风险操作可批量批准。中高风险和停用不能批量批准。

## 人工审核设计

审核不是重新浏览整个本体，而是审核变化：

- 左侧变更队列按草案、根类、来源、风险和操作类型筛选；
- 中央显示 before/after、祖先路径、直接父类/子类和一跳约束上下文；
- 右侧显示来源证据、置信度、影响、校验和技术详情；
- `A` 批准、`E` 调整、`R` 拒绝、`J/K` 切换；
- 决定即时保存，可退出后继续；
- 调整、拒绝和高风险批准必须填写理由；
- 低风险可按当前可见筛选结果批量选择，确认对话框再次展示数量、筛选快照和影响汇总；
- 不提供跨风险、不透明的“一键全部通过”；
- 循环、缺失引用等阻断项不显示批准按钮，只允许调整或拒绝。

## 大规模本体浏览

- 默认对象视角：当前类、直接父类、直接子类、对象关系、数据属性；
- 层级总览：左侧虚拟滚动树按需展开，中央只显示选中路径和配置深度；
- 关系矩阵：按类查看 domain/range 和属性适用范围；
- 搜索支持名称、多语言标签和 IRI；
- 支持“只看变更”“只看冲突”“只看根类”“草案叠加”；
- 后代折叠为数量，需要时按页加载；
- 不把实例节点混入本体层级画布。

## 页面信息架构

侧栏只保留一个“本体工作台”入口，内部五阶段：

1. 候选发现
2. 设计编排
3. 变更审核
4. 发布校验
5. 版本治理

工作台沿用现有全局样式。页面内部采用：

- 左：本体对象库/变更队列；
- 中：对象视角、层级总览或关系矩阵；
- 右：检查器、证据、影响、审核与高级详情。

新增独立 `/assets/ontology-workbench.css?v=ontology-workbench-1`，由 `index.html` 显式引用并加入静态资源契约测试。所有选择器以工作台根命名空间限定，避免影响其他页面。

## 现有能力迁移

| 当前能力 | 统一工作台位置 | 处理方式 |
|---|---|---|
| 本体发现统计、聚类、候选脑图 | 候选发现 | 保留并整合 |
| 来源证据、置信度和候选筛选 | 候选发现/审核右栏 | 保留 |
| 发现草案生成 | 候选发现 | 输出统一 draft/operations |
| 结构化新增类、关系、属性 | 设计编排 | 保留，改为草案命令 |
| 标签、说明、父类、domain/range、datatype | 设计检查器 | 保留并补多父类 |
| 停用影响弹窗 | 设计/审核影响栏 | 保留并扩展依赖拆分 |
| Turtle 编辑 | 设计编排高级工具 | 保留，改为 diff 草案 |
| 发现三阶段审核 | 变更审核 | 保留并扩展逐边决定 |
| 知识候选本体变更提案 | 变更审核 | 保留来源链接，统一操作模型 |
| SHACL/本体验证 | 发布校验 | 保留并补 DAG/引用校验 |
| 版本列表、历史 Turtle、diff | 版本治理 | 保留 |
| SPARQL | 版本治理高级工具 | 保留 |
| `ontology-manager.html` | 无独立入口 | 重复页面不再维护 |

## 接口

### 新接口

```text
POST /api/projects/{p}/ontology-drafts
GET  /api/projects/{p}/ontology-drafts
GET  /api/projects/{p}/ontology-drafts/{draft_id}

POST /api/projects/{p}/ontology-drafts/{draft_id}/commands
POST /api/projects/{p}/ontology-drafts/{draft_id}/submit
POST /api/projects/{p}/ontology-drafts/{draft_id}/decisions
POST /api/projects/{p}/ontology-drafts/{draft_id}/validate
POST /api/projects/{p}/ontology-drafts/{draft_id}/rebase
POST /api/projects/{p}/ontology-drafts/{draft_id}/publish
```

所有变更请求包含 `expected_revision`；发布还包含 `expected_ontology_id`。版本冲突返回 409，不自动覆盖。

命令示例：

```json
{
  "expected_revision": 3,
  "command": {
    "action": "add_parent",
    "target_iri": "urn:knowledge:ontology:project:合作伙伴",
    "parent_iri": "urn:knowledge:ontology:project:业务参与方",
    "reason": "人工建模",
    "evidence_refs": []
  }
}
```

审核请求支持一条或多条 decision，但服务端只接受全部为低风险且最新校验通过的批量批准；不能依赖前端隐藏高风险项。

### 兼容适配器

以下旧接口保留路由，但不再发布：

- `/ontology/terms` → `create_term`；
- `/ontology/term` → annotations 与结构边操作；
- `/ontology/term-retire` → `retire_term`；
- `/ontologies` Turtle POST → Turtle diff operations；
- `/ontology-discovery/drafts` → `source_kind=discovery`；
- `/ontology-change-proposals` → 带 candidate/document evidence 的 operations。

旧写接口返回草案和预览，不再返回已发布 ontology version，并附弃用提示。当前前端和测试同时迁移；读取本体、历史版本和 SPARQL 接口保持兼容。

## Turtle 差异

1. 解析提交 Turtle 和 base Turtle；
2. 按 subject/predicate/grouped RDF collection 计算 additions/removals；
3. 可识别的 class/property/label/subClassOf/domain/range/datatype 转成结构化操作；
4. 其他安全变更转换成 `advanced_rdf_patch`；
5. blank-node collection 被整体识别和替换，不能产生半个 RDF list；
6. 展示可读 diff 和原始 triples；
7. 进入统一审核，不直接发布。

## 开放发现与 Semantica

- 候选池继续保持开放，不要求未知候选先匹配当前本体；
- 调用 Semantica 归纳时保留 `build_hierarchy=True` 的父子建议；
- 归纳结果映射成 `create_term`、`add_parent`、relation/attribute operations；
- 每条建议携带 candidate/document refs、置信度和理由；
- 关系观察只作为证据，不自动推断为严格 domain/range，除非归纳器明确提供且通过审核；
- 多语言标签、多个根类和多个父类均被保留；
- Semantica 缺失时发现草案接口返回明确的可操作错误，不影响手工本体工作台。

## Provenance

统一 provenance 记录以下链路：

```text
ontology version
  <- published-from draft
  <- contains approved operation
  <- decided-by review decision
  <- proposed-by manual/discovery/turtle/import/ai
  <- supported-by candidate/document/source refs
```

操作 before/after、影响报告、审核理由、actor、时间、base ontology、published ontology 均保存。不得保存模型隐藏思维链，只保存可审计建议、理由和证据引用。

## 并发、错误和恢复

- draft 使用 `expected_revision` 乐观锁；
- publish 同时校验 `expected_ontology_id`；
- 两个草案基于同一 base 时允许并存，但先发布者使另一个变为 stale；
- rebase 对每个操作重新定位 before 值并分类为 clean/conflict/no-op；
- no-op 可自动标记 superseded；conflict 必须人工调整；
- 发布失败回滚 ontology version、draft 状态和 provenance；
- 重试相同发布请求必须幂等，不创建重复版本；
- UI 始终保留未提交编辑，不因网络错误清空表单。

## 验证策略

### 当前基线

工作区在设计时为干净的 `master`，最近提交包含属性编辑及渲染。针对本体维护、变更提案和发现测试的结果为 `16 passed, 11 failed`；11 个失败均因当前 `.venv` 缺少 `semantica` 包而发生在 `_induce`，不是新增/调整/停用断言失败。完整测试还在一个异常 LLM 响应用例处出现进程退出，需要在实现前建立正确运行环境并定位。

项目文档要求使用包含 `local,semantica-runtime,test` extras 的环境，并单独安装 `semantica==0.6.7`。实现验证必须使用符合项目文档的环境，而不是把缺包失败误判为业务回归。

### 模块 interface 测试

- create class/relation/attribute；
- 多根、多父类、添加/删除父级边；
- 重复边、自引用和多跳循环阻断；
- annotations、domain/range、datatype 调整；
- retire 的依赖展开、阻断和历史保留；
- restore、replacement 和 stale rebase；
- raw Turtle 结构化 diff 与 advanced patch；
- 风险分类与服务端批量批准限制；
- 全拒绝、部分批准、原子发布和失败回滚；
- provenance 完整链路。

### 接口与页面测试

- 所有写入口只创建/修改草案，不直接增加 ontology version；
- 旧路由适配器与新路由行为一致；
- 低风险批量审核、中高风险单审和阻断项无批准入口；
- 大列表虚拟化、搜索、按需展开和 stale 状态；
- `ontology-workbench.css`/JS 资源版本和加载顺序；
- 页面使用文本转义，不把标签、IRI、证据作为 HTML 注入；
- 键盘操作、焦点、窄屏和 reduced motion；
- 现有本体发现、知识审核、记录写入、RDF、Neo4j、provenance 和前端契约回归。

### 完成标准

- 任何外部本体写入口都不能绕过草案；
- 类支持 0..N 父类且完整图保持 DAG；
- 新增、调整、停用和恢复都有 before/after、影响、决定与发布溯源；
- 已发布术语没有物理删除路径；
- 低风险可批量批准，中高风险和停用只能逐项审核；
- 现有发现、结构化维护、Turtle、影响、验证、SPARQL 和版本能力在新工作台中可达；
- 正确安装项目依赖后，完整测试套件通过；
- 页面样式与当前项目一致，且新增样式不会污染其他模块。

