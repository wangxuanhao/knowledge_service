"""Pure projection contracts; may run with unittest without a PostgreSQL session."""
import unittest

from knowledge_service.domain import search_projection as projection
from knowledge_service.domain.search_filter_catalog import default_catalog


def attribute(**changes):
    return {'project_id': 'p', 'id': 'a', 'version_id': 'a-v1',
            'kind': 'attribute', 'type': 'https://example.test/description',
            'subject_id': 'platform', 'text': '属性说明', 'value': '只在具体值中出现的退款条件',
            'datatype': 'http://www.w3.org/2001/XMLSchema#string',
            'metadata': {}, 'valid_from': None, 'valid_until': None,
            **changes}


class AttributeProjectionTests(unittest.TestCase):
    def test_context_window_multiplication_is_bounded_before_first_row(self):
        contexts=tuple(dict(id='c'+str(i),version=1) for i in range(1000))
        value=projection.ProjectionInput(attribute(value='paragraph '*4000),source_contexts=contexts)
        with self.assertRaisesRegex(ValueError,'预算'):
            next(projection.build_projections(value,default_catalog(),index_version=1))

    def rows(self, record, **kwargs):
        builder = getattr(projection, 'build_projections', None)
        self.assertTrue(callable(builder), 'multi-window attribute projection must exist')
        return list(builder(projection.ProjectionInput(record, **kwargs), default_catalog(), index_version=1))

    def test_attribute_value_is_searchable_without_display_text_copy(self):
        result = projection.build_projection(projection.ProjectionInput(attribute()), default_catalog(), index_version=1)
        self.assertIsNotNone(result, 'accepted attributes must be indexed')
        self.assertIn('退款条件', result['text'])
        self.assertEqual(result['record_id'], 'a')

    def test_long_paragraph_tail_has_a_window_with_exact_offsets(self):
        value = '第一段说明。\n' * 1800 + '尾部独有词：退款审核条件'
        rows = self.rows(attribute(value=value), owner_label='美团平台', property_label='业务说明')
        self.assertGreater(len(rows), 1)
        self.assertEqual(len({row['vector_id'] for row in rows}), len(rows))
        self.assertTrue(any('退款审核条件' in row['text'] for row in rows))
        for row in rows:
            self.assertIn(value[row['window_start']:row['window_end']], row['text'])
            self.assertLessEqual(len(row['text'].encode('utf-8')), 65535)
            self.assertEqual(row['version_id'], 'a-v1')
            self.assertEqual(row['owner_id'], 'platform')

    def test_source_contexts_do_not_mix_time_and_platform(self):
        record = attribute(kind='entity', id='platform', version_id='e-v1', type='Platform',
                           text='美团平台', metadata={'identity_policy': 'reference'})
        contexts = ({'id': 'c1', 'version': 2, 'valid_from': '2024-01-01',
                     'valid_until': '2024-12-01', 'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['A']}}}},
                    {'id': 'c2', 'version': 4, 'valid_from': '2025-01-01',
                     'valid_until': None, 'metadata': {'applicability': {'platforms': {'mode': 'specific', 'values': ['B']}}}})
        rows = self.rows(record, source_contexts=contexts)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['platform_values'] for row in rows], [['A'], ['B']])
        self.assertNotEqual(rows[0]['valid_from_us'], rows[1]['valid_from_us'])
        self.assertEqual([row['support_context_id'] for row in rows], ['c1', 'c2'])

    def test_reference_entity_without_support_cannot_create_timeless_hit(self):
        self.assertEqual(self.rows(attribute(kind='entity', metadata={'identity_policy': 'reference'})), [])

    def test_deleted_or_quarantined_attribute_is_not_indexed(self):
        for flag in ('_deleted', '_audit', '_quarantined'):
            self.assertEqual(self.rows(attribute(metadata={flag: True})), [])


if __name__ == '__main__':
    unittest.main()
