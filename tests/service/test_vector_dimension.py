"""Embedding 与 Milvus 必须在连接前约定同一个、可证明的向量维度。"""
import json
import sys
import time
import types
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge_service.api import _build_milvus_store, _resolve_milvus_dim, create_app
from knowledge_service.integrations import embeddings
from knowledge_service.integrations.embeddings import (
    HashingEncoder,
    LocalEncoder,
    RemoteEncoder,
    encoder_dimension,
)
from knowledge_service.integrations.milvus_store import (
    COLLECTION,
    MilvusStore,
    MilvusSchemaMismatch,
    VectorDimensionMismatch,
)
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection
from test_production_milvus_schema import production_metadata

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / 'data' / 'model'


def _schema_client(dim, *, exists=True, legacy=False):
    """External SDK metadata and row I/O only; store validation remains real."""
    info, indexes = production_metadata()
    next(field for field in info['fields'] if field['name'] == 'embedding')['params']['dim'] = dim
    if legacy:
        info = {'fields': [next(field for field in info['fields'] if field['name'] == 'embedding')]}
        indexes = {}
    live = {'exists': exists}
    client = Mock()
    client.rows = {}
    client.has_collection.side_effect = lambda name, **kwargs: name == COLLECTION and live['exists']
    client.describe_collection.side_effect = lambda name, **kwargs: deepcopy(info)
    client.list_indexes.side_effect = lambda **kwargs: list(indexes)
    client.describe_index.side_effect = lambda **kwargs: deepcopy(indexes[kwargs['index_name']])
    client.list_collections.return_value = [COLLECTION]
    client.search.return_value = [[]]

    def create(name, *, schema, **kwargs):
        assert name == COLLECTION
        live['exists'] = True

    def upsert(name, rows, **kwargs):
        assert name == COLLECTION
        client.rows.update((row['vector_id'], dict(row)) for row in rows)

    client.create_collection.side_effect = create
    client.upsert.side_effect = upsert
    return client


# --------------------------------------------------------------------------- model metadata

def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


def _model_dir(tmp_path: Path, *, root=None, modules=None, module_configs=None) -> Path:
    model = tmp_path / 'model'
    model.mkdir()
    if root is not None:
        _write_json(model / 'config.json', root)
    if modules is not None:
        _write_json(model / 'modules.json', modules)
    for relative_path, payload in (module_configs or {}).items():
        _write_json(model / relative_path / 'config.json', payload)
    return model


def test_local_encoder_reads_current_pooling_layout_as_1024(tmp_path):
    model = _model_dir(
        tmp_path,
        root={'hidden_size': 768},
        modules=[
            {'idx': 0, 'name': '0', 'path': '', 'type': 'sentence_transformers.models.Transformer'},
            {'idx': 1, 'name': '1', 'path': '1_Pooling', 'type': 'sentence_transformers.models.Pooling'},
        ],
        module_configs={'1_Pooling': {'word_embedding_dimension': 1024}},
    )

    encoder = LocalEncoder(model)

    assert encoder.dimension == 1024
    assert encoder.dimension_source == '1_Pooling/config.json:word_embedding_dimension'


def test_local_encoder_uses_the_last_declared_transform_dimension(tmp_path):
    model = _model_dir(
        tmp_path,
        root={'sentence_embedding_dimension': 768},
        modules=[
            {'idx': 0, 'path': '1_Pooling', 'type': 'sentence_transformers.models.Pooling'},
            {'idx': 1, 'path': '2_Dense', 'type': 'sentence_transformers.models.Dense'},
            {'idx': 2, 'path': '3_Output', 'type': 'custom.Output'},
        ],
        module_configs={
            '1_Pooling': {'word_embedding_dimension': 1024},
            '2_Dense': {'out_features': 512},
            '3_Output': {'output_dimension': 384},
        },
    )

    encoder = LocalEncoder(model)

    assert encoder.dimension == 384
    assert encoder.dimension_source == '3_Output/config.json:output_dimension'


@pytest.mark.parametrize('root', [
    {'sentence_embedding_dimension': 1536},
    {'hidden_size': 768},
    {'d_model': 512},
])
def test_local_encoder_falls_back_to_supported_root_dimension_keys(tmp_path, root):
    model = _model_dir(tmp_path, root=root)
    key, expected = next(iter(root.items()))

    encoder = LocalEncoder(model)

    assert encoder.dimension == expected
    assert encoder.dimension_source == f'config.json:{key}'


def test_local_encoder_ignores_malformed_module_metadata_and_uses_root(tmp_path):
    model = _model_dir(tmp_path, root={'hidden_size': 768})
    (model / 'modules.json').write_text('{bad json', encoding='utf-8')

    encoder = LocalEncoder(model)

    assert encoder.dimension == 768
    assert encoder.dimension_source == 'config.json:hidden_size'


def test_local_encoder_reports_unknown_when_configs_are_missing_or_invalid(tmp_path):
    model = _model_dir(tmp_path)
    (model / 'config.json').write_text('{bad json', encoding='utf-8')

    encoder = LocalEncoder(model)

    assert encoder.dimension is None
    assert encoder.dimension_source == 'unavailable'


def test_local_encoder_rejects_loaded_model_dimension_that_disagrees_with_config(
        tmp_path, monkeypatch):
    model = _model_dir(tmp_path, root={'hidden_size': 1024})

    class FakeSentenceTransformer:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_sentence_embedding_dimension(self):
            return 768

        def encode(self, *_args, **_kwargs):
            raise AssertionError('dimension mismatch must fail before encoding')

    monkeypatch.setitem(
        sys.modules,
        'sentence_transformers',
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    encoder = LocalEncoder(model)
    with pytest.raises(embeddings.EmbeddingDimensionMismatch,
                       match='配置声明 1024 维.*实际模型 768 维'):
        encoder.encode(['test'])
    assert encoder.dimension == 1024, '加载权重后也不能悄悄改变建表所用的维度'


def test_local_encoder_checks_encoded_vector_when_model_cannot_report_dimension(
        tmp_path, monkeypatch):
    model = _model_dir(tmp_path, root={'hidden_size': 4})

    class FakeSentenceTransformer:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_sentence_embedding_dimension(self):
            return None

        def encode(self, *_args, **_kwargs):
            return types.SimpleNamespace(tolist=lambda: [[0.0, 0.0, 0.0]])

    monkeypatch.setitem(
        sys.modules,
        'sentence_transformers',
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    with pytest.raises(embeddings.EmbeddingDimensionMismatch,
                       match='配置声明 4 维.*实际向量 3 维'):
        LocalEncoder(model).encode(['test'])


# --------------------------------------------------------------------------- dimension resolution

def test_demo_encoder_declares_the_dimension_it_actually_produces():
    """demo 编码器声明 256，就必须真的产出 256 维——声明和实现不能各说各话。"""
    encoder = HashingEncoder()
    assert encoder.dimension == 256
    assert len(encoder.encode(['商户退款'])[0]) == 256


def test_checked_in_model_dimension_comes_from_pooling_config_when_available():
    """部署模型很大；只读 metadata，绝不能为这个断言加载权重。"""
    if not (MODEL_DIR / 'modules.json').exists():
        pytest.skip('本机没有 data/model，跳过')
    encoder = LocalEncoder(MODEL_DIR)
    assert encoder.dimension == 1024
    assert encoder.dimension_source == '1_Pooling/config.json:word_embedding_dimension'
    assert encoder_dimension(encoder) == 1024


def test_dimension_falls_back_to_the_encoder_when_not_declared(monkeypatch):
    """没设 KG_MILVUS_DIM 就按编码器来——这样任何编码器都不会和表结构错配。"""
    monkeypatch.delenv('KG_MILVUS_DIM', raising=False)
    assert _resolve_milvus_dim(HashingEncoder()) == 256


def test_matching_compatibility_dimension_is_accepted(monkeypatch):
    monkeypatch.setenv('KG_MILVUS_DIM', '256')
    assert _resolve_milvus_dim(HashingEncoder()) == 256


def test_mismatch_between_env_and_encoder_raises_before_milvus_connect(monkeypatch):
    monkeypatch.setenv('KG_MILVUS_DIM', '1024')
    monkeypatch.setenv('KG_VECTOR_BACKEND', 'milvus')
    connected = False

    def unexpected_connect(_self):
        nonlocal connected
        connected = True

    monkeypatch.setattr(MilvusStore, 'connect', unexpected_connect)

    with pytest.raises(
            embeddings.EmbeddingDimensionConfigurationError,
            match='KG_MILVUS_DIM=1024.*编码器维度 256'):
        _build_milvus_store(HashingEncoder())
    assert connected is False


def test_milvus_store_keeps_encoder_identity_not_dimension_source(monkeypatch):
    monkeypatch.setenv('KG_VECTOR_BACKEND', 'milvus')
    monkeypatch.setenv('KG_MILVUS_DIM', '256')
    monkeypatch.setattr(MilvusStore, 'connect', lambda self: self)
    monkeypatch.setattr(MilvusStore, 'ensure_collection', lambda self: self)

    store = _build_milvus_store(HashingEncoder())

    assert store.encoder_identity == HashingEncoder.identity
    assert store.encoder_identity != HashingEncoder.dimension_source


def test_shared_milvus_collection_isolates_projects_and_index_generations(monkeypatch):
    monkeypatch.setenv('KG_VECTOR_BACKEND', 'milvus')
    monkeypatch.setenv('KG_MILVUS_DIM', '256')
    monkeypatch.setenv('KG_MILVUS_COLLECTION', 'knowledge_redesign_validation')
    client = _schema_client(256)

    def connect(store):
        store._client = client
        return store

    monkeypatch.setattr(MilvusStore, 'connect', connect)
    store = _build_milvus_store(HashingEncoder())
    # Older deployments selected a collection through this environment value.
    # The current shared index instead isolates every request by project/build.
    assert store.collection_name == COLLECTION == 'knowledge_vectors'
    assert client.mock_calls == [], 'startup must only connect, never prepare the index'
    with pytest.raises(ValueError, match='knowledge_vectors'):
        MilvusStore(collection='knowledge_redesign_validation')

    scopes = [('project-a', 1), ('project-a', 2), ('project-b', 1)]
    for project, generation in scopes:
        assert store.search(project, '退款', retrieval_mode='keyword', index_version=generation,
                            catalog=default_catalog(), kinds=['chunk']) == []
    assert client.search.call_count == len(scopes)
    for call, (project, generation) in zip(client.search.call_args_list, scopes):
        assert call.kwargs['collection_name'] == COLLECTION
        assert call.kwargs['filter_params']['index_version'] == generation
        project_parameter = next(name for name, value in call.kwargs['filter_params'].items()
                                 if value == project)
        assert f'project_id == {{{project_parameter}}}' in call.kwargs['filter']
        assert 'index_version == {index_version}' in call.kwargs['filter']
    for method in (client.create_collection, client.create_index, client.drop_collection,
                   client.upsert, client.delete):
        method.assert_not_called()


def test_legacy_milvus_schema_keeps_connected_store_for_generation_rebuild(monkeypatch):
    monkeypatch.setenv('KG_VECTOR_BACKEND', 'milvus')
    monkeypatch.setenv('KG_MILVUS_DIM', '256')
    client = _schema_client(256, legacy=True)

    def connect(store):
        store._client = client
        return store

    monkeypatch.setattr(MilvusStore, 'connect', connect)
    diagnostics = []
    store = _build_milvus_store(HashingEncoder(), diagnostics=diagnostics)

    assert isinstance(store, MilvusStore)
    assert store.available and store._client is client
    assert diagnostics == [] and client.mock_calls == []
    with pytest.raises(MilvusSchemaMismatch, match='missing column vector_id'):
        store.ensure_collection(project_id='project', index_version=2, catalog=default_catalog())
    assert store.available and store._client is client
    assert store.ping(), 'schema rejection must retain the connection for explicit maintenance'
    for method in (client.close, client.create_collection, client.create_index,
                   client.drop_collection, client.upsert, client.delete):
        method.assert_not_called()


def test_unknown_encoder_dimension_requires_an_explicit_positive_dimension(monkeypatch):

    class Opaque:
        identity = 'contains-a-secret-url-that-must-not-be-logged'

    monkeypatch.delenv('KG_MILVUS_DIM', raising=False)
    with pytest.raises(
            embeddings.EmbeddingDimensionConfigurationError,
            match='无法确定 embedding 维度.*KG_EMBEDDING_DIMENSION') as excinfo:
        _resolve_milvus_dim(Opaque())
    assert Opaque.identity not in str(excinfo.value)

    monkeypatch.setenv('KG_MILVUS_DIM', '1536')
    assert _resolve_milvus_dim(Opaque()) == 1536


def test_remote_encoder_without_embedding_dimension_is_not_guessed(monkeypatch):
    monkeypatch.delenv('KG_EMBEDDING_DIMENSION', raising=False)
    monkeypatch.delenv('KG_MILVUS_DIM', raising=False)

    encoder = RemoteEncoder()

    assert encoder.dimension is None
    with pytest.raises(embeddings.EmbeddingDimensionConfigurationError):
        _resolve_milvus_dim(encoder)


@pytest.mark.parametrize('declared', ['0', '-1', 'not-a-number'])
def test_milvus_dimension_must_be_a_positive_integer(monkeypatch, declared):
    monkeypatch.setenv('KG_MILVUS_DIM', declared)
    with pytest.raises(embeddings.EmbeddingDimensionConfigurationError, match='正整数'):
        _resolve_milvus_dim(HashingEncoder())


# --------------------------------------------------------------------------- 表结构不一致

class FakeMilvusClient:
    """够用的 MilvusClient 替身：只实现 MilvusStore 用到的那几个方法。"""

    def __init__(self, dim=1024):
        self.collections = {'knowledge_records': dim} if dim else {}
        self.dropped = []
        self.created = []
        self.upserted = []

    def has_collection(self, name):
        return name in self.collections

    def describe_collection(self, name):
        return {'fields': [{'name': 'embedding', 'params': {'dim': self.collections[name]}}]}

    def drop_collection(self, name):
        self.dropped.append(name)
        self.collections.pop(name, None)

    def create_collection(self, name, schema=None, **kwargs):
        dim = None
        for field in getattr(schema, 'fields', []) or []:
            if getattr(field, 'name', '') == 'embedding':
                dim = (getattr(field, 'params', {}) or {}).get('dim')
        self.collections[name] = dim
        self.created.append((name, dim))

    def create_index(self, *args, **kwargs):
        pass

    def load_collection(self, *args, **kwargs):
        pass

    def upsert(self, name, rows, **kwargs):
        self.upserted.append(rows)

    def delete(self, name, filter=None, **kwargs):
        return {'delete_count': 0}

    def flush(self, *args, **kwargs):
        pass

    def close(self):
        pass


def test_ensure_collection_raises_an_actionable_error_on_dimension_mismatch():
    """读到旧表维度与当前维度不一致：报错必须包含"差多少"和"怎么修"。"""
    store = MilvusStore(dim=256, encoder_identity=HashingEncoder.identity)
    store._client = _schema_client(1024)
    with pytest.raises(VectorDimensionMismatch) as excinfo:
        store.ensure_collection(project_id='project', index_version=2, catalog=default_catalog())
    message = str(excinfo.value)
    assert excinfo.value.collection == COLLECTION
    assert excinfo.value.collection_dim == 1024
    assert excinfo.value.expected_dim == 256
    assert excinfo.value.encoder_identity == HashingEncoder.identity
    assert '1024' in message and '256' in message
    assert '请统一模型配置' in message
    assert store.available
    for method in (store._client.create_collection, store._client.create_index,
                   store._client.drop_collection, store._client.upsert, store._client.delete):
        method.assert_not_called()


def test_explicit_collection_creation_reuses_compatible_project_generations_without_drop():
    catalog = default_catalog()
    store = MilvusStore(dim=256, encoder_identity=HashingEncoder.identity)
    client = store._client = _schema_client(256, exists=False)
    assert store.ensure_collection(project_id='project-a', index_version=1, catalog=catalog) is store
    assert client.create_collection.call_count == client.create_index.call_count == 1
    assert client.create_collection.call_args.args == (COLLECTION,)
    schema = client.create_collection.call_args.kwargs['schema']
    fields = {field.name: field for field in schema.fields}
    assert len(fields) == len(catalog.column_defs()) == 21
    assert set(fields) == {column.name for column in catalog.column_defs()}
    assert fields['embedding'].params['dim'] == 256
    assert fields['vector_id'].is_primary and not fields['vector_id'].auto_id
    assert fields['project_id'].is_partition_key
    assert schema.enable_dynamic_field is False
    assert len(schema.functions) == 1
    function = schema.functions[0].to_dict()
    assert function['name'] == 'text_bm25' and int(function['type']) == 1
    assert function['input_field_names'] == ['text'] and function['output_field_names'] == ['sparse']
    index_definitions = {item.index_name: item.to_dict()
                         for item in client.create_index.call_args.args[1]}
    assert index_definitions['dense'] == {'field_name': 'embedding', 'index_name': 'dense',
        'index_type': 'HNSW', 'metric_type': 'COSINE', 'M': 16, 'efConstruction': 256}
    assert index_definitions['bm25'] == {'field_name': 'sparse', 'index_name': 'bm25',
        'index_type': 'SPARSE_INVERTED_INDEX', 'metric_type': 'BM25'}
    assert set(index_definitions) == {'dense', 'bm25', *(
        'filter_' + column.name for column in catalog.column_defs() if column.index)}
    for column in catalog.column_defs():
        if column.index and column.name not in {'embedding', 'sparse'}:
            assert index_definitions['filter_' + column.name] == {
                'field_name': column.name, 'index_name': 'filter_' + column.name, 'index_type': 'INVERTED'}

    identities, snapshots = [], {}
    for project, generation in [('project-a', 1), ('project-a', 2), ('project-b', 1)]:
        assert store.ensure_collection(project_id=project, index_version=generation, catalog=catalog) is store
        row = build_projection(ProjectionInput({'project_id': project, 'id': 'record',
            'version_id': 'v1', 'kind': 'chunk', 'text': f'退款 {project} {generation}'}),
            catalog, index_version=generation)
        assert row is not None
        row['embedding'] = [1.] + [0.] * 255
        assert store.upsert_rows([row], project_id=project, index_version=generation, catalog=catalog) == 1
        for vector_id, original in snapshots.items():
            assert {key: client.rows[vector_id][key] for key in original} == original
        snapshots[row['vector_id']] = {key: row[key] for key in (
            'project_id', 'index_version', 'record_id', 'version_id', 'text')}
        identities.append((project, generation, row['vector_id']))
    assert len(client.rows) == len({vector for _, _, vector in identities}) == 3
    assert {(row['project_id'], row['index_version']) for row in client.rows.values()} == {
        ('project-a', 1), ('project-a', 2), ('project-b', 1)}
    assert client.create_collection.call_count == client.create_index.call_count == 1
    assert all(call.args[0] == COLLECTION for call in client.upsert.call_args_list)
    client.drop_collection.assert_not_called()
    client.delete.assert_not_called()


def test_upsert_dimension_mismatch_raises_the_same_error_type():
    """写入时才发现维度不对，也必须抛同一类异常（调用方只需处理一种）。"""
    catalog = default_catalog()
    store = MilvusStore(dim=1024, encoder_identity=HashingEncoder.identity)
    store._client = _schema_client(1024)
    row = build_projection(ProjectionInput({'version_id': 'v1', 'project_id': 'p',
        'id': 'r1', 'kind': 'chunk', 'text': '商户退款'}), catalog, index_version=2)
    assert row is not None
    row['embedding'] = [1.] + [0.] * 255
    with pytest.raises(VectorDimensionMismatch) as excinfo:
        store.upsert_rows([row], project_id='p', index_version=2, catalog=catalog)
    assert '256' in str(excinfo.value) and '1024' in str(excinfo.value)
    assert excinfo.value.collection == COLLECTION
    assert (excinfo.value.collection_dim, excinfo.value.expected_dim) == (1024, 256)
    assert excinfo.value.encoder_identity == HashingEncoder.identity
    assert store._client.mock_calls == [], 'invalid vectors must fail before native preparation or writes'


# --------------------------------------------------------------------------- 重建接口自愈

def test_rebuild_endpoint_uses_generation_without_dropping_shared_collection(
        tmp_path, monkeypatch):
    """重建任务委托投影协调器创建新代，不删除共享旧 collection。"""
    from fastapi.testclient import TestClient

    # create_app 不应连接本机真实 Milvus；本用例在装配后注入受控替身。
    monkeypatch.setenv('KG_VECTOR_BACKEND', '')
    monkeypatch.setenv('KG_MILVUS_DIM', '256')
    store = MilvusStore(dim=256, encoder_identity=HashingEncoder.identity)
    store._client = FakeMilvusClient(dim=1024)          # 旧表：1024 维
    store.ping = lambda: True
    app = create_app(tmp_path / 'dim.sqlite', HashingEncoder())
    app.state.service.milvus_store = store              # 测试里直接注入替身
    rebuild_calls = []
    class Sync:
        def rebuild(self, project_id, target):
            rebuild_calls.append((project_id, target))
            return {'destination': 'generation-v2', 'indexed': 0, 'index_version': 2}
    app.state.service.projection_sync = Sync()

    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': '维度'}).json()['id']
        submitted = client.post(f'/api/projects/{project}/indexes/rebuild')
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()['id']
        job = {}
        for _ in range(200):
            job = client.get('/api/jobs/' + job_id).json()
            if job['status'] in ('completed', 'failed'):
                break
            time.sleep(0.02)
        assert job['status'] == 'completed', job
        assert rebuild_calls == [(project, 'milvus')]
        assert store._client.dropped == [], '任务不得直接删除共享 collection'
        assert store._client.collections['knowledge_records'] == 1024
        assert job['result']['recreated'] is False
        assert job['result']['destination'] == 'generation-v2'
        assert job['result']['index_version'] == 2
        assert job['result']['indexed'] == job['result']['total'] == 0
        assert job['result']['dim'] == 256
        assert job['result']['embedding_model'] == HashingEncoder.identity


def test_rebuild_endpoint_uses_generation_when_dimension_matches(
        tmp_path, monkeypatch):
    """维度一致时也通过隔离代和别名切换完成受控重建。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv('KG_VECTOR_BACKEND', '')
    monkeypatch.setenv('KG_MILVUS_DIM', '256')
    store = MilvusStore(dim=256, encoder_identity=HashingEncoder.identity)
    store._client = FakeMilvusClient(dim=256)
    store.ping = lambda: True
    app = create_app(tmp_path / 'dim-ok.sqlite', HashingEncoder())
    app.state.service.milvus_store = store
    rebuild_calls = []
    class Sync:
        def rebuild(self, project_id, target):
            rebuild_calls.append((project_id, target))
            return {'destination': 'generation-v2', 'indexed': 0, 'index_version': 2}
    app.state.service.projection_sync = Sync()

    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': '维度一致'}).json()['id']
        submitted = client.post(f'/api/projects/{project}/indexes/rebuild')
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()['id']
        job = {}
        for _ in range(200):
            job = client.get('/api/jobs/' + job_id).json()
            if job['status'] in ('completed', 'failed'):
                break
            time.sleep(0.02)
        assert job['status'] == 'completed', job
        assert rebuild_calls == [(project, 'milvus')]
        assert store._client.dropped == []
        assert store._client.collections['knowledge_records'] == 256
        assert job['result']['recreated'] is False
        assert job['result']['destination'] == 'generation-v2'
        assert job['result']['index_version'] == 2
        assert job['result']['indexed'] == job['result']['total'] == 0
        assert job['result']['dim'] == 256
        assert job['result']['embedding_model'] == HashingEncoder.identity
