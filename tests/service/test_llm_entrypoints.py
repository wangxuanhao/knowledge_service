"""Platform selections reach ordinary extraction and both answer entry points."""
from contextlib import contextmanager
import json

import pytest

from test_rule_document_oag import qa, source  # noqa: F401


@pytest.fixture
def selections(qa):
    service, repo, project = qa
    manager = service.llm_models
    first = manager.create(name='first', base_url='https://first.example/v1', model='first', api_key='first-key')
    second = manager.create(name='second', base_url='https://second.example/v1', model='second', api_key='second-key')
    manager.activate(first['id'])
    return service, repo, project, manager, first, second


def test_json_and_stream_use_platform_selection_and_frozen_identity(selections, monkeypatch):
    from knowledge_service.services import answers, service as facade
    from knowledge_service.core.llm_config import bound_llm_config
    service, repo, project, manager, first, second = selections
    source(repo, project, 'original')
    calls = []
    reply = '文档介绍退款条款 [U1]'

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'choices': [{'message': {'content': reply}}]}

        def iter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': reply}}]})
            yield 'data: [DONE]'

    class Client:
        def post(self, url, **kwargs):
            calls.append((url, kwargs['headers']['Authorization'], kwargs['json']['model']))
            manager.activate(second['id'])
            return Response()

        @contextmanager
        def stream(self, method, url, **kwargs):
            yield self.post(url, **kwargs)

    @contextmanager
    def provider(*args, **kwargs):
        yield Client()

    monkeypatch.setattr(answers, 'external_client', provider)
    monkeypatch.setattr(facade, 'external_client', provider)
    request = {'query': '退款条款是什么', 'generate': True, 'retrieval_mode': 'keyword', 'hops': 0}
    result = service.answer(project, request)
    assert result['generation']['llm']['profile_id'] == first['id']
    manager.activate(first['id'])
    stream = answers.stream_events(service, project, request)
    assert next(stream).startswith('event: evidence')
    assert bound_llm_config() is None
    manager.activate(second['id'])
    done = next(json.loads(item.split('\ndata: ', 1)[1]) for item in stream if item.startswith('event: done'))
    stream.close()
    assert done['generation']['llm']['profile_id'] == first['id']
    assert calls == [('https://first.example/v1/chat/completions', 'Bearer first-key', 'first')] * 2
    assert manager.resolve().model == 'second'
    assert bound_llm_config() is None


def test_extraction_adapter_and_hierarchy_receive_bound_platform_profile(selections, monkeypatch):
    from knowledge_service.core.llm_config import bound_llm
    from knowledge_service.integrations import semantica_adapter
    from knowledge_service.services.hierarchy_suggestion import _llm_settings
    service, repo, project, manager, first, second = selections
    observed = []

    def extract(text, config, exceptions=None):
        observed.append(config)
        manager.activate(second['id'])
        return []

    monkeypatch.setattr(semantica_adapter, '_extract_entities_open', extract)
    with bound_llm(manager.resolve()):
        assert semantica_adapter.SemanticaExtractor().discover('设备说明') == []
        assert _llm_settings() == ('https://first.example/v1', 'first-key', 'first')
    assert observed[0]['llm_model'] == 'first'
    assert observed[0]['api_key'] == 'first-key'


def test_evidence_only_answers_and_document_ingest_survive_unavailable_llm_key(qa, monkeypatch):
    from knowledge_service.services.answers import stream_events
    service, repo, project = qa
    source(repo, project, 'original')

    def unavailable():
        raise RuntimeError('key unavailable')

    monkeypatch.setattr(service.llm_models, 'resolve', unavailable)
    request = {'query': '退款条款是什么', 'generate': False, 'retrieval_mode': 'keyword', 'hops': 0}
    assert service.answer(project, request)['mode'] == 'evidence_only'
    assert any(item.startswith('event: done') for item in stream_events(service, project, request))
    document = service.ingest(project, {'title': '资料', 'text': '资料内容', 'extraction_mode': 'documents'})
    assert document['document']['metadata']['status'] == 'ready'


def test_experiment_retry_preserves_profile_when_job_id_changes(tmp_path):
    from knowledge_service.core.llm_config import current_llm
    from knowledge_service.services.llm_models import LLMModels
    from test_retrieval_experiments import system
    manager, service, calls, state, project = system(tmp_path)
    service.llm_models = LLMModels(service.repository)
    first = service.llm_models.create(name='first', base_url='https://provider.example/v1', model='shared', api_key='first-key')
    second = service.llm_models.create(name='second', base_url='https://provider.example/v1', model='shared', api_key='second-key')
    service.llm_models.activate(first['id'])
    original = service.answer
    used_keys = []

    def answer(project, request):
        used_keys.append(current_llm().api_key)
        return original(project, request)

    service.answer = answer
    run = manager.create(project, {'questions': [{'query': 'one'}, {'query': 'two'}],
        'variants': ['basic'], 'generate': True}, actor_id='owner')

    def stop(*args):
        if used_keys:
            manager.cancel(project, run['id'], actor_id='owner')

    manager.execute(project, run['id'], actor_id='owner', progress=stop, task_id='first-job')
    assert manager.get(project, run['id'], actor_id='owner')['status'] == 'cancelled'
    service.llm_models.activate(second['id'])
    manager.retry(project, run['id'], actor_id='owner')
    manager.execute(project, run['id'], actor_id='owner', task_id='second-job')
    assert manager.get(project, run['id'], actor_id='owner')['status'] == 'completed'
    assert used_keys == ['first-key', 'first-key']
