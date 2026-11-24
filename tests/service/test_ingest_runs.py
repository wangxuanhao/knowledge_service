import pytest

from keyword_index_fixture import ready_keyword_index
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


def test_late_extraction_failure_publishes_chunks_only_after_successful_retry(tmp_path, monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    calls = 0

    def fail_after_first_chunk(self, text, ontology):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('second chunk extraction failed')
        return []

    monkeypatch.setattr(SemanticaExtractor, 'extract', fail_after_first_chunk)
    with TestClient(create_app(tmp_path / 'staged.sqlite', HashingEncoder())) as client:
        project_id = client.post('/api/projects', json={'name': '甲'}).json()['id']
        request = {
            'title': '分阶段', 'text': '退款规则。' * 80,
            'chunk_size': 100, 'chunk_overlap': 0,
        }
        response = client.post(f'/api/projects/{project_id}/documents', json=request)
        assert response.status_code == 503
        records = client.post(f'/api/projects/{project_id}/records/page', json={}).json()['records']
        document = next(row for row in records if row['kind'] == 'document')
        assert document['metadata']['status'] == 'failed'
        assert not [row for row in records if row['kind'] == 'chunk']
        assert document['metadata']['readiness']['search_ready'] is False
        assert document['metadata']['readiness']['graph_ready'] is False

        service = client.app.state.service
        result = service.ingest(project_id, {**request, '_document_id': document['id']})
        assert result['status'] == 'completed'
        chunks = service.repository.document_chunks(project_id, document['id'])
        assert len(chunks) == 4
        assert all(chunk['version'] == 1 for chunk in chunks)
        ready_keyword_index(service, project_id, monkeypatch)
        response = client.post(f'/api/projects/{project_id}/search', json={
            'query': '退款', 'retrieval_mode': 'keyword', 'kinds': ['chunk']})
        assert response.status_code == 200, response.text
        search = response.json()
        assert search['hits']
        assert {hit['version_id'] for hit in search['hits']} <= {chunk['version_id'] for chunk in chunks}
        assert all(hit['kind'] == 'chunk' and hit['source_id'] == document['id'] for hit in search['hits'])


def test_legacy_failed_ingest_chunks_are_excluded_from_result_reads(tmp_path):
    from knowledge_service.repository.scoped_reads import ScopedReads
    from knowledge_service.services.service import KnowledgeService

    repo = Repository(tmp_path / 'legacy-ingest')
    project_id = repo.create_project('旧摄取')['id']
    document = repo.put_record(project_id, {'id': 'doc', 'kind': 'document', 'text': '原文'})
    run = repo.create_ingest_run(project_id, document['id'], document['version_id'])
    failed = repo.put_record(project_id, {'id': 'doc:chunk:0', 'kind': 'chunk', 'text': '退款规则',
                                         'source_id': 'doc', 'metadata': {'run_id': run['id']}})
    run = repo.update_ingest_run(project_id, run['id'], run['version'], status='running')
    repo.update_ingest_run(project_id, run['id'], run['version'], status='interrupted')
    saved = repo.put_record(project_id, {'id': 'saved', 'kind': 'chunk', 'text': '退款规则'})

    reads = ScopedReads(repo)
    assert [row['id'] for row in reads.by_ids(project_id, {}, [failed['id'], saved['id']], kinds=['chunk'])] == ['saved']
    assert [row['id'] for row in reads.ranked_keyword(project_id, {}, '退款', kinds=['chunk'], limit=10)] == ['saved']
    result = KnowledgeService(repo, HashingEncoder()).ingest(project_id, {
        'title': '重新处理', 'text': '退款规则', 'extraction_mode': 'documents', '_document_id': 'doc',
    })
    assert result['status'] == 'completed'
    assert [row['version'] for row in reads.by_ids(project_id, {}, [failed['id']], kinds=['chunk'])] == [2]
    repo.close()
