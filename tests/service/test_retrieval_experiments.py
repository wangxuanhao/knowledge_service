"""Durable ablation runs execute distinct requests and protect frozen results."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_retrieval import activate_index


def system(tmp_path):
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.repository import Repository
    from knowledge_service.services.retrieval_experiments import RetrievalExperiments
    repository = Repository(tmp_path / 'experiment-orchestration.sqlite')
    project = repository.create_project('experiment-orchestration')['id']
    encoder = HashingEncoder()
    activate_index(repository, project, encoder)
    calls = []
    def answer(project, request):
        calls.append(deepcopy(request))
        return {'answer': '证据原文', 'mode': 'evidence_only',
                'evidence': [{'id': 'c', 'version_id': 'cv1', 'kind': 'chunk', 'text': '退款'}],
                'hits': [{'id': 'c', 'version_id': 'cv1', 'kind': 'chunk', 'text': '退款'}],
                'reasoning_paths': [], 'active_modes': ['keyword'], 'degraded': False,
                'channels': {'chunk': 1}, 'answer_id': 'answer-real'}
    service = SimpleNamespace(repository=repository, encoder=encoder, answer=answer)
    manager = RetrievalExperiments(service, authorize=lambda *args, **kwargs: None)
    state = {'watermark': repository.projections.project_watermark(project), 'ontology_digest': 'fixed'}
    manager._snapshot = lambda *args, **kwargs: deepcopy(state)
    return manager, service, calls, state, project


def test_variants_execute_actual_omitted_stages_and_fixed_inputs(tmp_path):
    manager, service, calls, _, project = system(tmp_path)
    run = manager.create(project, {'name': 'compare', 'questions': [
        {'id': 'q1', 'query': '退款', 'expected_record_ids': ['c'], 'expected_answer': '退款说明'}],
        'variants': ['basic', 'document_only', 'no_graph', 'no_ontology', 'combined'],
        'retrieval_mode': 'keyword', 'k_chunks': 3, 'generate': False}, actor_id='owner')
    manager.execute(project, run['id'], actor_id='owner')
    completed = manager.get(project, run['id'], actor_id='owner')
    assert completed['status'] == 'completed'
    assert len(calls) == len(completed['results']) == 5
    assert len({request['known_at'] for request in calls}) == 1
    index = service.repository.projections.search_index(project, 3)
    pin = {'project_id': project, 'index_version': index['index_version'], 'watermark': index['watermark']}
    assert completed['manifest']['qa_index_snapshot'] == pin
    assert all(request['_qa_index_snapshot'] == pin for request in calls)
    assert calls[1]['k_entities'] == calls[1]['k_relations'] == 0
    assert calls[1]['kinds'] == ['chunk'] and calls[1]['graph_context'] is False
    assert calls[2]['graph_context'] is False
    assert calls[3]['ontology_expansion'] is False and calls[3]['ontology_context'] is False
    assert calls[4]['graph_context'] is False and calls[4]['ontology_context'] is False
    assert completed['results'][0]['metrics']['evidence_recall'] == 1
    assert completed['results'][0]['metrics']['answer_exact_match'] is None
    assert completed['summary']['basic']['answer_scored_questions'] == 0


def test_changed_watermark_redacts_old_results_and_blocks_retry_and_export(tmp_path):
    manager, _, _, state, project = system(tmp_path)
    run = manager.create(project, {'questions': [{'query': 'secret'}], 'variants': ['basic']}, actor_id='owner')
    manager.execute(project, run['id'], actor_id='owner')
    state['watermark'] = 2
    public = manager.get(project, run['id'], actor_id='owner')
    assert public['status'] == 'invalidated'
    assert public['results'] == [] and public['manifest'] is None
    for method in (manager.retry, manager.export):
        with pytest.raises(ValueError, match='变化|失效'):
            method(project, run['id'], actor_id='owner')


def test_other_actor_and_project_cannot_read_run(tmp_path):
    manager, service, _, _, project = system(tmp_path)
    run = manager.create(project, {'questions': [{'query': 'refund'}]}, actor_id='owner')
    other = service.repository.create_project('other')['id']
    for project, actor in [(project, 'other'), (other, 'owner')]:
        with pytest.raises(PermissionError):
            manager.get(project, run['id'], actor_id=actor)


def test_cancel_checkpoint_stops_next_question_and_retry_reuses_manifest(tmp_path):
    manager, _, calls, _, project = system(tmp_path)
    run = manager.create(project, {'questions': [{'query': 'one'}, {'query': 'two'}],
                               'variants': ['basic']}, actor_id='owner')
    def progress(*args):
        if calls:
            manager.cancel(project, run['id'], actor_id='owner')
    manager.execute(project, run['id'], actor_id='owner', progress=progress)
    saved = manager.get(project, run['id'], actor_id='owner')
    assert saved['status'] == 'cancelled' and len(calls) == 1
    manifest = deepcopy(saved['manifest'])
    manager.retry(project, run['id'], actor_id='owner')
    manager.execute(project, run['id'], actor_id='owner')
    saved = manager.get(project, run['id'], actor_id='owner')
    assert saved['status'] == 'completed'
    assert saved['manifest'] == manifest
    assert len(calls) == 2


def test_real_execution_failure_does_not_fabricate_result_or_metrics(tmp_path):
    manager, service, _, _, project = system(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('Milvus is not ready')
    service.answer = fail
    run = manager.create(project, {'questions': [{'query': '退款'}], 'variants': ['basic']}, actor_id='owner')
    summary = manager.run_job({'run_id': run['id'], 'actor_id': 'owner'}, project, lambda *args: None, 'task')
    assert summary['status'] == 'failed' and 'results' not in summary
    result = manager.get(project, run['id'], actor_id='owner')['results'][0]
    assert result['status'] == 'failed' and result['result'] is None and result['metrics'] is None
    assert result['error_category'] == 'milvus_unavailable'


def test_retry_recomputes_counts_and_summary_after_discarding_failed_receipts(tmp_path):
    manager, service, _, _, project = system(tmp_path)
    run = manager.create(project, {'questions': [{'query': 'one'}, {'query': 'two'}],
        'variants': ['basic']}, actor_id='owner')
    answer = service.answer
    def partly_fail(project, body):
        if body['query'] == 'two':
            raise RuntimeError('Milvus unavailable')
        return answer(project, body)
    service.answer = partly_fail
    manager.execute(project, run['id'], actor_id='owner')
    retried = manager.retry(project, run['id'], actor_id='owner')
    assert len(retried['results']) == retried['completed_items'] == 1
    assert retried['summary']['basic']['completed_questions'] == 1


def test_pg_experiment_persists_and_invalidates_after_source_revision(tmp_path, monkeypatch):
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
    from keyword_index_fixture import install_keyword_store
    activate_index(repo, project, service.encoder)
    install_keyword_store(service, monkeypatch)
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
