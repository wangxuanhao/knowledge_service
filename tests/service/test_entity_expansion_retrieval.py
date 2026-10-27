"""Entity expansion keeps direct query ranking and bounded related content; no I/O."""
from copy import deepcopy
from types import SimpleNamespace
from threading import Lock
from unittest import TestCase
from unittest.mock import patch

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.services.retrieval import RetrievalEngine, cached_query_vector


def row(name, kind='entity'):
    return {'id': name, 'version_id': name+'-v1', 'kind': kind, 'text': name,
            'type': 'urn:Platform', 'metadata': {}}


class EntityExpansionRetrievalTest(TestCase):
    def harness(self):
        self.calls, self.encodes = [], []
        self.base = [*[row('base-'+str(i)) for i in range(4)], row('meituan'),
                     *[row('source-'+str(i), 'chunk') for i in range(5)]]
        self.related = [row('related-entity'), row('related-source', 'chunk')]
        index = {'index_version': 1, 'watermark': 8, 'embedding_model': 'test',
                 'catalog': default_catalog().to_dict()}
        self.current = True
        repo = SimpleNamespace(projections=SimpleNamespace(search_index=lambda *_: dict(index),
            search_index_is_current=lambda *_: self.current))
        def encode(texts):
            self.encodes.extend(texts)
            return [[1.0, float(len(text))] for text in texts]
        encoder = SimpleNamespace(identity='test', semantic=True, encode=encode)
        engine = RetrievalEngine(repo, encoder, SimpleNamespace(available=True))
        def rank(_engine, project, query, mode, kinds, target, scope, pg_scope, platform, selected, catalog, vector):
            self.calls.append({'query': query, 'scope': deepcopy(scope), 'vector': vector, 'kinds': kinds})
            rows = self.base if query == '美团' else self.related
            return deepcopy([item for item in rows if item['kind'] in kinds][:target]), len(rows), True
        return engine, rank

    def expansion(self):
        return {'terms': ['urn:Platform'], 'labels': ['互联网平台'], 'subclasses': [],
                'type_iris': ['urn:Platform'], 'literal_terms': [], 'entity_terms': ['urn:Platform'],
                'entity_matches': [{'id': 'meituan', 'version_id': 'meituan-v1', 'text': '美团',
                    'matched_name': '美团', 'match_kind': 'name', 'type': 'urn:Platform', 'type_label': '互联网平台'}]}

    def test_entity_expansion_preserves_original_query_and_keeps_anchor_and_most_direct_hits(self):
        engine, rank = self.harness()
        scope = {'filters': {'field': 'region', 'op': 'eq', 'value': '华东'},
                 'valid_at': '2025-01-01', '_ontology_expansion': self.expansion()}
        # This native scope is supported; patch the actual engine ranking seam.
        scope.pop('filters')
        with patch.object(RetrievalEngine, '_rank_current', rank), patch(
                'knowledge_service.services.scoped_ranking.ontology_type_rows', return_value=[row('typed-extra')]):
            result = engine.search('p', '美团', retrieval_mode='semantic', scope=scope,
                content_channels=['entity', 'chunk'], content_k={'entity': 5, 'chunk': 5},
                query_vector_cache={'lock': Lock()})
        self.assertEqual([call['query'] for call in self.calls[:2]], ['美团', '美团'])
        entities = [item['id'] for item in result['hits'] if item['kind'] == 'entity']
        chunks = [item['id'] for item in result['hits'] if item['kind'] == 'chunk']
        self.assertIn('meituan', entities)
        self.assertEqual(len(set(entities) & {item['id'] for item in self.base}), 4)
        self.assertEqual(len(entities), 5)
        self.assertIn('related-source', chunks)
        self.assertEqual(len(chunks), 5)
        self.assertEqual(result['ontology_expansion']['used_hits'], 2)
        self.assertEqual(result['ontology_expansion']['entity_matches'], self.expansion()['entity_matches'])
        self.assertEqual(len(set(self.encodes)), 2, 'different ranking queries need different vectors')

    def test_budget_one_protects_an_available_direct_result(self):
        engine, rank = self.harness()
        with patch.object(RetrievalEngine, '_rank_current', rank), patch(
                'knowledge_service.services.scoped_ranking.ontology_type_rows', return_value=[row('typed-extra')]):
            result = engine.search('p', '美团', retrieval_mode='keyword', scope={'_ontology_expansion': self.expansion()},
                content_channels=['entity'], content_k={'entity': 1})
        self.assertEqual([item['id'] for item in result['hits']], ['base-0'])
        self.assertEqual(result['ontology_expansion']['used_hits'], 0)
        self.assertEqual(self.encodes, [])

    def test_empty_base_can_use_related_hits_without_exceeding_quota(self):
        engine, rank = self.harness(); self.base = []
        with patch.object(RetrievalEngine, '_rank_current', rank), patch(
                'knowledge_service.services.scoped_ranking.ontology_type_rows', return_value=[row('typed-extra')]):
            result = engine.search('p', '美团', retrieval_mode='keyword', scope={'_ontology_expansion': self.expansion()},
                content_channels=['chunk'], content_k={'chunk': 1})
        self.assertEqual([item['id'] for item in result['hits']], ['related-source'])
        self.assertEqual(result['ontology_expansion']['used_hits'], 1)

    def test_disabled_expansion_keeps_original_single_query_path(self):
        engine, rank = self.harness()
        with patch.object(RetrievalEngine, '_rank_current', rank), patch(
                'knowledge_service.services.scoped_ranking.ontology_type_rows', side_effect=AssertionError('off reads type channel')):
            result = engine.search('p', '美团', retrieval_mode='semantic', scope={}, content_channels=['entity'])
        self.assertEqual([call['query'] for call in self.calls], ['美团'])
        self.assertNotIn('ontology_expansion', result)
        self.assertEqual(self.encodes, ['美团'])

    def test_vector_cache_is_keyed_by_query_across_shared_platform_workers(self):
        engine, _rank = self.harness(); cache = {'lock': Lock()}
        first = cached_query_vector(engine.encoder, '美团', cache)
        second = cached_query_vector(engine.encoder, '美团 互联网平台', cache)
        self.assertNotEqual(first, second)
        self.assertEqual(cached_query_vector(engine.encoder, '美团', cache), first)
        self.assertEqual(self.encodes, ['美团', '美团 互联网平台'])


class EntityExpansionServiceTest(TestCase):
    def test_search_scope_is_used_to_resolve_entity_mentions_and_public_path(self):
        from knowledge_service.services.service import KnowledgeService
        from knowledge_service.repository.scoped_reads import ScopedReads
        turtle = '@prefix owl: <http://www.w3.org/2002/07/owl#> . <urn:Platform> a owl:Class .'
        repository = SimpleNamespace(list_ontologies=lambda _: [{'id': 'published', 'turtle': turtle}])
        service = KnowledgeService(repository, SimpleNamespace(identity='test', semantic=False))
        scope = {'applicability': {'platforms': {'mode': 'specific', 'values': ['美团']}}, 'valid_at': '2025-01-01'}
        entity = {**row('meituan'), 'text': '美团', 'matched_name': '美团', 'match_kind': 'name'}
        with patch.object(ScopedReads, 'mentioned_entities', create=True, return_value=[entity]) as mentions:
            expanded = service.ontology_expansion('p', '美团直播', True, scope=scope)
        self.assertEqual(mentions.call_args.args, ('p', scope, '美团直播'))
        self.assertEqual(expanded['entity_terms'], ['urn:Platform'])
        self.assertEqual(expanded['entity_matches'][0]['text'], '美团')

    def test_no_date_uses_current_time_only_for_entity_seed_qualification(self):
        from knowledge_service.services.service import KnowledgeService
        from knowledge_service.repository.scoped_reads import ScopedReads
        turtle = '@prefix owl: <http://www.w3.org/2002/07/owl#> . <urn:Platform> a owl:Class .'
        service = KnowledgeService(SimpleNamespace(list_ontologies=lambda _: [{'id': 'o', 'turtle': turtle}]), SimpleNamespace())
        with patch.object(ScopedReads, 'mentioned_entities', return_value=[]) as mentions, patch(
                'knowledge_service.services.service.utc_now', return_value='2026-10-27T00:00:00Z'):
            service.ontology_expansion('p', '美团', True, scope={})
        self.assertEqual(mentions.call_args.args[1]['valid_at'], '2026-10-27T00:00:00Z')

    def test_explicit_platform_groups_aggregate_actual_supplements_after_deduplication(self):
        from knowledge_service.services.service import KnowledgeService
        service = KnowledgeService(SimpleNamespace(), SimpleNamespace(identity='test', semantic=False))
        expansion = {**EntityExpansionRetrievalTest().expansion(), 'has_hierarchy': True, 'matched': True}
        def retrieve(_engine, _project, _query, **options):
            platform = options['scope'].get('applicability', {}).get('platforms', {}).get('values', [None])[0]
            if options.get('platform_match') == 'all_only':
                platform = None
            rows = [{**row('extra-'+str(i)), 'retrieval_channel': 'ontology_entity_type'} for i in range(2)] if platform == 'B' else []
            return {'hits': rows, 'candidate_count': len(rows), 'complete': True, '_backend': 'milvus',
                    'backends': {}, 'ontology_expansion': {**expansion, 'type_hits': len(rows), 'used_hits': len(rows)}}
        with patch.object(service, 'ontology_expansion', return_value=expansion), patch(
                'knowledge_service.services.service.RetrievalEngine.search', retrieve), patch(
                'knowledge_service.services.retrieval_context.enrich_source_context'):
            result = service.search('p', {'query': '美团', 'ontology_context': False, 'ontology_expansion': True,
                'applicability': {'platforms': {'mode': 'specific', 'values': ['A', 'B']}}})
        self.assertEqual(result['ontology_expansion']['used_hits'], 2)
        self.assertEqual(result['ontology_expansion']['type_hits'], 2)
