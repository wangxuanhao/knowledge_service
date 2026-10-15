# Graph Maintenance Interaction Implementation Plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** 实现用户已授权的模块 1—4，修复主图谱节点/画布拖动，完善属性维护、本体画布和实例路径循迹。

**Architecture:** 保留现有引擎与修订服务。独立 graph-interaction.js/graph-paths.js 管视图和算法；workspace.js 最少挂接；record-dialog.js 管属性表单；ontology-model-canvas.js 管本体视图位置。OAG 后端不变。

**Tech Stack:** 原生 JS/CSS、ECharts、Cytoscape、node:test、浏览器 fixture。

### Task 1: 当前实现核对与设计审查
- [x] 查当前 OAG 进展、现有编辑弹窗与拖动配置。
- [x] 审查 spec/plan 的范围、隔离与可验证性。

### Task 2: 正式属性表单
Files: knowledge_service/web/record-dialog.js; tests/service/record-attribute-editor.test.cjs。
- [x] 写行为失败测试，运行 node --test 验证缺失能力。
- [x] 读取绑定本体 attributes，渲染主体/类型/值/数据类型/时间，走 saveRevision。
- [x] 验证保留 value/datatype、版本及本体字段；处理错误和重复提交。

### Task 3: 本体画布交互
Files: knowledge_service/web/ontology-model-canvas.js; ontology-model-canvas.css; tests/service/ontology-canvas-interaction.test.cjs。
- [x] 复现/测试节点移动刷新丢失和拖动误选。
- [x] 缓存当前画布坐标、分离适应和重新布局、同步覆盖层。
- [x] 验证选择/连关系/改父类原有能力和项目销毁清理。

### Task 4: 主图谱交互与路径
Files: knowledge_service/web/graph-interaction.js; graph-paths.js; workspace.js; workspace.css; index.html; tests/service/graph-interaction.test.cjs; graph-paths.test.cjs。
- [x] 先写失败的方向/跳数/虚拟边/拖动/视图保持测试。
- [x] 实现纯寻路函数与图谱交互会话，节点 draggable、拖动阈值抑制 click，保留已求解坐标。
- [x] 动态挂接工具条/起终点/方向/路径结果，点击路径边复用检查器。
- [x] 将适应视图改为保留坐标的变换，新增显式重新布局。
- [x] 项目和图谱切换清理，版本资源挂载。

### Task 5: 集成验证与审查
- [x] 相关 Node 回归与语法检查；所有 Python/PG 测试由主代理串行协调，避免和 OAG 冲突。
- [x] 静态 API fixture 下真实浏览器验证拖节点、拖空白、滚轮、选择/路径/表单，不写业务库。
- [x] 独立规格和质量审查，修复后复验。
- [x] 更新操作说明与验证证据；不提交其他聊天已有改动。
