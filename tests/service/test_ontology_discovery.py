from fastapi.testclient import TestClient
import pytest
from rdflib import RDFS, URIRef

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_drafts import OntologyDrafts
from knowledge_service.services.ontology_discovery import _candidate_mindmap, _induce, _validated_materialization


def test_discovery_generates_readable_unicode_iris_for_every_term_kind():
    candidates=[
        {'id':'entity','kind':'entity','text':'平台规范','proposed_type':'规则 文件'},
        {'id':'other','kind':'entity','text':'数据平台','proposed_type':'数据平台'},
        {'id':'relation','kind':'relation','subject_id':'entity','object_id':'other',
         'subject':'平台规范','object':'数据平台','proposed_type':'适用于'},
        {'id':'attribute','kind':'attribute','entity_id':'entity','proposed_type':'发布日期','value':'2026-09-16'},
    ]
    _,mappings,_=_induce('project-id','中文本体',candidates)

    assert mappings['entity_types']['规则 文件'].endswith(':规则-文件')
    assert mappings['relation_types']['适用于'].endswith(':适用于')
    assert mappings['attributes']['发布日期'].endswith(':发布日期')
    assert all('%' not in iri for group in mappings.values() for iri in group.values())


def test_open_discovery_builds_draft_then_publishes_versioned_ontology(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    def discover(self,text,include_attributes=False):
        assert include_attributes is True
        return [
            {'id':'merchant','kind':'entity','text':'测试商户','proposed_type':'Merchant','confidence':.93},
            {'id':'rule','kind':'entity','text':'平台规则','proposed_type':'RuleDocument','confidence':.91},
            {'id':'edge','kind':'relation','subject_id':'rule','object_id':'merchant','subject':'平台规则',
             'object':'测试商户','proposed_type':'appliesTo','confidence':.89},
            {'id':'attr','kind':'attribute','entity_id':'merchant','subject':'测试商户',
             'proposed_type':'employeeCount','value':20,'confidence':.86,
             'attribute_evidence':'员工20人','evidence_status':'exact'},
            {'id':'attr-later','kind':'attribute','entity_id':'merchant','subject':'测试商户',
             'proposed_type':'employeeCount','value':21,'confidence':.84,
             'attribute_evidence':'后续增长到21人','evidence_status':'exact'},
        ]

    monkeypatch.setattr(SemanticaExtractor,'discover',discover)
    app=create_app(tmp_path/'discovery.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={
            'name':'开放领域','use_default_ontology':False,'ontology_mode':'discovery'}).json()
        assert project['metadata']['ontology_mode']=='discovery'
        base=f"/api/projects/{project['id']}"
        ingested=client.post(base+'/documents',json={
            'title':'开放原文','text':'平台规则适用于测试商户，员工20人。','extraction_mode':'discovery',
            'extract_attributes':True,'resolve_entities':False})
        assert ingested.status_code==201,ingested.text
        assert ingested.json()['discovery_candidates']==5
        rows=client.post(base+'/records/query',json={}).json()['records']
        assert {row['kind'] for row in rows}=={'document','chunk'}

        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_count']==5
        assert overview['attribute_count']==2
        assert overview['entity_types'][0]['name'] in {'Merchant','RuleDocument'}
        assert overview['relation_types']==[{'name':'appliesTo','count':1,'examples':['平台规则 → 测试商户']}]
        assert [x['code'] for x in overview['quality_warnings']]==['relation_language_mismatch']
        candidate_map=client.get(base+'/ontology-discovery/candidate-mindmap').json()
        assert candidate_map['summary']['entity_clusters']==2
        assert len(candidate_map['edges'])==1
        assert {node['status'] for node in candidate_map['nodes']}=={'pending'}
        merchant=next(node for node in candidate_map['nodes'] if node['text']=='测试商户')
        assert merchant['attributes'][0]['name']=='employeeCount'
        assert merchant['sources'][0]['document_title']=='开放原文'

        draft=client.post(base+'/ontology-discovery/drafts',json={'name':'开放领域本体'}).json()
        assert draft['status']=='draft' and draft['generator_backend']=='semantica'
        assert draft['parent_ontology_id'] is None
        assert len(draft['candidate_snapshot'])==5
        assert Ontology(draft['turtle']).summary()['classes']
        assert draft['mappings']['entity_types']['Merchant']
        assert draft['mappings']['attributes']['employeeCount']
        summary=Ontology(draft['turtle']).summary()
        assert all(not item['name'].startswith('urn:') for item in summary['classes']+summary['relations'])
        assert all(item['description'] for item in summary['classes']+summary['relations'])
        assert {node['status'] for node in client.get(
            base+'/ontology-discovery/candidate-mindmap').json()['nodes']}=={'included_in_draft'}
        resp=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert resp.status_code==202,resp.text
        submitted=resp.json()
        assert submitted['status']=='submitted'
        assert submitted['deprecation']['deprecated'] is True
        assert all(operation['risk'] in {'medium','high'}
                   for operation in submitted['operations'])
        assert app.state.service.repository.list_ontologies(project['id'])==[]
        graph=client.post(base+'/records/query',json={}).json()['records']
        formal=[row for row in graph if row['kind'] in ('entity','relation','attribute')]
        assert formal==[]
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_status_counts']['included_in_draft']==5
        assert overview['published'] is False
        warning_codes=[item['code'] for item in submitted['validation_report']['warnings']
                       if item.get('code')]
        current=submitted
        for operation in submitted['operations']:
            decision=client.post(
                base+f"/ontology-drafts/{draft['id']}/decisions",json={
                    'expected_revision':current['revision'],
                    'expected_ontology_id':None,
                    'validation_fingerprint':submitted['validation_fingerprint'],
                    'acknowledged_warning_codes':warning_codes,
                    'actor':'discovery-reviewer',
                    'decisions':[{
                        'operation_id':operation['id'],
                        'operation_fingerprint':operation['fingerprint'],
                        'action':'approve','reason':'来源与结构已核验'}],
                })
            assert decision.status_code==200,decision.text
            current=decision.json()
        assert current['status']=='reviewed'
        published=client.post(
            base+f"/ontology-drafts/{draft['id']}/publish",json={
                'expected_revision':current['revision'],
                'expected_ontology_id':None,
                'validation_fingerprint':current['validation_fingerprint'],
                'acknowledged_warning_codes':warning_codes,
                'idempotency_key':'discovery-publish-1','actor':'publisher',
            })
        assert published.status_code==200,published.text
        formal=[row for row in app.state.service.repository.current_records(project['id'])
                if row['kind'] in ('entity','relation','attribute')]
        assert len(formal)==5
        stored=app.state.service.repository.get_artifact(
            'ontology_discovery_draft',draft['id'])
        assert stored['status']=='published' and stored['mapped_entities']==2
        sync=app.state.service.repository.get_artifact(
            'ontology_sync_job','ontology-sync:'+published.json()['id'])
        assert sync['status']=='completed' and sync['fingerprint']
        assert client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={}).status_code==409


def test_legacy_discovery_publish_only_submits_and_never_writes_records(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    monkeypatch.setattr(SemanticaExtractor,'discover',lambda self,text,include_attributes=False:[
        {'id':'merchant','kind':'entity','text':'测试商户','proposed_type':'Merchant','confidence':.93},
        {'id':'rule','kind':'entity','text':'平台规则','proposed_type':'RuleDocument','confidence':.91},
        {'id':'edge','kind':'relation','subject_id':'rule','object_id':'merchant','subject':'平台规则',
         'object':'测试商户','proposed_type':'appliesTo','confidence':.89},
        {'id':'attr','kind':'attribute','entity_id':'merchant','subject':'测试商户',
         'proposed_type':'employeeCount','value':20,'confidence':.86,
         'attribute_evidence':'员工20人','evidence_status':'exact'},
    ])

    app=create_app(tmp_path/'atomic-discovery.sqlite',HashingEncoder())
    with TestClient(app) as client:
        class MilvusSpy:
            def __init__(self,lock):self.lock=lock;self.upserts=[];self.lock_owned=[]
            def upsert(self,rows,flush=False):
                self.upserts.append([row['id'] for row in rows]);self.lock_owned.append(self.lock._is_owned())
            def delete(self,*args,**kwargs):pass
        milvus=MilvusSpy(app.state.service.lock)
        project=client.post('/api/projects',json={
            'name':'原子发布','use_default_ontology':False,'ontology_mode':'discovery'}).json()
        pid=project['id'];base=f'/api/projects/{pid}'
        client.post(base+'/documents',json={'title':'原文','text':'平台规则适用于测试商户，员工20人。',
            'extraction_mode':'discovery','extract_attributes':True,'resolve_entities':False})
        draft=client.post(base+'/ontology-discovery/drafts',json={'name':'原子本体'}).json()
        app.state.service.milvus_store=milvus
        submitted=client.post(
            base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert submitted.status_code==202,submitted.text
        assert submitted.json()['status']=='submitted'
        assert app.state.service.repository.list_ontologies(pid)==[]
        assert not [row for row in app.state.service.repository.current_records(pid)
                    if row['kind'] in ('entity','attribute','relation')]
        assert milvus.upserts==[]


def test_legacy_extract_boolean_remains_backward_compatible():
    from knowledge_service.models import Ingest
    assert Ingest(title='a',text='b',extract=False).effective_extraction_mode()=='documents'
    assert Ingest(title='a',text='b',extract=True).effective_extraction_mode()=='ontology'
    assert Ingest(title='a',text='b',extract=False,extraction_mode='discovery').effective_extraction_mode()=='discovery'


def test_candidate_mindmap_visually_clusters_repeated_occurrences_without_formal_ids():
    candidates=[]
    for index in range(2):
        prefix=f'doc-{index}'
        candidates.extend([
            {'id':prefix+':merchant','kind':'entity','text':'同一商户','proposed_type':'商户',
             'document_id':prefix,'document_title':f'来源 {index}','evidence':'商户使用平台'},
            {'id':prefix+':platform','kind':'entity','text':'同一平台','proposed_type':'平台',
             'document_id':prefix,'document_title':f'来源 {index}','evidence':'商户使用平台'},
            {'id':prefix+':uses','kind':'relation','subject_id':prefix+':merchant',
             'object_id':prefix+':platform','subject':'同一商户','object':'同一平台',
             'proposed_type':'使用','document_id':prefix,'document_title':f'来源 {index}',
             'evidence':'商户使用平台'},
        ])
    result=_candidate_mindmap(candidates,{item['id']:'pending' for item in candidates})
    assert len(result['nodes'])==2
    assert {node['occurrence_count'] for node in result['nodes']}=={2}
    assert all(len(node['sources'])==2 for node in result['nodes'])
    assert len(result['edges'])==1 and result['edges'][0]['occurrence_count']==2
    assert all(node['id'].startswith('candidate:') for node in result['nodes'])


def test_missing_ontology_reports_clear_state_without_project_id(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    def discover(self,text,include_attributes=False):
        return [{'id':'merchant','kind':'entity','text':'测试商户','proposed_type':'Merchant','confidence':.93}]

    monkeypatch.setattr(SemanticaExtractor,'discover',discover)
    app=create_app(tmp_path/'missing.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={
            'name':'无本体','use_default_ontology':False,'ontology_mode':'discovery'}).json()
        pid=project['id'];base=f'/api/projects/{pid}'

        missing=client.get(base+'/ontology')
        assert missing.status_code==404
        body=missing.json()
        assert body['code']=='ontology_not_published'
        assert pid not in body['detail']

        overview=client.get(base+'/ontology-discovery').json()
        assert overview['published'] is False and overview['ontology_id'] is None
        assert overview['unpublished_candidate_count']==0

        client.post(base+'/documents',json={'title':'原文','text':'平台规则适用于测试商户。',
            'extraction_mode':'discovery','resolve_entities':False})
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_count']==1
        assert overview['unpublished_candidate_count']==1

        draft=client.post(base+'/ontology-discovery/drafts',json={'name':'草案'}).json()
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['published'] is False and overview['unpublished_candidate_count']==1
        assert overview['candidate_status_counts']['included_in_draft']==1

        resp=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert resp.status_code==202,resp.text
        submitted=resp.json()
        assert submitted['status']=='submitted'
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['published'] is False and overview['ontology_id'] is None
        assert overview['candidate_status_counts']['included_in_draft']==1
        assert client.get(base+'/ontology').status_code==404


def test_cumulative_draft_reuses_existing_term_iri_and_records_diff(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    monkeypatch.setattr(SemanticaExtractor,'discover',lambda self,text,include_attributes=False:[
        {'id':'merchant','kind':'entity','text':'测试商户','proposed_type':'商户','confidence':.93},
        {'id':'platform','kind':'entity','text':'测试平台','proposed_type':'平台','confidence':.91},
    ])
    app=create_app(tmp_path/'cumulative.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={
            'name':'累计发现','use_default_ontology':False,'ontology_mode':'discovery'}).json()
        pid=project['id'];base=f'/api/projects/{pid}'
        base_ttl='''@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:stable:Merchant> a owl:Class ; rdfs:label "商户"@zh .'''
        parent=app.state.service.repository.save_ontology(pid,base_ttl,Ontology(base_ttl).summary())
        client.post(base+'/documents',json={'title':'原文','text':'测试商户使用测试平台',
            'extraction_mode':'discovery','resolve_entities':False})

        draft=client.post(base+'/ontology-discovery/drafts',json={'name':'累计草案'}).json()
        assert draft['parent_ontology_id']==parent['id']
        assert draft['mappings']['entity_types']['商户']=='urn:stable:Merchant'
        assert any(x['id']=='urn:stable:Merchant' for x in draft['diff']['classes']['retained'])
        assert any(x['label']=='平台' for x in draft['diff']['classes']['added'])
        assert draft['diff']['classes']['removed']==[]


def test_publish_rejects_draft_when_parent_ontology_changed(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    monkeypatch.setattr(SemanticaExtractor,'discover',lambda self,text,include_attributes=False:[
        {'id':'merchant','kind':'entity','text':'测试商户','proposed_type':'商户','confidence':.93}])
    app=create_app(tmp_path/'stale.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={
            'name':'并发本体','use_default_ontology':False,'ontology_mode':'discovery'}).json()
        pid=project['id'];base=f'/api/projects/{pid}'
        client.post(base+'/documents',json={'title':'原文','text':'测试商户',
            'extraction_mode':'discovery','resolve_entities':False})
        draft=client.post(base+'/ontology-discovery/drafts',json={'name':'过期草案'}).json()
        ttl='''@prefix owl: <http://www.w3.org/2002/07/owl#> . <urn:other> a owl:Class .'''
        app.state.service.repository.save_ontology(pid,ttl,Ontology(ttl).summary())

        response=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert response.status_code==409
        assert '本体已更新' in response.text


def test_discovery_warns_before_publishing_generic_or_wrong_language_vocabulary(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    monkeypatch.setattr(SemanticaExtractor,'discover',lambda self,text,include_attributes=False:[
        {'id':'a','kind':'entity','text':'平台','proposed_type':'CONCEPT','confidence':.9},
        {'id':'b','kind':'entity','text':'商户','proposed_type':'ORG','confidence':.9},
        {'id':'r','kind':'relation','subject_id':'a','object_id':'b','subject':'平台','object':'商户',
         'proposed_type':'appliesTo','confidence':.8}])
    app=create_app(tmp_path/'quality.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'质量检查','use_default_ontology':False,
            'ontology_mode':'discovery'}).json();base=f"/api/projects/{project['id']}"
        client.post(base+'/documents',json={'title':'原文','text':'平台适用于商户',
            'extraction_mode':'discovery','resolve_entities':False})
        warnings=client.get(base+'/ontology-discovery').json()['quality_warnings']
        assert {x['code'] for x in warnings}=={'generic_entity_vocabulary','relation_language_mismatch'}


def test_induction_keeps_distinct_chinese_types_when_semantica_normalizes_names():
    candidates=[
        {'id':'a','kind':'entity','text':'美团','proposed_type':'平台品牌'},
        {'id':'b','kind':'entity','text':'直播间','proposed_type':'直播平台'},
        {'id':'r','kind':'relation','subject_id':'a','object_id':'b','subject':'美团','object':'直播间',
         'proposed_type':'运营'},
    ]
    turtle,mappings,_=_induce('project','中文本体',candidates)
    summary=Ontology(turtle).summary()
    assert {x['label_zh'] for x in summary['classes']}=={'平台品牌','直播平台'}
    assert {x['label_zh'] for x in summary['relations']}=={'运营'}
    assert all(not x['domain'] and not x['range'] for x in summary['relations'])
    assert set(mappings['entity_types'])=={'平台品牌','直播平台'}


def test_open_induction_removes_legacy_inferred_relation_ranges_from_parent():
    baseline='''
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:knowledge:ontology:project:%E8%BF%90%E8%90%A5> a owl:ObjectProperty ;
            rdfs:label "运营"@zh ;
            rdfs:domain <urn:old:PlatformBrand> ;
            rdfs:range <urn:old:LivePlatform> .
    '''
    candidates=[
        {'id':'a','kind':'entity','text':'美团','proposed_type':'平台品牌'},
        {'id':'b','kind':'entity','text':'直播间','proposed_type':'直播平台'},
        {'id':'r','kind':'relation','subject_id':'a','object_id':'b','subject':'美团','object':'直播间',
         'proposed_type':'运营'},
    ]
    turtle,_,_=_induce('project','开放本体',candidates,baseline_turtle=baseline)
    relation=next(item for item in Ontology(turtle).summary()['relations'] if item['label_zh']=='运营')
    assert relation['domain']==[] and relation['range']==[]


def test_review_can_exclude_candidate_before_publish_and_reports_it(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    monkeypatch.setattr(SemanticaExtractor,'discover',lambda self,text,include_attributes=False:[
        {'id':'keep','kind':'entity','text':'保留实体','proposed_type':'主体','confidence':.9},
        {'id':'drop','kind':'entity','text':'排除实体','proposed_type':'主体','confidence':.4}])
    app=create_app(tmp_path/'review.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'审核','use_default_ontology':False,
            'ontology_mode':'discovery'}).json();base=f"/api/projects/{project['id']}"
        client.post(base+'/documents',json={'title':'原文','text':'测试','extraction_mode':'discovery','resolve_entities':False})
        draft=client.post(base+'/ontology-discovery/drafts',json={'name':'审核草案'}).json()
        excluded=next(item['id'] for item in draft['candidate_snapshot'] if item['text']=='排除实体')
        reviewed=client.put(base+f"/ontology-discovery/drafts/{draft['id']}",json={'excluded_candidate_ids':[excluded]})
        assert reviewed.status_code==200 and reviewed.json()['revision']==2
        resp=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert resp.status_code==202,resp.text
        assert resp.json()['status']=='submitted'
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_status_counts']['pending']==1
        assert overview['candidate_status_counts']['included_in_draft']==1
        assert overview['requires_candidate_review'] is True
        formal=[row for row in app.state.service.repository.current_records(project['id']) if row['kind']=='entity']
        assert formal==[]


def test_draft_review_can_rename_and_remove_ontology_terms(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    monkeypatch.setattr(SemanticaExtractor,'discover',lambda self,text,include_attributes=False:[
        {'id':'a','kind':'entity','text':'甲','proposed_type':'旧类型','confidence':.9},
        {'id':'b','kind':'entity','text':'乙','proposed_type':'删除类型','confidence':.9}])
    app=create_app(tmp_path/'term-review.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'术语审核','use_default_ontology':False,
            'ontology_mode':'discovery'}).json();base=f"/api/projects/{project['id']}"
        client.post(base+'/documents',json={'title':'原文','text':'甲乙','extraction_mode':'discovery','resolve_entities':False})
        draft=client.post(base+'/ontology-discovery/drafts',json={'name':'草案'}).json()
        reviewed=client.put(base+f"/ontology-discovery/drafts/{draft['id']}",json={
            'excluded_candidate_ids':[],'excluded_terms':['删除类型'],'term_labels':{'旧类型':'正式类型'}})
        assert reviewed.status_code==200,reviewed.text
        body=reviewed.json();summary=Ontology(body['turtle']).summary()
        assert {item['label_zh'] for item in summary['classes']}=={'正式类型'}
        restored=client.put(base+f"/ontology-discovery/drafts/{draft['id']}",json={
            'excluded_candidate_ids':[],'excluded_terms':[],'term_labels':{'旧类型':'正式类型'}})
        assert {item['label_zh'] for item in Ontology(restored.json()['turtle']).summary()['classes']}=={
            '正式类型','删除类型'}
        reviewed=client.put(base+f"/ontology-discovery/drafts/{draft['id']}",json={
            'excluded_candidate_ids':[],'excluded_terms':['删除类型'],'term_labels':{'旧类型':'正式类型'}})
        assert reviewed.status_code==200
        resp=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert resp.status_code==202,resp.text
        assert resp.json()['status']=='submitted'
        assert app.state.service.repository.list_ontologies(project['id'])==[]


def test_induction_does_not_invent_name_attribute_without_attribute_candidates():
    turtle,mappings,_=_induce('project','无属性',[{'id':'a','kind':'entity','text':'甲','proposed_type':'主体'}])
    assert mappings['attributes']=={}
    assert Ontology(turtle).summary()['attributes']==[]


def test_materialization_validation_keeps_invalid_relation_pending():
    turtle='''
        @prefix ex: <urn:test:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        ex:A a owl:Class . ex:B a owl:Class . ex:C a owl:Class .
        ex:links a owl:ObjectProperty ; rdfs:domain ex:A ; rdfs:range ex:B .
        ex:count a owl:DatatypeProperty ; rdfs:domain ex:C ; rdfs:range xsd:integer .
    '''
    def entity(identifier,type_iri):
        return {'id':identifier,'kind':'entity','type':type_iri,'text':identifier,'ontology_id':'draft',
            'properties':{},'metadata':{'discovery_candidate_id':'candidate-'+identifier}}
    records=[entity('source','urn:test:C'),entity('target','urn:test:B'),
        {'id':'valid-count','kind':'attribute','type':'urn:test:count','text':'source count 2',
         'ontology_id':'draft','subject_id':'source','value':2,
         'datatype':'http://www.w3.org/2001/XMLSchema#integer',
         'metadata':{'discovery_candidate_id':'candidate-valid-count'}},
        {'id':'bad-count','kind':'attribute','type':'urn:test:count','text':'source count two',
         'ontology_id':'draft','subject_id':'source','value':'two',
         'datatype':'http://www.w3.org/2001/XMLSchema#string',
         'metadata':{'discovery_candidate_id':'candidate-bad-count'}},
        {'id':'edge','kind':'relation','type':'urn:test:links','text':'bad edge','ontology_id':'draft',
         'subject_id':'source','object_id':'target','metadata':{'discovery_candidate_id':'candidate-edge'}}]
    accepted,skipped,report=_validated_materialization(turtle,records,[])
    assert [row['id'] for row in accepted]==['source','target','valid-count']
    assert {item['candidate_id'] for item in skipped}=={'candidate-bad-count','candidate-edge'}
    assert report=={'conforms':True,'accepted_count':3,'skipped_count':2}
from rdflib import RDFS, URIRef

def test_semantica_067_single_parent_adapter_keeps_unknown_parents_as_roots(monkeypatch):
    def generated(_self, data, **_kwargs):
        machine = {item['text']: item['type'] for item in data['entities']}
        return {
            'classes': [
                {'name': machine['Parent']},
                {'name': machine['Child'], 'parent': machine['Parent']},
                {'name': machine['Alias'], 'subClassOf': machine['Parent']},
                {'name': machine['Orphan'], 'parent': 'UnknownParent'},
            ],
            'properties': [], 'metadata': {}, 'validation': {},
        }

    monkeypatch.setattr(
        'semantica.ontology.OntologyGenerator.generate_ontology', generated)
    candidates = [{
        'id': f'c-{name}', 'kind': 'entity', 'proposed_type': name,
        'text': name, 'confidence': 0.9,
    } for name in ('Parent', 'Child', 'Alias', 'Orphan')]
    turtle, mappings, _ = _induce('project', 'hierarchy', candidates)
    ontology = Ontology(turtle)
    parent = URIRef(mappings['entity_types']['Parent'])
    child = URIRef(mappings['entity_types']['Child'])
    alias = URIRef(mappings['entity_types']['Alias'])
    orphan = URIRef(mappings['entity_types']['Orphan'])

    assert (child, RDFS.subClassOf, parent) in ontology.graph
    assert (alias, RDFS.subClassOf, parent) in ontology.graph
    assert list(ontology.graph.objects(orphan, RDFS.subClassOf)) == []


def test_discovery_publication_effect_failure_rolls_back_everything(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'discovery-effects.sqlite')
    project_id = repo.create_project('atomic discovery')['id']
    candidate = {
        'id': 'candidate', 'kind': 'entity', 'text': 'Thing',
        'proposed_type': 'Thing', 'status': 'pending',
    }
    document = repo.put_record(project_id, {
        'id': 'doc', 'kind': 'document', 'text': 'Thing',
        'metadata': {'discovery_candidates': [candidate]},
    })
    drafts = OntologyDrafts(repo, publisher=repo)
    draft = drafts.create(
        project_id, None, 'discovery', 'atomic', 'author',
        source_context={
            'documents': [{
                'document_id': document['id'],
                'expected_document_version': document['version'],
                'candidate_id': candidate['id'],
            }],
            'publication_effects': {
                'kind': 'discovery', 'skipped_candidates': [],
                'records': [{
                    'id': 'formal-thing', 'kind': 'entity',
                    'type': 'urn:test:Thing', 'text': 'Thing',
                    'ontology_id': '__PUBLISHED_ONTOLOGY_ID__',
                    'source_id': document['id'], 'metadata': {
                        'discovery_candidate_id': candidate['id']},
                }],
            },
        })
    draft = drafts.command(project_id, draft['id'], draft['revision'], {
        'action': 'create_term', 'target_iri': 'urn:test:Thing',
        'kind': 'class'})
    draft = drafts.submit(project_id, draft['id'], draft['revision'])
    operation = draft['operations'][0]
    warnings = [item['code'] for item in draft['validation_report']['warnings']
                if item.get('code')]
    draft = drafts.decide(
        project_id, draft['id'], draft['revision'], None,
        draft['validation_fingerprint'], [{
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': 'approve', 'reason': 'verified',
        }], warnings, 'reviewer')

    monkeypatch.setattr(
        repo, '_put', lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError('injected publication effect failure')))
    with pytest.raises(RuntimeError, match='injected publication effect failure'):
        drafts.publish(
            project_id, draft['id'], draft['revision'], None,
            draft['validation_fingerprint'], warnings,
            'atomic-discovery', 'publisher')

    assert repo.list_ontologies(project_id) == []
    assert repo._ontology_drafts.get(project_id, draft['id'])['status'] == 'reviewed'
    assert not [row for row in repo.current_records(project_id)
                if row['kind'] == 'entity']
