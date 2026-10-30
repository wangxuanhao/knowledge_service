# 生效 / 失效时间与 Semantica Explorer 对标

核对日期：2026-10-28。初次核对范围为 Explorer 的 `now`、业务有效期、系统记录时间及当前服务的对应能力。用户随后认可建议，补充要求覆盖问答和检索筛选并要求继续；后续实施见第 5 节。

**Explorer 上的 `now` 不能直接解释成“缺失的生效 / 失效时间被写成当前时间”。** 已定位的两处分别是 Bounds 文案的缺省值，以及时间滑块缺少最大边界时使用的视窗上限 / 初始游标。当前服务应保留未知业务时间，优先完善时间来源与双时间轴表达，不应据此自动补写 `valid_from=now`。[S1][S2][L1][L2]

## 1. Explorer 的 `now` 具体来自哪里

- 时间叠层 Bounds 使用 `(temporal?.minDate ?? "1970") → (temporal?.maxDate ?? "now")`。这是展示回退；该表达式没有写入事实时间。[S1]
- `resolveScrubberBounds` 在缺少 / 无法解析最小边界时取 1970，缺少最大边界时取调用方传入的 `now`；默认游标是限制在边界内的 `now`。例如已有最大时间为 2025，默认游标会停在 2025，并非总停在今天。[S2]
- `TimelinePanel` 用 `useMemo(() => new Date(), [])` 在每次挂载时捕获一次当前时间；这不是持续走动的实时时钟。[S3]
- **Semantica 的不同写入入口确实存在不同默认值。** `BiTemporalFact.from_relationship({})` 保留 `valid_from=None`、`valid_until=OPEN`，缺少 `recorded_at` 时使用 `valid_from` 或当前时间；而 `GraphBuilder.add_temporal_edge` 会把缺省 `valid_from` 设为当前时间。应区分显示、模型转换和具体建边 API，不能概括为“Semantica 一律补 now”或“一律不补 now”。[S4][S5]

## 2. 与当前服务的能力比较

| 维度 | Semantica 已核对行为 | 当前服务已核对行为 / 差距 |
| --- | --- | --- |
| 无业务时间 | 模型可保留未知起点 / 开放终点；特定建边 API 默认起点为当前时间。[S4][S5] | `IngestSettings` 未填时间时为 `None / None`，来源为 `unknown`；仓储规范化不补业务时间。[L1][L2] |
| 发布时间与生效时间 | 上述模型转换未体现独立的发布时间优先规则。[S4] | 显式 `valid_from` 优先，否则回退 `published_at`，并记录 `effective_time_source=explicit/published_at/unknown`。这是当前服务的业务策略，不能视为“发布时间天然等于生效时间”。[L1] |
| 系统时间 | 模型含 `recorded_at / superseded_at`；转换时缺省记录时间可能来自业务起点。[S4] | 仓储写入时独立生成 `recorded_at`，修订关闭旧版本；业务有效期与系统版本区间分开保存。[L3] |
| 区间边界 | 区间推理的 `active_at` 使用 `[start, end)`。[S6] | 查询使用 `valid_from ≤ valid_at < valid_until`，系统历史使用 `recorded_at ≤ known_at < superseded_at`；快照适配额外排除等于结束时刻的记录。[L4][L5] |
| 时间查询 | `query_at_time / reconstruct_at_time / query_time_range` 有 `time_axis`，可选业务、交易或两者。[S7] | `valid_at` 和 `known_at` 可独立传入；不选 `valid_at` 不过滤业务有效期；默认 `include_unknown=True`，因此时间点结果也可能含起点未知记录。[L4] |
| 时间抽取 | 本次未验证中文制度文档的端到端抽取准确率，不能由 Explorer 界面推断。 | 已核对关系抽取：保留时间原文 / 置信度；置信度至少 0.5 才提升 ISO 时间，无法解析的表述留在 metadata，反向区间不提升。尚不能据此声称实体、属性抽取全部等价。[L6] |
| 来源继承 | 本次未比较所有导入 / 来源继承路径。 | 候选没有相应字段时，可从已核验的不可变来源文档版本继承业务时间；这是文档级继承，未证明事实本身具有同一有效期。[L7] |
| 可视化时间轴 | 当前核对的 Bounds / 滑块提供时间浏览范围；`now` 是其默认上限之一。[S1–S3] | 图谱“知识时间”选项实际修改 `known_at`，是系统修订轴；检查器已分别显示业务生效、业务失效和系统记录，空值分别显示“未知”“未设定”。[L8][L9] |

## 3. 可选路径与建议

| 路径 | 收益 | 约束 / 建议 |
| --- | --- | --- |
| A. 先澄清显示和来源（推荐） | 保持现有双时态查询，消除 `now`、未知、生效与录入时间的混淆 | 统一空值文案；展示可证明的时间来源；把业务筛选和系统修订轴分开。本轮实施范围见第 5 节。 |
| B. 增加独立业务时间滑块 | 直观看某时刻有效的实体和关系，可借鉴 Explorer 的范围 / 游标交互 | 1970 / now 只作为明确标注的浏览边界；未知起点记录需单独计数 / 开关，不能伪造为时间事件。 |
| C. 扩展抽取与时间推理 | 可进一步对标 Semantica 的区间查询 / 推理 API | 先用中文条款评估明确日期、相对生效、废止、替代及事实级例外；本次不据源码存在性判断生产可用性。 |

初始建议路径 A 的具体语义（本轮已实施部分见第 5 节）：

1. 检查器缺少起点显示 **“未知”**，缺少终点显示 **“未设结束时间”**；后者仅表示没有记录结束边界，不保证永远有效。系统时间单独显示“系统记录 / 系统替代”。
2. 图谱业务筛选默认显示 **“全部业务时间”**；用户选择“当前有效”后才发送显式 `valid_at=当前时刻`。系统轴独立显示“当前系统版本”或指定 `known_at`，允许组合“某业务日期、截至某系统日期已知”的查询。
3. 显示时间来源，例如“用户明确指定 / 发布时间回退 / 事实原文抽取 / 来源文档继承 / 未知”，抽取时展示原文和置信度，继承时指向具体文档版本。当前 `effective_time_source` 只证明上传设置三种来源，统一事实级来源仍需后续设计。
4. 若增业务滑块，无最大日期可将 now 用作可见范围上限，但标注“浏览截至”；无最小日期不把 1970 写成事实生效时间。**不自动用上传、抽取或当前时间回填未知业务时间。**

## 4. 初次对标验证与限制

- 静态核对固定官方提交 `e1489f78838cabca7e01e734a4d84fa535e7a8c3`，并读取当前工作区对应实现；本机包是 Semantica 0.7.0，不能默认与该提交全部一致。
- 已运行当前 `IngestSettings`：无时间得到 `None / None / unknown`；仅发布时间时回退生效起点；显式起点覆盖发布时间。
- 已用 Node 执行当前前端范围存储：新项目搜索默认值为 `valid_at=null`、`known_at=null`、`include_unknown=true`，与“全部业务时间 / 当前系统版本 / 包含未知”一致。
- 已运行本机 `BiTemporalFact` 空输入与区间边界检查：未知 / 开放边界保留；`active_at(start)=True`、`active_at(end)=False`。
- 已用 Node 26 执行上述官方提交的原始 `temporalScrubberBounds.ts`：固定 now 为 `2026-10-28T09:00:00Z`，无边界得到 `[1970, now]`，只有 2020 起点得到 `[2020, now]`，最大边界为 2025 时游标停在 2025；各次输入序列化结果不变。源码保存在忽略目录 `.benchmarks/semantica-temporal-reference-20261028/`。
- 这些是模型 / 纯函数运行检查与源码审计；未重跑生产写入、真实文档 LLM 抽取或双时态数据库端到端验收，没有新增测试文件。

## 5. 用户确认后的实施

- 共享筛选新增“全部业务时间 / 当前有效 / 指定日期”及“包含生效时间未知的记录”，覆盖检索、问答及复用该范围的图谱。当前有效在应用时固定成真实 UTC 时间；重开筛选显示该已固定日期，不向后端发送 `now` 或 UI 模式字段。
- 系统历史时点独立保留，图谱时间轴改称“系统修订时间”。历史语义/混合检索继续明确提示现有能力限制，关键词模式保留两条时间轴。
- 问答移除重复日期输入，提交请求、注记、保存历史和重试共用一次捕获的范围；后续修改筛选不改写旧回答。未修改的时间保留微秒和夏令时重复小时的原 UTC 时点。
- 严格排除未知时间时，同步检查知识记录、来源文档和各条来源出现；即使失效时间已知，也不能把未知的生效起点重新引入历史参考。相同排除决定覆盖回答依据、参考、原始命中、平台分组及上下文。
- 检查器区分未知起点、未设结束时间、原文发布日期和系统记录时间。抽取来源标记要求时间原文、置信度及当前边界一致；没有来源继承标记时保持未知。未扩展来源继承标记落库、批量日期回填或时间推理引擎。
- 详细实现与验证见 [统一时间筛选验证记录](2026-10-28-unified-temporal-scope-verification.md)，设计与实施步骤分别见 [设计](../superpowers/specs/2026-10-28-unified-temporal-scope-design.md)和[计划](../superpowers/plans/2026-10-28-unified-temporal-scope.md)。

## 一手来源

官方链接均固定到同一提交；本地链接指向本次核对的工作区源码，后续修改可能移动行号。

- [S1] [Explorer 时间叠层 Bounds，第 94 行](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/plugins/temporalOverlayPlugin.tsx#L94)。
- [S2] [滑块边界与默认游标，第 13、34–39 行](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/temporalScrubberBounds.ts#L13)。
- [S3] [挂载时捕获 now，第 68–74 行](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/explorer/src/workspaces/GraphWorkspace/TimelinePanel.tsx#L68)。
- [S4] [BiTemporalFact.from_relationship，第 44–58 行](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/semantica/kg/temporal_model.py#L44)。
- [S5] [add_temporal_edge 的当前时间回退，第 1202 行](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/semantica/kg/graph_builder.py#L1202)。
- [S6] [active_at 半开区间，第 95–113 行](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/semantica/kg/temporal_reasoning.py#L95)。
- [S7] [TemporalQuery 时间查询，第 107 行起](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/semantica/kg/temporal_query.py#L107)，[时间范围查询，第 363 行起](https://github.com/semantica-agi/semantica/blob/e1489f78838cabca7e01e734a4d84fa535e7a8c3/semantica/kg/temporal_query.py#L363)。
- [L1] [上传设置的有效期与来源：models.py:139](../../knowledge_service/models.py#L139)。
- [L2] [业务时间规范化：repository/core.py:845](../../knowledge_service/repository/core.py#L845)。
- [L3] [系统版本写入：repository/core.py:930](../../knowledge_service/repository/core.py#L930)。
- [L4] [双时间查询与未知起点策略：repository/core.py:1426](../../knowledge_service/repository/core.py#L1426)。
- [L5] [快照边界适配：integrations/semantica_adapter.py:21](../../knowledge_service/integrations/semantica_adapter.py#L21)。
- [L6] [抽取时间规范化：integrations/semantica_adapter.py:84](../../knowledge_service/integrations/semantica_adapter.py#L84)，[关系写入：665](../../knowledge_service/integrations/semantica_adapter.py#L665)。
- [L7] [来源文档时间继承：services/source_scope.py:6](../../knowledge_service/services/source_scope.py#L6)。
- [L8] [知识时间轴：web/workspace.js:186](../../knowledge_service/web/workspace.js#L186)。
- [L9] [检查器时间字段：web/evidence-inspector.js:89](../../knowledge_service/web/evidence-inspector.js#L89)。
