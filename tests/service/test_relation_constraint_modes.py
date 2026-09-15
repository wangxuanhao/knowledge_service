import pytest
from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.embeddings import HashingEncoder
from knowledge_service.ontology import Ontology

TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:Entity a owl:Class . :Person a owl:Class ; rdfs:subClassOf :Entity .
:Company a owl:Class ; rdfs:subClassOf :Entity . :Product a owl:Class ; rdfs:subClassOf :Entity .
:worksAt a owl:ObjectProperty ; rdfs:label "任职于" ; rdfs:comment "人员任职于公司" ;
  rdfs:domain :Person ; rdfs:range :Company .'''


def test_guided_prompt_keeps_schema_guidance_but_relation_context_is_source_only(monkeypatch):
    import knowledge_service.semantica_adapter as adapter
    from semantica.semantic_extract.types import Entity
    from semantica.semantic_extract import providers
    captured={}
    class Provider:
        def generate_typed(self,prompt,schema,**kwargs):
            captured['prompt']=prompt
            return schema(relations=[{'subject':'张三','predicate':'worksAt','object':'甲公司',
                'confidence':.9,'evidence':'张三任职于甲公司'}])
    monkeypatch.setattr(providers,'create_provider',lambda *args,**kwargs:Provider())
    entities=[Entity('张三','Person',0,2),Entity('甲公司','Company',5,8)]
    summary=Ontology(TTL).summary()
    relations=adapter._extract_relations_guided('张三任职于甲公司',entities,summary,['worksAt'],{
        'provider':'openai','llm_model':'test','api_key':'test','base_url':'http://test'})
    assert 'domain' in captured['prompt'] and 'range' in captured['prompt'] and '人员任职于公司' in captured['prompt']
    assert relations[0].context=='张三任职于甲公司'
    assert 'ONTOLOGY RELATION GUIDANCE' not in relations[0].context

@pytest.fixture
def extraction(monkeypatch):
    import knowledge_service.semantica_adapter as adapter
    from semantica.semantic_extract import methods
    from semantica.semantic_extract.types import Entity,Relation
    for key in ['KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL']:monkeypatch.setenv(key,'test')
    person=Entity('张三','Person',0,2,.95);product=Entity('商品','Product',3,5,.9);observed=[]
    monkeypatch.setattr(methods,'extract_entities_llm',lambda *args,**kwargs:[person,product])
    def relations(text,entities,*args,**kwargs):
        observed.append(text)
        return [Relation(person,'worksAt',product,.88,context=text)]
    monkeypatch.setattr(adapter,'_extract_relations_guided',relations)
    return observed

def test_adapter_constraint_modes_and_model_guidance(extraction):
    from knowledge_service.semantica_adapter import SemanticaExtractor
    ontology=Ontology(TTL)
    expected={'review':(2,1,False),'advisory':(3,0,True),'strict':(3,0,False),'off':(3,0,False)}
    for mode,(row_count,candidate_count,warning) in expected.items():
        adapter=SemanticaExtractor();adapter.relation_constraint_mode=mode;rows=adapter.extract('张三与商品',ontology)
        assert len(rows)==row_count and len(adapter.review_candidates)==candidate_count
        relation=next((r for r in rows if r['kind']=='relation'),None)
        assert bool(relation and relation['metadata'].get('constraint_warning')) is warning
        if relation:
            assert relation['text']=='张三 worksAt 商品'
            assert relation['metadata']['evidence']=='张三与商品'
        if mode=='review':
            candidate=adapter.review_candidates[0]
            assert candidate['proposed_type']=='https://test/worksAt'
            assert candidate['constraint_issues']==[{'endpoint':'object_id','actual_type':'https://test/Product','expected_types':['https://test/Company']}]
    assert extraction and all(text=='张三与商品' for text in extraction)

@pytest.mark.parametrize('mode,status,edges,reviews,warning',[
    ('review',201,0,1,False),('advisory',201,1,0,True),('off',201,1,0,False),('strict',503,0,0,False)])
def test_ingestion_policy_isolated_per_document(tmp_path,extraction,mode,status,edges,reviews,warning):
    app=create_app(tmp_path/(mode+'.sqlite'),HashingEncoder())
    with TestClient(app) as client:
        p=client.post('/api/projects',json={'name':mode,'use_default_ontology':False}).json()['id'];base='/api/projects/'+p
        client.post(base+'/ontologies',json={'turtle':TTL})
        response=client.post(base+'/documents',json={'title':'冲突示例','text':'张三与商品','resolve_entities':False,'relation_constraint_mode':mode})
        assert response.status_code==status,response.text
        rows=app.state.service.repository.current_records(p);relations=[r for r in rows if r['kind']=='relation']
        assert len(relations)==edges
        assert bool(relations and relations[0]['metadata'].get('constraint_warning')) is warning
        assert len(client.get(base+'/reviews').json()['reviews'])==reviews
        if mode=='strict':assert next(r for r in rows if r['kind']=='document')['metadata']['status']=='failed'

def test_structured_write_remains_strict(tmp_path):
    with TestClient(create_app(tmp_path/'structured.sqlite',HashingEncoder())) as client:
        p=client.post('/api/projects',json={'name':'strict-api','use_default_ontology':False}).json()['id'];base='/api/projects/'+p
        client.post(base+'/ontologies',json={'turtle':TTL})
        response=client.post(base+'/records',json={'records':[
            {'id':'p','kind':'entity','text':'张三','type':'Person'}, {'id':'x','kind':'entity','text':'商品','type':'Product'},
            {'id':'r','kind':'relation','text':'错误组合','type':'worksAt','subject_id':'p','object_id':'x'}]})
        assert response.status_code==422 and 'range' in response.text
