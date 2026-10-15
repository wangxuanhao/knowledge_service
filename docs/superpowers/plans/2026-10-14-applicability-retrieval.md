# Applicability-aware retrieval Implementation Plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development to implement this plan. Steps use checkbox syntax for tracking.

**Goal:** 同项目多平台规则从上传到问答不混用，支持发布时间推定生效及可空失效时间。

**Architecture:** 共享纯范围模块负责规范化、指纹、匹配、问题平台解析；Pydantic与摄取共用日期语义。现有PostgreSQL metadata存储范围，既有查询候选前置过滤贯穿Milvus与图扩展，保留双时态与来源断言。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic / PostgreSQL / Milvus / 原生JS，当前工作区集成，保留用户已有修改。

## Task 1: 范围和时间契约

Files: 新建 domain/applicability.py；修改 models.py、domain/formal_facts.py、services/entity_resolution.py、integrations/semantica_adapter.py；新建 tests/service/test_applicability.py。

- [ ] 写并运行失败测试：范围列表规范化/匹配/身份，生效回退和开放式结束，NER与属性冲突。
- [ ] 实现 Applicability 模型和纯函数 normalize_applicability / scope_identity / matches_applicability / resolve_query_applicability；非空scope参与身份，空scope保留旧键。
- [ ] 验证模型/实体解析/属性路由回归。不要并行运行两个pytest进程。

## Task 2: 摄取与检索链路

Files: services/service.py、services/answers.py、services/retrieval.py、services/reconciliation.py、services/governance.py（合并保护）、integrations/milvus_store.py、新建 services/retrieval_context.py；tests/service/test_applicability_integration.py。

- [ ] 先写失败测试：三平台摄取、检索、图扩展、实体合并、日期、JSON/SSE范围。
- [ ] ingest 对原始dict也校验范围/日期；在融合前给抽取记录、候选及断言继承权威范围，保持来源 occurrence ID 与关系端点一致；规范实体身份在融合/治理层按完整范围隔离。
- [ ] 文档规范范围覆盖抽取同名字段；reconciliation实体与关系池要求范围精确相等；显式治理合并拒绝不同范围。
- [ ] scoped 先匹配scope后检查端点；search/QA统一解析问题平台，默认时间和显式冲突；无命中不扩大范围。
- [ ] 多平台逐平台检索/额度/空组；Milvus空候选短路，大于后端ID过滤上限时受控本地降级，禁止空列表变成无过滤。
- [ ] 增加同范围同文档邻接chunk补充，返回补充原因；答案提供范围与来源标记。
- [ ] 运行定向集成与现有检索/问答/摄取回归。

## Task 3: 上传与检索界面

Files: web/index.html、web/ingest-mode.js、web/workbench.js；tests/service/applicability_ui.test.cjs。

- [ ] 写JS契约测试并确认失败。
- [ ] 上传字段：平台/业务/对象（逗号分隔），发布时间/生效/失效时间（可空）；实时说明有效生效来源及批量共用字段。
- [ ] 文本/单文件/批量统一传递；检索/QA增加平台范围选择，结果/来源显示范围和时间信息，服务端解析结果可见。
- [ ] bump修改资源版本，同步直接依赖的旧契约。
- [ ] node测试和浏览器检查。

## Task 4: 审核与最终验证

- [ ] 独立规范与质量审查，修复跨范围泄漏、身份冲突、时间继承问题。
- [ ] 运行Python与Node适当回归、git diff --check。
- [ ] 更新用户文档，说明未标范围历史资料不进入特定平台查询，不批量猜测改写；不承诺模型答案绝对正确。
