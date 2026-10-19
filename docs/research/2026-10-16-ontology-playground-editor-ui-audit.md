# Ontology Playground 本体编辑与展示界面核查

日期：2026-10-16（Asia/Shanghai）。范围：为知识服务方案修订核对 **本体类型、属性、关系和继承的编辑／展示方式**，不以 NL2Ontology 查询面板代替编辑器研究。本次按 research skill 做一手源码核查；本文件中的“建议”是当前项目的设计建议，不是参考项目已实现的功能。

## 基线与结论

- 参考仓库：`D:/workspace/references/Ontology-Playground`，当前本地 HEAD 为 `9ec82ec6605de15dd50c2254a169986c0b2bd7cc`。固定版本入口：[Microsoft 官方源码](https://github.com/microsoft/Ontology-Playground/tree/9ec82ec6605de15dd50c2254a169986c0b2bd7cc)。本次没有拉取远端，故“当前”指已克隆的本地提交，不声称为远端最新提交。
- 已先使用 codebase-memory-mcp `search_graph` 发现 `OntologyDesigner`、`EntityForm`、`RelationshipForm`、`DesignerPreview`、`OntologyGraph`、`InspectorPanel`，再用 `get_code_snippet` 和对应实际文件核对。索引项目名为 `D-workspace-references-Ontology-Playground`；本次图查询正常返回，无需以文本搜索替代代码发现。CSS 样式定位与既有调研文档读取属于非代码／字符串核对。
- **最值得借鉴的是“可展开类型卡片 + 类型内属性行 + 显式关系表单 + 图形选择联动 + RDF 视图”。** 参考项目的 Designer 是左右两栏；浏览首页另有右侧 Inspector。不能把两者混称为已存在的一套三栏编辑器。[E1][E2]
- **参考项目画布上的节点仍是彩色圆形和名称，属性并不直接列在画布节点里。** 类型的属性列表位于 Designer 的左侧展开卡片及浏览页 Inspector。若本项目采用“画布类卡片直接列属性”，这是借鉴后的增强，应明确写成我们的设计。[E3][E4][E5]
- **Fabric IQ 的名称和标识约束属于参考应用的业务要求。** 不应把 26 字符、仅英文／数字等规则和“每个类必须指定 string/integer 标识属性”直接移植为 OWL 的通用规则。[E6]

## 1. 实际信息架构：浏览与编辑有明确区别

| 区域 | 浏览首页 | 全屏 Designer |
| --- | --- | --- |
| 顶部 | Header 提供 Catalogue、Designer、Import / Export 等入口 | Back、本体名称、说明、撤销／重做、New、Validate、导出 profile、Export RDF、Load in Playground、Submit to Catalogue |
| 左侧 | Quest 教学任务 | 可滚动侧栏；空本体时显示模板；随后是校验问题、Entity Types、Relationships |
| 主区域 | Cytoscape 本体图 | Graph / RDF 两个预览页签 |
| 右侧 | 统计、Path Finder、Search & Filter、Inspector、Query | 没有独立右侧 Inspector；图中选中对象后定位左侧表单 |

证据：[E1][E2][E7]。Designer 侧栏宽 380px、最小 320px；预览填充剩余宽度。小于 900px 时改为上下布局，表单侧栏最大 50vh、预览最小 40vh。这是源码中的布局设置，未作设备实测。[E8]

对本项目的启示：应以本体编辑作为完整工作区，在稳定位置暴露“类型／属性／关系／继承”，并让表单与图保持同一选中对象。可以沿用现有左侧术语、中央画布、右侧编辑器布局，但不要仅在原页面追加一个问答框，就称为完成了此参考。

## 2. 实体类型卡片与属性编辑

Entity Types 区带总数和 Add。每个实体类型卡片的标题行由展开箭头、可配色图标、类型名称、属性数量和删除按钮组成；选中边框变蓝。多个类型卡片可以同时展开。新增类型会放到列表开头并自动展开；从图上点选类型会展开并滚动到相应卡片。[E4][E9]

展开后按照以下顺序编辑：[E4]

1. 类型名称、说明。
2. 图标网格、颜色网格。
3. Properties 标题、数量和 Add。
4. 每个属性占一行：拖动排序手柄、属性名称、数据类型选择、钥匙标识切换、删除。

类型选择包含 `string / integer / decimal / double / date / datetime / boolean / enum`。属性名称／类型变更直接调用 store 更新；没有逐个卡片的 Save 按钮。新增实体默认含一个 `id: string` 标识属性，属于该参考项目的初始化策略。[E4][E9]

需要保留的差别：数据模型还可存 `unit`、`values` 和属性 `description`，但本次核对的 EntityForm 只有名称／类型／标识／排序等控件，没有对应的单位、枚举选项、属性说明、必填、最小／最大值或任意 OWL restriction 表单。不能因接口字段存在，就把完整约束编辑算作已实现。[E4][E10]

**建议用于本项目的类型展示卡：** 标题显示中文标签、术语缩写／IRI、父类与属性数；主体展示属性名称和数据类型，直接属性与继承属性有可识别标记；收起时保留关键属性摘要，展开时再看全部。编辑器应支持数据类型对应控件与适用约束的来源说明。以上是设计建议，画布内卡片和继承属性分组均不是此提交的原有节点样式。

## 3. 关系、继承和基数

关系以独立卡片列在类型列表后面，标题呈现“来源类型 → 目标类型”。选中后才显示表单：关系名称、From、To、Cardinality、说明和关系属性。基数选项用 `1 : 1 / 1 : N / N : 1 / N : N` 表达，内部存四种枚举。关系属性是“名称 + 类型文本框”，不等同于经过严格类型系统约束的 OWL 属性编辑。[E11]

若导入关系的 `kind` 为 `subclass`，表单会明确说明它是 `rdfs:subClassOf`，端点改称 Subclass / Parent class，隐藏普通关系名称编辑、Cardinality 和 Attributes。图中继承边使用虚线，普通关联使用实线，两者有方向箭头。导出契约也明确继承保留为类层次声明。[E3][E11][E12]

此表单的 Add 按钮只调用普通 `addRelationship(from, to)`，store 默认名称为 `relates_to`、基数 one-to-many；表单没有 `kind` 选择器，也没有独立“添加继承”动作。现有继承边能被识别和编辑端点，不应据此宣称已实现完整的继承创建体验。[E11][E13]

**建议用于本项目：** 将“设定父类”和“添加业务关系”作为两个语义清晰的动作；父类、关联关系和数据属性分别编辑。普通关系行显示关系名、起点类、终点类、方向、实际已有的约束及说明。需要基数时读取本项目的 OWL／SHACL／业务约束，不把 `1:N` 自动理解为“至少一个”：参考项目自己的视觉模型也不记录 optionality，其标准导出不凭空生成关系 `sh:minCount`。[E12]

## 4. 选择检查器与展示联动

浏览首页 Inspector 未选中时给出“点击实体类型或关系查看”的空态；选中类型时展示名称／说明、属性名称、标识 ID、单位和数据类型，随后列出关联关系的方向、对端类型和基数，有绑定时再展示数据表与列映射。选中边时展示来源 → 目标、关系说明、基数；继承边的标题与语义区域则变为 Class Hierarchy / `rdfs:subClassOf`。[E5]

Search & Filter 以类型名、说明、属性名匹配类型，以关系名、基数和端点 ID 匹配关系。结果行显示类型名和属性数量，点击后同时选择并高亮对象；这有助于从业务术语找到复杂图中的目标，而不要求用户逐个找圆点。[E14]

浏览图选择类型时突出该类型、一跳相邻边和邻居，其他元素淡化；双击类型切换局部聚焦。缩放、适配视图、重排和 PNG 下载属于浏览图控制。Designer 的预览只在节点／边 tap 时回调选择表单对象，其选择能力不应与浏览图的全部工具合并表述。[E3][E15]

**建议用于本项目：** 点选类卡片或属性行后，右侧编辑器显示同一术语；检查器应支持明确的“查看／编辑草案”状态，并在类型、关系、属性之间保持一致的信息顺序。事实实例的来源和修订历史应由当前项目的事实详情承担，参考应用的 Data Bindings 不能充当证据溯源。

## 5. Graph、RDF、历史与校验

| 能力 | 当前提交的可确认行为 | 本项目可借鉴／需补足 |
| --- | --- | --- |
| Graph / RDF | Graph 实时读取设计器 ontology；RDF 可复制、进入 Edit RDF 文本框、Load into Designer；成功后切回 Graph；解析失败有错误区 | 主视图保持表单编辑；RDF/Turtle 等源码视图作为高级入口，写回时走同一草案命令和语义校验 |
| 撤销／重做 | 顶栏按钮与 Ctrl/Cmd+Z、Ctrl/Cmd+Shift+Z；本体 JSON 深拷贝快照，最多 50 层 | 可借鉴即时撤销；需区分会话撤销、服务端版本和发布历史 |
| 校验反馈 | 局部名称／标识类型即时提示；全局 Validate 或 Export / Load / Submit 调用 `validate()`；问题汇总可点击定位对象；成功提示显示 3 秒 | 每个校验项可定位是好模式；本项目需标清规则来源、严重度、受影响知识和当前校验状态 |
| 导出与使用 | Export RDF 即使有问题也可下载，文件名加 draft；Load in Playground 和 Submit to Catalogue 遇到校验错误会停止 | 草稿导出与正式发布应具有不同文案和门槛 |

证据：[E7][E16][E17][E18]。**“实时预览”成立；“完整全局实时校验”不宜照抄 README 概括。** store 的编辑动作没有自动调用 `validate()`，全局错误列表也不是每次键入都重新推理。[E9][E13][E17]

Designer draft 与主 appStore 分离，Load / Export 前不影响主图。draft 是 Zustand 内存状态；该 store 未使用本体持久化中间件。快照历史是本地编辑便利设施，不能代替本项目的草案、审核、发布、版本冲突和审计。[E9][E18]

## 6. 导入导出的适用边界

Designer 顶栏明确提供 RDF 导出；RDF 页签提供粘贴／编辑 RDF/XML 并载入草案。主页面另有文件导入弹窗，支持 `.rdf/.owl/.iq`；JSON 导入和 JSON/YAML/CSV 格式选择受 `VITE_ENABLE_LEGACY_FORMATS === 'true'` 控制，因此不要无条件写成默认开放所有格式。[E7][E16][E19]

两种 RDF/XML profile 值得借鉴其契约表达：Playground round-trip 用自己的元数据词汇保留图标、颜色、枚举、单位、基数和绑定；standards-oriented OWL + SHACL 使用标准词汇表达可支持的类、属性、顺序、枚举、标识、基数及关联类。它们都只覆盖支持子集，模型不保留原始 ontology IRI、version、license、`owl:imports` 及未支持三元组，也不运行 SHACL validation engine。应保留原始导入文件；不能将“可导入 OWL”理解为任意 OWL 无损编辑。[E12]

**建议用于本项目：** 导入先显示格式、可识别术语、未支持内容与差异，再进入草案；保留源文件和 IRI 身份。不要因参考项目用名称生成 IRI，就将本项目的术语身份也绑定到可编辑中文标签。[E12]

## 7. 方案和原型应明确体现的内容

以下为本次审查后的设计建议，供主方案与 mockup 采用：

1. **本体建模作为主体验。** 首屏能看到类型及其属性，而不是只有关系圆点和自然语言输入。优先把类型卡片、属性清单、继承和关系方向做清楚。
2. **选择即定位，编辑有上下文。** 左侧术语检索、中央图或卡片、右侧属性编辑器保持一个选择状态；直接属性、继承属性、关系和类层次分别标注。
3. **关系和继承有各自入口。** 连线后必须看清起点、终点、关系类型／父类与方向；基数应是可解释的真实约束。
4. **显式草案变更区。** 以未发布状态、变更条目、影响范围和可点击校验结果支撑提交审核；用户能区分移动节点与修改语义。
5. **高级源码视图保留。** 表单与图是常用入口，原始 RDF/Turtle 和差异可供高级用户检查；所有写入复用当前项目的草案治理通道。
6. **将参考项目没有的能力标为新增。** 画布内属性卡、继承属性分组、完整 OWL/SHACL 约束表单、后端持久化与正式发布治理均需本项目另行实现或适配。

## 8. 源码证据与可直接打开的素材

所有本地路径均对应上述固定 commit；链接行号为本次核对位置。范围写在标签内，链接定位该范围起点。

| 编号 | 本地一手依据 | 用途 |
| --- | --- | --- |
| E1 | [App.tsx:182–210](D:/workspace/references/Ontology-Playground/src/App.tsx:182) | Designer 是独立全页；首页右栏组件组织 |
| E2 | [OntologyDesigner.tsx:18–112](D:/workspace/references/Ontology-Playground/src/components/OntologyDesigner.tsx:18) | 顶栏、左表单／右预览、图选表单、快捷键、大型本体拒绝全量编辑 |
| E3 | [DesignerPreview.tsx:72–265](D:/workspace/references/Ontology-Playground/src/components/designer/DesignerPreview.tsx:72) | 圆形预览节点、虚线继承、tap 选择、增量图同步 |
| E4 | [EntityForm.tsx:27–258](D:/workspace/references/Ontology-Playground/src/components/designer/EntityForm.tsx:27) | 卡片展开、定位、字段、类型选择、属性编辑与拖动排序 |
| E5 | [InspectorPanel.tsx:22–236](D:/workspace/references/Ontology-Playground/src/components/InspectorPanel.tsx:22) | 只读检查器的类型／属性／关系／继承／绑定展示 |
| E6 | [designerStore.ts:19–136](D:/workspace/references/Ontology-Playground/src/store/designerStore.ts:19) | Fabric IQ 规则；与 OWL 通用语义的边界 |
| E7 | [DesignerActions.tsx:16–112](D:/workspace/references/Ontology-Playground/src/components/designer/DesignerActions.tsx:16) | 工具栏、动作校验、两种导出 profile、draft 文件名 |
| E8 | [app.css:2056–2175](D:/workspace/references/Ontology-Playground/src/styles/app.css:2056)、[app.css:4286–4322](D:/workspace/references/Ontology-Playground/src/styles/app.css:4286) | 侧栏尺寸、Graph/RDF 页签、移动布局 |
| E9 | [designerStore.ts:248–381](D:/workspace/references/Ontology-Playground/src/store/designerStore.ts:248) | draft 内存初态、新增类型／默认属性、属性编辑与排序 |
| E10 | [ontology.ts:3–50](D:/workspace/references/Ontology-Playground/src/data/ontology.ts:3)、[EntityForm.tsx:6–8](D:/workspace/references/Ontology-Playground/src/components/designer/EntityForm.tsx:6) | 数据模型与可选属性元数据；八种属性类型 |
| E11 | [RelationshipForm.tsx:5–212](D:/workspace/references/Ontology-Playground/src/components/designer/RelationshipForm.tsx:5) | 关系方向、基数表单、继承边特殊处理、关系属性 |
| E12 | [rdf-export-profiles.md:7–85](D:/workspace/references/Ontology-Playground/docs/rdf-export-profiles.md:7) | profile 契约、类继承、可选参与、IRI／元数据保留限制、非 SHACL 执行器 |
| E13 | [designerStore.ts:384–414](D:/workspace/references/Ontology-Playground/src/store/designerStore.ts:384) | 新建关联的默认值与普通 store 更新 |
| E14 | [SearchFilter.tsx:24–64](D:/workspace/references/Ontology-Playground/src/components/SearchFilter.tsx:24)、[SearchFilter.tsx:232–276](D:/workspace/references/Ontology-Playground/src/components/SearchFilter.tsx:232) | 术语／属性搜索、结果与选择高亮 |
| E15 | [OntologyGraph.tsx:149–244](D:/workspace/references/Ontology-Playground/src/components/OntologyGraph.tsx:149)、[OntologyGraph.tsx:309–372](D:/workspace/references/Ontology-Playground/src/components/OntologyGraph.tsx:309)、[OntologyGraph.tsx:428–626](D:/workspace/references/Ontology-Playground/src/components/OntologyGraph.tsx:428) | 圆形浏览节点、层次虚线、局部聚焦、选中邻域与图控制 |
| E16 | [DesignerPreview.tsx:17–51](D:/workspace/references/Ontology-Playground/src/components/designer/DesignerPreview.tsx:17)、[DesignerPreview.tsx:274–370](D:/workspace/references/Ontology-Playground/src/components/designer/DesignerPreview.tsx:274) | Graph/RDF、源码编辑、解析与错误区 |
| E17 | [DesignerActions.tsx:117–184](D:/workspace/references/Ontology-Playground/src/components/designer/DesignerActions.tsx:117)、[designerStore.ts:480–483](D:/workspace/references/Ontology-Playground/src/store/designerStore.ts:480) | 校验汇总、问题定位、成功提示和动作触发 |
| E18 | [designerStore.ts:1–7](D:/workspace/references/Ontology-Playground/src/store/designerStore.ts:1)、[designerStore.ts:188–198](D:/workspace/references/Ontology-Playground/src/store/designerStore.ts:188)、[designerStore.ts:473–508](D:/workspace/references/Ontology-Playground/src/store/designerStore.ts:473) | 独立 draft、50 个内存快照、批量导入清历史、撤销／重做 |
| E19 | [ImportExportModal.tsx:12–102](D:/workspace/references/Ontology-Playground/src/components/ImportExportModal.tsx:12)、[ImportExportModal.tsx:339–385](D:/workspace/references/Ontology-Playground/src/components/ImportExportModal.tsx:339)、[ImportExportModal.tsx:410–465](D:/workspace/references/Ontology-Playground/src/components/ImportExportModal.tsx:410) | 文件导入、拖放区域、legacy 格式开关 |

可视素材：[仓库自带 og-image.png](D:/workspace/references/Ontology-Playground/public/og-image.png)。已查看：它展示浏览模式的左任务栏、中央圆形类型节点与关系、右搜索／Inspector／查询区；没有展示 Designer，不能作为编辑器已运行的证明。固定线上文件：[该提交的原始截图](https://github.com/microsoft/Ontology-Playground/blob/9ec82ec6605de15dd50c2254a169986c0b2bd7cc/public/og-image.png)。

## 验证范围与限制

本次读取实际源码、固定 HEAD、核对已有调研并查看仓库截图。尝试通过 cua_repl 打开官方静态演示时返回 `Browser is not available: iab`，随后浏览器清单为 `apps: [], browsers: []`。本地参考仓库没有 node_modules 或 dist；遵守不安装依赖、不改服务器的范围，未启动应用，未取得新的 Designer 运行截图，未运行测试或性能测量，也没有发起模型／生成 API 调用。本文件的 UI 行为判断为源码证据，不宣称实机交互验收通过。
