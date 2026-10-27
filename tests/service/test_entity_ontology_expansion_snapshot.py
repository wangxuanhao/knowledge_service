"""Pure snapshot allowlist checks; can run with unittest without database fixtures."""
import unittest

from knowledge_service.services.qa_conversations import sanitize_messages


class EntityExpansionSnapshotTests(unittest.TestCase):
    def snapshot(self, expansion):
        return sanitize_messages('project', [{'kind': 'assistant', 'text': 'answer',
            'scope': {}, 'ontology_expansion': expansion}])[0]['ontology_expansion']

    def test_preserves_public_entity_paths_and_supplemental_counts(self):
        match = {'id': 'meituan', 'version_id': 'meituan-v1', 'text': '美团',
            'matched_name': '团团', 'match_kind': 'alias',
            'type': 'urn:InternetPlatform', 'type_label': '互联网平台'}
        ledger = {'enabled': True, 'matched': True, 'has_hierarchy': False,
            'terms': ['urn:InternetPlatform'], 'literal_terms': [],
            'entity_terms': ['urn:InternetPlatform'], 'entity_matches': [match],
            'subclasses': [], 'type_hits': 7, 'used_hits': 2}
        actual = self.snapshot({**ledger, 'provider': 'secret', 'entity_matches': [
            {**match, 'provider': {'token': 'secret'}, 'source': 'secret'}]})
        self.assertEqual(actual, ledger)

    def test_bounds_explicit_path_fields_and_ignores_malformed_values(self):
        long_text = 'x' * 900
        actual = self.snapshot({'entity_matches': [None, 4, {},
            {'id': long_text, 'version_id': long_text, 'text': long_text,
             'matched_name': long_text, 'match_kind': 'provider',
             'type': long_text, 'type_label': long_text, 'token': 'secret'}] * 10,
            'literal_terms': [long_text] * 150 + [False, {}],
            'entity_terms': {'secret': 'secret'}, 'used_hits': True})
        self.assertEqual(len(actual.get('entity_matches', [])), 8)
        self.assertTrue(all(len(value) <= 500 for match in actual['entity_matches']
            for value in match.values()))
        self.assertTrue(all(set(match) <= {'id', 'version_id', 'text', 'matched_name',
            'match_kind', 'type', 'type_label'} for match in actual['entity_matches']))
        self.assertTrue(all('match_kind' not in match for match in actual['entity_matches']))
        self.assertEqual(actual['literal_terms'], ['x' * 500] * 100)
        self.assertNotIn('entity_terms', actual)
        self.assertNotIn('used_hits', actual)
        for value in (-1, 1.5, '2', 2**53):
            self.assertNotIn('used_hits', self.snapshot({'used_hits': value}))
        self.assertEqual(self.snapshot({'used_hits': 0})['used_hits'], 0)


if __name__ == '__main__':
    unittest.main()
