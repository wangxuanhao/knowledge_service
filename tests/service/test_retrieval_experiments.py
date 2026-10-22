"""Durable ablation runs execute distinct requests and protect frozen results."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest


def system():
    from knowledge_service.services.retrieval_experiments import RetrievalExperiments
    class Repository:
        def __init__(self):
            self.items = {}
        def save_artifact(self, kind, item):
            self.items[(kind, item['id'])] = deepcopy(item)
            return item
        def get_artifact(self, kind, id):
            return deepcopy(self.items[(kind, id)])
        def list_artifacts(self, kind, project_id=None):
            return [deepcopy(value) for (type, _), value in self.items.items()
                    if type == kind and value['project_id'] == project_id]
        def get_project(self, project):
            return {'id': project}
        def read_snapshot(self):
            return nullcontext()
    calls = []
    def answer(project, request):
        calls.append(deepcopy(request))
        return {'answer': '证据原文', 'mode': 'evidence_only',
                'evidence': [{'id': 'c', 'version_id': 'cv1', 'kind': 'chunk', 'text': '退款'}],
                'hits': [{'id': 'c', 'version_id': 'cv1', 'kind': 'chunk', 'text': '退款'}],
                'reasoning_paths': [], 'active_modes': ['keyword'], 'degraded': False,
                'channels': {'chunk': 1}, 'answer_id': 'answer-real'}
    service = SimpleNamespace(repository=Repository(), encoder=SimpleNamespace(identity='demo', semantic=False), answer=answer)
    manager = RetrievalExperiments(service, authorize=lambda *args, **kwargs: None)
    state = {'watermark': 1, 'ontology_digest': 'fixed'}
    manager._snapshot = lambda *args, **kwargs: deepcopy(state)
    return manager, service, calls, state


def test_variants_execute_actual_omitted_stages_and_fixed_inputs():
    manager, _, calls, _ = system()
    run = manager.create('p', {'name': 'compare', 'questions': [
        {'id': 'q1', 'query': '退款', 'expected_record_ids': ['c'], 'expected_answer': '退款说明'}],
        'variants': ['basic', 'document_only', 'no_graph', 'no_ontology', 'combined'],
        'retrieval_mode': 'keyword', 'k_chunks': 3, 'generate': False}, actor_id='owner')
    manager.execute('p', run['id'], actor_id='owner')
    completed = manager.get('p', run['id'], actor_id='owner')
    assert completed['status'] == 'completed'
    assert len(calls) == len(completed['results']) == 5
    assert len({request['known_at'] for request in calls}) == 1
    assert calls[1]['k_entities'] == calls[1]['k_relations'] == 0
    assert calls[1]['kinds'] == ['chunk'] and calls[1]['graph_context'] is False
    assert calls[2]['graph_context'] is False
    assert calls[3]['ontology_expansion'] is False and calls[3]['ontology_context'] is False
    assert calls[4]['graph_context'] is False and calls[4]['ontology_context'] is False
    assert completed['results'][0]['metrics']['evidence_recall'] == 1
    assert completed['results'][0]['metrics']['answer_exact_match'] is None
    assert completed['summary']['basic']['answer_scored_questions'] == 0


def test_changed_watermark_redacts_old_results_and_blocks_retry_and_export():
    manager, _, _, state = system()
    run = manager.create('p', {'questions': [{'query': 'secret'}], 'variants': ['basic']}, actor_id='owner')
    manager.execute('p', run['id'], actor_id='owner')
    state['watermark'] = 2
    public = manager.get('p', run['id'], actor_id='owner')
    assert public['status'] == 'invalidated'
    assert public['results'] == [] and public['manifest'] is None
    for method in (manager.retry, manager.export):
        with pytest.raises(ValueError, match='变化|失效'):
            method('p', run['id'], actor_id='owner')


def test_other_actor_and_project_cannot_read_run():
    manager, _, _, _ = system()
    run = manager.create('p', {'questions': [{'query': 'refund'}]}, actor_id='owner')
    for project, actor in [('p', 'other'), ('other', 'owner')]:
        with pytest.raises(PermissionError):
            manager.get(project, run['id'], actor_id=actor)


def test_cancel_checkpoint_stops_next_question_and_retry_reuses_manifest():
    manager, _, calls, _ = system()
    run = manager.create('p', {'questions': [{'query': 'one'}, {'query': 'two'}],
                               'variants': ['basic']}, actor_id='owner')
    def progress(*args):
        if calls:
            manager.cancel('p', run['id'], actor_id='owner')
    manager.execute('p', run['id'], actor_id='owner', progress=progress)
    saved = manager.get('p', run['id'], actor_id='owner')
    assert saved['status'] == 'cancelled' and len(calls) == 1
    manifest = deepcopy(saved['manifest'])
    manager.retry('p', run['id'], actor_id='owner')
    manager.execute('p', run['id'], actor_id='owner')
    saved = manager.get('p', run['id'], actor_id='owner')
    assert saved['status'] == 'completed'
    assert saved['manifest'] == manifest
    assert len(calls) == 2


def test_real_execution_failure_does_not_fabricate_result_or_metrics():
    manager, service, _, _ = system()
    def fail(*args, **kwargs):
        raise RuntimeError('Milvus is not ready')
    service.answer = fail
    run = manager.create('p', {'questions': [{'query': '退款'}], 'variants': ['basic']}, actor_id='owner')
    summary = manager.run_job({'run_id': run['id'], 'actor_id': 'owner'}, 'p', lambda *args: None, 'task')
    assert summary['status'] == 'failed' and 'results' not in summary
    result = manager.get('p', run['id'], actor_id='owner')['results'][0]
    assert result['status'] == 'failed' and result['result'] is None and result['metrics'] is None
    assert result['error_category'] == 'milvus_unavailable'


def test_retry_recomputes_counts_and_summary_after_discarding_failed_receipts():
    manager, service, _, _ = system()
    run = manager.create('p', {'questions': [{'query': 'one'}, {'query': 'two'}],
        'variants': ['basic']}, actor_id='owner')
    answer = service.answer
    def partly_fail(project, body):
        if body['query'] == 'two':
            raise RuntimeError('Milvus unavailable')
        return answer(project, body)
    service.answer = partly_fail
    manager.execute('p', run['id'], actor_id='owner')
    retried = manager.retry('p', run['id'], actor_id='owner')
    assert len(retried['results']) == retried['completed_items'] == 1
    assert retried['summary']['basic']['completed_questions'] == 1


def test_pg_experiment_persists_and_invalidates_after_source_revision(tmp_path):
    from knowledge_service.repository import Repository
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.services.service import KnowledgeService
    from knowledge_service.services.retrieval_experiments import RetrievalExperiments
    repo = Repository(tmp_path / 'experiment-persistence.sqlite')
    project = repo.create_project('实验持久化')['id']
    doc = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': '退款依据',
        'valid_from': '2020-01-01', 'metadata': {'applicability': {'platforms': {'mode': 'all'}}}})
    repo.put_record(project, {'id': 'chunk', 'kind': 'chunk', 'source_id': 'doc', 'text': '退款依据',
        'valid_from': '2020-01-01', 'metadata': {**doc['metadata'], 'source_version_id': doc['version_id'], 'start_char': 0, 'end_char': 4}})
    service = KnowledgeService(repo, HashingEncoder())
    manager = RetrievalExperiments(service, authorize=lambda *args, **kwargs: None)
    run = manager.create(project, {'questions': [{'query': '退款依据', 'expected_record_ids': ['chunk']}],
        'retrieval_mode': 'keyword', 'variants': ['basic', 'document_only'],
        'scope': {'valid_at': '2025-01-01'}}, actor_id='owner')
    manager.execute(project, run['id'], actor_id='owner')
    persisted = RetrievalExperiments(service, authorize=lambda *args, **kwargs: None).get(project, run['id'], actor_id='owner')
    assert persisted['status'] == 'completed'
    assert persisted['summary']['basic']['mean_evidence_recall'] == 1.
    repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': '已撤回', 'metadata': {'_deleted': True}}, expected_version=1)
    assert manager.get(project, run['id'], actor_id='owner')['results'] == []
