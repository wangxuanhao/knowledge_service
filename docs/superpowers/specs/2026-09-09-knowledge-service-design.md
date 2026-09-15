# Knowledge Service 服务层设计

> 已被用户纠正：以下“不兼容编号脚本和旧数据格式”的前提不再有效。必须以旧项目完整功能与向后兼容为基线，详见 `docs/2026-09-09-能力对标与修正.md`。此稿仅记录之前实现，不能作为继续删减旧能力的依据。

已确认：不兼容编号脚本和旧数据格式；SQLite + FAISS + RDF/OWL；双时态；支持嵌套 metadata。默认保留旧文件作为参考，不自动导入、不删除已有数据。

## 方案与选择

采用独立 Python 包 `knowledge_service`，FastAPI 为统一入口。SQLite 保存不可变知识版本、文档和本体版本；向量保存于 SQLite 并由 FAISS 在过滤后的候选集合内排名。适合当前本机环境，避免 JSON 与向量文件更新不一致。

另两种方案：继续拆分编号脚本，迁移少但旧数据结构无法充分表达时态；直接 Neo4j 全量落库，图查询较强但需要额外部署。当前选择前者的独立服务实现，预留 Semantica 图导出/快照适配，不宣称已实现 Neo4j 后端。

## 模块与接口

- models：请求校验、稳定 ID、JSON metadata、时间标准化。
- repository：SQLite 项目、双时态知识版本、文档、本体和向量事务。
- filters：安全参数化 metadata 过滤，AND/OR、eq/ne/in/contains/gt/gte/lt/lte/exists，支持 `region.city`。
- retrieval：先筛选再向量排名，实体、关系、段落统一候选；图扩展和问答证据必须保持同样的过滤范围。
- ontology：项目本体不可变版本、Turtle 校验、类/谓词白名单、domain/range 校验及继承、SHACL 校验、受限本地 SPARQL 查询。
- adapters：Semantica 实体/关系提取、时间快照；本地 sentence-transformers 或 OpenAI 兼容 embedding；演示专用 hashing 编码明确标注非语义模型。
- api：项目、文档摄取、知识写入/修订/历史、图查询、过滤检索、基于证据问答、本体、健康状态。默认仅本机监听；tenant metadata 不等于身份认证。
- web：轻量中文服务工作台，项目/导入/记录/时间与metadata筛选/检索/本体；Swagger 完整接口说明。

## 时间与版本

统一 UTC ISO 8601，带时区时间或 YYYY-MM-DD（按 UTC 零点）输入；拒绝无时区 datetime。业务时间 `[valid_from, valid_until)`，null 表示未知/开放端点；系统时间 `[recorded_at, superseded_at)` 由服务生成，不允许客户端回写。查询支持 `valid_at` 和 `known_at`，默认当前；历史未知业务时间可显式排除。不得用上传时间冒充规则生效时间。

同一稳定记录 ID 的修订关闭旧系统版本，再生成新版本；支持乐观版本号防丢失更新。业务范围变更保留原始有效期的历史版本。每个版本独立拥有 metadata 和来源，关系有独立 ID，可表示同端点不同谓词与有效期。文档上传时间独立于成功抽取时间。

修订明确表示纠错并替换整个业务区间，不自动切分区间。规则业务版本变更必须创建不同的稳定 ID，分别设置有效期，保留过去业务有效的规则。向量保存模型标识和维度，配置变更后不同模型向量拒绝混排。关系端点必须在同项目且覆盖关系的有效期；图/检索端点还必须满足相同时间与 metadata 范围。JSON missing 不等于 null；ne 对缺失返回 false，contains 对字符串为子串、数组为严格类型元素成员。

Semantica ContextGraph 只负责业务时间快照适配；SQLite 负责双时态持久化。没有本体时允许文档/片段，实体/关系写入要求先保存本体。完整 IRI 或无歧义 local name 均支持。SHACL 与本体同版本保存。

## 过滤、检索和本体

metadata 标准字段建议 source/domain/document_type/language/tags/confidence/owner，允许任意嵌套 JSON。项目隔离为强制条件。字段路径与操作符校验，值参数化；无字段不等同 null。过滤应用于 FAISS 排名前的全部候选，而非 top-k 后过滤。候选向量从事务存储恢复，重启后查询结果保持一致。

本体变更产生新版本，导入指定或使用当前本体；抽取白名单从本体读取，无需重启。实体类型、关系谓词与端点类型校验；记录本体版本，可重新验证并返回报告。SPARQL 只开放本地 SELECT/ASK，阻止 SERVICE 和数据更新。筛选后的图只返回筛选后的节点及端点齐全的边。

## 验收与错误处理

缺依赖/模型/LLM 配置明确返回错误，禁止无提示切换引擎。提供不调用外部模型的演示流程。验证非法过滤、嵌套类型、时间边界、历史修订、项目隔离、先过滤再 top-k、本体约束、重启持久化及 HTTP 端到端流程。未配置模型时不把 hashing 结果称为语义检索。

## 范围

交付本机可运行的完整核心服务层和工作台，不包含分布式 worker、身份认证、多机并发或生产 Neo4j 适配。保留老项目文件，README 和启动脚本指向新入口。没有 Git 仓库，设计与代码保存在工作目录，不创建虚假提交。
