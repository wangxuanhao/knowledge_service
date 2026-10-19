# 图谱交互性能修复

用户反馈：拖动节点、平移和缩放明显卡顿。

## 原因与实现

- 平移每次 mousemove 都分派视口更新，释放时还提交整图数据。改为 requestAnimationFrame 合并位移，释放时刷完剩余位移，不再整图更新。
- 缩放按钮通过 getOption/setOption 复制并更新整图。改为读取当前 series model，复用 ECharts GraphView 原生 controller 更新视口和实际图形。
- 无路径高亮时，挂载仍触发样式重绘。现在仅清理实际存在的高亮；重新布局用 ID Map 匹配节点，并合并为一次更新。
- 保留节点拖动后的坐标提交，保证后续窗口变化不会使位置跳回。切换图谱会取消旧的待处理手势。
- ResizeObserver 仍会在窗口监听器已调用 chart.resize 后重算像素边界，避免缩小窗口裁切节点。

## 验证

2026-10-16，真实无头 Edge，生产 ECharts 与交互模块，合成 500 节点 / 1500 边。修改前为原始 5 次中位数，修改后为可视平移修复后的 3 次中位数。以下是同步主线程操作耗时，不能当作帧率或所有业务数据的保证。

| 操作 | 修改前 | 修改后 | 修改后整图更新次数 |
| --- | ---: | ---: | ---: |
| 120 次连续平移事件 | 154.4 ms | 3.8 ms | 0 |
| 无路径高亮的挂载 | 152.6 ms | 14.7 ms | 0 |
| 12 次缩放按钮操作 | 1430.8 ms | 84.6 ms | 0 |
| 重新布局 | 347.1 ms | 202.4 ms | 1 |

真实鼠标检查通过：外侧节点拖动且邻居保持位置、拖动不误选、背景平移、实际缩放比例、路径高亮清理、外部 resize 后边界重算、视图操作不修改知识事实。136 项 Node 契约测试通过。本地 8100 返回新缓存版本；真实页面 28 实体/30 关系验证背景平移可见生效。

### 可视平移修复与测试纠正

用户确认问题发生在「检索与交互图谱」，并非「本体建模层」。
此前直接 dispatch `graphRoam`，但 bundled ECharts 注册该 action 为 `update:'none'`：
只更新 coordinateSystem，GraphView 原生 controller 才负责移动实际图形组。
因此事件在处理、模型坐标在变化，屏幕上的图却不移动。缩放按钮也存在同一问题。

现在合帧后的 pan 和按钮 zoom 都交给当前 GraphView 原生 controller，复用其变换、符号缩放、边/标签布局，
保留 `roam:'scale'` 避免原生和自定义 pan 重复执行。此适配依赖已捆绑 ECharts 的内部 controller，
升级 vendor 时必须重新执行浏览器回归。

旧验证读取 coordinateSystem.dataToPoint，曾误报平移和缩放通过，旧缩放 0.6ms 不代表可见缩放生效。
现在改读 ItemGraphicEl.transformCoordToGlobal 的实际绘制位置，精确断言平移位移与缩放比例；
修复前该断言失败，修复后通过。动作计数也改为监听 graphroam 事件，覆盖原生 ExtensionAPI 分发。

参考 [Semantica 官方 Explorer](https://github.com/semantica-agi/semantica/blob/main/explorer/README.md)
及本地 `explorer/src/workspaces/GraphWorkspace/GraphCanvas.tsx`：它使用 Sigma.js 的 camera，
将视口更新与图数据更新分开。官网 demo 在当前浏览器访问超时，未以其流畅度作实测对比。

复现命令（仓库根目录）：

```powershell
D:/anaconda/envs/llm_model/python.exe scripts/benchmark_graph_interaction.py --nodes 500 --repeats 5 --output ../.tmp_doc_review/graph-performance-after.json --check --verify
node --test tests/service/*.test.cjs
```

仍保留节点释放时的一次坐标提交；此轮没有更换渲染引擎或减少知识数据。

## 本体编辑画布：层带同步

上述 ECharts 验证没有覆盖本体编辑画布。该画布与本地参考项目
`D:/workspace/references/Ontology-Playground/src/components/OntologyGraph.tsx`
都使用 Cytoscape；参考项目没有逐次 `position/pan/zoom` 扫描全部节点并改写额外 SVG 的逻辑。
本项目的分层视图原来在每次事件里重算所有层带；普通图谱视图无此开销。

现在将高频事件合并到动画帧，只重算移动节点所属的层带，平移/缩放通过单个 SVG 组变换完成。
销毁/切换视图时取消待执行更新。节点坐标、选中、两步连线和增量更新语义保持不变。

真实无头 Edge，生产 Cytoscape/JS/CSS，160 个类、159 条继承边、120 次连续事件：

| 分层操作 | 修改前同步耗时 | 修改后同步耗时 | SVG 属性写入（前 → 后） | 节点查询（前 → 后） |
| --- | ---: | ---: | ---: | ---: |
| 节点移动 | 153.2 ms | 2.6 ms | 34,440 → 6 | 19,200 → 1 |
| 平移 | 69.1 ms | 1.2 ms | 34,440 → 1 | 19,200 → 0 |

这些是事件突发的同步主线程耗时，不代表真实业务页面的帧率提升倍数。
真实鼠标拖动、背景平移、滚轮缩放和操作后的层带对齐均通过；136 项 Node 契约测试通过。
本地 8100 返回新缓存版本和优化后的脚本。

600 类补测也通过同一套交互/几何断言。真实抓取节点状态下，普通图谱/分层视图的帧间隔
中位数约 28.2/21.9 ms，仍有大图绘制成本；空白平移约 16.7 ms。
同一视口分别尝试纯色节点、小字号裁剪、视口纹理缓存，收益不足以解释主要卡顿，未纳入生产改动。
`--probe` 保留这些单变量诊断；`programmatic_frames` 表示程序改坐标，
`grabbed_drag_frames` 才是保留渲染器抓取状态的逐帧移动，两者不能混为拖动帧率。

```powershell
D:/anaconda/envs/llm_model/python.exe scripts/benchmark_ontology_canvas.py --check
node --test tests/service/*.test.cjs
```
