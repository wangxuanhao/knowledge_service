# Hindsight 图可读性与 30 万节点适用性研究

日期：2026-10-26。范围：官方源码与本机界面观察；只做研究，方案须经用户确认后实施。

> 2026-10-27 补充：用户随后明确浏览器也必须纯 CPU。下文 Iris Xe 的 17 ms 结果与 GPU 拾取建议不能用于这一条件的验收；缓存、SwiftShader 和实际 Hindsight 扩容测试见[纯 CPU 对照报告](2026-10-27-graph-cpu-cache-assessment.md)。生产方案仍待确认，未据此更换渲染器。

## 结论

Hindsight 值得借鉴的是**标签避让、缩放分级、局部关系聚焦和视觉层次**。它当前的清晰界面不能证明浏览器同时处理 30 万真实节点的能力：本机看到的首页实际只加载 200 个节点，空闲绘制最多 6,000 条边；记忆页默认取 1,000 个节点。[1][2][3]

本机运行镜像为 `ghcr.io/vectorize-io/hindsight:1.1.0`。研究固定到官方 **v1.1.0 / `a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5`**（2026-10-21）。另核对 2026-10-26 的 main `1da5f03241de742aaf37f78399061636077d2aad`：HomeView、Constellation、DataView、EntitiesView 的文件 blob 与 v1.1.0 相同。下文源码链接全部固定版本，避免把未来 main 的变化混进结论。[0]

## 数据规模、加载规模、绘制规模必须分开

| 层面 | v1.1.0 的实际行为 | 对本项目的含义 |
|---|---|---|
| 后端存储与检索 | 官方性能文档称每 bank 测过数百万 facts；该段讨论 PostgreSQL / pgvector 的向量检索 | 不能推导前端同时加载、布局、拾取或绘制数百万节点。[4] |
| 首页概览 | `GRAPH_NODE_CAP = 200`；单独请求 `getGraph(limit: 200)`。源码注释称约 1,000 节点可产生约 67,000 边、约 22 MB 数据 | 是受限概览，不能把 bank 总量当成已显示节点量。[1] |
| 记忆详情页 | 默认 `limit = 1000`；“加载更多”每次把 limit 加 1,000，重新请求；服务端按时间倒序执行 `LIMIT`，返回匹配总量 | 不是按视口流式加载，也不是追加游标分页。已检查的 API 参数只有 `ge=0`、没有数值最大值；没有上限校验不等于能成功渲染 30 万。[3][5] |
| 实体关系页 | UI 发送 `limit: 2000, min_count: 1`；API 明确把该 limit 定义为**共现边数** | 不能写成“2,000 节点上限”；节点量取决于返回边的端点。[6] |
| 空闲画面 | 每帧最多画 6,000 条通过视口检查的边 | 限制的是已加载边中的绘制数量，不是 API 返回边数，也不是存储边数。[2] |

本次本机观察（由同一研究会话的主代理核验）：bank 有 357 条记忆；首页 HUD 为 `200 memories / 200 visible / 0 labels / 6000 edges / .33x`。这与源码的概览策略一致，只证明这幅小规模概览的运行情况。上述官方资料与所查组件没有提供 30 万节点的前端 FPS、内存、首屏时间或拾取延迟测量。

## 当前渲染与交互：自定义 Canvas 2D

当前 `Constellation` 使用 `canvas.getContext("2d")`，并通过 `@chenglou/pretext` 准备、换行文字；并非 Cytoscape、Sigma 或 WebGL 图引擎。包中 `pretext` 为 `^0.0.7`。[7]

| 机制 | 源码事实 | 30 万节点时不能直接照搬的部分 |
|---|---|---|
| 布局 | 默认由节点顺序与 ID hash 生成环形散点，半径随 `sqrt(V)` 增长；没有基于边的力导向求解。可选 scope 分组把组中心放在外环，组内散布；多组模式把节点放在所属组中心的均值附近。[8] | 空间接近主要代表人为分组或散布，不能解释成语义邻近或真实拓扑。组内 `members.indexOf(i)` 对每个成员执行，单大组会出现二次查找成本；它不是大图布局方案。 |
| 动画 | 始终用 `requestAnimationFrame` 调度，平滑 pan/zoom，并给节点漂移、呼吸、边闪烁。暂停按钮只冻结动画时钟，仍会走绘制循环。[9] | 不能把“暂停”当成停止 CPU/GPU 工作；大图应按交互、数据或视口变化重绘，并单独预算可选动画。 |
| 节点绘制 | 每帧新建两个 `Float32Array` 和一个可见性数组，扫描**全部已加载节点**计算屏幕位置与可见性；随后只给视口内节点画圆点/文字。视口外节点不画，但仍参加前面的线性扫描。[10] | 不是空间索引查询。数据在全图自动 fit 时通常大量同时可见；Canvas 逐节点画圆、阴影、文字不能视为 30 万节点能力证明。 |
| 节点拾取 | 鼠标在画布内、未拖动时，每帧扫描全部节点；跳过不可见节点，按屏幕距离选择最近点。命中半径：普通 30 px，`zoom > 1.5` 时 80 px；点击复用 hover 节点。[10][11] | 没有 GPU ID picking、网格或四叉树加速；不能解决本项目当前的大图拾取停顿。 |
| 边与聚焦 | 空闲边细且低透明度，按输入顺序绘制至 6,000 条；同侧越出视口的边跳过。hover 时只画当前节点的入/出关联边，邻居增强、其他点降至 0.08 透明度。**hover 分支没有 6,000 上限**。[2][12] | 6,000 不是“重要边排序”或完整图呈现；高阶节点仍可能暴露大量边。当前组件仅有节点距离命中，没有边拾取实现。 |
| 标签 | 每帧重建屏幕网格避免文字重叠：普通网格 90 px，紧凑实体标签网格 28 px。普通文字在 `zoom > .5` 出现，`>1.5` 变卡片；hover 总是显示，绕过避让，并最后绘制。短标签截断到 45 字符；hover 卡片最多 5 行。[13] | 可以迁移显示规则；不能给 30 万点每帧准备/布局全部文字。实体紧凑模式也不是所有实体名称都能同时显示。 |

边的默认 6,000 限制只在空闲分支的 `linksDrawn` 达标时停止；大量边在视口外时，循环仍可能检查许多边。hover 的邻居判断依赖预建邻接表，但绘制节点时仍会检查其关联边；源码没有独立的大图边空间索引。[2][12]

## 旧版 Cytoscape 与当前版本不可混为一谈

2026-07-02 提交删除了独立 Graph 视图及 `graph-2d.tsx`。删除前的固定提交 `6a479dddb9b044b795415960ad3cc2366c84bd78` 使用 `cytoscape ^3.33.1`、`cytoscape-fcose ^2.2.0`；先 preset 初始化，再运行 fCoSE，显式设置节点分离、较长理想边长及 `nodeDimensionsIncludeLabels: true`。旧记忆 Graph 界面默认 20 个节点、滑块最多 50 个，注释说明是为避免 UI 不稳定；它保留所选节点间的边，并提供双击聚焦邻域、只在聚焦模式悬停高亮边的交互。这可解释旧截图中疏朗的节点，但不能作为 30 万节点选库依据。[14][15]

## 对本项目可借鉴的方向（待确认）

1. **先迁移可读性规则。** 保留现有原生 JS/CSS 与全图 WebGL 路径，在视口范围内安排标签预算与避让；按缩放显示点、短名称、详情卡片，选中节点及邻居有明确优先级。无需为复制视觉效果引入 React、Next.js 或 npm 工程。
2. **全量数据与显示细节分别计数。** 30 万真实节点及真实边仍须完整保留在数据层；HUD 分别说明总节点、已加载节点、当前可见节点、显示标签和已绘边。若采用边密度图、聚合或绘制预算，应让用户知道当前层级，且可继续定位和展开真实对象；不能把固定截取 200/1,000 节点或 6,000 边冒充完整图。
3. **拾取应另行设计并实测。** 优先解决节点/边空间候选索引或 GPU 节点拾取、再做有界的精确边命中；Hindsight 的逐节点扫描不能直接解决本项目已测到的拾取延迟。标签测量只覆盖有机会显示的候选，复用缓冲数组；避免持续执行全部节点的 Canvas 装饰动画。
4. **布局须表达可解释关系。** Hindsight 的 hash 环可借鉴稳定散布的视觉，但本项目若要让空间位置表达真实关系，应另用预计算或分层拓扑坐标；不能把 hash 散点解释成关系社区。

这些是源码分析后的建议，不是已实施改动。30 万节点是否达标，仍须用本项目真实数据、实际边密度、浏览器与目标硬件验证完整加载、交互、搜索定位及长期内存行为；Hindsight 的存储规模和小图外观不能代替验收。

## 当前绘图层的 30 万节点短测

2026-10-26，在本机 Intel Iris Xe / Edge 150 / 1280×1000 下，用未修改的 `full-graph.js` 与仓库现有容量夹具测量；每档连续相机交互 5 秒，再执行 12 次稀疏屏幕位置拾取。仅评估合成数据绘图层，不含业务 API、网络、数据库或详情读取；单次短测不构成端到端、长期稳定性或跨机器容量验收。

| 数据规模 | 平移缩放帧间隔 P95 | JS heap | 定位到节点的拾取 P95 | 稀疏位置/边拾取样本范围 |
|---|---:|---:|---:|---:|
| 15 万节点 / 30 万关系 | 17.0 ms | 62.1 MiB | 2.5 ms | 27.5–71.6 ms |
| 30 万节点 / 60 万关系 | 17.0 ms | 133.1 MiB | 7.8 ms | 86.6–213.9 ms |
| 30 万节点 / 150 万关系 | 17.1 ms | 246.2 MiB | 7.2 ms | 169.7–501.9 ms |

JS heap 不含浏览器进程整体与 GPU 显存。稀疏位置样本是原始最小/最大值，不标作 P95。全部档位的实体、关系数量与实际提交绘制数一致，最终 GL 错误码为 0。原始本机数据：[graph-300k-assessment.json](../../.benchmarks/graph-300k-assessment.json)；诊断入口：[graph-300k-assessment.py](../../.benchmarks/graph-300k-assessment.py)。这两份诊断产物位于 Git 忽略的 `.benchmarks/`，关键测量在本节留档。

源码同时确认三处扩展风险：`Renderer.pick()` 在节点未命中时线性检查全部关系；`sync()` 每批重新生成/上传累计节点与边缓冲；`SessionLoader` 默认每批 400、客户端全部阶段限制至 500，顺序读取 connected 与 entities。当前服务端已允许 entities/relations 单页最高 10,000，connected 则保持 500 上限；因此无需仅为合并大块传输而放弃首批的渐进可见性。[本地绘图实现](../../knowledge_service/web/full-graph.js)、[分页契约](../../knowledge_service/api/graph_sessions.py)、[会话完整性与紧凑记录](../../knowledge_service/services/graph_sessions.py)。

## 建议实施范围与验收（等待确认）

建议保留全量 WebGL，迁移 Hindsight 的可读性规则；先做性能可行性关卡，再统一局部/全量的颜色、节点轮廓、名称样式和交互反馈。

1. **完整性固定为 30 万真实节点。** 同一会话完整保留节点、关系 ID 与版本，核对清单总数与端点；视口外节点仍在完整数据集中且可定位。若后续采用密度/聚合显示，明确标注并能展开到真实节点；不能截取前 200/1,000/6,000 条来冒充完成。
2. **清晰度采用分级细节。** 远景显示清楚的类型色点和低对比关系底图；放大后增加节点轮廓和避让后的短名称；悬停/选中优先显示完整名称与关联关系。保持局部/全量的同类颜色和名称规范，避免给 30 万节点常驻文字、阴影、呼吸动画和实时力导向。
3. **先解加载与拾取瓶颈。** 评估“首批 connected 显示 → 更大的 entities/relations 后台批次”的加载器，在现有会话水位、资格、去重、取消和计数校验下减少请求次数；使用容量预留/几何扩容与增量 GPU 上传，避免每批重建全量数组。用空间候选索引或独立 GPU ID 拾取替代全关系扫描，并先测 GPU 读回开销与真实边选择正确性，不能只测能直接命中节点的点击。
4. **布局另有预算。** 在稳定坐标基础上，可选类型/分组布局或可缓存的后台拓扑布局；空间接近的含义要明确。大量节点的布局计算不进入相机交互热路径，也不在每次打开页面时从头运行。
5. **先以两档关系密度验收。** 30 万节点 / 60 万关系及 30 万节点 / 150 万关系为基线，实际关系更多时补测真实规模。建议待确认目标：加载时可平移、取消和续传；连续平移缩放帧间隔 P95 ≤33 ms；节点、边、空白点击反馈分别 P95 ≤200 ms；30 分钟探索及反复切换无持续内存增长。另行记录首批和完整加载 P50/P95，沿用完整可交互 ≤30 s 作为需要实测证明的初始目标，不从当前合成绘图耗时推断真实加载已达标。浏览器进程和 GPU 内存预算在目标机器测试前固定。

本体扩展改为独立开关按钮仍在原始需求中，但用户已明确先确认性能方案；本次没有改按钮、绘图器或加载器。

## 一手来源

- [0] [官方 v1.1.0 release](https://github.com/vectorize-io/hindsight/releases/tag/v1.1.0)，[固定提交](https://github.com/vectorize-io/hindsight/commit/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5)。
- [1] HomeView：[200 节点与数据量说明](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/home-view.tsx#L19-L22)、[请求](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/home-view.tsx#L114-L132)。
- [2] Constellation：[邻接边与 6,000 预算及 hover 分支](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L622-L637)、[空闲边预算与视口判断](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L679-L713)。
- [3] DataView：[默认 1,000](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/data-view.tsx#L67-L69)、[Load more 增量与重新请求](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/data-view.tsx#L729-L748)。
- [4] [官方性能文档：数据库向量检索规模](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-docs/docs/developer/performance.md#L212-L218)。
- [5] [图 API 参数与调用](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-api-slim/hindsight_api/api/http.py#L5940-L5979)、[计数、排序与 LIMIT](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-api-slim/hindsight_api/engine/memories/pg/graph.py#L235-L253)。
- [6] [实体图 UI 请求 2,000](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/entities-view.tsx#L152-L168)、[API 定义为边数](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-api-slim/hindsight_api/api/http.py#L6984-L7016)。
- [7] [Pretext import](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L3-L7)、[Canvas 2D context](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L548-L553)、[依赖声明](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/package.json#L33-L35)。
- [8] [分组中心、均值、hash 布局、indexOf 与文字预处理](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L353-L471)。
- [9] [暂停只更新状态/时钟](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L275-L296)、[动画时钟与漂移](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L575-L602)、[继续调度 rAF](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L1022-L1025)。
- [10] [全节点屏幕变换、可见性及线性 hover 拾取](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L588-L620)。
- [11] [点击复用 hoverIndex](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L1295-L1306)。
- [12] [只遍历可见节点绘制、邻居判断与明暗层次](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L831-L890)、[邻接表预建](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L499-L534)。
- [13] [标签网格与缩放规则](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L804-L920)、[卡片行数与短标签截断](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-control-plane/src/components/constellation.tsx#L1040-L1094)。
- [14] [删除 Graph 视图的官方提交](https://github.com/vectorize-io/hindsight/commit/36334f27a12c532de14e4d1fceb476f2d57b4821)、[旧依赖](https://github.com/vectorize-io/hindsight/blob/6a479dddb9b044b795415960ad3cc2366c84bd78/hindsight-control-plane/package.json#L58-L59)、[旧布局参数](https://github.com/vectorize-io/hindsight/blob/6a479dddb9b044b795415960ad3cc2366c84bd78/hindsight-control-plane/src/components/graph-2d.tsx#L384-L418)。
- [15] [旧默认 20 节点](https://github.com/vectorize-io/hindsight/blob/6a479dddb9b044b795415960ad3cc2366c84bd78/hindsight-control-plane/src/components/data-view.tsx#L512-L523)、[旧界面 50 节点限制](https://github.com/vectorize-io/hindsight/blob/6a479dddb9b044b795415960ad3cc2366c84bd78/hindsight-control-plane/src/components/data-view.tsx#L963-L990)、[旧聚焦边悬停](https://github.com/vectorize-io/hindsight/blob/6a479dddb9b044b795415960ad3cc2366c84bd78/hindsight-control-plane/src/components/graph-2d.tsx#L448-L463)、[旧邻域聚焦](https://github.com/vectorize-io/hindsight/blob/6a479dddb9b044b795415960ad3cc2366c84bd78/hindsight-control-plane/src/components/graph-2d.tsx#L483-L525)。
