"""An ontology class channel recalls instances independently of body tokens."""
from types import SimpleNamespace

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.retrieval import RetrievalEngine
from knowledge_service.utils.filters import matches_filter


def test_keyword_expansion_recalls_qualified_subclass_without_encoding(monkeypatch):
    rows = [{'id': 'child', 'version_id': 'child-v1', 'kind': 'entity',
             'type': 'urn:Child', 'text': '小美', 'metadata': {'region': '华东'}},
            {'id': 'wrong-region', 'version_id': 'wrong-v1', 'kind': 'entity',
             'type': 'urn:Child', 'text': '小王', 'metadata': {'region': '华北'}},
            {'id': 'unrelated', 'version_id': 'other-v1', 'kind': 'entity',
             'type': 'urn:Other', 'text': '小李', 'metadata': {'region': '华东'}}]
    index = {'index_version': 1, 'watermark': 4, 'dim': 2, 'embedding_model': 'test',
             'catalog': default_catalog().to_dict()}
    repo = SimpleNamespace(projections=SimpleNamespace(
        search_index=lambda *_: dict(index), search_index_is_current=lambda *_: True))
    def selected(scope, kinds):
        return [row for row in rows if row['kind'] in kinds and matches_filter(row, scope.get('filters'))
                and (scope.get('_type_iris') is None or row['type'] in scope['_type_iris'])]
    def page(_self, _project, scope, *, kinds=None, limit=1000, **_options):
        return {'records': selected(scope, kinds)[:limit], 'next_cursor': None, 'total': None}
    monkeypatch.setattr(ScopedReads, 'page', page)
    monkeypatch.setattr(ScopedReads, 'by_ids', lambda _self, _p, scope, ids, *, kinds=None:
                        [row for row in selected(scope, kinds) if row['id'] in ids])
    monkeypatch.setattr(ScopedReads, 'version_id_batches', lambda _self, _p, scope, *, kinds=None, **_opts:
                        iter([[row['version_id'] for row in selected(scope, kinds)]]))
    encoder = SimpleNamespace(identity='test', semantic=False,
        encode=lambda *_: (_ for _ in ()).throw(AssertionError('keyword must not load a model')))
    engine = RetrievalEngine(repo, encoder, SimpleNamespace(available=True, search=lambda *_a, **_kw: []))
    expansion = {'terms': ['urn:Parent'], 'labels': ['合作方', '签约主播'],
                 'subclasses': ['urn:Child'], 'type_iris': ['urn:Parent', 'urn:Child']}
    scope = {'filters': {'field': 'region', 'op': 'eq', 'value': '华东'}}
    off = engine.search('p', '合作方', retrieval_mode='keyword', scope=scope,
                        content_channels=['entity'], content_k={'entity': 5})
    on = engine.search('p', '合作方', retrieval_mode='keyword', scope={**scope, '_ontology_expansion': expansion},
                       content_channels=['entity'], content_k={'entity': 5})
    assert off['hits'] == []
    assert [row['id'] for row in on['hits']] == ['child']
    assert on['backends']['ontology'] == {'active': True, 'hits': 1}
    assert on['ontology_expansion']['type_hits'] == 1
