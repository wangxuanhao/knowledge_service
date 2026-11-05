from types import SimpleNamespace

import pytest

from keyword_index_fixture import ready_keyword_index
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.repository import Repository
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.projection_sync import ProjectionSync
from knowledge_service.services.retrieval import RetrievalEngine, rrf_fuse


class Encoder:
    identity = 'test-v1'
    semantic = True

    def encode(self, texts):
        return [[1.0, 0.0] if '退款' in text else [0.0, 1.0] for text in texts]


def keyword_engine(repo, project, monkeypatch):
    service = SimpleNamespace(repository=repo, milvus_store=None)
    ready_keyword_index(service, project, monkeypatch)
    return RetrievalEngine(repo, Encoder(), service.milvus_store)


def activate_semantic_index(repo, project):
    sync = ProjectionSync(repo.projections, {}, schema_version=3)
    rebuild = repo.projections.begin_rebuild(
        project, 'milvus', 3, 'semantic-quota-fixture',
        lease_guard=sync._rebuild_lease_guard, embedding_model=Encoder.identity, dim=2)
    state = {**rebuild['state'], 'catalog': default_catalog().to_dict()}
    with repo.projections.activation_guard(project, rebuild['claim'], 3) as watermark:
        assert watermark == rebuild['snapshot_seq']
        repo.projections.activate_index(rebuild['claim'], 3, watermark, state)
    cursor = repo.projections.cursor(project, 'milvus')
    assert cursor['status'] == 'ready'
    assert cursor['schema_version'] == 3
    assert cursor['applied_seq'] == repo.projections.project_watermark(project)
    assert cursor['rebuild_state']['embedding_model'] == Encoder.identity
    assert cursor['rebuild_state']['dim'] == 2
    assert cursor['rebuild_state']['catalog'] == default_catalog().to_dict()
    index = repo.projections.search_index(project, 3)
    assert (index['embedding_model'], index['dim'], index['watermark']) == (
        Encoder.identity, 2, watermark)
    return index


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


def test_hybrid_requires_milvus_and_explicit_keyword_prefilters(tmp_path, monkeypatch):
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

    with pytest.raises(RuntimeError, match='Milvus'):
        engine.search(project_id, '退款', retrieval_mode='hybrid',
            scope={'filters': {'field': 'region', 'op': 'eq', 'value': '北京'}},
            content_channels=['chunk'], k=5)
    engine = keyword_engine(repo, project_id, monkeypatch)
    result = engine.search(
        project_id, '退款', retrieval_mode='keyword',
        scope={'filters': {'field': 'region', 'op': 'eq', 'value': '北京'}},
        content_channels=['chunk'], k=5)

    assert [row['id'] for row in result['hits']] == ['keyword-hit']
    assert result['requested_mode'] == 'keyword'
    assert result['active_mode'] == 'keyword'
    assert result['degraded'] is False
    assert result['filter_stage'] == 'before_ranking'

    repo.put_record(project_id, {'id': 'no-vector', 'kind': 'chunk', 'text': '精确标识 Z-42'})
    with pytest.raises(RuntimeError, match='追平'):
        engine.search(project_id, 'Z-42', retrieval_mode='keyword',
                      scope={}, content_channels=['chunk'], k=5)
    keyword = keyword_engine(repo, project_id, monkeypatch).search(
        project_id, 'Z-42', retrieval_mode='keyword', scope={},
        content_channels=['chunk'], k=5)
    assert keyword['active_mode'] == 'keyword'
    assert keyword['degraded'] is False
    assert keyword['hits'][0]['id'] == 'no-vector'


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
    assert result['hits'][0]['version_id'] == old['version_id']
    assert result['keyword_backend'] == 'postgres_history'


def test_search_preserves_independent_entity_chunk_and_relation_quotas(tmp_path, monkeypatch):
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

    engine = keyword_engine(repo, project_id, monkeypatch)
    calls, native_search = [], engine.milvus.search

    def search(*args, **options):
        calls.append(options)
        return native_search(*args, **options)

    monkeypatch.setattr(engine.milvus, 'search', search)
    result = engine.search(
        project_id, '退款', retrieval_mode='keyword',
        content_channels=['entity', 'chunk', 'relation'], k=99,
        content_k={'entity': 2, 'chunk': 2, 'relation': 1})

    assert {kind: sum(hit['kind'] == kind for hit in result['hits'])
            for kind in ('entity', 'chunk', 'relation')} == {
                'entity': 2, 'chunk': 2, 'relation': 1}
    assert result['channel_quotas'] == {'entity': 2, 'chunk': 2, 'relation': 1}
    assert [call['kinds'] for call in calls] == [['entity'], ['chunk'], ['relation']]
    assert all(call['retrieval_mode'] == 'keyword' and call['index_version'] == 2
               and 1 <= call['k'] <= RetrievalEngine.MAX_NATIVE_LIMIT for call in calls)


def test_search_reuses_preloaded_scoped_rows_without_querying_repository(tmp_path, monkeypatch):
    # Preserve the regression node while covering the supported bounded-ID contract.
    repo = Repository(tmp_path / 'preloaded.sqlite')
    project_id = repo.create_project('甲')['id']
    saved = repo.put_record(project_id, {
        'id': 'chunk-1', 'kind': 'chunk', 'text': '退款原文', 'metadata': {},
    })
    engine = keyword_engine(repo, project_id, monkeypatch)
    native_calls, hydration_calls = [], []
    by_ids = ScopedReads.by_ids

    def native_search(project, query, **options):
        native_calls.append((project, query, options))
        return [{'record_id': saved['id'], 'version_id': saved['version_id'], 'score': 1.}]

    def hydrate(reader, project, scope, ids, *, kinds=None):
        hydration_calls.append((project, scope, ids, kinds))
        return by_ids(reader, project, scope, ids, kinds=kinds)

    def unexpected_query(*args, **kwargs):
        raise AssertionError('native search must hydrate only bounded ranked IDs')

    monkeypatch.setattr(repo, 'query', unexpected_query)
    monkeypatch.setattr(ScopedReads, 'page', unexpected_query)
    monkeypatch.setattr(ScopedReads, 'by_ids', hydrate)
    engine.milvus = SimpleNamespace(available=True, search=native_search)
    with pytest.raises(TypeError, match='candidates'):
        engine.search(project_id, '退款', retrieval_mode='keyword', candidates=[saved])
    result = engine.search(
        project_id, '退款', retrieval_mode='keyword', content_channels=['chunk'],
        k=5)

    assert [hit['id'] for hit in result['hits']] == ['chunk-1']
    assert len(native_calls) == 1
    assert native_calls[0][:2] == (project_id, '退款')
    assert native_calls[0][2]['kinds'] == ['chunk']
    assert native_calls[0][2]['retrieval_mode'] == 'keyword'
    assert native_calls[0][2]['index_version'] == 2
    assert 5 <= native_calls[0][2]['k'] <= RetrievalEngine.MAX_NATIVE_LIMIT
    assert len(hydration_calls) == 1
    assert hydration_calls[0][0] == project_id
    assert hydration_calls[0][2:] == (['chunk-1'], ['chunk'])
    assert result['hits'][0]['version_id'] == saved['version_id']


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
    saved = repo.put_batch(project_id, records)
    index = activate_semantic_index(repo, project_id)
    calls = []
    class NativeStore:
        available = True
        def search(self, project, query, *, kinds, k, scope, **options):
            calls.append((project, query, kinds, k, scope, options))
            return [{'record_id': row['id'], 'version_id': row['version_id'], 'score': 1.}
                    for row in saved if row['kind'] in kinds][:k]

    result = RetrievalEngine(repo, Encoder(), NativeStore()).search(
        project_id, '退款', retrieval_mode='semantic',
        content_channels=['entity', 'chunk', 'relation'], k=10,
        content_k={'entity': 1, 'chunk': 1, 'relation': 1})

    assert {kind: sum(hit['kind'] == kind for hit in result['hits'])
            for kind in ('entity', 'chunk', 'relation')} == {
                'entity': 1, 'chunk': 1, 'relation': 1}
    assert result['channel_quotas'] == {'entity': 1, 'chunk': 1, 'relation': 1}
    assert [call[2] for call in calls] == [['entity'], ['chunk'], ['relation']]
    assert all(call[0:2] == (project_id, '退款') and call[4] == {} for call in calls)
    assert all(1 <= call[3] <= RetrievalEngine.MAX_NATIVE_LIMIT for call in calls)
    assert all(call[5]['retrieval_mode'] == 'semantic'
               and call[5]['query_vector'] == [1., 0.]
               and call[5]['index_version'] == index['index_version'] for call in calls)
