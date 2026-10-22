"""Current search ranks in Milvus; PG reads only the bounded returned IDs."""
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from knowledge_service.domain.search_filter_catalog import FieldSpec, FilterCatalog
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.retrieval import RetrievalEngine


class NativePageRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.encoder = SimpleNamespace(identity='test-model', semantic=True,
                                       encode=lambda text: [[1, 0, 0, 0] for _ in text])
        self.catalog = FilterCatalog(1, (FieldSpec('metadata.amount', 'int64', column_name='test_amount_int64'),))
        self.index = {'index_version': 2, 'catalog': self.catalog.to_dict(),
                      'embedding_model': 'test-model', 'dim': 4, 'watermark': 9}
        self.calls, self.reads = [], []
        self.rows = [{'id': 'a', 'version_id': 'a-v1', 'kind': 'entity',
                      'text': '退款', 'metadata': {'amount': 9007199254740991}}]
        self.current = True
        class Repo:
            @contextmanager
            def independent_read_view(inner):
                yield inner
            def query(inner, *_args, **_kwargs):
                raise AssertionError('full candidate reads are forbidden')
        self.repo = Repo()
        self.repo.projections = SimpleNamespace(
            search_index=lambda *_: dict(self.index),
            search_index_is_current=lambda *_: self.current)
        self.hits = [{'record_id': 'a', 'version_id': 'a-v1', 'score': .9}]
        def search(project, query, **options):
            self.calls.append((project, query, options))
            return self.hits[:options['k']]
        self.store = SimpleNamespace(available=True, search=search)
        def by_ids(_reader, project, scope, ids, *, kinds=None):
            self.reads.append((project, scope, ids, kinds))
            return [row for row in self.rows if row['id'] in ids]
        self.patch = patch.object(ScopedReads, 'by_ids', by_ids)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_page_filters_rank_natively_and_pg_recheck_uses_exact_canonical_ast(self):
        result = RetrievalEngine(self.repo, self.encoder, self.store).search(
            'project-a', '退款', retrieval_mode='keyword', content_channels=['entity'], k=1,
            scope={'filters': {'field': 'metadata.amount', 'op': 'gt',
                               'value': Decimal('9007199254740990.5')}})
        self.assertEqual([row['id'] for row in result['hits']], ['a'])
        self.assertEqual(self.calls[0][2]['index_version'], 2)
        self.assertEqual(self.reads[0][1]['filters'],
                         {'field': 'metadata.amount', 'op': 'gte', 'value': 9007199254740991})
        self.assertLessEqual(len(self.reads[0][2]), 16)
        self.assertFalse(result['degraded'])

    def test_no_index_does_not_transparently_fall_back_for_keyword(self):
        with self.assertRaisesRegex(RuntimeError, 'Milvus'):
            RetrievalEngine(self.repo, self.encoder).search('a', '退款', retrieval_mode='keyword')

    def test_rejected_hits_trigger_bounded_refill_and_exhausted_budget_is_explicit(self):
        self.hits = [{'record_id': f'stale-{i}', 'version_id': f'stale-v{i}', 'score': 1}
                     for i in range(600)]
        result = RetrievalEngine(self.repo, self.encoder, self.store).search(
            'a', '退款', retrieval_mode='keyword', content_channels=['entity'], k=1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['incomplete_reason'], 'authority_refill_budget')
        self.assertLessEqual(len(self.calls), 4)
        self.assertTrue(all(len(read[2]) <= 512 for read in self.reads))

    def test_index_switch_before_return_rejects_mixed_results(self):
        self.current = False
        with self.assertRaisesRegex(RuntimeError, '索引'):
            RetrievalEngine(self.repo, self.encoder, self.store).search(
                'a', '退款', retrieval_mode='keyword', content_channels=['entity'], k=1)


if __name__ == '__main__':
    unittest.main()
