"""Pure entity matching regressions; run directly without PostgreSQL fixtures."""
import unittest
from types import SimpleNamespace

from knowledge_service.services.entity_resolution import EntityResolver
from knowledge_service.services.governance import Governance
from knowledge_service.services.service import KnowledgeService
from knowledge_service.services.ingest_retry import classify_error, public_failure, retry_delay


def entity(record_id='person', **changes):
    return {'id': record_id, 'version': 1, 'version_id': record_id + ':v1',
            'kind': 'entity', 'type': 'Person', 'text': '张三',
            'valid_from': None, 'valid_until': None, 'metadata': {}, **changes}


class Repository:
    def __init__(self, rows):
        self.rows = rows

    def current_records(self, project, **kwargs):
        return self.rows


class Search:
    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error
        self.requests = []

    def search(self, project, text, **kwargs):
        self.requests.append((project, text, kwargs))
        if self.error:
            raise self.error
        return {'hits': self.hits}


class EntityMatchingContracts(unittest.TestCase):
    def resolver(self, rows, search):
        resolver = EntityResolver(Repository(rows), None)
        resolver.retrieval = search
        return resolver

    def test_semantic_resolution_uses_milvus_hits_without_postgres_vectors(self):
        compatible = entity()
        search = Search([{**compatible, 'score': .95}])
        outcome = self.resolver([compatible, entity('company', type='Company')], search).resolve(
            'p', entity('mention', text='张先生'), review_threshold=.7, merge_threshold=.9)
        self.assertEqual((outcome.status, outcome.canonical_id), ('aligned', 'person'))
        kwargs = search.requests[0][2]
        self.assertEqual(kwargs['retrieval_mode'], 'semantic')
        self.assertEqual(kwargs['content_channels'], ['entity'])
        self.assertEqual(kwargs['scope']['_candidate_ids'], ['person'])

    def test_exact_alias_and_empty_compatible_set_do_not_require_milvus(self):
        search = Search(error=RuntimeError('Milvus unavailable'))
        resolver = self.resolver([entity(metadata={'aliases': ['老张']})], search)
        exact = resolver.resolve('p', entity('mention', text='老张'), review_threshold=.7, merge_threshold=.9)
        empty = resolver.resolve('p', entity('mention', type='Company'), review_threshold=.7, merge_threshold=.9)
        self.assertEqual(exact.status, 'aligned')
        self.assertEqual(empty.reason, 'no_compatible_candidate')
        self.assertEqual(search.requests, [])

    def test_type_scope_time_and_deletion_qualification_precedes_recall(self):
        rows = [entity(), entity('different-type', type='Company'),
                entity('different-start', valid_from='2020-01-01'),
                entity('different-end', valid_until='2030-01-01'),
                entity('different-scope', metadata={'applicability': {'platforms': {'mode': 'specific', 'values': ['京东']}}}),
                entity('deleted', metadata={'_deleted': True})]
        search = Search([{**rows[0], 'score': .95}])
        self.resolver(rows, search).resolve('p', entity('mention', text='张先生'),
                                           review_threshold=.7, merge_threshold=.9)
        self.assertEqual(search.requests[0][2]['scope']['_candidate_ids'], ['person'])

    def test_service_resolver_uses_the_current_milvus_connection(self):
        first, second = object(), object()
        service = KnowledgeService(Repository([]), None, first)
        self.assertIs(service.entity_resolver().retrieval.milvus, first)
        service.milvus_store = second
        self.assertIs(service.entity_resolver().retrieval.milvus, second)

    def test_temporary_milvus_failure_has_a_bounded_retryable_domain_code(self):
        search = Search(error=RuntimeError('secret connection detail'))
        with self.assertRaises(RuntimeError) as raised:
            self.resolver([entity()], search).resolve('p', entity('mention', text='张先生'),
                review_threshold=.7, merge_threshold=.9)
        failure = classify_error(raised.exception)
        self.assertEqual(failure.code, 'entity_resolution_unavailable')
        self.assertTrue(failure.retryable)
        self.assertIn('Milvus', failure.message)
        self.assertNotIn('secret', failure.message)
        persisted = public_failure({'code': failure.code, 'message': 'secret raw failure'})
        self.assertEqual(persisted['message'], failure.message)
        self.assertIsNone(retry_delay(5, jitter=lambda: 0))

    def test_dimension_and_encoder_configuration_failures_require_manual_repair(self):
        dimension_error = RuntimeError('Milvus search failed')
        dimension_error.__cause__ = ValueError('invalid embedding dimension')
        dimension = Search(error=dimension_error)
        identity = Search(error=RuntimeError('encoder identity mismatch'))
        identity.encoder = SimpleNamespace(semantic=True, identity='new-model')
        identity.milvus = SimpleNamespace(encoder_identity='old-model')
        demo = Search(error=RuntimeError('non semantic model'))
        demo.encoder = SimpleNamespace(semantic=False)
        for search in (dimension, identity, demo):
            with self.subTest(search=search):
                with self.assertRaises(RuntimeError) as raised:
                    self.resolver([entity()], search).resolve('p', entity('mention', text='张先生'),
                        review_threshold=.7, merge_threshold=.9)
                failure = classify_error(raised.exception)
                self.assertEqual(failure.code, 'entity_resolution_configuration')
                self.assertFalse(failure.retryable)
                self.assertIn('Milvus', failure.message)

    def test_existing_candidates_with_no_milvus_raise_instead_of_separate(self):
        resolver = EntityResolver(Repository([entity()]), None)
        with self.assertRaisesRegex(RuntimeError, 'Milvus'):
            resolver.resolve('p', entity('mention', text='张先生'), review_threshold=.7, merge_threshold=.9)

    def test_missing_or_stale_milvus_versions_are_unavailable(self):
        for hits in ([], [{**entity(version_id='person:stale'), 'score': .99}],
                     [{**entity('outside'), 'score': .99}]):
            with self.subTest(hits=hits):
                resolver = self.resolver([entity()], Search(hits))
                with self.assertRaisesRegex(RuntimeError, 'Milvus'):
                    resolver.resolve('p', entity('mention', text='张先生'), review_threshold=.7, merge_threshold=.9)

    def test_semantic_review_and_low_bands_use_the_milvus_score(self):
        for score, status in ((.95, 'review'), (.8, 'review'), (.2, 'separate')):
            with self.subTest(score=score):
                resolver = self.resolver([entity()], Search([{**entity(), 'score': score}]))
                result = resolver.resolve('p', entity('mention', text='张先生', _auto_merge=False),
                                          review_threshold=.7, merge_threshold=.9)
                self.assertEqual(result.status, status)
                self.assertEqual(result.candidate_id, 'person')
                self.assertEqual(result.score, score)

    def test_governance_uses_authoritative_payload_and_preserves_scope(self):
        row = entity(metadata={'city': '北京'})
        search = Search([{**row, 'text': 'untrusted payload', 'metadata': {}, 'score': .82}])
        resolver = self.resolver([row], search)
        scopes = []
        service = SimpleNamespace(repository=resolver.repository,
                                  entity_resolver=lambda: resolver,
                                  scoped=lambda project, scope: scopes.append(scope) or [row])
        requested_scope = {'ontology_scope': 'ids', 'ontology_ids': ['ontology:v1'],
                           'valid_at': '2026-10-19'}
        result = Governance(service).resolve('p', '张先生', requested_scope, threshold=.7)
        self.assertEqual(result['candidates'][0]['text'], '张三')
        self.assertEqual(result['candidates'][0]['metadata'], {'city': '北京'})
        self.assertEqual(result['candidates'][0]['reasons'], ['milvus_embedding'])
        self.assertIn('milvus', result['backend'])
        self.assertNotIn('_include_embeddings', scopes[0])
        self.assertEqual(search.requests[0][2]['scope']['ontology_ids'], ['ontology:v1'])

    def test_governance_exact_duplicates_and_empty_scope_skip_vector_recall(self):
        search = Search(error=RuntimeError('Milvus unavailable'))
        rows = [entity(), entity('second')]
        resolver = self.resolver(rows, search)
        service = SimpleNamespace(repository=resolver.repository,
                                  entity_resolver=lambda: resolver,
                                  scoped=lambda project, scope: rows)
        self.assertEqual(Governance(service).resolve('p', '张三')['status'], 'exact_duplicates')
        rows.clear()
        self.assertEqual(Governance(service).resolve('p', '张先生')['candidates'], [])
        self.assertEqual(search.requests, [])

    def test_governance_scope_excluding_entities_has_no_vector_candidates(self):
        search = Search(error=RuntimeError('Milvus unavailable'))
        resolver = self.resolver([entity()], search)
        service = SimpleNamespace(repository=resolver.repository,
                                  entity_resolver=lambda: resolver,
                                  scoped=lambda project, scope: [entity()] if 'entity' in scope['kinds'] else [])
        result = Governance(service).resolve('p', '张先生', {'kinds': ['relation']})
        self.assertEqual(result['candidates'], [])
        self.assertEqual(search.requests, [])


if __name__ == '__main__':
    unittest.main()
