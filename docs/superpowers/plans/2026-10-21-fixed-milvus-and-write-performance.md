# 固定 Milvus 字段与写入性能 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking. 用户已要求直接继续实施；沿用当前 checkout，保留其他 dirty 改动，不新增确认暂停。

**Goal:** 取消 bf_ 自动扩展并解决现场已复现的健康误报、构建与单写性能问题，保留治理语义和现有测试项目。

**Architecture:** PostgreSQL 权威源；Milvus 仅固定 21 列并按项目显式构建。普通文本修订只在依赖可证明闭合时使用有界权威快照，其余保留全量校验与执行 CAS。

**Tech Stack:** Python 3.12 conda llm_model、PostgreSQL 18、Milvus 3.0.2、原生 JS/Node test。

---

### Task 1: 固定向量字段与健康探测（主代理）

Files: domain/search_filter_catalog.py, domain/search_projection.py, integrations/milvus_store.py, services/projection_runtime.py, repository/projection_store.py；对应 test_search_filter_catalog/test_search_projection/test_flat_milvus_store/test_runtime_capabilities/test_projection_explicit_build。

- [x] 先写并运行失败测试：metadata 变化不扩列/不要求重建；bf_ 命名不自动生成；旧扩展目录须显式重建；健康维度探测无创建；正常 upsert 不强制 flush。
- [x] 覆盖 source_id 只映射 chunk_owner_id：实体/关系多来源不能借此列伪造筛选；保留 assertions 来源读取。
- [x] 移除自动目录发现，构建使用固定目录；普通业务字段只走固定列。旧目录拒绝增量与搜索，显式构建可恢复。
- [x] 实现只读维度探测；对初建固定索引采用明确的有界就绪检查；Strong 检索/校验不依赖每条 flush。
- [x] 运行针对性测试并检查当前投影/删除/租约隔离回归。

### Task 2: 前端筛选边界（独立代理）

Files: web/scope-fields.js, web/workspace.js, web/workspace-context-ui.js, web/workbench.js, web/index.html；scope-fields/request-contract/相关 Node 测试。

- [x] Node 回归先红：当前检索拒绝不支持字段且不改 AST；历史 PG、资料库/台账、图谱保留 metadata；问答重试同样验证。
- [x] 固定检索用途展示明确业务筛选；图谱用途仍可发现 metadata，既有条件保留并显示不兼容提示。
- [x] bump 修改资源版本；运行相关 Node 测试，不启动服务和 pytest。

### Task 3: 大库单写（巨型文件代理）

Files: repository/core.py, services/service.py, services/formal_writes.py, repository/formal_write_planner.py / 必要的新小模块；相关 Python 测试。不得修改 Task 1 文件。

- [x] 先补失败测试和精确读集方案。首先替换历史时间 fetchall，保留两表高水位、锁、微秒与异常时间合同。
- [x] 给安全单条纯 text 修订构造有界读集并保留全部事实键/来源/CAS/断言依赖；SHACL/复杂操作回退全量。未证明闭合时不得提交局部路径。
- [x] root 统一串行运行 pytest 红/绿；代理不独立启动 pytest，以免互删测试库。
- [x] 保留外部 detached 计划冲突验证；记录实际减少的读取范围。

### Task 4: 审查、现场变更与复测（主代理）

- [x] 独立审查 3 项实现及治理/租约/时态边界，修复真实问题。
- [x] 只运行一个 Python 测试会话，Node 单独执行；检查语法和所改文件 diff。
- [x] 停唯一应用、备份派生目录/字段与计数；通过 Milvus 3.0 SDK 在线删除旧 bf_ 标量和其索引，验证向量数量及剩余 21 列；不 drop collection。
- [x] 落盘迁移检查点和明确白名单：仅 eeb3a4dc-7475-4937-9966-74aa261334e6 / 828887db-e1f3-4f5d-b2b3-4b3dc97d2040 可显式向量化；容量项目 46249548-2189-4ad7-8f23-70ee2bcc7f6f 排除。失败时保持 requires_rebuild、禁止后台或请求自动重建。
- [x] 启唯一普通服务，显式构建前次选定普通项目。确认 ready、水位、物理字段、健康状态与零降级检索。
- [x] 控制资源负载后复测普通检索/增量及保留容量项目单条编辑恢复/并行读取/分页；保存新的原始证据和报告，不覆盖初次问题证据。
- [x] 更新相关手册/方案中自动 metadata 字段的旧说明，最终按实测样本报告完成项与剩余限制。

### 最终证据与未通过项

见 [固定字段与容量复测报告](../../research/2026-10-21-fixed-fields-and-capacity-report.md)。340 项后端综合通过（另有 1 个既有 xfail）、最终图谱相关 64 项通过、Node 435 项通过；测试集合有重叠，不相加。最终服务 PID 44644，健康 ok、readyz 200；只读 schema 复核为 21 列、无 bf_，两个普通项目有效投影 37/5。

容量项目 20 次文字修改/恢复零失败，P95 5.236 秒，探针恢复到原文、版本 41。最终局部图 1/3/10 跳各 20 次 P95 0.125/0.169/0.550 秒；一次 HTTP 全枚举 29.468 秒，计数、唯一 ID 及端点完整。

清单创建 P95 2.115 秒仍未达到 2 秒目标，整体性能目标汇总为 false；HTTP 单次枚举不构成浏览器加载/FPS 或 P95 认证。容量项目本轮未向量化；复杂治理写入保留完整校验，未承诺同样速度。首次语义预热含冷模型加载 43.156 秒，已与暖态结果分别记录。上述限制不以任务勾选隐藏。
