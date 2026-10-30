# Mindmap Explorer Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 补齐用户确认的 Archify 对标差距：搜索聚焦、小地图、事实路径追踪和完整 PNG/SVG 导出。

**Architecture:** 扩展现有树视图；模型与导出是独立 UMD 模块，探索控制通过明确视图 API 使用同一棵树。路径按 occurrence ID 计算并明确无方向信息，规范导出从快照重新布局全部已加载后代。

**Tech Stack:** 原生 JavaScript/CSS、现有 ECharts、node:test、已有 Playwright/Edge，无新依赖。使用当前工作区保留用户未提交的修改，不创建空白工作树。

## Task 1: Model and canonical export

Files: create `knowledge_service/web/entity-mindmap-model.js`, `knowledge_service/web/entity-mindmap-export.js`; tests `tests/service/entity-mindmap-model.test.cjs`, `tests/service/entity-mindmap-export.test.cjs`.

- [x] 写失败测试并运行 `node --test tests/service/entity-mindmap-model.test.cjs tests/service/entity-mindmap-export.test.cjs`：覆盖重复实体出现位置、隐藏节点匹配、父链展开、同节点/不存在端点/跨分支路径、特殊字符 SVG 转义及完整后代。
- [x] 按设计接口实现纯模型；输入均为 prepareTree 的 `{id:occurrenceId,entityId,name,...}`；search 返回匹配节点数组；pathBetween 返回 `{nodeIds,edgeIds}` 或 null，其中 edge ID 是子节点 occurrence ID；reveal 只返回根到父的祖先 ID，视图负责展开。
- [x] 实现完整树的确定性导出布局及安全 SVG，白底、标题/截断说明、卡片/实体类型/predicate；PNG 复用 SVG 栅格化并有边长及总像素限制；下载前后检查 isCurrent，释放对象 URL。
- [x] 运行定向测试，检查纯函数不修改输入；先规格核对，再质量核对。

## Task 2: View and exploration UI

Files: modify `knowledge_service/web/entity-mindmap-view.js`, `knowledge_service/web/workspace.js`, `knowledge_service/web/index.html`, `knowledge_service/web/mindmap.css`; create `knowledge_service/web/entity-mindmap-explorer.js`.

- [x] 扩展现有浏览器回归先写失败用例：搜索折叠后代聚焦、跨分支路径清除、点击小地图真实移动主视图、空图清理和规范导出。
- [x] 按设计给视图增加 getTree/focus/trace/clearTrace/pan/geometry/subscribe/snapshot；程序平移显式重绘 ECharts model-only 动作；绘制保持字体等比缩放，ResizeObserver 保持已有相机。
- [x] 添加紧凑搜索工具条、结果列表、折叠路径面板、可开关小地图、SVG/PNG 导出按钮。清空失效全部 UI；导出捕捉 generation，失败可重试。
- [x] 在 HTML 中先载入模型/导出，再视图、探索控制和 workspace；仅修改本功能资源的缓存版本号，保留已有 request-recovery 修改。
- [x] 实际工作台 mount 一次；浏览器 fixture 载入相同模块与结构。

## Task 3: Verification and handoff

Files: `tests/service/entity-mindmap-view.browser.cjs`, `tests/README.md` and this plan.

- [x] `NODE_PATH` 指向既有 bundled Node modules，运行 `node --test tests/service/entity-mindmap-view.browser.cjs`，保留真实交互及下载证据。
- [x] 在浏览器读取 SVG XML、节点数和 label，确认无当前高亮/小地图；PNG 读取真实宽高/像素，完整下载独立于活动视窗。
- [x] 1440×900、1920×1080、390×844 检查并截图到 `.benchmarks/mindmap-explorer/`，人工查看截图排版，修复遮挡/横向溢出。
- [x] `node --test tests/service/*.test.cjs`；`git diff --check` 仅核对触及文件；本机 8100 静态文件和磁盘一致。
- [x] 规格核对和独立代码评审通过后更新测试指南/勾选计划，报告实际能力和真实边界，不自动提交用户工作区。

## 验收结果

- 真实 ECharts 浏览器回归：14/14 通过，覆盖三个屏幕尺寸、实际相机移动、完整文件下载、文字边界与异步失效。
- 前端默认 Node 回归：747/747 通过。
- 独立规格与代码质量复核通过；SVG 长字溢出和重新挂载路径残留已修复并锁定回归。
- 已人工查看桌面、手机截图和完整 PNG 导出；本机 8100 新资源与磁盘一致。
- 未提交或覆盖用户其他工作区改动。
