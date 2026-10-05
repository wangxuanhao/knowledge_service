"""向量维度只能有一个来源（编码器），不一致必须给出可执行的补救。

背景（用户反馈"维度的问题，看下改成1024啊"）：
`KG_MILVUS_DIM` 与编码器产出维度错配时，旧行为是把 1024 写死在建表里，换成
256 维的 demo 哈希编码器后，每一次写入都抛「向量维度 256 != 预期 1024」——
既看不出原因（谁产的 256？），也看不出该改哪一边，更没法通过重建接口自愈。
更隐蔽的是：测试套件一旦继承本机 `.env`（`KG_VECTOR_BACKEND=milvus` +
`KG_MILVUS_DIM=1024`），也会复现同一串失败，看起来像"环境数据坏了"。

本文件锁住四条：
  1. 编码器自己声明维度（demo=256、本地模型读 config.json 的 hidden_size）；
  2. 建表维度解析顺序：显式 KG_MILVUS_DIM > 编码器 > 1024 兜底；
  3. 显式配置与编码器矛盾时，启动阶段就告警并写清怎么改；
  4. 表维度与当前维度不一致时：报可执行的错，且 `/indexes/rebuild` 能删表重建自愈。
"""
import json
import time
from pathlib import Path

import pytest

from knowledge_service.api import _resolve_milvus_dim, create_app
from knowledge_service.integrations.embeddings import (
    HashingEncoder,
    LocalEncoder,
    encoder_dimension,
)
from knowledge_service.integrations.milvus_store import (
    MilvusStore,
    VectorDimensionMismatch,
)

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / 'data' / 'model'


# --------------------------------------------------------------------------- 维度唯一的出处

def test_demo_encoder_declares_the_dimension_it_actually_produces():
    """demo 编码器声明 256，就必须真的产出 256 维——声明和实现不能各说各话。"""
    encoder = HashingEncoder()
    assert encoder.dimension == 256
    assert len(encoder.encode(['商户退款'])[0]) == 256


def test_local_encoder_reads_dimension_from_model_config():
    """本地模型维度读 config.json 的 hidden_size（本机 xlm-roberta，1024 维）。"""
    if not (MODEL_DIR / 'config.json').exists():
        pytest.skip('本机没有 data/model，跳过')
    expected = json.loads((MODEL_DIR / 'config.json').read_text(encoding='utf-8')).get('hidden_size')
    assert expected, 'config.json 里应有 hidden_size'
    assert LocalEncoder(MODEL_DIR).dimension == expected
    assert encoder_dimension(LocalEncoder(MODEL_DIR)) == expected


def test_dimension_falls_back_to_the_encoder_when_not_declared(monkeypatch):
    """没设 KG_MILVUS_DIM 就按编码器来——这样任何编码器都不会和表结构错配。"""
    monkeypatch.delenv('KG_MILVUS_DIM', raising=False)
    assert _resolve_milvus_dim(HashingEncoder()) == 256


def test_explicit_env_still_wins(monkeypatch):
    monkeypatch.setenv('KG_MILVUS_DIM', '512')
    assert _resolve_milvus_dim(HashingEncoder()) == 512


def test_mismatch_between_env_and_encoder_is_loud_and_actionable(monkeypatch, caplog):
    """显式配置与编码器矛盾：启动就告警，并写明"把哪一边改成多少"。"""
    monkeypatch.setenv('KG_MILVUS_DIM', '1024')
    with caplog.at_level('WARNING'):
        assert _resolve_milvus_dim(HashingEncoder()) == 1024
    text = caplog.text
    assert 'KG_MILVUS_DIM=1024' in text
    assert '256' in text
    assert '改成 256' in text


def test_unknown_encoder_dimension_uses_declared_or_default(monkeypatch):
    """远端 embedding 调用前问不出维度：用显式声明，没有就 1024 兜底（不瞎猜也不崩）。"""

    class Opaque:
        identity = 'openai:https://example.invalid:v1'

    monkeypatch.delenv('KG_MILVUS_DIM', raising=False)
    assert _resolve_milvus_dim(Opaque()) == 1024
    monkeypatch.setenv('KG_MILVUS_DIM', '1536')
    assert _resolve_milvus_dim(Opaque()) == 1536


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
