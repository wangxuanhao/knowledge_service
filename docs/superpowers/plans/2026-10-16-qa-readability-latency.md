# 问答可读性与延迟实施计划

**Goal:** 落实用户确认的问答设计，并消除经测量确认的检索重复开销。
**Architecture:** 保持现有检索与有效性边界；独立安全正文渲染模块、问答样式模块，主控制器只接入。Milvus 集合热路径状态在适配器内管理。
**Tech Stack:** 原生 JS/CSS、FastAPI、pymilvus、pytest、node:test、Playwright。

1. [x] 在 scripts 添加可复现的只读阶段计时工具，测量同一业务问题，避免输出密钥、向量或提示词。
2. [x] tests/service 添加集合重复加载/检查和生命周期回归，先验证失败，再改 integrations/milvus_store.py；必要时仅在请求内共享投影检查。
3. [x] services/answers.py 添加结构化回答模式和未调用原因；用实际 source validity 条件改进空证据文案，保留无有效证据不调用规则回答模型的边界。添加针对性测试。
4. [x] web/qa-presentation.js 安全渲染受限 Markdown 与模式/耗时；web/qa-page.css 提供紧凑系统布局。web/workbench.js 接入终态与历史、折叠证据，index.html 更新结构和缓存。
5. [x] 单会话运行相关 Python 测试；运行全部 Node 契约测试；浏览器验证真实宽窄屏、引用与安全正文；必要时重启服务并重测API。git diff --check，保留现有改动，不自动提交。
