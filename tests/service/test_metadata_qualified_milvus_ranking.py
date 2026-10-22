"""Full public predicates gate every Milvus candidate without local vectors."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.retrieval import RetrievalEngine
from knowledge_service.utils.filters import matches_filter


def _engine(monkeypatch, records, *, page_size=1000, dense_scores=None, keyword_scores=None):
    calls, qualifications = [], []
    index = {'index_version': 2, 'watermark': 9, 'dim': 2, 'embedding_model': 'test',
             'catalog': default_catalog().to_dict()}
    repo = SimpleNamespace(projections=SimpleNamespace(
        search_index=lambda *_: dict(index), search_index_is_current=lambda *_: True))
    encoder = SimpleNamespace(identity='test', semantic=True, encode=lambda texts: [[1., 0.] for _ in texts])
    def batches(_self, project, scope, *, kinds=None, ids=None, batch_size=1000):
        qualifications.append(scope)
        selected = [row['version_id'] for row in records if row['kind'] in kinds
                    and matches_filter(row, scope.get('filters'))]
        for offset in range(0, len(selected), page_size):
            yield selected[offset:offset + page_size]
    def by_ids(_self, _project, scope, ids, *, kinds=None):
        return [row for row in records if row['id'] in ids and row['kind'] in kinds
                and matches_filter(row, scope.get('filters'))]
    def search(_project, _query, **options):
        calls.append(options)
        eligible = options.get('eligible_version_ids')
        assert eligible is not None and 0 < len(eligible) <= 1000
        assert options['scope'].get('filters') is None
        mode = options['retrieval_mode']
        assert mode in {'semantic', 'keyword'}, 'batch-local RRF must never replace global RRF'
        scores = (dense_scores if mode == 'semantic' else keyword_scores) or {}
        selected = [row for row in records if row['version_id'] in eligible]
        selected.sort(key=lambda row: (-scores.get(row['id'], int(row['id'][1:])), row['id']))
        return [{'record_id': row['id'], 'version_id': row['version_id'],
                 'score': scores.get(row['id'], int(row['id'][1:]))}
                for row in selected[:options['k']]]
    monkeypatch.setattr(ScopedReads, 'version_id_batches', batches)
    monkeypatch.setattr(ScopedReads, 'by_ids', by_ids)
    monkeypatch.setattr(ScopedReads, 'page', lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError('payload preloads are forbidden')))
    return RetrievalEngine(repo, encoder, SimpleNamespace(available=True, search=search)), calls, qualifications


@pytest.mark.parametrize('mode', ['keyword', 'semantic', 'hybrid'])
def test_metadata_gate_ranks_all_5002_qualified_versions_before_top_k(monkeypatch, mode):
    records = [{'id': f'r{i}', 'version_id': f'v{i}', 'kind': 'chunk', 'text': '退款',
                'metadata': {'region': '华东'}} for i in range(5002)]
    records.append({'id': 'r99999', 'version_id': 'v99999', 'kind': 'chunk', 'text': '退款',
                    'metadata': {'region': '华北'}})
    engine, calls, qualifications = _engine(monkeypatch, records)
    predicate = {'field': 'metadata.region', 'op': 'eq', 'value': '华东'}
    result = engine.search('p', '退款', retrieval_mode=mode, scope={'filters': predicate},
                           content_channels=['chunk'], content_k={'chunk': 2})
    assert [row['id'] for row in result['hits']] == ['r5001', 'r5000']
    assert result['complete'] is True
    assert all(scope['filters'] == predicate for scope in qualifications)
    assert set().union(*(set(call['eligible_version_ids']) for call in calls)) == {f'v{i}' for i in range(5002)}
    assert all('v99999' not in call['eligible_version_ids'] for call in calls)
    assert result['ranking_strategy'] == 'milvus_scoped_batches'


def test_hybrid_merges_global_dense_and_bm25_ranks_before_rrf(monkeypatch):
    records = [{'id': f'r{i}', 'version_id': f'v{i}', 'kind': 'chunk', 'text': '退款',
                'metadata': {'region': '华东'}} for i in range(4)]
    engine, calls, _ = _engine(monkeypatch, records, page_size=2,
        dense_scores={'r0': 1, 'r1': 2, 'r2': 4, 'r3': 3},
        keyword_scores={'r0': 4, 'r1': 1, 'r2': 3, 'r3': 2})
    result = engine.search('p', '退款', scope={'filters': {'field': 'region', 'op': 'eq', 'value': '华东'}},
                           content_channels=['chunk'], content_k={'chunk': 1})
    assert [row['id'] for row in result['hits']] == ['r2']
    assert {call['retrieval_mode'] for call in calls} == {'semantic', 'keyword'}


@pytest.mark.parametrize('predicate', [
    {'and': [{'field': 'region', 'op': 'eq', 'value': '华东'},
             {'field': 'amount', 'op': 'gt', 'value': Decimal('9007199254740990.5')}]},
    {'or': [{'field': 'nested.status', 'op': 'eq', 'value': None},
            {'field': 'tags', 'op': 'contains', 'value': '退款'}]},
    {'field': 'nested', 'op': 'eq', 'value': {'status': None}},
    {'field': 'missing', 'op': 'exists', 'value': False},
])
def test_full_complex_ast_reaches_pg_unchanged(monkeypatch, predicate):
    records = [{'id': 'r1', 'version_id': 'v1', 'kind': 'chunk', 'text': '退款',
                'metadata': {'region': '华东', 'amount': 9007199254740991,
                             'nested': {'status': None}, 'tags': ['退款']}}]
    engine, _calls, scopes = _engine(monkeypatch, records)
    result = engine.search('p', '退款', retrieval_mode='keyword', scope={'filters': predicate},
                           content_channels=['chunk'], content_k={'chunk': 1})
    assert [row['id'] for row in result['hits']] == ['r1']
    assert scopes[0]['filters'] == predicate
