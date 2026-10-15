# Record / Ontology Version Consistency Implementation Plan

> **For agentic workers:** Execute sequentially in the current checkout, preserving existing user changes. Use tests before implementation and independent review before completion.

**Goal:** 图谱编辑、知识台账与本体发布使用一致且可追溯的版本语义。

**Architecture:** 保留 record_versions / ontologies 模型，普通修订保留绑定本体。绑定本体解释类层级，前端共用版本格式函数并在保存后刷新消费页面。

**Tech Stack:** FastAPI, PostgreSQL, Python 3.12, native JS, Playwright Edge, node:test.

## 1. 回归测试

- [x] 在 test_entity_class_parents.py 增加历史本体层级、未知绑定、缓存独立性测试。
- [x] 增加 API 实体/关系省略或置空 ontology_id 修订、历史不变、版本冲突与显式归属迁移测试。
- [x] 在 test_records_ledger_ui.py 增加真实 record-dialog 的绑定选项、未知/不可读归属阻止保存与保存刷新测试。
- [x] 新增版本格式 node:test，观察失败。

## 2. 后端修复

- [x] api/knowledge.py 的普通修订保留已有 ontology_id。
- [x] services/service.py 的 ontology_family 支持明确发布 id，缓存按发布区分。
- [x] api 详情及 services/explorer.py 按记录绑定获取层级，未知本体无层级。

## 3. 前端修复

- [x] 添加窄共享版本标签工具，records-view / record-dialog / evidence-inspector 使用同一规则。
- [x] 编辑按绑定本体加载选项；显示原修订、保存后修订和本体不变提示。
- [x] 保存成功刷新台账与图谱，保护窗口/项目切换与重复保存。
- [x] 明确本体筛选与当前治理状态；同步 index.html 缓存版本与测试加载器。

## 4. 验证与交付

- [x] 运行相关 Python 后端和浏览器测试、前端 Node 契约测试。
- [x] 独立审查改动，修复实质问题，确认 git diff 不覆盖用户既有修改。
- [x] 说明后端关联规则与前端显示规则，报告真实测试结果和运行服务更新状态。

## 验证结果

- 后端扩大回归 165 项通过；共享格式器抽取导致的 1 项旧静态断言已更新，后续 workbench 回归通过。
- 最终编辑/并发/错误分支与父类浏览器回归 13 项通过；实体与关系真实服务闭环 2 项通过。
- Node 前端契约全量 78 项通过。
- 8100 服务已重启，health 返回 HTTP 200 / ok；最终编辑资源返回 HTTP 200。
- 独立审查指出的读取失败、刷新失败、旧保存回退、旧加载错误覆盖问题均已修正。测试使用独立 PostgreSQL 测试库，未修改业务知识或本体。
