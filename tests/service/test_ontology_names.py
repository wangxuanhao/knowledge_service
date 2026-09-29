from rdflib import URIRef

from knowledge_service.services.discovery_vocabulary import DiscoveryVocabularyNormalizer
from knowledge_service.services.ontology import Ontology,local_name


def test_local_name_supports_project_urns_without_breaking_urls():
    assert local_name('urn:knowledge:ontology:project:Merchant')=='Merchant'
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
