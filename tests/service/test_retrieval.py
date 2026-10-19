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


@pytest.mark.parametrize('known_at,ready,expected', [(None, True, 'semantic'),
    ('2026-01-01T00:00:00Z', True, 'keyword'), (None, False, 'keyword')])
def test_scoped_semantic_ranking_reads_exact_current_version_vectors(known_at, ready, expected):
    from knowledge_service.services.retrieval import RetrievalEngine

    class Repo:
        def query(self, *args, **kwargs):
            return [dict(id='allowed', version_id='v2', text='退款规则', kind='entity', metadata={}),
                    dict(id='excluded', version_id='v3', text='退款规则', kind='entity', metadata={})]

        def keyword_candidates(self, *args, **kwargs):
            return []

    class Vectors:
        available = True
        calls = []

        def vectors_by_version_ids(self, project, versions):
            self.calls.append((project, versions))
            return {'v2': {'embedding': [1., 0.], 'embedding_model': 'test-v1'},
                    'v3': {'embedding': [0., 1.], 'embedding_model': 'test-v1'}}

    store = Vectors()
    result = RetrievalEngine(Repo(), Encoder(), store, projection_readiness=lambda *a: ready).search(
        'p', '退款', retrieval_mode='semantic',
        scope={'_candidate_ids': ['allowed'], 'known_at': known_at})
    assert result['active_mode'] == expected
    assert [row['id'] for row in result['hits']] == ['allowed']
    if expected == 'semantic':
        assert store.calls == [('p', ['v2'])]
        assert 'embedding' not in result['hits'][0]
    else:
        assert not store.calls
