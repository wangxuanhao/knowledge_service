from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.embeddings import HashingEncoder
from knowledge_service.environment import load_environment


def test_linked_scope_and_ontology_and_index(tmp_path):
    with TestClient(create_app(tmp_path/'w.sqlite', HashingEncoder())) as c:
        p=c.post('/api/projects',json={'name':'workspace'}).json()['id']
        repo=c.app.state.service.repository
        o=repo.get_ontology(p)
        repo.put_batch(p,[
            {'id':'a','kind':'entity','text':'商户','type':'Merchant','metadata':{'city':'北京'},'valid_from':'2025-01-01'},
            {'id':'b','kind':'entity','text':'平台','type':'Platform','metadata':{'city':'北京'},'valid_from':'2025-01-01'},
            {'id':'r','kind':'relation','text':'商户关联平台','type':'onboards','subject_id':'a','object_id':'b','metadata':{'city':'北京'},'valid_from':'2025-01-01'},
            {'id':'secret','kind':'entity','text':'商户秘密','type':'Merchant','metadata':{'city':'上海'}},
        ])
        body={'query':'商户','filters':{'field':'city','op':'eq','value':'北京'},'valid_at':'2026-01-01'}
        result=c.post(f'/api/projects/{p}/explore',json=body)
        assert result.status_code==200,result.text
        assert {r['id'] for r in result.json()['nodes']}=={'a','b'}
        assert result.json()['mode']=='keyword'
        assert c.post(f'/api/projects/{p}/explore',json={**body,'query':'不存在'}).json()['nodes']==[]
        assert c.post(f'/api/projects/{p}/explore',json={**body,'valid_at':'2024-01-01'}).json()['nodes']==[]
        opts=c.post(f'/api/projects/{p}/entity-options',json={'filters':body['filters']}).json()
        assert {r['id'] for r in opts['entities']}=={'a','b'}
        assert c.post(f'/api/projects/{p}/explore',json={**body,'semantic':True}).status_code==503
        records=repo.current_records(p)
        repo.store_embeddings(p,records,HashingEncoder().encode([r['text'] for r in records]),HashingEncoder.identity)
        assert len(repo.history(p,'a'))==1
        assert c.post(f'/api/projects/{p}/explore',json={**body,'semantic':True}).status_code==200
        term={'kind':'class','uri':'urn:test:Shop','label':'门店','expected_ontology_id':o['id']}
        saved=c.post(f'/api/projects/{p}/ontology/terms',json=term)
        assert saved.status_code==201,saved.text
        assert any(r['label']=='门店' for r in saved.json()['summary']['classes'])
        assert c.post(f'/api/projects/{p}/ontology/terms',json=term).status_code==409


def test_keyword_explore_matches_chinese_ontology_label(tmp_path):
    with TestClient(create_app(tmp_path/'labels.sqlite',HashingEncoder())) as c:
        p=c.post('/api/projects',json={'name':'中文标签检索','use_default_ontology':False}).json()['id']
        ttl='''@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:test:Merchant> a owl:Class ; rdfs:label "Merchant"@en, "商户类型"@zh .'''
        ontology=c.post(f'/api/projects/{p}/ontologies',json={'turtle':ttl}).json()
        c.app.state.service.repository.put_record(p,{'id':'shop','kind':'entity','text':'甲店',
            'type':'urn:test:Merchant','ontology_id':ontology['id'],'metadata':{}})
        result=c.post(f'/api/projects/{p}/explore',json={'query':'商户类型'}).json()
        assert [x['id'] for x in result['nodes']]==['shop']
        assert result['nodes'][0]['type_label']=='商户类型'


def test_environment_never_loads_example_model_values(tmp_path,monkeypatch):
    for key in ('KG_NEO4J_PASSWORD','KG_LLM_API_KEY','KG_NEO4J_DATABASE'):
        monkeypatch.delenv(key,raising=False)
    (tmp_path/'.env.example').write_text('KG_NEO4J_PASSWORD=test-secret\nKG_LLM_API_KEY=example-do-not-use\nKG_NEO4J_DATABASE=example\n')
    (tmp_path/'.env').write_text('KG_NEO4J_DATABASE=local\n')
    loaded=load_environment(tmp_path)
    import os
    assert os.environ['KG_NEO4J_DATABASE']=='local'
    assert 'KG_LLM_API_KEY' not in os.environ
    assert 'test-secret' not in str(loaded)
    for key in loaded:monkeypatch.delenv(key,raising=False)
