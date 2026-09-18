# Milvus 向量化改造设计

> 状态：设计中（已实测，待接入 repository/retrieval）
> 依据：Milvus v3.0.1（Docker `milvus-standalone`，19530）+ pymilvus 3.0.1 实测

## 一、核心结论（先看这个）

把"向量"从 SQLite 的 `vector` float32 BLOB 列迁出，交给本地 Milvus；关键词/BM25/向量/混合四种检索全部由 Milvus 承担。

**Milvus 3.0 实测能力（已验证）**：
- `chinese` analyzer 中文分词正确（"平台规则"能同时命中"平台规则禁止…"和"商家结算规则…"并正确排序）
- `FunctionType.BM25`（sparse 向量）可用，BM25 检索中文零命中问题彻底解决（替代 FTS5 unicode61）
- `RRFRanker` + `AnnSearchRequest` 原生支持 dense+sparse 混合检索（替代现在手写的 `rrf_fuse`）
- `partition key`（=project_id）物理分区，scoped 检索自动只搜对应分区

## 二、存储分工（谁放哪）

| 存储 | 角色 | 内容 |
|---|---|---|
| **SQLite（真相源，不动）** | 双时态版本 + 元数据 + 本体 + 版本链 | `record_versions`（删掉 `vector` 列，payload 不再存 embedding）、`ontologies`、`projects`、`reviews`、`assertions`、`record_fts`（可保留做兜底） |
| **Milvus（检索索引，可重建）** | 当前活跃记录的 dense + sparse 索引 | 见下方 schema；纯派生数据，`/indexes/rebuild` 可全量重算 |

**原则**：SQLite 是唯一真相源；Milvus 是"当前活跃记录"的检索加速索引，丢了可重建，不承载任何业务语义（时态、审核、版本链都不在 Milvus）。

## 三、Milvus Collection Schema

```
collection 名：knowledge_records
partition key：project_id（物理分区，scoped 检索自动过滤）

字段：
  version_id       VARCHAR(64)   主键    # = SQLite record_versions.version_id，两库关联键
  project_id       VARCHAR(64)   分区键  # scoped 检索
  record_id        VARCHAR(128)  标量索引 # 业务 id（跨版本稳定），同步删旧/去重用
  kind             VARCHAR(32)   标量索引 # entity / relation / chunk / document
  type             VARCHAR(256)          # 实体/关系类型（短名或 IRI）
  text             VARCHAR(65535) analyzer=chinese  # 检索文本（主 text + 别名拼接）
  embedding_model  VARCHAR(128)          # 向量身份（dense 检索前校验一致性）
  embedding        FLOAT_VECTOR(1024)    # dense 向量，bge-m3
  sparse           SPARSE_FLOAT_VECTOR   # BM25 输出（由 BM25 function 从 text 生成）

索引：
  embedding：index_type=HNSW, metric_type=COSINE
  sparse：index_type=SPARSE_INVERTED_INDEX, metric_type=BM25
  record_id / kind：标量索引（INVERTED）
```

**为什么不进 Milvus 的字段**（推理 + 取舍）：
- `metadata` JSON：复杂嵌套过滤（filters.py）已由 SQLite 正确实现，Milvus 不冗余存，检索结果按 version_id 批量回 SQLite 拿完整 payload（主键查询，成本极低）。
- 时态字段（valid_from/valid_until/recorded_at/superseded_at）：**双时态过滤逻辑留在 SQLite**（repository.query 是核心资产，搬进 Milvus filter 表达式风险高）。Milvus 只存"当前活跃版本"（`superseded_at IS NULL`），与现状 `store_embeddings` 的语义完全一致——历史已知时间点（known_at）的语义检索本就不完整，不损失能力。

## 四、四种检索的实现映射

| 检索类型 | 后端 | Milvus 做法 |
|---|---|---|
| **关键词检索** | 精确词法 | SQLite FTS5 + 词法兜底（保留，中文修 bigram 切词）——作为 `keyword` 精确档 |
| **BM25 检索** | sparse 统计相关性 | `anns_field=sparse, metric_type=BM25`（Milvus 中文 analyzer 正确分词） |
| **向量检索** | dense 语义 | `anns_field=embedding, metric_type=COSINE` |
| **混合检索** | dense + sparse RRF | `AnnSearchRequest(embedding) + AnnSearchRequest(sparse)` → `RRFRanker`（Milvus 原生融合，替代手写 `rrf_fuse`） |

`retrieval_mode` 对外保持兼容（`hybrid`/`semantic`/`keyword`），内部：
- `semantic` → Milvus dense
- `keyword` → 精确词法（FTS5/lexical）；BM25 作为 keyword 的增强档可单独暴露
- `hybrid` → Milvus dense + sparse 原生 RRF

## 五、同步策略（写入/更新/删除/重建）

1. **写入**：`service.write` 编码后，`store_embeddings`（现 repository.py:432）从"UPDATE record_versions 的 vector 列"改为"向 Milvus upsert"。
2. **更新（revise/软删除）**：记录 supersede 或删除时，同步 upsert/delete 到 Milvus（按 record_id 定位，保证一个 record 只有一条活跃向量）。软删除（`_deleted` 标记）同步从 Milvus 删除。
3. **重建**：`/indexes/rebuild`（workspace_api.py:232）改为"读当前活跃记录 → encoder.encode → 批量 upsert Milvus"；重建前清空该 project 分区。
4. **一致性兜底**：Milvus 与 SQLite 是两套存储，单事务原子性让渡；但向量是派生数据（可重建），`rebuild` 即兜底，与现状"重传可补向量"一致。

## 六、双时态语义（明确取舍）

- Milvus 只存**当前激活**（`superseded_at IS NULL`）的记录向量。
- 时间切片查询（known_at 指过去）：候选集仍由 SQLite repository.query 按正确语义给出版本号集合，检索时 Milvus 用 `version_id in [...]` 限定在这些版本内 → 语义不丢。
- 代价：历史版本的向量在 Milvus 中不常驻（存于 SQLite 的 `record_versions`，需要时回读）——与现状"只为当前版本存向量"一致，无能力退步。

## 七、风险与待实测项

1. **`version_id in [...]` 大列表过滤性能**：候选集几千时，Milvus filter 表达式较长的性能需实测；配合 partition key 应该可控，若慢则改"Milvus 粗过滤（project+kind）后 Python 侧交集"。
2. **Milvus hybrid search API 实测**：`AnnSearchRequest` + `RRFRanker` 的参数与返回结构在 pymilvus 2.6.2 的精确用法（实现前写最小验证脚本）。
3. **写入延迟**：Milvus upsert 有异步可见性（需 flush/load 后立即检索），检索接口要考虑"刚写入是否立即可见"，必要时 query 阶段仍可回退 SQLite 词法兜底。
4. **一致性**：服务重启后 Milvus connection 重建 + collection 存在性检查（幂等初始化）。

## 八、实施方案（顺序）

1. `milvus_store.py`：连接管理 + collection 幂等创建 + `upsert/delete/search/search_hybrid/rebuild_project`
2. `repository.store_embeddings` 改走 Milvus（保留 SQLite 写降级开关，可回滚）
3. `retrieval.py`：semantic 走 Milvus dense，keyword 保留 FTS5（中文 bigram 修），hybrid 走 Milvus native RRF
4. `workspace_api.reindex` 改重建到 Milvus
5. 前端检索/问答对接 + 会话记录/清空按钮 UI
6. 四种检索验证脚本 + 知识问答端到端验证