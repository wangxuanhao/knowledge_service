import pytest
from knowledge_service.retrieval import rank_candidates


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
    with pytest.raises(ValueError, match='model'):
        rank_candidates([dict(id='a', embedding=[1, 0], embedding_model='other')], 'x', 1, Encoder())
    with pytest.raises(ValueError, match='dimension'):
        rank_candidates([dict(id='a', embedding=[1, 0, 0], embedding_model='test-v1')], 'x', 1, Encoder())
