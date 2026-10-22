"""Canonical command batches preserve a complete manual term definition."""
import unittest
from unittest.mock import Mock

from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_drafts_commands import DraftCommandMixin


class CommandBatchTests(unittest.TestCase):
    def compiler(self):
        compiler = DraftCommandMixin()
        compiler._impact = Mock(return_value={})
        compiler._authoritative_evidence = Mock(return_value=[])
        return compiler

    def test_batch_compiles_declaration_before_its_labels_and_domain(self):
        compiler = self.compiler()
        operations = compiler._compile_command('project', {'source_kind': 'manual'}, {
            'action': 'batch', 'commands': [
                {'action': 'create_term', 'target_iri': 'urn:test:field', 'kind': 'attribute'},
                {'action': 'add_annotation', 'target_iri': 'urn:test:field',
                 'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': '字段'},
                {'action': 'add_domain', 'target_iri': 'urn:test:field', 'value': 'urn:test:Owner'},
            ]}, Ontology('<urn:test:Owner> a <http://www.w3.org/2002/07/owl#Class> .'))
        self.assertEqual([op['action'] for op in operations],
                         ['create_term', 'add_annotation', 'add_domain'])

    def test_invalid_batch_rejects_the_whole_command_before_persistence(self):
        compiler = self.compiler()
        with self.assertRaises(ValueError):
            compiler._compile_command('project', {'source_kind': 'manual'}, {
                'action': 'batch', 'commands': [
                    {'action': 'create_term', 'target_iri': 'urn:test:relation', 'kind': 'relation'},
                    {'action': 'add_domain', 'target_iri': 'urn:test:relation', 'value': 'urn:test:Missing'},
                ]}, Ontology(''))


if __name__ == '__main__':
    unittest.main()
