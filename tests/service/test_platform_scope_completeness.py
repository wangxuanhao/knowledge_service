"""Platform fan-out regression at the real service-to-query boundary; no DB."""
from copy import deepcopy
from pathlib import Path
import sys
from threading import Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from knowledge_service.services import service as service_module
from knowledge_service.repository.scoped_reads import ScopedReads


class PlatformScopeCompletenessTests(unittest.TestCase):
    def test_matching_last_of_64_selected_platforms_is_returned_and_declared_scope_is_complete(self):
        platforms = [f'平台{index:02d}' for index in range(64)]
        selected = {'mode': 'specific', 'values': platforms}
        calls, lock = [], Lock()
        match = {'id': 'last-platform-match', 'version_id': 'last-platform-v1',
                 'kind': 'chunk', 'text': '价格规则',
                 'metadata': {'applicability': {'platforms': {
                     'mode': 'specific', 'values': [platforms[-1]]}}}}

        class QueryEngine:
            def __init__(self, *_args, **_kwargs):
                pass

            def search(self, project, query, **kwargs):
                with lock:
                    calls.append(deepcopy({key: kwargs[key] for key in (
                        'scope', 'platform_match', 'k', 'content_k')}))
                scope = kwargs['scope']['applicability']['platforms']
                hits = ([deepcopy(match)] if kwargs['platform_match'] == 'specific_only'
                        and scope['values'] == [platforms[-1]] else [])
                return {'hits': hits, 'candidate_count': len(hits),
                        'requested_mode': 'keyword', 'active_mode': 'keyword',
                        'degraded': False, '_backend': 'milvus',
                        'backends': {'milvus': {'active': True, 'hits': len(hits)}}}

        service = object.__new__(service_module.KnowledgeService)
        service.repository = SimpleNamespace()
        service.encoder = SimpleNamespace(identity='test', semantic=True)
        service.milvus_store = object()
        service.ontology_expansion = lambda *_args, **_kwargs: None
        service.ontology_labels = lambda *_args, **_kwargs: {}
        with patch.object(service_module, 'RetrievalEngine', QueryEngine), patch.object(
                ScopedReads, 'source_context', return_value={'source_chunks': [], 'context_chunks': []}):
            result = service.search('project', {'query': '价格规则', 'retrieval_mode': 'keyword',
                'applicability': {'platforms': selected}, 'k_per_platform': 1,
                'k_entities': 0, 'k_relations': 0, 'k_chunks': 1})

        self.assertEqual([row['id'] for row in result['hits']], [match['id']])
        self.assertEqual(result['applicability']['platforms'], selected)
        queried = {call['scope']['applicability']['platforms']['values'][0] for call in calls
                   if call['platform_match'] == 'specific_only'}
        self.assertEqual(queried, set(result['applicability']['platforms']['values']))
        groups = {group['platform']: group for group in result['platform_groups']
                  if group['source_scope'] == 'specific'}
        self.assertEqual(set(groups), set(platforms))
        self.assertEqual([row['id'] for row in groups[platforms[-1]]['hits']], [match['id']])
        self.assertEqual(sum(call['platform_match'] == 'all_only' for call in calls), 1)
        self.assertTrue(all(call['k'] == 1 for call in calls))


if __name__ == '__main__':
    unittest.main()
