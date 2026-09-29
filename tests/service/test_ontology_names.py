import pytest
from rdflib import Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from knowledge_service.services.discovery_vocabulary import DiscoveryVocabularyNormalizer
from knowledge_service.services.ontology import Ontology,local_name


def test_local_name_supports_project_urns_without_breaking_urls():
    assert local_name('urn:knowledge:ontology:project:Merchant')=='Merchant'
    assert local_name('urn:knowledge:catalog#Merchant')=='Merchant'
    assert local_name('urn:knowledge:catalog/Order%20Item')=='Order Item'
    assert local_name('https://example.test/schema#Merchant')=='Merchant'
    assert local_name('https://example.test/schema/Merchant')=='Merchant'


def test_urn_summary_keeps_identifier_internal():
    ontology=Ontology('''
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:knowledge:ontology:p:Merchant> a owl:Class ; rdfs:label "商户"@zh .
    ''')
    item=ontology.summary()['classes'][0]
    assert item['id']=='urn:knowledge:ontology:p:Merchant'
    assert item['name']=='Merchant'
    assert item['label_zh']=='商户'


def test_percent_encoded_rdfs_class_uses_decoded_name_and_retirement_state():
    turtle = '''
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <http://example.test/Order%20Item> a rdfs:Class ; owl:deprecated true .
    '''
    ontology = Ontology(turtle)

    item = ontology.summary()['classes'][0]

    assert item['name'] == 'Order Item'
    assert item['active'] is False
    assert ontology.resolve('Order Item') == URIRef('http://example.test/Order%20Item')
    assert ontology.summary(active_only=True)['classes'] == []
    normalized = DiscoveryVocabularyNormalizer(turtle).normalize([{
        'id': 'candidate', 'kind': 'class', 'name': 'Order Item',
    }])
    assert normalized.conflicts[0]['code'] == 'retired_term_reuse_blocked'


def test_live_graph_mutations_refresh_kinds_labels_and_retirement():
    ontology = Ontology('''
        @prefix ex: <http://example.test/> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        ex:Existing a owl:Class .
    ''')
    existing = URIRef('http://example.test/Existing')
    added = URIRef('http://example.test/Added')
    classes = ontology.classes

    ontology.graph.add((added, RDF.type, RDFS.Class))

    assert ontology.resolve('Added') == added
    assert ontology.classes is classes
    assert added in classes

    ontology.graph.add((existing, RDFS.label, Literal('Existing label')))
    ontology.graph.add((existing, OWL.deprecated, Literal(True)))

    item = next(row for row in ontology.summary()['classes'] if row['id'] == str(existing))
    assert item['label'] == 'Existing label'
    assert item['active'] is False
    assert ontology.is_active_term(existing) is False

    ontology.graph.remove((existing, OWL.deprecated, None))
    ontology.graph.remove((added, RDF.type, RDFS.Class))

    assert ontology.is_active_term(existing) is True
    with pytest.raises(ValueError):
        ontology.resolve('Added')
