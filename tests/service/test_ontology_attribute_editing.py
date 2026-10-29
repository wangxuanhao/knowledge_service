"""RDF attribute edits preserve literal identity and existing draft semantics."""
import unittest
from copy import deepcopy
from unittest.mock import Mock

from rdflib import Literal, RDFS, URIRef
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_drafts_commands import DraftCommandMixin
from knowledge_service.services.ontology_operations import apply_operations

TTL = '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:test:Owner> a owl:Class . <urn:test:Other> a owl:Class .
<urn:test:stock> a owl:DatatypeProperty; rdfs:domain <urn:test:Owner>, <urn:test:Other>;
 rdfs:label "库存"@zh-CN, "庫存"@zh-Hant, "Stock"@en;
 rdfs:comment "当前库存"@zh, "Current stock"@en;
 rdfs:range <http://www.w3.org/2001/XMLSchema#integer> .'''


class AttributeEditingTests(unittest.TestCase):
    def test_legacy_version_projects_editable_annotations_without_rewriting_history(self):
        from knowledge_service.services.ontology import editable_attribute_version
        summary = Ontology(TTL).summary()
        del summary['attributes'][0]['annotations']
        version = {'id': 'legacy', 'turtle': TTL, 'summary': summary}
        before = deepcopy(version)
        result = editable_attribute_version(version)
        self.assertIn({'predicate': str(RDFS.label), 'value': '库存', 'language': 'zh-CN'},
                      result['summary']['attributes'][0]['annotations'])
        self.assertEqual(result['summary']['attributes'][0]['domain'], summary['attributes'][0]['domain'])
        self.assertEqual(version, before, 'reading a legacy version must not mutate its persisted summary')
        self.assertIs(editable_attribute_version(result), result, 'complete projections need no reparsing')

    def test_summary_carries_exact_editable_literal_annotations(self):
        item = Ontology(TTL).summary()['attributes'][0]
        self.assertEqual(item['description'], '当前库存')
        self.assertIn({'predicate': str(RDFS.label), 'value': '库存', 'language': 'zh-CN'}, item['annotations'])
        self.assertIn({'predicate': str(RDFS.comment), 'value': 'Current stock', 'language': 'en'}, item['annotations'])

    def test_attribute_edit_batch_replays_without_removing_other_types_or_languages(self):
        compiler = DraftCommandMixin()
        compiler._impact = Mock(return_value={})
        compiler._authoritative_evidence = Mock(return_value=[])
        operations = compiler._compile_command('p', {'source_kind': 'manual'}, {
            'action': 'batch', 'commands': [
                {'action': 'remove_annotation', 'target_iri': 'urn:test:stock', 'predicate': str(RDFS.label), 'value': '库存', 'language': 'zh-CN'},
                {'action': 'add_annotation', 'target_iri': 'urn:test:stock', 'predicate': str(RDFS.label), 'value': '可用库存', 'language': 'zh-CN'},
                {'action': 'remove_domain', 'target_iri': 'urn:test:stock', 'value': 'urn:test:Owner'},
                {'action': 'set_datatype', 'target_iri': 'urn:test:stock',
                 'before': {'datatype': 'http://www.w3.org/2001/XMLSchema#integer'},
                 'datatype': 'http://www.w3.org/2001/XMLSchema#decimal'},
            ]}, Ontology(TTL))
        graph = Ontology(apply_operations(TTL, operations)).graph
        self.assertIn((URIRef('urn:test:stock'), RDFS.label, Literal('Stock', lang='en')), graph)
        self.assertIn((URIRef('urn:test:stock'), RDFS.label, Literal('可用库存', lang='zh-CN')), graph)
        self.assertIn((URIRef('urn:test:stock'), RDFS.label, Literal('庫存', lang='zh-Hant')), graph)
        self.assertEqual(Ontology(graph.serialize(format='turtle')).summary()['attributes'][0]['label_zh'], '可用库存')
        self.assertIn((URIRef('urn:test:stock'), RDFS.domain, URIRef('urn:test:Other')), graph)
        self.assertNotIn((URIRef('urn:test:stock'), RDFS.domain, URIRef('urn:test:Owner')), graph)


if __name__ == '__main__':
    unittest.main()
