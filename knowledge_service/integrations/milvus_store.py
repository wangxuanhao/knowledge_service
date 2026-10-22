"""The single shared native Milvus index, scoped by project and build version."""
from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from threading import RLock
from time import monotonic, sleep

import numpy as np
from pymilvus import (AnnSearchRequest, CollectionSchema, DataType, FieldSchema,
                      Function, FunctionType, MilvusClient, RRFRanker)
from pymilvus.milvus_client.index import IndexParams

from ..domain.search_filter_catalog import FilterCatalog, INT64_MAX
from ..domain.search_projection import _validate_columns, projection_fingerprint
from .milvus_filters import compile_scope


def row_bytes(row):
    """Conservative scalar/vector payload estimate, including per-field overhead."""
    def size(value):
        if isinstance(value, str):
            return len(value.encode('utf-8')) + 16
        if isinstance(value, np.ndarray):
            return int(value.nbytes) + 32
        if isinstance(value, (list, tuple)):
            return 32 + sum(size(item) for item in value)
        return 16
    return 256 + sum(len(name.encode('utf-8')) + 64 + size(value)
                     for name, value in row.items())


def bounded_batches(rows, *, max_rows=64, max_bytes=24 * 1024 * 1024):
    """Bound every RPC by both row count and payload bytes."""
    batch, used = [], 0
    for row in rows:
        required = row_bytes(row)
        if required > max_bytes:
            raise ValueError('平铺投影单行超过 RPC 字节预算')
        if batch and (len(batch) >= max_rows or used + required > max_bytes):
            yield batch
            batch, used = [], 0
        batch.append(row)
        used += required
    if batch:
        yield batch


COLLECTION = 'knowledge_vectors'
_TYPES = {'varchar': DataType.VARCHAR, 'bool': DataType.BOOL,
          'int64': DataType.INT64, 'double': DataType.DOUBLE,
          'array_varchar': DataType.ARRAY, 'float_vector': DataType.FLOAT_VECTOR,
          'sparse_float_vector': DataType.SPARSE_FLOAT_VECTOR}


def _metadata_object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _metadata_names(value):
    # SDK versions expose either protobuf repeated fields or list repr strings.
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return []
    if isinstance(value, (str, bytes, Mapping, set, frozenset)):
        return []
    try:
        names = list(value)
    except TypeError:
        return []
    return names if all(isinstance(name, str) for name in names) else []


def _metadata_bool(value):
    return value is True or value == 1 or (isinstance(value, str) and value.lower() == 'true')


def _metadata_int(value, default=-1):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class MilvusSchemaMismatch(RuntimeError):
    requires_rebuild = True


class VectorDimensionMismatch(MilvusSchemaMismatch):
    def __init__(self, collection, collection_dim, expected_dim, encoder_identity=''):
        self.collection = collection
        self.collection_dim = collection_dim
        self.expected_dim = expected_dim
        self.encoder_identity = encoder_identity
        super().__init__(f'Milvus {collection} 向量维度 {collection_dim} 与配置 {expected_dim} 不符；请统一模型配置')


class MilvusStore:
    def __init__(self, host='localhost', port='19530', dim=1024,
                 collection=COLLECTION, encoder_identity=''):
        if collection != COLLECTION:
            raise ValueError('本服务只使用 knowledge_vectors 共享集合')
        self.host, self.port, self.dim = host, port, dim
        self.collection_name = COLLECTION
        self.encoder_identity = encoder_identity
        self._client = None
        self._preparation_lock = RLock()
        self._schema_cache = None
        self._validated = {}
        self._loaded_at = 0

    @staticmethod
    def _rpc(method, *args, **kwargs):
        return method(*args, timeout=20, **kwargs)

    def connect(self):
        with self._preparation_lock:
            self._schema_cache = None
            self._validated.clear()
            self._loaded_at = 0
            self._client = MilvusClient(uri=f'http://{self.host}:{self.port}')
        return self

    @property
    def available(self):
        return self._client is not None

    def ping(self):
        if self._client is None:
            return False
        self._rpc(self._client.list_collections)
        return True

    def collection_dim(self):
        """Probe the existing schema only; health checks never prepare indexes."""
        if self._client is None:
            return None
        with self._preparation_lock:
            if not self._rpc(self._client.has_collection, COLLECTION):
                return None
            info = self._rpc(self._client.describe_collection, COLLECTION)
            field = next((item for item in info.get('fields', [])
                          if item.get('name') == 'embedding'), None)
            if field is None:
                return None
            params = field.get('params') or field.get('type_params') or {}
            dimension = params.get('dim')
            return int(dimension) if dimension is not None else None

    def close(self):
        with self._preparation_lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None
                    self._schema_cache = None
                    self._validated.clear()
                    self._loaded_at = 0

    def _scope(self, project_id, index_version):
        if not isinstance(project_id, str) or not project_id or len(project_id.encode('utf-8')) > 800:
            raise ValueError('无效的 project_id')
        if type(index_version) is not int or not 1 <= index_version <= INT64_MAX:
            raise ValueError('index_version 必须为正 INT64')
        return 'project_id == {project} and index_version == {index}', {'project': project_id, 'index': index_version}

    def _field_params(self, column):
        params = {'nullable': column.nullable}
        if column.name == 'vector_id':
            params['is_primary'] = True
        if column.name == 'project_id':
            params['is_partition_key'] = True
        if column.max_length is not None:
            params['max_length'] = column.max_length
        if column.max_capacity is not None:
            params.update(max_capacity=column.max_capacity, element_type=DataType.VARCHAR)
        if column.name == 'embedding':
            params['dim'] = self.dim
        if column.name == 'text':
            params.update(enable_analyzer=True, analyzer_params={'type': 'chinese'})
        return params

    def _indexes(self, columns):
        indexes = IndexParams()
        for column in columns:
            if column.name == 'embedding':
                indexes.add_index('embedding', index_name='dense', index_type='HNSW',
                                  metric_type='COSINE', M=16, efConstruction=256)
            elif column.name == 'sparse':
                indexes.add_index('sparse', index_name='bm25', index_type='SPARSE_INVERTED_INDEX', metric_type='BM25')
            elif column.index:
                indexes.add_index(column.name, index_name='filter_' + column.name, index_type='INVERTED')
        return indexes

    def _invalidate(self):
        self._schema_cache = None
        self._validated.clear()
        self._loaded_at = 0

    def _create_collection(self, *, catalog):
        columns = catalog.column_defs()
        fields = [FieldSchema(name=c.name, dtype=_TYPES[c.field_type], **self._field_params(c)) for c in columns]
        schema = CollectionSchema(fields, description='knowledge_service shared page search', enable_dynamic_field=False)
        schema.add_function(Function(name='text_bm25', function_type=FunctionType.BM25,
                                     input_field_names=['text'], output_field_names=['sparse']))
        self._rpc(self._client.create_collection, COLLECTION, schema=schema)
        indexes = self._indexes(columns)
        self._rpc(self._client.create_index, COLLECTION, indexes, sync=False)
        self._wait_for_indexes([item.index_name for item in indexes])
        self._rpc(self._client.load_collection, COLLECTION)
        self._invalidate()
        return self

    def _wait_for_indexes(self, names, *, timeout_seconds=180):
        """Bound the explicit build's readiness wait separately from submit RPC."""
        pending = set(names)
        deadline = monotonic() + timeout_seconds
        while pending:
            for name in tuple(pending):
                info = self._rpc(self._client.describe_index,
                                 collection_name=COLLECTION, index_name=name)
                state = str(info.get('state', '')).lower()
                if state in {'finished', '3'}:
                    pending.remove(name)
                elif state in {'failed', '4'}:
                    raise RuntimeError('Milvus 索引构建失败: ' + name)
            if pending:
                if monotonic() >= deadline:
                    raise RuntimeError('Milvus 索引仍未就绪，请稍后显式重试: '
                                       + ', '.join(sorted(pending)))
                sleep(min(.5, max(0, deadline - monotonic())))

    def _metadata(self):
        if self._client is None:
            raise RuntimeError('Milvus 未连接')
        now = monotonic()
        if self._schema_cache is None or now - self._schema_cache[0] >= 60:
            if not self._rpc(self._client.has_collection, COLLECTION):
                raise MilvusSchemaMismatch('Milvus 尚未向量化，请在项目页面显式向量化')
            info = self._rpc(self._client.describe_collection, COLLECTION) or {}
            names = self._rpc(self._client.list_indexes, collection_name=COLLECTION)
            indexes = {name: self._rpc(self._client.describe_index, collection_name=COLLECTION, index_name=name) for name in names}
            self._schema_cache = (now, info, indexes)
        return self._schema_cache[1:]

    def _schema_issues(self, catalog, info, indexes):
        columns = catalog.column_defs()
        fields = {f.get('name'): f for f in info.get('fields', [])}
        issues = []
        if _metadata_bool(info.get('enable_dynamic_field', False)):
            issues.append('dynamic fields are forbidden')
        if _metadata_bool(info.get('auto_id', False)):
            issues.append('auto_id is forbidden')
        unexpected = set(fields) - {column.name for column in columns}
        if unexpected:
            issues.append('unexpected columns: ' + ', '.join(sorted(map(str, unexpected))))
        if any(str(name).startswith('bf_') for name in fields):
            issues.append('legacy bf_ columns require explicit schema cleanup')
        for column in columns:
            actual = fields.get(column.name)
            if actual is None:
                issues.append('missing column ' + column.name)
                continue
            if _metadata_int(actual.get('type', actual.get('data_type'))) != int(_TYPES[column.field_type]):
                issues.append(column.name + ' dtype differs')
            if _metadata_bool(actual.get('nullable', False)) != column.nullable:
                issues.append(column.name + ' nullable differs')
            if _metadata_bool(actual.get('is_primary', False)) != (column.name == 'vector_id'):
                issues.append(column.name + ' primary differs')
            if _metadata_bool(actual.get('is_partition_key', False)) != (column.name == 'project_id'):
                issues.append(column.name + ' partition key differs')
            if _metadata_bool(actual.get('auto_id', False)):
                issues.append(column.name + ' auto_id is forbidden')
            params = _metadata_object(actual.get('params') or actual.get('type_params'))
            for name, required in (('max_length', column.max_length), ('max_capacity', column.max_capacity)):
                if required is not None and _metadata_int(params.get(name), 0) < required:
                    issues.append(column.name + ' capacity differs')
            if column.name == 'embedding' and _metadata_int(params.get('dim'), 0) != self.dim:
                raise VectorDimensionMismatch(COLLECTION, _metadata_int(params.get('dim'), 0), self.dim, self.encoder_identity)
            if column.field_type == 'array_varchar' and _metadata_int(actual.get('element_type', params.get('element_type'))) != int(DataType.VARCHAR):
                issues.append(column.name + ' array dtype differs')
            if column.name == 'text' and (not _metadata_bool(params.get('enable_analyzer'))
                    or _metadata_object(params.get('analyzer_params')) != {'type': 'chinese'}):
                issues.append('text analyzer differs')
            index = ('dense' if column.name == 'embedding' else 'bm25' if column.name == 'sparse'
                     else 'filter_' + column.name if column.index else None)
            if index is not None and index not in indexes:
                issues.append('missing index ' + index)
            elif index is not None:
                definition = indexes[index]
                expected_type = ('HNSW' if column.name == 'embedding' else
                                 'SPARSE_INVERTED_INDEX' if column.name == 'sparse' else 'INVERTED')
                if definition.get('field_name') != column.name:
                    issues.append(index + ' field differs')
                if str(definition.get('index_type', '')).upper() != expected_type:
                    issues.append(index + ' type differs')
        functions = info.get('functions') or []
        if (len(functions) != 1 or not any(f.get('name') == 'text_bm25'
                and _metadata_int(f.get('type', f.get('function_type'))) == int(FunctionType.BM25)
                and _metadata_names(f.get('input_field_names')) == ['text']
                and _metadata_names(f.get('output_field_names')) == ['sparse'] for f in functions)):
            issues.append('BM25 function differs')
        for name, metric in (('dense', 'COSINE'), ('bm25', 'BM25')):
            if name in indexes and str(indexes[name].get('metric_type', '')).upper() != metric:
                issues.append(name + ' metric differs')
        return issues

    @staticmethod
    def _pending_indexes(catalog, indexes):
        pending = []
        for column in catalog.column_defs():
            name = ('dense' if column.name == 'embedding' else 'bm25' if column.name == 'sparse'
                    else 'filter_' + column.name if column.index else None)
            if name in indexes and str(indexes[name].get('state', '')).lower() not in {'finished', '3'}:
                pending.append(name)
        return pending

    def check_collection(self, *, catalog):
        if not isinstance(catalog, FilterCatalog):
            raise ValueError('需要项目冻结的筛选字段清单')
        with self._preparation_lock:
            checked = self._validated.get(catalog.fingerprint)
            if checked is not None and monotonic() - checked < 60:
                return self
            info, indexes = self._metadata()
            issues = self._schema_issues(catalog, info, indexes)
            if issues:
                raise MilvusSchemaMismatch('共享集合与本项目字段不符: ' + ', '.join(issues))
            pending = self._pending_indexes(catalog, indexes)
            if pending:
                self._invalidate()
                raise RuntimeError('Milvus 索引尚未就绪: ' + ', '.join(pending))
            self._validated[catalog.fingerprint] = monotonic()
        return self

    def ensure_collection(self, *, project_id, index_version, catalog):
        """Explicit build prepares fixed indexes; never infers or adds columns."""
        self._scope(project_id, index_version)
        catalog.column_defs()
        if self._client is None:
            raise RuntimeError('Milvus 未连接')
        with self._preparation_lock:
            if not self._rpc(self._client.has_collection, COLLECTION):
                return self._create_collection(catalog=catalog)
            info, indexes = self._metadata()
            columns = catalog.column_defs()
            issues = self._schema_issues(catalog, info, indexes)
            missing_indexes = [c for c in columns if c.index
                               and 'filter_' + c.name not in indexes]
            allowed = {'missing index filter_' + c.name for c in missing_indexes}
            remaining = [issue for issue in issues if issue not in allowed]
            if remaining:
                raise MilvusSchemaMismatch(', '.join(remaining))
            pending = self._pending_indexes(catalog, indexes)
            if missing_indexes:
                params = self._indexes(missing_indexes)
                self._rpc(self._client.create_index, COLLECTION, params, sync=False)
                pending.extend(item.index_name for item in params)
            if pending:
                self._wait_for_indexes(pending)
                self._invalidate()
            return self.check_collection(catalog=catalog)

    def upsert_rows(self, rows, *, project_id, index_version, catalog, flush=False):
        self._scope(project_id, index_version)
        expected = {c.name for c in catalog.column_defs()} - {'sparse'}
        prepared = []
        for incoming in rows:
            if set(incoming) != expected:
                raise ValueError('Milvus 只接受完整平铺投影')
            row = dict(incoming)
            if row['project_id'] != project_id or row['index_version'] != index_version:
                raise ValueError('平铺投影与项目构建范围不符')
            _validate_columns(row, catalog.column_defs())
            vector = np.asarray(row['embedding'], dtype='float32').reshape(-1)
            if vector.size != self.dim:
                raise VectorDimensionMismatch(COLLECTION, self.dim, vector.size, self.encoder_identity)
            if not np.isfinite(vector).all() or not np.linalg.norm(vector):
                raise ValueError('无效的投影向量')
            row['embedding'] = vector
            prepared.append(row)
        if not prepared:
            return 0
        self.check_collection(catalog=catalog)
        for batch in bounded_batches(prepared):
            try:
                self._rpc(self._client.upsert, COLLECTION, batch)
            except Exception as exc:
                exc.uncertain_write = True
                raise
        if flush:
            self._rpc(self._client.flush, COLLECTION)
        return len(prepared)

    def delete_records(self, record_ids, *, project_id, index_version, catalog):
        expression, params = self._scope(project_id, index_version)
        if record_ids is not None:
            values = list(record_ids)
            if not values:
                return 0
            if len(values) > 1000 or any(not isinstance(v, str) or not v for v in values):
                raise ValueError('删除记录 ID 必须为不超过 1000 项的字符串列表')
            expression += ' and record_id in {records}'
            params['records'] = values
        self.check_collection(catalog=catalog)
        try:
            result = self._rpc(self._client.delete, COLLECTION, filter=expression, filter_params=params)
        except Exception as exc:
            exc.uncertain_write = True
            raise
        return int(result.get('delete_count', result.get('deleted_count', 0)))

    def cleanup_project(self, project_id):
        """Deleted-project cleanup intentionally removes all of its builds."""
        if not isinstance(project_id, str) or not project_id:
            raise ValueError('无效的项目 ID')
        if not self._rpc(self._client.has_collection, COLLECTION):
            return {'deleted': 0}
        result = self._rpc(self._client.delete, COLLECTION,
                           filter='project_id == {project}', filter_params={'project': project_id})
        return {'deleted': int(result.get('delete_count', result.get('deleted_count', 0)))}

    def delete_versions(self, project_id, versions):
        scopes = [self._scope(project_id, version) for version in sorted(set(versions))]
        if not scopes or not self._rpc(self._client.has_collection, COLLECTION):
            return 0
        deleted = 0
        for expression, params in scopes:
            result = self._rpc(self._client.delete, COLLECTION, filter=expression, filter_params=params)
            deleted += int(result.get('delete_count', result.get('deleted_count', 0)))
        return deleted

    def _query_pages(self, expression, params, output_fields):
        iterator = self._rpc(self._client.query_iterator, collection_name=COLLECTION,
                             filter=expression, expr_params=params,
                             output_fields=output_fields, batch_size=512,
                             consistency_level='Strong')
        try:
            while True:
                page = iterator.next()
                if not page:
                    return
                yield from page
        finally:
            iterator.close()

    def validate_projection(self, expected, *, project_id, index_version, catalog):
        """Compare authoritative PG receipts with every native row in this scope."""
        self.check_collection(catalog=catalog)
        expression, params = self._scope(project_id, index_version)
        fields = [c.name for c in catalog.column_defs() if c.name not in {'embedding', 'sparse'}]
        count, ok, page = 0, True, []
        expected_count = len(expected)
        lookup = getattr(expected, 'lookup', None)
        if lookup is None:
            lookup = lambda ids: {record_id: expected[record_id] for record_id in ids if record_id in expected}
        def compare(values):
            actual = {row['record_id']: {'version_id': row['version_id'],
                      'fingerprint': projection_fingerprint(row)} for row in values}
            return len(actual) == len(values) and actual == lookup(list(actual))
        for row in self._query_pages(expression, params, fields):
            count += 1
            page.append(row)
            if len(page) == 512:
                ok = compare(page) and ok
                page = []
        if page:
            ok = compare(page) and ok
        return {'ok': ok and count == expected_count, 'count': count,
                'expected_count': expected_count}

    def _load_if_needed(self, *, catalog):
        with self._preparation_lock:
            try:
                self.check_collection(catalog=catalog)
                if monotonic() - self._loaded_at >= 60:
                    self._rpc(self._client.load_collection, COLLECTION)
                    self._loaded_at = monotonic()
            except Exception:
                self._invalidate()
                raise

    def _search_rpc(self, operation, **kwargs):
        try:
            return self._rpc(operation, **kwargs)
        except Exception:
            with self._preparation_lock:
                self._invalidate()
            raise

    def _query_vector(self, value):
        vector = np.asarray(value, dtype='float32').reshape(1, -1)
        if vector.shape[1] != self.dim or not np.isfinite(vector).all() or not np.linalg.norm(vector):
            raise ValueError('查询编码器向量维度或数值无效')
        return vector

    def search(self, project_id, query, *, retrieval_mode, query_vector=None,
               kinds=None, k=10, scope=None, index_version, catalog, platform_match=None,
               eligible_version_ids=None):
        self._scope(project_id, index_version)
        if retrieval_mode not in {'keyword', 'semantic', 'hybrid'}:
            raise ValueError('不支持的检索模式')
        if type(k) is not int or not 1 <= k <= 16384:
            raise ValueError('检索数量必须为 1 到 16384')
        if eligible_version_ids is not None:
            if (not isinstance(eligible_version_ids, (list, tuple)) or len(eligible_version_ids) > 1000
                    or any(not isinstance(value, str) or not value
                           or len(value.encode('utf-8')) > 800 for value in eligible_version_ids)):
                raise ValueError('PG 资格批次必须是不超过 1000 项的非空版本 ID 列表')
            if not eligible_version_ids:
                return []
        compiled = compile_scope(project_id, scope or {}, catalog, kinds=kinds,
                                 platform_match=platform_match)
        expression = '(' + compiled.expr + ') and index_version == {index_version}'
        params = dict(compiled.filter_params, index_version=index_version)
        if eligible_version_ids is not None:
            expression += ' and version_id in {eligible_versions}'
            params['eligible_versions'] = list(dict.fromkeys(eligible_version_ids))
        self._load_if_needed(catalog=catalog)
        outputs = ['record_id', 'version_id', 'kind', 'type', 'text']
        if retrieval_mode == 'hybrid':
            requests = [AnnSearchRequest(data=self._query_vector(query_vector), anns_field='embedding',
                            param={'metric_type': 'COSINE'}, limit=k, expr=expression, expr_params=params),
                        AnnSearchRequest(data=[query], anns_field='sparse', param={'metric_type': 'BM25'},
                            limit=k, expr=expression, expr_params=params)]
            result = self._search_rpc(self._client.hybrid_search, collection_name=COLLECTION,
                               reqs=requests, ranker=RRFRanker(), limit=k,
                               output_fields=outputs, consistency_level='Strong')
        else:
            dense = retrieval_mode == 'semantic'
            result = self._search_rpc(self._client.search, collection_name=COLLECTION,
                         data=self._query_vector(query_vector) if dense else [query],
                         anns_field='embedding' if dense else 'sparse', filter=expression,
                         filter_params=params, limit=k,
                         search_params={'metric_type': 'COSINE' if dense else 'BM25'},
                         output_fields=outputs, consistency_level='Strong')
        return self._hits(result)

    @staticmethod
    def _hits(result):
        hits = []
        for batch in result:
            for hit in batch:
                entity = hit.get('entity') or {}
                hits.append({**{key: entity.get(key) for key in ('record_id','version_id','kind','type','text')},
                             'score': float(hit.get('distance', 0.0))})
        return hits
