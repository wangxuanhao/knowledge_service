"""The shared index contains page predicates and two build isolation columns."""
from decimal import Decimal
import unittest

from knowledge_service.domain.search_filter_catalog import FieldSpec, FilterCatalog, default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection


def record(**changes):
    return {'project_id': 'p', 'id': 'r', 'version_id': 'v', 'kind': 'relation',
            'text': '正文', 'superseded_at': None, 'metadata': {}, **changes}


class SearchProjectionTests(unittest.TestCase):
    def build(self, value=None, catalog=None, index_version=1):
        return build_projection(ProjectionInput(value or record()), catalog or default_catalog(),
                                index_version=index_version)

    def test_exact_21_column_contract_has_no_endpoint_or_control_copies(self):
        row = self.build()
        self.assertEqual(set(row), {c.name for c in default_catalog().column_defs()} - {'embedding', 'sparse'})
        self.assertEqual(len(row), 19)
        self.assertTrue(all(not isinstance(v, dict) for v in row.values()))
        self.assertEqual(row['record_id'], 'r')
        self.assertEqual(row['text'], '正文')

    def test_vector_identity_is_deterministic_and_isolates_project_build_and_pg_version(self):
        baseline = self.build()
        self.assertEqual(baseline['vector_id'], self.build()['vector_id'])
        self.assertEqual(len(baseline['vector_id']), 64)
        for changed in (self.build(index_version=2), self.build(record(project_id='other')),
                        self.build(record(version_id='v2'))):
            self.assertNotEqual(baseline['vector_id'], changed['vector_id'])
        for invalid in (None, 0, -1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.build(index_version=invalid)

    def test_only_current_searchable_visible_rows_are_projected(self):
        for changes in ({'kind': 'attribute'}, {'kind': 'document'},
                        {'superseded_at': '2026-10-21T00:00:00Z'},
                        {'metadata': {'_deleted': 'yes'}}, {'metadata': {'_audit': True}}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.build(record(**changes)))

    def test_missing_and_null_presence_share_one_array(self):
        catalog = FilterCatalog(1, (FieldSpec('metadata.status', 'varchar', column_name='test_status_varchar'),
                                    FieldSpec('metadata.price', 'double', column_name='test_price_double')))
        row = self.build(record(metadata={'status': None}, valid_until=None), catalog)
        self.assertIn('metadata.status', row['filter_present'])
        self.assertNotIn('metadata.price', row['filter_present'])
        self.assertIn('valid_until', row['filter_present'])
        self.assertIsNone(row[catalog.resolve('status').column_name])

    def test_decimal_native_conversion_and_utf8_validation_precede_encoding(self):
        catalog = FilterCatalog(1, (FieldSpec('metadata.price', 'double', column_name='test_price_double'),))
        row = self.build(record(metadata={'price': Decimal('0.1')}), catalog)
        self.assertEqual(row[catalog.resolve('price').column_name], 0.1)
        with self.assertRaises(ValueError):
            self.build(record(metadata={'price': Decimal('9007199254740990.5')}), catalog)
        with self.assertRaises(ValueError):
            self.build(record(text='中' * 21846))

    def test_false_and_empty_nonstring_text_are_rejected(self):
        for value in (False, 0, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.build(record(text=value))

    def test_time_exact_microseconds_unknown_ontology_and_chunk_owner(self):
        row = self.build(record(valid_from='1969-12-31T23:59:59.999999Z', ontology_id='missing', source_id='stale'))
        self.assertEqual(row['valid_from_us'], -1)
        self.assertIsNone(row['ontology_id'])
        self.assertIsNone(row['chunk_owner_id'])
        self.assertNotIn('source_id', row['filter_present'])
        chunk = self.build(record(kind='chunk', source_id='doc'))
        self.assertEqual(chunk['chunk_owner_id'], 'doc')
        self.assertIn('source_id', chunk['filter_present'])


if __name__ == '__main__':
    unittest.main()
