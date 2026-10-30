"""Encoder readiness follows verified calls; health never loads model weights."""
from contextlib import contextmanager
import json
import sys
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest

from knowledge_service.integrations import embeddings
from knowledge_service.services.runtime_capabilities import RuntimeCapabilities


class ReadyStore:
    available = True
    dim = 2
    collection_name = 'knowledge_vectors'

    def collection_dim(self):
        return self.dim


class Model:
    dimension = 2
    vectors = [[1.0, 0.0]]
    error = None

    def __init__(self):
        self.calls = 0

    def get_sentence_embedding_dimension(self):
        return self.dimension

    def encode(self, texts, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.vectors


def local_encoder(tmp_path, monkeypatch, model=None, *, initialization_error=None):
    (tmp_path / 'config.json').write_text(json.dumps({'hidden_size': 2}), encoding='utf-8')
    created = []
    model = model or Model()
    def create(path, **kwargs):
        created.append((path, kwargs))
        if initialization_error is not None:
            raise initialization_error
        return model
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=create))
    return embeddings.LocalEncoder(tmp_path), model, created


def capabilities(encoder):
    return RuntimeCapabilities(encoder=encoder, milvus_store=ReadyStore(), milvus_configured=True)


def test_local_success_updates_health_without_health_loading_or_encoding(tmp_path, monkeypatch):
    encoder, model, created = local_encoder(tmp_path, monkeypatch)
    runtime = capabilities(encoder)
    for _ in range(2):
        assert runtime.snapshot()['embedding']['ready'] is False
    assert created == [] and model.calls == 0
    assert encoder.encode(['query']) == [[1.0, 0.0]]
    report = runtime.snapshot()
    assert report['embedding']['ready'] is True
    assert report['capability_matrix']['semantic']['status'] == 'ready'
    assert report['status'] == 'ok'
    assert encoder.runtime_ready is True
    assert len(created) == 1 and model.calls == 1
    runtime.snapshot()
    assert len(created) == 1 and model.calls == 1


@pytest.mark.parametrize('failure', ['initialization', 'reported_dimension', 'vector_dimension'])
def test_failed_local_load_or_dimension_validation_never_marks_ready(tmp_path, monkeypatch, failure):
    model = Model()
    initialization_error = RuntimeError('cannot initialize') if failure == 'initialization' else None
    if failure == 'reported_dimension':
        model.dimension = 3
    if failure == 'vector_dimension':
        model.vectors = [[1.0, 0.0, 0.0]]
    encoder, _, _ = local_encoder(tmp_path, monkeypatch, model, initialization_error=initialization_error)
    with pytest.raises(RuntimeError):
        encoder.encode(['query'])
    assert encoder.runtime_ready is False
    assert capabilities(encoder).snapshot()['embedding']['ready'] is False
    if failure == 'reported_dimension':
        # A loaded object whose declared size failed validation is not ready
        # on a second call merely because initialization already ran.
        with pytest.raises(embeddings.EmbeddingDimensionMismatch):
            encoder.encode(['retry'])
        assert encoder.runtime_ready is False


def test_local_later_failure_clears_readiness_and_success_recovers(tmp_path, monkeypatch):
    encoder, model, _ = local_encoder(tmp_path, monkeypatch)
    encoder.encode(['first'])
    assert encoder.runtime_ready is True

    model.error = RuntimeError('encoding failed')
    with pytest.raises(RuntimeError, match='encoding failed'):
        encoder.encode(['second'])
    assert encoder.runtime_ready is False
    assert capabilities(encoder).snapshot()['status'] == 'degraded'
    model.error = None
    model.vectors = [[1.0, 0.0, 0.0]]
    with pytest.raises(embeddings.EmbeddingDimensionMismatch):
        encoder.encode(['wrong dimension'])
    assert encoder.runtime_ready is False
    model.vectors = [[1.0, 0.0]]
    encoder.encode(['repaired'])
    assert encoder.runtime_ready is True


def test_query_does_not_queue_behind_the_first_model_load(tmp_path, monkeypatch):
    from knowledge_service.services.retrieval import cached_query_vector

    encoder, model, _ = local_encoder(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = model.encode

    def loading(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(model, 'encode', loading)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(encoder.encode, ['first query'])
        assert entered.wait(1)
        second = pool.submit(cached_query_vector, encoder, '未成年')
        try:
            with pytest.raises(RuntimeError, match='初始化'):
                second.result(timeout=.2)
        finally:
            release.set()
        assert first.result(timeout=1) == [[1., 0.]]
    assert cached_query_vector(encoder, '未成年') == [1., 0.]


def test_startup_warmup_runs_once_without_blocking_or_readiness_probes_loading_models(tmp_path, monkeypatch):
    encoder, model, created = local_encoder(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = model.encode

    def loading(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(model, 'encode', loading)
    worker = None
    try:
        worker = encoder.start_warmup()
        assert entered.wait(1)
        assert encoder.start_warmup() is worker
        report = capabilities(encoder).snapshot()
        assert report['embedding']['ready'] is False
        assert report['embedding']['initialization_state'] == 'loading'
        with pytest.raises(RuntimeError, match='初始化'):
            encoder.prepare_query()
        assert len(created) == 1 and model.calls == 0
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=1)
    assert capabilities(encoder).snapshot()['embedding']['ready'] is True
    assert encoder.start_warmup() is worker
    assert len(created) == 1 and model.calls == 1


def test_failed_warmup_reports_the_error_without_repeating_heavy_load_on_each_query(tmp_path, monkeypatch):
    encoder, model, created = local_encoder(tmp_path, monkeypatch,
        initialization_error=RuntimeError('bad model api_key=private-value'))
    worker = encoder.start_warmup()
    worker.join(timeout=1)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='初始化失败'):
            encoder.prepare_query()
    report = capabilities(encoder).snapshot()
    assert report['embedding']['ready'] is False
    assert report['embedding']['initialization_state'] == 'failed'
    assert 'private-value' not in str(report)
    assert len(created) == 1


def test_app_starts_preloading_before_accepting_interactive_requests(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app

    encoder = embeddings.HashingEncoder()
    starts = []
    encoder.start_warmup = lambda: starts.append('started')
    app = create_app(tmp_path / 'warmup-app', encoder=encoder)
    assert starts == []
    with TestClient(app) as client:
        assert starts == ['started']
        assert client.get('/api/health').status_code == 200
        assert starts == ['started']


def test_warming_queries_fail_promptly_through_json_and_sse_while_keyword_stays_available(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from test_rule_document_oag import _ready_keyword_index

    class WarmingEncoder(embeddings.HashingEncoder):
        semantic = True

        def prepare_query(self):
            raise RuntimeError('本地语义模型正在初始化，请稍后重试。')

        def encode(self, texts):
            raise AssertionError('interactive queries must not wait for model inference')

    app = create_app(tmp_path / 'warming-requests', encoder=WarmingEncoder())
    _ready_keyword_index(app.state.service, monkeypatch)
    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'warming'}).json()['id']
        base = f'/api/projects/{project}'
        for mode in ['semantic', 'hybrid']:
            response = client.post(base + '/search', json={'query': '未成年', 'retrieval_mode': mode})
            assert response.status_code == 503
            assert '初始化' in response.json()['detail']
        response = client.post(base + '/qa/stream', json={'query': '未成年', 'retrieval_mode': 'hybrid'})
        assert response.status_code == 200
        assert 'event: error' in response.text and '初始化' in response.text
        assert client.post(base + '/search', json={'query': '未成年', 'retrieval_mode': 'keyword'}).status_code == 200


def remote_encoder(monkeypatch):
    monkeypatch.setenv('KG_EMBEDDING_BASE_URL', 'https://embedding.invalid/v1')
    monkeypatch.setenv('KG_EMBEDDING_MODEL', 'test-model')
    monkeypatch.setenv('KG_EMBEDDING_API_KEY', 'test-only-key')
    monkeypatch.setenv('KG_EMBEDDING_DIMENSION', '2')
    response = {'data': [{'index': 0, 'embedding': [1.0, 0.0]}]}
    state = {'response': response, 'error': None, 'calls': 0}
    class Client:
        def post(self, *args, **kwargs):
            state['calls'] += 1
            if state['error'] is not None:
                raise state['error']
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: state['response'])
    @contextmanager
    def external_client(*args, **kwargs):
        yield Client()
    monkeypatch.setattr(embeddings, 'external_client', external_client)
    return embeddings.RemoteEncoder(), state


def test_remote_success_and_failure_update_ready_without_health_requests(monkeypatch):
    encoder, state = remote_encoder(monkeypatch)
    runtime = capabilities(encoder)
    assert runtime.snapshot()['embedding']['ready'] is False
    assert state['calls'] == 0
    encoder.encode(['first'])
    assert encoder.runtime_ready is True
    assert runtime.snapshot()['status'] == 'ok'
    assert state['calls'] == 1
    state['error'] = httpx.ConnectError('unavailable')
    with pytest.raises(RuntimeError):
        encoder.encode(['second'])
    assert encoder.runtime_ready is False
    assert runtime.snapshot()['embedding']['ready'] is False
    assert state['calls'] == 2


def test_remote_wrong_dimension_cannot_leave_a_previous_success_ready(monkeypatch):
    encoder, state = remote_encoder(monkeypatch)
    encoder.encode(['first'])
    state['response'] = {'data': [{'index': 0, 'embedding': [1.0, 0.0, 0.0]}]}
    with pytest.raises(embeddings.EmbeddingDimensionMismatch):
        encoder.encode(['wrong dimension'])
    assert encoder.runtime_ready is False
