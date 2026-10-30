import re

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.integrations.milvus_filters import compile_scope
from test_native_qa_grouping import native_service


def test_query_mentions_do_not_create_metadata_restrictions(monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)
    result = service.search('project', {'query': '美团价格', 'retrieval_mode': 'keyword'})
    assert result['applicability']['platforms']['mode'] == 'unknown'
    assert result['applicability']['businesses']['mode'] == 'unknown'
    assert len(calls) == 1
    assert calls[0]['platform_match'] is None
    assert calls[0]['scope'].get('valid_at') is None


def test_explicit_platform_scope_still_restricts_before_ranking(monkeypatch):
    calls = []
    service = native_service(monkeypatch, calls)
    service.search('project', {'query': '价格', 'retrieval_mode': 'keyword',
                              'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}})
    assert {call['platform_match'] for call in calls} == {'specific_only', 'all_only'}
    assert all(call['scope']['applicability']['platforms']['mode'] != 'unknown' for call in calls)


def test_grouping_does_not_reorder_global_relevance(monkeypatch):
    from knowledge_service.services import service as module
    service = native_service(monkeypatch, [])
    rows = [{'id': record_id, 'version_id': record_id, 'kind': 'chunk', 'text': '价格',
             'score': score, 'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': [platform]}}}}
            for record_id, score, platform in [('a1', .9, 'A'), ('b1', .8, 'B'), ('a2', .7, 'A')]]
    class Engine:
        def __init__(self, *args):
            pass
        def search(self, *args, **kwargs):
            return {'hits': rows, 'candidate_count': 3, 'active_mode': 'semantic',
                    '_backend': 'milvus', 'backends': {}, 'degraded': False}
    monkeypatch.setattr(module, 'RetrievalEngine', Engine)
    result = service.search('project', {'query': '价格', 'retrieval_mode': 'semantic'})
    assert [row['id'] for row in result['hits']] == ['a1', 'b1', 'a2']
    assert [[row['id'] for row in group['hits']] for group in result['platform_groups']] == [['a1', 'a2'], ['b1']]


def test_milvus_without_time_condition_does_not_exclude_expired_or_future():
    expression = compile_scope('project', {}, default_catalog(), kinds=['chunk']).expr
    assert 'valid_from' not in expression
    assert 'valid_until' not in expression
    assert 'platform_mode' not in expression


@pytest.mark.parametrize('include_unknown', [False, True])
def test_milvus_explicit_time_keeps_half_open_validity_filter(include_unknown):
    compiled = compile_scope('project', {
        'valid_at': '2025-01-01', 'include_unknown': include_unknown,
        'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}},
        default_catalog(), kinds=['chunk'])
    expression = compiled.expr
    parameters = re.findall(r'valid_(?:from|until)_us (?:<=|>) \{(\w+)\}', expression)
    assert len(parameters) == 2
    assert all(compiled.filter_params[key] == 1735689600000000 for key in parameters)
    assert ('valid_from_us is null or' if include_unknown else 'valid_from_us is not null and') in expression
    assert 'array_contains_any(platform_values,' in expression
    assert ['美团'] in compiled.filter_params.values()


def test_postgres_without_time_filter_returns_expired_and_future(tmp_path):
    from knowledge_service.repository.core import Repository
    repo = Repository(tmp_path / 'unrestricted.sqlite')
    p = repo.create_project('explicit scope only')['id']
    repo.put_batch(p, [
        {'id': 'expired', 'kind': 'chunk', 'text': '美团价格旧说明',
         'valid_from': '2020-01-01', 'valid_until': '2021-01-01'},
        {'id': 'future', 'kind': 'chunk', 'text': '美团价格新说明', 'valid_from': '2099-01-01'},
        {'id': 'undated', 'kind': 'chunk', 'text': '美团价格说明'},
    ])
    assert {row['id'] for row in repo.query(p)} == {'expired', 'future', 'undated'}
    assert repo.get_record(p, 'expired')['id'] == 'expired'
    assert {row['id'] for row in repo.query(p, valid_at='2025-01-01')} == {'undated'}
