"""Embedding 与 Milvus 必须在连接前约定同一个、可证明的向量维度。"""
import json
import sys
import time
import types
from pathlib import Path

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
    MilvusStore,
    VectorDimensionMismatch,
)

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / 'data' / 'model'


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
    store = MilvusStore(dim=256, encoder_identity='demo-character-bigrams-sha256-256-v1')
    store._client = FakeMilvusClient(dim=1024)
    with pytest.raises(VectorDimensionMismatch) as excinfo:
        store.ensure_collection()
    message = str(excinfo.value)
    assert excinfo.value.collection_dim == 1024
    assert excinfo.value.expected_dim == 256
    assert '1024' in message and '256' in message
    assert 'indexes/rebuild' in message
    assert 'demo-character-bigrams-sha256-256-v1' in message
    assert store._client.dropped == []


def test_ensure_collection_can_recreate_the_table_at_the_new_dimension():
    store = MilvusStore(dim=256)
    store._client = FakeMilvusClient(dim=1024)
    store.ensure_collection(recreate=True)
    assert store._client.dropped == ['knowledge_records']
    assert store._client.collections['knowledge_records'] == 256


def test_upsert_dimension_mismatch_raises_the_same_error_type():
    """写入时才发现维度不对，也必须抛同一类异常（调用方只需处理一种）。"""
    store = MilvusStore(dim=1024)
    store._client = FakeMilvusClient(dim=1024)
    with pytest.raises(VectorDimensionMismatch) as excinfo:
        store.upsert([{'version_id': 'v1', 'project_id': 'p', 'id': 'r1',
                       'text': '商户退款', 'embedding': [0.0] * 256}])
    assert '256' in str(excinfo.value) and '1024' in str(excinfo.value)


# --------------------------------------------------------------------------- 重建接口自愈

def test_rebuild_endpoint_recreates_the_vector_table_when_dimension_changes(tmp_path):
    """端到端：表是旧的 1024 维、编码器是 256 维 → 重建接口删表重建并回报 recreated。

    这是本次反馈的正解：维度是建表时固定的，物理上改不了，所以"换了模型"
    只能删表重建；Milvus 里全是派生数据，重建后全量重编码即可补回。
    """
    from fastapi.testclient import TestClient

    store = MilvusStore(dim=256, encoder_identity=HashingEncoder.identity)
    store._client = FakeMilvusClient(dim=1024)          # 旧表：1024 维
    app = create_app(tmp_path / 'dim.sqlite', HashingEncoder())
    app.state.service.milvus_store = store              # 测试里直接注入替身

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
        assert store._client.dropped == ['knowledge_records'], '必须先删掉旧维度的表'
        assert store._client.collections['knowledge_records'] == 256, '必须按当前编码器维度重建'
        assert job['result']['recreated'] is True
        assert job['result']['dim'] == 256
        assert job['result']['embedding_model'] == HashingEncoder.identity


def test_rebuild_endpoint_does_not_touch_the_table_when_dimension_matches(tmp_path):
    """维度一致时不做任何删表动作：recreated=False，重建就只是重灌。"""
    from fastapi.testclient import TestClient

    store = MilvusStore(dim=256, encoder_identity=HashingEncoder.identity)
    store._client = FakeMilvusClient(dim=256)
    app = create_app(tmp_path / 'dim-ok.sqlite', HashingEncoder())
    app.state.service.milvus_store = store

    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': '维度一致'}).json()['id']
        submitted = client.post(f'/api/projects/{project}/indexes/rebuild')
        job_id = submitted.json()['id']
        job = {}
        for _ in range(200):
            job = client.get('/api/jobs/' + job_id).json()
            if job['status'] in ('completed', 'failed'):
                break
            time.sleep(0.02)
        assert job['status'] == 'completed', job
        assert store._client.dropped == []
        assert job['result']['recreated'] is False
