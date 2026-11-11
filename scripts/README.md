# 脚本使用索引

这里是**手动工具**，不等于默认测试。日常回归入口见 [tests/README.md](../tests/README.md)。下文区分当前入口、需要适配或复核的专项，以及迁移安全工具；静态分类不能当作所有脚本本轮全部执行通过。

## 常用入口

| 要做什么 | 入口 | 条件与结果 |
| --- | --- | --- |
| 启动应用 | [start-service.ps1](start-service.ps1) 或 `python -m knowledge_service --port 8100` | 已有 conda 环境和基础设施；正常业务服务 |
| 生成当前手册 | [build_knowledge_service_manual.py](build_knowledge_service_manual.py) | 不连接业务库；生成并静态校验当前 MD/HTML |
| 打包部分平台规则与媒体 | [package_rule_documents.py](package_rule_documents.py) | 每份选中 MD 各生成一个 v2 单资料 ZIP，收集引用图片、视频；也可按顺序打包图片合集，输出清单和核对报告 |
| 真实上传到图谱验收 | [verify_platform_rule_pipeline.py](verify_platform_rule_pipeline.py) | 创建隔离项目，通过真实 OCR/LLM、候选选择、本体审核发布、来源校验、索引、三类检索和图谱会话验证最小关系链，保存状态支持恢复 |
| 静态校验当前手册 | [verify_redesign_manual.py](verify_redesign_manual.py) | 检查章节、路由、链接、图片；`--build` 可同时生成 |
| 内容与权威数据差分 | [verify_production_content.py](verify_production_content.py) | 正常认证和真实服务；默认依赖保留的指定验收项目 ID，不是任意部署的通用 smoke |
| 部署前后存储检查点 | [production_storage_checkpoint.py](production_storage_checkpoint.py) | 读取 PG 和 Milvus schema/计数/回执；`--output` 保存，`--compare` 对比 |
| 真实账号权限专题 | [verify_auth_rbac.py](verify_auth_rbac.py) | 真实认证专题，需要指定服务和可用账号 |

本机服务健康可直接请求 `GET http://127.0.0.1:8100/api/health`；健康通过不替代检索、写入、发布或容量验收。旧 `smoke_service.py` 已移除，因为它依赖已废弃的默认本体/自动向量化假设和返回字段。

### 平台规则单资料 ZIP 打包

在仓库根目录运行 `python scripts/package_rule_documents.py --help` 查看当前参数。源目录只读，输出目录必须在源目录之外且尚不存在。**每个 ZIP 只上传为一份资料**；选择十份 MD 就生成十个 ZIP，不再按 200 份 MD 合包。连续截图可用有序图片合集生成一个 ZIP。

| 参数 | 用法与约束 |
| --- | --- |
| `--root`、`--output-dir` | 必填；源文件根目录与本次新输出目录 |
| `--file` | 相对根目录的 MD 路径，可重复 |
| `--list-file` | UTF-8/BOM 路径清单，可重复；空行和 `#` 注释忽略，可与 `--file` 按顺序混用 |
| `--manifest` | JSON 选件清单，不能与 `--file` / `--list-file` 同用；可读旧 `documents` 多项或 v2 单资料，MD 仍逐份输出 ZIP 并保留 title、metadata |
| `--image`、`--title` | 按阅读顺序重复 `--image`，组成一份 PNG/JPEG/WebP 图片资料；`--title` 只用于此模式，不能同时使用 MD 选件参数 |
| `--max-zip-bytes` | 默认 24,000,000，最大 25,000,000；单份资料超限报错，不自动拆开 |
| `--max-documents` | 已弃用的兼容参数，不能恢复多资料合包 |

只打包一份真实规则：

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' scripts/package_rule_documents.py `
  --root 'D:\workspace\知识图谱源数据\平台经济规则' `
  --file '抖音电商/创作者管理_「违规营销：憋单」实施细则_aHsCBTFBK4YS.md' `
  --output-dir 'D:\workspace\knowledge_service_v2\outputs\unified-media-example\single-rule-packages'
```

成功后检查 `rule-documents-001.zip` 和 `packaging-report.json`。报告包含每包资料/媒体路径、字节数、SHA256 和外链警告。脚本校验包结构及媒体，不执行 OCR/ASR。

旧多资料包不能直接作为新上传：先保留原包并解压副本，再以解压目录为 `--root`、旧清单为 `--manifest`，或用 `--file` / `--list-file` 选择部分 MD，输出多个新版包。CLI 的多项输入清单仍可用，但生成的上传包必须使用 `schema_version: 2` 与一个 `document`。已入库历史来源保持原有读取方式。

新版 ZIP 的 `document.entry` 指定一个主文件，或用 `document.pages` 明确图片页序；两个字段互斥。无清单包只允许一个根主文件及 `assets/` 附件，单个外层目录可归一化。独立图片/图片页限静态 PNG/JPEG/WebP，每张 10 MiB、4000 万像素；所有 ZIP 还受单文件 25,000,000 字节上限约束。

完整目录、两种 manifest、`--list-file` 与图片合集命令及错误处理见[资料上传与 ZIP 打包操作手册](../docs/平台规则资料上传与ZIP打包操作手册.md)。页面只在第 1 步显示完整上传区；第 2、3 步显示文件摘要，可点击“返回修改文件”回到第 1 步追加，队列保留。新样例输出统一放在 `D:\workspace\knowledge_service_v2\outputs\unified-media-example\packages\`；文件生成与验收结论须分别核对。

已上传资料可在详情的“重新处理 / 构建图谱”中选择“仅文档检索”“开放发现”或“使用项目本体”，复用固定来源创建新处理代际，不必重复打包上传；资料 ID 和处理历史保留。仅文档模式执行解析、OCR/ASR、切片与检索处理，暂不抽取实体关系。单项 OCR 重试只更新识别结果；需要更新切片、检索或图谱时执行整份重新处理。项目索引首次需显式构建，已有项目仍须核对增量同步就绪。

### 真实规则链路验收

在仓库根目录与现有环境运行。文件可用打包工具按 `--file` 或 `--list-file` 选取。验收命令会在隔离项目审核并发布所选的一条关系及其两个实体，默认采用 10000 字符固定窗口。账户从当前 `.env` 读取，也可使用 `KG_ACCEPTANCE_USERNAME`、`KG_ACCEPTANCE_PASSWORD` 指定。

```powershell
& 'D:\anaconda\envs\llm_model\python.exe' scripts/verify_platform_rule_pipeline.py `
  --file 'D:\workspace\knowledge_service_v2\outputs\unified-media-example\packages\rule-documents-001.zip' `
  --output-dir '.benchmarks/my-rule-acceptance' `
  --platform '抖音电商'
```

首次运行创建项目与批次，再次使用相同输入和输出目录可恢复。输出包括 `state.json`、候选和发布回执、固定来源校验、索引状态、三类检索及图谱结果，完成后生成 `report.json`。质量拦截会保存失败阶段并返回非零，需要在资料库审核、补件或重试后继续。已有记录缺少来源哈希时，命令根据固定文档与不可变切片独立验证，再核对 MinIO 原件和原包哈希。正式本体定义继续按业务审核确定。

## 运维与模型工具

执行前先读对应参数和运维说明；文件复制、模型准备、来源修复与服务启动不是只读操作：

- [archive_windows_docker_bind.py](archive_windows_docker_bind.py)、[copy_stopped_docker_volume.sh](copy_stopped_docker_volume.sh)、[migrate_docker_storage.py](migrate_docker_storage.py)：停止写入后的归档、复制与校验，配合 [迁移指南](../docs/2026-10-19-Docker-Linux-volume-migration.md)。
- [install_ocr_models.py](install_ocr_models.py)、[prepare_model_layout.py](prepare_model_layout.py)：本地模型安装和布局。
- [prepare_document_models.py](prepare_document_models.py)：部署前准备 Docling 的布局、表格和 RapidOCR 权重。默认写入 `data/model/document-parser/docling`，`--output-dir` 可指定目录，`--check` 仅校验已有文件。运行服务配置 `KG_DOCUMENT_ARTIFACTS_PATH`；PDF 请求只使用本地模型，缺失时明确返回不可用，DOCX/TXT 不依赖 PDF 模型。
- [prepare_embedding_runtime.py](prepare_embedding_runtime.py)：将支持的 BGE-M3 流水线离线导出并验证为 FP32 ONNX；通过分词/向量对照后原子启用，保留原权重和旧版本。参数 `--model-dir data/model/embedding/bge-m3`，已有有效制品默认复用，`--rebuild` 创建新版本。依赖 `local-export`，不连接业务库、不修改 `.env`。
- [production_storage_checkpoint.py](production_storage_checkpoint.py)：部署读取检查点。
- [repair_source_scope.py](repair_source_scope.py)：来源范围修复计划及显式应用。
- [migrate_source_objects.py](migrate_source_objects.py)：本地来源对象与历史队列 BYTEA 迁往显式配置的 S3/MinIO，默认只读盘点；完整校验原始大小和 SHA256 后，`--apply` 才上传并将队列改为对象引用。
- [start-service.ps1](start-service.ps1)：正常服务启动。

### 来源对象迁移

先备份数据库及 `data/sources`，执行普通 schema 迁移至 `0029`，安装 `s3` extra 并预建独立来源 bucket。安排暂停上传、来源 GC 与批次 worker 的维护窗口；把本次命令进程的 `KG_SOURCE_OBJECT_BACKEND=s3` 及 `KG_SOURCE_S3_*` 指向目标。命令经 `resolve_dsn()` 使用应用角色做 DML，不需要数据库管理员凭据。

```powershell
python scripts/migrate_source_objects.py --source-root data/sources --output .benchmarks/source-migration-dry-run.json
python scripts/migrate_source_objects.py --source-root data/sources --apply --output .benchmarks/source-migration-applied.json
```

默认盘点已登记的原文件/图片及全部队列，验证源和已有目标；不上传、不改数据。显式 `--apply` 保留相同对象键，目标完整回读的大小和 SHA256 均通过后才登记引用、清除对应队列 BYTEA。ZIP 的旧成员 Markdown 不能代替原始 ZIP；只能跟随 SHA256 一致的已冻结原始包。缺失或损坏对象只尝试校验和完全匹配的冻结 Markdown（含 UTF-8 BOM）、历史原始 BYTEA 或已经验证的目标；不能恢复的逐项报错，退出码 2，保留未验证的原始字节。失败项可以修复后重跑，已完成项幂等。

脚本不创建 bucket、不删除或修复本地文件、不修改 `.env`、不自动切换运行服务配置。核对报告零错误后，由运维显式切换应用对象后端并重启；保留旧文件用于回滚。S3 超时与重试有界，内存暂存上限 8 MiB，总对象上限 1 GB（大对象需维护窗口内另行处理）。

## 容量、基准与诊断

保留以下 10 个。它们需要单独安排数据规模、模型、内存和采样环境，部分会创建或修改合成验收数据。不要与 pytest 混跑后拿时延当性能认证。

- [benchmark_graph_interaction.py](benchmark_graph_interaction.py)：图交互。
- [benchmark_image_ocr.py](benchmark_image_ocr.py)：图像解析/OCR。
- [benchmark_ontology_canvas.py](benchmark_ontology_canvas.py)：本体画布。
- [build_capacity_recertification_report.py](build_capacity_recertification_report.py)：生成已有容量观测报告。
- [observe_capacity_runtime.py](observe_capacity_runtime.py)：运行过程观察。
- [profile_retrieval.py](profile_retrieval.py)：按项目和查询读取检索阶段计时。
- [recertify_retained_capacity.py](recertify_retained_capacity.py)：保留容量项目的专项验收。
- [run_capacity_audited_service.py](run_capacity_audited_service.py)：带阶段诊断的服务，使用独立验收安排。
- [verify_full_graph_capacity.py](verify_full_graph_capacity.py)：独立绘图层夹具，不代表完整业务服务性能。
- [verify_redesign_product_capacity.py](verify_redesign_product_capacity.py)：产品容量故事。

固定项目 ID、报告日期和样本语料都属于特定验收条件。复跑前读取脚本头部，不能用小项目、模拟向量或单次绘图替代 15 万节点的真实向量检索结论。

## 当前专项验证与支持

以下 10 个用途保留；它们不都属于无依赖的自动回归：

- [check_font_floor.py](check_font_floor.py)：字号检查；`--css-only` 不需要业务服务，浏览器分支需要认证环境。
- [generate_ingest_fixture.py](generate_ingest_fixture.py)：构造合成输入，也被 OCR 基准调用。
- [retest_shared_milvus.py](retest_shared_milvus.py)：共享集合指定项目真实复测，显式写入/构建参数与只读场景分开。
- [verify_auth_rbac.py](verify_auth_rbac.py)：真实权限验证。
- [verify_graph_maintenance_browser.py](verify_graph_maintenance_browser.py)：图谱维护故事。
- [verify_ingest_batch.py](verify_ingest_batch.py)：资料批次。
- [verify_ocr_models.py](verify_ocr_models.py)：OCR 模型可用性。
- [verify_production_content.py](verify_production_content.py)：真实结果与 PG 差分。
- [verify_redesign_live_ui.py](verify_redesign_live_ui.py)：现行页面观察。
- [verify_redesign_workflows.py](verify_redesign_workflows.py)：改造操作故事。

手册/内部支持另有 4 个：[build_knowledge_service_manual.py](build_knowledge_service_manual.py)、[verify_redesign_manual.py](verify_redesign_manual.py)、[verify_redesign_manual_browser.py](verify_redesign_manual_browser.py)、[ontology_validation_helpers.py](ontology_validation_helpers.py)。最后一个是共用 helper，不是无用的空脚本。浏览器工具需要既有 Playwright 和可用浏览器。

## 保留但需适配的专项：17 个

**以下暂不作为现行版本的通过判定入口。** 它们保留了独特的验收故事、输入或 helper；仅保留文件不能证明它们现在可运行。

| 范围 | 文件 | 已知需核对内容 |
| --- | --- | --- |
| 旧汇总与隔离启动 | [verify_all_chains.py](verify_all_chains.py)、[run_redesign_validation.py](run_redesign_validation.py) | 子链有旧路由/DOM；自定义 collection 环境变量不能隔离当前固定共享集合 |
| 认证包装/硬化 | [verify_authenticated.py](verify_authenticated.py)、[verify_hardening.py](verify_hardening.py) | 新建项目后的默认本体假设 |
| 抽取与审核 | [verify_ingest_end_to_end.py](verify_ingest_end_to_end.py)、[verify_review_decision.py](verify_review_decision.py) | 旧 `/ontology-discovery/drafts` 路由；二者有 helper 依赖 |
| 本体专题 | [verify_ontology_gate.py](verify_ontology_gate.py)、[verify_ontology_model.py](verify_ontology_model.py)、[verify_ontology_retire.py](verify_ontology_retire.py)、[verify_ontology_version.py](verify_ontology_version.py) | 对照当前草案选择、命令、审核与发布流程逐项复核 |
| 台账/重分类/待定 | [verify_knowledge_ledger.py](verify_knowledge_ledger.py)、[verify_reclassify.py](verify_reclassify.py)、[verify_structure_pending.py](verify_structure_pending.py) | 现行写入依据、DOM、共享详情与候选协议 |
| 原生范围/后端容量 | [verify_native_applicability_capacity.py](verify_native_applicability_capacity.py)、[verify_redesign_backend_capacity.py](verify_redesign_backend_capacity.py) | 旧 fence/alias/任意 collection；保留隔离、恢复等专题意图 |
| 运行页 | [verify_runtime_page.py](verify_runtime_page.py) | 旧五组导航与四分区，现项目管理独立 |
| 基础设施 | [verify-compose-stack.sh](verify-compose-stack.sh) | 仍断言 `./volumes` bind 挂载；现部署已使用 Linux named volumes |

默认 pytest/Node 不会替你执行这些手动脚本。尤其 `verify_all_chains.py` 不再被项目 README 推荐为“一键全部验证”。

## 历史迁移安全验证：3 个

[rehearse_migration_0005.py](rehearse_migration_0005.py)、[rehearse_migration_0006.py](rehearse_migration_0006.py)、[rehearse_migration_0013.py](rehearse_migration_0013.py) 保留迁移样本映射、回填选择性、幂等性和演练约束。它们与正式编号迁移 SQL 不同，不属于日常启动命令；前两者仍含旧路径/演练方式，使用前需要复核。正式部署迁移入口始终是 `python -m knowledge_service.repository.migrate`。

## 本次退役

9 个旧主题、旧页面或一次性 Milvus 迁移脚本已删除，原文可从 Git 历史恢复，不保留看似可运行的兼容壳。精确文件与证据见 [清理记录](../docs/research/2026-10-22-project-file-cleanup.md)。

没有把保留脚本直接搬目录：多个脚本以 `__file__.parents[1]` 解析仓库根，并有 `runpy`、同目录 helper 和测试夹具依赖。用途索引在保留可追溯入口的同时，避免制造新的路径错误。
