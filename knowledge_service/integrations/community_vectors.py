"""Milvus projection for community reports, independent of fact records."""
from hashlib import sha256
import json
from math import isfinite

import numpy as np
from pymilvus import CollectionSchema, DataType, FieldSchema
from pymilvus.milvus_client.index import IndexParams

from .milvus_store import MilvusSchemaMismatch, VectorDimensionMismatch, bounded_batches


COLLECTION = 'community_report_vectors'
_DENSE_INDEX = 'dense'
_TEXT_LIMITS = {
    'vector_id': 64,
    'project_id': 800,
    'scope_key': 1024,
    'revision': 128,
    'embedding_model': 800,
    'community_id': 800,
    'fingerprint': 128,
}
_MAX_FILTER_KEYS = 1000


def _field(name, value):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > _TEXT_LIMITS[name]:
        raise ValueError(f'社区报告索引字段无效：{name}')
    return value


def _scope(project, scope_key, revision, embedding_model):
    return {name: _field(name, value) for name, value in (
        ('project_id', project), ('scope_key', scope_key), ('revision', revision),
        ('embedding_model', embedding_model),
    )}


def _expression(fields):
    return ' and '.join(f'{field} == {{{field}}}' for field in fields)


def _key(scope, community_id, fingerprint):
    payload = json.dumps([*scope.values(), community_id, fingerprint], ensure_ascii=False,
                         separators=(',', ':'))
    return sha256(payload.encode('utf-8')).hexdigest()


class CommunityVectors:
    def __init__(self, store):
        self.store = store
        self._prepared_client = None

    def _client(self):
        client = self.store._client
        if client is None:
            raise RuntimeError('Milvus 未连接')
        return client

    def _schema(self):
        fields = [FieldSchema(name=name, dtype=DataType.VARCHAR, max_length=limit,
                              **({'is_primary': True} if name == 'vector_id' else
                                 {'is_partition_key': True} if name == 'project_id' else {}))
                  for name, limit in _TEXT_LIMITS.items()]
        fields.append(FieldSchema(name='embedding', dtype=DataType.FLOAT_VECTOR, dim=self.store.dim))
        return CollectionSchema(fields, description='knowledge_service community report semantic index',
                                enable_dynamic_field=False)

    def _validate_schema(self, client):
        schema = self.store._rpc(client.describe_collection, COLLECTION)
        fields = {field['name']: field for field in schema['fields']}
        if set(fields) != {*_TEXT_LIMITS, 'embedding'} or schema.get('enable_dynamic_field'):
            raise MilvusSchemaMismatch('社区报告向量集合字段不一致')
        for name, limit in _TEXT_LIMITS.items():
            field = fields[name]
            if (field.get('type') != DataType.VARCHAR or
                    int((field.get('params') or {}).get('max_length', 0)) != limit):
                raise MilvusSchemaMismatch(f'社区报告向量集合字段定义不一致：{name}')
        if (not fields['vector_id'].get('is_primary') or not fields['project_id'].get('is_partition_key') or
                fields['embedding'].get('type') != DataType.FLOAT_VECTOR):
            raise MilvusSchemaMismatch('社区报告向量集合主键、分区键或向量类型不一致')
        dimension = int((fields['embedding'].get('params') or {}).get('dim', 0))
        if dimension != self.store.dim:
            raise VectorDimensionMismatch(COLLECTION, dimension, self.store.dim, self.store.encoder_identity)

    def _create_index(self, client):
        indexes = IndexParams()
        indexes.add_index('embedding', index_name=_DENSE_INDEX, index_type='HNSW',
                          metric_type='COSINE', M=16, efConstruction=128)
        self.store._rpc(client.create_index, COLLECTION, indexes, sync=True)

    def _prepare(self, *, create):
        with self.store._preparation_lock:
            client = self._client()
            if client is self._prepared_client:
                return client
            if not self.store._rpc(client.has_collection, COLLECTION):
                if not create:
                    return None
                self.store._rpc(client.create_collection, COLLECTION, schema=self._schema())
                self._create_index(client)
            else:
                self._validate_schema(client)
                indexes = self.store._rpc(client.list_indexes, COLLECTION)
                if _DENSE_INDEX not in indexes:
                    if not create:
                        raise MilvusSchemaMismatch('社区报告向量集合尚未建立稠密索引')
                    self._create_index(client)
                else:
                    index = self.store._rpc(client.describe_index, COLLECTION, _DENSE_INDEX)
                    if (index.get('field_name'), index.get('index_type'), index.get('metric_type')) != (
                            'embedding', 'HNSW', 'COSINE'):
                        raise MilvusSchemaMismatch('社区报告向量集合的稠密索引定义不一致')
            self.store._rpc(client.load_collection, COLLECTION)
            self._prepared_client = client
            return client

    def _vector(self, value):
        vector = np.asarray(value, dtype=np.float32)
        if vector.shape != (self.store.dim,) or not np.isfinite(vector).all() or not np.any(vector):
            raise ValueError('社区报告向量维度或数值无效')
        return vector

    def upsert(self, project, scope_key, revision, embedding_model, rows):
        scope = _scope(project, scope_key, revision, embedding_model)
        prepared = []
        for row in rows:
            community_id = _field('community_id', row['id'])
            fingerprint = _field('fingerprint', row['fingerprint'])
            prepared.append({**scope, 'vector_id': _key(scope, community_id, fingerprint),
                             'community_id': community_id, 'fingerprint': fingerprint,
                             'embedding': self._vector(row['embedding'])})
        if not prepared:
            return 0
        client = self._prepare(create=True)
        for batch in bounded_batches(prepared):
            try:
                self.store._rpc(client.upsert, COLLECTION, batch)
            except Exception as exc:
                exc.uncertain_write = True
                raise
        return len(prepared)

    def search(self, project, scope_key, revision, embedding_model, query_vector, limit, *, expected=None):
        scope = _scope(project, scope_key, revision, embedding_model)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('社区语义检索数量必须为 1 到 100')
        vector = self._vector(query_vector)
        if expected is not None and not expected:
            return []
        keys = ([_key(scope, _field('community_id', ident), _field('fingerprint', fingerprint))
                 for ident, fingerprint in expected.items()] if expected is not None else None)
        client = self._prepare(create=False)
        if client is None:
            return []
        batches = (keys[start:start + _MAX_FILTER_KEYS] for start in range(0, len(keys), _MAX_FILTER_KEYS)) if keys else [None]
        hits = []
        for batch in batches:
            params = dict(scope)
            expression = _expression(scope)
            if batch is not None:
                expression += ' and vector_id in {report_keys}'
                params['report_keys'] = batch
            try:
                result = self.store._rpc(client.search, collection_name=COLLECTION, data=[vector],
                                         anns_field='embedding', filter=expression, filter_params=params,
                                         limit=limit, search_params={'metric_type': 'COSINE'},
                                         output_fields=['community_id', 'fingerprint'], consistency_level='Strong',
                                         group_by_field='community_id', group_size=1)
            except Exception:
                with self.store._preparation_lock:
                    if self._prepared_client is client:
                        self._prepared_client = None
                raise
            for match in result[0]:
                entity = match['entity']
                ident, fingerprint = entity['community_id'], entity['fingerprint']
                score = float(match['distance'])
                if isfinite(score) and (expected is None or expected.get(ident) == fingerprint):
                    hits.append({'id': ident, 'fingerprint': fingerprint, 'score': score})
        return sorted(hits, key=lambda hit: (-hit['score'], hit['id']))[:limit]

    def _delete(self, scope):
        client = self._client()
        if not self.store._rpc(client.has_collection, COLLECTION):
            return 0
        result = self.store._rpc(client.delete, COLLECTION, filter=_expression(scope), filter_params=scope)
        return int(result.get('delete_count', result.get('deleted_count', 0)))

    def discard(self, project, scope_key, revision, embedding_model):
        """Discard an exact obsolete revision and model after the caller fences it."""
        return self._delete(_scope(project, scope_key, revision, embedding_model))

    def cleanup_project(self, project):
        return self._delete({'project_id': _field('project_id', project)})
