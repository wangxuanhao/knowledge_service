from unittest.mock import patch

from knowledge_service.repository.core import Repository
from knowledge_service.services.retrieval import RetrievalEngine


def test_historical_keyword_filters_and_ranks_before_bounded_decode(tmp_path):
    repo = Repository(tmp_path / 'historical-sql.sqlite')
    project = repo.create_project('history')['id']
    original = repo.put_record(project, {'id': 'rule', 'kind': 'entity',
        'text': '退款需要凭证', 'metadata': {'region': '杭州'}})
    repo.put_record(project, {'id': 'rule', 'kind': 'entity',
        'text': '新的条款', 'metadata': {'region': '北京'}}, expected_version=1)
    engine = RetrievalEngine(repo, object())
    with patch.object(repo, 'query', side_effect=AssertionError('full scan decode forbidden')):
        result = engine.search(project, '退款', retrieval_mode='keyword',
            content_channels=['entity'], k=1, scope={'known_at': original['recorded_at'],
                'filters': {'field': 'metadata.region', 'op': 'eq', 'value': '杭州'}})
    assert [row['version_id'] for row in result['hits']] == [original['version_id']]
    assert result['_backend'] == 'postgres_history'
    assert result['complete'] is True
    repo.close()
