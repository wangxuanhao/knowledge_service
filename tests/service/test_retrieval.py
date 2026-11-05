import pytest
from knowledge_service.services.retrieval import rank_candidates


class Encoder:
    identity = 'test-v1'
    def encode(self, texts):
        return [[1., 0.] for _ in texts]


def test_ranks_only_supplied_candidates():
    candidates = [dict(id='b', text='b', embedding=[0., 1.], embedding_model='test-v1'),
                  dict(id='a', text='a', embedding=[1., 0.], embedding_model='test-v1')]
    assert rank_candidates(candidates, 'question', 1, Encoder())[0]['id'] == 'a'
    assert rank_candidates(candidates[:1], 'question', 1, Encoder())[0]['id'] == 'b'
    assert rank_candidates([], 'question', 1, Encoder()) == []


def test_model_mismatch_is_not_silently_ranked():
    with pytest.raises(ValueError, match='模型'):
        rank_candidates([dict(id='a', embedding=[1, 0], embedding_model='other')], 'x', 1, Encoder())
    with pytest.raises(ValueError, match='维度'):
        rank_candidates([dict(id='a', embedding=[1, 0, 0], embedding_model='test-v1')], 'x', 1, Encoder())


class SemanticEncoder(Encoder):
    semantic = True
    dimension = 2

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return super().encode(texts)


def activate_index(repo, project, encoder):
    """Publish this fixture's index identity through the real PG lease guards."""
    from knowledge_service.domain.search_filter_catalog import default_catalog
    from knowledge_service.services.projection_sync import ProjectionSync

    sync = ProjectionSync(repo.projections, {}, schema_version=3)
    rebuild = repo.projections.begin_rebuild(
        project, 'milvus', 3, 'retrieval-fixture', lease_guard=sync._rebuild_lease_guard,
        embedding_model=encoder.identity, dim=encoder.dimension)
    state = {**rebuild['state'], 'catalog': default_catalog().to_dict()}
    with repo.projections.activation_guard(project, rebuild['claim'], 3) as watermark:
        assert watermark == rebuild['snapshot_seq']
        repo.projections.activate_index(rebuild['claim'], 3, watermark, state)
    index = repo.projections.search_index(project, 3)
    assert (index['embedding_model'], index['dim'], index['watermark']) == (
        encoder.identity, encoder.dimension, repo.projections.project_watermark(project))
    assert index['catalog'] == default_catalog().to_dict()
    return index


@pytest.mark.parametrize('known_at,ready,error', [
    (None, True, None),
    ('2026-01-01T00:00:00Z', True, (ValueError, '历史时间视图')),
    (None, False, (RuntimeError, 'not_built')),
])
def test_scoped_semantic_ranking_reads_exact_current_version_vectors(tmp_path, monkeypatch, known_at, ready, error):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    from knowledge_service.services.retrieval import RetrievalEngine

    repo = Repository(tmp_path / 'scoped-semantic.sqlite')
    project = repo.create_project('scoped-semantic')['id']
    valid_at = '2025-06-01T00:00:00Z'
    old = repo.put_record(project, dict(id='allowed', text='旧退款规则', kind='entity',
        metadata={'custom_gate': 'allowed'}, valid_from='2025-01-01',
        embedding=[0., 1.], embedding_model=Encoder.identity))
    current = repo.put_record(project, dict(id='allowed', text='当前退款规则', kind='entity',
        metadata={'custom_gate': 'allowed'}, valid_from='2025-01-01',
        embedding=[1., 0.], embedding_model=Encoder.identity), expected_version=old['version'])
    repo.put_record(project, dict(id='excluded', text='范围外退款规则', kind='entity',
        metadata={'custom_gate': 'excluded'}, valid_from='2025-01-01'))
    encoder = SemanticEncoder()
    index = activate_index(repo, project, encoder) if ready else None
    batches, qualifications = [], []
    real_batches, real_by_ids = ScopedReads.version_id_batches, ScopedReads.by_ids

    class NativeMilvus:
        available = True

        def __init__(self):
            self.calls = []

        def search(self, requested_project, query, *, kinds, k, scope, **options):
            self.calls.append((requested_project, query, kinds, k, dict(scope), options))
            assert batches == [[current['version_id']]] and qualifications == []
            assert options['eligible_version_ids'] == [current['version_id']]
            assert options['index_version'] == index['index_version']
            assert options['retrieval_mode'] == 'semantic'
            assert options['query_vector'] == [1., 0.]
            return [{'record_id': 'allowed', 'version_id': current['version_id'], 'score': .9}]

        def vectors_by_version_ids(self, *args, **kwargs):
            raise AssertionError('semantic retrieval must rank in Milvus, not download vectors')

        def search_bm25(self, *args, **kwargs):
            raise AssertionError('semantic retrieval must not switch to BM25')

    store = NativeMilvus()

    def version_batches(reader, project, scope, *, kinds, ids=None, batch_size=1000):
        assert reader.repo is repo
        assert ids is None and kinds == ['entity']
        assert scope['valid_at'] == valid_at and scope['include_unknown'] is False
        assert scope.get('known_at') is None
        for versions in real_batches(reader, project, scope, kinds=kinds, ids=ids, batch_size=batch_size):
            assert versions == [current['version_id']]
            batches.append(versions)
            yield versions

    def qualified_rows(reader, project, scope, ids, *, kinds=None):
        assert reader.repo is repo
        assert ids == ['allowed'] and kinds == ['entity']
        assert scope['valid_at'] == valid_at and scope['include_unknown'] is False
        qualifications.append(list(ids))
        return real_by_ids(reader, project, scope, ids, kinds=kinds)

    def forbidden(*args, **kwargs):
        raise AssertionError('native semantic retrieval must not scan or rank local candidates')

    monkeypatch.setattr(ScopedReads, 'version_id_batches', version_batches)
    monkeypatch.setattr(ScopedReads, 'by_ids', qualified_rows)
    monkeypatch.setattr(ScopedReads, 'page', forbidden)
    monkeypatch.setattr(repo, 'query', forbidden)
    monkeypatch.setattr(repo, 'keyword_candidates', forbidden)
    monkeypatch.setattr(repo, 'current_records_by_version_ids', forbidden)
    engine = RetrievalEngine(repo, encoder, store)
    scope = {'filters': {'field': 'custom_gate', 'op': 'eq', 'value': 'allowed'}, 'known_at': known_at,
             'valid_at': valid_at, 'include_unknown': False}
    if error:
        with pytest.raises(error[0], match=error[1]):
            engine.search(project, '退款', retrieval_mode='semantic', scope=scope,
                          content_channels=['entity'])
        assert store.calls == encoder.calls == batches == qualifications == []
        return

    result = engine.search(project, '退款', retrieval_mode='semantic', scope=scope,
                           content_channels=['entity'])
    assert result['active_mode'] == 'semantic' and result['degraded'] is False
    assert result['_backend'] == 'milvus' and result['ranking_strategy'] == 'milvus_scoped_batches'
    assert [(row['id'], row['version_id'], row['text']) for row in result['hits']] == [
        ('allowed', current['version_id'], '当前退款规则')]
    assert batches == [[current['version_id']]] and qualifications == [['allowed']]
    assert encoder.calls == [['退款']]
    assert len(store.calls) == 1
    requested_project, query, kinds, limit, native_scope, options = store.calls[0]
    assert (requested_project, query, kinds, limit) == (project, '退款', ['entity'], 50)
    assert native_scope['filters'] is None
    assert native_scope['valid_at'] == result['valid_at'] == valid_at
    assert native_scope['include_unknown'] is False and result['known_at'] is None
    assert 'embedding' not in result['hits'][0]


def test_native_semantic_backfill_does_not_replace_a_stale_hit_with_the_current_version(tmp_path, monkeypatch):
    from knowledge_service.repository import Repository
    from knowledge_service.repository.scoped_reads import ScopedReads
    from knowledge_service.services.retrieval import RetrievalEngine

    repo = Repository(tmp_path / 'stale-native.sqlite')
    project = repo.create_project('stale-native')['id']
    old = repo.put_record(project, dict(id='allowed', kind='entity', text='旧退款规则', metadata={}))
    current = repo.put_record(project, dict(id='allowed', kind='entity', text='当前退款规则', metadata={}),
                              expected_version=old['version'])
    encoder = SemanticEncoder()
    activate_index(repo, project, encoder)
    calls, real_by_ids = [], ScopedReads.by_ids

    def hydrate(reader, requested_project, scope, ids, *, kinds=None):
        calls.append((requested_project, list(ids)))
        rows = real_by_ids(reader, requested_project, scope, ids, kinds=kinds)
        assert [row['version_id'] for row in rows] == [current['version_id']]
        return rows

    def forbidden(*args, **kwargs):
        raise AssertionError('a stale Milvus hit must not trigger local ranking')

    monkeypatch.setattr(ScopedReads, 'by_ids', hydrate)
    monkeypatch.setattr(ScopedReads, 'page', forbidden)
    monkeypatch.setattr(repo, 'query', forbidden)

    class NativeMilvus:
        available = True

        def search(self, requested_project, query, *, kinds, k, scope, **options):
            assert requested_project == project and kinds == ['entity']
            assert options['retrieval_mode'] == 'semantic'
            return [{'record_id': 'allowed', 'version_id': old['version_id'], 'score': 1.}]

    result = RetrievalEngine(repo, encoder, NativeMilvus()).search(
        project, '退款', retrieval_mode='semantic', content_channels=['entity'])
    assert calls == [(project, ['allowed'])]
    assert result['hits'] == [] and result['degraded'] is False
    assert result['_backend'] == 'milvus' and result['active_mode'] == 'semantic'
