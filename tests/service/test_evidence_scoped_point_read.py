"""Standalone regressions: evidence must qualify one ID, never the full corpus."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from knowledge_service.services.evidence import evidence


class ScopedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'selected', 'version': 3, 'version_id': 'selected-v3',
                    'kind': 'entity', 'text': 'selected', 'recorded_at': '2026-10-19T00:00:00Z',
                    'metadata': {}}
        self.repo = SimpleNamespace(list_assertions=Mock(return_value=[]))
        self.service = SimpleNamespace(repository=self.repo,
            scoped=Mock(side_effect=AssertionError('evidence performed a whole-project scoped read')))
        self.scope = {'known_at': '2026-10-18T00:00:00Z', 'valid_at': '2026-10-17T00:00:00Z',
            'include_unknown': False, 'filters': {'field': 'metadata.team', 'op': 'eq', 'value': 'selected'},
            'applicability': {'platforms': {'mode': 'specific', 'values': ['web']}},
            'ontology_scope': 'ids', 'ontology_ids': ['release-1'], 'kinds': ['entity']}

    def reads(self, rows):
        patched = patch('knowledge_service.services.evidence.ScopedReads', create=True)
        factory = patched.start()
        self.addCleanup(patched.stop)
        factory.return_value.by_ids.return_value = rows
        return factory

    def test_qualifies_exact_record_with_complete_unchanged_scope(self):
        factory = self.reads([self.row])
        original = copy.deepcopy(self.scope)
        result = evidence(self.service, 'project', 'selected', self.scope)
        factory.assert_called_once_with(self.repo)
        factory.return_value.by_ids.assert_called_once_with('project', self.scope, ['selected'])
        self.assertEqual(self.scope, original)
        self.assertEqual(result['record_version'], 3)
        self.assertEqual(result['documents'], [])
        self.service.scoped.assert_not_called()

    def test_out_of_scope_record_cannot_trigger_assertion_or_source_reads(self):
        self.reads([])
        with self.assertRaises(KeyError):
            evidence(self.service, 'project', 'selected', self.scope)
        self.repo.list_assertions.assert_not_called()
        self.service.scoped.assert_not_called()

    def test_formal_evidence_still_resolves_frozen_assertion_sources(self):
        self.reads([self.row])
        assertion = {'id': 'a1', 'status': 'accepted', 'payload': {'private': True}}
        self.repo.list_assertions.return_value = [assertion]
        resolution = {'document': {'version_id': 'frozen-document-v1'}}
        document = {'id': 'document', 'version': 1, 'source_location': {'document_version_id': 'frozen-document-v1'}}
        with patch('knowledge_service.services.evidence._resolve_assertion_evidence', return_value=resolution) as resolve, \
             patch('knowledge_service.services.evidence._resolution_score', return_value=1), \
             patch('knowledge_service.services.evidence._serialize_formal_document', return_value=document):
            result = evidence(self.service, 'project', 'selected', self.scope)
        resolve.assert_called_once_with(self.repo, 'project', assertion)
        self.assertEqual(result['documents'], [document])
        self.assertEqual(result['support_counts']['accepted'], 1)
        self.assertNotIn('payload', result['assertions'][0])

    def test_record_metadata_cannot_supply_sources_without_assertions(self):
        origin = {'id': 'original', 'source_id': 'source', 'metadata': {'source_version_id': 'source-v2'}}
        self.row['metadata'] = {'merged_sources': [origin, 'invalid-origin']}
        self.row['source_id'] = 'source'
        self.repo.history = Mock(side_effect=AssertionError('guessed source read'))
        self.reads([self.row])
        result = evidence(self.service, 'project', 'selected', self.scope)
        self.assertEqual(result['documents'], [])
        self.assertIn('来源断言', result['message'])
        self.repo.history.assert_not_called()


if __name__ == '__main__':
    unittest.main()
