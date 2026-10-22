"""Typed, parameterized filters for the current page's flat row scope."""
from decimal import Decimal
import json
import unittest

from knowledge_service.domain.search_filter_catalog import FieldSpec, FilterCatalog
from knowledge_service.integrations.milvus_filters import compile_filter, compile_scope, datetime_to_us
from knowledge_service.utils.filters import matches_filter, validate_filter


class MilvusBusinessFilterTests(unittest.TestCase):
    def setUp(self):
        self.catalog = FilterCatalog(1, [FieldSpec('metadata.' + name, kind, column_name='test_' + name) for name, kind in [
            ('text', 'varchar'), ('flag', 'bool'), ('n', 'int64'),
            ('score', 'double'), ('tags', 'array_varchar')]])

    def compile(self, field, op, value, **kwargs):
        return compile_filter({'field': field, 'op': op, 'value': value}, self.catalog, **kwargs)

    @staticmethod
    def native_eval(result, row):
        expression = result.expr.replace('true', 'True').replace('false', 'False').replace('null', 'None')
        for name in result.filter_params:
            expression = expression.replace('{' + name + '}', 'params[' + repr(name) + ']')
        return eval(expression, {'__builtins__': {}}, {
            'params': result.filter_params, 'array_contains': lambda values, item: item in values, **row})

    def test_values_only_enter_sdk_parameters(self):
        value = '知识"\\%_ or visible == false'
        result = self.compile('text', 'eq', value)
        self.assertNotIn(value, result.expr)
        self.assertEqual(list(result.filter_params.values()), ['metadata.text', value])
        field = self.catalog.resolve('text')
        self.assertIn('array_contains(filter_present, {p0})', result.expr)
        self.assertIn(field.column_name + ' == {p1}', result.expr)
        self.assertEqual(result.canonical_filter['field'], 'metadata.text')
        with self.assertRaises(ValueError):
            self.compile('text', 'eq', 'a', prefix='subject_ or true ')
        for prefix in ['subject_', 'object_']:
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                self.compile('text', 'eq', 'a', prefix=prefix)

    def test_presence_and_nullable_value_are_distinct(self):
        for op, target, expected in [('eq', None, [False, True, False]),
                                      ('ne', None, [False, False, True]),
                                      ('ne', 'x', [False, True, False]),
                                      ('exists', False, [True, False, False])]:
            result = self.compile('text', op, target)
            rows = [{'metadata': {}}, {'metadata': {'text': None}}, {'metadata': {'text': 'x'}}]
            self.assertEqual([matches_filter(row, result.canonical_filter) for row in rows], expected)
            self.assertIn('array_contains(filter_present,', result.expr)
            column = self.catalog.resolve('text').column_name
            native_rows = [{column: None, 'filter_present': []},
                           {column: None, 'filter_present': ['metadata.text']},
                           {column: 'x', 'filter_present': ['metadata.text']}]
            self.assertEqual([self.native_eval(result, row) for row in native_rows], expected)
        self.assertIn(' is null', self.compile('text', 'eq', None).expr)
        self.assertIn(' is null or not ', self.compile('text', 'ne', 'x').expr)

    def test_int64_fractional_comparison_uses_exact_integer_boundary(self):
        cases = [('gt', '9007199254740990.5', 'gte', 9007199254740991),
                 ('gte', '-1.5', 'gte', -1), ('lt', '-1.5', 'lte', -2),
                 ('lte', '9007199254740990.5', 'lte', 9007199254740990)]
        for op, target, canonical_op, boundary in cases:
            with self.subTest(op=op, target=target):
                result = self.compile('n', op, Decimal(target))
                self.assertEqual(result.canonical_filter, {
                    'field': 'metadata.n', 'op': canonical_op, 'value': boundary})
                self.assertEqual(list(result.filter_params.values()), ['metadata.n', boundary])
                validate_filter(result.canonical_filter)
                json.dumps(result.canonical_filter, allow_nan=False)

    def test_integer_bounds_and_fractional_equality_canonicalize_without_decimal(self):
        for op, target, expected in [('lt', Decimal('-9223372036854775808'), False),
                                      ('lte', Decimal('9223372036854775808.5'), True),
                                      ('eq', Decimal('1.5'), False),
                                      ('ne', Decimal('1.5'), True),
                                      ('gt', Decimal('9223372036854775807'), False)]:
            with self.subTest(op=op, target=target):
                result = self.compile('n', op, target)
                self.assertEqual(matches_filter({'metadata': {'n': 1}}, result.canonical_filter), expected)
                self.assertFalse(matches_filter({'metadata': {}}, result.canonical_filter))
                json.dumps(result.canonical_filter, allow_nan=False)
        result = self.compile('n', 'eq', Decimal('-9223372036854775808'))
        self.assertNotIn('-9223372036854775808', result.expr)
        self.assertTrue(matches_filter({'metadata': {'n': -2**63}}, result.canonical_filter))

    def test_double_checks_raw_decimal_and_simplifies_outside_domain(self):
        for value in [Decimal('9007199254740990.5'), Decimal('0.100000000000000005')]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'DOUBLE'):
                self.compile('score', 'eq', value)
        result = self.compile('score', 'eq', Decimal('0.1'))
        self.assertEqual(result.canonical_filter['value'], 0.1)
        for op, expected in [('lt', True), ('eq', False), ('gt', False), ('ne', True)]:
            result = self.compile('score', op, Decimal('9007199254740993'))
            self.assertEqual(matches_filter({'metadata': {'score': 0.1}}, result.canonical_filter), expected)
            self.assertNotIn(9007199254740993, result.filter_params.values())

    def test_extremely_precise_domain_boundary_is_not_rounded_by_decimal_context(self):
        target = Decimal('9007199254740992.00000000000000000000000001')
        result = self.compile('score', 'lt', target)
        self.assertTrue(matches_filter({'metadata': {'score': 2**53}}, result.canonical_filter))
        self.assertEqual(result.canonical_filter, {'field': 'metadata.score', 'op': 'ne', 'value': None})

    def test_integer_native_expression_and_canonical_filter_match_decimal_oracle(self):
        numbers = [-2**63, -2**63 + 1, -2**53 - 1, -2, -1, 0, 1, 2,
                   2**53 - 2, 2**53 - 1, 2**53, 2**63 - 2, 2**63 - 1]
        targets = [Decimal(str(value)) for value in numbers]
        targets += [Decimal('-9223372036854775808.5'), Decimal('9223372036854775807.5'),
                    Decimal('9007199254740990.5'), Decimal('-1.5'), Decimal('1.5'), Decimal('1e100')]
        column = self.catalog.resolve('n').column_name
        operators = {'eq': lambda a, b: a == b, 'ne': lambda a, b: a != b,
                     'gt': lambda a, b: a > b, 'gte': lambda a, b: a >= b,
                     'lt': lambda a, b: a < b, 'lte': lambda a, b: a <= b}
        for op, predicate in operators.items():
            for target in targets:
                result = self.compile('n', op, target)
                for value in numbers:
                    with self.subTest(op=op, target=str(target), value=value):
                        expected = predicate(Decimal(value), target)
                        self.assertEqual(matches_filter({'metadata': {'n': value}}, result.canonical_filter), expected)
                        self.assertEqual(self.native_eval(result, {
                            column: value, 'filter_present': ['metadata.n']}), expected)

    def test_time_contains_matches_normalized_public_time_semantics(self):
        result = self.compile('valid_from', 'contains', '2026-10-21')
        self.assertEqual(list(result.filter_params.values()), ['valid_from', datetime_to_us('2026-10-21')])
        self.assertTrue(matches_filter({'valid_from': '2026-10-21T00:00:00Z'}, result.canonical_filter))
        for op, value in [('exists', True), ('eq', '2026-10-21')]:
            with self.subTest(op=op), self.assertRaises(ValueError):
                self.compile('recorded_at', op, value)

    def test_literal_string_contains_and_ordered_array_equality(self):
        value = '知识\\%_'
        result = self.compile('text', 'contains', value)
        self.assertEqual(list(result.filter_params.values()), ['metadata.text', '%知识\\\\\\%\\_%'])
        self.assertIn(' like {p1}', result.expr)
        for target in [[], ['a', 'b'], ['b', 'a'], ['a', 'a']]:
            result = self.compile('tags', 'eq', target)
            self.assertIn('array_length(', result.expr)
            self.assertTrue(matches_filter({'metadata': {'tags': target}}, result.canonical_filter))
            if len(target) == 2 and target[0] != target[1]:
                self.assertFalse(matches_filter({'metadata': {'tags': target[::-1]}}, result.canonical_filter))
        self.assertIn('array_contains(', self.compile('tags', 'contains', 'a').expr)
        self.assertIn(' or ', self.compile('tags', 'in', [['a'], ['b']]).expr)

    def test_string_query_targets_obey_raw_utf8_capacity_for_every_operator(self):
        directory = FilterCatalog(1, [FieldSpec('metadata.text', 'varchar', max_length=6, column_name='test_text_varchar'),
                                       FieldSpec('metadata.tags', 'array_varchar', max_length=6, column_name='test_tags_array_varchar')])
        for op in ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'in']:
            target = ['知识图'] if op == 'in' else '知识图'
            with self.subTest(op=op), self.assertRaises(ValueError):
                compile_filter({'field': 'text', 'op': op, 'value': target}, directory)
        with self.assertRaises(ValueError):
            compile_filter({'field': 'tags', 'op': 'contains', 'value': '知识图'}, directory)
        for field in ['id', 'kind', 'type', 'source_id']:
            with self.subTest(field=field), self.assertRaises(ValueError):
                compile_filter({'field': field, 'op': 'eq', 'value': '😀' * 201}, directory)
        for target in ['知识', '%_\\', 'a' * 6]:
            result = compile_filter({'field': 'text', 'op': 'contains', 'value': target}, directory)
            self.assertEqual(result.canonical_filter['value'], target)
        result = compile_filter({'field': 'text', 'op': 'contains', 'value': '%_\\'}, directory)
        self.assertGreater(len(result.filter_params['p1'].encode('utf-8')), 6)

    def test_attribute_and_relation_compile_only_current_row_page_scope(self):
        result = compile_scope('p', {}, self.catalog, kinds=['attribute'])
        self.assertTrue(self.native_eval(result, {'project_id': 'p', 'kind': 'attribute'}))
        self.assertFalse(self.native_eval(result, {'project_id': 'p', 'kind': 'relation'}))
        for kind in ['relation', 'attribute']:
            expression = compile_scope('p', {}, self.catalog, kinds=[kind]).expr
            for forbidden in ['subject_', 'object_', 'active', 'visible', 'projection_seq', 'schema_version']:
                self.assertNotIn(forbidden, expression)

    def test_groups_share_unique_parameter_names_and_reject_structures(self):
        ast = {'and': [{'field': 'n', 'op': 'gt', 'value': 1},
                       {'or': [{'field': 'flag', 'op': 'eq', 'value': True},
                               {'field': 'text', 'op': 'eq', 'value': 'a'}]}]}
        result = compile_filter(ast, self.catalog)
        self.assertEqual(set(result.filter_params), {'p0', 'p1', 'p2', 'p3', 'p4', 'p5'})
        for field, op, target in [('text', 'eq', {}), ('tags', 'eq', [['a']]),
                                   ('tags', 'eq', ['a', 1]), ('tags', 'contains', ['a']),
                                   ('no_such_field', 'eq', 1)]:
            with self.subTest(field=field, op=op), self.assertRaises(ValueError):
                self.compile(field, op, target)

    def test_canonical_in_and_deep_filters_stay_inside_public_ast_limits(self):
        result = self.compile('n', 'in', list(range(101)) + [Decimal('1.5')])
        validate_filter(result.canonical_filter)
        self.assertTrue(matches_filter({'metadata': {'n': 100}}, result.canonical_filter))
        self.assertFalse(matches_filter({'metadata': {'n': None}}, result.canonical_filter))
        ast = {'field': 'n', 'op': 'eq', 'value': Decimal('1.5')}
        for _ in range(16):
            ast = {'and': [ast]}
        result = compile_filter(ast, self.catalog)
        validate_filter(result.canonical_filter)

    def test_invalid_public_time_targets_are_rejected_before_compilation(self):
        for target in [1, False, 1.5]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.compile('valid_from', 'eq', target)

    def test_top_level_paths_use_the_shared_presence_array(self):
        for field, column in [('id', 'record_id'), ('type', 'type'), ('kind', 'kind'),
                              ('source_id', 'chunk_owner_id'), ('valid_from', 'valid_from_us'),
                              ('valid_until', 'valid_until_us')]:
            with self.subTest(field=field):
                result = self.compile(field, 'exists', False)
                self.assertEqual(result.expr, 'not (array_contains(filter_present, {p0}))')
                self.assertEqual(result.filter_params, {'p0': field})
                self.assertIn(column, self.compile(field, 'eq', None).expr)
        result = self.compile('valid_from', 'eq', '0001-01-01T00:00:00.000001Z')
        self.assertEqual(list(result.filter_params.values()), ['valid_from', -62135596799999999])
        self.assertIn('valid_from_us', result.expr)

    def test_scope_applies_only_page_predicate_to_main_row(self):
        result = compile_scope('project', {
            'applicability': {'platforms': {'mode': 'specific', 'values': ['A', 'B']}},
            'ontology_scope': 'ids', 'ontology_ids': ['o1'],
            'valid_at': '2026-10-21T00:00:00.123456Z',
            'filters': {'field': 'flag', 'op': 'eq', 'value': True},
        }, self.catalog, kinds=['relation'])
        for column in ['ontology_id', 'platform_mode', 'platform_values', 'valid_from_us',
                       'valid_until_us', self.catalog.resolve('flag').column_name]:
            self.assertIn(column, result.expr)
        for forbidden in ['subject_', 'object_', 'visible', 'active ==', '_known', 'recorded_at',
                          'projection_seq', 'schema_version', 'ontology_known']:
            self.assertNotIn(forbidden, result.expr)
        selected = [value for value in result.filter_params.values() if value == ['A', 'B']]
        self.assertEqual(len(selected), 1)
        self.assertNotIn('array_intersect', result.expr)
        self.assertEqual(result.canonical_filter, {'field': 'metadata.flag', 'op': 'eq', 'value': True})

    def test_scope_platform_groups_and_microseconds_at_calendar_extremes(self):
        self.assertEqual(datetime_to_us('0001-01-01T00:00:00.000001Z'), -62135596799999999)
        self.assertEqual(datetime_to_us('9999-12-31T23:59:59.999999Z'), 253402300799999999)
        result = compile_scope('p', {'ontology_scope': 'unknown', 'include_unknown': False,
                                    'valid_at': '2026-10-21T00:00:00.000001Z'},
                               self.catalog, platform_match='unknown_only')
        self.assertIn('ontology_id is null', result.expr)
        self.assertIn('valid_from_us is not null', result.expr)
        self.assertIn('platform_mode ==', result.expr)
        with self.assertRaises(ValueError):
            compile_scope('p', {'_version_ids': ['x']}, self.catalog)
        for scope in [{'document_ids': ['d']}, {'known_at': '2026-10-21'}]:
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                compile_scope('p', scope, self.catalog)


if __name__ == '__main__':
    unittest.main()
