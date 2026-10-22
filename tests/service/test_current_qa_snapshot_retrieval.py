"""Default QA must use current Milvus ranking while freezing authority evidence.

These tests use only in-memory adapters; they never open PostgreSQL, Milvus,
HTTP connections, or an embedding model. Explicit knowledge-time requests keep
their historical contract instead of inheriting the default-QA exception.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services import answer_validity, answers, provenance
from knowledge_service.services.answer_evidence import AnswerEvidence
from knowledge_service.services.service import KnowledgeService


FROZEN_AT = '2026-10-21T08:00:00.000000Z'
HISTORICAL_AT = '2025-01-01T00:00:00.000000Z'


def _harness(monkeypatch):
    row = {'id': 'refund', 'version_id': 'refund-v1', 'version': 1,
           'kind': 'entity', 'text': '退款依据', 'type': 'urn:test:Rule',
           'valid_from': '2020-01-01T00:00:00.000000Z', 'valid_until': None,
           'recorded_at': '2020-01-01T00:00:00.000000Z', 'superseded_at': None,
           'metadata': {'applicability': {'platforms': {'mode': 'all', 'values': []}}}}
    state = {'current': True, 'index_version': 2, 'watermark': 9}
    calls = {'native': [], 'hydration': [], 'history': [], 'validity': [], 'frozen': [], 'index': []}
    index = {'index_version': 2, 'watermark': 9, 'dim': 2,
             'embedding_model': 'test-model', 'catalog': default_catalog().to_dict()}

    def native_search(project, query, **options):
        calls['native'].append((project, query, deepcopy(options)))
        return [{'record_id': row['id'],
                 'version_id': state.get('native_version', row['version_id']), 'score': 0.9}]

    def selected_index(*_args):
        selected = {**index, 'index_version': state['index_version'], 'watermark': state['watermark']}
        calls['index'].append(deepcopy(selected))
        return selected

    def clock():
        callback = state.pop('during_freeze', None)
        if callback is not None:
            callback()
        return FROZEN_AT

    def by_ids(_reads, project, scope, ids, *, kinds=None):
        calls['hydration'].append(deepcopy(scope))
        return [dict(row)] if row['id'] in ids else []

    def historical_keyword(_reads, project, scope, query, *, kinds, limit):
        calls['history'].append(deepcopy(scope))
        return [{**row, 'score': 1.0}] if 'entity' in kinds else []

    def qualify(_service, _project, context, request):
        calls['validity'].append(deepcopy(request))
        context.update(as_of=request['valid_at'], valid_at=request['valid_at'],
                       known_at=request['known_at'])
        context['evidence_rows'] = [{**item, 'answer_eligible': True,
                                    'validity_status': 'effective', 'usage': 'effective_basis'}
                                   for item in context['evidence_rows']]
        return context

    class FrozenProvenance:
        def __init__(self, _repository):
            pass

        def begin_retrieval(self, _project, _request):
            return 'retrieval-run'

        def complete_retrieval(self, _project, _run, context, evidence):
            calls['frozen'].append((deepcopy(context), deepcopy(evidence)))
            return 'answer-id'

        def complete_answer(self, *_args, **_kwargs):
            pass

        def fail_activity(self, *_args, **_kwargs):
            pass

    repository = SimpleNamespace(projections=SimpleNamespace(
        search_index=selected_index,
        search_index_is_current=lambda _project, generation, _schema, watermark:
            state['current'] and (generation, watermark) == (state['index_version'], state['watermark'])))
    encoder = SimpleNamespace(identity='test-model', semantic=True,
                              encode=lambda texts: [[1.0, 0.0] for _text in texts])
    service = KnowledgeService(repository, encoder,
                               SimpleNamespace(available=True, search=native_search))
    monkeypatch.setattr(answer_validity, 'utc_now', clock)
    monkeypatch.setattr(answer_validity, 'apply_validity', qualify)
    monkeypatch.setattr(ScopedReads, 'by_ids', by_ids)
    monkeypatch.setattr(ScopedReads, 'ranked_keyword', historical_keyword)
    monkeypatch.setattr(service, 'annotate_structure_pending', lambda *_args: None)
    monkeypatch.setattr('knowledge_service.services.retrieval_context.enrich_source_context',
                        lambda *_args: None)
    monkeypatch.setattr(provenance, 'ProvenanceService', FrozenProvenance)
    monkeypatch.setattr(answers, 'ProvenanceService', FrozenProvenance)
    monkeypatch.setattr(AnswerEvidence, 'summaries',
                        lambda _self, _project, _answer, evidence: list(evidence))
    return service, calls, state


def _request(mode='hybrid', **extra):
    return {'query': '退款', 'retrieval_mode': mode, 'ontology_context': False,
            'graph_context': False, 'include_references': False, 'generate': False,
            'k_entities': 1, 'k_chunks': 0, 'k_relations': 0, **extra}


@pytest.mark.parametrize('entrypoint', ['json', 'sse'])
@pytest.mark.parametrize('mode', ['keyword', 'hybrid'])
def test_default_qa_uses_current_milvus_in_both_delivery_paths(monkeypatch, entrypoint, mode):
    service, calls, _state = _harness(monkeypatch)
    if entrypoint == 'json':
        result = service.answer('project', _request(mode))
        assert result['_backend'] == 'milvus'
        assert [item['version_id'] for item in result['evidence']] == ['refund-v1']
    else:
        events = list(answers.stream_events(service, 'project', _request(mode)))
        assert any(item.startswith('event: evidence\n') for item in events), events
        assert any(item.startswith('event: done\n') for item in events), events
        assert not any(item.startswith('event: error\n') for item in events), events
    assert len(calls['native']) == 1
    assert calls['native'][0][2]['retrieval_mode'] == mode
    assert calls['native'][0][2]['scope'].get('known_at') is None
    assert not calls['history']
    # Only native ranking ignores the implicit knowledge time. Authority
    # hydration, qualification, and immutable evidence all retain its value.
    assert calls['hydration'][0]['known_at'] == FROZEN_AT
    assert calls['validity'][0]['known_at'] == FROZEN_AT
    assert calls['frozen'][0][0]['known_at'] == FROZEN_AT
    assert calls['frozen'][0][1][0]['version_id'] == 'refund-v1'


def test_default_semantic_question_keeps_frozen_knowledge_time(monkeypatch):
    service, calls, _state = _harness(monkeypatch)
    context = answers.question_context(service, 'project', _request('semantic'))
    assert context['_backend'] == 'milvus'
    assert calls['native'][0][2]['retrieval_mode'] == 'semantic'
    assert calls['hydration'][0]['known_at'] == FROZEN_AT
    assert context['known_at'] == FROZEN_AT


def test_explicit_historical_keyword_qa_does_not_use_current_index(monkeypatch):
    service, calls, _state = _harness(monkeypatch)
    context = answers.question_context(service, 'project',
                                       _request('keyword', known_at=HISTORICAL_AT))
    assert context['_backend'] == 'postgres_history'
    assert not calls['native']
    assert calls['history'][0]['known_at'] == HISTORICAL_AT
    assert context['known_at'] == HISTORICAL_AT


@pytest.mark.parametrize('mode', ['semantic', 'hybrid'])
def test_explicit_historical_vector_qa_keeps_its_clear_rejection(monkeypatch, mode):
    service, calls, _state = _harness(monkeypatch)
    with pytest.raises(ValueError, match='历史'):
        answers.question_context(service, 'project', _request(mode, known_at=HISTORICAL_AT))
    assert not calls['native']
    assert not calls['history']


def test_default_qa_still_rejects_index_switch_before_return(monkeypatch):
    service, calls, state = _harness(monkeypatch)
    state['current'] = False
    with pytest.raises(RuntimeError, match='索引'):
        answers.question_context(service, 'project', _request())
    assert len(calls['native']) == 1
    assert not calls['frozen']


@pytest.mark.parametrize('changed', ['index_version', 'watermark'])
def test_default_qa_rejects_generation_or_watermark_change_during_freeze(monkeypatch, changed):
    service, calls, state = _harness(monkeypatch)
    state['during_freeze'] = lambda: state.update({changed: state[changed] + 1})
    with pytest.raises(RuntimeError, match='索引'):
        service.answer('project', _request('keyword'))
    assert len(calls['index']) >= 2
    assert not calls['native']
    assert not calls['frozen']


def test_default_qa_does_not_publish_false_empty_result_after_new_version(monkeypatch):
    service, calls, state = _harness(monkeypatch)

    def replace_version():
        state.update(watermark=10, native_version='refund-v2')

    state['during_freeze'] = replace_version
    with pytest.raises(RuntimeError, match='索引'):
        service.answer('project', _request('keyword'))
    assert not calls['native']
    assert not calls['frozen']
