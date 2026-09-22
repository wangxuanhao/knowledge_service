from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology
from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
from knowledge_service.services.governance import writable
import pytest

TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
:Person a owl:Class . :Company a owl:Class . :knows a owl:ObjectProperty .
:count a owl:DatatypeProperty ; rdfs:domain :Person ; rdfs:range xsd:integer .
:companyCount a owl:DatatypeProperty ; rdfs:domain :Company ; rdfs:range xsd:integer .
:name a owl:DatatypeProperty ; rdfs:range xsd:string .'''

TTL_MAX_ONE=TTL+'''\n@prefix sh: <http://www.w3.org/ns/shacl#> .
:PersonShape a sh:NodeShape ; sh:targetClass :Person ;
  sh:property [ sh:path :count ; sh:maxCount 1 ] .'''


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
        base='/api/projects/'+p;repo=app.state.service.repository
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
        before_attribute=repo.history(p,entity_id)[-1]
        attribute_candidate_id=next(r for r in candidates() if r['kind']=='attribute')['id']
        assert decide('attribute','count').status_code==200
        versions=app.state.service.repository.history(p,entity_id)
        assert versions==[before_attribute]
        attribute=next(r for r in repo.current_records(p) if r['kind']=='attribute')
        assert attribute['id']=='reviewattr_'+attribute_candidate_id
        assert attribute['subject_id']==entity_id
        assert attribute['type']=='https://test/count'
        assert attribute['value']==2
        assert attribute['datatype']=='http://www.w3.org/2001/XMLSchema#integer'
        assertion=repo.get_assertion(p,next(r for r in candidates() if r['kind']=='attribute')['id'])
        assert assertion['quote']=='甲认识乙，数量2'
        assert assertion['canonical_record_id']==attribute['id']
        assert all(r['status']=='approved' for r in candidates())


def test_adapter_queues_unknown_entity_and_dependent_known_relation(monkeypatch):
    import knowledge_service.integrations.semantica_adapter as adapter
    from semantica.semantic_extract import methods
    from semantica.semantic_extract.types import Entity,Relation
    from knowledge_service.services.attribute_extraction import AttributeProposal
    import knowledge_service.services.attribute_extraction as attributes
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


def test_max_count_one_conflict_stays_reviewable_and_can_be_rejected(tmp_path,monkeypatch):
    def extract(self,text,ontology):
        self.review_candidates=[dict(kind='entity',record_id='person-1',text='甲',proposed_type='Person'),
            dict(kind='attribute',entity_id='person-1',subject='甲',
            proposed_type='count',value=2,attribute_evidence='数量 2')]
        return []
    monkeypatch.setattr(SemanticaExtractor,'extract',extract)
    app=create_app(tmp_path/'attribute-conflict.sqlite',HashingEncoder())
    with TestClient(app) as client:
        p=client.post('/api/projects',json={'name':'attribute-conflict','use_default_ontology':False}).json()['id']
        base='/api/projects/'+p
        ontology=client.post(base+'/ontologies',json={'turtle':TTL_MAX_ONE}).json()
        ingested=client.post(base+'/documents',json={'title':'新证据','text':'甲数量2','resolve_entities':False})
        assert ingested.status_code==201,ingested.text
        items=client.get(base+'/reviews').json()['reviews']
        entity=next(r for r in items if r['kind']=='entity')
        approved=client.post(base+'/reviews/'+entity['document_id']+'/'+entity['id'],json={
            'action':'approve','note':'确认实体','target_type':'Person',
            'expected_version':entity['document_version']})
        assert approved.status_code==200,approved.text
        entity_id=approved.json()['record_id']
        written=client.post(base+'/records',json={'records':[
            {'id':'old-count','kind':'attribute','type':'https://test/count','subject_id':entity_id,
             'value':1,'datatype':'http://www.w3.org/2001/XMLSchema#integer','text':'甲 · count = 1',
             'ontology_id':ontology['id']}]} )
        assert written.status_code==201,written.text
        item=next(r for r in client.get(base+'/reviews').json()['reviews'] if r['kind']=='attribute')
        response=client.post(base+'/reviews/'+item['document_id']+'/'+item['id'],json={
            'action':'approve','note':'与当前值冲突','target_type':'count',
            'expected_version':item['document_version'],'expected_entity_version':item['entity_version']})
        assert response.status_code==200,response.text
        assert response.json()=={'status':'contradicting','conflict':{
            'code':'attribute_max_count_one','current_values':[{
                'record_id':'old-count','value':1,
                'datatype':'http://www.w3.org/2001/XMLSchema#integer','version':1,
                'valid_from':None,'valid_until':None}]}}
        current=[r for r in app.state.service.repository.current_records(p)
                 if r['kind']=='attribute' and not r['metadata'].get('_deleted')]
        assert [r['id'] for r in current]==['old-count']
        item=next(r for r in client.get(base+'/reviews').json()['reviews'] if r['kind']=='attribute')
        assert item['status']=='contradicting'
        assert item['conflict']['code']=='attribute_max_count_one'
        rejected=client.post(base+'/reviews/'+item['document_id']+'/'+item['id'],json={
            'action':'reject','note':'保留旧值','expected_version':item['document_version']})
        assert rejected.status_code==200,rejected.text
        assert next(r for r in client.get(base+'/reviews').json()['reviews']
                    if r['kind']=='attribute')['status']=='rejected'


def test_approve_replace_supersedes_supports_and_tombstones_old_attribute_atomically(tmp_path,monkeypatch):
    def extract(self,text,ontology):
        self.review_candidates=[dict(kind='entity',record_id='person-1',text='甲',proposed_type='Person'),
            dict(kind='attribute',entity_id='person-1',subject='甲',
                 proposed_type='count',value=2,attribute_evidence='数量 2')]
        return []
    monkeypatch.setattr(SemanticaExtractor,'extract',extract)
    app=create_app(tmp_path/'attribute-replace.sqlite',HashingEncoder())
    with TestClient(app) as client:
        p=client.post('/api/projects',json={'name':'attribute-replace','use_default_ontology':False}).json()['id']
        base='/api/projects/'+p;repo=app.state.service.repository
        ontology=client.post(base+'/ontologies',json={'turtle':TTL_MAX_ONE}).json()
        assert client.post(base+'/documents',json={
            'title':'新证据','text':'甲数量2','resolve_entities':False}).status_code==201
        entity=next(r for r in client.get(base+'/reviews').json()['reviews'] if r['kind']=='entity')
        approved=client.post(base+'/reviews/'+entity['document_id']+'/'+entity['id'],json={
            'action':'approve','note':'确认实体','target_type':'Person',
            'expected_version':entity['document_version']})
        entity_id=approved.json()['record_id']
        assert client.post(base+'/records',json={'records':[
            {'id':'old-count','kind':'attribute','type':'https://test/count','subject_id':entity_id,
             'value':1,'datatype':'http://www.w3.org/2001/XMLSchema#integer','text':'甲 · count = 1',
             'ontology_id':ontology['id']}]}).status_code==201
        item=next(r for r in client.get(base+'/reviews').json()['reviews'] if r['kind']=='attribute')
        direct=client.post(base+'/reviews/'+item['document_id']+'/'+item['id'],json={
            'action':'approve_replace','note':'不能跳过冲突登记','target_type':'count',
            'expected_version':item['document_version'],'expected_entity_version':item['entity_version'],
            'expected_attribute_versions':{'old-count':1}})
        assert direct.status_code==422,direct.text
        assert repo.get_assertion(p,item['id'])['status']=='pending'
        ordinary={'action':'approve','note':'发现单值冲突','target_type':'count',
            'expected_version':item['document_version'],'expected_entity_version':item['entity_version']}
        assert client.post(base+'/reviews/'+item['document_id']+'/'+item['id'],json=ordinary).status_code==200
        item=next(r for r in client.get(base+'/reviews').json()['reviews'] if r['kind']=='attribute')
        endpoint=base+'/reviews/'+item['document_id']+'/'+item['id']
        stale=client.post(endpoint,json={'action':'approve_replace','note':'接受新值','target_type':'count',
            'expected_version':item['document_version'],'expected_entity_version':item['entity_version'],
            'expected_attribute_versions':{'old-count':99}})
        assert stale.status_code==409,stale.text
        assert repo.get_assertion(p,item['id'])['status']=='contradicting'
        assert not repo.history(p,'old-count')[-1]['metadata'].get('_deleted')
        response=client.post(endpoint,json={'action':'approve_replace','note':'接受新值','target_type':'count',
            'expected_version':item['document_version'],'expected_entity_version':item['entity_version'],
            'expected_attribute_versions':{'old-count':1}})
        assert response.status_code==200,response.text
        new_id=response.json()['record_id']
        assert new_id=='reviewattr_'+item['id']
        assert repo.history(p,'old-count')[-1]['metadata']['_deleted'] is True
        assert repo.history(p,'old-count')[-1]['metadata']['replaced_by']==new_id
        old_supports=repo.list_assertions(p,canonical_record_id='old-count')
        assert old_supports and {support['status'] for support in old_supports}=={'superseded'}
        candidate_assertion=repo.get_assertion(p,item['id'])
        assert candidate_assertion['status']=='accepted'
        assert candidate_assertion['canonical_record_id']==new_id
        current=[r for r in repo.current_records(p)
                 if r['kind']=='attribute' and not r['metadata'].get('_deleted')]
        assert [(r['id'],r['value']) for r in current]==[(new_id,2)]


def test_same_attribute_value_reuses_canonical_fact_and_adds_candidate_support(tmp_path,monkeypatch):
    def extract(self,text,ontology):
        self.review_candidates=[dict(kind='entity',record_id='person-1',text='甲',proposed_type='Person'),
            dict(kind='attribute',entity_id='person-1',subject='甲',proposed_type='count',value=2)]
        return []
    monkeypatch.setattr(SemanticaExtractor,'extract',extract)
    app=create_app(tmp_path/'attribute-reuse.sqlite',HashingEncoder())
    with TestClient(app) as client:
        p=client.post('/api/projects',json={'name':'attribute-reuse','use_default_ontology':False}).json()['id']
        base='/api/projects/'+p;repo=app.state.service.repository
        ontology=client.post(base+'/ontologies',json={'turtle':TTL}).json()
        assert client.post(base+'/documents',json={
            'title':'同值证据','text':'甲数量2','resolve_entities':False}).status_code==201
        entity=next(r for r in client.get(base+'/reviews').json()['reviews'] if r['kind']=='entity')
        approved=client.post(base+'/reviews/'+entity['document_id']+'/'+entity['id'],json={
            'action':'approve','note':'确认实体','target_type':'Person',
            'expected_version':entity['document_version']})
        entity_id=approved.json()['record_id']
        assert client.post(base+'/records',json={'records':[{
            'id':'canonical-count','kind':'attribute','type':'https://test/count','subject_id':entity_id,
            'value':2,'datatype':'http://www.w3.org/2001/XMLSchema#integer','text':'甲 · count = 2',
            'ontology_id':ontology['id']}]}).status_code==201
        initial_support_ids={support['id'] for support in
            repo.list_assertions(p,status='accepted',canonical_record_id='canonical-count')}
        assert len(initial_support_ids)==1
        item=next(r for r in client.get(base+'/reviews').json()['reviews'] if r['kind']=='attribute')
        response=client.post(base+'/reviews/'+item['document_id']+'/'+item['id'],json={
            'action':'approve','note':'确认同值证据','target_type':'count',
            'expected_version':item['document_version'],'expected_entity_version':item['entity_version']})
        assert response.status_code==200,response.text
        assert response.json()['record_id']=='canonical-count'
        current=[r for r in repo.current_records(p)
                 if r['kind']=='attribute' and not r['metadata'].get('_deleted')]
        assert [(r['id'],r['version']) for r in current]==[('canonical-count',1)]
        assertion=repo.get_assertion(p,item['id'])
        assert assertion['status']=='accepted'
        assert assertion['canonical_record_id']=='canonical-count'
        supports=repo.list_assertions(p,status='accepted',canonical_record_id='canonical-count')
        assert len(supports)==2
        assert {support['id'] for support in supports}==initial_support_ids|{item['id']}


def test_attribute_provider_isolates_bad_items_and_marks_unverified_evidence(monkeypatch):
    from semantica.semantic_extract import providers
    from semantica.semantic_extract.types import Entity
    from knowledge_service.services.attribute_extraction import extract_attributes,AttributeResponse
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
    from knowledge_service.services.attribute_extraction import extract_attributes,AttributeResponse
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


@pytest.mark.parametrize('mode',['rejected_parent','stale_entity','legacy_property','embedding_failure'])
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
            if mode in ('stale_entity','legacy_property'):
                updated=writable(entity);updated['properties']={'https://test/count':99}
                repo.put_record(p,updated,expected_version=entity['version'])
            if mode=='legacy_property':attribute=items()[1]
            if mode=='embedding_failure':
                def fail(*args):raise RuntimeError('test unavailable')
                monkeypatch.setattr(app.state.service.encoder,'encode',fail)
            response=send(attribute,'count')
            assert response.status_code=={'stale_entity':409,'legacy_property':200,'embedding_failure':503}[mode],response.text
            latest=repo.history(p,parent['record_id'])[-1]
            assert latest['properties'].get('https://test/count')!=2
            if mode=='legacy_property':
                fact=next(r for r in repo.current_records(p) if r['kind']=='attribute')
                assert fact['value']==2
        assert items()[1]['status']==('approved' if mode=='legacy_property' else 'pending')
