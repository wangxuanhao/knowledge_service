from dataclasses import dataclass

from knowledge_service.integrations.semantica_ontology import (
    SemanticaOntologyQuality, QualityEngineUnavailable,
)


@dataclass
class Report:
    def to_dict(self):
        return {
            'passed': False,
            'issues': [{'code': 'MISSING_LABEL', 'message': 'label missing',
                        'severity': 'warning', 'element_id': 'urn:x',
                        'element_type': 'class', 'details': {'z': 1}}],
            'stats': {'classes': 2}, 'metrics': {'coverage': .5},
            'thresholds': {'min_coverage': .8},
            'threshold_failures': ['min_coverage'],
        }


def test_adapter_returns_stable_plain_dto_and_version():
    adapter = SemanticaOntologyQuality(
        checker=lambda ontology, **kwargs: Report(), version='0.7.0')
    result = adapter.check({'classes': []}, competency_questions=['能回答什么？'])

    assert result['engine'] == 'semantica'
    assert result['engine_version'] == '0.7.0'
    assert result['passed'] is False
    assert result['issues'][0]['code'] == 'missing_label'
    assert result['competency_questions'] == ['能回答什么？']


def test_threshold_failure_alone_is_a_blocking_quality_issue():
    adapter = SemanticaOntologyQuality(
        checker=lambda ontology, **kwargs: Report(), version='0.7.0')
    result = adapter.check({'classes': []})
    assert any(issue['severity'] == 'error'
               and issue['code'] == 'quality_gate_failed'
               for issue in result['issues'])


def test_import_or_runtime_failure_is_a_blocking_engine_error():
    def broken(*args, **kwargs):
        raise RuntimeError('boom')

    adapter = SemanticaOntologyQuality(checker=broken, version='0.7.0')
    try:
        adapter.check({'classes': []})
    except QualityEngineUnavailable as exc:
        assert exc.code == 'quality_engine_unavailable'
    else:
        raise AssertionError('quality engine failure must block publication')


def test_quality_payload_expands_union_constraints_and_is_stable_after_reparse():
    from knowledge_service.integrations.semantica_ontology import ontology_payload
    from knowledge_service.services.ontology import Ontology

    turtle = '''@prefix : <urn:quality:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        :A a owl:Class . :B a owl:Class .
        :rating a owl:DatatypeProperty;
            rdfs:domain [ a owl:Class; owl:unionOf (:A :B) ]; rdfs:range xsd:decimal .
        :rel a owl:ObjectProperty;
            rdfs:domain :A; rdfs:range [ a owl:Class; owl:unionOf (:A :B) ] .'''
    ontology = Ontology(turtle)
    payload = ontology_payload(ontology)
    properties = {item['uri']: item for item in payload['properties']}
    assert properties['urn:quality:rating']['domain'] == ['urn:quality:A', 'urn:quality:B']
    assert properties['urn:quality:rating']['range'] == ['http://www.w3.org/2001/XMLSchema#decimal']
    assert properties['urn:quality:rel']['range'] == ['urn:quality:A', 'urn:quality:B']
    assert ontology_payload(Ontology(ontology.graph.serialize(format='turtle'))) == payload
