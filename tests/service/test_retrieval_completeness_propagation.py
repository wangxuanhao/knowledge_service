"""A partial platform/search result remains partial through QA delivery."""
from types import SimpleNamespace

from knowledge_service.services import answers
from knowledge_service.services.retrieval import RetrievalEngine
from knowledge_service.services.service import KnowledgeService


def test_later_partial_platform_makes_aggregate_and_group_explicit(monkeypatch):
    def search(_self, _project, _query, **options):
        platforms = options['scope']['applicability']['platforms'].get('values', [])
        partial = platforms == ['B'] and options.get('platform_match') == 'specific_only'
        return {'hits': [], 'candidate_count': 0, 'active_mode': 'keyword', '_backend': 'milvus',
                'complete': not partial, 'incomplete_reason': 'authority_refill_budget' if partial else None,
                'backends': {}}
    monkeypatch.setattr(RetrievalEngine, 'search', search)
    monkeypatch.setattr('knowledge_service.services.retrieval_context.enrich_source_context', lambda *_: None)
    service = KnowledgeService(SimpleNamespace(), SimpleNamespace(identity='test', semantic=False))
    result = service.search('p', {'query': '退款', 'retrieval_mode': 'keyword', 'ontology_context': False,
        'applicability': {'platforms': {'mode': 'specific', 'values': ['A', 'B']}}})
    assert result['complete'] is False
    b = next(group for group in result['platform_groups'] if group['platform'] == 'B')
    assert b['complete'] is False
    assert b['incomplete_reason'] == 'authority_refill_budget'


def test_qa_keeps_retrieval_partial_status_and_coverage_warning():
    class Service:
        encoder = SimpleNamespace(identity='test', semantic=False)
        repository = SimpleNamespace()
        def search(self, *_args):
            return {'hits': [], 'candidate_count': 128, 'active_mode': 'keyword', '_backend': 'milvus',
                    'complete': False, 'incomplete_reason': 'authority_refill_budget',
                    'platform_groups': [{'hits': [], 'source_scope': 'all', 'platform': None,
                                         'complete': False, 'incomplete_reason': 'authority_refill_budget'}]}
        def annotate_structure_pending(self, *_args):
            pass
    context = answers._retrieved_question_context(Service(), 'p', {
        'query': '退款', 'retrieval_mode': 'keyword', 'graph_context': False})
    assert context['complete'] is False
    assert context['incomplete_reason'] == 'authority_refill_budget'
    assert any('检索' in text and '不完整' in text for text in context['coverage_warnings'])
