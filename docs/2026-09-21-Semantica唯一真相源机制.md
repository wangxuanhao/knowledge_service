# Semantica 0.6.8 的“唯一真相源”机制

调研日期：2026-09-21

> 范围：只核对 Semantica `v0.6.8` 官方文档、该版本源码和本机安装包。本文所说的“唯一真相源（SSOT）”是指系统明确指定一个权威写模型，其他存储可由它重建且不得反向成为竞争权威。

## 结论

**Semantica 0.6.8 没有定义、选举或强制一个跨模块的唯一真相源。** 它提供的是一组可独立使用、可替换的处理与存储组件，以及帮助应用保持一致性的能力：实体去重/合并、冲突解决、provenance、快照、diff 和回滚。

官方架构明确强调模块可独立使用、无需实例化完整技术栈；`graph_store`、`triplet_store`、`vector_store`、`context`、`provenance` 和 `change_management` 各自拥有接口或持久化方式。[官方架构说明](https://github.com/semantica-agi/semantica/blob/v0.6.8/docs/architecture.md#design-decisions)

因此，Semantica 能帮助应用**构造和审计 canonical knowledge**，但“哪一份数据最终算真、其余存储如何同步和重建”仍是应用架构决策。

## “single source of truth”在 Semantica 中实际指什么

源码中确实出现“maintain a single source of truth”，但位于 `deduplication` 模块说明中，含义是通过重复检测和实体合并减少同一实体的多个副本，并不是声明某个数据库是全局 system of record。[deduplication 模块源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/deduplication/__init__.py#L1-L14)

`EntityMerger.merge_duplicates()` 返回 `MergeOperation`，合并策略可选 `keep_first`、`keep_last`、`keep_most_complete`、`keep_highest_confidence` 或 `merge_all`，并可在结果元数据中保留 `merged_from`。它不会自动把合并结果提交到某个权威数据库；调用方仍要决定策略、审核、事务写入和旧实体失效方式。[EntityMerger 源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/deduplication/entity_merger.py#L67-L172)

## 各类存储的关系

| 组件 | 实际职责 | 是否天然权威 |
|---|---|---:|
| `kg.GraphBuilder` | 从实体/关系构造内存中的 canonical graph dict；传入 `graph_store` 时可额外写入属性图 | 否；`graph_store` 是可选持久化目标，[源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/kg/graph_builder.py#L57-L92) |
| `graph_store` | Neo4j、FalkorDB、Neptune、AGE 等属性图后端的统一接口 | 否；只封装选定后端，[源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/graph_store/graph_store.py#L525-L604) |
| `triplet_store` | RDF 三元组和 SPARQL 后端的统一接口 | 否；与属性图是独立存储接口，[源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/triplet_store/triplet_store.py#L40-L94) |
| `vector_store` | embedding、元数据和相似度检索 | 否；后端独立，通常应视为可重建检索索引，[源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/vector_store/vector_store.py#L100-L154) |
| `ContextGraph` / `AgentContext` | Agent 记忆、决策和 GraphRAG 上下文；`ContextGraph` 本质为内存图，可显式保存为 JSON/Markdown | 否；这是运行上下文，不等同于业务事实库，[源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/context/context_graph.py#L1-L20) |
| `provenance` | 记录实体、关系、chunk、来源和 lineage；默认内存，也可单独写 SQLite | 否；记录“数据怎么来的”，不是业务事实本身，[官方参考](https://github.com/semantica-agi/semantica/blob/v0.6.8/docs/reference/provenance.md#exported-classes) |
| `change_management` | 保存 KG/本体快照，提供 checksum、diff、mutation history 和 restore | 否；保存的是版本快照/审计历史，不自动成为 live graph，[官方参考](https://github.com/semantica-agi/semantica/blob/v0.6.8/docs/reference/change_management.md#typical-workflow) |

`AgentContext.save()` 也会分别保存 memory、vector store 和 knowledge graph；`store()` 先写 memory/vector 路径，再构图。源码没有把这些步骤包装成一个跨存储事务，也没有规定发生部分失败时应以哪一份为准。[AgentContext 保存源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/context/agent_context.py#L308-L376) [AgentContext 写入源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/context/agent_context.py#L378-L490)

## Provenance 和版本管理不能自动制造 SSOT

`ProvenanceManager` 是统一 lineage API，但默认后端是会随进程退出而丢失的 `InMemoryStorage`；只有调用方传入 `storage_path` 或 storage 实例才会持久化到 SQLite。[官方 provenance 参考](https://github.com/semantica-agi/semantica/blob/v0.6.8/docs/reference/provenance.md#getting-started) 顶层 `Semantica` orchestrator 只是在配置存在时设置全局 provenance SQLite 路径，不会把业务图、RDF、向量和版本存储合并为一个库。[orchestrator 源码](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/core/orchestrator.py#L93-L103)

`change_management` 同样默认使用内存存储，而且官方文档明确写明：版本管理器不会自动追踪修改，何时创建快照由调用方控制。它可以证明某个快照未被篡改、比较版本和恢复 live graph，但无法替应用确定“哪个版本已经审核发布”。[官方 change management 参考](https://github.com/semantica-agi/semantica/blob/v0.6.8/docs/reference/change_management.md#typical-workflow)

还有一个 0.6.8 的实现注意点：架构页写着 provenance “always on”，但属性图、RDF 和向量模块对应的 `*WithProvenance` 包装器构造参数默认都是 `provenance=False`。不能仅凭架构页假设所有独立模块调用都会自动写入同一份持久 provenance。[GraphStore 包装器](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/graph_store/graph_store_provenance.py#L21-L44) [TripletStore 包装器](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/triplet_store/triplet_store_provenance.py#L21-L44) [VectorStore 包装器](https://github.com/semantica-agi/semantica/blob/v0.6.8/semantica/vector_store/vector_store_provenance.py#L21-L44)

## 应用必须明确的决策

若使用 Semantica 构建生产系统，应用仍需写清以下契约：

1. **权威写模型**：SQLite、属性图或 RDF 三元组库中，哪一个保存已审核事实和版本；只能选一个主入口。
2. **派生存储**：向量索引、Neo4j 投影、RDF 导出、ContextGraph 是否可从权威库重建，以及重建版本/水位线。
3. **身份与合并**：稳定 ID、别名、合并审批、旧 ID 重定向和撤销策略；不能直接把 `keep_most_complete` 当作业务规则。
4. **冲突权威**：来源优先级、时效、置信度、人工审核和推理事实能否覆盖显式事实。
5. **写入一致性**：先提交权威库，再通过 outbox/任务更新派生索引；为失败重试、幂等、对账和修复定义规则。
6. **溯源与版本**：统一 provenance 持久化位置和稳定引用；在何时生成快照、哪个状态代表发布、如何回滚。

## 对本项目的直接判断

本项目现有的“SQLite 双时态记录为真相源，Milvus/Neo4j 为可重建派生索引/投影”比 Semantica 默认组合更明确。接入 Semantica provenance 或 change management 时，应把它们作为**审计/导出/校验能力**接到现有写事务之后，而不是让它们变成第二套业务事实或版本真相源。

推荐的数据流仍是：

```text
SQLite 已审核版本（权威）
  ├─> Neo4j 属性图投影（可重建）
  ├─> Milvus 向量索引（可重建）
  ├─> RDF / PROV-O 导出（可重建）
  └─> ContextGraph / 检索运行态（可丢弃或重放）
```

一句话：**Semantica 提供“让真相可合并、可解释、可审计、可回滚”的工具；它不替应用决定真相存在哪里。**
