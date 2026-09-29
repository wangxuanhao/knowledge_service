# Semantica 与开源本体编辑器调研

调研日期：2026-09-28

## 结论

Semantica 0.7.0 **已经实现了可视化本体管理的一部分**。它自带浏览器端 Explorer / Ontology Hub，可创建或加载本体，浏览类与属性，处理 SKOS、SHACL、跨本体对齐、健康检查和版本；还提供 `draft → proposed → approved/rejected → published` 状态流转。

但它目前不能直接替代本项目的本体工作台：Semantica 官方文档明确说明 Explorer 会话状态只在内存中，重启会丢失，也不在多个 worker 间共享；0.7.0 的发布实现只把新增类和新增属性写入图，删除和修改仅记入版本历史，不实际改写已有图。本项目又以 SQLite 中的不可变本体版本、草案操作、审核决定和 provenance 为权威，Semantica Explorer 则维护独立 `GraphSession` 和进程内 registry/draft/proposal/version。把两个界面并列开放会产生双写和两个真相源。

因此，本项目的推荐路线是：

1. **保留唯一的“本体工作台”入口**，把用户操作收敛为中文表单、层级选择和关系矩阵，不要求用户编写 Turtle/RDF。
2. **复用 Semantica 的生成、归纳和校验能力，不接管权威写入**。Semantica 输出必须转换成本项目的草案原子操作，经过现有校验、审核和发布。
3. 如果以后确实需要独立的多人专家编辑平台，首选评估 **WebProtégé**（OWL 专业编辑）或 **VocBench**（多语言术语与治理工作流）；通过 OWL/Turtle 导入导出同步成草案，避免直接双写。

## 仓库实际情况

### 当前 Semantica 集成不是 Ontology Hub 集成

项目依赖锁定为 `semantica[parse-docling]>=0.7,<0.8`，当前 conda 环境安装 0.7.0。应用实际调用的是：

- `semantic_extract`：实体、关系和属性候选抽取；
- `ContextGraph`：时间快照；
- `OntologyGenerator`：从发现候选归纳类、属性与层级；
- `DuplicateDetector` / `EntityMerger`：实体消歧和合并建议；
- `DoclingParser`：文档解析。

仓库没有导入或启动 `semantica.explorer`，也没有把 Semantica Ontology Hub 的 API 挂到当前 FastAPI 应用。因此“已经安装 Semantica 0.7.0”不等于项目已经启用了它的可视化编辑器。

代码证据：

- `pyproject.toml:20-24`
- `knowledge_service/integrations/semantica_adapter.py:27,155-249,377-379`
- `knowledge_service/services/ontology_discovery.py:342-379`
- `knowledge_service/services/governance.py:43,183`
- `knowledge_service/integrations/document_parser.py:105-112`

### 当前项目已具备治理后端，缺的是低门槛入口

项目已经有：

- 手工草案创建接口：`POST /api/projects/{p}/ontology-drafts`；
- 类、关系、属性的原子变更；
- 多父类、domain/range、datatype、注解、停用、历史恢复；
- 草案提交、逐项决定、校验、rebase 和幂等发布；
- 层级、邻域和关系矩阵读取接口；
- 不可变版本、影响检查和 provenance。

直接问题在前端：

- 草案列表提示“可创建手工草案”，但没有按钮调用草案创建接口；
- `sendCommand()` 要求先有草案，所以默认本体创建后用户无法自然开始编辑；
- “新增本体对象”要求用户手填绝对 IRI，父类、Domain、Range 也要求粘贴 IRI；
- 项目创建的“加载默认本体”会把内置 `default_ontology.ttl` 直接发布，而该文件明确是“美团规则知识图谱本体”，不是通用模板。

代码证据：

- `knowledge_service/api/ontology_drafts.py:22-125`
- `knowledge_service/web/ontology-workbench.js:97-117,163-180`
- `knowledge_service/api/projects.py:25-35`
- `knowledge_service/web/projects-view.js:181-205`
- `knowledge_service/resources/default_ontology.ttl:1-17`

## Semantica 0.7.0 到底提供什么

Semantica 官方 0.7.0 文档把 Explorer 定义为浏览器工作台，Ontology Hub 包含可视化编辑、SHACL Studio、对齐、SKOS、健康检查和版本。它支持三种创建方式：从空白创建、从样例数据生成、从文本生成；也支持 URL/内容加载及多种 RDF 格式。

治理 API 已有明确状态机：

```text
PATCH /api/ontology/draft
        ↓
POST /api/ontology/propose
        ↓
approve / reject
        ↓
POST /api/ontology/proposals/{id}/publish
```

它值得复用或借鉴的能力：

- 从数据或文本生成本体候选；
- OWL/RDF 导入导出；
- SHACL 生成与校验；
- 跨本体 alignment；
- 版本 diff 和基本审批状态机；
- React 可视化交互模式。

必须注意的限制：

- Explorer 文档明确说明 session state 为内存态，重启丢失且不跨 worker；
- 官方本体指南明确说明 publish 只应用 `added_classes` 和 `added_properties`，删除和修改只记录、不实际改图；
- 其 registry、draft、proposal、version 在 0.7.0 源码中保存在 `request.app.state` 字典；
- 它没有本项目的项目级双时态账本、乐观锁、稳定 provenance 和现有知识影响契约；
- 当前项目没有安装 `semantica[explorer]` extra，也没有启动独立 Explorer 服务。

所以 Semantica Ontology Hub 可以作为原型和交互参考，也可以在隔离环境中给专家试用，但不宜直接成为本项目正式写入口。

官方来源：

- [Semantica 0.7.0 README](https://github.com/semantica-agi/semantica/blob/v0.7.0/README.md)
- [Semantica 0.7.0 Explorer 官方参考](https://github.com/semantica-agi/semantica/blob/v0.7.0/docs/reference/explorer.md)
- [Semantica 0.7.0 本体指南](https://github.com/semantica-agi/semantica/blob/v0.7.0/docs/guides/ontology.md)
- [Semantica 0.7.0 Ontology Hub 路由源码](https://github.com/semantica-agi/semantica/blob/v0.7.0/semantica/explorer/routes/ontology.py)
- [Semantica 0.7.0 依赖与 Explorer extra](https://github.com/semantica-agi/semantica/blob/v0.7.0/pyproject.toml)

## 成熟开源方案比较

| 方案 | 核心定位 | 可视化编辑 | 协作/治理 | 集成方式 | 对本项目的判断 |
|---|---|---|---|---|---|
| Protégé Desktop | 专家桌面 OWL 2 编辑器 | 强，插件丰富 | 单机为主 | OWL/Turtle 文件交换 | 适合专家离线精修，不适合作为业务用户入口 |
| WebProtégé | 浏览器多人 OWL/OBO 编辑 | 强，覆盖常用 OWL 构造 | 权限、讨论、通知、完整变更历史 | 自托管；优先文件/版本交换 | 最成熟的专业 OWL 外部编辑器 |
| VocBench 3 | OWL、SKOS、OntoLex、通用 RDF 协同治理 | 强，尤其适合多语言术语 | 用户、组、角色、历史、验证、发布工作流 | Semantic Turkey Web API；RDF4J/GraphDB | 若需要企业式词表治理，最值得独立评估，但部署和概念较重 |
| OntoPortal / BioPortal | 本体仓库、目录、检索和版本发布 | 主要浏览，不是类/公理设计器 | 提交版本、元数据、映射、notes | REST API、widgets、文件/URL 提交 | 可作资产目录，不解决“不写 TTL 的设计界面” |
| GraphDB Workbench | RDF 数据库管理与查询 UI | 能看图、改 RDF、SPARQL update，但不是本体工程 UI | 仓库权限和运维管理 | SPARQL/RDF4J/REST | 适合作为三元组库控制台，不应替代本体工作台 |
| Semantica 0.7 Ontology Hub | Python 图谱工具包自带工作台 | 有创建、导入、SHACL、对齐和基本编辑 | 有内存态草案/提案/批准/发布 | REST API / 独立 Explorer | 最贴近现有技术栈，但生产持久化和权威写入必须由本项目负责 |

### Protégé / WebProtégé

Protégé Desktop 是 BSD-2-Clause 的开源 OWL 2 编辑器，拥有插件架构。WebProtégé 是免费的开源协同本体开发环境，支持 OWL 2/OBO、常用 OWL 构造、完整变更历史、共享权限、讨论、watch/邮件通知，以及 RDF/XML、Turtle、OWL/XML、OBO 等上传下载。官方仓库提供 Web 应用与 Docker 部署，并通过持久卷保存 WebProtégé 和 MongoDB 数据。

这套工具最适合“本体专家编辑”，但用户仍需理解类、公理、限制等 OWL 概念。对当前产品，较稳妥的集成是：导出当前发布版本 → 在 WebProtégé 修改 → 导回本项目生成 diff 草案 → 在本项目审核发布。不要让 WebProtégé直接写本项目数据库。

官方来源：

- [Protégé Desktop 官方仓库](https://github.com/protegeproject/protege)
- [Protégé BSD-2-Clause 许可](https://github.com/protegeproject/protege/blob/master/license.txt)
- [WebProtégé 官方仓库与能力说明](https://github.com/protegeproject/webprotege)
- [WebProtégé 许可](https://github.com/protegeproject/webprotege/blob/master/license.txt)

### VocBench 3

VocBench 官方定位是多语言、多人协作的 OWL、SKOS/SKOS-XL、OntoLex 和通用 RDF 管理平台。它有结构化 OWL 编辑、Manchester Syntax 辅助、项目/用户/角色、历史、验证、发布工作流、通知、SPARQL、对齐和完整 Web API。官方部署要求 Java 17，底层使用 Semantic Turkey，并建议连接独立 RDF4J 兼容 triple store。

它比 WebProtégé更接近“组织级术语治理平台”，API 也更适合服务集成，但部署较重、界面面向专业用户。只有在多人、多语言、角色分工、正式发布流程成为核心需求时，才值得引入；否则本项目已有治理模型，重复建设和双真相源风险更高。

官方来源：

- [VocBench 官方主页](https://vocbench.uniroma2.it/)
- [VocBench 3 文档与安装](https://vocbench.uniroma2.it/doc)
- [VocBench 用户手册和 Web API](https://vocbench.uniroma2.it/doc/user)
- [VocBench OWL 编辑说明](https://vocbench.uniroma2.it/doc/user/owl_editing.jsf)
- [VocBench 项目与权限](https://vocbench.uniroma2.it/doc/user/projects.jsf)
- [VocBench 系统部署](https://vocbench.uniroma2.it/doc/sys)

### OntoPortal / BioPortal

OntoPortal 是 BioPortal 技术的社区维护发行版，定位是 ontology repository。它擅长提交和保存本体版本、搜索、类层级浏览、映射、标注、元数据和 REST API。官方“更新本体”说明中的编辑是编辑提交元数据或新增提交，OWL 内容仍主要通过文件或 URL 交付。

因此它适合未来的本体资产目录或跨项目检索中心，不适合拿来补当前缺少的可视化建模表单。

官方来源：

- [OntoPortal 官方文档](https://ontoportal.github.io/documentation/)
- [本体管理与提交](https://ontoportal.github.io/documentation/administration/ontologies)
- [本体更新说明](https://ontoportal.github.io/documentation/user_guide/ontology_lifecycle/update/OntoPortal)
- [OntoPortal 开发者指南](https://ontoportal.github.io/documentation/user_guide/developer_guide/OntoPortal)

### GraphDB Workbench 与 TopBraid

GraphDB 官方把 Workbench 定义为 Web 管理界面，用于导入、转换、浏览、管理、查询和导出 RDF，并提供 SPARQL 与 REST API。Workbench 前端仓库本身使用 Apache-2.0，但 GraphDB 引擎和企业功能另有许可。它不是面向业务用户的本体类、公理、术语审批设计器，因此不应作为本项目的编辑前端。

TopBraid EDG/Composer 功能很强，但官方法律页同时列出产品 EULA 和“产品内开源组件清单”；后者不等于整个产品以开源许可证发布。按本次“只考虑开源替代”的边界，不把 TopBraid 作为候选。其开源功能方向由 WebProtégé（OWL）和 VocBench（词表/治理）覆盖。

官方来源：

- [GraphDB Workbench 官方文档](https://graphdb.ontotext.com/documentation/11.4/working-with-workbench.html)
- [GraphDB Workbench 源码](https://github.com/Ontotext-AD/graphdb-workbench)
- [GraphDB Workbench Apache-2.0 许可](https://github.com/Ontotext-AD/graphdb-workbench/blob/master/licenses/LICENSE)
- [TopBraid 官方法律与许可页](https://www.topquadrant.com/support-documents/legal)

## 本项目具体怎么开发

### 产品边界：不要再开一个平级“本体编辑器”菜单

保持一个“本体工作台”，在内部明确四种开始方式：

1. **从空白开始**：创建手工草案；系统按项目自动生成 namespace 和术语 IRI。
2. **使用行业模板**：先预览模板的适用领域、类/关系/属性数量，再复制为草案；不直接发布。
3. **从文档发现**：Semantica 从项目文档生成累计候选草案，用户用中文审核。
4. **导入已有本体**：上传 Turtle、RDF/XML、OWL/XML 或 JSON-LD，转换为相对当前版本的 diff 草案。

项目创建页只选择起点，不承担完整编辑。创建成功后跳转“本体工作台 · 设计”，后续统一走设计、审核、校验、发布。

### 面向不会写 TTL 的交互

- “类”显示为“对象类型”，“ObjectProperty”显示为“关系”，“DatatypeProperty”显示为“字段/属性”；
- IRI 默认隐藏在“高级设置”，由 `项目 namespace + 稳定机器名` 自动生成；允许专家覆盖，但发布后不可随意改义；
- 父类、Domain、Range 使用可搜索选择器和中文标签，不能要求复制 IRI；
- 新建关系采用一句话向导：“什么类型 — 通过什么关系 — 指向什么类型”；
- 新建属性采用：“哪个类型 — 有什么字段 — 数据类型/是否必填/是否多值”；
- 所有改动先显示自然语言 diff，例如“商家 新增父类 主体”，底部再展开 RDF 技术详情；
- Turtle 编辑器移入“高级工具”，不作为主流程。

### 推荐实施顺序

#### P0：修复当前可达性缺口

1. 在设计阶段加“新建手工草案”按钮，调用现有 `POST /ontology-drafts`；
2. 默认基于当前发布本体创建草案；没有版本时允许空基线草案；
3. 新建对象时只要求中文名、类型和定义，自动生成 IRI；
4. 父类/Domain/Range 改为检索选择器；
5. 创建项目的“加载默认本体”改为“使用美团规则模板”，显示预览和明确领域，不再叫通用默认本体；
6. 模板先生成草案，再由用户审核发布，避免创建项目即静默发布。

#### P1：补齐非 TTL 工作流

1. 新增模板目录及模板元数据：名称、领域、说明、版本、namespace、术语统计；
2. 增加 OWL/Turtle/RDF/XML/JSON-LD 导入，全部转成现有结构化 operations；
3. 增加当前版本和草案导出，供 Protégé/WebProtégé/VocBench 使用；
4. 自然语言 diff、图形预览、冲突提示都从现有草案/校验数据生成。

#### P2：复用 Semantica，而不是嵌入第二套状态机

1. 用 Semantica 的 `OntologyGenerator` / text generation 产生候选；
2. 把候选映射为 `create_term`、`add_parent`、`add_domain`、`add_range`、annotation 等现有命令；
3. 保存来源文档、置信度和生成器版本；
4. 仍由本项目完成乐观锁、逐项审核、SHACL/图校验、影响分析和发布；
5. 可参考或合法复用 Semantica MIT 前端交互，但不直接复用其内存 registry/draft/proposal 作为业务状态。

#### P3：确有多人专家协作时再接外部平台

- 以 OWL 专家编辑为主：评估 WebProtégé；
- 以多语言词表、角色、发布流程为主：评估 VocBench；
- 首期只做“导出版本 / 导回草案”，不要做双向实时同步；
- 若未来做 API 同步，仍规定本项目发布接口是唯一正式写入口，外部平台只产生候选版本。

## 最终判断

本项目不缺另一套后端本体库，缺的是把现有草案治理能力包装成业务用户能理解的可视化入口。最小且正确的整改是先补“手工草案”入口、自动 IRI、中文选择器和模板预览；Semantica 负责建议，当前工作台负责权威治理。只有当专业本体团队真的需要多人深度 OWL 建模时，再把 WebProtégé或 VocBench作为外部专家工具接入。
