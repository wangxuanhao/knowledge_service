"""Attribute extraction has one explicit dependency and one batch result shape."""
import os
import unittest
from unittest.mock import patch

from knowledge_service.integrations import semantica_adapter as adapter
from knowledge_service.services.attribute_extraction import AttributeExtractionBatch
from knowledge_service.services.ontology import Ontology


class AttributeExtractorContractTests(unittest.TestCase):
    def extract(self, dependency):
        from semantica.semantic_extract.types import Entity
        extractor = adapter.SemanticaExtractor(attribute_extractor=dependency)
        extractor.include_attributes = True
        ontology = Ontology('@prefix ex: <https://test/> . '
            '@prefix owl: <http://www.w3.org/2002/07/owl#> . ex:Person a owl:Class .')
        with patch.dict(os.environ, {'KG_LLM_API_KEY': 'test', 'KG_LLM_BASE_URL': 'test', 'KG_LLM_MODEL': 'test'}), \
             patch('semantica.semantic_extract.methods.extract_entities_llm', return_value=[Entity('甲', 'Person', 0, 1)]), \
             patch.object(adapter, '_extract_relations_guided', return_value=[]):
            rows = extractor.extract('甲', ontology)
        return rows, extractor.extraction_diagnostics

    def test_explicit_batch_preserves_diagnostics(self):
        rows, diagnostics = self.extract(lambda *args: AttributeExtractionBatch(diagnostics={'accepted': 0}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(diagnostics['attribute_accepted'], 0)

    def test_raw_list_result_is_rejected(self):
        with self.assertRaises(AttributeError):
            self.extract(lambda *args: [])

    def test_enabled_extraction_requires_explicit_dependency(self):
        with self.assertRaisesRegex(RuntimeError, 'no extractor was configured'):
            self.extract(None)


if __name__ == '__main__':
    unittest.main()
