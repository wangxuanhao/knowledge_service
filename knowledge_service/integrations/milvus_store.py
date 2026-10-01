"""Milvus 向量与 BM25 检索索引存储 —— 对齐 Milvus 3.0 / pymilvus >= 3.0.1，走 MilvusClient。

Milvus 承载"当前活跃记录"的 dense 向量 + sparse(BM25) 索引 —— 派生数据，
丢了可经 /indexes/rebuild 全量重建；真值（双时态、元数据、本体、版本链）
由 PostgreSQL 持久化。

Milvus 3.0 对齐决策（与 2.6 的差异已逐条评估，不盲目沿用旧封装）：
  * 全部走 MilvusClient（非 ORM-style 的 Collection/connections/utility——后者在
    pymilvus 3.1 将移除，本文件因此不再触发 DeprecationWarning）。
  * text 字段用 VARCHAR(65535) 而非 3.0 新增的 TEXT —— TEXT 需 Storage V3
    （common.storage.useLoonFFI，默认关），启用后失去 2.6 rollback；本项目
    实体/关系/片段文本均 < 64KB，TEXT 的 LOB/无长度限制用不上，不为此承担
    存储引擎升级的不可逆代价。
  * 时态字段暂不用 3.0 新增的 TIMESTAMPTZ —— 双时态过滤由 PostgreSQL 承担
    （repository.query 是核心资产），Milvus 只做当前活跃记录的排名索引。
  * sparse 索引沿用 SPARSE_INVERTED_INDEX + BM25 metric：3.0 内部已默认
    MaxScore 做 BM25 打分、SINDI 做 sparse IP，对本封装透明，无需显式改参。
  * hybrid 走 3.0 原生 AnnSearchRequest + RRFRanker（dense+sparse 融合），
    注意 filter 必须放进每个 AnnSearchRequest.expr，不能作为 hybrid_search 的
    kwargs（pymilvus 会与 req.expr 位置参数重复传参抛 TypeError）。

对外职责（供 repository/retrieval 调用）：
  ensure_collection()   幂等建 collection + 索引
  upsert(records)       按 version_id 批量 upsert（BM25 sparse 自动生成）
  delete(project, id)   按 record_id 删除（软删/更新时同步）
  search_dense(...)     dense 向量余弦检索
  search_bm25(...)      sparse BM25 词法检索（中文 analyzer 正确分词）
  search_hybrid(...)    原生 RRF：dense + sparse 融合
  rebuild_project(...)  清空某 project 分区后批量重建

BM25 依赖 Milvus 3.x 的 Function(BM25)（从 text 自动生成 sparse），dense 用
bge-m3 的 embedding 字段。与 PostgreSQL 关联的键是 version_id（=== record_versions.version_id）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
from pymilvus import (
    AnnSearchRequest,
    CollectionSchema,
    DataType,
    FieldSchema,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
    WeightedRanker,
)
from pymilvus.milvus_client.index import IndexParams

COLLECTION = "knowledge_records"
_METRIC_DENSE = "COSINE"
_METRIC_SPARSE = "BM25"


class MilvusStore:
    """Milvus 检索索引；未连接（milvus 不可用）时所有方法安全降级/抛错，不拖垮主流程。"""

    def __init__(self, host: str = "localhost", port: str = "19530",
                 dim: int = 1024, collection: str = COLLECTION):
        self.host = host
        self.port = port
        self.dim = dim
        self.collection_name = collection
        self._client: Optional[MilvusClient] = None

    # ------------------------------------------------------------------ 连接与建表
    def connect(self) -> "MilvusStore":
        try:
            self._client = MilvusClient(uri=f"http://{self.host}:{self.port}")
        except Exception as exc:
            self._client = None
            raise RuntimeError(f"Milvus 连接失败 {self.host}:{self.port}: {exc}") from exc
        return self

    @property
    def available(self) -> bool:
        return self._client is not None

    def ensure_collection(self) -> "MilvusStore":
        """幂等：已存在直接复用，不存在则建 schema + BM25 function + 双索引。"""
        if self._client is None:
            self.connect()
        if self._client.has_collection(self.collection_name):
            return self

        fields = [
            FieldSchema(name="version_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="project_id", dtype=DataType.VARCHAR, is_partition_key=True, max_length=64),
            FieldSchema(name="record_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="kind", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="type", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535,
                        enable_analyzer=True, analyzer_params={"type": "chinese"}),
            FieldSchema(name="embedding_model", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dim),
            FieldSchema(name="sparse", dtype=DataType.SPARSE_FLOAT_VECTOR),
        ]
        schema = CollectionSchema(fields, description="knowledge_service 检索索引（dense + BM25 sparse）")
        # BM25：从 text 自动生成 sparse 向量（中文 analyzer 分词后统计词频）
        schema.add_function(Function(
            name="text_bm25", function_type=FunctionType.BM25,
            input_field_names=["text"], output_field_names="sparse"))
        self._client.create_collection(self.collection_name, schema=schema)

        index_params = IndexParams()
        # dense 向量索引（HNSW + 余弦）
        index_params.add_index("embedding", index_type="HNSW", metric_type=_METRIC_DENSE,
                               M=16, efConstruction=256)
        # sparse BM25 索引
        index_params.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type=_METRIC_SPARSE)
        # 标量过滤索引（record_id 删旧 / kind 粗过滤）
        for name in ("record_id", "kind"):
            index_params.add_index(name, index_type="INVERTED")
        self._client.create_index(self.collection_name, index_params)
        self._client.load_collection(self.collection_name)
        return self

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    # ------------------------------------------------------------------ 写入/删除
    @staticmethod
    def _build_text(record: Dict[str, Any]) -> str:
        """BM25 的输入文本：主 text + 别名拼接，保留别名匹配能力。"""
        text = str(record.get("text") or "")
        aliases = (record.get("metadata") or {}).get("aliases")
        if isinstance(aliases, (list, tuple)) and aliases:
            text = text + " " + " ".join(str(a) for a in aliases)
        return text[:65535]

    def upsert(self, records: Iterable[Dict[str, Any]], flush: bool = True) -> int:
        """批量 upsert「当前活跃记录」。records 需含 version_id/id/kind/text/embedding。

        返回写入条数。sparse 由 BM25 function 自动生成，无需手动传。
        """
        self.ensure_collection()
        rows: List[Dict[str, Any]] = []
        for r in records:
            ids: Dict[str, Any] = {
                "version_id": str(r["version_id"]),
                "project_id": str(r["project_id"]),
                "record_id": str(r["id"]),
                "kind": str(r.get("kind") or ""),
                "type": str(r.get("type") or ""),
                "text": self._build_text(r),
                "embedding_model": str(r.get("embedding_model") or ""),
            }
            emb = r.get("embedding")
            if emb is None:
                continue
            vec = np.asarray(emb, dtype="float32").reshape(-1)
            if vec.size != self.dim:
                raise ValueError(f"向量维度 {vec.size} != 预期 {self.dim}")
            ids["embedding"] = vec
            rows.append(ids)
        if not rows:
            return 0
        self._client.upsert(self.collection_name, rows)
        if flush:
            self._client.flush(self.collection_name)
        return len(rows)

    def delete(self, project_id: str, record_id: Optional[str] = None) -> int:
        """删除：按 record_id 删单条，或清空整个 project 分区（rebuild 用）。"""
        self.ensure_collection()
        if record_id:
            expr = f'project_id == "{project_id}" and record_id == "{record_id}"'
        else:
            expr = f'project_id == "{project_id}"'
        res = self._client.delete(self.collection_name, filter=expr)
        return int(res.get("delete_count", res.get("deleted_count", 0))) if isinstance(res, dict) else 0

    def rebuild_project(self, project_id: str, records: Iterable[Dict[str, Any]]) -> int:
        """清空该 project 分区后批量重建（records 为已编码好的当前活跃记录）。"""
        self.delete(project_id)
        return self.upsert(records, flush=True)

    # ------------------------------------------------------------------ 检索
    @staticmethod
    def _kind_expr(kinds: Optional[List[str]]) -> str:
        if not kinds:
            return ""
        quoted = ", ".join(f'"{k}"' for k in kinds)
        return f"kind in [{quoted}]"

    def _filter_expr(self, project_id: str, kinds: Optional[List[str]] = None,
                     candidate_ids: Optional[List[str]] = None) -> str:
        parts = [f'project_id == "{project_id}"']
        kind = self._kind_expr(kinds)
        if kind:
            parts.append(kind)
        if candidate_ids:
            # 由 scope 过滤后给出的候选版本号集合，限定在项目可见集内排名
            quoted = ", ".join(f'"{v}"' for v in candidate_ids[:5000])
            parts.append(f"version_id in [{quoted}]")
        return " and ".join(parts)

    def _load_if_needed(self) -> None:
        self.ensure_collection()
        try:
            self._client.load_collection(self.collection_name)
        except Exception:
            pass  # 已 load 时重复 load 会抛，忽略

    def search_dense(self, project_id: str, query_vector: List[float], kinds: Optional[List[str]] = None,
                     k: int = 10, candidate_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        self._load_if_needed()
        qvec = np.asarray(query_vector, dtype="float32").reshape(1, -1)
        res = self._client.search(
            collection_name=self.collection_name, data=qvec, anns_field="embedding",
            filter=self._filter_expr(project_id, kinds, candidate_ids), limit=k,
            search_params={"metric_type": _METRIC_DENSE},
            output_fields=["record_id", "kind", "type", "text"])
        return self._hits(res)

    def search_bm25(self, project_id: str, query: str, kinds: Optional[List[str]] = None,
                    k: int = 10, candidate_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        self._load_if_needed()
        res = self._client.search(
            collection_name=self.collection_name, data=[query], anns_field="sparse",
            filter=self._filter_expr(project_id, kinds, candidate_ids), limit=k,
            search_params={"metric_type": _METRIC_SPARSE},
            output_fields=["record_id", "kind", "type", "text"])
        return self._hits(res)

    def search_hybrid(self, project_id: str, query: str, query_vector: List[float],
                      kinds: Optional[List[str]] = None, k: int = 10,
                      candidate_ids: Optional[List[str]] = None,
                      dense_weight: float = 0.5) -> List[Dict[str, Any]]:
        """dense + sparse 原生融合。dense_weight>0 时走 WeightedRanker，否则纯 RRF。"""
        self._load_if_needed()
        qvec = np.asarray(query_vector, dtype="float32").reshape(1, -1)
        expr = self._filter_expr(project_id, kinds, candidate_ids)
        # filter 必须放进每个 AnnSearchRequest（MilvusClient 的 hybrid_search 无 filter 参数）
        dense_req = AnnSearchRequest(data=qvec, anns_field="embedding",
                                     param={"metric_type": _METRIC_DENSE}, limit=k, expr=expr)
        sparse_req = AnnSearchRequest(data=[query], anns_field="sparse",
                                      param={"metric_type": _METRIC_SPARSE}, limit=k, expr=expr)
        ranker = (WeightedRanker(dense_weight, 1.0 - dense_weight)
                  if dense_weight > 0 else RRFRanker())
        res = self._client.hybrid_search(
            collection_name=self.collection_name, reqs=[dense_req, sparse_req], ranker=ranker,
            limit=k, output_fields=["record_id", "kind", "type", "text"])
        return self._hits(res)

    @staticmethod
    def _hits(res) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for batch in res:
            for hit in batch:
                entity = hit.get("entity", {}) or {}
                out.append({
                    "version_id": hit.get("version_id", hit.get("id")),
                    "record_id": entity.get("record_id"),
                    "kind": entity.get("kind"),
                    "type": entity.get("type"),
                    "text": entity.get("text"),
                    "score": float(hit.get("distance", 0.0)),
                })
        return out