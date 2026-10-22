# 项目测试、脚本与历史文件整理

本轮在已有 31 项存储测试更新之上清理项目文件。只删除确定退役的实现入口、专属旧测试和误导现行部署的说明，不按日期、文件数量、失败状态或未被 import 直接删除文件。

## 盘点结论

- 整理前跟踪的 `scripts/` 共 61 个脚本：32 个用途与现行协议基本一致、17 个专项需适配/复核、3 个历史迁移验证仍有独有安全覆盖、9 个可明确退役。这里“当前”是静态用途分类，不是所有 CLI 已在本轮执行通过。
- 整理前 `tests/` 共 273 个跟踪文件：207 份 service Python 测试、57 份 Node 契约、1 份独立浏览器测试、6 份支持文件，以及被默认入口遗漏的 2 份 Python 文件。没有发现可按“空壳”批量删除的现行测试。
- 大部分测试仍通过 pytest/Node 自动发现使用；共用 fixture、`runpy` 文件和手动容量工具有独立依赖，不能根据是否被 import 判断用途。

## 精确退役清单

共删除 15 个文件，可从 Git 历史恢复原文。

| 文件 | 删除依据与当前去向 |
| --- | --- |
| `scripts/capture_knowledge_service_manual.py` | 仅剩“已停用”壳；当前手册按真实页面故事取图，构建与图片校验入口保留 |
| `scripts/migrate_ui_tokens.py` | 自述一次性主题迁移且硬编码旧工作区，任务已完成 |
| `scripts/shot_ui_theme.py` | 旧主题/旧导航截图助手，个人缓存路径；不是现行功能门禁 |
| `scripts/probe_ui_light.py` | 旧深色主题的人工亮度诊断，没有门禁退出判据；该可选诊断随旧主题助手退役，不声称有完全等价替代 |
| `scripts/verify_workbench_inbox.py` | 头注明确已退役、旧 DOM 必然失败；现行本体治理回归保留 |
| `scripts/verify_milvus_dimension_migration.py` | 任意 collection、无参 ensure 与全局重建预期均已废弃；当前维度冲突拒绝、所选项目重建保护继续验证 |
| `scripts/reset_milvus_indexes.py` | 只识别旧集合/alias，却能全局清 PG 游标；不认识 `knowledge_vectors`，不适合作为当前运维入口 |
| `scripts/prune_milvus_metadata_fields.py` | 固定两个项目、小批次、旧 `bf_` 字段的一次性迁移已完成；当前固定 schema 测试和部署检查点保留 |
| `scripts/smoke_service.py` | 缺当前认证/显式向量化，默认本体及返回字段假设过时；不再作为现行 smoke 推荐 |
| `tests/service/test_milvus_index_reset.py` | 上述旧 reset 工具专属测试，随该工具退役；现行共享集合的构建/回执/清理/隔离测试保留 |
| `tests/test_web_ui_contract.py` | 旧类检查不存在的 `data/kg_web/static` 且一直 skip，末尾当前主入口检查由更强的 `test_frontend_static_contract.py` 覆盖 |
| `docs/KNOWLEDGE_SERVICE.md` | 旧 `.venv-service`、SQLite/FAISS、无登录与旧模型/路由说明；以当前 README 和完整手册替代 |
| `docs/neo4j接入.md` | 旧 SQLite/手动同步和已删除工具说明；现行可选投影边界见完整手册第 24 章 |
| `docs/milvus-向量化改造设计.md` | 旧 SQLite、旧 collection 与历史向量降级方案；现行明确字段/共享索引见当前规格和完整手册 |
| `IDEA.md` | 无引用的单句目标，与 README/CONTEXT 重复 |

`test_docs_contract.py` 保留当前手册构建器、章节、截图与链接验证，只去掉“已停用截图壳必须存在”的检查。历史文档对退役说明的链接改为明确的 Git 历史引用，不把过去的证据换成当前内容。

## 整理后的入口

- [项目 README](../../README.md) 已按启动、存储、目录、测试和真实验收重写，移除旧“一键 smoke/全链可跑”的推荐。
- [测试指南](../../tests/README.md) 解释 pytest/Node/独立浏览器/支持文件的关系和单 pytest 会话约束。
- [脚本索引](../../scripts/README.md) 完整列出保留的 52 个脚本，区分当前用途、待适配专项和迁移安全演练。
- [文档索引](../README.md) 把当前操作、当前规格/证据和历史材料分开，避免“当前/本轮/接手必读”的历史文案误导。
- pytest 的 `testpaths` 新增 `tests/scripts`，补上之前遗漏的 6 项 Docker 迁移安全测试。纯脚本测试也可用独立 unittest 入口绕开全局 PG fixture。

没有直接搬动保留脚本/测试目录：很多文件用 `__file__.parents`、相邻 helper、固定 `runpy` 路径确定资源位置。通过精确删除与用途索引整理，避免把未更新的导入路径变成新的故障。

## 保留范围

所有编号 SQL 迁移、业务代码、模型、上传来源、规则语料、Docker 运行数据，以及真实验收证据和当前手册截图均保留。`.claude` 技能、代码图/IDE 配置和安装元数据不当作垃圾删除。strict xfail 和具有独有保护条件的失败测试保留。

17 个待适配专项包含旧路由、默认本体、旧导航和 bind 挂载假设；它们不会被推荐为当前可运行的全量验收，保留原因与具体缺口已在脚本索引列明。3 个历史迁移演练仍包含数据映射、选择性回填、幂等与演练安全检查，不能因为迁移号早就视为无用。

此前 `.tmp_doc_review` 的归档与去引用已完成，但目录删除请求曾被执行工具以 `blocked by policy` 拒绝，工具未返回具体命中的规则。提交前只读检查发现原目录已不存在；本次复核未执行删除操作，不把当前状态归因于此前被拒绝的命令。3 份归档 JSON 和现行截图输出路径均保留。

## 整理后验证

以下验证均在本轮整理后执行：

| 验证范围 | 实际结果 |
| --- | --- |
| pytest 全量收集 | 2535 项成功收集，包含新纳入的 6 项迁移安全测试；这不是全量执行结果 |
| 相关 Python 回归 | 153 passed，0 失败/跳过，23.84 秒 |
| Node 前端契约 | 438 passed，0 失败/跳过 |
| 独立 unittest 脚本入口 | 6 项通过，确认可绕开 pytest 全局 PG fixture；与上述 Python 范围重叠，不累加 |
| 当前手册构建 | `scripts/build_knowledge_service_manual.py` 构建和静态检查通过 |
| 当前入口文档链接 | README、文档索引、脚本索引、测试指南和本记录的本地链接无缺失 |
| 保留脚本索引 | 52 个脚本全部列入索引，无遗漏 |
| Git diff 空白检查 | `git -c core.safecrlf=false diff --check` 通过 |

153 项 Python 回归的范围为：`tests/scripts/test_migrate_docker_storage.py`、`test_docs_contract.py`、`test_frontend_static_contract.py`、`test_current_storage_contract.py`、`test_fixed_milvus_fields.py`、`test_production_milvus_schema.py`、`test_production_generation_cleanup.py`、`test_shared_milvus_rebuild.py` 和 `test_native_applicability_acceptance_scripts.py`；未写目录的文件均位于 `tests/service/`。Node 使用 `node --test tests/service/*.test.cjs`。

本机原始输出保留在 Git 忽略目录 `.benchmarks/`：`2026-10-22-cleanup-collection.log`、`2026-10-22-cleanup-python.log`、`2026-10-22-cleanup-node.log`。另经最终只读复审，删除的两份测试没有现行独有覆盖；跨测试导入、Node 相对引用、支持夹具和手册构建断言未发现断链。

文件清理不能作为仓库全量测试或尚未完成的容量/问答质量验收通过的证据。本轮没有执行真实模型、容量项目向量化、数据库重建或旧运维脚本。

### 提交前补充复核

提交前重新执行原 153 项集合，并补入 `test_projection_sync.py`、`test_projection_rebuild_lifecycle.py`、`test_projection_runtime.py`、`test_projection_audit_reads.py`：**213 passed，0 failed，0 skipped，21.01 秒**。Node 契约重新执行为 **438 passed，0 failed，0 skipped**，当前手册重新构建和静态检查通过。原始输出在 `.benchmarks/2026-10-22-precommit-python-current.log` 和 `.benchmarks/2026-10-22-precommit-node.log`。

额外浏览器专项最初暴露 4 项旧操作假设，按真实的抽屉和按需候选加载流程更新后，独立复跑 **7 passed，0 failed，0 skipped**。原业务断言完整保留，详见 [浏览器补充记录](2026-10-22-storage-regression-followup.md#提交前浏览器专项)。上述 213 项与 7 项按不同文件分组运行，不能把上一轮的 153/255 或最初含失败的 216 项再累加。全量 2535 项仍仅完成收集检查。
