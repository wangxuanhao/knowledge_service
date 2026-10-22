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

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return super().encode(texts)


@pytest.mark.parametrize('known_at,ready,error', [
    (None, True, None),
    ('2026-01-01T00:00:00Z', True, (ValueError, '历史时间视图')),
    (None, False, (RuntimeError, '投影未就绪')),
])
def test_scoped_semantic_ranking_reads_exact_current_version_vectors(monkeypatch, known_at, ready, error):
    from knowledge_service.repository.scoped_reads import ScopedReads
    from knowledge_service.services.retrieval import RetrievalEngine

    current = dict(id='allowed', version_id='v2', text='当前退款规则', kind='entity',
                   metadata={}, embedding=[1., 0.])
    batches, qualifications, readiness = [], [], []
    valid_at = '2025-06-01T00:00:00Z'

    class Repo:
        # ScopedReads is replaced at its SQL boundary; no database is opened.
        _db = object()

        def __init__(self):
            self.backfills = []

        def query(self, *args, **kwargs):
            raise AssertionError('semantic retrieval must not preload local ranking candidates')

        def keyword_candidates(self, *args, **kwargs):
            raise AssertionError('semantic retrieval must not fall back to keyword ranking')

        def current_records_by_version_ids(self, project, versions):
            self.backfills.append((project, list(versions)))
            assert versions == ['v2']
            return [current]

    class NativeMilvus:
        available = True
        encoder_identity = 'test-v1'

        def __init__(self):
            self.calls = []

        def search_dense(self, project, vector, *, kinds, k, scope):
            self.calls.append((project, vector, kinds, k, dict(scope)))
            assert scope['_version_ids'] == ['v2']
            return [{'version_id': 'v2', 'score': .9}]

        def vectors_by_version_ids(self, *args, **kwargs):
            raise AssertionError('semantic retrieval must rank in Milvus, not download vectors')

        def search_bm25(self, *args, **kwargs):
            raise AssertionError('semantic retrieval must not switch to BM25')

    repo, store, encoder = Repo(), NativeMilvus(), SemanticEncoder()

    def version_batches(reader, project, scope, *, kinds, ids=None, batch_size=1000):
        assert reader.repo is repo and project == 'p'
        assert ids == ['allowed'] and kinds == ['entity']
        assert scope['valid_at'] == valid_at and scope['include_unknown'] is False
        assert scope['known_at'] is None
        batches.append(list(ids))
        yield ['v2']  # Authority supplies the current version, never v1 or excluded v3.

    def qualified_rows(reader, project, scope, ids, *, kinds=None):
        assert reader.repo is repo and project == 'p'
        assert ids == ['allowed'] and kinds == ['entity']
        assert scope['valid_at'] == valid_at and scope['include_unknown'] is False
        qualifications.append(list(ids))
        return [current]

    def projection_ready(project, target, schema):
        readiness.append((project, target, schema))
        return ready

    monkeypatch.setattr(ScopedReads, 'version_id_batches', version_batches)
    monkeypatch.setattr(ScopedReads, 'by_ids', qualified_rows)
    engine = RetrievalEngine(repo, encoder, store, projection_readiness=projection_ready)
    scope = {'_candidate_ids': ['allowed'], 'known_at': known_at,
             'valid_at': valid_at, 'include_unknown': False}
    if error:
        with pytest.raises(error[0], match=error[1]):
            engine.search('p', '退款', retrieval_mode='semantic', scope=scope,
                          content_channels=['entity'])
        assert store.calls == repo.backfills == encoder.calls == batches == qualifications == []
        assert readiness == ([] if known_at else [('p', 'milvus', 3)])
        return

    result = engine.search('p', '退款', retrieval_mode='semantic', scope=scope,
                           content_channels=['entity'])
    assert result['active_mode'] == 'semantic' and result['degraded'] is False
    assert result['_backend'] == 'milvus' and result['ranking_strategy'] == 'milvus_scoped_batches'
    assert [(row['id'], row['version_id'], row['text']) for row in result['hits']] == [
        ('allowed', 'v2', '当前退款规则')]
    assert repo.backfills == [('p', ['v2'])]
    assert batches == qualifications == [['allowed']]
    assert readiness == [('p', 'milvus', 3)] and encoder.calls == [['退款']]
    assert len(store.calls) == 1
    project, vector, kinds, limit, native_scope = store.calls[0]
    assert (project, vector, kinds, limit) == ('p', [1., 0.], ['entity'], 10)
    assert native_scope['_exact_record_ids'] == ['allowed']
    assert native_scope['valid_at'] == result['valid_at'] == valid_at
    assert native_scope['include_unknown'] is False and result['known_at'] is None
    assert 'embedding' not in result['hits'][0]


def test_native_semantic_backfill_does_not_replace_a_stale_hit_with_the_current_version():
    from knowledge_service.services.retrieval import RetrievalEngine

    class Repo:
        def __init__(self):
            self.calls = []
            self.current = dict(id='allowed', version_id='v2', kind='entity',
                                text='当前退款规则', metadata={})

        def current_records_by_version_ids(self, project, versions):
            self.calls.append((project, list(versions)))
            return [self.current] if self.current['version_id'] in versions else []

        def query(self, *args, **kwargs):
            raise AssertionError('a stale Milvus hit must not trigger local ranking')

    class NativeMilvus:
        available = True
        encoder_identity = 'test-v1'

        def search_dense(self, project, vector, *, kinds, k, scope):
            assert project == 'p' and kinds == ['entity']
            return [{'version_id': 'v1', 'score': 1.}]

    repo = Repo()
    result = RetrievalEngine(repo, SemanticEncoder(), NativeMilvus(),
        projection_readiness=lambda *args: True).search(
            'p', '退款', retrieval_mode='semantic', content_channels=['entity'])
    assert repo.calls == [('p', ['v1'])]
    assert result['hits'] == [] and result['degraded'] is False
    assert result['_backend'] == 'milvus' and result['active_mode'] == 'semantic'
