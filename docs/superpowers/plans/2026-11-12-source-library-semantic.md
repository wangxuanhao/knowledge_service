# Source Library Semantic Search Plan

> **For agentic workers:** REQUIRED: Use subagent-driven-development for backend/UI, followed by independent review. Only one pytest session.

**Goal:** 资料库提供真实关键词/向量正文模式切换。

**Architecture:** 独立来源语义检索服务复用 RetrievalEngine/Milvus，PG 父资料资格门禁先于排名；聚合100片段为有界资料排名，复用列表详情。

**Tech Stack:** Python/FastAPI/PostgreSQL/Milvus、原生 JS/CSS、pytest、Node、Playwright。

- [x] 后端先写失败测试，新增来源语义服务/门禁/模式API和有界摘要读取，验证聚合、范围、错误和游标。
- [x] 前端先写失败交互测试，增加模式、相关片段和召回口径，更新资源版本，验证旧请求失效。
- [x] 独立规格与实现审查，运行关键词、检索、资料库相关回归及桌面/手机 smoke。
- [x] 更新操作说明，重启本地服务并验证；不默认提交、不改并行任务文件。

验证结果：后端/资料目录/原生检索与桌面手机浏览器合并回归 42 passed；相关 Node 交互回归 22 passed。浏览器先复现并修正嵌套 label 将选项文字并入模式名称的问题，再完整复验通过。桌面、390px 手机截图保存在 `.benchmarks/source-library-search/`。本地 8100 服务已重启，健康检查 status=ok、semantic=ready，静态资源包含模式切换与修正后的独立标签。
