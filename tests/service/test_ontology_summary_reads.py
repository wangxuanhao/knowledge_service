from ontology_seed import seed_ontology
from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology

TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
:Person a owl:Class . :age a owl:DatatypeProperty ; rdfs:domain :Person ; rdfs:range xsd:integer .'''

def test_current_and_history_reads_use_the_same_persisted_summary(tmp_path):
    app=create_app(tmp_path/'summary-read',HashingEncoder())
    with TestClient(app) as client:
        p=client.post('/api/projects',json={'name':'summary-read','use_default_ontology':False}).json()['id']
        seed_ontology(app.state.service.repository, p,TTL,{'classes':[{'id':'https://test/Person'}],
            'relations':[],'attributes':[{'id':'https://test/age','name':'age','label':'age','description':''}]})
        current=client.get('/api/projects/'+p+'/ontology').json()
        attr=current['summary']['attributes'][0]
        assert attr['domain']==['https://test/Person']
        assert attr['range']==['http://www.w3.org/2001/XMLSchema#integer']
        historical=client.get('/api/projects/'+p+'/ontologies').json()['versions'][0]
        assert historical['summary']['attributes'][0]['domain']==['https://test/Person']
        # Current writes persist the complete summary once; reads reuse it.
        stored=app.state.service.repository.get_ontology(p)
        assert stored['summary']==current['summary']==historical['summary']


def test_legacy_published_attributes_are_editable_without_republishing_or_rewriting(tmp_path):
    app=create_app(tmp_path/'legacy-summary-read',HashingEncoder())
    turtle=TTL+'\n:age rdfs:label "年龄"@zh ; rdfs:comment "周岁年龄"@zh .'
    with TestClient(app) as client:
        p=client.post('/api/projects',json={'name':'legacy-summary','use_default_ontology':False}).json()['id']
        summary=Ontology(turtle).summary()
        del summary['attributes'][0]['annotations']
        repository=app.state.service.repository
        repository=vars(repository).get('_ApplicationData__repository',repository)
        with repository._transaction():
            repository._insert_ontology_version(p,turtle,summary,{'write_path':'test_fixture'})
        current=client.get('/api/projects/'+p+'/ontology').json()
        historical=client.get('/api/projects/'+p+'/ontologies').json()['versions'][0]
        attribute=current['summary']['attributes'][0]
        assert any(a['value']=='年龄' and a.get('language')=='zh' for a in attribute['annotations'])
        assert current['summary']==historical['summary']
        assert 'annotations' not in repository.get_ontology(p)['summary']['attributes'][0]
