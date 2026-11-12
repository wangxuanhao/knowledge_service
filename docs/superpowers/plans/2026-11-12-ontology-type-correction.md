# Ontology Type Correction Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 用户可预览并审核重复类型合并，停用时选择替代，并可靠迁移旧类型实例。

**Architecture:** 复用草案原子操作编译、乐观锁、发布和 Reclassify。新增纯合并规划模块与草案预览，不直接修改正式数据。

**Tech Stack:** Python/FastAPI/rdflib/PostgreSQL、原生 JS、pytest、node:test。

## Task 1: Backend merge planning and guarded draft command

- [x] 在 `tests/service/test_ontology_type_merge.py` 写结构与 API 行为失败用例（沿用 seed_ontology 和 workbench 夹具）。先执行单次 pytest，记录失败。
- [x] 新增 `knowledge_service/services/ontology_type_merge.py`，以源和目标 class 编译预览及规范原子操作；扩展 `ontology_drafts_commands.py` 接入 merge_class。
- [x] 在 `api/ontology_drafts.py` 新增类型合并预览请求模型和路由，仅编排参数与领域服务。
- [x] 校验 self/retired/wrong-kind、未知引用、父类循环、预览指纹、revision 和记录成员版本变化，确认写入前重新规划，失败无部分操作。
- [x] 执行新增测试及 ontology_operations、ontology_drafts、ontology_draft_api 回归。

## Task 2: UI entry points

- [x] 为类型详情和停用替代流程添加行为契约（node:test），先看到预期失败。
- [x] 修改 `web/ontology-model-panel.js`：合并目标、预览、原因和确认；停用可选替代及阶段说明。根据需要在 `ontology-model.js` 增加只读预览封装，避免复制草案状态管理。
- [x] 对项目、草案身份/修订、目标与选择变动进行异步防护。复用已有迁移入口。
- [x] 更新 `index.html` 资源版本与依赖这些字符串的契约断言，执行前端契约与语法检查。

## Task 3: Replacement chains and integration

- [x] 在 `test_reclassify.py` 写多跳替代与歧义/循环拒绝的失败用例。
- [x] 修改 `services/reclassify.py` 的目标解析，校验唯一、同种类、活动终点；复用当前与历史映射但不随意择一。
- [x] 验证合并发布后 plan 推荐目标，迁移保持实例 ID、版本历史、关联关系与撤销路径。

## Task 4: Review and delivery

- [x] 由独立代理做规格符合性和代码质量审查，修复可复现问题。
- [x] 顺序运行相关 Python 测试、全部 Node 契约及必要 UI 验证；不把 skip 当作通过。
- [x] 更新调研/用户操作说明，报告已支持的操作和真实边界。不要自动发布项目草案、迁移真实数据或提交工作区其他改动。

## 执行结果（2026-11-12）

- Task 1–3 已实现并经过独立规格/质量审查；初次审查的短名称记录影响面漏算已修正，并增加红绿回归。连续合并保留既有弃用替代链。
- 新增合并测试 15 项通过；重分类 17 项通过（包括新增替代链、歧义/循环/种类冲突和撤销验证）。
- 核心组合回归 290 项通过；新增端到端用例最初因错误使用批量批准高风险操作失败，改为工作台的逐定义审核后，完整链路通过。业务批准门禁未放宽。
- 全部 Node 契约 1054 项通过，无跳过；JS 语法和 diff 空白检查通过。
- 扩展静态/浏览器检查 72 项通过、3 项失败；对 HEAD 本体未改动的 web 归档重跑同三项，全部复现相同失败，确认是既有上传版本断言、导航断言及属性台账多元素选择器问题。未改动无关业务代码。
- 使用临时测试项目完成草案、逐定义审核、发布、显式迁移、历史与撤销验证；未发布真实用户草案，未迁移真实实体。
- 操作说明：`docs/2026-11-12-类型纠正与合并操作说明.md`。实现保留结构发布与实例迁移两个显式阶段，专用合并只支持实体类型。

- 本地 8100 服务已重启；模型初始化完成后 `/api/health` 返回 status=ok，实际提供的新脚本包含合并入口和停用替代选择。
