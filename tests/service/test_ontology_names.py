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
