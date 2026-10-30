# 统一时间筛选实施与验证

日期：2026-10-28。依据用户认可的 [Semantica 对标](2026-10-28-semantica-temporal-reference.md)及补充要求“问答和检索的筛选这块也要考虑到”“继续”实施。

## 已实现

- 共享筛选提供“全部业务时间 / 当前有效 / 指定日期”，默认全部；当前有效在应用时固定为明确 UTC 时点。指定日期为空或无效时保留旧条件。
- 生效时间未知可显式纳入或排除；全部业务时间仍包含未知记录。系统历史时点单独保存，不与业务时间混用。历史语义/混合查询按既有能力提示改用关键词，不清空条件或暗中切模式。
- 检索范围可复制到问答。问答移除独立日期覆盖入口；每次提问只捕获一次范围，传输、提交注记、用户/助手历史和重试使用同一份冻结值。
- 严格时间筛选同时检查记录及具体来源出现。只有失效日期、没有生效起点的记录也属于生效时间未知；发布时间推定不升级为问答的确定有效依据。失效/未来参考补读、原始命中、分组与上下文不能绕过这条规则。
- 图谱时间轴明确为系统修订时间；检查器分别展示未知生效、未设结束时间、时间来源、原文发布日期和系统记录时间。抽取来源要有原文、可信置信度及匹配的实际边界；缺少继承来源标记时不猜测。
- 未改动的日期保留后端微秒精度及夏令时重复小时原本的 UTC 时点；手机筛选浮层限制在屏幕内，长 ISO 条件可换行。

相关实现：[时间辅助模块](../../knowledge_service/web/temporal-scope.js)、[共享筛选](../../knowledge_service/web/workspace-context-ui.js)、[问答工作台](../../knowledge_service/web/workbench.js)、[问答有效性](../../knowledge_service/services/answer_validity.py)。

## 验证结果

### Python：134 passed，3 个依赖弃用提示

使用既有 Python 3.12 环境；没有并行运行两个 pytest 会话。验证使用独立测试数据库 `knowledge_temporal_regression_20261028`，不连接业务库。

```powershell
$env:KG_TEST_POSTGRES_DB = 'knowledge_temporal_regression_20261028'
& 'D:\anaconda\envs\llm_model\python.exe' -m pytest -q `
  tests/service/test_temporal_scope_filters.py `
  tests/service/test_temporal_scope_browser.py `
  tests/service/test_unrestricted_retrieval_scope.py `
  tests/service/test_rule_document_oag.py `
  tests/service/test_document_qa_policy.py `
  tests/service/test_qa_record_and_source_filter_scope.py `
  tests/service/test_temporal_reference_provenance.py `
  tests/service/test_source_scope_inheritance.py `
  tests/service/test_milvus_business_filters.py `
  tests/service/test_qa_source_context_authorization.py `
  tests/service/test_qa_presentation_browser.py
```

结果：`134 passed, 3 warnings in 261.17s`。提示来自 Starlette/AnyIO 与 WebSocket 依赖的弃用 API。

- 新增 [时间过滤服务回归](../../tests/service/test_temporal_scope_filters.py)：半开边界、全部时间、严格未知开关、来源未知、已知失效但起点未知、混合来源出现、参考和完整 JSON 的排除。
- 新增 [真实浏览器回归](../../tests/service/test_temporal_scope_browser.py)：搜索/问答实际请求、复制范围、无效日期、系统历史限制、旧回答注记和手机浮层边界。采用独立 HTTP/API 夹具，不调用模型。
- 同步旧原生过滤用例：原 `_filter_expr` 已被现有 `compile_scope` 取代，当前测试检查真实编译入口、参数化的微秒时间及未知开关，不添加旧接口兼容实现。

### Node：相关检查通过；全量快照 813/815

- 时间模块单独 16/16 通过，包含真实 `TZ=America/New_York` 子进程的重复小时回归。
- 最后一次相关工作台命令输出 53/53 通过；实现阶段含其他检查器/适用范围用例的较大专项集合也已通过。
- 全量命令 `node --test tests/service/*.test.cjs` 的最后快照：815 项，813 通过，2 项失败。工作区同期存在独立图谱改动，本轮保留其源文件和行为。
- 未解决的两项为 `full-graph-progressive.test.cjs` 的缓存关系像素深度，以及 `graph-types.test.cjs` 的 `graphRequests.invalidate()` 次数（期望 1，当前图谱逻辑有 2 处）；本轮只修改该模块的时间轴文案，没有改变图谱失效计数。
- [完整 Node 日志](../../.benchmarks/unified-temporal-node-final.log)记录这次快照；不能将本轮时间专项通过说成全仓测试全绿。

### 浏览器与审查

- 真实 Edge 的共享筛选/问答流程通过；390px 视口检查浮层左右边界及页面横向溢出。
- [手机截图](../../.benchmarks/temporal-scope/mobile-filters.png)已人工检查，时间选择、未知开关、系统历史与应用按钮完整可见。
- 需求审查及代码质量审查均已通过；审查发现的未知来源旁路、缺起点但已失效、夏令时重新应用偏移均已补回归并修复。

## 交付边界

本轮修改代码与说明，不回填历史日期，不修改业务资料，不提交或部署。服务用例使用真实 PostgreSQL、确定性关键词排名夹具；原生 Milvus 条件通过编译契约验证，不等于真实 Milvus 或 LLM 的端到端验收。运行中的开发后端需要重启、页面需要刷新才能加载全部改动。
