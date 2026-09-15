from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.embeddings import HashingEncoder
from knowledge_service.ontology import Ontology
from knowledge_service.semantica_adapter import SemanticaExtractor
from knowledge_service.governance import writable
import pytest

TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
:Person a owl:Class . :Company a owl:Class . :knows a owl:ObjectProperty .
:count a owl:DatatypeProperty ; rdfs:domain :Person ; rdfs:range xsd:integer .
:companyCount a owl:DatatypeProperty ; rdfs:domain :Company ; rdfs:range xsd:integer .
:name a owl:DatatypeProperty ; rdfs:range xsd:string .'''


def test_entity_relation_attribute_dependency_and_version_lifecycle(tmp_path,monkeypatch):
    def extract(self,text,ontology):
        self.review_candidates=[
            dict(kind='entity',record_id='a',text='甲',proposed_type='NewPerson',confidence=.9),
            dict(kind='relation',subject_id='a',object_id='b',subject='甲',object='乙',predicate='knows'),
            dict(kind='attribute',entity_id='a',subject='甲',proposed_type='count',value=2,attribute_evidence='2')]
        return [dict(id='b',kind='entity',type='https://test/Person',text='乙')]
    monkeypatch.setattr(SemanticaExtractor,'extract',extract)
    app=create_app(tmp_path/'typed.sqlite',HashingEncoder())
    with TestClient(app) as c:
        p=c.post('/api/projects',json={'name':'typed','use_default_ontology':False}).json()['id']
        base='/api/projects/'+p
        assert c.post(base+'/ontologies',json={'turtle':TTL}).status_code==201
        result=c.post(base+'/documents',json={'title':'样例','text':'甲认识乙，数量2','resolve_entities':False})
        assert result.status_code==201,result.text
        assert result.json()['pending_reviews']==3
        def candidates():return c.get(base+'/reviews').json()['reviews']
        def decide(kind,target,action='approve'):
            item=next(r for r in candidates() if r['kind']==kind)
            return c.post(base+'/reviews/'+item['document_id']+'/'+item['id'],json={
                'action':action,'note':'核对原文','target_type':target,'expected_version':item['document_version'],
                'expected_entity_version':item['entity_version']})
        assert next(r for r in candidates() if r['kind']=='relation')['blocked']
        assert decide('relation','knows').status_code==422
        assert decide('attribute','count').status_code==422
        response=decide('entity','Person');assert response.status_code==200,response.text
        entity_id=response.json()['record_id']
        assert not any(r['blocked'] for r in candidates())
        assert decide('attribute','companyCount').status_code==422
        assert decide('attribute','name').status_code==422
        assert decide('relation','knows').status_code==200
        assert decide('attribute','count').status_code==200
        versions=app.state.service.repository.history(p,entity_id)
        assert len(versions)==2
        assert versions[0]['properties']=={}
        assert versions[1]['properties']=={'https://test/count':2}
        assert versions[1]['metadata']['attribute_reviews'][0]['evidence']=='2'
        assert all(r['status']=='approved' for r in candidates())


def test_adapter_queues_unknown_entity_and_dependent_known_relation(monkeypatch):
    import knowledge_service.semantica_adapter as adapter
    from semantica.semantic_extract import methods
    from semantica.semantic_extract.types import Entity,Relation
    from knowledge_service.attribute_extraction import AttributeProposal
    import knowledge_service.attribute_extraction as attributes
    for key in ['KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL']:monkeypatch.setenv(key,'test')
    a,b=Entity('甲','NewPerson',0,1),Entity('乙','Person',2,3)
    monkeypatch.setattr(methods,'extract_entities_llm',lambda *args,**kw:[a,b])
    monkeypatch.setattr(adapter,'_extract_relations_guided',lambda *args,**kw:[Relation(a,'knows',b)])
    monkeypatch.setattr(attributes,'extract_attributes',lambda *args:[AttributeProposal(entity_index=0,attribute='count',value=2,evidence='2',confidence=.8)])
    extractor=SemanticaExtractor();extractor.include_attributes=True
    records=extractor.extract('甲乙2',Ontology(TTL))
    assert len(records)==1 and records[0]['text']=='乙'
    assert [r['kind'] for r in extractor.review_candidates]==['entity','relation','attribute']
    assert extractor.review_candidates[0]['record_id']==extractor.review_candidates[1]['subject_id']
    assert extractor.review_candidates[0]['record_id']==extractor.review_candidates[2]['entity_id']


def test_attribute_provider_isolates_bad_items_and_marks_unverified_evidence(monkeypatch):
    from semantica.semantic_extract import providers
    from semantica.semantic_extract.types import Entity
    from knowledge_service.attribute_extraction import extract_attributes,AttributeResponse
    class Provider:
        def generate_typed(self,prompt,schema):
            assert schema==AttributeResponse and 'ontology_attributes' in prompt
            return AttributeResponse(attributes=[
                dict(entity_index=0,attribute='count',value=2,evidence='甲\n数量 2',confidence=.9),
                dict(entity_index=99,attribute='count',value=3,evidence='数量 2',confidence=.8),
                dict(entity_index='bad',attribute='count',value=4,evidence='数量 2',confidence=.7),
                'not-an-object'])
    monkeypatch.setattr(providers,'create_provider',lambda *args,**kw:Provider())
    result=extract_attributes('甲 数量 2',[Entity('甲','Person',0,1)],Ontology(TTL),
        dict(provider='openai',llm_model='test',api_key='test',base_url='test'))
    assert len(result.attributes)==1
    assert result.attributes[0].evidence_status=='normalized'
    assert result.attributes[0].evidence=='甲 数量 2'
    assert result.diagnostics=={'returned':4,'accepted':1,'unverified_evidence':0,
        'skipped_invalid_schema':2,'skipped_invalid_entity':1}


def test_attribute_provider_keeps_nonmatching_evidence_for_review(monkeypatch):
    from semantica.semantic_extract import providers
    from semantica.semantic_extract.types import Entity
    from knowledge_service.attribute_extraction import extract_attributes,AttributeResponse
    class Provider:
        def generate_typed(self,prompt,schema):
            return AttributeResponse(attributes=[dict(entity_index=0,attribute='count',value=2,
                evidence='模型声称的证据',confidence=.9)])
    monkeypatch.setattr(providers,'create_provider',lambda *args,**kw:Provider())
    result=extract_attributes('甲的数量是2',[Entity('甲','Person',0,1)],Ontology(TTL),
        dict(provider='openai',llm_model='test',api_key='test',base_url='test'))
    assert len(result.attributes)==1
    assert result.attributes[0].evidence_status=='unverified'
    assert result.diagnostics['unverified_evidence']==1


@pytest.mark.parametrize('mode',['rejected_parent','stale_entity','existing_value','embedding_failure'])
def test_review_safety_for_attribute_dependencies(tmp_path,monkeypatch,mode):
    def extract(self,text,ontology):
        self.review_candidates=[dict(kind='entity',record_id='a',text='甲',proposed_type='Unknown'),
            dict(kind='attribute',entity_id='a',subject='甲',proposed_type='count',value=2)]
        return []
    monkeypatch.setattr(SemanticaExtractor,'extract',extract)
    app=create_app(tmp_path/'safety.sqlite',HashingEncoder())
    with TestClient(app) as client:
        p=client.post('/api/projects',json={'name':'safety','use_default_ontology':False}).json()['id']
        base='/api/projects/'+p;repo=app.state.service.repository
        client.post(base+'/ontologies',json={'turtle':TTL})
        result=client.post(base+'/documents',json={'title':'原文','text':'甲数量2','resolve_entities':False})
        assert result.status_code==201,result.text
        def items():return client.get(base+'/reviews').json()['reviews']
        def send(item,target,action='approve'):
            return client.post(base+'/reviews/'+item['document_id']+'/'+item['id'],json={
                'action':action,'note':'核实','target_type':target,'expected_version':item['document_version'],
                'expected_entity_version':item['entity_version']})
        parent=items()[0]
        assert send(parent,'Person','reject' if mode=='rejected_parent' else 'approve').status_code==200
        attribute=items()[1]
        if mode=='rejected_parent':
            assert attribute['blocked']
            assert send(attribute,'count').status_code==422
        else:
            entity=repo.history(p,parent['record_id'])[-1]
            if mode in ('stale_entity','existing_value'):
                updated=writable(entity);updated['properties']={'https://test/count':99}
                repo.put_record(p,updated,expected_version=entity['version'])
            if mode=='existing_value':attribute=items()[1]
            if mode=='embedding_failure':
                def fail(*args):raise RuntimeError('test unavailable')
                monkeypatch.setattr(app.state.service.encoder,'encode',fail)
            response=send(attribute,'count')
            assert response.status_code=={'stale_entity':409,'existing_value':422,'embedding_failure':503}[mode],response.text
            latest=repo.history(p,parent['record_id'])[-1]
            assert latest['properties'].get('https://test/count')!=2
        assert items()[1]['status']=='pending'
