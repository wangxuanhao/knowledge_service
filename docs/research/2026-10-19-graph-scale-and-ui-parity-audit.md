# 检索与交互图谱：15 万实体容量与能力保留审计

日期：2026-10-19。检查基线：`724c10d039595a8276c4fb5f7ae8ee26ee0aafa2` 的当前工作区源码；收尾时 `git status --short` 仅显示本研究文件未跟踪，没有跟踪源码变更。本次只做静态研究，没有改产品代码或数据，没有调用模型，没有运行生产容量测试。

## 结论

**重新设计可以保留现有展示、证据、编辑和治理能力；但当前实现没有证明能流畅同时绘制 150,000 个实体。** 用户已明确两个目标都必须实现：全语料检索，以及在同一个图谱中加载并绘制 150,000 个实体。两者必须分别验收，局部图或聚合总览不能替代第二个目标：

| 目标 | 本次结论 |
| --- | --- |
| 项目中存有 150,000 个实体，用户能检索任意实体、查看详情、编辑，并按需展开邻域 | 可以作为设计目标。现有 PostgreSQL 权威记录、检索、独立记录读取和版本化写入可以继续使用；需补齐有界图谱读取、服务端实体选择器、分页、计数及性能验收。当前代码仍有全范围扫描，不能直接承诺现状达标。 |
| 同一个图谱中加载并绘制 150,000 个实体 | **必须新增的完整图模式。** 当前没有容量证据。正常全图路径没有节点/边预算，ECharts 和 DOM 路径都按全部返回数据构造，不能承诺性能。关系总量/密度、属性值是否展开、标签策略必须另列验收口径，不能把聚合点数量算作真实实体数量。 |
| 用户能看到整个项目的结构，并逐层进入具体实体 | 范围统计/聚合总览 + 有界局部实体图可以作为辅助模式；总览、局部图、完整图、完整导出使用明确的完整性口径。聚合项必须先定位到真实记录，才进入编辑。 |

当前结论只说明能力保留在架构上可行、现有完整图容量未经证明；不把“全部数据可达、可查、可改”当作“同一画布绘制 15 万实体”已经实现。

## 检查方法与源码时效

先使用项目 `D-workspace-knowledge_service_v2-knowledge_service` 的 codebase-memory MCP 搜索 `Explorer.graph`、`mindmap`、`KnowledgeService.scoped` 等符号，再核对磁盘源码。索引的符号位置已经落后：例如 `Explorer.graph` 索引起点是 129，磁盘定义是 168；`scoped` 的索引摘录甚至落在前一个方法内。因此本文引用磁盘行号。前端模块在图索引中没有充分结果，使用文件读取补足。

本工作区的活动实现位于 `web/workspace.js`、`web/workbench.js`、`web/app.js`；未发现名为 `app-loaders.js` 或 `graph.js` 的同级文件，不能把其他版本的拆分路径当成当前事实。

## 1. 当前读取契约与实际边界

| 入口 | 当前边界 | 对 15 万实体的影响 |
| --- | --- | --- |
| `POST /subgraph` | `node_id` 可空，`hops` 默认 1、范围 0–5；属性默认 `summary`；支持实体类型、关系谓词及通用 Scope。没有 `max_nodes`、`max_edges`、cursor、`truncated` 或实际总体数。[请求/路由](../../knowledge_service/api/parity.py#L22-L31)、[返回值](../../knowledge_service/services/explorer.py#L256-L260) | `node_id=null` 返回整个可见范围；传了种子也只限制跳数，未限制高出度邻域大小。 |
| PostgreSQL 子图回退 | 先 `service.scoped` 读取范围，再建实体/关系集合；有种子时每跳扫描边集合，最后才收窄。[实现](../../knowledge_service/services/explorer.py#L168-L218) | 小响应不代表小读取；耗时受项目可见记录规模和关系数影响。 |
| Neo4j 原生展开 | 仅配置适配器、投影 ready、种子存在、0–2 跳、无 `known_at`/复杂 filters/实体类型/谓词/非 all 本体范围时启用；否则明确回退 PostgreSQL。[决策](../../knowledge_service/services/explorer.py#L133-L166) | 已有可复用的邻接读取路径，但并非所有现有筛选语义都走它。Cypher 按 0–2 跳收集版本 ID，没有节点/关系数量 LIMIT；高出度仍可能返回很大集合。[展开](../../knowledge_service/integrations/neo4j_store.py#L699-L777) |
| 属性展示 | `summary` 为每个返回实体附属性组；`expanded` 给所选实体增加 response-only 属性值节点及边。当前值、冲突候选、支撑数量、来源信息保留不同语义。[分组](../../knowledge_service/services/explorer.py#L69-L131)、[投影](../../knowledge_service/services/explorer.py#L216-L252) | `max_nodes` 未来必须说明是否包括虚拟属性值；否则“实体节点预算”并不能控制最终绘制对象数。 |
| 属性支撑/冲突读取 | `_entity_attributes` 读取项目全部 assertions 与 record-version/assertion mappings，然后按实体集合筛选。[实现](../../knowledge_service/services/explorer.py#L59-L91) | 即使 Neo4j 子图很小，开启属性后仍有项目级读取成本。 |
| `POST /mindmap` | 默认深度 3，上限 5；先调用 `graph(..., attribute_mode='none')`；树投影最多约 500 次节点出现，每次取邻接列表前 40 项，返回 `bounded:true`。[实现](../../knowledge_service/services/explorer.py#L321-L337) | 500 是树出现次数预算，不是完整图谱容量；读取底层子图仍可能很大。没有剩余邻居数或继续加载 cursor，`bounded:true` 不说明这次实际省略了多少。 |
| `POST /entity-options` | 返回范围内全部实体简要信息、谓词和日期，无页大小、query 或 cursor。[路由](../../knowledge_service/api/workspace.py#L80-L88) | 150,000 个实体会进入全量选择器数据；仅输入框前端过滤不能降低初次读取或 DOM 成本。 |
| `POST /records/query` | 默认 limit 200，上限 1000，offset 可用；先拿到完整 `service.scoped` 列表，再在 Python 切片，返回 `total`。[实现](../../knowledge_service/api/knowledge.py#L76-L81) | 是响应分页，尚非数据库读取分页；不能视为全量扫描已消除，也没有固定快照的稳定 cursor 契约。 |
| `POST /explore` | `k` 默认 15、最大 100，hops 默认 1、最大 3；但服务先读整个范围，空 query 返回整个范围图，有 query 在检索后扩展全部匹配邻域。[模型](../../knowledge_service/api/workspace.py#L26-L34)、[服务](../../knowledge_service/services/explorer.py#L373-L443) | `k` 限制检索命中，不能当作图谱节点/边预算。当前主搜索 UI 使用 `/search`，不要因旧 `/explore` 存在就接回它作为大图路径。[UI](../../knowledge_service/web/workspace.js#L455-L477) |

### 需要特别注意的实际参数转发

`Explorer.graph` 与 `/entity-options` 都传入 `kinds`，注释称会下推以跳过 document/chunk；但当前 `KnowledgeService.scoped` 仅在 `_reference_mode` 分支把 `kinds` 和 applicability 加入 repository 参数。正常分支只传 filters、valid_at、known_at、include_unknown，然后在 Python 处理 applicability/本体归属/端点可见性。因此正常图谱回退和选项列表目前会读取所有符合仓储时态条件的记录，再筛掉不展示的类型。[当前 scoped](../../knowledge_service/services/service.py#L375-L415)

仓储本身已经支持 kinds、applicability、业务时间下推，但普通 query 仍是 `ORDER BY id,version` + `fetchall()`，只有 reference 模式加 LIMIT；复杂 metadata filters 仍在 Python 求值。[SQL 构造与读取](../../knowledge_service/repository/core.py#L1347-L1447) 这条差异属于源码观察，本次没有修复，也没有用容量测试量化其影响。

原生/回退结果等价还需要回归验证：Neo4j `expand` 把空 `valid_at` 转为 `utc_now()`，其 predicate 按该时间过滤；PostgreSQL 在 `valid_at is None` 时不做业务时间过滤。[Neo4j 时间参数/谓词](../../knowledge_service/integrations/neo4j_store.py#L682-L719)、[PostgreSQL 条件](../../knowledge_service/repository/core.py#L1391-L1400) 另外，PostgreSQL BFS 选出节点后返回两端都在集合内的关系，即所选节点的诱导边；Neo4j 当前收集的是已遍历路径的关系版本，不能未经测试就认为相同跳数下一定包含所有同层邻居间连边。[回退边选择](../../knowledge_service/services/explorer.py#L205-L212)、[原生关系收集](../../knowledge_service/integrations/neo4j_store.py#L724-L772) 这些是静态发现的语义差异风险，本次未复现生产症状。

## 2. 浏览器为何不能直接保证同时画 15 万实体

当前主图配置是 **ECharts graph + `layout:'none'`**，使用已记忆坐标或确定性螺旋初始坐标；不能因同段保留了 `force` 配置和旧注释，就声称当前实际运行力导向模拟。[坐标](../../knowledge_service/web/graph-workbench.js#L25-L31)、[实际渲染配置](../../knowledge_service/web/workspace.js#L368-L392)

已有的平移优化保留：背景手势合并到动画帧，视口通过 GraphView 控制器变换，避免每个鼠标事件重新构造图谱。节点释放、fit、重新布局、路径高亮仍可能遍历并提交当前图的完整数据。[交互实现](../../knowledge_service/web/graph-workbench.js#L6-L89)、[重新布局](../../knowledge_service/web/graph-workbench.js#L136-L144)

剩余成本可直接从源码看到：

- `renderGraph` 为所有返回节点创建坐标对象、Map、类别及 chart data，为所有返回关系创建 links，然后 `setOption(...replaceMerge:['series'])`。[主图](../../knowledge_service/web/workspace.js#L344-L392)
- `GraphWorkbench.mount` 序列化全图身份/版本签名，再为路径起点、终点分别创建每实体一个原生 `<option>`。150,000 个实体意味着仅这两份路径下拉就会构造约 300,000 个 option，尚未算其他实体选择器。[挂载](../../knowledge_service/web/graph-workbench.js#L148-L168)
- 全范围 entity-options 在客户端按名称/类型筛选，然后重建实体选择器与脑图选择器。[加载](../../knowledge_service/web/workspace.js#L297-L307)、[筛选](../../knowledge_service/web/workspace.js#L212-L226)
- 当前路径查找只搜索已加载事实：先对本地图构造邻接表，再 BFS；找不到路径不表示整个项目没有路径。[路径算法](../../knowledge_service/web/graph-paths.js#L1-L28)
- bounds 和 preserveTransform 用 `Math.min(...array)`/`Math.max(...array)`；大量节点需考虑引擎实参数量上限，改为遍历归约更稳妥。此处是实现风险观察，没有在 150k 数组上测过或声称必然崩溃。[实现](../../knowledge_service/web/graph-workbench.js#L37-L55)

150k 指的是实体数；关系条数、属性值数量、文本长度、元数据、冲突和证据数量都额外影响响应、JSON 解析、内存和绘制。不能仅给出实体数量就推导某个浏览器内存或 FPS。当前代码也没有基于视口的节点裁剪、聚合层级或远端分块读取契约；未来只替换渲染器不能自动消除上述读取/DOM 路径。

完整图模式需要独立的可验证技术方案：WebGL 级批量节点/边渲染与拾取，服务端按稳定快照分块/流式输送轻量节点和边，Worker 处理布局/索引/解析或数据准备，主线程仅做交互与视图提交，详情/属性/证据按 ID 延迟读取。以上是待实现的方向，不是对任何特定库承载 150k 的保证；应先用当前浏览器、目标关系密度和标签/拾取策略做可复现测量。LOD、隐藏标签、视口裁剪可以是交互策略，但验收必须证明完整图的 150k 真实实体加载和绘制要求，不得只报告聚合或采样后的数量。

## 3. 现有能力可保留，但必须解除“依赖全量图”的耦合

| 用户能力 | 当前证据与保留要求 |
| --- | --- |
| 检索后选择实体/关系、先看详情，再决定展开 | 搜索结果的“在图谱中查看”走 detail selection；详情使用独立 `GET /records/{id}`，不会强制替换当前图。保留请求 epoch、范围检查与过时响应丢弃。[结果动作](../../knowledge_service/web/workspace.js#L411-L437)、[详情选择](../../knowledge_service/web/workspace.js#L278-L295)、[图谱请求门禁](../../knowledge_service/web/workspace.js#L490-L523) |
| 类型筛选、关系类型、标签、颜色、命中高亮、拖动、缩放、fit、重新布局、方向与跳数路径 | 都能在有界局部图中保留；类型按钮现在只过滤当前图，路径现在只查当前已加载事实。新 UI 必须继续明确它们的范围，不能把局部空结果解释为项目无此数据。[渲染](../../knowledge_service/web/workspace.js#L344-L392)、[路径](../../knowledge_service/web/graph-paths.js#L1-L28) |
| 类、父类、祖先、本体归属、修订与双时间信息 | 独立记录详情按该记录绑定的本体发布补充类家族，普通编辑不默认迁移到最新本体。保留这套语义，不用当前本体覆盖历史绑定。[详情/编辑 API](../../knowledge_service/api/knowledge.py#L34-L74)、[record_family](../../knowledge_service/services/service.py#L442-L465) |
| 属性值、数据类型、有效期、接受支撑、冲突候选、虚拟属性节点 | 仍需独立属性详情/分页读取；不能只保留普通 `GET record` 而漏掉 `/subgraph` 当前补充的属性组。虚拟展示节点只定位所属实体，不能拿其 ID 发编辑写入。[属性投影](../../knowledge_service/services/explorer.py#L216-L252)、[属性表](../../knowledge_service/web/evidence-inspector.js#L5-L20)、[虚拟节点点击](../../knowledge_service/web/workspace.js#L309-L319) |
| 编辑实体、关系端点/类型、正式属性；高级 JSON；版本冲突防护 | 共享编辑窗口最终使用 PUT + expected_version；返回新修订，旧修订保留。编辑器使用绑定本体目录；未知本体归属不能假装有可靠选项。[编辑器](../../knowledge_service/web/record-dialog.js#L26-L119)、[API](../../knowledge_service/api/knowledge.py#L50-L67) |
| 新增关系、软删除、修订历史、来源证据、台账联动、脑图 | 这些已有独立动作，均可继续挂到同一记录详情。新增关系通过正式 records API，校验两端有效期；软删除使用 expected_version。[动作](../../knowledge_service/web/evidence-inspector.js#L43-L77)、[详情动作](../../knowledge_service/web/evidence-inspector.js#L128-L174)、[历史窗口](../../knowledge_service/web/record-dialog.js#L235-L256) |
| 别名、重复实体合并、撤销、恢复 | 仍沿用治理 API 和独立台账入口；不要求把全部实体绘制到主图。[现有入口](../../knowledge_service/web/workbench.js#L101-L107)、[路由](../../knowledge_service/api/parity.py#L307-L322) |

### 已有规模缺口不能包装成“完整可用”

新增关系弹窗只请求 `/records/query?limit=1000`，在这批本地记录中筛实体，每次显示前 100 个匹配。结构化编辑同样只取最多 1000 条，并保留未在列表中的原端点 ID；任意第 1001 条以后实体并不能靠该本地搜索找回。[关系选择器](../../knowledge_service/web/evidence-inspector.js#L48-L64)、[编辑选项](../../knowledge_service/web/record-dialog.js#L91-L119) 加上前述 scoped 参数转发问题，返回的 1000 条目前甚至不一定都是请求的实体类型。

知识台账当前也只取一页 1000 条，之后的每页 25 条是客户端分页；它会显示已加载数/total，但没有继续读取所有 offset 页。顶部旁数据还整批读取 assertions、operations、duplicate-groups 等。[加载](../../knowledge_service/web/records-view.js#L492-L519)、[分页](../../knowledge_service/web/records-view.js#L414-L433) 要承诺“15 万实体都能编辑”，需要服务端搜索选择器和按 ID 定位/分页台账，不能把当前截断行为原样继承后称为完整保留。

当前新增/编辑/删除完成后会触发全图重画；这个刷新策略在大规模下必须改为按已加载记录集合或局部查询更新，同时保留 selection、坐标、范围与版本一致性。[编辑后刷新](../../knowledge_service/web/record-dialog.js#L48-L76)、[关系保存后刷新](../../knowledge_service/web/evidence-inspector.js#L76-L77)、[删除后刷新](../../knowledge_service/web/evidence-inspector.js#L165-L167)

## 4. 完整导出与屏幕可见图必须分开

现有 `GET /export` 调用 `export_projection`，在事务内读取项目全部历史版本，包含本体、治理、断言映射、provenance、artifacts；不是“当前画布导出”。前端再 `JSON.stringify(..., null, 2)` 并构造 Blob。[API](../../knowledge_service/api/parity.py#L386-L388)、[完整快照](../../knowledge_service/repository/core.py#L260-L301)、[浏览器导出](../../knowledge_service/web/workbench.js#L430)

应保留完整快照的格式、引用与恢复语义，同时提供可取消、可查询进度的服务器导出任务或流式/分块下载，避免浏览器先持有整份历史对象再生成字符串/Blob。另设“当前视图/筛选结果导出”，明确采样、隐藏、截断是否包含。任何局部图导出都不能冒充完整项目备份。这些是待设计的契约，不是现有 export 的容量结论。

## 5. 改造前必须补齐的契约

| 契约 | 最低要求 |
| --- | --- |
| 范围统一 | 项目、applicability、metadata、known_at、valid_at、include_unknown、本体范围组成同一 scope fingerprint；总数、总览、搜索、局部图、详情和导出说明实际使用范围。当前主搜索/图谱显式 `valid_at:null`，是否改变需产品确认并有回归证据。[搜索](../../knowledge_service/web/workspace.js#L455-L462)、[图谱](../../knowledge_service/web/workspace.js#L487-L499) |
| 总览 | 返回精确或明确标注近似的实体/关系计数、类型/聚合节点与连边、聚合定义、统计快照标识；不下发全部 entity payload。 |
| 完整图（必需） | 同一图谱的 150k 真实实体 ID 和相应已声明关系集合完整加载；稳定快照、分块进度、可取消/恢复、完整性校验；WebGL/Worker/紧凑传输方案及实际绘制、拾取、搜索定位、交互、内存和编辑更新的测量。聚合或局部展开不能充当该模式完成。 |
| 局部展开 | 种子、方向、谓词、跳数、max_entities/max_edges/max_attribute_values/bytes/time 预算；稳定排序；has_more/next_cursor、actual_returned、total 或 total_unknown、truncation_reason。高出度节点需能继续浏览。 |
| 游标与一致性 | cursor 绑定项目、scope、排序和读取快照/投影 generation；范围或版本变更时拒绝过期 cursor，避免混合快照/重复漏项。 |
| 后端读取 | 邻接索引/原生投影优先；回退也按 SQL 有界读取，属性、支撑/冲突查询按所选实体 ID 收窄；返回 backend 和 degraded_reason。 |
| 选择器与台账 | 服务器按名称/别名/类型搜索、分页、按 ID 补载，保留重名实体的身份区分；选关系端点不依赖首 1000 条。 |
| 详情与写入 | 详情以 project + record_id + revision/version_id 读取；保留正式属性、来源、绑定本体家族。写入仍发真实 record ID 和 expected_version；历史查看、虚拟属性值、聚合项不会误作当前记录写入。 |
| 视图状态 | 更改筛选/项目后取消旧请求；局部更新保持选择、布局与手势；保存成功与视图刷新失败分开反馈。 |
| 路径与脑图 | 明确“已加载图路径”与“全项目路径”；全项目路径采用服务端预算。脑图标明遗漏的邻居、节点出现次数及继续加载能力。 |
| 导出 | 当前视图、筛选结果、全项目完整快照三种口径可辨识；全量导出不要求浏览器加载全量图；保留治理/provenance 和可恢复格式。 |

## 6. 已有性能证据及尚未证明的部分

- 图交互历史报告使用真实无头 Edge、生产 ECharts/交互模块、**500 节点/1500 边**；记录平移/挂载/缩放/重新布局的同步操作时间，并明确不等于帧率或其他业务数据保证。报告的真实业务页面仅 28 实体/30 关系。[历史报告](../superpowers/specs/2026-10-16-graph-interaction-performance.md#L15-L24)
- `benchmark_graph_interaction.py` 默认 500 节点、5 repeats；合成每节点 3 条边，无数据库/用户会话，指标为交互主线程操作、settled_ms 和更新次数。它没有测 API、JSON、真实属性/证据负载，亦不构成 15 万实体渲染证明。[生成与渲染](../../scripts/benchmark_graph_interaction.py#L1-L44)、[测量与默认值](../../scripts/benchmark_graph_interaction.py#L45-L100)
- 仓库有 native applicability 容量验收脚本，最低数据集是 **100,000 条 current records**，不是 100,000 个实体，更不是全部绘制。阈值包含检索和 2-hop p95；默认 dry run 返回 `status: skipped`，真实验收需专用 PostgreSQL/Milvus/Neo4j 环境。[目标与阈值](../../scripts/verify_native_applicability_capacity.py#L1-L36)、[报告契约](../../scripts/verify_native_applicability_capacity.py#L174-L203) 现有说明截至 10 月 15 日未执行真实重型容量验收。[说明](../applicability-retrieval.md#L47-L82)
- OAG 的 10,001 行测试验证参考关键词读取与 SQL 计划；不能外推为图渲染容量。[测试报告构造](../../tests/service/test_oag_performance.py#L182-L219)
- 本次搜索 docs/scripts/tests 未找到 150k 实体同时绘制的验证报告；未发现 artifacts 目录中的容量通过报告。这里是本次可见材料的检查结果，不声称外部从未测试过。

下一轮应在隔离数据集按 1k/10k/50k/150k **实体总量**测试。检索/局部图组固定可见预算，**完整图组必须实际加载和绘制 150k 实体**，不得用前一组替代；覆盖低出度/高出度、关系密度、属性和冲突、当前/历史/筛选、投影 ready/回退。记录仓储读取行数/EXPLAIN、API p50/p95、响应字节、解析与布局、首次可交互、完整加载/绘制时间、主线程长任务、峰值内存、平移/缩放/拖动/拾取/搜索定位和编辑后更新。先约定可接受阈值、目标硬件和图密度，再报告通过与失败；不把脚本存在、dry run 或小图交互报告当成 15 万规模达标。

## 7. 验收时的能力回归清单

现有契约测试可作为种子：图谱/台账/修订/绑定本体往返见 [test_graph_version_roundtrip.py](../../tests/service/test_graph_version_roundtrip.py#L15)，局部事实路径与虚拟节点排除见 [graph-paths.test.cjs](../../tests/service/graph-paths.test.cjs#L9-L36)，过时响应与类型筛选见 [graph-types.test.cjs](../../tests/service/graph-types.test.cjs#L4-L108)，拖动点击区分/坐标身份/手势取消见 [graph-interaction.test.cjs](../../tests/service/graph-interaction.test.cjs#L4-L35)。这些测试本次只阅读，没有重新运行。

必须追加规模相关场景：第 1001 个以外的实体可搜索并作为关系端点；高出度展开明确截断且可继续；全范围总数不被局部可见数替代；局部路径无结果不报全局断开；保存/删除/撤销只刷新当前相关视图；聚合和虚拟节点不能编辑；完整备份不因画布预算缺失记录；跨项目/历史快照的 cursor 和迟到响应不能污染当前图。
