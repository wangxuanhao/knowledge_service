import pytest
from rdflib import Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

import knowledge_service.services.ontology as ontology_module
from knowledge_service.services.discovery_vocabulary import DiscoveryVocabularyNormalizer
from knowledge_service.services.ontology import Ontology, local_name, term_kind


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


def test_vocabulary_index_rebuilds_once_per_graph_revision(monkeypatch):
    calls = 0
    original = ontology_module.index_governed_vocabulary

    def counted(graph):
        nonlocal calls
        calls += 1
        return original(graph)

    monkeypatch.setattr(ontology_module, 'index_governed_vocabulary', counted)
    ontology = Ontology('''
        @prefix ex: <http://example.test/> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        ex:Existing a owl:Class .
    ''')

    assert calls == 1
    assert ontology.resolve('Existing')
    assert ontology.resolve('Existing')
    assert calls == 1

    ontology.graph.add((
        URIRef('http://example.test/Added'), RDF.type, RDFS.Class))
    assert ontology.resolve('Added')
    assert ontology.resolve('Added')
    assert calls == 2

    ontology.graph.set((
        URIRef('http://example.test/Added'), RDFS.label, Literal('Renamed')))
    assert ontology.summary()['classes'][0]['label'] == 'Renamed'
    assert ontology.summary()['classes'][0]['label'] == 'Renamed'
    assert calls == 3

    ontology.graph.remove((
        URIRef('http://example.test/Added'), RDF.type, RDFS.Class))
    with pytest.raises(ValueError):
        ontology.resolve('Added')
    assert calls == 4


def test_blank_node_classes_remain_anonymous_graph_semantics():
    ontology = Ontology('''
        @prefix ex: <http://example.test/> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:Named a owl:Class ; rdfs:subClassOf [
            a owl:Class ; rdfs:label "Anonymous"
        ] .
    ''')

    assert [item['name'] for item in ontology.summary()['classes']] == ['Named']
    with pytest.raises(ValueError):
        ontology.resolve('Anonymous')


def test_term_kind_observes_live_graph_mutations():
    ontology = Ontology('')
    term = URIRef('http://example.test/Mutable')

    assert term_kind(ontology, term) is None

    ontology.graph.add((term, RDF.type, OWL.Class))
    assert term_kind(ontology, term) == 'class'

    ontology.graph.remove((term, RDF.type, OWL.Class))
    ontology.graph.add((term, RDF.type, OWL.ObjectProperty))
    assert term_kind(ontology, term) == 'relation'
