"""A real experiment variant and QA freezer must share one frozen index/time."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.services import answer_validity, retrieval_experiments
from knowledge_service.services.answer_validity import freeze_qa_request
from knowledge_service.services.retrieval_experiments import RetrievalExperiments, digest, variant_request


def _manager(monkeypatch):
    events, artifacts = [], {}
    index = {'index_version': 2, 'watermark': 9, 'dim': 2, 'embedding_model': 'test',
             'catalog': default_catalog().to_dict()}
    def clock():
        events.append('clock')
        return f'2026-10-21T08:00:{len(events):02d}.000000Z'
    def selected_index(*_args):
        events.append('index')
        return dict(index)
    repo = SimpleNamespace(projections=SimpleNamespace(search_index=selected_index),
        save_artifact=lambda kind, item: artifacts.update({(kind, item['id']): deepcopy(item)}),
        get_artifact=lambda kind, id: deepcopy(artifacts[(kind, id)]),
        get_project=lambda project: {'id': project})
    service = SimpleNamespace(repository=repo, encoder=SimpleNamespace(identity='test', semantic=True))
    manager = RetrievalExperiments(service, authorize=lambda *_a, **_k: None)
    monkeypatch.setattr(manager, '_snapshot', lambda _p, _s, *, versions=True:
        {'watermark': 9, **({'record_versions': []} if versions else {})})
    monkeypatch.setattr(manager, '_runtime', lambda: {'configured': False})
    monkeypatch.setattr(answer_validity, 'utc_now', clock)
    monkeypatch.setattr(retrieval_experiments, 'utc_now', clock)
    return manager, service, events


def test_experiment_pins_before_time_and_real_variant_freeze_preserves_it(monkeypatch):
    manager, service, events = _manager(monkeypatch)
    run = manager.create('p', {'questions': [{'query': '退款'}],
                             'variants': ['basic', 'combined'], 'retrieval_mode': 'hybrid'}, actor_id='owner')
    assert events[0] == 'index', 'the manifest time must follow its ready generation pin'
    manifest = run['manifest']
    actual_times = []
    for variant in manifest['variants']:
        body, _ = variant_request(manifest, manifest['questions'][0], variant)
        actual = freeze_qa_request(service, 'p', body)
        actual_times.append(actual['known_at'])
        assert actual['_qa_index_snapshot'] == {'project_id': 'p', 'index_version': 2, 'watermark': 9}
    assert actual_times == [manifest['scope']['known_at']] * len(manifest['variants'])


def test_legacy_implicit_manifest_without_index_pin_is_invalidated(monkeypatch):
    manager, _service, _events = _manager(monkeypatch)
    run = manager.create('p', {'questions': [{'query': '退款'}], 'variants': ['basic']}, actor_id='owner')
    run['manifest'].pop('qa_index_snapshot', None)
    run['manifest_digest'] = digest(run['manifest'])
    assert manager._valid(run) is False
    with pytest.raises(ValueError, match='重新|失效|固定'):
        variant_request(run['manifest'], run['manifest']['questions'][0], 'basic')


def test_explicit_history_experiment_needs_no_current_index(monkeypatch):
    manager, service, events = _manager(monkeypatch)
    run = manager.create('p', {'questions': [{'query': '退款'}], 'variants': ['basic'],
        'retrieval_mode': 'keyword', 'scope': {'known_at': '2025-01-01T00:00:00Z'}}, actor_id='owner')
    body, _ = variant_request(run['manifest'], run['manifest']['questions'][0], 'basic')
    actual = freeze_qa_request(service, 'p', body)
    assert actual['known_at'] == '2025-01-01T00:00:00.000000Z'
    assert 'index' not in events
