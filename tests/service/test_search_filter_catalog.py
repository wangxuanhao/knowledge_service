"""Pure field-directory contracts; no database or Milvus process required."""
from dataclasses import FrozenInstanceError
from decimal import Decimal
import json
import unittest

from knowledge_service.domain import search_filter_catalog as catalog


class SearchFilterCatalogTests(unittest.TestCase):
    def test_page_manifest_ignores_metadata_and_keeps_fixed_business_columns(self):
        result = catalog.discover_page_catalog([
            {'metadata': {'region': '杭州', 'order': {'amount': Decimal('12.5')},
                          'active_stage': 'done', 'confidence': Decimal('0.9'),
                          'applicability': {'platforms': {'mode': 'all'}},
                          'tags': ['退款', '审核']}},
            {'metadata': {'order': {'amount': 15}, 'region': None}},
        ])
        self.assertEqual(result.fields, ())
        self.assertEqual(catalog.discover_page_catalog([]).fields, ())

    def test_nonindexed_metadata_does_not_block_build_for_mixed_or_lossy_values(self):
        result = catalog.discover_page_catalog([
            {'metadata': {'region': 'x', 'amount': Decimal('9007199254740990.5')}},
            {'metadata': {'region': True, 'amount': {'nested': [1, 2]}}}])
        self.assertEqual(result, catalog.default_catalog())
    def test_default_directory_is_explicit_and_immutable(self):
        directory = catalog.default_catalog()
        self.assertEqual(directory.revision, 1)
        self.assertEqual(directory.fields, ())
        with self.assertRaises(FrozenInstanceError):
            directory.revision = 2

    def test_stable_safe_names_and_fingerprints(self):
        a = catalog.FieldSpec('metadata.订单.状态', 'varchar', column_name='test_______varchar')
        self.assertRegex(a.column_name, r'^test_[A-Za-z0-9_]+$')
        self.assertFalse(hasattr(a, 'presence_column'))
        b = catalog.FieldSpec('metadata.count', 'int64', column_name='test_count_int64')
        self.assertNotEqual(b.column_name,
                            catalog.FieldSpec('metadata.count', 'varchar', column_name='test_count_varchar').column_name)
        self.assertEqual(catalog.FilterCatalog(2, [a, b]).fingerprint,
                         catalog.FilterCatalog(2, [b, a]).fingerprint)
        self.assertNotEqual(catalog.FilterCatalog(2, [a]).fingerprint,
                            catalog.FilterCatalog(3, [a]).fingerprint)
        self.assertEqual(catalog.FilterCatalog.from_dict(
            catalog.FilterCatalog(2, [a, b]).to_dict()).fields,
            catalog.FilterCatalog(2, [a, b]).fields)

    def test_display_name_is_optional_validated_and_part_of_fingerprint(self):
        plain = catalog.FieldSpec('metadata.title', 'varchar', column_name='test_title_varchar')
        named = catalog.FieldSpec('metadata.title', 'varchar', display_name='业务标题', column_name='test_title_varchar')
        self.assertIsNone(plain.display_name)
        self.assertEqual(plain.label, 'metadata.title')
        self.assertEqual(named.label, '业务标题')
        self.assertNotEqual(catalog.FilterCatalog(1, [plain]).fingerprint,
                            catalog.FilterCatalog(1, [named]).fingerprint)
        self.assertEqual(catalog.FilterCatalog.from_dict(
            catalog.FilterCatalog(1, [named]).to_dict()).fields, (named,))
        for value in ['', ' ' * 3, 'x' * 101, 1, False]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                catalog.FieldSpec('metadata.title', 'varchar', display_name=value, column_name='test_title_varchar')

    def test_invalid_directory_and_unknown_paths_fail_closed(self):
        for path in ['metadata', 'metadata.a[0]', 'metadata.a" or true', 'id']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                catalog.FieldSpec(path, 'varchar', column_name='test_declaration_66')
        for field_type in ['json', 'array_array', 'array_int64', 'object']:
            with self.subTest(field_type=field_type), self.assertRaises(ValueError):
                catalog.FieldSpec('metadata.a', field_type, column_name='test_a_value')
        a = catalog.FieldSpec('metadata.a', 'varchar', column_name='same_name')
        b = catalog.FieldSpec('metadata.b', 'varchar', column_name='same_name')
        for fields in [[a, a], [a, b]]:
            with self.assertRaises(ValueError):
                catalog.FilterCatalog(1, fields)
        with self.assertRaises(ValueError):
            catalog.default_catalog().resolve('metadata.unregistered')
        with self.assertRaises(ValueError):
            catalog.FieldSpec('metadata.a', 'varchar', column_name='project_id')

    def test_expansion_budget_comes_from_actual_native_columns(self):
        directory = catalog.default_catalog()
        columns = directory.column_defs()
        self.assertEqual(len(columns), 21)
        self.assertEqual({column.name for column in columns}, {
            'vector_id', 'index_version', 'version_id', 'project_id', 'record_id', 'kind', 'type', 'ontology_id',
            'text', 'embedding', 'sparse', 'platform_mode', 'platform_values',
            'business_mode', 'business_values', 'audience_mode', 'audience_values',
            'valid_from_us', 'valid_until_us', 'chunk_owner_id', 'filter_present'})
        self.assertEqual(len({column.name for column in columns}), len(columns))
        self.assertFalse(any(column.field_type in {'json', 'dynamic'} for column in columns))
        fields = [catalog.FieldSpec('metadata.f' + str(index), 'bool', column_name='test_field_' + str(index)) for index in range(235)]
        self.assertEqual(len(catalog.FilterCatalog(1, fields).column_defs()), 256)
        with self.assertRaisesRegex(ValueError, '256'):
            catalog.FilterCatalog(1, fields + [catalog.FieldSpec('metadata.extra', 'bool', column_name='test_extra_bool')]).column_defs()

    def test_native_values_preserve_missing_null_and_enforce_capacity(self):
        field = catalog.FieldSpec('metadata.order.status', 'varchar', max_length=6, column_name='test_order_status_varchar')
        self.assertEqual(field.project({'metadata': {}}),
                         {field.column_name: None})
        self.assertEqual(field.project({'metadata': {'order': {'status': None}}}),
                         {field.column_name: None})
        with self.assertRaises(ValueError):
            field.project({'metadata': {}}, prefix='subject_')
        self.assertEqual(field.project({'metadata': {'order': {'status': '知识'}}})[field.column_name], '知识')
        with self.assertRaises(ValueError):
            field.project({'metadata': {'order': {'status': '知识图'}}})
        array = catalog.FieldSpec('metadata.tags', 'array_varchar', max_length=6, max_capacity=2, column_name='test_tags_array_varchar')
        self.assertEqual(array.native_value(['a', '知识']), ['a', '知识'])
        for value in [['a', 1], [['a']], [{}], ['a', 'b', 'c']]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                array.native_value(value)

    def test_raw_decimal_numeric_values_are_checked_before_float_conversion(self):
        field = catalog.FieldSpec('metadata.confidence', 'double', column_name='test_confidence_double')
        self.assertEqual(field.native_value(Decimal('0.1')), 0.1)
        for value in [Decimal('9007199254740990.5'), Decimal('0.100000000000000005'),
                      Decimal('9007199254740993'), float('nan'), True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                field.native_value(value)
        integer = catalog.FieldSpec('metadata.n', 'int64', column_name='test_n_int64')
        self.assertEqual(integer.native_value(Decimal('9223372036854775807')), 2**63 - 1)
        for value in [Decimal('1.5'), 2**63, True, '1']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                integer.native_value(value)
        parsed = catalog.parse_filter_json('{"field":"n","op":"lt","value":9007199254740990.5}')
        self.assertIsInstance(parsed['value'], Decimal)
        self.assertEqual(str(parsed['value']), '9007199254740990.5')
        self.assertEqual(json.loads('{"value":0.1}', parse_float=Decimal)['value'], Decimal('0.1'))


if __name__ == '__main__':
    unittest.main()
