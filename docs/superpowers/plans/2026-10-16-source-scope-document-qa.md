# 来源范围与文档问答实施计划

**Goal:** 修复来源范围丢失，并让文档定义问题使用可追溯原文回答。
**Architecture:** 独立的来源范围物化/修复服务与问答目的策略；不改变向量模型、真值有效期或候选快照。原生前端只展示明确的回答模式。
**Tech Stack:** Python/FastAPI/PostgreSQL/Milvus、原生JS、pytest/node:test/Playwright。

1. [x] 来源范围子任务：冻结来源版本补齐物化元数据，两个入口；生成历史58条的只读修复计划与版本保护执行路径；回归测试。
2. [x] 问答子任务：共享文档/规则目的判定与输入白名单；JSON/SSE、最终引用审计和qualified_quote冻结审计一致；模型调用与原文回退测试。
3. [x] 前端显示文档说明模式及有效性标识，引用保持可点击，更新缓存版本；Node测试。
4. [x] 审查两个子任务并修正；单会话测试。核对历史修复的来源后执行正常版本更新及投影同步。重启服务，真实问题检索与模型回答验证。

## Verified 2026-10-16

- Backend regression suite: 110 passed; browser: 7 passed; Node: 136 passed.
- Projection replay regression suite: 37 passed; original adapter reproduced partial-batch acknowledgement failure.
- Source repair: 58 records; business dates and fact content preserved.
- Milvus cursor applied_seq=watermark=192, ready=true.
- Live production service search: both Meituan price queries returned 10 hits, _backend=milvus, degraded=false.
- Explicit Meituan scope returns relevant hits; Jingdong scope returns zero.
- Model cold initialization is substantial; warm complete retrieval measured approximately 0.6-1.4 seconds.
- Evidence: ../.tmp_doc_review/meituan-milvus-backend.json and meituan-milvus-verified.json.
