# 本体建模层展示与交互整理

**Goal:** 按用户指定的 Archify 展示方向，让继承与业务关系易读、点选可聚焦、节点详情简洁、类型限制通过选择编辑。

**Architecture:** 继续使用原生 JS/CSS 与 Cytoscape，保留主控作为选中和草案命令的唯一入口。画布负责布局、镜头和直接关系高亮；详情负责问号说明与带搜索的多选类型控件。编辑沿用草案、审核、发布语义。

## 方案

采用现有画布上的定向整理，避免替换引擎影响拖拽、历史查看和增量更新。继承图按父子方向排布，业务关系保留不同线型；选中后将直接关系提升并淡化无关元素。相较仅加粗线条，此方案同时解决布局与阅读焦点；相较重新实现 SVG 编辑器，能保留现有编辑能力。

详情保留定义、属性、关系、继承和状态。解释移入可悬浮、键盘聚焦、点击和 Escape 关闭的问号提示。关系端点以中文标题展示，通过搜索、多选和应用按钮一次性提交增删差量；保留多类型与未限定语义，历史版本只读。

## 实施与验证

- [x] 在 `tests/service/ontology-canvas-interaction.test.cjs` 与 `ontology-model-refresh.test.cjs` 增加聚焦、高亮和清除回归，先验证失败。
- [x] 修改 `knowledge_service/web/ontology-model-canvas.js`：继承布局与线型、直接邻接高亮、节点/关系/属性聚焦、全图恢复；保留拖拽坐标与刷新时的视口。
- [x] 修改 `ontology-model.js`：所有选中入口完成渲染后调用聚焦，术语卡片视图保持原行为。
- [x] 修改 `ontology-model-panel.js` / `.css`：问号帮助、精简标题、搜索多选类型、原子差量命令与错误反馈；生命周期操作默认折叠。
- [x] 浏览器验证真实 DOM/Cytoscape，包括点击节点、关系、帮助悬浮及键盘、选择器应用/失败、历史只读、桌面及窄屏边界；截图存放 `.benchmarks/ontology-clarity/`。
- [x] 只更新 `index.html` 中本次修改资源的缓存版本，保留工作区已有改动。
- [x] 运行相关 Node 契约与离线浏览器验收，检查 diff 和语法；记录实际结果。

## 验证结果

- `node --test tests/service/ontology*.test.cjs tests/service/actor-request-contract.test.cjs`：214 项通过，0 失败。完整输出：`.benchmarks/ontology-clarity/node-tests.txt`。
- `test_frontend_static_contract.py` 中与本体建模、生命周期、版本及资源版本相关的 14 项纯静态函数通过；使用 runpy 直接执行，不启动 pytest/PG 会话。
- `D:/anaconda/envs/llm_model/python.exe -X utf8 tests/service/ontology-model-clarity.browser.py`：1600、1280、390 三种宽度全部通过，无页面脚本错误。调用真实前端模块与 Cytoscape，API 使用离线夹具，无业务写入或模型调用。
- 最终窄屏高度修正后再次执行 390 宽度检查，确认详情使用抽屉的可用高度。
- JS 语法、Python 编译及相关文件 `git diff --check` 通过。人工查看了桌面聚焦、类型选择器和窄屏截图。
- 独立审查发现的远端点缩放限制和稀疏本体横向压缩已修复并完成红绿回归；复审未发现未解决的问题。

开发阶段保留工作区已有改动，不重置或移动它们；后续按用户明确要求统一检查、汇总提交并推送到 `origin/master`。最初记忆与代码图服务不可用，已尝试后回退源码；后续代码图查询使用项目 `D-workspace-knowledge_service_v2-knowledge_service`。

## 父类选择器后续修正

用户反馈继承编辑下方有白色空白、已有父类不直观。浏览器复现收起的选择器高 55px、摘要高 38px：清理选择器摘要的外边距及容器内边距后，两者只相差 2px 边框。外层继承标题现在直接展示全部已声明父类；无父类时显示「暂无父类」，展开后自动勾选既有声明，应用成功后同步预览。

验证：216 项相关 Node 契约通过；`ontology-model-clarity.browser.py --parents 1600 1280 390` 覆盖空父类间距、已有父类、预选状态、自继承排除及父类增删，三种宽度均通过。截图在 `.benchmarks/ontology-clarity/parents-*.png`，JS 语法、Python 编译及相关文件 diff 检查通过。

## 关系端点与推荐依据整理

用户反馈关系端点选择区也需直接展示现有值，实际用法依据的文字与样例过于杂乱。关系编辑的折叠标题现按「主体 / 客体」展示全部已配置类型，应用后只更新对应一端；编辑态使用该区展示当前值，历史态继续保留只读列表，减少重复信息。

推荐区使用服务端返回的知识条数和各类型次数，按主体、客体分组；核对样例改成默认收起的独立卡片，实体名称与类型分层显示。保留主动写入草案、审核发布、无依据不推荐、两端依据不完整不启用写入的规则，未改动 `applyEndpoints` 命令流程；未知使用次数不再展示成零。

验证：220 项 Node 契约、15 项相关静态检查通过；`ontology-model-clarity.browser.py --relations 1600 1280 390` 在三种宽度验证当前端点、预选、闭合间距、原始次数、样例折叠、明确草案写入及历史只读。独立审查未发现新的问题。截图：`.benchmarks/ontology-clarity/recommendation-{compact,evidence}-{width}.png`；JS 语法、Python 编译及相关文件 diff 检查通过。
