from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_slice import OntologySliceSelector


TTL = '''
@prefix ex: <urn:test:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Employee a owl:Class ; rdfs:label "员工" .
ex:Manager a owl:Class ; rdfs:label "经理" ; rdfs:subClassOf ex:Employee .
ex:Department a owl:Class ; rdfs:label "部门" .
ex:approves a owl:ObjectProperty ; rdfs:label "审批" ; rdfs:domain ex:Manager ; rdfs:range ex:Department .
'''


def test_slice_is_deterministic_and_closes_hierarchy_and_property_endpoints():
    selector = OntologySliceSelector(selector_version='test-v1')
    first = selector.select_terms(
        '经理审批部门', Ontology(TTL), ontology_id='ont-1')
    second = selector.select_terms(
        '经理审批部门', Ontology(TTL), ontology_id='ont-1')

    assert first == second
    assert first['ontology_id'] == 'ont-1'
    assert first['selector_version'] == 'test-v1'
    iris = {item['term_iri'] for item in first['terms']}
    assert {'urn:test:Manager', 'urn:test:Employee', 'urn:test:Department',
            'urn:test:approves'} <= iris
    reasons = {reason for item in first['terms'] for reason in item['reasons']}
    assert {'lexical', 'ancestor', 'property_domain', 'property_range'} <= reasons


def test_empty_hit_uses_only_configured_roots_and_never_another_ontology():
    result = OntologySliceSelector(roots=['urn:test:Employee']).select_terms(
        '完全无关', Ontology(TTL), ontology_id='ont-a')

    assert [item['term_iri'] for item in result['terms']] == ['urn:test:Employee']
    assert result['degraded'] is True
    assert all(item['ontology_id'] == 'ont-a' for item in result['terms'])
