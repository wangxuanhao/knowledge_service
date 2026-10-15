# Ontology Playground 源码调研

调研日期：2026-10-15（Asia/Shanghai）。本报告仅记录参考项目事实；本仓库的设计建议由配套调研另述。没有改动业务代码，也没有执行参考项目的 LLM 调用或发起任何 GitHub PR。

## 项目与复现基线

- 确认项目为 Microsoft 官方公开仓库：[microsoft/Ontology-Playground](https://github.com/microsoft/Ontology-Playground)，其主界面确实使用 “Natural Language Query (NL2Ontology)” 名称。
- 公开演示：[Ontology Playground](https://microsoft.github.io/Ontology-Playground/)。
- 已浅克隆到 `D:/workspace/references/Ontology-Playground`，固定源码 commit：`9ec82ec6605de15dd50c2254a169986c0b2bd7cc`。该提交标题为 `fix: validate AI-generated ontologies against Fabric IQ rules (#94)`，提交时间换算为北京时间是 2026-10-15 05:41:23。
- 先运行 codebase-memory-mcp 的 index_repository，再以 search_graph/get_code_snippet/search_code 发现并核实代码。索引项目名 `D-workspace-references-Ontology-Playground`。报告中的 GitHub 链接均固定到本次 commit。
- README 自称 Preview 和学习/设计工具；前端 package.json 的版本是 0.0.0。不能据此把它描述为完整生产知识平台。[README:1-14](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/README.md#L1-L14)、[package.json:1-5](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/package.json#L1-L5)

## 最关键的结论：NL2Ontology 查询没有调用 LLM

当前主界面查询过程：

1. 输入问题；
2. 前端人为延迟 600 毫秒；
3. 在浏览器里执行 `processQuery(input, currentOntology)`；
4. 返回模板文字、实体 ID、关系 ID、规则命中的解释；
5. 将 ID 交给全局状态，让图上的节点和边高亮。

直接证据：[QueryPlayground.tsx:35-58](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/components/QueryPlayground.tsx#L35-L58)。注释明确写着 “Simulate processing delay for realistic feel”。

实际 `processQuery` 是英文规则引擎：

- 小写化、去标点、简单单复数处理；
- Fourth Coffee 优先匹配内置演示响应；
- 用 `includes`、`startsWith` 等匹配 “what is”“list all”“how many”、实体名、关系名、属性名；
- 根据当前本体结构拼接定义、属性说明、关系说明和模式总览；
- 不匹配就返回建议问题。

证据：[queryEngine.ts:70-281](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/data/queryEngine.ts#L70-L281)。

因此，此处没有查询提示词、模型推理、向量检索、SQL/Cypher/SPARQL/GQL 编译和实际数据库执行链路。计数分支仅展示类似 `SELECT COUNT(*)` 的文字示例；实体列表分支明确说明真实部署才会查询实际记录。不能把演示文本理解为实例查询结果，更不能把 600 毫秒动画当作模型推理。

这是对 Fabric IQ NL2Ontology 概念的交互演示，不是 Fabric IQ 服务端 NL2Ontology 的开源实现。README 自身也称其为 preview：[README:102-106](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/README.md#L102-L106)。

## 另一个功能确实使用 LLM：用描述生成本体

仓库存在独立的可选 AI Builder。它与上述 Natural Language Query 面板是两条链路，必须区分。

| 项目 | Natural Language Query | AI Ontology Builder |
|---|---|---|
| 目标 | 解释/定位已有模式中的实体和关系 | 根据业务描述生成新的本体 JSON |
| 执行位置 | 浏览器本地规则 | 前端 → 独立 Azure Functions → Azure OpenAI |
| 调用 LLM | 没有 | 有 |
| 查询实例数据 | 没有 | 没有，输出也是模式 |
| 默认启用 | 主页面查询面板 | 构建开关默认 false |

AI Builder 的功能开关是 `VITE_ENABLE_AI_BUILDER === 'true'`，通过 lazy import 加载：[App.tsx:38-42](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/App.tsx#L38-L42)。

完整生成链路：

1. 前端发送 `POST buildApiUrl('generate-ontology')`，请求为 `{description}`；默认 API 基址是 `/api`，可由 `VITE_API_BASE` 指定独立后端。
2. Azure Functions handler 读取 `AZURE_OPENAI_ENDPOINT`、`AZURE_OPENAI_API_KEY` 和部署名；部署名未设置时使用 `gpt-4o-mini`。
3. 请求 Azure OpenAI 的 `openai/deployments/{deployment}/chat/completions`，API version 为 `2024-02-15-preview`。
4. messages 为固定 SYSTEM_PROMPT + 用户业务描述，temperature=0.3、max_tokens=4000、response_format=json_object。
5. 提取 choices[0].message.content，JSON.parse，并做基础结构检查，返回 `{ontology}`。
6. 前端再做严格结构解析与 `validateOntology`；成功后显示预览，用户可编辑 JSON。
7. Apply 时再次解析、校验，然后 loadOntology。

证据：[NLBuilderModal.tsx:239-305](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/components/NLBuilderModal.tsx#L239-L305)、[api.ts:1-12](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/lib/api.ts#L1-L12)、[generateOntology.ts:80-243](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/api/src/handlers/generateOntology.ts#L80-L243)。

提示词要求：名词抽成实体、动词抽成关系；每个实体必须有标识属性；生成 3–6 个属性；选择基数；输出实体颜色和图标；只输出匹配示例结构的 JSON。它是单次结构化生成，没有看到基于实例检索的规划/执行/纠错闭环。[SYSTEM_PROMPT:22-65](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/api/src/handlers/generateOntology.ts#L22-L65)

部署边界也很明确：官方 GitHub Pages 工作流只发布静态 build，不部署 api/；使用 AI Builder 需要额外配置函数后端及模型凭据。不能仅凭仓库包含 api/ 就推断公开演示已经调用模型。[README:192-255](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/README.md#L192-L255)

## 本体模型与可视化编辑真实能力

本体核心结构是 `Ontology {name, description, entityTypes, relationships}`，实体类型有属性、图标、颜色；关系有 from/to、基数、association/subclass 分类与可选关系属性。属性类型包含 string/integer/decimal/double/date/datetime/boolean/enum，可带 identifier/unit/enum values。[ontology.ts:3-50](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/data/ontology.ts#L3-L50)

设计器是“结构化编辑 + 实时图预览”的方式。状态层支持实体、属性、关系、关系属性的增删改与属性排序；图中点选实体/关系联动编辑对象，Graph/RDF 预览切换。它不等于已经实现任意白板绘图、拖线建边、后端实例写入和多人协同。[designerStore.ts:203-245](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/store/designerStore.ts#L203-L245)、[DesignerPreview.tsx:17-51](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/components/designer/DesignerPreview.tsx#L17-L51)

预览用 Cytoscape.js，普通图使用 fcose 布局，大图使用 grid。tap 节点/边只调用选择回调；数据修改来自设计器状态，再增量同步到图。大图还省略边文字，以减轻渲染成本。[DesignerPreview.tsx:72-265](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/components/designer/DesignerPreview.tsx#L72-L265)

撤销/重做通过深拷贝本体快照实现，历史上限为 50；不是服务端版本历史。[designerStore.ts:186-199](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/store/designerStore.ts#L186-L199)、[designerStore.ts:487-508](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/store/designerStore.ts#L487-L508)

本地校验针对 Fabric IQ 兼容要求：ID 不重复、实体名称规则、标识属性存在且类型是 string/integer、跨实体同名属性类型一致、关系端点存在等。这是 TypeScript 规则函数，不是执行 SHACL 推理或约束引擎。[validateOntology:38-136](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/store/designerStore.ts#L38-L136)

## 存储、导入导出与治理边界

- 当前设计器 draft 存在 Zustand 内存状态，初始化为空本体；该 store 没有持久化中间件/本体 autosave。页面导出文件与导入文件是保留工作的主要路径。浏览器 localStorage 的主题/导览状态不等于本体的后端保存。[designerStore.ts:248-264](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/store/designerStore.ts#L248-L264)
- RDF 导出即使有校验问题也可以进行，文件名附 draft；因此下载成功不代表本体满足发布条件。[DesignerActions.tsx:28-43](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/components/designer/DesignerActions.tsx#L28-L43)
- 有两种 RDF/XML profile：Playground round-trip 保留图标、颜色、枚举、单位、基数、绑定等编辑元数据；standards-oriented OWL + SHACL 则映射到标准词汇。[rdf-export-profiles.md:7-66](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/docs/rdf-export-profiles.md#L7-L66)
- 导入只支持选定子集。原始 ontology IRI、版本、license、owl:imports、未支持三元组不会完整保留；它不执行 SHACL validation engine。应保留原始文件，不能把 round trip 理解为任意 OWL 文档无损保存。[rdf-export-profiles.md:68-85](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/docs/rdf-export-profiles.md#L68-L85)
- 关系 optionality 不在其视觉模型中，所以标准导出不凭空生成关系 sh:minCount。基数语义映射值得借鉴，但并非完整约束模型。[rdf-export-profiles.md:51-55](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/docs/rdf-export-profiles.md#L51-L55)
- GitHub 一键贡献是 fork/branch/commit/PR 的目录贡献流程，不能代替业务台账的行级维护、审批、权限、并发控制、审计和生效版本治理。[README:63-69](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/README.md#L63-L69)

## 许可证与可借鉴范围

主代码为 MIT License，可使用、修改、分发，但复制重要代码时应保留版权及许可声明；按原样提供，无保证。[LICENSE:1-21](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/LICENSE#L1-L21)

目录还收录外部标准/社区本体，README 特意说明展示各自 license/source/version。MIT 主代码许可不能自动推及每份外部本体数据。[README:29-38](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/README.md#L29-L38)

可确认适合借鉴的是：本体的表单与图联动、点选检查器、可解释的节点/关系高亮、实时预览、撤销重做、校验反馈、导入导出、模板和学习引导。不能从该项目直接获得完整业务知识台账、生产实例图数据库或通用 NL2Ontology 查询执行能力。

## 补充：Path Finder 路径循迹

`PathFinderPanel` 从 `currentOntology.entityTypes` 选起点、终点，调用 `findShortestPath`，把返回节点和关系 ID 交给 `setHighlights`。面板展示带关系名的链和跳数，支持清空；没有找到路径时明确提示不存在有向路径。[PathFinderPanel.tsx:18-185](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/components/PathFinderPanel.tsx#L18-L185)

核心算法是在浏览器对关系建立出边邻接表，执行 BFS，返回按边数最少的一条有向路径。它不调用 LLM，也没有权重、全部路径或多条候选路径选择；同长路径的选择受关系遍历顺序影响。[pathFinder.ts:11-49](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/src/lib/pathFinder.ts#L11-L49)

这里连接的是本体中的实体类型，表示结构上可沿哪些关系连接，不证明实际业务实例存在该连接，也不构成原文来源证明或业务因果推理。源码实现为链条展示和整条路径高亮，没有在该面板发现逐步播放路径的控制。

## 本次验证范围

完成仓库下载、commit 固定、图索引和关键函数逐段源码检查，并交叉核对 README 与 RDF 导出契约。未安装依赖、未启动演示、未运行其测试；本报告不声称已验证运行体验、模型效果、生产性能或完整测试通过。GitHub repository metadata API 后续请求遭遇匿名 rate limit，因此没有将星标数/issue 数等易变指标作为成熟度依据。
