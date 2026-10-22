"""JSON numbers retain decimal precision across Python, SQL and Milvus."""
import unittest

from knowledge_service.utils.filters import matches_filter
from knowledge_service.repository.scoped_reads import _filter_sql


class NumericFilterContractTests(unittest.TestCase):
    def test_float_and_integer_equality_uses_the_stored_json_value(self):
        binary_float_integer = int(1e23)
        self.assertNotEqual(binary_float_integer, 10 ** 23)
        row = {'metadata': {'value': 1e23}}
        self.assertFalse(matches_filter(row, {
            'field': 'value', 'op': 'eq', 'value': binary_float_integer}))
        self.assertTrue(matches_filter(row, {
            'field': 'value', 'op': 'eq', 'value': 10 ** 23}))
        self.assertTrue(matches_filter({'metadata': {'value': [1.0, {'n': 1e23}]}}, {
            'field': 'value', 'op': 'eq', 'value': [1, {'n': 10 ** 23}]}))

    def test_numeric_comparison_does_not_round_the_stored_integer(self):
        self.assertTrue(matches_filter({'metadata': {'value': int(1e23)}}, {
            'field': 'value', 'op': 'lt', 'value': 1e23}))
        self.assertTrue(matches_filter({'metadata': {'value': 2 ** 53 + 1}}, {
            'field': 'value', 'op': 'gt', 'value': float(2 ** 53)}))

    def test_sql_numeric_parameter_has_explicit_decimal_type(self):
        sql, parameters = _filter_sql({
            'field': 'value', 'op': 'gt', 'value': float(2 ** 53)}, 'r')
        self.assertIn('> ?::numeric', sql)
        self.assertEqual(parameters, ['9007199254740992.0'])


if __name__ == '__main__':
    unittest.main()
