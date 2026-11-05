import time

from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository.scoped_reads import ScopedReads
from keyword_index_fixture import install_keyword_store
from test_retrieval import activate_index


def test_keyword_search_three_channel_response_and_performance_budget(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / 'budget.sqlite', HashingEncoder())) as client:
        project_id = client.post('/api/projects', json={'name': 'budget'}).json()['id']
        repository = client.app.state.service.repository
        records = []
        for index in range(60):
            records.append({'id': f'e-{index}', 'kind': 'entity', 'type': 'Thing',
                            'text': f'退款实体 {index}', 'metadata': {}})
            records.append({'id': f'c-{index}', 'kind': 'chunk',
                            'text': f'退款原文 {index}', 'metadata': {}})
        for index in range(30):
            records.append({'id': f'r-{index}', 'kind': 'relation', 'type': 'mentions',
                            'subject_id': f'e-{index}', 'object_id': f'e-{index + 1}',
                            'text': f'退款关系 {index}', 'metadata': {}})
        # This quota budget uses common evidence; unknown scope has its own k_unknown cap.
        for record in records:
            record['metadata']['applicability'] = {'platforms': {'mode': 'all'}}
        repository.put_batch(project_id, records)
        service = client.app.state.service
        activate_index(repository, project_id, service.encoder)
        install_keyword_store(service, monkeypatch)
        original_query = repository.query
        query_calls = 0
        hydration_calls, native_calls = [], []
        original_hydrate, original_search = ScopedReads.by_ids, service.milvus_store.search

        def counted_query(*args, **kwargs):
            nonlocal query_calls
            query_calls += 1
            return original_query(*args, **kwargs)

        def hydrate(reader, project, scope, ids, *, kinds=None):
            hydration_calls.append((list(ids), kinds))
            assert len(ids) <= 16
            return original_hydrate(reader, project, scope, ids, kinds=kinds)

        def search(*args, **options):
            native_calls.append(options)
            return original_search(*args, **options)

        monkeypatch.setattr(repository, 'query', counted_query)
        monkeypatch.setattr(ScopedReads, 'by_ids', hydrate)
        monkeypatch.setattr(service.milvus_store, 'search', search)
        started = time.perf_counter()
        response = client.post(f'/api/projects/{project_id}/search', json={
            'query': '退款', 'retrieval_mode': 'keyword',
            'k_entities': 5, 'k_chunks': 5, 'k_relations': 5,
        })
        elapsed = time.perf_counter() - started

        assert response.status_code == 200, response.text
        result = response.json()
        assert query_calls == 0
        # Native ranking prefetches 16 per channel before selecting five hits.
        assert {tuple(kinds): len(ids) for ids, kinds in hydration_calls[:3]} == {
            ('entity',): 16, ('chunk',): 16, ('relation',): 16}
        assert all(len(ids) <= 15 and kinds in (['chunk'], ['document'])
                   for ids, kinds in hydration_calls[3:])
        assert len(hydration_calls) <= 5
        assert native_calls and all(1 <= call['k'] <= 512 for call in native_calls)
        assert all(call['retrieval_mode'] == 'keyword' for call in native_calls)
        assert elapsed < .75
        assert len(response.content) < 100_000
        assert 'nodes' not in result and 'edges' not in result
        assert {kind: sum(hit['kind'] == kind for hit in result['hits'])
                for kind in ('entity', 'chunk', 'relation')} == {
                    'entity': 5, 'chunk': 5, 'relation': 5}


def test_keyword_query_uses_embedding_free_projection(tmp_path):
    app = create_app(tmp_path / 'projection.sqlite', HashingEncoder())
    repository = app.state.service.repository
    project_id = repository.create_project('projection')['id']
    repository.put_record(project_id, {
        'id': 'c', 'kind': 'chunk', 'text': '退款', 'metadata': {},
        'embedding': [1.0] * 256, 'embedding_model': HashingEncoder.identity,
    })

    rows = repository.query(project_id)

    assert 'embedding' not in rows[0]
