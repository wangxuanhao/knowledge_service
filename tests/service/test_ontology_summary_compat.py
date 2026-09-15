from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.embeddings import HashingEncoder

TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
:Person a owl:Class . :age a owl:DatatypeProperty ; rdfs:domain :Person ; rdfs:range xsd:integer .'''

def test_old_stored_summary_is_rebuilt_on_current_and_history_reads(tmp_path):
    app=create_app(tmp_path/'compat.sqlite',HashingEncoder())
    with TestClient(app) as client:
        p=client.post('/api/projects',json={'name':'compat','use_default_ontology':False}).json()['id']
        app.state.service.repository.save_ontology(p,TTL,{'classes':[{'id':'https://test/Person'}],
            'relations':[],'attributes':[{'id':'https://test/age','name':'age','label':'age','description':''}]})
        current=client.get('/api/projects/'+p+'/ontology').json()
        attr=current['summary']['attributes'][0]
        assert attr['domain']==['https://test/Person']
        assert attr['range']==['http://www.w3.org/2001/XMLSchema#integer']
        historical=client.get('/api/projects/'+p+'/ontologies').json()['versions'][0]
        assert historical['summary']['attributes'][0]['domain']==['https://test/Person']
        # The authoritative Turtle and stored history are not rewritten by compatibility reads.
        stored=app.state.service.repository.get_ontology(p)
        assert 'domain' not in stored['summary']['attributes'][0]
