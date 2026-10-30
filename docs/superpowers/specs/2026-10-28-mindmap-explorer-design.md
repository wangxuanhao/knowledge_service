# 实体关系脑图交互对齐设计

用户已在对比 Archify 后要求补齐列出的差距：搜索聚焦、小地图、关系追踪和 PNG/SVG 导出。现有缩放、平移、复位和范围隔离必须保持。

## 方案与范围

扩展现有 ECharts 树视图，分离纯数据索引、探索控制和文件导出。复用项目原生 JS/CSS 和现有浅绿界面，不新增构建依赖。直接嵌入 Archify 独立文档会产生两套数据/视图生命周期；整体重写 SVG 渲染会扩大已验证缩放的回归面，因此不采用。

## 单一数据来源

仅使用服务端本次已加载的树。每个节点出现位置有独立 occurrence ID；实体 ID 只作为事实标识及搜索字段，同一实体重复出现不能合并。索引保存节点、父节点和树枝关系。当前 API 双向遍历后只返回 predicate，没有关系方向与关系 ID，因此本轮明确提供「脑图路径（不表示关系方向）」：任意两个出现位置间的树路径，仅高亮真实树枝并展示其 predicate，不声称有向可达性。截断提示和总节点数始终可见。

## 用户操作

- 画布工具条增加本图节点搜索，匹配名称、类型标签、实体 ID。结果显示节点类型和实体 ID，区分重复实体；显示匹配数量并限制一次展示 30 个。选择结果展开祖先分支、聚焦并高亮节点，保留其他分支。
- 关系路径区有起点、终点、追踪、清除和可读路径明细。选项使用 occurrence ID，追踪展开路径所需分支、突出路径节点和树枝。不存在的端点不能沿用旧结果；同一端点是零条关系路径。清除不删除事实。
- 小地图展示当前可见树和视窗矩形，随缩放、平移、展开、画布尺寸变化更新；点击/键盘操作小地图平移主视窗，可隐藏。它没有独立知识状态。
- 导出完整已加载树，包含当前折叠的后代，独立于缩放、平移、搜索/路径高亮及小地图。SVG 保留文字；PNG 白底且有合理像素上限，必要时整体缩小，绝不裁掉节点。文件含标题和「已加载 N 个节点」说明，截断结果须标注「部分结果」。下载失败显示可读状态，异步导出不能在切换项目/重新生成后下载旧数据。
- 空图禁用操作；换项目/清空时同步移除搜索、路径、小地图和待处理任务。所有用户数据通过 textContent/安全 XML 转义，文件导出不包含活动脚本、外部资源或凭据。

## 模块接口

所有模型、视图和导出接口统一接收 `prepareTree` 已转换的树：`{id: occurrenceId,entityId,name,type,predicate,collapsed,children}`；仅 prepareTree 从原始 API 的 `{id,text}` 分配出现位置 ID，indexTree 不再改写 ID。reveal 是只读查询，返回由根到父的祖先 ID 数组，视图按该数组展开自身状态。snapshot 返回 null 或深拷贝 `{tree,truncated}`。geometry 返回 `{nodes:[{id,name,x,y,width,height}],width,height}`，x/y 是卡片中心，width/height 是当前显示尺寸，坐标位于主画布像素空间。subscribe 回调接收 `{type:'render'|'view'|'clear'}`，返回取消订阅函数。

- `entity-mindmap-model.js`：UMD；`indexTree(tree)` 返回 `{nodes:Map,parents:Map,edges:Array}`，edge 为 `{id:childOccurrenceId,parentId,childId,predicate}`；`search(index,query,labelOf)` 返回节点数组，空查询返回空数组；`pathBetween(index,fromId,toId)` 返回 `{nodeIds,edgeIds}` 或 null；`reveal(index,id)` 返回祖先 ID 数组；`canonicalTree(tree)` 深拷贝并取消全部折叠。全部不修改输入，不依赖 DOM/ECharts。
- `entity-mindmap-export.js`：UMD；`buildSvg(tree,{labelOf,colorFor,truncated})` 返回 `{svg,width,height,count}`；`download(snapshot,format,{filename,isCurrent})` 返回尺寸，SVG/PNG 使用同一规范图。独立布局保持卡片不重叠，完整保留所有出现位置。
- `entity-mindmap-view.js`：保留现有 create/render/zoom/reset/clear；增加 `getTree()`、`focus(id)`、`trace(fromId,toId)`、`clearTrace()`、`pan(dx,dy)`、`geometry()`、`subscribe(fn)`、`snapshot()`。几何返回当前可见出现位置的画布像素坐标和卡片尺寸；订阅覆盖渲染、缩放、平移和清空。snapshot 是深拷贝，不暴露可变事实。焦点和路径装饰由视图管理。
- `entity-mindmap-explorer.js`：用视图 API 装配现有页面的探索工具、搜索结果、路径面板、小地图和导出。mount 返回 destroy；重复 mount 不重复绑定。工作台只在创建视图时 mount 一次。

## 验证

Node 测试验证重复 ID、折叠后代搜索、路径和 XML 安全/完整导出。真实浏览器验证搜索隐藏节点聚焦、路径与清除、小地图改变相机且不改知识、PNG/SVG 下载完整性及项目切换时作废；复跑现有缩放/字体/平移/分支回归。在 1440×900、1920×1080、390×844 检查无横向溢出和可操作性。不得将工具存在或百分比变化当作交互成功证据。
