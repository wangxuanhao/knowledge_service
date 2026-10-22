"""Application orchestration must preserve explicit independent quotas."""
from types import SimpleNamespace

from knowledge_service.services.retrieval import RetrievalEngine
from knowledge_service.services.service import KnowledgeService
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.repository.scoped_reads import ScopedReads


def test_service_passes_explicit_channel_budgets_without_global_k_clamp(monkeypatch):
    calls = []
    def search(_self, _project, _query, **options):
        calls.append(options)
        return {'hits': [], 'candidate_count': 0, 'active_mode': 'keyword',
                '_backend': 'milvus', 'complete': True, 'backends': {}}
    monkeypatch.setattr(RetrievalEngine, 'search', search)
    monkeypatch.setattr('knowledge_service.services.retrieval_context.enrich_source_context', lambda *_: None)
    service = KnowledgeService(SimpleNamespace(), SimpleNamespace(identity='test', semantic=False))
    service.search('p', {'query': '退款', 'retrieval_mode': 'keyword', 'ontology_context': False,
                          'k': 1, 'k_entities': 2, 'k_chunks': 2, 'k_relations': 1})
    assert calls[0]['content_k'] == {'entity': 2, 'chunk': 2, 'relation': 1}


def test_explicit_platform_budget_limits_real_engine_per_channel(monkeypatch):
    rows = [{'id': f'c{i}', 'version_id': f'v{i}', 'kind': 'chunk', 'text': '退款',
             'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['A']}}}}
            for i in range(5)]
    index = {'index_version': 1, 'watermark': 2, 'dim': 2, 'embedding_model': 'test',
             'catalog': default_catalog().to_dict()}
    repo = SimpleNamespace(projections=SimpleNamespace(search_index=lambda *_: dict(index),
        search_index_is_current=lambda *_: True))
    monkeypatch.setattr(ScopedReads, 'by_ids', lambda _self, _p, _s, ids, *, kinds=None:
        [row for row in rows if row['id'] in ids and row['kind'] in kinds])
    monkeypatch.setattr('knowledge_service.services.retrieval_context.enrich_source_context', lambda *_: None)
    def search(_project, _query, **options):
        if options.get('platform_match') != 'specific_only':
            return []
        return [{'record_id': row['id'], 'version_id': row['version_id'], 'score': 5-index}
                for index, row in enumerate(rows)][:options['k']]
    service = KnowledgeService(repo, SimpleNamespace(identity='test', semantic=False),
                               SimpleNamespace(available=True, search=search))
    result = service.search('p', {'query': '退款', 'retrieval_mode': 'keyword', 'ontology_context': False,
        'k_entities': 0, 'k_relations': 0, 'k_chunks': 5, 'k_per_platform': 1,
        'applicability': {'platforms': {'mode': 'specific', 'values': ['A']}}})
    assert len(next(group for group in result['platform_groups'] if group['platform'] == 'A')['hits']) == 1
