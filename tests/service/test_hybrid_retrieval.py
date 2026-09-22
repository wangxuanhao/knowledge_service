from knowledge_service.repository import Repository
from knowledge_service.services.retrieval import RetrievalEngine, rrf_fuse


class Encoder:
    identity = 'test-v1'
    semantic = True

    def encode(self, texts):
        return [[1.0, 0.0] if '退款' in text else [0.0, 1.0] for text in texts]


def test_rrf_is_deterministic_and_keeps_backend_ranks():
    keyword = [{'id': 'exact'}, {'id': 'semantic'}]
    semantic = [{'id': 'semantic'}, {'id': 'exact'}]

    fused = rrf_fuse({'keyword': keyword, 'semantic': semantic}, limit=2)

    assert [row['id'] for row in fused] == ['exact', 'semantic']
    assert fused[0]['rrf_score'] == fused[1]['rrf_score']
    assert fused[0]['backend_ranks'] == {'keyword': 1, 'semantic': 2}


def test_fts_tracks_current_rows_and_can_be_rebuilt(tmp_path):
    repo = Repository(tmp_path / 'fts.sqlite')
    project_id = repo.create_project('甲')['id']
    first = repo.put_record(project_id, {
        'id': 'entity-1', 'kind': 'entity', 'type': 'Merchant',
        'text': '商户ABC-001', 'metadata': {'code': 'ABC-001'},
    })
    assert [row['id'] for row in repo.keyword_candidates(project_id, 'ABC-001')] == ['entity-1']

    repo.put_record(project_id, {
        'id': 'entity-1', 'kind': 'entity', 'type': 'Merchant',
        'text': '商户XYZ-009', 'metadata': {'code': 'XYZ-009'},
    }, expected_version=first['version'])
    assert repo.keyword_candidates(project_id, 'ABC-001') == []
    assert [row['id'] for row in repo.keyword_candidates(project_id, 'XYZ-009')] == ['entity-1']

    repo._db.execute('DELETE FROM record_fts')
    repo._db.commit()
    assert repo.keyword_candidates(project_id, 'XYZ-009') == []
    assert repo.rebuild_fts(project_id) == 1
    assert [row['id'] for row in repo.keyword_candidates(project_id, 'XYZ-009')] == ['entity-1']


def test_hybrid_retrieval_prefilters_and_degrades_per_backend(tmp_path):
    repo = Repository(tmp_path / 'hybrid.sqlite')
    project_id = repo.create_project('甲')['id']
    repo.put_record(project_id, {
        'id': 'keyword-hit', 'kind': 'chunk', 'text': '退款条款 ABC-001',
        'metadata': {'region': '北京'}, 'embedding': [0.0, 1.0],
        'embedding_model': 'test-v1',
    })
    repo.put_record(project_id, {
        'id': 'semantic-hit', 'kind': 'chunk', 'text': '退款办法',
        'metadata': {'region': '上海'}, 'embedding': [1.0, 0.0],
        'embedding_model': 'test-v1',
    })
    engine = RetrievalEngine(repo, Encoder())

    result = engine.search(
        project_id, '退款', retrieval_mode='hybrid',
        scope={'filters': {'field': 'region', 'op': 'eq', 'value': '北京'}},
        content_channels=['chunk'], k=5)

    assert [row['id'] for row in result['hits']] == ['keyword-hit']
    assert result['requested_mode'] == 'hybrid'
    # 删列后本地无向量，semantic 通道不可用，hybrid 降级 keyword
    assert result['active_mode'] == 'keyword'
    assert result['degraded'] is True
    assert result['filter_stage'] == 'before_ranking'

    repo.put_record(project_id, {'id': 'no-vector', 'kind': 'chunk', 'text': '精确标识 Z-42'})
    degraded = engine.search(
        project_id, 'Z-42', retrieval_mode='hybrid', scope={},
        content_channels=['chunk'], k=5)
    assert degraded['active_mode'] == 'keyword'
    assert degraded['degraded'] is True
    assert degraded['hits'][0]['id'] == 'no-vector'


def test_historical_keyword_search_scans_historical_versions(tmp_path):
    repo = Repository(tmp_path / 'history.sqlite')
    project_id = repo.create_project('甲')['id']
    old = repo.put_record(project_id, {'id': 'chunk-1', 'kind': 'chunk', 'text': '旧标识 OLD-7'})
    repo.put_record(project_id, {'id': 'chunk-1', 'kind': 'chunk', 'text': '新标识 NEW-8'}, expected_version=1)
    engine = RetrievalEngine(repo, Encoder())

    result = engine.search(
        project_id, 'OLD-7', retrieval_mode='keyword',
        scope={'known_at': old['recorded_at']}, content_channels=['chunk'], k=5)

    assert [row['id'] for row in result['hits']] == ['chunk-1']
    assert result['keyword_backend'] == 'historical_scan'


def test_search_preserves_independent_entity_chunk_and_relation_quotas(tmp_path):
    repo = Repository(tmp_path / 'quotas.sqlite')
    project_id = repo.create_project('甲')['id']
    for index in range(4):
        repo.put_record(project_id, {
            'id': f'entity-{index}', 'kind': 'entity', 'type': 'Thing',
            'text': f'退款实体 {index}', 'metadata': {},
        })
    for index in range(3):
        repo.put_record(project_id, {
            'id': f'chunk-{index}', 'kind': 'chunk', 'text': f'退款原文 {index}',
            'metadata': {},
        })
    for index in range(2):
        repo.put_record(project_id, {
            'id': f'relation-{index}', 'kind': 'relation', 'type': 'mentions',
            'subject_id': 'entity-0', 'object_id': f'entity-{index + 1}',
            'text': f'退款关系 {index}', 'metadata': {},
        })

    result = RetrievalEngine(repo, Encoder()).search(
        project_id, '退款', retrieval_mode='keyword',
        content_channels=['entity', 'chunk', 'relation'], k=99,
        content_k={'entity': 2, 'chunk': 2, 'relation': 1})

    assert {kind: sum(hit['kind'] == kind for hit in result['hits'])
            for kind in ('entity', 'chunk', 'relation')} == {
                'entity': 2, 'chunk': 2, 'relation': 1}
    assert result['channel_quotas'] == {'entity': 2, 'chunk': 2, 'relation': 1}


def test_search_reuses_preloaded_scoped_rows_without_querying_repository(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'preloaded.sqlite')
    project_id = repo.create_project('甲')['id']
    repo.put_record(project_id, {
        'id': 'chunk-1', 'kind': 'chunk', 'text': '退款原文', 'metadata': {},
    })
    rows = repo.query(project_id)

    def unexpected_query(*args, **kwargs):
        raise AssertionError('preloaded search must not read records again')

    monkeypatch.setattr(repo, 'query', unexpected_query)
    result = RetrievalEngine(repo, Encoder()).search(
        project_id, '退款', retrieval_mode='keyword', content_channels=['chunk'],
        k=5, candidates=rows)

    assert [hit['id'] for hit in result['hits']] == ['chunk-1']


def test_semantic_search_applies_quotas_before_channels_can_crowd_each_other(tmp_path):
    repo = Repository(tmp_path / 'semantic-quotas.sqlite')
    project_id = repo.create_project('甲')['id']
    records = [
        {'id': 'a', 'kind': 'entity', 'type': 'Thing', 'text': '退款实体 A',
         'metadata': {}, 'embedding': [1.0, 0.0], 'embedding_model': 'test-v1'},
        {'id': 'b', 'kind': 'entity', 'type': 'Thing', 'text': '退款实体 B',
         'metadata': {}, 'embedding': [1.0, 0.0], 'embedding_model': 'test-v1'},
        {'id': 'c', 'kind': 'chunk', 'text': '退款原文', 'metadata': {},
         'embedding': [1.0, 0.0], 'embedding_model': 'test-v1'},
        {'id': 'r', 'kind': 'relation', 'type': 'mentions', 'text': '退款关系',
         'subject_id': 'a', 'object_id': 'b', 'metadata': {},
         'embedding': [1.0, 0.0], 'embedding_model': 'test-v1'},
    ]
    repo.put_batch(project_id, records)

    result = RetrievalEngine(repo, Encoder()).search(
        project_id, '退款', retrieval_mode='semantic',
        content_channels=['entity', 'chunk', 'relation'], k=10,
        content_k={'entity': 1, 'chunk': 1, 'relation': 1})

    assert {kind: sum(hit['kind'] == kind for hit in result['hits'])
            for kind in ('entity', 'chunk', 'relation')} == {
                'entity': 1, 'chunk': 1, 'relation': 1}
