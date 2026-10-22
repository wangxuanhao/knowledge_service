"""Current applicability wire contract; runnable without an application or DB."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[2] / 'knowledge_service'
PACKAGE = '_canonical_applicability_contract'
for suffix in ('', '.domain', '.core'):
    module = ModuleType(PACKAGE + suffix)
    module.__path__ = [str(ROOT.joinpath(*suffix.lstrip('.').split('.')))] if suffix else [str(ROOT)]
    sys.modules[module.__name__] = module
applicability = importlib.import_module(PACKAGE + '.domain.applicability')


class CanonicalApplicabilityTests(unittest.TestCase):
    def test_dimension_lists_are_rejected_instead_of_migrated(self):
        for value in ([], ['美团'], (), ('美团',)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                applicability.normalize_applicability({'platforms': value})

    def test_every_nonempty_scope_uses_one_object_identity(self):
        scope = applicability.normalize_applicability({
            'platforms': {'mode': 'specific', 'values': ['meituan']}})
        payload = json.dumps({'schema_version': 2, **scope}, ensure_ascii=False,
                             sort_keys=True, separators=(',', ':'))
        self.assertEqual(applicability.scope_identity(scope),
                         hashlib.sha256(payload.encode()).hexdigest())

    def test_optional_scope_is_unknown_and_canonical_values_keep_aliases(self):
        self.assertEqual(applicability.scope_identity(None), '')
        self.assertEqual(applicability.normalize_dimension({
            'mode': 'specific', 'values': [' meituan ', '美团']}, 'platforms'),
            {'mode': 'specific', 'values': ['美团']})
        self.assertNotEqual(applicability.scope_identity({
            'platforms': {'mode': 'all', 'values': []}}), '')


if __name__ == '__main__':
    unittest.main()
