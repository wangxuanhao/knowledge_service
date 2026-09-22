import pytest

from knowledge_service.utils.ingest_runs import readiness
from knowledge_service.repository import Repository
from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def test_search_readiness_is_independent_from_graph_readiness():
    state = readiness(document_ready=True, keyword_ready=True, semantic_ready=False,
                      candidate_ready=False, graph_ready=False)
    assert state['search_ready'] is True
    assert state['graph_ready'] is False


def test_ingest_run_is_durable_cas_guarded_and_restart_safe(tmp_path):
    path = tmp_path / 'runs.sqlite'
    repo = Repository(path)
    project_id = repo.create_project('甲')['id']
    document = repo.put_record(project_id, {'id': 'doc-1', 'kind': 'document', 'text': '原文'})
    run = repo.create_ingest_run(project_id, document['id'], document['version_id'])
    updated = repo.update_ingest_run(
        project_id, run['id'], expected_version=1, status='running', active_stage='keyword',
        readiness_patch={'document_ready': True, 'keyword_ready': True},
        counts_patch={'chunks_succeeded': 2})

    assert updated['version'] == 2
    assert updated['readiness']['search_ready'] is True
    with pytest.raises(ValueError, match='冲突'):
        repo.update_ingest_run(project_id, run['id'], expected_version=1, status='failed')
    repo.close()

    reopened = Repository(path)
    saved = reopened.get_ingest_run(project_id, run['id'])
    assert saved['active_stage'] == 'keyword'
    assert saved['counts']['chunks_succeeded'] == 2


def test_retry_attempt_links_to_prior_run(tmp_path):
    repo = Repository(tmp_path / 'runs.sqlite')
    project_id = repo.create_project('甲')['id']
    document = repo.put_record(project_id, {'id': 'doc-1', 'kind': 'document', 'text': '原文'})
    first = repo.create_ingest_run(project_id, document['id'], document['version_id'])
    retry = repo.create_ingest_run(
        project_id, document['id'], document['version_id'], retry_of=first['id'])

    assert first['attempt'] == 1
    assert retry['attempt'] == 2
    assert retry['retry_of'] == first['id']


def test_late_extraction_failure_preserves_search_ready_chunks(tmp_path, monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    calls = 0

    def fail_after_first_chunk(self, text, ontology):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError('second chunk extraction failed')
        return []

    monkeypatch.setattr(SemanticaExtractor, 'extract', fail_after_first_chunk)
    with TestClient(create_app(tmp_path / 'staged.sqlite', HashingEncoder())) as client:
        project_id = client.post('/api/projects', json={'name': '甲'}).json()['id']
        response = client.post(f'/api/projects/{project_id}/documents', json={
            'title': '分阶段', 'text': '退款规则。' * 80,
            'chunk_size': 100, 'chunk_overlap': 0,
        })
        assert response.status_code == 503
        records = client.post(f'/api/projects/{project_id}/records/query', json={}).json()['records']
        document = next(row for row in records if row['kind'] == 'document')
        chunks = [row for row in records if row['kind'] == 'chunk']
        assert chunks
        assert document['metadata']['status'] == 'failed'
        assert document['metadata']['readiness']['search_ready'] is True
        assert document['metadata']['readiness']['graph_ready'] is False
        search = client.post(f'/api/projects/{project_id}/search', json={
            'query': '退款', 'retrieval_mode': 'keyword'}).json()
        assert search['hits']
