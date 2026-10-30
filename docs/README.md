# 文档索引

当前使用、当前验收与历史设计分开阅读。日期较早的设计和测量保留审计价值，但其中的运行环境、路由、界面及容量数字不自动适用于现行版本。

## 当前操作与运维

- [完整图文操作手册](知识平台完整操作手册.html) · [Markdown 源文件](2026-10-20-知识平台完整操作手册.md)：29 章覆盖操作原因、步骤、结果和恢复；用户操作以它为入口。
- [项目启动与目录](../README.md)、[测试指南](../tests/README.md)、[脚本索引](../scripts/README.md)。
- [Docker 数据卷迁移](2026-10-19-Docker-Linux-volume-migration.md)：已停止写入后的复制、核对与回退边界。
- [资料原件存储与图文阅读](2026-10-28-资料原件存储与图文阅读.md)：MinIO 业务 bucket、本地解析模型、旧原件和队列迁移、图文阅读与配套备份。
- [解析日志定位](解析日志定位.md)：排查解析过程和来源。
- [适用范围与检索](applicability-retrieval.md)：范围语义及专题设计；具体运行状态仍核对当前服务。

## 当前规格与验收

- [依赖与启动审计](research/2026-10-28-dependency-audit.md)：依赖版本、循环导入、包入口优化和剩余冷启动成本；[检索问答冷启动排查](research/2026-10-28-search-qa-cold-start.md)保留原请求与真实服务复测。
- [本地推理依赖拆分](research/2026-10-28-embedding-runtime-optimization.md)：已验证的 BGE-M3 ONNX 运行时、依赖从约 56 秒降至约 2 秒的真实服务复测，以及制品准备与恢复。
- [生产检索检查点](research/2026-10-22-production-checkpoint.md)：真实数据差分、部署与尚未完成的容量/质量边界。
- [存储回归补齐](research/2026-10-22-storage-regression-followup.md)：原 31 项失败映射、255 项专项复跑。
- [项目文件清理](research/2026-10-22-project-file-cleanup.md)：删除依据、保留范围及整理后的检查结果。
- [全能力静态矩阵](research/2026-10-21-full-capability-static-audit.md)：已实现能力和独立后续阶段，静态存在不等于真实运行通过。
- [统一写入操作设计](superpowers/specs/2026-10-20-unified-knowledge-intake-operation-design.md)、[固定 Milvus 字段与写入性能](superpowers/specs/2026-10-21-fixed-milvus-and-write-performance-design.md)。

## 目录分工

| 目录 | 内容 |
| --- | --- |
| `manual-redesign/` | 当前手册引用的真实页面截图，不能按临时文件清理 |
| `research/` | 观察结果、实测 JSON/截图、专题审计与限制，读取时核对日期和测试条件 |
| `superpowers/specs/` | 各轮方案；出现冲突时以用户后续决策和现行实现为准 |
| `superpowers/plans/` | 实施计划与历史交接，不是操作手册或全部已完成的证明 |
| `assets/` | 历史文档使用的附件，清理前需检查引用 |

## 历史材料

根目录中 9 月和早期 10 月的设计、代码差距报告、阶段进度、汇报材料继续保留原路径，方便核对当时决策和证据。文中的“当前”“本轮”“接手必读”均按其成文日期理解，不能代替上面的现行入口。

过时的 `KNOWLEDGE_SERVICE.md`、旧 Neo4j 接入说明和旧 Milvus 改造设计已退役：它们仍描述 SQLite/FAISS、旧集合、旧启动环境等已移除实现。原文可从 Git 历史恢复；现行存储和操作分别见项目 README 与完整手册第 24、27、29 章。
