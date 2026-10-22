# 页面读取冗余修复计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan. Steps use checkbox syntax for tracking.

**Goal:** 消除已复现的重复与隐藏读取，验证普通项目实际瓶颈。

**Architecture:** 在原生 JS 页面模块内统一请求所有权；鉴权策略和轻量目录从运行诊断、管理统计分离；OCR 能力按资源身份复用校验。沿用独立只读快照与原有治理边界。

**Tech Stack:** FastAPI、psycopg、PostgreSQL、原生 JS、Node test、pytest，唯一 Python 为 llm_model。

---

## 1. 页面所有权（独立切片）

文件：`web/projects-view.js`、`web/records-view.js`、`web/ingest-batches.js`、`web/library-workspace.js`、相关 `tests/service/*.test.cjs`（均相对 `knowledge_service/`，测试在仓库根）。

- [x] RED：`node .benchmarks/page-request-count-audit.cjs` 已运行，4 项失败。
- [x] 项目卡片进入才读；台账 scope 只响应唯一事件；批次页面列表、详情与轮询可见性分离。
- [x] 增加真实模块测试覆盖隐藏/重开/暂停/切项目。
- [x] 重跑请求计数及 Node 契约测试；变更脚本对应 `index.html` 版本统一由主代理更新。

## 2. 启动与能力读取（独立切片）

文件：`api/auth.py`、`api/security_middleware.py`、`web/auth.js`、`integrations/image_ocr.py` 及针对测试。

- [x] 先增加测试：策略读不调运行依赖；模型身份不变重复能力查询只校验一次，修改后重验；初始化仍校验。
- [x] 增加公开轻量策略 API，auth.js 使用它；OCR 缓存有明确失效身份。
- [x] 单个 pytest 会话验证鉴权/能力/OCR，并跑前端 auth 契约。

## 3. 模型与运行页面调用审查

文件：`web/ontology-model.js`、`web/ontology-model-catalog.js`、`web/workbench.js`、`web/task-review.js`、`web/ingest-mode.js`。

- [x] 按请求所有权设计消除草案目录多持有者、旧运行视图预加载、无消费者全局 jobs；保留确有消费者数据。
- [x] 每条新修改先建立可捕获对应冗余/竞态的测试，再运行相关契约；不以简单静态字符串替代行为验证。

## 3a. 轻量项目目录

文件：`knowledge_service/repository/core.py`、`knowledge_service/api/projects.py`、`knowledge_service/web/app.js`、`tests/service/test_project_directory.py`。

- [x] 增加 repository.list_project_directory()：只从 projects 读取 id/name/metadata，不查询 record_versions/ontologies。
- [x] `GET /api/projects?include_counts=false` 使用独立只读视图调用目录方法；默认保持旧项目管理计数响应。
- [x] 项目选择器使用 include_counts=false；请求计数探针按 pathname 识别相同目录，避免 query 参数掩盖重复请求。
- [x] 测试断言默认管理接口兼容、轻量接口不调用计数方法、仓储只发一次 projects SELECT；集成验收确认无 writer 锁等待。

## 4. 整体验证与交付

- [x] 恢复已停止的本地 Docker 引擎和现有服务，不改变容器存储，不迁移业务数据。
- [x] 重测 SELECT1、结果体大小与 SQL 等待，区分数据库计算和客户端等待。
- [x] 正常项目 HTTP 与浏览器页面路径验证，记录请求数和长任务；失败明确边界。
- [x] 汇总前后证据，标明已修复、待验证和后续设计项。保留用户原有脏改动，不做广泛提交。
