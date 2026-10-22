"""One shared collection; every RPC carries explicit project/build scope."""
import unittest
from unittest.mock import Mock
from pymilvus import DataType
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection
from knowledge_service.integrations.milvus_store import COLLECTION, MilvusStore, bounded_batches


class FlatMilvusStoreTests(unittest.TestCase):
    def test_schema_has_21_columns_vector_pk_and_readable_index_names(self):
        self.assertEqual(COLLECTION, 'knowledge_vectors')
        store = MilvusStore(dim=2)
        store._client = Mock()
        store._client.describe_index.return_value = {'state': 'Finished'}
        store._create_collection(catalog=default_catalog())
        call = store._client.create_collection.call_args
        self.assertEqual(call.args[0], COLLECTION)
        schema = call.kwargs['schema']
        self.assertEqual(len(schema.fields), 21)
        self.assertEqual([f.name for f in schema.fields if f.is_primary], ['vector_id'])
        self.assertFalse(schema.enable_dynamic_field)
        self.assertFalse(any(f.dtype == DataType.JSON for f in schema.fields))
        indexes = store._client.create_index.call_args.args[1]
        names = {p.index_name for p in indexes}
        self.assertIn('dense', names)
        self.assertIn('bm25', names)
        self.assertIn('filter_project_id', names)

    def test_batching_is_bounded_by_row_count_and_conservative_utf8_bytes(self):
        rows = [{'text': '中' * 10} for _ in range(5)]
        batches = list(bounded_batches(rows, max_rows=2, max_bytes=400))
        self.assertEqual(sum(map(len, batches)), 5)
        self.assertTrue(all(len(batch) <= 2 for batch in batches))
        with self.assertRaises(ValueError):
            list(bounded_batches([{'text': '中' * 1000}], max_bytes=400))

    def test_wrong_scope_and_raw_record_fail_before_rpc(self):
        store = MilvusStore(dim=2)
        store._client = Mock()
        with self.assertRaises(ValueError):
            store.upsert_rows([{'id': 'raw', 'metadata': {}}], project_id='p', index_version=1, catalog=default_catalog())
        row = build_projection(ProjectionInput({'id': 'r', 'kind': 'chunk', 'text': 'x',
                               'version_id': 'v', 'project_id': 'p'}), default_catalog(), index_version=1)
        row['embedding'] = [1., 0.]
        with self.assertRaises(ValueError):
            store.upsert_rows([row], project_id='other', index_version=1, catalog=default_catalog())
        store._client.upsert.assert_not_called()
        store._client.create_collection.assert_not_called()

    def test_delete_is_always_limited_to_project_and_build(self):
        store = MilvusStore(dim=2)
        store._client = Mock()
        store._client.delete.return_value = {'delete_count': 1}
        store.check_collection = Mock()
        store.delete_records(['r'], project_id='p', index_version=3, catalog=default_catalog())
        kwargs = store._client.delete.call_args.kwargs
        self.assertIn('project_id ==', kwargs['filter'])
        self.assertIn('index_version ==', kwargs['filter'])
        self.assertIn('record_id in', kwargs['filter'])
        self.assertEqual(kwargs['filter_params'], {'project': 'p', 'index': 3, 'records': ['r']})

    def test_sdk_iterator_receives_native_expr_params_without_dropping_scope(self):
        store = MilvusStore(dim=2)
        store._client = Mock()
        store._client.query_iterator.return_value.next.return_value = []
        params = {'project': 'p', 'index': 9}
        self.assertEqual(list(store._query_pages('project_id == {project} and index_version == {index}', params, ['record_id'])), [])
        kwargs = store._client.query_iterator.call_args.kwargs
        self.assertEqual(kwargs.get('expr_params'), params)
        self.assertNotIn('filter_params', kwargs)
        store._client.query_iterator.return_value.close.assert_called_once()

    def test_search_parameterization_retains_build_scope_for_every_mode(self):
        for mode in ('keyword', 'semantic', 'hybrid'):
            store = MilvusStore(dim=2)
            store._client = Mock()
            store._client.search.return_value = [[]]
            store._client.hybrid_search.return_value = [[]]
            store._load_if_needed = Mock()
            store.search('p', 'query', retrieval_mode=mode, query_vector=[1., 0.],
                         index_version=7, catalog=default_catalog())
            if mode == 'hybrid':
                requests = store._client.hybrid_search.call_args.kwargs['reqs']
                for request in requests:
                    self.assertIn('index_version ==', request.expr)
                    self.assertIn(7, request.expr_params.values())
            else:
                kwargs = store._client.search.call_args.kwargs
                self.assertIn('index_version ==', kwargs['filter'])
                self.assertIn(7, kwargs['filter_params'].values())


if __name__ == '__main__':
    unittest.main()
