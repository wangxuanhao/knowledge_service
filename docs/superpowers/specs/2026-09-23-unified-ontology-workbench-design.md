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
- 停用采用可逆的语义弃用：保留术语声明、标签、父级和约束，在新版本中增加 `owl:deprecated true`；活动术语查询和新的正式知识写入默认排除已停用术语；
- 旧本体版本和绑定旧版本的历史知识继续保留并可查询；
- 尚未发布的草案术语可从草案中真正丢弃；
- 恢复停用术语也必须通过新草案移除弃用标记，不能改写旧版本；
- 替代关系写入 RDF `dcterms:isReplacedBy`，不能只保存在草案 JSON 中。

## 选择的模块与 seam

新增深模块 `OntologyDrafts`，其 interface 是所有本体变更调用方和测试共同跨越的 seam。复杂性集中在该模块内部，路由、发现、Turtle 和页面不分别实现校验、影响分析或发布。

建议 interface：

```python
class OntologyDrafts:
    def create(project_id, base_ontology_id_or_none, source, title, actor) -> Draft
    def command(project_id, draft_id, expected_revision, command) -> DraftPreview
    def submit(project_id, draft_id, expected_revision) -> DraftPreview
    def decide(project_id, draft_id, expected_revision, expected_ontology_id, decisions, actor) -> DraftPreview
    def rebase(project_id, draft_id, expected_revision, expected_ontology_id) -> DraftPreview
    def close(project_id, draft_id, expected_revision, actor, reason) -> Draft
    def publish(project_id, draft_id, expected_revision, expected_ontology_id, idempotency_key, actor) -> OntologyVersion
```

模块内部负责：

- 命令规范化和原子操作生成；
- base graph + 当前有效操作及其最新决定的覆盖层预览；
- DAG、引用、数据类型、SHACL 和影响校验；
- 风险分类和批量审核资格；
- 乐观锁、基线冲突和 rebase；
- 原子发布和 provenance 记录；
- 把结构化操作编译为 RDF triple additions/removals。

现有 `Repository` 仍是本地可替代依赖和持久化 adapter，但现有通用 `artifacts` 表不足以提供数据库级 CAS、不可变审核历史和跨本体版本/草案/provenance 的原子提交。因此统一草案必须新增专用表和 Repository 方法，不能仅复用 artifact JSON。

### 持久化与迁移

新增一次可回滚迁移，至少包含：

- `ontology_drafts`：`id`、`project_id`、可空 `base_ontology_id`、`source_kind`、`status`、`revision`、标题/摘要、来源上下文、`published_ontology_id`、时间戳、可选 `legacy_artifact_id`；
- `ontology_operations`：只追加操作，保存规范化 before/after、证据、风险、影响、校验快照、指纹和可选 `supersedes_operation_id`；调整操作通过追加新行替代旧行，不原地覆盖审计内容；
- `ontology_review_decisions`：只追加决定，保存稳定 decision id、operation id/fingerprint、action、reason、actor、时间和被替代关系；
- 发布幂等记录或等价唯一约束：以 `(project_id, draft_id, idempotency_key)` 保证重试不会产生重复版本。

草案命令使用单条数据库 CAS：`UPDATE ontology_drafts ... WHERE id=? AND project_id=? AND revision=?`，未更新到一行即返回 409。进程内锁只可作为性能优化，不能承担正确性。发布在一个 Repository 事务内完成：校验当前 ontology、插入 ontology version、落地草案终态、操作/决定引用和 ontology provenance；任何一步失败均整体回滚。

现有 `save_ontology` 降为 Repository 私有/受控原语，外部路由和业务 service 不得直接调用。仅允许三类内部调用：新项目的可信默认本体 bootstrap、向全新空项目执行精确快照恢复/迁移、统一草案 publish。项目创建时 bootstrap 记录来源与 actor；已有项目的旧格式导入必须进入草案。首个草案允许 `base_ontology_id=null`，仅当项目发布时仍无当前本体才可成功。

旧 artifact 的迁移策略：

- 已发布或已拒绝的 `ontology_discovery_draft` / `ontology_change` 保持只读历史，不伪造新决定；
- 尚未结束的旧 artifact 在首次打开时事务性惰性转换为统一草案，并保存 `legacy_artifact_id`，原 artifact 保持不变；
- 转换失败时标记“需要迁移复核”，不允许从旧接口直接发布；
- 新写入一律进入专用草案表。

## 草案模型

统一使用 artifact kind `ontology_draft`。现有 `ontology_discovery_draft` 和 `ontology_change` 通过兼容适配器迁移，不再新增第三种提案模型。

```json
{
  "id": "draft_uuid",
  "project_id": "project_uuid",
  "base_ontology_id": "ontology_uuid 或首版时为 null",
  "source_kind": "discovery|manual|turtle|import|ai",
  "title": "合作伙伴层级调整",
  "summary": "新增第二父类并补充合作等级属性",
  "status": "editing",
  "revision": 4,
  "operation_ids": [],
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

草案只引用相对 base version 的只追加操作，不保存第二份权威完整本体。工作台需要预览时由模块应用当前有效操作生成覆盖图；发布时重新从当前 base graph 计算，不信任前端传回的 Turtle。

## 原子操作模型

```json
{
  "id": "operation_uuid",
  "action": "add_parent",
  "target_iri": "urn:...:合作伙伴",
  "before": null,
  "after": {"parent_iri": "urn:...:业务参与方"},
  "fingerprint": "sha256:...",
  "reason": "发现候选表现为业务参与者",
  "evidence_refs": ["candidate:...", "document-version:..."],
  "impact": {},
  "risk": "medium",
  "supersedes_operation_id": null,
  "created_at": "..."
}
```

首期支持：

- `create_term`
- `add_annotation`
- `remove_annotation`
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

标签与说明也以 `(subject, predicate, language, value)` 为最小增删粒度。修改某个中文标签不能覆盖英文标签或同谓词的其他值；替换由一条 `remove_annotation` 加一条 `add_annotation` 表达。

domain/range 在首期是“允许类型的 OR 集合”，不是交集：零个值不输出约束，一个值直接输出该 class，多个值输出唯一一个规范化的 `owl:unionOf` RDF list。编译器按稳定 IRI 排序重建整条 list；逻辑上的 add/remove 操作不得直接修改半条 RDF collection。

`advanced_rdf_patch` 只由 Turtle diff 生成，用于首期结构化编辑器未覆盖但现有 Turtle 能力已允许的 RDF/OWL/SHACL 语句。它必须展示增删三元组、标记为高风险并逐项审核；无法安全归组或影响 RDF collection 完整性的 patch 阻断提交。

## 状态机

草案状态：

```text
editing -> submitted -> reviewed -> published
   |           |           |
   |           |           +-> stale_base / stale_source
   |           +-> editing（request_changes）
   +-> closed（主动丢弃）
reviewed -> closed（全部操作被拒绝）
stale_base / stale_source -> editing（成功 rebase/刷新来源）
```

审核决定 action：

- `approve`
- `reject`
- `request_changes`

规则：

- 编辑中可增加、调整、撤销操作；每次成功命令增加 draft revision；
- 提交后操作不可直接编辑；`request_changes` 使草案回到 editing；调整会追加带 `supersedes_operation_id` 的新操作，旧操作和决定不可变；
- 所有非阻断操作有明确决定后，草案进入 `reviewed`；
- 全部拒绝则 `closed`，不创建本体版本；
- 发布只应用最新不可变决定为 `approve` 且 fingerprint 仍匹配的操作；
- 每次 submit、decide、validate 和 publish 都检查 base 是否为当前本体；不一致即变为 `stale_base`，不得继续决定或发布；
- discovery/candidate 来源版本变化时变为 `stale_source`，不得继续决定或发布；
- rebase 只允许对齐项目当前最新本体，重新计算 before、impact、risk、validation 和操作 fingerprint；fingerprint 改变的决定失效，完全不变的决定才可保留；
- rebase 将操作标为 clean/conflict/no-op；no-op 追加替代记录，conflict 必须人工调整；
- 发布必须把新 ontology version、draft 状态和 provenance 在一个事务提交。

## 新增、调整、停用和恢复

### 新增

- 类：IRI、标签、说明、0..N 父类；
- 对象关系：IRI、标签、说明、0..N domain、0..N range；
- 数据属性：IRI、标签、说明、0..N domain、一个受支持 datatype；
- 新建术语和每条结构边分别形成操作，审核人可以批准术语但拒绝某条建议父级边；
- 创建后 IRI 稳定，不因标签变化而改变。

### 调整

- 标签和说明使用逐值 `add_annotation` / `remove_annotation`，保留其他语言和值；
- 父级、domain、range 逐边 add/remove；
- datatype 使用 `set_datatype` 并保存 before/after；
- 调整前展示对后代、已有知识、关系约束、属性值、待审核候选和 SHACL 的影响；
- 不支持术语类型原地转换。

### 停用

`retire_term` 首先生成依赖报告：

- 类：直接子类边、关系 domain/range、属性 domain、SHACL/其他 RDF 引用、该类实例、以这些实例为端点的关系、未完成候选；
- 关系：正式关系记录、待审核关系候选、SHACL/其他 RDF 引用；
- 属性：正式属性事实、实体兼容 properties、待审核属性候选、SHACL/其他 RDF 引用。

停用不能静默级联，也不通过删除术语三元组来实现。`retire_term` 为目标增加弃用标记，并把所有依赖完整展示为影响；活动术语若仍依赖已停用术语，根据约束类型产生 warning 或阻断。需要改变依赖时必须在同一或后续草案中显式增加独立边操作。历史知识不删除、不自动改型，并继续按其写入时绑定的 `ontology_id` 校验。

### 恢复和替代

- 恢复旧定义：请求必须携带 `source_ontology_id`，从该不可变版本选择注解和结构边作为模板，生成 `restore_term` 与用户明确勾选的边操作；
- 替代：创建新术语，并以 RDF annotation `dcterms:isReplacedBy` 记录替代关系；
- 是否迁移知识事实是独立知识治理任务，不作为本体发布的隐式副作用。

## 校验与风险

校验顺序：

1. IRI、kind、标签和 datatype 语法；
2. 目标、父类、domain/range 引用存在性；
3. 重复边和自引用；
4. 完整多父类 DAG 循环检测；
5. RDF collection 和图结构完整性；
6. relation/attribute 约束；
7. SHACL 与新版本的预期写入契约；
8. 对现有知识、后代和待审核候选的影响；历史记录按自身 `ontology_id` 校验，只形成历史影响报告，不因不满足新版本约束而被错误阻断；
9. base ontology、来源版本与 draft revision 乐观锁。

严重级别：

- `error`：阻断提交或发布；
- `warning`：可提交，但审核和发布必须明确确认；
- `info`：提示范围和证据。

风险分类：

- 低风险：仅人工结构化的标签/说明；人工创建且无引用的新叶子术语；
- 中风险：新增父级边、增加 domain/range、影响后代但不删除语义；所有 discovery/AI/import 语义建议风险下限为中风险，置信度高也不能自动降为低风险；
- 高风险：停用、删除父级/domain/range、datatype 修改、影响正式知识、影响大量后代、`advanced_rdf_patch`；
- 阻断：循环、缺失引用、非法类型、无法安全拆分的复杂 RDF/SHACL 引用。

风险阈值由服务端常量和测试固定，首期定义为：受影响后代大于 50、约束引用大于 10 或待处理候选大于 20 时至少为高风险；任何正式记录影响至少为高风险。只有最新校验通过、没有 warning、来源不是 discovery/AI/import 的低风险操作可批量批准；一次最多 100 条。中高风险和停用不能批量批准。

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
- 层级总览：左侧虚拟滚动 DAG 视图按需展开，中央只显示选中路径和配置深度；同一 IRI 可在多个父类下显示为引用行，但所有引用共享同一 canonical selection，不复制术语状态；
- 每个类确定一条仅用于展示的主路径，并显示“另有 N 个父级”；面包屑可切换全部祖先路径。主路径不写回 RDF，也不改变多父类语义；
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
POST /api/projects/{p}/ontology-drafts/{draft_id}/close
POST /api/projects/{p}/ontology-drafts/{draft_id}/publish

GET  /api/projects/{p}/ontology-hierarchy/roots
GET  /api/projects/{p}/ontology-hierarchy/search
GET  /api/projects/{p}/ontology-hierarchy/{term_id}/children
GET  /api/projects/{p}/ontology-hierarchy/{term_id}/neighborhood
GET  /api/projects/{p}/ontology-matrix
```

所有变更请求包含 `expected_revision`；decide、rebase 和 publish 还包含 `expected_ontology_id`，publish 另含 `idempotency_key`。版本或来源冲突返回 409，并返回 `stale_base`/`stale_source` 与当前版本，不自动覆盖。

层级、搜索和矩阵读取接口均支持 cursor 分页、限制 page size、`ontology_id` 和可选 `draft_id` 覆盖层；children 返回 canonical IRI、是否引用行、子级数量和其他父级数量，前端不从一次性完整 summary 构造大树。

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

审核请求支持一条或多条 decision。每条包含 operation id、operation fingerprint、action 和 reason；批量时服务端只接受全部为低风险且最新校验通过且无 warning 的最多 100 条批准，不能依赖前端隐藏高风险项。`request_changes` 回到 editing，调整生成替代操作；`close` 必须保存 actor 和 reason。

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
2. 先做 RDF graph isomorphism/canonicalization，再按 subject/predicate/规范子图计算 additions/removals；blank node 的解析器临时 ID 绝不能作为稳定身份；
3. 可识别的 class/property/label/subClassOf/domain/range/datatype 转成结构化操作；
4. 首期只识别规范 `owl:unionOf` 和项目已支持的 SHACL shape blank-node 子图，并用 canonical subgraph hash 作为 provenance 指纹；
5. 其他没有 blank node 的安全变更可转换成 `advanced_rdf_patch`；不支持的 OWL restriction 或复杂 blank-node 子图直接阻断并给出原因，不能盲目降级为 patch；
6. blank-node collection 被整体识别和替换，不能产生半个 RDF list；语义等价、仅 blank-node ID 或序列化顺序不同的 Turtle 必须产生零操作；
7. 展示可读 diff 和原始 triples；
8. 进入统一审核，不直接发布。

## 开放发现与 Semantica

- 候选池继续保持开放，不要求未知候选先匹配当前本体；
- Semantica adapter 固定面向项目要求的 `semantica==0.6.7`：读取 `classes[].name` 与可选的单值 `classes[].parent`；若实际结果出现 `subClassOf`，仅作为兼容别名读取；未知或缺失 parent 视为根，不凭空创造父级；
- 调用 Semantica 归纳时保留 `build_hierarchy=True` 的单父级建议；多父类是本项目统一草案模型支持的人工/额外建议能力，不虚称 Semantica 0.6.7 一次产出多个父级；
- 归纳结果映射成 `create_term`、`add_parent`、relation/attribute operations；
- 每条建议携带 candidate/document refs、置信度和理由；
- 关系观察只作为证据，不自动推断为严格 domain/range，除非归纳器明确提供且通过审核；
- 多语言标签、多个根类和多个父类均被保留；
- Semantica 缺失时发现草案接口返回明确的可操作错误，不影响手工本体工作台。

实现时以真实或固定的 Semantica 0.6.7 输出 fixture 锁定 adapter 契约，防止文档字段与运行时结果漂移。参考上游文档：[Ontology Learning Guide](https://github.com/Hawksight-AI/semantica/blob/main/docs/guides/ontology.md) 与 [Semantica repository](https://github.com/Hawksight-AI/semantica)。

### 发现与候选发布副作用

统一模型不能丢失现有发现发布和知识候选提案的业务副作用：

- discovery 草案保存来源 document/candidate refs 及其 expected document version；发布准备阶段重新检查候选状态和文档版本，变化则进入 `stale_source`；
- discovery publish 在同一数据库事务内创建本体版本、物化批准/映射候选、更新 mapped/skipped 状态和文档 revision；任一步失败全部回滚；
- candidate 驱动的 ontology change 发布继续执行候选重校验，并在同一事务内更新源文档候选与本体版本；
- Milvus 同步在数据库提交后执行，通过带 fingerprint 的可重试 sync artifact/job 保证最终一致；向量库失败不得回滚已提交数据库事务，也不得重复发布本体版本；
- 需要 rebase/刷新来源时重新产生操作 fingerprint 和证据快照，受影响决定失效。

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

统一 provenance 复用现有权威表，但必须通过迁移扩展其约束，而不是把链路只塞进 draft JSON：

- 重建/扩展 `provenance_activities.kind` CHECK，保留已有 `retrieval`/`answer` 并加入 `ontology_draft`/`ontology_publish`；
- 扩展允许的 edge relation，加入 `published-from`、`contains-operation`、`decided-by`、`proposed-by`、`supported-by` 和 `based-on`；
- 稳定引用格式为 `ontology-draft:{id}`、`ontology-operation:{id}`、`ontology-decision:{id}`、`ontology-version:{id}`；
- 迁移必须原样复制现有 provenance rows，验证外键和 CHECK 后再切换表；迁移失败整体回滚；
- `ontology_operations` 和 `ontology_review_decisions` 是不可变业务事实，provenance edges 负责把它们与来源和发布版本连接，不复制第二套可变决定真值。

## 并发、错误和恢复

- draft 使用 `expected_revision` 乐观锁；
- publish 同时校验 `expected_ontology_id`；
- 两个草案基于同一 base 时允许并存，但先发布者使另一个变为 `stale_base`；
- rebase 对每个操作重新定位 before 值并分类为 clean/conflict/no-op；
- no-op 可自动标记 superseded；conflict 必须人工调整；
- 发布失败回滚 ontology version、draft 状态和 provenance；
- 重试相同 `idempotency_key` 的发布请求必须返回同一结果，不创建重复版本；同 key 不同 payload 返回 409；
- UI 始终保留未提交编辑，不因网络错误清空表单。

## 验证策略

### 当前基线

工作区在设计时为干净的 `master`，最近提交包含属性编辑及渲染。针对本体维护、变更提案和发现测试的结果为 `16 passed, 11 failed`；11 个失败均因当前 `.venv` 缺少 `semantica` 包而发生在 `_induce`，不是新增/调整/停用断言失败。完整测试还在一个异常 LLM 响应用例处出现进程退出，需要在实现前建立正确运行环境并定位。

项目文档要求使用包含 `local,semantica-runtime,test` extras 的环境，并单独安装 `semantica==0.6.7`。实现验证必须使用符合项目文档的环境，而不是把缺包失败误判为业务回归。

### 模块 interface 测试

- create class/relation/attribute；
- `base_ontology_id=null` 的首版本发布和 bootstrap 受控旁路；
- 多根、多父类、添加/删除父级边；
- 重复边、自引用和多跳循环阻断；
- 多语言 annotation 单值调整不覆盖其他语言；
- domain/range 的 0/1/N 规范 `owl:unionOf` 往返与安全重建；
- retire 的真实弃用、依赖展开、阻断、历史保留和 restore 去标记；
- 带 `source_ontology_id` 的 restore、RDF replacement 和 stale base/source rebase；
- raw Turtle 结构化 diff、等价 blank-node 图零 diff 与复杂 BNode 阻断；
- 来源下限、数量阈值、warning 和最多 100 条的服务端批量批准限制；
- 全拒绝、部分批准、原子发布和失败回滚；
- 双数据库连接并发 CAS、publish 幂等和同 key 异 payload 冲突；
- discovery publish 的候选物化、文档更新、事务回滚与提交后 Milvus 重试；
- candidate 来源文档变化触发 `stale_source`；
- provenance schema 迁移保留旧数据、完整链路和失败回滚；
- pending legacy artifact 惰性转换和失败复核状态。

### 接口与页面测试

- 所有写入口只创建/修改草案，不直接增加 ontology version；
- 项目 bootstrap、旧导入和精确恢复不能形成对外直接发布旁路；
- 旧路由适配器与新路由行为一致；
- 低风险批量审核、中高风险单审和阻断项无批准入口；
- 大列表虚拟化、搜索、按需展开和 stale 状态；
- DAG 引用行共享 canonical selection，主路径不改变 RDF；roots/children/search/neighborhood/matrix 均验证 cursor 分页和 draft overlay；
- `ontology-workbench.css`/JS 资源版本和加载顺序；
- 页面使用文本转义，不把标签、IRI、证据作为 HTML 注入；
- 键盘操作、焦点、窄屏和 reduced motion；
- 现有本体发现、知识审核、记录写入、RDF、Neo4j、provenance 和前端契约回归。

### 完成标准

- 任何外部本体写入口都不能绕过草案；
- 草案、操作、决定使用专用持久化，并由数据库 CAS 和原子发布事务保护；
- 类支持 0..N 父类且完整图保持 DAG；
- 新增、调整、停用和恢复都有 before/after、影响、决定与发布溯源；
- 已发布术语没有物理删除路径；
- 停用使用 `owl:deprecated`，恢复可逆，替代关系进入 RDF；
- 低风险可批量批准，中高风险和停用只能逐项审核；
- 现有发现、结构化维护、Turtle、影响、验证、SPARQL 和版本能力在新工作台中可达；
- 正确安装项目依赖后，完整测试套件通过；
- 页面样式与当前项目一致，且新增样式不会污染其他模块。

