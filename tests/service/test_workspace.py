from fastapi.testclient import TestClient
from ontology_seed import seed_ontology
from knowledge_service.services.ontology import Ontology
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.core.config import load_environment


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
        timeline=c.get(f'/api/projects/{p}/timeline').json()
        assert timeline['total']==1
        assert timeline['events'][0]['changed']==4
        assert timeline['events'][0]['created']==4
        assert timeline['events'][0]['revised']==0
        # HashingEncoder cannot serve semantic requests, even when explicit keyword works.
        unavailable=c.post(f'/api/projects/{p}/explore',json={**body,'semantic':True})
        assert unavailable.status_code==503
        assert 'Milvus 向量索引' in unavailable.json()['detail']
        assert len(repo.history(p,'a'))==1
        assert c.post(f'/api/projects/{p}/explore',json=body).json()['mode']=='keyword'
        base=f'/api/projects/{p}'
        created=c.post(base+'/ontology-drafts',json={'base_ontology_id':o['id'],
            'source_kind':'manual','title':'新增门店'}).json()
        saved=c.post(base+'/ontology-drafts/'+created['id']+'/commands',json={
            'expected_revision':created['revision'],'command':{'action':'batch','commands':[
                {'action':'create_term','target_iri':'urn:test:Shop','kind':'class'},
                {'action':'add_annotation','target_iri':'urn:test:Shop',
                 'predicate':'http://www.w3.org/2000/01/rdf-schema#label','value':'门店'}]}})
        assert saved.status_code==200,saved.text
        draft=saved.json()
        assert any(r['label']=='门店' for r in draft['ontology']['classes'])
        assert 'deprecation' not in draft and 'Deprecation' not in saved.headers
        assert repo.get_ontology(p)['id']==o['id']
        assert not any(r['label']=='门店' for r in repo.get_ontology(p)['summary']['classes'])
        assert c.post(base+'/ontology-drafts',json={'base_ontology_id':'stale',
            'source_kind':'manual','title':'过期'}).status_code==409



def test_keyword_explore_matches_chinese_ontology_label(tmp_path):
    with TestClient(create_app(tmp_path/'labels.sqlite',HashingEncoder())) as c:
        p=c.post('/api/projects',json={'name':'中文标签检索','use_default_ontology':False}).json()['id']
        ttl='''@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:test:Merchant> a owl:Class ; rdfs:label "Merchant"@en, "商户类型"@zh .'''
        ontology=seed_ontology(c.app.state.service.repository,p,ttl,Ontology(ttl).summary())
        c.app.state.service.repository.put_record(p,{'id':'shop','kind':'entity','text':'甲店',
            'type':'urn:test:Merchant','ontology_id':ontology['id'],'metadata':{}})
        result=c.post(f'/api/projects/{p}/explore',json={'query':'商户类型'}).json()
        assert [x['id'] for x in result['nodes']]==['shop']
        assert result['nodes'][0]['type_label']=='商户类型'


def test_environment_never_loads_example_model_values(tmp_path,monkeypatch):
    # 本用例专门验证「.env 会被加载，且 .env.example 不会被当配置源」，
    # 因此必须显式关掉测试套件默认的 KG_SKIP_DOTENV（见 tests/conftest.py）。
    monkeypatch.setenv('KG_SKIP_DOTENV','0')
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
