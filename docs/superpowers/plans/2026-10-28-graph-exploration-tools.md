# Graph Exploration Tools Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在现有图谱上增加有界邻居列表、局部分支展开/收起和静态邻域聚焦，保留加载、范围隔离和纯 CPU 交互能力。

**Architecture:** 独立纯模型负责增量邻接索引与局部分支归属；独立工具面板连接局部/全量适配器。局部组合现有样式，全量只在显式聚焦后绘制有界 Canvas2D 覆盖层，常规绘制保持原路径。

**Tech Stack:** 原生 JS/CSS、ECharts、现有 FullGraph、Node node:test、既有 Python Playwright HTTP 夹具与软件 Edge。

**Approved spec:** `docs/superpowers/specs/2026-10-28-graph-exploration-tools-design.md`

**Workspace:** 复用当前在途 checkout；不另起工作树丢失当前图谱修复。禁止全量暂存、reset 或混入其他任务的未提交变更。提交/推送不在本轮要求内。

## Task 1 — 纯模型

Files: create `knowledge_service/web/graph-exploration.js`, `tests/service/graph-exploration.test.cjs`.

- [x] 先编写行为测试并运行，确认因模型缺失失败。
- [x] 实现 `NeighborIndex(source)`，source 提供 `records`（或 `nodes`）、`edges` 和 `nodeIndex`；`sync()` 仅消费追加；`page(id,{direction,limit,cursor})` 返回关系与邻居、方向、下页游标及当前已加载计数；追加后旧游标重置。
- [x] 实现 `focus(id,{hops,direction,maxNodes,maxEdges,maxScannedEdges})`，返回节点/边 ID Set、截断状态、扫描数，防止高出度节点无限遍历。方向 both/forward/backward，排除属性虚拟节点作为锚点。
- [x] 实现 `BranchSession`：reset 基础图、expand 按锚点归属、collapse 隐藏独有分支和无可见锚点后代、graph 取得合并图、clear 与范围键隔离；保留共享记录和稳定顺序，按局部总预算截断并报告。
- [x] 分支票据 begin/accept：collapse、reset 和 clear 失效迟到响应；以基础节点为根求可见固定点，测试共享 X 的后代和不受基础支持的 X↔Y 环。邻居/聚焦使用当前显示类型投影。
- [x] 运行 `node --test tests/service/graph-exploration.test.cjs`，修复并进行规格/质量审查。

## Task 2 — 面板与局部接入

Files: create `graph-explorer.js`, `graph-explorer.css`; modify `workspace.js`, `graph-workbench.js`, `index.html`; create `tests/service/graph-explorer.test.cjs`.

- [x] 测试面板分页、选择切换、关闭、聚焦开关、空态、过期响应和共享分支后再实现。
- [x] 共用面板只渲染一页关系，textContent 防注入；点击使用既有详情。只增加一个邻居入口，聚焦/方向/跳数/展开/收起放面板内。
- [x] 初始 draw/刷新重置分支；显式 expand 保留基础，使用既有 graphRequests 与范围票据。清空、模式/范围变化失效；失败保留原图。
- [x] 聚焦从预存样式派生，路径强调优先，名称开关、类型筛选不变；保留原手工坐标和相机。收起不清除其他工具状态。
- [x] 资源按依赖顺序加载并 bump 变化文件版本；不更改无关资源版本。

## Task 3 — 全量接入

Files: modify `full-graph.js`, `full-graph-view.js`; add tests to `full-graph-renderer.test.cjs` and model tests.

- [x] 为索引追加、聚焦覆盖、退出清理、标签关闭、类型筛选/数据集替换失效写失败测试。
- [x] 全量 dataset 复用已有 nodeIndex，随新增边更新 typed 邻接索引；不复制全量对象，不触碰 SessionLoader 的 scope/watermark/worker/export 语义。
- [x] 全量选择事件接入共用面板；聚焦只准备受限索引和 Canvas2D 叠绘，常规 GL 缓存和拾取不变，加载计数不因聚焦变化。
- [x] 新批次、类型、范围、重启使聚焦/邻居缓存按正确版本刷新；全量扩展明确进入局部，不混合两类数据集。

## Task 4 — 回归、浏览器与性能

Files: create `tests/service/graph-exploration.browser.py`; update `docs/research/2026-10-29-graph-exploration-validation.md` and plan checklist.

- [x] 运行新模型/UI Node 测试，以及现有 local-neighborhood、graph-paths、graph-interaction、graph-types、graph-display-controls、full-graph-renderer/progressive 回归。分别记录已存在失败，不删断言掩盖问题。
- [x] 使用独立 Python 夹具运行浏览器验证，避免并行 pytest 与全局 PG fixture。真实软件浏览器覆盖分支、聚焦、共享节点、名称/类型、路径、详情、全屏、响应式同高与所有工具可达。
- [x] 保存改前目标文件至忽略目录；相同输入比较索引加载/追加、面板开关与 focus；大规模实验设置上限并保留失败结果。确认无每帧全量扫描、无新增常驻动画。
- [x] 分规格与质量审查，修复重要问题后重跑相关验证，保存截图和结论。完成的定义为功能和回归证据齐全；不宣称生产 30 万全流程已经验收。

## 完成与限制（2026-10-29）

第一轮功能、规格/质量审查、160 项 Node 回归和真实软件浏览器验证已完成。性能短测保留冷启动与预热结果；原有 30 万节点首次软件栅格化仍有约 4 秒等待，未作为本轮已解决项。具体证据与生产验收边界见验证报告。
