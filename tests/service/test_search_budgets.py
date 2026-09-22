import time

from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


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
        repository.put_batch(project_id, records)
        original_query = repository.query
        query_calls = 0

        def counted_query(*args, **kwargs):
            nonlocal query_calls
            query_calls += 1
            return original_query(*args, **kwargs)

        monkeypatch.setattr(repository, 'query', counted_query)
        started = time.perf_counter()
        response = client.post(f'/api/projects/{project_id}/search', json={
            'query': '退款', 'retrieval_mode': 'keyword',
            'k_entities': 5, 'k_chunks': 5, 'k_relations': 5,
        })
        elapsed = time.perf_counter() - started

        assert response.status_code == 200, response.text
        result = response.json()
        assert query_calls == 1
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

    rows = repository.query(project_id, include_embeddings=False)

    assert 'embedding' not in rows[0]
    assert 'embedding' not in repository.query(project_id)[0]  # 删列后向量不存 SQLite
