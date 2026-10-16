# ontology-driven-dev 项目调研

> 调研对象：[`sharptoolbox/ontology-driven-dev`](https://github.com/sharptoolbox/ontology-driven-dev)\
> 调研日期：2026-10-10（Asia/Shanghai）\
> 代码基线：[`49a9eff8b17349adaa860b355e82e6154c35dc82`](https://github.com/sharptoolbox/ontology-driven-dev/tree/49a9eff8b17349adaa860b355e82e6154c35dc82)\
> 材料范围：仅使用项目自身 README、技能说明、建模/架构文档、源码、Git 历史和 GitHub 官方仓库元数据；未使用二手介绍。本文不检查或评价当前本地项目，只给出可用于后续对照的外部项目画像。

## 一句话结论

`ontology-driven-dev` 不是通用“本体平台”或知识图谱基础设施，而是一套**让编码 Agent 按固定方法把业务需求逐步变成七类 YAML 模型，再基于随仓库提供的全栈底座构造管理系统**的技能包。它把“本体”用作业务软件设计的核心隐喻和可追溯语义来源，覆盖对象、行为、规则、角色权限、流程、查询报表和 UI；运行时则把这些 YAML 装入内存注册表，供元数据接口、流程/规则、AI 工具和 SQL 白名单使用。[README](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/README.md#L3-L18) [建模框架](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/references/ontology_modeling_framework_v9.md#L40-L65) [Go 注册表源码](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/go-techbase/internal/pkg/ontology/registry.go#L1-L32)

## 1. 项目目的与交付物

项目的目标是把一句或一段业务需求转成可运行的浏览器前后端管理系统，并强制保持“需求文档—七模型—实现”之间的可追溯关系。它本身首先是一个可被 Claude Code、Codex、Cursor 等编码 Agent 加载的 `SKILL.md`，同时附带方法论文档、模型范例、Python/Go 两套技术底座和一个销售合同管理完整样例。[README](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/README.md#L22-L55)

固定交付链为：

1. `<业务域>-需求规格说明书-V9.md`；
2. 七个 YAML 模型和 `manifest.json`；
3. 在复制技术底座后扩展得到的 `code-app/` 可运行系统。

项目允许仅执行“建模”或“构建”阶段，但完整流程按上述顺序推进。[SKILL.md](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/SKILL.md#L83-L98)

## 2. 这里的“本体”具体扮演什么角色

### 2.1 七模型是业务应用的设计语义模型

项目将业务软件拆成七个彼此分工的 YAML 元模型：

| 模型 | 承担的语义 |
|---|---|
| M1 对象 | 聚合、实体、属性、关联、字典和完整性约束 |
| M2 行为 | 原子命令/查询、前后置约束、同步联动 |
| M3 规则 | 跨对象、跨行为或可复用的业务决策 |
| M5 主体 | 参与者、角色、权限和行为授权 |
| M6 流程 | 协同流、审批流、任务、网关、子流程 |
| M7 查询报表 | 跨对象查询、统计和固定报表 |
| MU UI | 菜单、屏幕、布局、操作与行为调用关系 |

这些定义来自项目自己的七模型规范，而不是通用语义网标准。[建模框架](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/references/ontology_modeling_framework_v9.md#L55-L67)

### 2.2 模型是“设计时真相源 + 运行时元数据”，不是应用数据库本身

技能要求 DDL、服务、权限种子、流程定义、菜单和页面都能追溯到七模型；完整样例也逐项展示了 M1→表、M2→服务/API、M3→规则执行、M5→权限、M6→流程、M7→报表服务、MU→菜单/页面的映射。[SKILL.md](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/SKILL.md#L70-L80) [完整样例映射](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/code-app-example/README.md#L72-L80)

运行时注册表读取 `manifest.json` 指定的 YAML，把聚合、实体、行为、规则、角色、流程、报表和屏幕登记为内存中的 map，并从 M1 派生只读 SQL 表/字段白名单。[Python 注册表](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/techbase/backend/ontology/registry.py#L7-L83) [Go 注册表](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/go-techbase/internal/pkg/ontology/registry.go#L47-L100)

这意味着其“本体驱动”有两层：

- 设计时：用模型约束 Agent 生成/扩展代码，并做引用、一对一、无环等一致性检查；
- 运行时：把模型暴露为语义注册表，支持字典/元数据、规则/流程、AI 提示与工具边界。

但业务逻辑仍落实为 SQL、服务代码、流程引擎和前端页面，并非每次请求都由一个通用本体推理机动态执行。

### 2.3 它不属于 RDF/OWL 知识图谱路线

在该基线的 README、技能、建模规范、依赖和注册表源码中，项目没有声明 RDF、RDFS、OWL、SHACL、SPARQL、三元组存储或描述逻辑推理能力；实际模型格式是项目自定义 YAML，运行时实现是 map 注册表。因此更准确的类别是“ontology-inspired / ontology-driven application modeling”，而不是“语义网本体管理与推理平台”。这是对源码和依赖的事实归类，不是项目作者明示的宣传语。[Python 依赖](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/techbase/backend/requirements.txt) [Go 注册表结构](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/go-techbase/internal/pkg/ontology/registry.go#L18-L40)

## 3. 工作流与门禁

### 阶段一：需求探索

按八段式流程依次确认总体理解、业务对象、功能与规则、跨对象联动、协同/审批流、查询报表、角色权限和可选 UI 原型。每一阶段都必须暂停等待人工确认；企业专属信息必须保持待确认状态，直到用户明确回答。最终还要通过 47 项自检。[SKILL.md](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/SKILL.md#L24-L38)

### 阶段二：七模型建模

以需求文档附录 C 为确定性输入，按 M1→M5 角色→M3→M2→M7→M5 权限→M6→MU 建模；检查用户行为与 UI 引用、M2/M7 双向一对一、M6 引用存在和子流程调用无环。[SKILL.md](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/SKILL.md#L40-L52)

### 阶段三：应用构建

复制 Python 或 Go 技术底座，然后依次完成模型登记、DDL、字典、行为/规则服务、权限、流程、UI、AI 侧栏、联调和验收。这里的“生成”主要表现为让 Agent 按规范执行一条十步开发流水线；仓库没有展示一个把任意七模型一次性编译为完整应用的独立通用编译器/CLI。[README](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/README.md#L131-L146) [SKILL.md](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/SKILL.md#L60-L81)

## 4. 架构和实现技术

### Python 基线

- 后端：Python 3.10+、Flask、SQLite WAL、`sqlite3` 直连、PyJWT、PyYAML、`simpleeval`；
- 前端：React 18、TypeScript、Vite、Vanilla CSS、ECharts、react-flow；
- 形态：单体分层应用，含 REST API、服务层、轻量流程引擎、RBAC、工作台和本体注册表；生产构建可由 Flask 托管前端静态资源。

这些技术选型和目录职责由底座 README 明确列出。[Python 底座 README](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/techbase/README.md#L15-L55)

### Go 基线

- 后端：Go 1.27、Gin、PostgreSQL 16、goose 迁移；GORM 仅用于连接池/事务，数据访问主要为原生 SQL；
- 认证与基础设施：ZITADEL OIDC 或本地模式、可选 Redis、健康检查、Prometheus 文本指标、Traefik/Docker Compose；
- 前端：React 19、Ant Design 6、Redux Toolkit、React Router 7、Vite、`@xyflow/react`；
- 扩展：仓库还包含 NATS 和 Temporal 适配，但项目主建模规范仍把中小型单体同步系统作为目标边界。

[Go/Python 技术栈对照](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/go-techbase/README.md#L6-L23) [Go 技术选型](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/go-techbase/README.md#L35-L49) [建模框架目标边界](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/references/ontology_modeling_framework_v9.md#L40-L44)

### AI 交互架构

目标应用采用固定页面加右侧 AI 对话的 Hybrid UI。AI 获得本体摘要、可用 API/工具、页面导航和只读 SQL 白名单；动态 SQL 仅允许只读查询。需要注意，干净 Python 底座只保留 AI 布局占位且 `ai.enabled=false`，完整 AI 编排位于合同样例中，技能要求复制底座后再补齐。[Python 底座说明](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/techbase/README.md#L1-L12) [SKILL.md](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/SKILL.md#L77-L80) [样例目录](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/code-app-example/README.md#L94-L107)

## 5. 作者明确给出的边界与非目标

1. **目标是领域边界清楚的中小型业务软件和单体同步部署**，不是以分布式微服务为起点的通用企业平台。[建模框架](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/references/ontology_modeling_framework_v9.md#L40-L44)
2. **v9 建模框架不引入 EDA**；跨对象协作默认用同步 `syncTriggers` 或同步流程步骤。Go 底座后来加入 NATS/Temporal 适配，是实现扩展，不改变规范中这条基础边界。[建模框架](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/references/ontology_modeling_framework_v9.md#L42-L53)
3. **M7 不是 BI/指标平台模型**：不定义事实表、维度表、宽表、数据集市、OLAP Cube、ETL、自助分析语义或独立指标资产。[M7 边界](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/references/ontology_modeling_framework_v9.md#L1203-L1216)
4. **AI 动态数据能力默认只读**：禁止 AI 对话直接写数据；写操作要走固定页面/受控行为工具，动态 SQL 必须经过白名单、限量和审计。[架构安全边界](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/references/AI%20原生应用技术架构设计文档.md#L594-L623)
5. **一个行为只修改一个聚合**；跨聚合修改由同步联动或流程编排承担。[SKILL.md](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/SKILL.md#L100-L107)
6. **Go 管理底座仍有明确暂缓项**，包括部门/岗位/租户、系统设置、代码生成、OAuth2 应用、Webhook、文件、定时任务和告警等。[Go 底座差距清单](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/go-techbase/README.md#L187-L191)

另外，项目没有显式宣称支持 RDF/OWL、跨本体映射、语义推理、三元组图查询、协同本体版本治理或通用知识图谱服务；不能把这些能力从“本体驱动”这个名称中推断出来。

## 6. 成熟度判断

结论应拆成两个维度：

### 作为示范性方法/脚手架：完成度较高

- 仓库同时提供规范、可安装的 Agent 技能、七模型黄金样例、两套可运行底座和一个完整合同管理样例；
- 合同样例包含数据库、流程、报表、AI 只读查询和冒烟测试入口，README 声明自测覆盖合同审批、联动、冲销、报表和 AI 对话。[完整样例](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/code-app-example/README.md#L1-L27) [自测范围](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/code-app-example/README.md#L87-L92)
- Go 底座的工程化面较宽，已有迁移、OIDC、日志、健康检查、指标、容器化及可选 Redis/NATS/Temporal 等构件。[Go 技术选型](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/go-techbase/README.md#L35-L49)

### 作为稳定通用产品/生态：仍属早期

- 截至 2026-10-10，默认分支只有 6 个提交，首个提交日期为 2026-08-30；无 GitHub Release、无 tag；GitHub API 显示 1 位贡献者。仓库获得 433 stars、160 forks，说明关注度很高，但版本治理和多贡献者维护信号仍弱。[提交历史](https://github.com/sharptoolbox/ontology-driven-dev/commits/49a9eff8b17349adaa860b355e82e6154c35dc82/) [Releases API](https://api.github.com/repos/sharptoolbox/ontology-driven-dev/releases) [Tags API](https://api.github.com/repos/sharptoolbox/ontology-driven-dev/tags) [仓库元数据 API](https://api.github.com/repos/sharptoolbox/ontology-driven-dev) [贡献者 API](https://api.github.com/repos/sharptoolbox/ontology-driven-dev/contributors)
- 模型一致性规则写得很细，但仓库主要把它们作为 Agent 必须执行的门禁；从仓库内容看不到独立、完整的模型 schema 校验器/编译器产品。
- Python 和 Go 底座的能力并不完全对齐；例如 Python 干净底座不含完整 AI 模块，而 Go 底座已扩展到更复杂基础设施。选型前需要逐项验证目标路径，不能只看顶层“支持”描述。
- README 和代码中存在较强的特定组织/基础设施痕迹（如 OPIC、ZITADEL、生产集成约定），说明 Go 底座并非完全中性的通用模板，二次采用需要去组织化和安全复核。[Go README](https://github.com/sharptoolbox/ontology-driven-dev/blob/49a9eff8b17349adaa860b355e82e6154c35dc82/go-techbase/README.md#L1-L6)

## 7. 与常见“本体类项目”的类别差异

| 对照类别 | `ontology-driven-dev` 的差异 |
|---|---|
| RDF/OWL 本体编辑器、知识图谱平台 | 不以三元组、OWL 公理、SPARQL、SHACL 或推理为核心；以自定义七模型 YAML 约束业务应用代码与界面。 |
| 企业知识图谱/检索问答系统 | 重点不是采集多源知识、实体链接、图谱存储或 RAG 检索，而是从需求到 CRUD/审批/报表系统的工程交付。 |
| 领域驱动设计（DDD）工具 | 与 DDD 很接近，尤其强调聚合、行为、规则和事务边界；额外把角色、流程、查询报表、UI、AI 工具纳入同一可追溯模型集。 |
| 低代码/代码生成平台 | 有强模板、底座和模型到实现映射，但主要依赖编码 Agent 按 `SKILL.md` 开发；不是一个具备可视化设计器和确定性通用编译器的独立平台。 |
| BPM/RBAC/报表套件 | 项目包含这些能力，但它们是七模型的一部分并服务于完整业务应用，不是分别独立的专业引擎。 |
| Agent 开发工作流/技能包 | 这是最贴切的产品形态：提示规范、人工门禁、参考模型、技术底座与样例共同约束 Agent 产出。 |

## 8. 采用前最值得验证的事项

1. 七模型 YAML 是否有机器可执行的 JSON Schema、跨文件引用校验器和版本迁移工具，而不仅是文档门禁；
2. “模型为唯一真相源”在修改已有代码后如何反向校验，能否检测模型/DDL/API/UI 漂移；
3. 目标项目应选 Python 还是 Go 底座，以及两者功能差异、升级路径和维护责任；
4. 是否接受其强约束：单体同步优先、固定 UI 规范、固定默认字段、固定编号、AI 侧栏强制存在；
5. 安全上线前需更换示例账号/密钥、清理组织专属配置，并对 AI 工具、只读 SQL 校验和权限边界做独立审计；
6. 若需求是语义网/知识图谱，应另行验证 RDF/OWL/SPARQL/推理/图数据库等能力，因为该仓库当前没有提供这些层。
