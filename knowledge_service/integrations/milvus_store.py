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
import json
import hashlib
import re
from datetime import datetime
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

from ..core.time import normalize_time, utc_now
from ..domain.applicability import DIMENSIONS, normalize_applicability, scope_identity

COLLECTION = "knowledge_records"
_METRIC_DENSE = "COSINE"
_METRIC_SPARSE = "BM25"
V2_FIELDS = frozenset({
    'version_id', 'project_id', 'record_id', 'kind', 'type', 'text',
    'embedding_model', 'embedding', 'sparse', 'platform_mode',
    'platform_values', 'business_mode', 'business_values', 'audience_mode',
    'audience_values', 'scope_identity', 'valid_from_known',
    'valid_from_epoch_ms', 'valid_until_known', 'valid_until_epoch_ms',
    'projection_seq', 'active', 'fence',
})
_SCALAR_INDEX_FIELDS = frozenset({
    'record_id', 'kind', 'active', 'projection_seq', 'platform_mode',
    'platform_values', 'business_mode', 'business_values', 'audience_mode',
    'audience_values', 'scope_identity', 'valid_from_known',
    'valid_from_epoch_ms', 'valid_until_known', 'valid_until_epoch_ms',
})


class MilvusSchemaMismatch(RuntimeError):
    """Existing collection cannot satisfy native V2 search semantics."""
    requires_rebuild = True


class VectorDimensionMismatch(RuntimeError):
    """collection 的向量维度与当前编码器不一致 —— 维度是建表时固定的，只能重建表。

    专门做一类异常而不是一句 ValueError：调用方（/indexes/rebuild）需要据此决定
    "删表重建"，而不是把它当成一个普通写入错误重试。
    """

    def __init__(self, collection: str, collection_dim: int, expected_dim: int, encoder_identity: str = ''):
        self.collection = collection
        self.collection_dim = collection_dim
        self.expected_dim = expected_dim
        self.encoder_identity = encoder_identity
        who = f'（当前编码器 {encoder_identity}）' if encoder_identity else ''
        super().__init__(
            f'Milvus collection「{collection}」的 embedding 维度是 {collection_dim}，'
            f'而当前配置要写 {expected_dim} 维{who}。'
            f'维度在建表时就固定了、改不了：调 POST /api/projects/{{project}}/indexes/rebuild '
            f'会检测到不一致并删表重建 + 全量重编码；若想保留旧表，则需让 KG_MILVUS_DIM 与编码器对齐。')


class MilvusStore:
    """Milvus 检索索引；未连接（milvus 不可用）时所有方法安全降级/抛错，不拖垮主流程。"""

    def __init__(self, host: str = "localhost", port: str = "19530",
                 dim: int = 1024, collection: str = COLLECTION, encoder_identity: str = ''):
        self.host = host
        self.port = port
        self.dim = dim
        self.collection_name = collection
        self.encoder_identity = encoder_identity
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

    @staticmethod
    def _rpc(method, *args, **kwargs):
        """Use a bounded RPC timeout while tolerating minimal test clients."""
        try:
            return method(*args, timeout=20, **kwargs)
        except TypeError as exc:
            if 'timeout' not in str(exc):
                raise
            return method(*args, **kwargs)

    def ping(self) -> bool:
        if self._client is None:
            return False
        list_collections = getattr(self._client, 'list_collections', None)
        if callable(list_collections):
            list_collections()
        else:
            has_collection = getattr(self._client, 'has_collection', None)
            if callable(has_collection):
                has_collection(self.collection_name)
        return True

    def collection_dim(self, collection: Optional[str] = None) -> Optional[int]:
        """已存在 collection 的 embedding 维度；不存在或读不到返回 None。"""
        collection = collection or self.collection_name
        if self._client is None or not self._client.has_collection(collection):
            return None
        try:
            info = self._client.describe_collection(collection)
        except Exception:
            return None
        for field in (info or {}).get('fields', []) or []:
            if field.get('name') == 'embedding':
                raw = (field.get('params') or {}).get('dim')
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return None
        return None

    def _field_names(self, collection: str) -> set[str]:
        info = self._client.describe_collection(collection) or {}
        return {field.get('name') for field in info.get('fields', []) or []}

    def _schema_issues(self, collection: str) -> List[str]:
        info = self._client.describe_collection(collection) or {}
        fields = {field.get('name'): field for field in info.get('fields', []) or []}
        issues = [f'missing field {name}' for name in sorted(V2_FIELDS - fields.keys())]
        expected_types = {
            **{name: DataType.VARCHAR for name in (
                'version_id', 'project_id', 'record_id', 'kind', 'type', 'text',
                'embedding_model', 'platform_mode', 'business_mode',
                'audience_mode', 'scope_identity', 'fence')},
            'embedding': DataType.FLOAT_VECTOR,
            'sparse': DataType.SPARSE_FLOAT_VECTOR,
            **{name: DataType.ARRAY for name in (
                'platform_values', 'business_values', 'audience_values')},
            **{name: DataType.BOOL for name in (
                'active', 'valid_from_known', 'valid_until_known')},
            **{name: DataType.INT64 for name in (
                'projection_seq', 'valid_from_epoch_ms',
                'valid_until_epoch_ms')},
        }
        for name, expected in expected_types.items():
            field = fields.get(name)
            if not field:
                continue
            actual = field.get('type', field.get('data_type', field.get('dtype')))
            try:
                matches = actual is not None and int(actual) == int(expected)
            except (TypeError, ValueError):
                matches = str(expected).split('.')[-1] in str(actual).upper()
            if not matches:
                issues.append(f'{name} has wrong dtype')
        for name in ('platform_values', 'business_values', 'audience_values'):
            field = fields.get(name) or {}
            params = field.get('params') or field.get('type_params') or {}
            capacity = params.get('max_capacity', field.get('max_capacity'))
            element = field.get('element_type', params.get('element_type'))
            if capacity is None or int(capacity) < 64:
                issues.append(f'{name} capacity is below 64 or missing')
            try:
                element_ok = element is not None and int(element) == int(DataType.VARCHAR)
            except (TypeError, ValueError):
                element_ok = 'VARCHAR' in str(element).upper()
            if not element_ok:
                issues.append(f'{name} element dtype is not VARCHAR')
        functions = info.get('functions') or []
        if not any(
                fn.get('name') == 'text_bm25'
                and 'sparse' in (fn.get('output_field_names') or [])
                for fn in functions):
            issues.append('BM25 function is missing or malformed')
        try:
            index_names = self._client.list_indexes(collection_name=collection) or []
            indexed_fields = set()
            index_details = {}
            for index_name in index_names:
                detail = self._client.describe_index(
                    collection_name=collection, index_name=index_name) or {}
                field = detail.get('field_name') or detail.get('field')
                if field:
                    indexed_fields.add(field)
                    index_details[field] = detail
            required = {'embedding', 'sparse', *_SCALAR_INDEX_FIELDS}
            issues.extend(f'missing index {name}'
                          for name in sorted(required - indexed_fields))
            if 'COSINE' not in str(index_details.get('embedding', {})).upper():
                issues.append('embedding index is not COSINE')
            if 'BM25' not in str(index_details.get('sparse', {})).upper():
                issues.append('sparse index is not BM25')
        except (AttributeError, TypeError):
            issues.append('index metadata is unavailable')
        return issues

    def ensure_collection(self, recreate: bool = False,
                          destination: Optional[str] = None) -> "MilvusStore":
        """幂等：已存在直接复用；维度不一致时报错（recreate=True 则删表重建）。

        为什么要在这里查维度：以前只看"表在不在"，于是换过编码器（或 KG_MILVUS_DIM
        设错）之后，所有写入都在 upsert 里炸一句「向量维度 256 != 预期 1024」，
        看不出该修哪儿。提前查出来 + 给出可执行的补救，才是可诊断的行为。
        """
        if self._client is None:
            self.connect()
        collection = destination or self.collection_name
        if self._client.has_collection(collection):
            existing = self.collection_dim(collection)
            if existing is not None and int(existing) != int(self.dim):
                if recreate:
                    return self.recreate_collection(destination=collection)
                raise VectorDimensionMismatch(collection, existing, self.dim, self.encoder_identity)
            issues = self._schema_issues(collection)
            if issues:
                if recreate:
                    return self.recreate_collection(destination=collection)
                raise MilvusSchemaMismatch(
                    f'Milvus collection {collection} is not V2: '
                    + ', '.join(issues))
            return self
        return self._create_collection(collection)

    def recreate_collection(self, destination: Optional[str] = None) -> "MilvusStore":
        """删掉旧 collection 并按当前 dim 重建。Milvus 里是派生数据，可全量重编码补回。"""
        if self._client is None:
            self.connect()
        collection = destination or self.collection_name
        if self._client.has_collection(collection):
            self._client.drop_collection(collection)
        return self._create_collection(collection)

    def _create_collection(self, collection: Optional[str] = None) -> "MilvusStore":
        """按 self.dim 建 schema + BM25 function + 双索引。"""
        collection = collection or self.collection_name
        array = lambda name: FieldSchema(
            name=name, dtype=DataType.ARRAY, element_type=DataType.VARCHAR,
            max_capacity=64, max_length=256)
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
            FieldSchema(name="platform_mode", dtype=DataType.VARCHAR, max_length=16),
            array("platform_values"),
            FieldSchema(name="business_mode", dtype=DataType.VARCHAR, max_length=16),
            array("business_values"),
            FieldSchema(name="audience_mode", dtype=DataType.VARCHAR, max_length=16),
            array("audience_values"),
            FieldSchema(name="scope_identity", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="valid_from_known", dtype=DataType.BOOL),
            FieldSchema(name="valid_from_epoch_ms", dtype=DataType.INT64),
            FieldSchema(name="valid_until_known", dtype=DataType.BOOL),
            FieldSchema(name="valid_until_epoch_ms", dtype=DataType.INT64),
            FieldSchema(name="projection_seq", dtype=DataType.INT64),
            FieldSchema(name="active", dtype=DataType.BOOL),
            FieldSchema(name="fence", dtype=DataType.VARCHAR, max_length=128),
        ]
        schema = CollectionSchema(fields, description="knowledge_service 检索索引（dense + BM25 sparse）")
        # BM25：从 text 自动生成 sparse 向量（中文 analyzer 分词后统计词频）
        schema.add_function(Function(
            name="text_bm25", function_type=FunctionType.BM25,
            input_field_names=["text"], output_field_names="sparse"))
        self._client.create_collection(collection, schema=schema)

        index_params = IndexParams()
        # dense 向量索引（HNSW + 余弦）
        index_params.add_index("embedding", index_type="HNSW", metric_type=_METRIC_DENSE,
                               M=16, efConstruction=256)
        # sparse BM25 索引
        index_params.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type=_METRIC_SPARSE)
        # 标量过滤索引（record_id 删旧 / kind 粗过滤）
        for name in (
                "record_id", "kind", "active", "projection_seq",
                "platform_mode", "business_mode", "audience_mode",
                "platform_values", "business_values", "audience_values",
                "scope_identity", "valid_from_known", "valid_from_epoch_ms",
                "valid_until_known", "valid_until_epoch_ms"):
            index_params.add_index(name, index_type="INVERTED")
        self._client.create_index(collection, index_params)
        self._client.load_collection(collection)
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

    @staticmethod
    def _epoch_ms(value) -> tuple[bool, int]:
        normalized = normalize_time(value)
        if normalized is None:
            return False, 0
        return True, int(datetime.fromisoformat(
            normalized.replace('Z', '+00:00')).timestamp() * 1000)

    def upsert(self, records: Iterable[Dict[str, Any]], flush: bool = True,
               *, destination: Optional[str] = None, projection_seq: int = 0,
               fence: str = '', active: bool = True) -> int:
        """批量 upsert「当前活跃记录」。records 需含 version_id/id/kind/text/embedding。

        返回写入条数。sparse 由 BM25 function 自动生成，无需手动传。
        """
        records = list(records)
        collection = destination or self.collection_name
        # Report an invalid payload deterministically even when the existing
        # destination also needs a schema rebuild.
        for record in records:
            embedding = record.get('embedding')
            if embedding is None:
                continue
            actual = np.asarray(embedding, dtype='float32').reshape(-1).size
            if actual != self.dim:
                raise VectorDimensionMismatch(
                    collection, self.dim, actual,
                    str(record.get('embedding_model') or self.encoder_identity))
        self.ensure_collection(destination=collection)
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
                "projection_seq": int(r.get('projection_seq', projection_seq)),
                "active": bool(r.get('active', active)),
                "fence": str(r.get('fence', fence) or ''),
            }
            applicability = normalize_applicability(
                (r.get('metadata') or {}).get('applicability'))
            for dimension, prefix in {
                    'platforms': 'platform', 'businesses': 'business',
                    'audiences': 'audience'}.items():
                ids[prefix + '_mode'] = applicability[dimension]['mode']
                ids[prefix + '_values'] = list(applicability[dimension]['values'])
            ids['scope_identity'] = scope_identity(
                (r.get('metadata') or {}).get('applicability'))
            ids['valid_from_known'], ids['valid_from_epoch_ms'] = self._epoch_ms(
                r.get('valid_from'))
            ids['valid_until_known'], ids['valid_until_epoch_ms'] = self._epoch_ms(
                r.get('valid_until'))
            emb = r.get("embedding")
            if emb is None:
                continue
            vec = np.asarray(emb, dtype="float32").reshape(-1)
            if vec.size != self.dim:
                # 和 ensure_collection 报同一类错（维度不可改，只能重建），
                # 这样调用方只需要处理一种异常，而不是"有时 ValueError 有时别的东西"。
                raise VectorDimensionMismatch(self.collection_name, self.dim, vec.size,
                                              str(r.get("embedding_model") or self.encoder_identity))
            ids["embedding"] = vec
            rows.append(ids)
        if not rows:
            return 0
        for offset in range(0, len(rows), 256):
            self._rpc(self._client.upsert, collection, rows[offset:offset + 256])
            if flush:
                self._rpc(self._client.flush, collection)
        return len(rows)

    def delete(self, project_id: str, record_id: Optional[str] = None,
               *, destination: Optional[str] = None) -> int:
        """删除：按 record_id 删单条，或清空整个 project 分区（rebuild 用）。"""
        collection = destination or self.collection_name
        self.ensure_collection(destination=collection)
        if record_id:
            expr = (f'project_id == {json.dumps(str(project_id), ensure_ascii=False)} '
                    f'and record_id == {json.dumps(str(record_id), ensure_ascii=False)}')
        else:
            expr = f'project_id == {json.dumps(str(project_id), ensure_ascii=False)}'
        res = self._rpc(self._client.delete, collection, filter=expr)
        return int(res.get("delete_count", res.get("deleted_count", 0))) if isinstance(res, dict) else 0

    def rebuild_project(self, project_id: str, records: Iterable[Dict[str, Any]]) -> int:
        """清空该 project 分区后批量重建（records 为已编码好的当前活跃记录）。"""
        self.delete(project_id)
        return self.upsert(records, flush=True)

    @staticmethod
    def project_alias(project_id: str, base: str = COLLECTION) -> str:
        digest = hashlib.sha256(str(project_id).encode('utf-8')).hexdigest()[:16]
        return f'{base}__p_{digest}'

    def generation_name(self, project_id: str, build_id: str) -> str:
        token = re.sub(r'[^A-Za-z0-9_]', '_', str(build_id))[:32]
        return f'{self.project_alias(project_id, self.collection_name)}__g_{token}'

    def current_destination(self, project_id: str) -> Optional[str]:
        alias = self.project_alias(project_id, self.collection_name)
        try:
            result = self._client.describe_alias(alias=alias) or {}
        except (AttributeError, TypeError):
            try:
                result = self._client.describe_alias(alias) or {}
            except Exception:
                return None
        except Exception:
            return None
        return (result.get('collection_name') or result.get('collection'))

    def switch_alias(self, project_id: str, destination: str) -> Optional[str]:
        alias = self.project_alias(project_id, self.collection_name)
        previous = self.current_destination(project_id)
        if previous == destination:
            return previous
        if previous:
            self._client.alter_alias(collection_name=destination, alias=alias)
        else:
            self._client.create_alias(collection_name=destination, alias=alias)
        return previous

    def drop_destination(self, destination: str) -> bool:
        if destination and self._client.has_collection(destination):
            self._client.drop_collection(destination)
            control = self._control_name(destination)
            if self._client.has_collection(control):
                self._client.drop_collection(control)
            return True
        return False

    @staticmethod
    def _control_name(destination: str) -> str:
        return destination + '__control'

    def _query_pages(self, collection: str, expression: str,
                     output_fields: List[str], page_size: int = 16384):
        page_size = max(1, min(int(page_size), 16384))
        iterator_factory = getattr(self._client, 'query_iterator', None)
        if callable(iterator_factory):
            iterator = iterator_factory(
                collection_name=collection, filter=expression,
                output_fields=output_fields, batch_size=page_size)
            try:
                while True:
                    page = iterator.next()
                    if not page:
                        break
                    yield from page
            finally:
                close = getattr(iterator, 'close', None)
                if callable(close):
                    close()
            return
        offset = 0
        while True:
            page = self._client.query(
                collection_name=collection, filter=expression,
                output_fields=output_fields, limit=page_size, offset=offset)
            if not page:
                break
            yield from page
            if len(page) < page_size:
                break
            offset += len(page)

    def write_fence_marker(self, project_id: str, destination: str, seq: int,
                           fence: str) -> None:
        control = self._control_name(destination)
        if not self._client.has_collection(control):
            schema = CollectionSchema([
                FieldSchema(name='marker_id', dtype=DataType.VARCHAR,
                            is_primary=True, max_length=64),
                FieldSchema(name='project_id', dtype=DataType.VARCHAR,
                            max_length=64),
                FieldSchema(name='projection_seq', dtype=DataType.INT64),
                FieldSchema(name='fence', dtype=DataType.VARCHAR, max_length=128),
                # Milvus 3 requires every collection to contain an indexed
                # vector, including control collections queried only by scalars.
                FieldSchema(name='marker_vector', dtype=DataType.FLOAT_VECTOR, dim=2),
            ], description='knowledge_service projection fence markers')
            self._client.create_collection(control, schema=schema)
            index_params = IndexParams()
            index_params.add_index('project_id', index_type='INVERTED')
            index_params.add_index('projection_seq', index_type='INVERTED')
            index_params.add_index('marker_vector', index_type='FLAT', metric_type='L2')
            self._client.create_index(control, index_params)
            self._client.load_collection(control)
        marker_id = hashlib.sha256(
            f'{project_id}\0{int(seq)}\0{fence}'.encode()).hexdigest()
        self._rpc(self._client.upsert, control, [{
            'marker_id': marker_id, 'project_id': str(project_id),
            'projection_seq': int(seq),
            'fence': str(fence),
            'marker_vector': [0.0, 0.0],
        }])
        self._rpc(self._client.flush, control)

    def read_fence_marker(self, project_id: str, destination: str) -> Dict[str, Any]:
        control = self._control_name(destination)
        if not self._client.has_collection(control):
            return {}
        rows = list(self._query_pages(
            control, f'project_id == {json.dumps(str(project_id))}',
            ['projection_seq', 'fence']))
        if not rows:
            return {}
        maximum = max(int(row.get('projection_seq') or 0) for row in rows)
        fences = {str(row.get('fence') or '') for row in rows
                  if int(row.get('projection_seq') or 0) == maximum}
        return {'projection_seq': maximum, 'fences': fences}

    def validate_destination(self, project_id: str, destination: str, *,
                             expected_count: Optional[int] = None,
                             expected_version_ids: Optional[List[str]] = None,
                             expected_projection: Optional[Dict[str, Any]] = None,
                             expected_digest: Optional[str] = None,
                             fence: Optional[str] = None) -> Dict[str, Any]:
        """Fail-closed validation before a generation becomes searchable."""
        self.ensure_collection(destination=destination)
        expression = f'project_id == {json.dumps(str(project_id))} and active == true'
        try:
            counted = self._client.query(
                collection_name=destination, filter=expression,
                output_fields=['count(*)'])
            actual = int((counted or [{}])[0].get('count(*)', 0))
            rows = list(self._query_pages(
                destination, expression, [
                    'record_id', 'version_id', 'projection_seq', 'platform_mode',
                    'business_mode', 'audience_mode', 'scope_identity',
                    'platform_values', 'business_values', 'audience_values',
                    'valid_from_known', 'valid_from_epoch_ms',
                    'valid_until_known', 'valid_until_epoch_ms']))
            marker = self.read_fence_marker(project_id, destination)
        except Exception as exc:
            return {'ok': False, 'reason': f'validation query failed: {exc}'}
        version_ids = sorted(str(row.get('version_id')) for row in rows)
        actual_projection = {}
        for row in rows:
            actual_projection[str(row.get('record_id'))] = {
                'version_id': str(row.get('version_id')),
                'platform': {'mode': row.get('platform_mode'),
                             'values': sorted(row.get('platform_values') or [])},
                'business': {'mode': row.get('business_mode'),
                             'values': sorted(row.get('business_values') or [])},
                'audience': {'mode': row.get('audience_mode'),
                             'values': sorted(row.get('audience_values') or [])},
                'scope_identity': row.get('scope_identity') or '',
                'valid_from': [bool(row.get('valid_from_known')),
                               int(row.get('valid_from_epoch_ms') or 0)],
                'valid_until': [bool(row.get('valid_until_known')),
                                int(row.get('valid_until_epoch_ms') or 0)],
            }
        digest = hashlib.sha256(json.dumps(
            actual_projection, sort_keys=True, ensure_ascii=False,
            separators=(',', ':')).encode()).hexdigest()
        modes_ok = all(
            row.get(field) in {'all', 'specific', 'unknown'}
            for row in rows for field in (
                'platform_mode', 'business_mode', 'audience_mode'))
        times_ok = all(
            (not row.get('valid_from_known')
             or isinstance(row.get('valid_from_epoch_ms'), int))
            and (not row.get('valid_until_known')
                 or isinstance(row.get('valid_until_epoch_ms'), int))
            for row in rows)
        expected_ids = (None if expected_version_ids is None
                        else sorted(str(value) for value in expected_version_ids))
        maximum = max((int(row.get('projection_seq') or 0) for row in rows), default=0)
        latest_fences = set(marker.get('fences') or ())
        ok = bool(expected_count is not None and actual == expected_count
                  and len(rows) == actual and modes_ok and times_ok
                  and expected_ids is not None and version_ids == expected_ids
                  and expected_projection is not None
                  and actual_projection == expected_projection
                  and expected_digest is not None and digest == expected_digest
                  and marker.get('projection_seq') is not None
                  and int(marker['projection_seq']) >= maximum)
        if fence:
            ok = ok and fence in latest_fences
        return {'ok': ok, 'count': actual, 'expected_count': expected_count,
                'max_projection_seq': maximum,
                'marker_fences': sorted(latest_fences),
                'version_digest': digest, 'modes_ok': modes_ok,
                'times_ok': times_ok}

    def authority_repair_ids(self, project_id: str, destination: str,
                             expected: Dict[str, str], heartbeat=None) -> set[str]:
        self.ensure_collection(destination=destination)
        expression = f'project_id == {json.dumps(str(project_id), ensure_ascii=False)}'
        indexed = []
        for row in self._query_pages(
                destination, expression,
                ['record_id', 'version_id', 'projection_seq']):
            indexed.append(row)
            if (len(indexed) % 16384 == 0 and heartbeat is not None
                    and not heartbeat()):
                raise RuntimeError('projection maintenance lease was lost')
        present: Dict[str, set[str]] = {}
        for row in indexed:
            present.setdefault(str(row.get('record_id')), set()).add(
                str(row.get('version_id')))
        repair_ids = {
            record_id for record_id, versions in present.items()
            if record_id not in expected
            or versions != {str(expected[record_id])}}
        repair_ids.update(
            record_id for record_id, version_id in expected.items()
            if present.get(record_id) != {str(version_id)})
        return repair_ids

    def apply_authority_repairs(self, project_id: str, destination: str,
                                records: Iterable[Dict[str, Any]],
                                repair_ids: set[str], *,
                                projection_seq: int,
                                before_write=None) -> Dict[str, int]:
        authoritative = {str(row['id']): row for row in records}
        for record_id in sorted(repair_ids):
            if before_write is not None and not before_write():
                raise RuntimeError('projection maintenance lease was lost')
            self.delete(project_id, record_id=record_id,
                        destination=destination)
            if before_write is not None and not before_write():
                raise RuntimeError('projection maintenance lease was lost')
        repaired = [authoritative[record_id] for record_id in repair_ids
                    if record_id in authoritative]
        for offset in range(0, len(repaired), 64):
            if before_write is not None and not before_write():
                raise RuntimeError('projection maintenance lease was lost')
            self.upsert(repaired[offset:offset + 64], destination=destination,
                        projection_seq=projection_seq,
                        fence='authority-audit', flush=True)
            if before_write is not None and not before_write():
                raise RuntimeError('projection maintenance lease was lost')
        return {'repaired': len(repair_ids)}

    def cleanup_project(self, project_id: str) -> Dict[str, int]:
        """Remove base rows, project alias, and every project generation."""
        prefix = self.project_alias(project_id, self.collection_name) + '__g_'
        destinations = []
        try:
            destinations = [name for name in self._client.list_collections()
                            if name.startswith(prefix)
                            and not name.endswith('__control')]
        except Exception:
            current = self.current_destination(project_id)
            destinations = [current] if current else []
        deleted = 0
        if self._client.has_collection(self.collection_name):
            deleted += self.delete(project_id)
        shared_control = self._control_name(self.collection_name)
        if self._client.has_collection(shared_control):
            self._client.delete(
                shared_control,
                filter=f'project_id == {json.dumps(str(project_id), ensure_ascii=False)}')
        for destination in dict.fromkeys(filter(None, destinations)):
            self.drop_destination(destination)
        alias = self.project_alias(project_id, self.collection_name)
        try:
            self._client.drop_alias(alias=alias)
        except TypeError:
            self._client.drop_alias(alias)
        except Exception:
            if self.current_destination(project_id) is not None:
                raise
        return {'deleted': deleted, 'dropped_generations': len(destinations)}

    # ------------------------------------------------------------------ 检索
    @staticmethod
    def _kind_expr(kinds: Optional[List[str]]) -> str:
        if not kinds:
            return ""
        allowed = {'entity', 'relation', 'chunk'}
        if any(kind not in allowed for kind in kinds):
            raise ValueError('Milvus kinds must be entity, relation, or chunk')
        quoted = ", ".join(json.dumps(str(k), ensure_ascii=False) for k in kinds)
        return f"kind in [{quoted}]"

    def _filter_expr(self, project_id: str, kinds: Optional[List[str]] = None,
                     candidate_ids: Optional[List[str]] = None,
                     scope: Optional[Dict[str, Any]] = None) -> str:
        if candidate_ids is not None:
            raise ValueError('Milvus V2 does not accept candidate_ids')
        parts = [f'project_id == {json.dumps(str(project_id), ensure_ascii=False)}',
                 'active == true']
        kind = self._kind_expr(kinds)
        if kind:
            parts.append(kind)
        scope = dict(scope or {})
        raw_applicability = scope.get('applicability') or {}
        applicability = normalize_applicability({
            key: value for key, value in raw_applicability.items()
            if not (isinstance(value, dict) and value.get('mode') in {
                'specific_only', 'all_only', 'unknown_only'})})
        prefixes = {'platforms': 'platform', 'businesses': 'business',
                    'audiences': 'audience'}
        for dimension in DIMENSIONS:
            raw = raw_applicability.get(dimension)
            special = raw.get('mode') if isinstance(raw, dict) else None
            selected = applicability[dimension]
            if special == 'all_only':
                parts.append(f'{prefixes[dimension]}_mode == "all"')
                continue
            if special == 'unknown_only':
                parts.append(f'{prefixes[dimension]}_mode == "unknown"')
                continue
            if special == 'specific_only':
                values = json.dumps(list(raw.get('values') or []), ensure_ascii=False)
                prefix = prefixes[dimension]
                parts.append(
                    f'({prefix}_mode == "specific" and '
                    f'array_contains_any({prefix}_values, {values}))')
                continue
            if selected['mode'] != 'specific':
                continue
            prefix = prefixes[dimension]
            values = json.dumps(selected['values'], ensure_ascii=False)
            parts.append(
                f'({prefix}_mode == "all" or ({prefix}_mode == "specific" '
                f'and array_contains_any({prefix}_values, {values})))')
        valid_at = normalize_time(scope.get('valid_at')) or utc_now()
        epoch_ms = int(datetime.fromisoformat(
            valid_at.replace('Z', '+00:00')).timestamp() * 1000)
        if scope.get('include_unknown', True) is False:
            parts.append(
                f'(valid_from_known == true and valid_from_epoch_ms <= {epoch_ms})')
        else:
            parts.append(
                f'(valid_from_known == false or valid_from_epoch_ms <= {epoch_ms})')
        parts.append(
            f'(valid_until_known == false or valid_until_epoch_ms > {epoch_ms})')
        return " and ".join(parts)

    def _load_if_needed(self, collection: Optional[str] = None) -> str:
        collection = collection or self.collection_name
        self.ensure_collection(destination=collection)
        try:
            self._client.load_collection(collection)
        except Exception:
            pass  # 已 load 时重复 load 会抛，忽略
        return collection

    def _search_destination(self, project_id: str) -> str:
        return self.current_destination(project_id) or self.collection_name

    def search_dense(self, project_id: str, query_vector: List[float], kinds: Optional[List[str]] = None,
                     k: int = 10, scope: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        collection = self._load_if_needed(self._search_destination(project_id))
        qvec = np.asarray(query_vector, dtype="float32").reshape(1, -1)
        res = self._client.search(
            collection_name=collection, data=qvec, anns_field="embedding",
            filter=self._filter_expr(project_id, kinds, scope=scope), limit=k,
            search_params={"metric_type": _METRIC_DENSE},
            output_fields=["record_id", "kind", "type", "text"])
        return self._hits(res)

    def search_bm25(self, project_id: str, query: str, kinds: Optional[List[str]] = None,
                    k: int = 10, scope: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        collection = self._load_if_needed(self._search_destination(project_id))
        res = self._client.search(
            collection_name=collection, data=[query], anns_field="sparse",
            filter=self._filter_expr(project_id, kinds, scope=scope), limit=k,
            search_params={"metric_type": _METRIC_SPARSE},
            output_fields=["record_id", "kind", "type", "text"])
        return self._hits(res)

    def search_hybrid(self, project_id: str, query: str, query_vector: List[float],
                      kinds: Optional[List[str]] = None, k: int = 10,
                      scope: Optional[Dict[str, Any]] = None,
                      dense_weight: float = 0.5) -> List[Dict[str, Any]]:
        """dense + sparse 原生融合。dense_weight>0 时走 WeightedRanker，否则纯 RRF。"""
        collection = self._load_if_needed(self._search_destination(project_id))
        qvec = np.asarray(query_vector, dtype="float32").reshape(1, -1)
        expr = self._filter_expr(project_id, kinds, scope=scope)
        # filter 必须放进每个 AnnSearchRequest（MilvusClient 的 hybrid_search 无 filter 参数）
        dense_req = AnnSearchRequest(data=qvec, anns_field="embedding",
                                     param={"metric_type": _METRIC_DENSE}, limit=k, expr=expr)
        sparse_req = AnnSearchRequest(data=[query], anns_field="sparse",
                                      param={"metric_type": _METRIC_SPARSE}, limit=k, expr=expr)
        ranker = (WeightedRanker(dense_weight, 1.0 - dense_weight)
                  if dense_weight > 0 else RRFRanker())
        res = self._client.hybrid_search(
            collection_name=collection, reqs=[dense_req, sparse_req], ranker=ranker,
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
