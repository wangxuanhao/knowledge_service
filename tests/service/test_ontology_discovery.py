from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology
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
        assert ingested.json()['discovery_candidates']==4
        rows=client.post(base+'/records/query',json={}).json()['records']
        assert {row['kind'] for row in rows}=={'document','chunk'}

        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_count']==4
        assert overview['attribute_count']==1
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
        assert len(draft['candidate_snapshot'])==4
        assert Ontology(draft['turtle']).summary()['classes']
        assert draft['mappings']['entity_types']['Merchant']
        assert draft['mappings']['attributes']['employeeCount']
        summary=Ontology(draft['turtle']).summary()
        assert all(not item['name'].startswith('urn:') for item in summary['classes']+summary['relations'])
        assert all(item['description'] for item in summary['classes']+summary['relations'])
        assert {node['status'] for node in client.get(
            base+'/ontology-discovery/candidate-mindmap').json()['nodes']}=={'included_in_draft'}
        import time
        resp=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert resp.status_code==202,resp.text
        job=resp.json()
        assert job['kind']=='ontology_publish'
        for _ in range(200):
            job=client.get('/api/jobs/'+job['id']).json()
            if job['status'] in ('completed','failed','interrupted'):break
            time.sleep(0.1)
        assert job['status']=='completed',job.get('error')
        published=job['result']
        assert published['status']=='published'
        assert published['mapped_entities']==2 and published['mapped_relations']==1
        assert published['mapped_attributes']==1
        assert published['requires_controlled_reingest'] is False
        graph=client.post(base+'/records/query',json={}).json()['records']
        formal=[row for row in graph if row['kind'] in ('entity','relation')]
        assert len(formal)==3
        assert all(row['metadata']['discovery_candidate_id'] for row in formal)
        relation=next(row for row in formal if row['kind']=='relation')
        assert {relation['subject_id'],relation['object_id']}<=set(row['id'] for row in formal)
        merchant=next(row for row in formal if row['kind']=='entity' and row['text']=='测试商户')
        assert list(merchant['properties'].values())==[20]
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_status_counts']['materialized']==4
        assert overview['candidate_status_counts']['approved']==0
        ontology=client.get(base+'/ontology').json()
        assert ontology['metadata']['source_draft_id']==draft['id']
        assert client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={}).status_code==409


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

        import time
        resp=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert resp.status_code==202,resp.text
        job=resp.json()
        for _ in range(200):
            job=client.get('/api/jobs/'+job['id']).json()
            if job['status'] in ('completed','failed','interrupted'):break
            time.sleep(0.1)
        assert job['status']=='completed',job.get('error')
        published=job['result']
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['published'] is True and overview['ontology_id']==published['ontology_id']
        assert overview['unpublished_candidate_count']==0
        assert overview['candidate_status_counts']['materialized']==1
        assert overview['candidate_status_counts']['approved']==0
        assert overview['requires_controlled_reingest'] is False
        assert client.get(base+'/ontology').status_code==200

        document=next(row for row in app.state.service.repository.current_records(pid)
            if row['kind']=='document')
        candidate_id=document['metadata']['discovery_candidates'][0]['id']
        app.state.service.repository.put_record(pid,{'id':'legacy-materialized','kind':'entity',
            'type':'urn:materialized:Merchant','text':'测试商户','ontology_id':published['ontology_id'],
            'metadata':{'discovery_candidate_id':candidate_id}})
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_status_counts']['materialized']==1
        assert overview['candidate_status_counts']['approved']==0
        assert overview['requires_controlled_reingest'] is False


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
        import time
        resp=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert resp.status_code==202,resp.text
        job=resp.json()
        for _ in range(200):
            job=client.get('/api/jobs/'+job['id']).json()
            if job['status'] in ('completed','failed','interrupted'):break
            time.sleep(0.1)
        assert job['status']=='completed',job.get('error')
        published=job['result']
        assert published['mapped_entities']==1 and published['requires_controlled_reingest'] is False
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_status_counts']['pending']==1
        assert overview['candidate_status_counts']['approved']==0
        assert overview['requires_candidate_review'] is True
        formal=[row for row in app.state.service.repository.current_records(project['id']) if row['kind']=='entity']
        assert [row['text'] for row in formal]==['保留实体']


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
        import time
        resp=client.post(base+f"/ontology-discovery/drafts/{draft['id']}/publish",json={})
        assert resp.status_code==202,resp.text
        job=resp.json()
        for _ in range(200):
            job=client.get('/api/jobs/'+job['id']).json()
            if job['status'] in ('completed','failed','interrupted'):break
            time.sleep(0.1)
        assert job['status']=='completed',job.get('error')
        assert job['result']['mapped_entities']==1


def test_induction_does_not_invent_name_attribute_without_attribute_candidates():
    turtle,mappings,_=_induce('project','无属性',[{'id':'a','kind':'entity','text':'甲','proposed_type':'主体'}])
    assert mappings['attributes']=={}
    assert Ontology(turtle).summary()['attributes']==[]


def test_materialization_validation_keeps_invalid_relation_pending():
    turtle='''
        @prefix ex: <urn:test:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:A a owl:Class . ex:B a owl:Class . ex:C a owl:Class .
        ex:links a owl:ObjectProperty ; rdfs:domain ex:A ; rdfs:range ex:B .
    '''
    def entity(identifier,type_iri):
        return {'id':identifier,'kind':'entity','type':type_iri,'text':identifier,'ontology_id':'draft',
            'properties':{},'metadata':{'discovery_candidate_id':'candidate-'+identifier}}
    records=[entity('source','urn:test:C'),entity('target','urn:test:B'),
        {'id':'edge','kind':'relation','type':'urn:test:links','text':'bad edge','ontology_id':'draft',
         'subject_id':'source','object_id':'target','metadata':{'discovery_candidate_id':'candidate-edge'}}]
    accepted,skipped,report=_validated_materialization(turtle,records,[])
    assert [row['kind'] for row in accepted]==['entity','entity']
    assert skipped[0]['candidate_id']=='candidate-edge'
    assert report=={'conforms':True,'accepted_count':2,'skipped_count':1}
