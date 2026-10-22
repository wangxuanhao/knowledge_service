# 固定 Milvus 字段与单条修订性能

用户于 2026-10-21 明确批准：暂时取消 bf_ 自动扩展，只把当前直接需要的业务维度写入 Milvus；后续有具体字段需求再通过线上 schema 变更增加；继续修复已复现的性能问题。本文件覆盖此前“自动发现 metadata 叶子”的设计。

## 字段边界

- 继续使用 knowledge_vectors，共享集合、project_id + index_version 隔离不变。
- 当前仅使用现有 21 列：vector_id/index_version/version_id/project_id/record_id/kind/type/ontology_id/text/embedding/sparse；platform/business/audience 的 mode + values；valid_from_us/valid_until_us/chunk_owner_id/filter_present。
- 不自动遍历 metadata 推导列，不生成 bf_ 哈希名，不将 model/provider/ontology_slice/测试标签投影为业务维度。未来确有需要，由代码明确声明可读列、类型、回填与验收；本次不建设扩展配置平台。
- 保留平台/业务/对象三态规则，mode 区分 all、specific、unknown，values 保存 specific 的具体值。筛选语义不简化为含糊的空数组。
- 当前在线检索/问答普通条件支持 id/kind/type/source_id/valid_from/valid_until；本体和适用范围沿用专用 scope。未支持的 metadata 条件整次拒绝，不拆 and/or/not、不静默忽略。历史关键词及 PG 图谱/资料库/台账原有 metadata 能力保留。
- source_id 在此特指片段所属文档，映射 chunk_owner_id，只有 chunk 行参与该来源条件。它不代表实体/关系的多来源断言，不用这一列伪造多文档来源筛选；相关来源关系仍查询 PG assertions。前端明确标为“片段所属文档”。
- 当前现场为 Milvus 3.0.2。官方支持新增可空字段及删除受支持字段。停应用后只删除本集合已有 bf_ 标量列和对应索引，保留向量、其他列、项目及业务数据；不 drop collection。旧目录索引明确需要显式重建；仅重建已选中的两个普通测试项目，容量项目不自动向量化。
- 显式重建白名单：eeb3a4dc-7475-4937-9966-74aa261334e6、828887db-e1f3-4f5d-b2b3-4b3dc97d2040。容量项目 46249548-2189-4ad7-8f23-70ee2bcc7f6f 永不进入本次向量化白名单。删列前备份 schema/目录/物理计数并持久化进度；失败保留检查点，旧目录保持 requires_rebuild，业务PG照常可读写。只有显式任务经物理投影验证和水位CAS才能激活ready，失败不得切换或自动补建。

## 已复现问题及修复边界

1. 健康检查调用缺失的 collection_dim：提供只读 schema 维度探测，不创建集合或触发模型编码。
2. 动态列索引创建等待超时：去掉项目构建中的自动加列。基础索引创建区分提交与就绪等待，使用有界显式任务等待和清晰错误；不把增加普通 RPC 超时当作解决方案。
3. 每批/每条 upsert 强制 flush：以 Strong 读和 Milvus 写确认保证可见性，避免把 segment sealing 当作每条写入的必要步骤；保留显式 flush 的维护用途。以真实增量追平和检索验证一致性。
4. 单条修订仍两次全项目快照，时间预留又 fetchall 全历史：先用 PostgreSQL typed MAX 保持两表高水位语义并保留锁、CAS、时态。
5. 大库普通实体文字修订需要真正有界的读取路径。仅在能证明依赖闭合的情形使用：单条已有实体、显式版本 CAS、仅修改 text、固定来源/本体/类型/时态/属性/metadata、无额外治理动作，且本体无任意 SHACL/跨图验证约束。依赖读集必须覆盖目标、来源、绑定及最新本体、旧/新事实键与胜出记录、涉及的断言/事件/版本映射，并在执行事务内防止并发与不存在谓词幻影。无法证明安全则走既有完整校验。不能只截取一个节点并跳过治理。
6. 外部 prepare→execute 计划仍保留执行期依赖比较；所有路径保留最终 _put CAS、来源、撤回、合并、SHACL、关系约束和 outbox 原子性。纯 text 修订也可能影响 rdfs:label 的 SHACL，因此不能无条件走局部路径。

## 验收

测试先复现再实现，只运行一个 pytest 会话。确认新构建无 bf_、没有读取无关 metadata；PG 页面条件不丢失；健康探测不写；Strong 读能看到未手动 flush 的增量；旧索引不自动重建。

写入回归覆盖事实键冲突、标签约束回退、过期 CAS、并发新记录/断言变化、来源精确版本映射、事务回滚、撤回与重放。性能断言优先检查不读取无关记录，而非脆弱的毫秒阈值。

最终使用唯一普通 8100 服务复测两个普通项目；保留初次失败证据。对保留的 150001 实体/300000 关系项目做受控编辑/恢复、并行读取和分页复测，不启动大规模自动向量化、不替换为独立小库。按阶段、样本数、失败和主机资源分别报告。
