"""An operation keeps its model even when another request changes the default."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest


def config(model):
    from knowledge_service.core.llm_config import LLMConfig
    return LLMConfig(profile_id=model, name=model, base_url='https://models.example/v1',
                     model=model, api_key='secret-' + model, max_tokens=65536, proxy=None)


def test_runtime_operation_resolves_once_and_restores_context():
    from knowledge_service.core.llm_config import current_llm, bound_llm_config
    from knowledge_service.services.llm_runtime import llm_operation
    first, second = config('first'), config('second')
    manager = SimpleNamespace(resolve=lambda: first)
    owner = SimpleNamespace(llm_models=manager)

    @llm_operation
    def operation(service):
        before = current_llm()
        manager.resolve = lambda: second
        return before, current_llm()

    assert operation(owner) == (first, first)
    assert operation(owner) == (second, second)
    assert bound_llm_config() is None


def test_stream_snapshots_are_isolated_between_yields_and_threads():
    from knowledge_service.core.llm_config import current_llm, bound_llm_config
    from knowledge_service.services.llm_runtime import llm_stream
    first, second = config('first'), config('second')
    manager = SimpleNamespace(resolve=lambda: first)
    owner = SimpleNamespace(llm_models=manager)

    @llm_stream
    def stream(service):
        yield current_llm()
        yield current_llm()

    one = stream(owner)
    assert next(one) == first
    assert bound_llm_config() is None
    manager.resolve = lambda: second
    two = stream(owner)
    assert next(two) == second
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(next, one).result() == first
        pool.submit(one.close).result()
    assert next(two) == second
    two.close()
    assert bound_llm_config() is None


def test_operation_restores_context_on_failure():
    from knowledge_service.core.llm_config import bound_llm_config
    from knowledge_service.services.llm_runtime import llm_operation

    @llm_operation
    def fail(service):
        raise ValueError('failed')

    with pytest.raises(ValueError, match='failed'):
        fail(SimpleNamespace(llm_models=SimpleNamespace(resolve=lambda: config('first'))))
    assert bound_llm_config() is None


def test_pinned_operation_uses_durable_task_identity():
    from knowledge_service.core.llm_config import current_llm
    from knowledge_service.services.llm_runtime import llm_operation
    pinned = []
    manager = SimpleNamespace(pin=lambda key: pinned.append(key) or config('saved'))
    owner = SimpleNamespace(service=SimpleNamespace(llm_models=manager))

    @llm_operation(task_key=lambda self, item, settings, owner: 'document:' + item['id'] + ':' + str(item.get('document_generation', 0)))
    def run(self, item, settings, owner):
        return current_llm().model

    assert run(owner, {'id': 'item1', 'document_generation': 2}, {}, 'worker') == 'saved'
    assert pinned == ['document:item1:2']


def test_non_llm_operations_do_not_resolve_or_decrypt_models(monkeypatch):
    from knowledge_service.core.llm_config import current_llm
    from knowledge_service.services.llm_runtime import llm_operation
    monkeypatch.setenv('KG_LLM_PROXY', 'http://127.0.0.1:7890')

    def unavailable():
        raise RuntimeError('encryption key unavailable')

    @llm_operation(uses_llm=lambda owner: False)
    def operation(owner):
        return current_llm().configured, current_llm().proxy

    assert operation(SimpleNamespace(llm_models=SimpleNamespace(resolve=unavailable))) == (False, 'http://127.0.0.1:7890')


def test_stream_model_resolution_failure_is_a_readable_error_event():
    from knowledge_service.services.llm_runtime import llm_stream

    def unavailable():
        raise RuntimeError('private provider details')

    @llm_stream
    def stream(owner):
        yield 'never reached'

    events = list(stream(SimpleNamespace(llm_models=SimpleNamespace(resolve=unavailable))))
    assert len(events) == 1 and events[0].startswith('event: error')
    assert 'private provider details' not in events[0]
    assert '模型配置' in events[0]
