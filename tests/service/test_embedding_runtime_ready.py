"""Encoder readiness follows verified calls; health never loads model weights."""
from contextlib import contextmanager
import json
import sys
from types import SimpleNamespace

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
