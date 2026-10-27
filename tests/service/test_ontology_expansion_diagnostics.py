"""Ontology diagnostics are unit tests: no PostgreSQL, Milvus or model calls.

May also run with unittest while a separate pytest database session is active.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from knowledge_service.services import answers
from knowledge_service.services.service import KnowledgeService


FLAT = '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
<urn:test:Parent> a owl:Class .
<urn:test:Child> a owl:Class .'''
HIERARCHICAL = FLAT + '''
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:test:Child> rdfs:subClassOf <urn:test:Parent> .'''


class OntologyExpansionDiagnosticsTest(unittest.TestCase):
    def search_diagnostic(self, turtle):
        repository = SimpleNamespace(list_ontologies=lambda _project:
            [{'id': 'published', 'turtle': turtle}] if turtle else [])
        encoder = SimpleNamespace(identity='unit-test', semantic=False)
        service = KnowledgeService(repository, encoder)
        # Ranking and provenance readers are external to this diagnostic seam.
        result = {'hits': [], 'candidate_count': 0, 'active_mode': 'keyword',
                  '_backend': 'milvus', 'backends': {}, 'complete': True}
        with patch('knowledge_service.services.service.RetrievalEngine.search', return_value=result), \
                patch('knowledge_service.services.retrieval_context.enrich_source_context'), \
                patch('knowledge_service.repository.scoped_reads.ScopedReads.mentioned_entities', return_value=[]):
            return service.search('project', {'query': 'unrelated query',
                'ontology_context': False, 'ontology_expansion': True,
                'retrieval_mode': 'keyword'})['ontology_expansion']

    def test_flat_classes_do_not_report_a_hierarchy(self):
        diagnostic = self.search_diagnostic(FLAT)
        self.assertFalse(diagnostic['has_hierarchy'])
        self.assertFalse(diagnostic['matched'])

    def test_declared_parent_edge_reports_a_hierarchy(self):
        self.assertTrue(self.search_diagnostic(HIERARCHICAL)['has_hierarchy'])

    def test_no_ontology_reports_no_hierarchy(self):
        self.assertFalse(self.search_diagnostic('')['has_hierarchy'])

    def test_qa_context_preserves_the_search_expansion_ledger(self):
        ledger = {'terms': ['urn:test:Parent'], 'subclasses': ['urn:test:Child'],
                  'type_hits': 2, 'has_hierarchy': True, 'matched': True}
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                result = {'hits': [], 'ontology_expansion': deepcopy(ledger),
                          '_backend': 'milvus', 'active_mode': 'keyword'}
                if grouped:
                    result['platform_groups'] = [{'hits': [], 'source_scope': 'all',
                                                 'active_mode': 'keyword'}]
                service = SimpleNamespace(
                    search=lambda _project, _request: result,
                    repository=SimpleNamespace(),
                    encoder=SimpleNamespace(identity='unit-test', semantic=False),
                    annotate_structure_pending=lambda *_args: None)
                context = answers._retrieved_question_context(service, 'project', {
                    'query': 'Parent', 'ontology_expansion': True,
                    'retrieval_mode': 'keyword', 'hops': 0})
                self.assertEqual(context.get('ontology_expansion'), ledger)

    def test_qa_without_expansion_keeps_the_ledger_absent(self):
        service = SimpleNamespace(
            search=lambda _project, _request: {'hits': [], '_backend': 'milvus'},
            repository=SimpleNamespace(),
            encoder=SimpleNamespace(identity='unit-test', semantic=False),
            annotate_structure_pending=lambda *_args: None)
        context = answers._retrieved_question_context(service, 'project', {
            'query': 'Parent', 'retrieval_mode': 'keyword', 'hops': 0})
        self.assertNotIn('ontology_expansion', context)


if __name__ == '__main__':
    unittest.main()
