"""Supplement attribution is display metadata; rankings stay unchanged. No I/O."""
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.retrieval import RetrievalEngine


def row(name, kind='entity', **extra):
    return {'id': name, 'version_id': name + '-v1', 'kind': kind,
            'text': name, 'type': 'urn:Parent', 'metadata': {}, **extra}


class LiteralClassAttributionTest(TestCase):
    query = 'Child'
    expanded_query = 'Child Parent'

    def setUp(self):
        self.calls, self.encodes, self.fences = [], [], []
        self.current = True
        self.index = {'index_version': 7, 'watermark': 19, 'embedding_model': 'unit',
                      'catalog': default_catalog().to_dict()}
        self.expansion = {'terms': ['urn:Child', 'urn:Parent'], 'labels': ['Child', 'Parent'],
                          'subclasses': [], 'type_iris': ['urn:Child', 'urn:Parent'],
                          'literal_terms': ['urn:Child'], 'entity_terms': [], 'entity_matches': []}
        self.rankings = {
            self.expanded_query: [row('shared'), row('expanded-only'), row('direct-type'),
                                  row('chunk-shared', 'chunk'), row('chunk-expanded', 'chunk')],
            self.query: [row('shared'), row('raw-only'), row('direct-type'), row('raw-fourth'),
                         row('chunk-shared', 'chunk'), row('chunk-raw', 'chunk')],
        }
        self.type_rows = [row('typed-extra'), row('shared'), row('direct-type'), row('shared')]
        def fence(*args):
            self.fences.append(args)
            return self.current
        repository = SimpleNamespace(projections=SimpleNamespace(
            search_index=lambda *_: dict(self.index), search_index_is_current=fence))
        def encode(texts):
            self.encodes.extend(texts)
            return [[1.0, float(len(text))] for text in texts]
        self.engine = RetrievalEngine(repository, SimpleNamespace(
            identity='unit', semantic=True, encode=encode), SimpleNamespace(available=True))

    def rank(self, _engine, project, query, mode, kinds, target, native_scope,
             pg_scope, platform_match, index, catalog, vector):
        self.calls.append({'query': query, 'kinds': list(kinds), 'target': target,
                           'scope': deepcopy(native_scope), 'pg_scope': deepcopy(pg_scope),
                           'platform': platform_match, 'index': index,
                           'catalog': catalog, 'vector': vector})
        rows = deepcopy([item for item in self.rankings[query] if item['kind'] in kinds][:target])
        return rows, len(rows), True

    def search(self, **options):
        arguments = {'retrieval_mode': 'keyword',
                     'scope': {'_ontology_expansion': self.expansion},
                     'content_channels': ['entity', 'chunk', 'relation'],
                     'content_k': {'entity': 4, 'chunk': 2, 'relation': 0}}
        arguments.update(options)
        with patch.object(RetrievalEngine, '_rank_current', self.rank), patch(
                'knowledge_service.services.scoped_ranking.ontology_type_rows', return_value=self.type_rows):
            return self.engine.search('p', self.query, **arguments)

    def test_marks_only_final_rows_absent_from_original_query_without_changing_legacy_rrf(self):
        result = self.search()
        self.assertEqual([item['id'] for item in result['hits']],
                         ['shared', 'direct-type', 'typed-extra', 'expanded-only',
                          'chunk-shared', 'chunk-expanded'])
        self.assertEqual([item['id'] for item in result['hits']
                          if item.get('retrieval_channel') == 'ontology_type'],
                         ['typed-extra', 'expanded-only', 'chunk-expanded'])
        self.assertEqual(result['ontology_expansion']['used_hits'], 3)
        self.assertEqual(result['ontology_expansion']['type_hits'], 4)
        self.assertEqual(result['candidate_count'], 9, 'comparison reads do not change recall metrics')
        self.assertTrue(result['complete'])
        self.assertFalse(result['degraded'])

    def test_raw_comparison_uses_same_scope_index_platform_and_each_nonzero_budget(self):
        scope = {'_ontology_expansion': self.expansion, 'valid_at': '2025-01-01',
                 'applicability': {'platforms': {'mode': 'specific', 'values': ['A']}}}
        self.search(scope=scope, retrieval_mode='semantic', platform_match='specific_only',
                    query_vector_cache={'lock': Lock()})
        self.assertEqual([(call['query'], call['kinds'], call['target']) for call in self.calls],
                         [(self.expanded_query, ['entity'], 4), (self.expanded_query, ['chunk'], 2),
                          (self.expanded_query, ['relation'], 0),
                          (self.query, ['entity'], 4), (self.query, ['chunk'], 2)])
        for direct, comparison in zip(self.calls[:2], self.calls[3:]):
            self.assertEqual(comparison['scope'], direct['scope'])
            self.assertEqual(comparison['pg_scope'], direct['pg_scope'])
            self.assertEqual(comparison['platform'], 'specific_only')
            self.assertIs(comparison['index'], direct['index'])
            self.assertIs(comparison['catalog'], direct['catalog'])
            self.assertNotEqual(comparison['vector'], direct['vector'])
        self.assertEqual(self.encodes, [self.expanded_query, self.query])

    def test_total_budget_comparison_keeps_the_legacy_mixed_kind_order(self):
        result = self.search(content_channels=['entity', 'chunk'], content_k=None, k=4)
        self.assertEqual([item['id'] for item in result['hits']],
                         ['shared', 'direct-type', 'typed-extra', 'expanded-only'])
        self.assertEqual([(call['query'], call['kinds'], call['target']) for call in self.calls],
                         [(self.expanded_query, ['entity', 'chunk'], 4),
                          (self.query, ['entity', 'chunk'], 4)])
        self.assertEqual(result['ontology_expansion']['used_hits'], 2)

    def test_matching_query_reuses_direct_ranking_and_does_not_mark_duplicate_type_rows(self):
        self.expansion['labels'] = ['Child']
        self.rankings[self.query] = [row('shared'), row('direct-type')]
        self.type_rows = [row('shared'), row('shared'), row('direct-type')]
        result = self.search(content_channels=['entity'], content_k={'entity': 4})
        self.assertEqual([call['query'] for call in self.calls], [self.query])
        self.assertEqual([item['id'] for item in result['hits']], ['shared', 'direct-type'])
        self.assertTrue(all('retrieval_channel' not in item for item in result['hits']))
        self.assertEqual(result['ontology_expansion'].get('used_hits'), 0)

    def test_flag_off_has_one_original_ranking_and_no_new_metadata_or_type_reads(self):
        with patch.object(RetrievalEngine, '_rank_current', self.rank), patch(
                'knowledge_service.services.scoped_ranking.ontology_type_rows',
                side_effect=AssertionError('disabled expansion read type channel')):
            result = self.engine.search('p', self.query, retrieval_mode='semantic',
                                        content_channels=['entity'])
        self.assertEqual([call['query'] for call in self.calls], [self.query])
        self.assertEqual(self.encodes, [self.query])
        self.assertEqual(len(self.fences), 1)
        self.assertNotIn('ontology_expansion', result)
        self.assertTrue(all('retrieval_channel' not in item for item in result['hits']))

    def test_historical_comparison_stays_sql_keyword_only_with_full_time_scope(self):
        calls = []
        scope = {'_ontology_expansion': self.expansion, 'known_at': '2025-02-01',
                 'valid_at': '2025-01-01', 'include_unknown': True}
        def keyword(_reader, project, read_scope, query, *, kinds, limit):
            calls.append((query, deepcopy(read_scope), list(kinds), limit))
            return deepcopy([item for item in self.rankings[query] if item['kind'] in kinds][:limit])
        with patch.object(ScopedReads, 'ranked_keyword', keyword), patch(
                'knowledge_service.services.scoped_ranking.ontology_type_rows', return_value=self.type_rows), patch.object(
                self.engine, '_current_index', side_effect=AssertionError('history read current index')):
            result = self.engine.search('p', self.query, retrieval_mode='keyword', scope=scope,
                                        content_channels=['entity'], content_k={'entity': 4},
                                        platform_match='all_only')
        self.assertEqual([(query, kinds, limit) for query, _, kinds, limit in calls],
                         [(self.expanded_query, ['entity'], 4), (self.query, ['entity'], 4)])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(calls[1][1], {'known_at': '2025-02-01', 'valid_at': '2025-01-01',
                                      'include_unknown': True, '_platform_match': 'all_only'})
        self.assertEqual(self.encodes, [])
        self.assertEqual(self.fences, [])
        self.assertEqual(result['_backend'], 'postgres_history')
        self.assertEqual(result['ontology_expansion']['used_hits'], 2)

    def test_final_fence_rejects_a_change_during_raw_comparison_without_type_channel(self):
        self.expansion['type_iris'] = []
        original_rank = self.rank
        def rank(*args):
            result = original_rank(*args)
            if args[2] == self.query:
                self.current = False
            return result
        with patch.object(RetrievalEngine, '_rank_current', rank):
            with self.assertRaisesRegex(RuntimeError, '索引已变化'):
                self.engine.search('p', self.query, retrieval_mode='keyword',
                    scope={'_ontology_expansion': self.expansion}, content_channels=['chunk'])

    def test_qualified_comparison_uses_authority_ranking_for_both_queries(self):
        scope = {'_ontology_expansion': self.expansion,
                 'filters': {'field': 'text', 'op': 'contains', 'value': 'Child'}}
        with patch('knowledge_service.integrations.milvus_filters.compile_filter', side_effect=ValueError('PG only')), patch(
                'knowledge_service.services.scoped_ranking.rank_qualified', self.rank), patch.object(
                RetrievalEngine, '_rank_current', side_effect=AssertionError('missing qualification')), patch(
                'knowledge_service.services.scoped_ranking.ontology_type_rows', return_value=[]):
            result = self.engine.search('p', self.query, retrieval_mode='keyword', scope=scope,
                                        content_channels=['chunk'], content_k={'chunk': 2})
        self.assertEqual([call['query'] for call in self.calls], [self.expanded_query, self.query])
        self.assertTrue(all(call['scope']['filters'] is None for call in self.calls))
        self.assertTrue(all(call['pg_scope']['filters'] == scope['filters'] for call in self.calls))
        self.assertEqual(result['ontology_expansion']['used_hits'], 1)


class LiteralClassServiceAttributionTest(TestCase):
    def explicit_group_result(self, channel, *, later_direct):
        from knowledge_service.services.service import KnowledgeService
        service = KnowledgeService(SimpleNamespace(), SimpleNamespace(identity='unit', semantic=False))
        expansion = {'terms': ['urn:Parent'], 'subclasses': [], 'type_iris': ['urn:Parent'],
                     'entity_matches': [], 'has_hierarchy': True, 'matched': True}
        def retrieve(_engine, _project, _query, **options):
            platform = options['scope'].get('applicability', {}).get('platforms', {}).get('values')
            if options.get('platform_match') == 'all_only':
                hits = []
            elif platform == ['A']:
                hits = [row('first'), row('shared', score=1, retrieval_channel=channel), row('A-last')]
            else:
                marker = {} if later_direct else {'retrieval_channel': channel}
                hits = [row('shared', score=2, **marker), row('B-last')]
            return {'hits': hits, 'candidate_count': len(hits), 'complete': True,
                    '_backend': 'milvus', 'backends': {},
                    'ontology_expansion': {**expansion, 'type_hits': 1}}
        with patch.object(service, 'ontology_expansion', return_value=expansion), patch(
                'knowledge_service.services.service.RetrievalEngine.search', retrieve), patch(
                'knowledge_service.services.retrieval_context.enrich_source_context'):
            return service.search('p', {'query': 'Child', 'ontology_context': False,
                'ontology_expansion': True, 'applicability': {
                    'platforms': {'mode': 'specific', 'values': ['A', 'B']}}})

    def test_explicit_groups_prefer_direct_membership_at_the_first_version_position(self):
        for channel in ('ontology_type', 'ontology_entity_type'):
            with self.subTest(channel=channel):
                result = self.explicit_group_result(channel, later_direct=True)
                self.assertEqual([hit['id'] for hit in result['hits']],
                                 ['first', 'shared', 'A-last', 'B-last'])
                shared = result['hits'][1]
                self.assertNotIn('retrieval_channel', shared)
                self.assertEqual(shared['score'], 2)
                self.assertEqual(shared['query_platform'], 'B')
                self.assertEqual(result['ontology_expansion']['used_hits'], 0)

    def test_explicit_groups_count_an_all_supplemental_duplicate_only_once(self):
        for channel in ('ontology_type', 'ontology_entity_type'):
            with self.subTest(channel=channel):
                result = self.explicit_group_result(channel, later_direct=False)
                self.assertEqual([hit['id'] for hit in result['hits']],
                                 ['first', 'shared', 'A-last', 'B-last'])
                self.assertEqual(result['hits'][1]['retrieval_channel'], channel)
                self.assertEqual(result['hits'][1]['score'], 1)
                self.assertEqual(result['ontology_expansion']['used_hits'], 1)

    def test_explicit_platforms_count_final_class_supplements_after_deduplication(self):
        from knowledge_service.services.service import KnowledgeService
        service = KnowledgeService(SimpleNamespace(), SimpleNamespace(identity='unit', semantic=False))
        expansion = {'terms': ['urn:Parent'], 'subclasses': [], 'type_iris': ['urn:Parent'],
                     'entity_matches': [], 'has_hierarchy': True, 'matched': True}
        def retrieve(_engine, _project, _query, **options):
            hits = [row('shared-extra', retrieval_channel='ontology_type')]
            if options['scope'].get('applicability', {}).get('platforms', {}).get('values') == ['B']:
                hits.append(row('B-extra', retrieval_channel='ontology_type'))
            return {'hits': hits, 'candidate_count': 1, 'complete': True, '_backend': 'milvus',
                    'backends': {}, 'ontology_expansion': {**expansion, 'type_hits': 2, 'used_hits': len(hits)}}
        with patch.object(service, 'ontology_expansion', return_value=expansion), patch(
                'knowledge_service.services.service.RetrievalEngine.search', retrieve), patch(
                'knowledge_service.services.retrieval_context.enrich_source_context'):
            result = service.search('p', {'query': 'Child', 'ontology_context': False,
                'ontology_expansion': True, 'applicability': {
                    'platforms': {'mode': 'specific', 'values': ['A', 'B']}}})
        self.assertEqual(result['ontology_expansion']['used_hits'], 2)
        self.assertEqual(result['ontology_expansion']['type_hits'], 6)

    def test_unrestricted_grouping_does_not_multiply_class_type_candidates(self):
        from knowledge_service.services.service import KnowledgeService
        service = KnowledgeService(SimpleNamespace(), SimpleNamespace(identity='unit', semantic=False))
        expansion = {'terms': ['urn:Parent'], 'subclasses': [], 'type_iris': ['urn:Parent'],
                     'entity_matches': [], 'has_hierarchy': True, 'matched': True}
        hit = row('shared-extra', retrieval_channel='ontology_type', metadata={
            'applicability': {'platforms': {'mode': 'specific', 'values': ['A', 'B']}}})
        with patch.object(service, 'ontology_expansion', return_value=expansion), patch(
                'knowledge_service.services.service.RetrievalEngine.search', return_value={
                    'hits': [hit], 'candidate_count': 1, 'complete': True, '_backend': 'milvus',
                    'backends': {}, 'ontology_expansion': {**expansion, 'type_hits': 3, 'used_hits': 1}}), patch(
                'knowledge_service.services.retrieval_context.enrich_source_context'):
            result = service.search('p', {'query': 'Child', 'ontology_context': False,
                                         'ontology_expansion': True})
        self.assertEqual(len(result['platform_groups']), 2)
        self.assertEqual(result['ontology_expansion']['used_hits'], 1)
        self.assertEqual(result['ontology_expansion']['type_hits'], 3)
