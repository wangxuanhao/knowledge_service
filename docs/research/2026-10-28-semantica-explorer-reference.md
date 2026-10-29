# Semantica Explorer 图可视化参考核对

核对日期：2026-10-28。前六节为源码研究；第七节补充本机真实示例观察与软件渲染测试。未安装依赖、未修改已安装包，参考研究期间未改业务图谱代码。

## 结论与建议

**Semantica 值得借鉴的是分层视觉和探索交互；本次检查未发现现成的明亮主题或已证实的“30 万节点纯 CPU 浏览器”方案。** 当前 Explorer 主图是 Sigma 3 的 WebGL2 渲染；ForceAtlas2 使用 CPU Worker，这两件事必须区分。建议保留其“概览 → 分组 → 局部探索”、语义分色、悬停邻居强调、路径强调、标签随缩放出现的设计；浅色背景和控件直接参考 G6 官方 Light 样例。若约束是浏览器也禁用 WebGL，G6 默认 Canvas 是比直接移植 Sigma 更合适的候选验证对象。若现有 Canvas 实现已经能承载局部图，则先复用这些视觉和交互规则，无需立即换引擎。[S1][S2][G1][G2]

**30 万节点应先作为数据集规模，另行约束同屏显示规模。** 建议用服务端索引查询、社区/类型聚合、预计算坐标及按需邻域加载承载数据全集；浏览器显示概览或有限局部图，并分别测量加载、内存、首帧、缩放和悬停延迟。该建议是实现方向，不是本文已验证的容量结论。Canvas API 也不保证浏览器底层完全不用 GPU；它能满足的是“不依赖 WebGL”的可检查实现要求。[S5][G2][G3]

## 1. 已固定的源码版本

| 项目 | 本次检查版本 | 用途 |
| --- | --- | --- |
| Semantica | [`e1489f78838cabca7e01e734a4d84fa535e7a8c3`](https://github.com/semantica-agi/semantica/commit/e1489f78838cabca7e01e734a4d84fa535e7a8c3)，2026-10-28 07:48:40 UTC | 用户指定 Explorer 的实际实现 |
| G6 | [`55a74698a9b432df993daae744c4c5526ab097b0`](https://github.com/antvis/G6/commit/55a74698a9b432df993daae744c4c5526ab097b0)，2026-10-22 | 官方浅色主题、Canvas 默认值、大图样例 |
| Sigma.js | [`1db338580abb10c75144b3131ba8718550cb177a`](https://github.com/jacomyal/sigma.js/commit/1db338580abb10c75144b3131ba8718550cb177a)，2026-10-27 | 官方轻量边、邻居悬停与搜索参考 |

Semantica 依赖 React `^19.2.4`、Sigma `^3.0.2`、Graphology `^0.26.0`、ForceAtlas2 `^0.10.1`。本次 Sigma 主仓样例来自 4.0.4，**只作为视觉/交互参考，不能把其新 API 直接复制到 Semantica 的 Sigma 3**。[S1][J1]

## 2. Semantica 实际画面来自哪里

实际 `GraphCanvas.tsx` 导入的是 `graphTheme.ts`。仓内另有 `graphConfig.ts`，含另一组更鲜艳的色值和 8,000/25,000 布局阈值；这些常量不能据此说成当前 Canvas 已实施的规模限制。当前实际主题仍为深色。[S2][S3]

| 视觉层 | 当前实际值/做法 | 明亮版可借鉴的规则 |
| --- | --- | --- |
| 背景 | Canvas `#0A0D11`；Stage `#0B0E12`；暗渐变、低对比网格和暗角 | 用浅灰白底、细弱网格和白色面板替换；保留层次，不复制暗角 |
| 语义色 | `#3E79F2`、`#149287`、`#2F9F61`、`#555FD6`、`#8A56D8`、`#B65473`、`#C9922E` | 在白底上保留饱和主体色；颜色表达分组/类型而非装饰 |
| 交互色 | 选中 `#F2D288`、悬停 `#8FE7FF`、路径 `#D79056`；独立选中圆环 | 同一节点保留语义色，使用轮廓/光晕强调状态；白底另调足够深的强调色 |
| 图元与标签 | 自定义 WebGL 节点图元；曲线/曲线箭头；Canvas2D 绘制圆角标签和悬停卡 | 常态节点简洁；只在局部/路径展示关系标签和复杂边 |

以上数值与图元来源见 [S3][S4]。载入逻辑会按 community/cluster/module/group/category/domain/layer/source/nodeType 等字段的覆盖度和分布选择分色依据，找不到有效字段时退回结构分色；不是给每个节点无意义地随机换色。[S5]

## 3. 已实现的探索行为

- **悬停与选择：** hover 设置当前节点；点击节点切换选择并清空边选择；点击边切换边选择；点空白清除选择。视觉 reducer 根据 hovered/selected/neighbor/path/muted 等状态调整大小、描边、标签和边可见性，并支持对受影响图元作局部刷新。[S6][S7]
- **局部探索与 Ego 不同：** Focus 模式建立一个显示子图，按关系权重、邻居度数、ID 排序，最多取 16 个邻居，前 6 个邻居作为主要标签，主动路径也参与显示。Ego Mode 则是围绕选中节点的深度衰减，界面允许 1–8 hops。不能把 Ego 的淡化当作服务端只加载了局部数据。[S3][S7][S8]
- **搜索与路径：** 搜索/选择变化会使相机居中；路径通过 `/api/graph/path` 请求 Dijkstra，返回 path 和 edge IDs 后强调相关节点和边；路径行为含短暂分阶段刷新。另有可切换的路径流动、局部透镜、时间高亮等效果，适合局部图按需使用。[S6][S8][S2]
- **缩放 LOD：** ratio > 1.2 为 overview、0.5 < ratio ≤ 1.2 为 structure、ratio ≤ 0.5 为 inspection。标签优先级阈值分别为 0.998/0.95/0.8；悬停、选中、路径强制显示标签。Sigma 同时配置 labelDensity 0.7、labelGridCellSize 140，并在移动时隐藏边和标签。Theme 虽声明 labelBudget 2/12/40，但本次所查 Canvas、scene state、analytics、loader 没有发现读取该字段，故不把它当作已生效的硬标签上限。[S2][S3][S7]

## 4. 布局和规模边界

- 主图设置 `webGLTarget: "webgl2"`；使用自定义 GPU 图元，叠加 Canvas2D 标签/效果。**Python/布局只用 CPU，不等于浏览器渲染不用 GPU。**[S2][S4]
- ForceAtlas2 从 `graphology-layout-forceatlas2/worker` 导入；启用 Barnes–Hut；普通图运行约 4.5 秒后自动停止，分组图主题设置约 1.5 秒后停止。停止时间是交互策略，不是收敛质量或吞吐保证。[S2][S3]
- 若 92% 以上节点已有坐标，优先使用给定坐标；48 节点及以下采用确定性紧凑布局；更大的缺坐标图先作语义簇种子布局再使用运行时布局。[S5][S9]
- Loader 以 1,000 条分页持续拉取全部 nodes/edges，汇总数组后进入 Graphology。分页改善传输组织，**不等于浏览器只保留一个数据页或已具备大规模虚拟化**。[S5]
- 本次检查的 Explorer README、主题、Canvas 和 loader 未给出 300,000 节点、禁用 WebGL、指定硬件下的可复现浏览器基准；不能把漂亮截图、Worker 或 Sigma 的宣传直接当作这一保证。[S1–S5]

## 5. 其他成熟开源参考及实际适用性

| 参考 | 有据可查的价值 | 本次证据的边界 |
| --- | --- | --- |
| **G6 官方 Light** | 白底 `#ffffff`、默认蓝节点 `#1783ff`、浅蓝灰边 `#99add1`、黑色文字；官方 theme 样例展示白底、cluster 分色、浅蓝背景切换；一跳 hover-activate 可直接表达邻居强调；默认 renderer 是 `@antv/g-canvas`。[G1][G2][G4] | 成熟浅色/Canvas 候选；尚未在本机/项目证明 300k 性能 |
| **G6 massive-data** | 关闭动画、节点 size 4、batchKey、只开拖动和缩放，数据已带坐标；适合研究大图中减少图元复杂度的做法。[G3] | 官方 `60000.json` 实际 55,001 节点 + 4,862 边；`20000.json` 实际 7,167 节点 + 5,421 边。此次仅下载到内存核对数组数量/坐标，未执行样例，文件名不是节点数或性能结果 |
| **Sigma 官方示例** | large-graph 用 `#e6e6e6` 轻边、cluster 分色、度数尺寸、circlepack 初始坐标、可选 FA2 Worker；hover-search 将非邻域淡化为 `#f6f6f6`，强调一跳邻居，并搜索后移动相机。[J2] | 示例默认 5,000 节点/10,000 边，仍为 WebGL；当前示例 API 是 4.x，与 Semantica 的 3.x 不兼容；不是 CPU-only 300k 证明 |

G6 数据核对源：[20000.json](https://assets.antv.antgroup.com/g6/20000.json)（1,069,505 bytes）、[60000.json](https://assets.antv.antgroup.com/g6/60000.json)（2,725,226 bytes）；两份数据全部节点已有坐标。数据文件未保存或修改。

## 6. 本机 Semantica 0.7.0 已有可展示成品

只读文件核对发现 `D:/anaconda/envs/llm_model/Lib/site-packages/semantica-0.7.0.dist-info`，并且已有完整 Explorer 前端构建，**无需 npm build**：

- [static/index.html](D:/anaconda/envs/llm_model/Lib/site-packages/semantica/static/index.html) 引用 `index-CgN8BDMH.js` 和 `index-1gKdBBTm.css`。
- `static/assets/` 包含 `GraphWorkspace-Dny3nAOi.js`、`graphTheme-CZgtRLnq.js`、`graph-vendor-C_DST6AN.js`、`style-BZV40eAE.css` 等。
- [explorer/app.py](D:/anaconda/envs/llm_model/Lib/site-packages/semantica/explorer/app.py) 第 190–194 行直接返回静态 index；第 230–234 行挂载 `/assets`。官方 README 也明确 wheel 自带已构建前端。[S1]

这能用于隔离展示真实 Explorer；本机安装包与上述 main commit 的内容不能默认相同。后续已启动的隔离示例见第七节。

## 7. 本机实际观察与 CPU 实验

### 7.1 真实示例

使用已安装的 Semantica 0.7.0，创建 32 个实体、44 条关系的中文合成图；未访问业务库或模型，测试服务只监听 loopback。

- `http://127.0.0.1:8191`：原生 Knowledge Explorer。实际操作 Full Graph、布局 Run/Pause、节点选择、Focus 和检查器；选中“法律依据”后显示 8 个直接邻居，Focus 显示 9 个节点。原生暗色 UI 在当前视口下顶部工具和时间轴占据较大空间，默认小图布局也不能直接视为业务页的理想布局。
- `http://127.0.0.1:8192/kg-visualizer.html`：KGVisualizer 原生 `force` / `vibrant` 生成的白底交互图，NetworkX 预计算布局、Plotly SVG、类型分色、度数尺寸。32/44 的展示可读性不构成大图性能证据；关系文字全开仍较嘈杂。
- `http://127.0.0.1:8193`：原生 Explorer 组件的隔离亮色映射实验，使用相同合成图和预计算坐标。这是本地映射，**不是 Semantica 官方提供的浅色主题**。目前观察到部分关系线反差不足，尚不适合作为可交付主题直接移植。

示例脚本：`.benchmarks/semantica-reference-demo.py`、`.benchmarks/semantica-light-reference.py`；白底截图：`.benchmarks/semantica-reference/kg-visualizer-light.png`。这些是忽略目录内的实验文件，未加入业务路由。

### 7.2 Sigma 软件渲染实验

从已安装包复制原有 `graph-vendor-C_DST6AN.js` 与其 vendor 依赖到实验目录，直接使用其中导出的 Graphology 和 Sigma。此实验仅测基础引擎，**不是整个 Semantica Explorer，也不含其自定义图元、服务端查询或完整渐进加载链路**。

条件：本机 Edge headless；`--disable-gpu --disable-accelerated-2d-canvas --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader`；CDP 确认 ANGLE / SwiftShader，GPU 合成和 2D 加速均为 disabled_software。画布 1000×500、DPR 1；确定性随机预设坐标、7 种颜色、最多 20 个普通标签、直线边、无布局动画。30 次小幅平移采样，5 次高亮更新并 refresh；`gl.finish()` 同步等待渲染。结果为单次合成试验，不是重复统计或生产容量保证。

| 实体 / 关系 | 数据对象构建 | 就绪 | 平移 P95 | 选择更新最大延迟 |
| --- | ---: | ---: | ---: | ---: |
| 200 / 400 | 4.2 ms | 176.2 ms | 39.3 ms | 126.5 ms |
| 1,000 / 2,000 | 13.9 ms | 123.8 ms | 90.1 ms | 230.7 ms |
| 10,000 / 20,000 | 122.9 ms | 1,361.6 ms | 729.3 ms | 990.7 ms |
| 300,000 / 600,000 | 未取得有效完整结果 | 未完成 | 无 | 无 |

30 万档启动后测试进程明显拖慢，已核实进程并终止整个独立测试进程树；不得把这次尝试标为通过、给出 P95 或当作已完成性能验收。该规模默认从后续脚本运行中移除，只有显式 `--large` 才会重试。更小规模已经未满足交互延迟目标，当前没有证据支持直接迁移基础引擎。

脚本与原始日志：`.benchmarks/semantica-sigma-cpu.py`、`.benchmarks/semantica-reference/sigma-cpu.html`、`.benchmarks/semantica-sigma-cpu.log`。初次大规模运行被终止，前三档完整记录保留在日志；不能将退出码说成全档通过。

### 7.3 对现有产品的决定建议

优先借鉴 Semantica 的语义分色、状态描边、选中节点和一跳邻居强调、缩放标签层级、Focus 的有限邻域展示；白底与深色文字应按当前页面进行映射。全局与局部继续保持统一画布几何和工具栏，不复制 Explorer 的完整导航、时间轴或所有特效。

现阶段不直接替换为 Sigma，也不把 KGVisualizer 的 SVG 全图路径用于 30 万数据全集。后续样式落地仍需在现有渐进加载与软件渲染条件下验证；如需换成 Canvas/G6，应先完成相同数据与场景的独立实测。上述结论支持视觉规则的复用，不支持声称已经具备纯 CPU 30 万全流程顺滑能力。

## 8. 图谱顶部菜单的采用建议

补充核对：2026-10-28，实际展开已安装 Explorer 的 Effects、Neighborhood、Ego depth 和 Temporal Context，另对照本项目当前 `index.html`、`full-graph-view.js`、`graph-workbench.js`。这是能力评估，不是新功能已实现或验收声明。

结论：现阶段保留现有渲染与渐进加载方案，采用 Semantica 中能改善探索效率的交互。基础 Sigma 合成实验不足以证明本项目在所有场景更快；本项目完整 30 万流程仍需验收。

| 原菜单 | 现有入口 / 建议 | 采用优先级与 CPU 边界 |
| --- | --- | --- |
| Full Graph / Focus | 现有全量大图 / 局部探索已对应，不重复增加第三个同义模式；借鉴选择后带入局部锚点的操作 | 优先统一行为和几何；切换不应重新计算全部布局 |
| Search / Camera | 现有定位、缩放、适应画布入口可复用；全量定位当前明确限定“已加载图”，全库检索需另走查询 | 保留，改善分组与状态提示；不得把未加载实体的缺失说成全库不存在 |
| Neighbors / Collapse / Expand | 补充可点击的邻居列表，显示类型、方向、关系和数量；支持受控展开、撤回本次扩展 | 第一优先；分页与预算限制，列表显示“已加载 / 总数”来源；收起撤回当前分支的呈现，不删除知识，也不丢弃其他分支共享的节点 |
| Ego Mode / Neighborhood Lens | 选中点及有限跳数内邻居保持清晰，其他内容淡化；映射到现有跳数与方向控制 | 第一优先；先 1–2 跳，静态强调，选择改变后复用邻接索引，不在每次指针移动遍历 30 万节点 |
| Directed Pathfinding | 现有路径循迹含方向和最大跳数；借鉴起终点操作及静态路径强调 | 第一优先；当前算法只搜索已加载图，未找到路径不代表全库无关联；不使用路径流光 |
| Edge Labels | 现有局部名称开关、全局名称设置可统一入口；保留线与文字的独立控制 | 第一优先；密集概览收敛标签，只强调选中和路径关系，开关状态必须一致 |
| Grouped View / Community Regions | 分组概览有价值，但类型分组与 Louvain 社区不是同一含义；按已知类型/预计算社区汇总并显示成员数 | 第二阶段；全库汇总应由后台预计算或查询提供；只有已加载样本时必须标注范围，不能直接当作全库统计，不在点击时对 30 万节点跑社区算法 |
| Centrality Ranking | 用关键节点排序、优先标签和定位入口改善概览 | 第二阶段；先复用已知关系数量/度数并明确范围。PageRank、介数中心性等全库指标需要预计算，不能用局部度数冒充 |
| Heatmap / Structural | 按距所选实体的跳数着色，帮助阅读局部关系层级 | 可选；仅对有界局部图计算，打开时切换图例，避免把距离色误读成实体类型色 |
| Semantic | 本体/实体相似不等于存在事实关系；源码请求 semantic-neighborhood API，服务不可用时会报错 | 后续；需要可用向量检索、阈值和解释，不能只换颜色或画出未经证实的事实边 |
| Layout Run / Pause | 学习有界运行后停止的方式 | 只在小局部图提供整理 / 停止；大图使用稳定坐标，避免持续全图布局与跳动 |
| Temporal / Play Evolution | 仅在实体/关系有真实有效期或历史快照时有业务意义 | 后续先考虑静态时间筛选；不默认铺设固定时间轴，不把入库时间冒充事实发生或生效时间 |
| Path Pulse / Path Flow / Contours | 动态路径、密度光晕等装饰效果 | 当前不采纳，用户优先纯 CPU 性能且不需要消耗性能的动画 |

建议常驻工具围绕已有模式、搜索定位、缩放、适应、整理布局组织；“邻居 / 聚焦 / 循迹”作为探索动作，“类型 / 名称 / 关系文字”收进设置。按是否选中节点启用相关动作，避免照搬完整 Effects 面板增加顶栏行数与占用画布高度。

上述原菜单来源仍为 [S2][S3][S7][S8]，其中 Effects 中现场还看到 Directed Pathfinding、Community Regions、Centrality Ranking、Regions Summary；可见菜单不等于当前隔离样例已完成所有分析计算。

## 一手来源

- [S1 — Explorer README / 技术栈与构建说明](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/README.md#L267)；[package.json](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/package.json#L17)
- [S2 — GraphCanvas：实际导入、Sigma 设置与 Worker](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/GraphCanvas.tsx#L125)，布局实现见同文件 [L2450](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/GraphCanvas.tsx#L2450)
- [S3 — 实际 graphTheme](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/graphTheme.ts#L393)；[另一份 graphConfig](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/graphConfig.ts#L1)
- [S4 — 自定义节点、标签、悬停卡与边程序](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/sigmaNativeRendering.ts#L316)
- [S5 — Loader：分色、分页、坐标](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/useLoadGraph.ts#L26)
- [S6 — clickSelectionBehavior](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/behaviors/clickSelectionBehavior.ts#L3)；[hover](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/behaviors/hoverActivationBehavior.ts#L3)；[search focus](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/behaviors/searchFocusBehavior.ts#L14)；[path refresh](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/behaviors/pathHighlightBehavior.ts#L36)
- [S7 — Scene：邻居排序、标签、显示子图](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/graphSceneState.ts#L941)，标签见 [L1553](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/graphSceneState.ts#L1553)，局部子图见 [L2539](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/graphSceneState.ts#L2539)
- [S8 — GraphWorkspace：路径 API](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/GraphWorkspace.tsx#L1843)，Ego 控件见 [L2943](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/GraphWorkspace.tsx#L2943)
- [S9 — 坐标覆盖度和小图布局判断](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/smallGraphLayout.ts#L1)
- [G1 — G6 Light tokens](https://github.com/antvis/G6/blob/55a74698a9b432df993daae744c4c5526ab097b0/packages/g6/src/themes/light.ts#L4)；[官方主题切换样例](https://github.com/antvis/G6/blob/55a74698a9b432df993daae744c4c5526ab097b0/packages/site/examples/feature/default/demo/theme.js#L5)
- [G2 — 默认 CanvasRenderer](https://github.com/antvis/G6/blob/55a74698a9b432df993daae744c4c5526ab097b0/packages/g6/src/runtime/canvas.ts#L358)
- [G3 — G6 60000 样例实现](https://github.com/antvis/G6/blob/55a74698a9b432df993daae744c4c5526ab097b0/packages/site/examples/performance/massive-data/demo/60000.js#L1)
- [G4 — 一跳关系悬停](https://github.com/antvis/G6/blob/55a74698a9b432df993daae744c4c5526ab097b0/packages/site/examples/behavior/highlight-element/demo/activate-relations.js#L14)
- [J1 — 当前 Sigma 主仓版本](https://github.com/jacomyal/sigma.js/blob/1db338580abb10c75144b3131ba8718550cb177a/packages/sigma/package.json#L3)
- [J2 — Sigma large graph](https://github.com/jacomyal/sigma.js/blob/1db338580abb10c75144b3131ba8718550cb177a/packages/website/src/examples/core/large-graph.astro#L24)；[hover/search](https://github.com/jacomyal/sigma.js/blob/1db338580abb10c75144b3131ba8718550cb177a/packages/website/src/examples/core/hover-search.astro#L33)
