"""Offline contracts for published-property guidance and bounded extraction."""
import json
import math
import os
import unittest
from unittest.mock import patch

from knowledge_service.integrations import semantica_adapter as adapter
from knowledge_service.services.attribute_extraction import (
    AttributeExtractionBatch, AttributeProposal, extract_attributes,
)
from knowledge_service.services.ontology import Ontology


XSD = 'http://www.w3.org/2001/XMLSchema#'
TTL = '''@prefix ex: <https://test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Person a owl:Class . ex:Reviewer a owl:Class; rdfs:subClassOf ex:Person .
ex:Company a owl:Class .
ex:responsibilities a owl:DatatypeProperty; rdfs:domain ex:Person;
  rdfs:range xsd:string; rdfs:comment "完整保留该岗位承担的职责段落" .
ex:registration a owl:DatatypeProperty; rdfs:domain ex:Company; rdfs:range xsd:string .
ex:enabled a owl:DatatypeProperty; rdfs:domain ex:Person; rdfs:range xsd:boolean .
'''


class AttributeExtractionPureTests(unittest.TestCase):
    def setUp(self):
        from semantica.semantic_extract.types import Entity
        self.entities = [Entity('甲', 'Reviewer', 0, 1)]
        self.ontology = Ontology(TTL)
        self.config = dict(provider='openai', llm_model='test', api_key='test', base_url='test')
        self.prompt = None

    def run_extraction(self, text, proposals, **config):
        owner = self

        class Provider:
            def generate_typed(self, prompt, schema, **kwargs):
                owner.prompt = prompt
                return schema(attributes=proposals)

        with patch('semantica.semantic_extract.providers.create_provider', return_value=Provider()):
            return extract_attributes(text, self.entities, self.ontology, {**self.config, **config})

    def proposal(self, value, attribute='responsibilities', **changes):
        return dict(entity_index=0, attribute=attribute, value=value,
                    evidence=str(value), confidence=.9, **changes)

    def test_declared_responsibility_paragraph_uses_domain_and_range_guidance(self):
        value = '审核申请材料。\n  核实证明文件，并完整记录处理结果。\n'
        batch = self.run_extraction('甲的职责如下：\n' + value, [self.proposal(value)])

        self.assertEqual(batch.attributes[0].value, value)
        payload = json.loads(self.prompt[self.prompt.index('{'):])
        properties = {item['id']: item for item in payload['ontology_attributes']}
        self.assertIn('https://test/responsibilities', properties)
        self.assertNotIn('https://test/registration', properties)
        self.assertEqual(properties['https://test/responsibilities']['range'], [XSD + 'string'])
        self.assertIn('完整保留', properties['https://test/responsibilities']['description'])
        self.assertNotIn('职责、所有权', self.prompt)
        self.assertIn('段落', self.prompt)
        self.assertIn('未定义', self.prompt)
        self.assertEqual(batch.diagnostics['ontology_attributes_excluded_by_domain'], 1)

    def test_default_budget_keeps_more_than_five_properties(self):
        proposals = [self.proposal(f'值{i}', attribute=f'字段{i}') for i in range(8)]
        batch = self.run_extraction('甲 ' + ' '.join(row['value'] for row in proposals), proposals)

        self.assertEqual(len(batch.attributes), 8)
        self.assertEqual(batch.diagnostics['budget_per_entity'], 50)
        self.assertEqual(batch.diagnostics['budget_total'], 500)
        self.assertEqual(batch.diagnostics['truncated'], 0)

    def test_configured_entity_and_total_budgets_report_every_omission(self):
        from semantica.semantic_extract.types import Entity
        self.entities.append(Entity('乙', 'Reviewer', 2, 3))
        proposals = [dict(entity_index=entity, attribute=f'字段{index}', value=index,
                          evidence=str(index), confidence=confidence)
                     for entity, index, confidence in [(0, 1, .8), (0, 2, .7), (0, 3, .6),
                                                        (1, 4, .9), (1, 5, .5), (1, 6, .4)]]
        batch = self.run_extraction('甲 乙 1 2 3 4 5 6', proposals,
                                    attribute_max_per_entity=2, attribute_max_total=3)

        self.assertEqual([item.value for item in batch.attributes], [1, 2, 4])
        self.assertEqual(batch.diagnostics['skipped_attribute_limit'], 2)
        self.assertEqual(batch.diagnostics['skipped_total_limit'], 1)
        self.assertEqual(batch.diagnostics['truncated'], 1)
        payload = json.loads(self.prompt[self.prompt.index('{'):])
        self.assertEqual(payload['extraction_budget'], {'max_per_entity': 2, 'max_total': 3})

    def test_rejects_unsafe_native_values_without_losing_valid_siblings(self):
        values = [True, 7, 2.5, '第一行\n第二行', {'nested': 'value'}, ['value'], None, math.inf]
        batch = self.run_extraction('甲 True 7 2.5 第一行\n第二行',
                                    [self.proposal(value) for value in values])

        self.assertEqual([type(item.value) for item in batch.attributes], [bool, int, float, str])
        self.assertEqual(batch.attributes[3].value, values[3])
        self.assertEqual(batch.diagnostics['skipped_invalid_schema'], 4)

    def test_full_string_value_and_quote_support_are_checked_separately(self):
        paragraph = '审核材料。\n核实证明。'
        proposals = [
            dict(entity_index=0, attribute='responsibilities', value=paragraph,
                 evidence='审核材料。', confidence=.9),
            dict(entity_index=0, attribute='other', value='模型补写的内容',
                 evidence='审核材料。', confidence=.8),
        ]
        batch = self.run_extraction('甲\n' + paragraph, proposals)

        self.assertEqual([item.value for item in batch.attributes], [paragraph, '模型补写的内容'])
        self.assertEqual([item.value_evidence_status for item in batch.attributes],
                         ['incomplete_quote', 'unverified'])
        self.assertEqual(batch.diagnostics['incomplete_value_evidence'], 1)
        self.assertEqual(batch.diagnostics['unverified_value_evidence'], 1)

    def test_slice_receipt_is_respected_without_dropping_property_definitions(self):
        summary = self.ontology.summary()
        summary['attributes'] = [item for item in summary['attributes']
                                 if item['name'] == 'responsibilities']
        self.run_extraction('甲审核材料', [], ontology_slice_summary=summary)
        payload = json.loads(self.prompt[self.prompt.index('{'):])
        self.assertEqual([item['name'] for item in payload['ontology_attributes']], ['responsibilities'])

    def test_invented_quote_never_verifies_a_string_value(self):
        batch = self.run_extraction('甲审核材料', [dict(entity_index=0,
            attribute='responsibilities', value='模型补写', evidence='甲的职责是模型补写', confidence=.9)])
        self.assertEqual(batch.attributes[0].evidence_status, 'unverified')
        self.assertEqual(batch.attributes[0].value_evidence_status, 'unverified')
        self.assertEqual(batch.diagnostics['unverified_value_evidence'], 1)

    def test_invalid_budgets_fail_explicitly_before_provider_call(self):
        for value in (0, -1, 501, True, '5'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'attribute_max_total'):
                self.run_extraction('甲', [], attribute_max_total=value)


class AttributeAdapterPureTests(unittest.TestCase):
    def extract(self, dependency, enabled=True):
        from semantica.semantic_extract.types import Entity
        extractor = adapter.SemanticaExtractor(attribute_extractor=dependency)
        extractor.include_attributes = enabled
        extractor.attribute_max_per_entity = 12
        extractor.attribute_max_total = 23
        extractor.ontology_slice_summary = Ontology(TTL).summary()
        with patch.dict(os.environ, {'KG_LLM_API_KEY': 'test', 'KG_LLM_BASE_URL': 'test', 'KG_LLM_MODEL': 'test'}), \
             patch('semantica.semantic_extract.methods.extract_entities_llm', return_value=[Entity('甲', 'Reviewer', 0, 1)]), \
             patch.object(adapter, '_extract_relations_guided', return_value=[]):
            records = extractor.extract('甲的职责：\n审核材料。\n核实证明。', Ontology(TTL))
        return extractor, records

    def test_optional_extraction_passes_budget_and_slice_and_preserves_value(self):
        captured = {}
        value = '审核材料。\n核实证明。'

        def dependency(text, entities, ontology, config):
            captured.update(config)
            return AttributeExtractionBatch(attributes=[AttributeProposal(entity_index=0,
                attribute='https://test/responsibilities', value=value,
                evidence=value, confidence=.9)], diagnostics={'accepted': 1, 'truncated': 0})

        extractor, records = self.extract(dependency)
        self.assertEqual(captured['attribute_max_per_entity'], 12)
        self.assertEqual(captured['attribute_max_total'], 23)
        self.assertIn('ontology_slice_summary', captured)
        self.assertTrue(all(row['kind'] == 'entity' for row in records))
        candidate = extractor.review_candidates[0]
        self.assertEqual(candidate['value'], value)
        self.assertEqual(candidate['datatype'], XSD + 'string')
        self.assertEqual(extractor.extraction_diagnostics['attribute_truncated'], 0)

    def test_disabled_extraction_never_calls_provider_dependency(self):
        def dependency(*args):
            self.fail('disabled extraction called its dependency')

        extractor, records = self.extract(dependency, enabled=False)
        self.assertTrue(records)
        self.assertEqual(extractor.review_candidates, [])

    def test_open_routing_quarantines_unsupported_object_and_keeps_valid_sibling(self):
        entities = [{'id': 'person', 'text': '甲', 'proposed_type': 'Person'}]
        facts = [dict(subject='甲', predicate='details', object={'nested': True},
                      fact_kind='attribute', literal_type='text', evidence='甲已启用'),
                 dict(subject='甲', predicate='enabled', object=True,
                      fact_kind='attribute', literal_type='boolean', evidence='甲已启用')]
        accepted, exceptions = adapter._route_open_facts('甲已启用', entities, facts, include_attributes=True)
        self.assertEqual([item['value'] for item in accepted], [True])
        self.assertEqual([item['reason_code'] for item in exceptions], ['unsupported_attribute_value'])

    def test_open_provider_envelope_does_not_lose_siblings_for_object_values(self):
        facts = [dict(subject='甲', predicate='details', object={'nested': True},
                      fact_kind='attribute', literal_type='text', evidence='甲已启用'),
                 dict(subject='甲', predicate='enabled', object=True,
                      fact_kind='attribute', literal_type='boolean', evidence='甲已启用')]
        result = adapter._OpenFacts(facts=facts)
        accepted, exceptions = adapter._route_open_facts('甲已启用',
            [{'id': 'person', 'text': '甲', 'proposed_type': 'Person'}],
            result.facts, include_attributes=True)
        self.assertEqual([item['value'] for item in accepted], [True])
        self.assertEqual(len(exceptions), 1)

    def test_open_guidance_limits_undeclared_paragraphs_without_blanket_exclusion(self):
        captured = {}

        class Provider:
            def generate_typed(self, prompt, schema, **kwargs):
                captured['prompt'] = prompt
                return schema(facts=[])

        with patch.object(adapter, '_open_fact_provider', return_value=Provider()), \
             patch.object(adapter, '_llm_max_tokens', return_value=65536):
            adapter._extract_facts_open('甲审核材料', [], {}, include_attributes=True)
        self.assertNotIn('responsibility, prohibition, ownership statement', captured['prompt'])
        self.assertIn('Without an explicit property definition', captured['prompt'])
        self.assertIn('conservative', captured['prompt'])


if __name__ == '__main__':
    unittest.main()
