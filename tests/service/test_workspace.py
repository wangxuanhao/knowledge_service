from fastapi.testclient import TestClient
import pytest
from ontology_seed import seed_ontology
from knowledge_service.services.ontology import Ontology
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.core.config import load_environment
from keyword_index_fixture import install_keyword_store
from test_retrieval import activate_index


def ready_linked_index(service, project, monkeypatch, native_calls=None):
    activate_index(service.repository, project, service.encoder)
    install_keyword_store(service, monkeypatch)
    if native_calls is not None:
        search = service.milvus_store.search
        def observed(*args, **options):
            native_calls.append(options)
            return search(*args, **options)
        monkeypatch.setattr(service.milvus_store, 'search', observed)


def test_linked_scope_and_ontology_and_index(tmp_path, monkeypatch):
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
        unavailable=c.post(f'/api/projects/{p}/explore',json=body)
        assert unavailable.status_code==503 and 'Milvus' in unavailable.json()['detail']
        ready_linked_index(c.app.state.service,p,monkeypatch)
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
        assert '真实语义编码模型' in unavailable.json()['detail']
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



def test_keyword_explore_matches_chinese_ontology_label(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path/'labels.sqlite',HashingEncoder())) as c:
        p=c.post('/api/projects',json={'name':'中文标签检索','use_default_ontology':False}).json()['id']
        ttl='''@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:test:Merchant> a owl:Class ; rdfs:label "Merchant"@en, "商户类型"@zh .'''
        ontology=seed_ontology(c.app.state.service.repository,p,ttl,Ontology(ttl).summary())
        c.app.state.service.repository.put_record(p,{'id':'shop','kind':'entity','text':'甲店',
            'type':'urn:test:Merchant','ontology_id':ontology['id'],'metadata':{}})
        ready_linked_index(c.app.state.service,p,monkeypatch)
        result=c.post(f'/api/projects/{p}/explore',json={'query':'商户类型'}).json()
        assert [x['id'] for x in result['nodes']]==['shop']
        assert result['nodes'][0]['type_label']=='商户类型'


def test_linked_native_retrieval_preserves_candidate_scope_and_index_guards(tmp_path, monkeypatch):
    from knowledge_service.services.retrieval import RetrievalEngine
    from knowledge_service.utils.filters import matches_filter

    ttl='''@prefix : <urn:test:> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    :Thing a owl:Class ; rdfs:label "事物类型"@zh .
    :Child a owl:Class ; rdfs:subClassOf :Thing . :Other a owl:Class .
    :mentions a owl:ObjectProperty . :ignores a owl:ObjectProperty .'''
    with TestClient(create_app(tmp_path/'linked-native.sqlite',HashingEncoder())) as client:
        project=client.post('/api/projects',json={'name':'linked-native','use_default_ontology':False}).json()['id']
        service=client.app.state.service
        repo=service.repository
        release=seed_ontology(repo,project,ttl,Ontology(ttl).summary())['id']
        other_release=seed_ontology(repo,project,ttl,Ontology(ttl).summary())['id']
        metadata={'city':'北京','applicability':{'platforms':{'mode':'specific','values':['wanted']}}}
        records=[
            {'id':'a','kind':'entity','type':'urn:test:Thing','text':'target'},
            {'id':'b','kind':'entity','type':'urn:test:Child','text':'neighbor'},
            {'id':'r','kind':'relation','type':'urn:test:mentions','text':'connection','subject_id':'a','object_id':'b'},
            {'id':'wrong-predicate','kind':'relation','type':'urn:test:ignores','text':'target','subject_id':'a','object_id':'b'},
            {'id':'wrong-type','kind':'entity','type':'urn:test:Other','text':'target'},
            {'id':'wrong-release','kind':'entity','type':'urn:test:Thing','text':'target','ontology_id':other_release},
            {'id':'wrong-city','kind':'entity','type':'urn:test:Thing','text':'target','metadata':{**metadata,'city':'上海'}},
            {'id':'wrong-platform','kind':'entity','type':'urn:test:Thing','text':'target',
             'metadata':{**metadata,'applicability':{'platforms':{'mode':'specific','values':['other']}}}},
        ]
        saved=repo.put_batch(project,[{'ontology_id':release,'valid_from':'2025-01-01',
                                      'metadata':metadata,**record} for record in records])
        endpoint=f'/api/projects/{project}/explore'
        body={'query':'target','filters':{'field':'city','op':'eq','value':'北京'},
              'valid_at':'2026-01-01','include_unknown':False,
              'ontology_scope':'ids','ontology_ids':[release],
              'applicability':metadata['applicability'],'entity_type':'urn:test:Thing',
              'predicate':'urn:test:mentions','hops':1}
        # An unqueried scoped graph needs no search index.
        graph=client.post(endpoint,json={**body,'query':''})
        assert graph.status_code==200,graph.text
        assert {row['id'] for row in graph.json()['nodes']}=={'a','b'}
        native_calls,engine_calls=[],[]
        ready_linked_index(service,project,monkeypatch,native_calls)
        search=RetrievalEngine.search
        def observed(engine,*args,**options):
            engine_calls.append(options)
            return search(engine,*args,**options)
        monkeypatch.setattr(RetrievalEngine,'search',observed)
        response=client.post(endpoint,json=body)
        assert response.status_code==200,response.text
        result=response.json()
        assert {row['id'] for row in result['nodes']}=={'a','b'}
        assert {row['id'] for row in result['edges']}=={'r'}
        assert {row['id'] for row in result['hits']}=={'a'}
        assert result['matched_ids']==['a'] and result['candidate_count']==3
        options=engine_calls[-1]
        assert 'candidates' not in options
        assert not {'_candidate_ids','_lexical_aliases'} & options['scope'].keys()
        qualified={row['id'] for row in saved if matches_filter(row,options['scope']['filters'])}
        assert qualified=={'a','b','r'}
        assert options['scope']['ontology_ids']==[release]
        assert options['scope']['applicability']['platforms']['values']==['wanted']
        assert native_calls and all(call['retrieval_mode']=='keyword' for call in native_calls)
        assert all(call['scope']['ontology_ids']==[release] for call in native_calls)
        exact=client.post(endpoint,json={**body,'include_subclasses':False}).json()
        assert {row['id'] for row in exact['nodes']}=={'a'} and exact['edges']==[]
        entities=client.post(endpoint,json={**body,'kinds':['entity']}).json()
        assert {row['id'] for row in entities['nodes']}=={'a'} and entities['edges']==[]
        assert engine_calls[-1]['content_channels']==['entity']
        assert engine_calls[-1]['content_k']=={'entity':5}
        deepest=body['filters']
        for _ in range(16):
            deepest={'or':[deepest]}
        accepted=client.post(endpoint,json={**body,'filters':deepest})
        assert accepted.status_code==200,accepted.text
        assert {row['id'] for row in accepted.json()['nodes']}=={'a','b'}
        assert engine_calls[-1]['scope']['filters']=={
            'field':'id','op':'in','value':['a','b','r']}
        before=len(native_calls)
        rejected=client.post(endpoint,json={**body,'filters':{'or':[deepest]}})
        assert rejected.status_code==422 and '16' in rejected.json()['detail']
        assert len(native_calls)==before
        before=len(native_calls)
        monkeypatch.setattr(service,'milvus_store',None)
        historical=client.post(endpoint,json={**body,'known_at':max(row['recorded_at'] for row in saved)})
        assert historical.status_code==200,historical.text
        assert {row['id'] for row in historical.json()['nodes']}=={'a','b'}
        assert historical.json()['backends']['postgres_history']['active']
        assert len(native_calls)==before
        ready_linked_index(service,project,monkeypatch,native_calls)
        original=next(row for row in records if row['id']=='a')
        repo.put_record(project,{'ontology_id':release,'valid_from':'2025-01-01',
            'metadata':metadata,**original,'text':'target revised'},expected_version=1)
        before=len(native_calls)
        stale=client.post(endpoint,json=body)
        assert stale.status_code==503 and '追平' in stale.json()['detail']
        assert len(native_calls)==before
        ready_linked_index(service,project,monkeypatch,native_calls)
        assert client.post(endpoint,json=body).status_code==200


@pytest.mark.parametrize('race',['entity_type','predicate','type_channel','no_hit','blank_query'])
def test_linked_explore_rejects_mixed_native_frames_and_preserves_blank_graph(tmp_path,monkeypatch,race):
    from knowledge_service.services.governance import writable

    ttl='''@prefix : <urn:test:> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    :Thing a owl:Class ; rdfs:label "事物类型"@zh . :Other a owl:Class .
    :mentions a owl:ObjectProperty . :ignores a owl:ObjectProperty .'''
    with TestClient(create_app(tmp_path/'linked-race.sqlite',HashingEncoder())) as client:
        project=client.post('/api/projects',json={'name':'linked-race','use_default_ontology':False}).json()['id']
        service=client.app.state.service
        repo=service.repository
        release=seed_ontology(repo,project,ttl,Ontology(ttl).summary())['id']
        records=[
            {'id':'a','kind':'entity','type':'urn:test:Thing','text':'target'},
            {'id':'b','kind':'entity','type':'urn:test:Thing','text':'neighbor'},
            {'id':'r','kind':'relation','type':'urn:test:mentions','text':'connection',
             'subject_id':'a','object_id':'b'},
        ]
        saved=repo.put_batch(project,[{'ontology_id':release,'metadata':{},**row} for row in records])
        frozen={row['id']:row['version_id'] for row in saved}
        native_calls=[]
        ready_linked_index(service,project,monkeypatch,native_calls)
        before=repo.projections.project_watermark(project)
        scoped=service.scoped
        changes=[]

        def rewrite_after_preload(project,scope):
            rows=scoped(project,scope)
            target='r' if race=='predicate' else 'b' if race in {'no_hit','blank_query'} else 'a'
            old=next(row for row in saved if row['id']==target)
            revised=writable(old)
            if race=='entity_type':
                revised['type']='urn:test:Other'
            elif race=='predicate':
                revised['type']='urn:test:ignores'
            else:
                revised['text']+=' revised'
            changes.append(repo.put_record(project,revised,expected_version=old['version']))
            # The index really catches up before RetrievalEngine selects it.
            ready_linked_index(service,project,monkeypatch,native_calls)
            assert repo.projections.search_index(project,3)['watermark']>before
            return rows

        monkeypatch.setattr(service,'scoped',rewrite_after_preload)
        query={'type_channel':'事物类型','no_hit':'nothing','blank_query':''}.get(race,'target')
        body={'query':query,'entity_type':'urn:test:Thing','predicate':'urn:test:mentions','hops':1}
        endpoint=f'/api/projects/{project}/explore'
        response=client.post(endpoint,json=body)
        assert len(changes)==1
        if race=='blank_query':
            assert response.status_code==200,response.text
            graph=response.json()
            assert {row['id']:row['version_id'] for row in graph['nodes']}=={
                key:frozen[key] for key in ('a','b')}
            assert {row['id']:row['version_id'] for row in graph['edges']}=={'r':frozen['r']}
            assert native_calls==[]
        else:
            assert response.status_code==503,response.text
            assert '知识已变化' in response.json()['detail']
            assert 'nodes' not in response.json() and 'hits' not in response.json()
        monkeypatch.setattr(service,'scoped',scoped)
        fresh=client.post(endpoint,json=body)
        assert fresh.status_code==200,fresh.text


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
