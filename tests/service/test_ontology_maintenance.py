from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology


TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
:Thing a owl:Class ; rdfs:label "事物" .
:Person a owl:Class ; rdfs:label "人员" ; rdfs:subClassOf :Thing .
:Organization a owl:Class ; rdfs:label "组织" ; rdfs:subClassOf :Thing .
:knows a owl:ObjectProperty ; rdfs:label "认识" ; rdfs:domain :Person ; rdfs:range :Person .
:age a owl:DatatypeProperty ; rdfs:label "年龄" ; rdfs:domain :Person ; rdfs:range xsd:integer .'''


def setup(tmp_path):
    app=create_app(tmp_path/'ontology-maintenance.sqlite',HashingEncoder())
    client=TestClient(app);client.__enter__()
    p=client.post('/api/projects',json={'name':'ontology','use_default_ontology':False}).json()['id']
    ontology=client.post('/api/projects/'+p+'/ontologies',json={'turtle':TTL}).json()
    return client,app,p,'/api/projects/'+p,ontology


def test_modify_term_constraints_creates_governed_draft_and_keeps_history(tmp_path):
    c,app,p,base,old=setup(tmp_path)
    try:
        response=c.put(base+'/ontology/term?uri=https%3A%2F%2Ftest%2Fknows',json={'label':'熟悉','description':'人工维护',
            'domain':'https://test/Thing','range':'https://test/Person','expected_ontology_id':old['id']})
        assert response.status_code==200,response.text
        new=response.json();assert new['id']!=old['id']
        term=next(x for x in new['summary']['relations'] if x['id']=='https://test/knows')
        assert term['label']=='熟悉' and term['description']=='人工维护'
        assert term['domain']==['https://test/Thing']
        assert response.headers['Deprecation']=='true'
        assert new['draft_id']==new['id'] and new['status']=='pending'
        assert len(app.state.service.repository.list_ontologies(p))==1
        assert app.state.service.repository.get_ontology(p,old['id'])['turtle']==old['turtle']
        assert c.put(base+'/ontology/term?uri=https%3A%2F%2Ftest%2Fknows',json={'label':'旧提交',
            'expected_ontology_id':old['id'],'draft_id':new['draft_id'],
            'expected_revision':1}).status_code==409
    finally:c.__exit__(None,None,None)


def test_relation_maintenance_supports_multiple_allowed_endpoint_types_as_union(tmp_path):
    c,app,p,base,old=setup(tmp_path)
    try:
        response=c.put(base+'/ontology/term?uri=https%3A%2F%2Ftest%2Fknows',json={
            'label':'认识','domains':['https://test/Person','https://test/Organization'],
            'ranges':['https://test/Person','https://test/Organization'],'expected_ontology_id':old['id']})
        assert response.status_code==200,response.text
        saved=response.json();term=next(x for x in saved['summary']['relations'] if x['id']=='https://test/knows')
        assert set(term['domain'])=={'https://test/Person','https://test/Organization'}
        assert set(term['range'])=={'https://test/Person','https://test/Organization'}
        assert 'owl:unionOf' in saved['turtle']
        ontology=Ontology(saved['turtle'])
        records=[
            {'id':'person','kind':'entity','type':'https://test/Person','text':'张三'},
            {'id':'org','kind':'entity','type':'https://test/Organization','text':'机构'},
            {'id':'edge','kind':'relation','type':'https://test/knows','text':'认识','subject_id':'person','object_id':'org'},
        ]
        assert ontology.validate_timeline(records)['conforms']
    finally:c.__exit__(None,None,None)


def test_impact_and_confirmed_retirement_preserve_old_knowledge(tmp_path):
    c,app,p,base,old=setup(tmp_path)
    try:
        record=c.post(base+'/records',json={'records':[{'id':'p','kind':'entity','text':'张三','type':'https://test/Person'}]})
        assert record.status_code==201,record.text
        impact=c.get(base+'/ontology/term-impact?uri=https%3A%2F%2Ftest%2FPerson')
        assert impact.status_code==200,impact.text
        assert impact.json()['record_count']==1 and impact.json()['constraint_count']==3
        body={'expected_ontology_id':old['id'],'confirm_references':False}
        assert c.post(base+'/ontology/term-retire?uri=https%3A%2F%2Ftest%2FPerson',json=body).status_code==422
        body['confirm_references']=True
        retired=c.post(base+'/ontology/term-retire?uri=https%3A%2F%2Ftest%2FPerson',json=body)
        assert retired.status_code==200,retired.text
        retired_term=next(x for x in retired.json()['summary']['classes']
                          if x['id']=='https://test/Person')
        assert retired_term['active'] is True
        retire_operation=retired.json()['operations'][0]
        assert retire_operation['action']=='retire_term'
        assert retire_operation['validation']['errors']
        latest=app.state.service.repository.current_records(p)
        assert next(x for x in latest if x['id']=='p')['ontology_id']==old['id']
        assert len(app.state.service.repository.list_ontologies(p))==1
    finally:c.__exit__(None,None,None)


def test_impact_breaks_down_by_record_kind_and_lists_readable_names(tmp_path):
    """停用评估必须分类计数：实体类被停用时"关系"才是受影响大头，单一总数无法判断后果。"""
    c,app,p,base,old=setup(tmp_path)
    try:
        created=c.post(base+'/records',json={'records':[
            {'id':'p1','kind':'entity','text':'张三','type':'https://test/Person'},
            {'id':'p2','kind':'entity','text':'李四','type':'https://test/Person'},
            {'id':'o1','kind':'entity','text':'某机构','type':'https://test/Organization'},
            {'id':'edge','kind':'relation','text':'张三 认识 李四','type':'https://test/knows',
             'subject_id':'p1','object_id':'p2'},
          ]})
        assert created.status_code==201,created.text
        impact=c.get(base+'/ontology/term-impact?uri=https%3A%2F%2Ftest%2FPerson').json()
        # 分类计数：2 个 Person 实体 + 1 条以 Person 为端点的关系；Organization 实体不该被算进来
        assert impact['kind_counts']=={'entity':2,'relation':1,'attribute':0},impact['kind_counts']
        names={x['text'] for x in impact['record_preview']}
        assert {'张三','李四'} <= names
        assert '某机构' not in names
        # 明细带 kind，前端才能分组展示
        assert {x['kind'] for x in impact['record_preview']}=={'entity','relation'}
        # 向后兼容字段仍在
        assert impact['record_count']==3
        assert len(impact['record_ids'])==3 and 'constraint_count' in impact
    finally:c.__exit__(None,None,None)


def test_attribute_datatype_add_edit_and_cycle_rejected(tmp_path):
    c,app,p,base,old=setup(tmp_path)
    try:
        added=c.post(base+'/ontology/terms',json={'kind':'attribute','uri':'https://test/score','label':'评分',
            'domain':'https://test/Person','range':'http://www.w3.org/2001/XMLSchema#decimal','expected_ontology_id':old['id']})
        assert added.status_code==201,added.text
        attr=next(x for x in added.json()['summary']['attributes'] if x['id']=='https://test/score')
        assert attr['domain']==['https://test/Person'] and attr['range']==['http://www.w3.org/2001/XMLSchema#decimal']
        cycle=c.put(base+'/ontology/term?uri=https%3A%2F%2Ftest%2FThing',json={'label':'事物','parent':'https://test/Person',
            'expected_ontology_id':old['id']})
        assert cycle.status_code==422 and 'cycle' in cycle.text
        bad=c.post(base+'/ontology/terms',json={'kind':'attribute','uri':'https://test/bad','label':'错误',
            'range':'https://test/Person','expected_ontology_id':old['id']})
        assert bad.status_code==422
    finally:c.__exit__(None,None,None)


def test_add_and_update_term_persist_chinese_label(tmp_path):
    c,app,p,base,old=setup(tmp_path)
    try:
        added=c.post(base+'/ontology/terms',json={'kind':'class','uri':'https://test/Shop','label':'Shop',
            'label_zh':'店铺','expected_ontology_id':old['id']})
        assert added.status_code==201,added.text
        term=next(x for x in added.json()['summary']['classes'] if x['id']=='https://test/Shop')
        assert term['label']=='Shop' and term['label_zh']=='店铺'
        updated=c.put(base+'/ontology/term?uri=https%3A%2F%2Ftest%2FShop',json={'label':'Shop','label_zh':'商家店铺',
            'expected_ontology_id':old['id'],'draft_id':added.json()['draft_id'],
            'expected_revision':added.json()['revision']})
        assert updated.status_code==200,updated.text
        term=next(x for x in updated.json()['summary']['classes'] if x['id']=='https://test/Shop')
        assert term['label']=='Shop' and term['label_zh']=='商家店铺'
        # Backward compatible: empty label_zh clears the @zh label, plain label remains.
        cleared=c.put(base+'/ontology/term?uri=https%3A%2F%2Ftest%2FShop',json={'label':'Shop',
            'expected_ontology_id':old['id'],'draft_id':updated.json()['draft_id'],
            'expected_revision':updated.json()['revision']})
        assert cleared.status_code==200,cleared.text
        term=next(x for x in cleared.json()['summary']['classes'] if x['id']=='https://test/Shop')
        assert term['label']=='Shop' and term['label_zh']==''
    finally:c.__exit__(None,None,None)


def test_unicode_iri_round_trips_without_percent_encoding(tmp_path):
    c,app,p,base,old=setup(tmp_path)
    try:
        iri='urn:knowledge:ontology:'+p+':保护对象'
        added=c.post(base+'/ontology/terms',json={'kind':'class','uri':iri,'label':'保护对象',
            'label_zh':'保护对象','expected_ontology_id':old['id']})
        assert added.status_code==201,added.text
        assert any(item['id']==iri for item in added.json()['summary']['classes'])
        assert len(app.state.service.repository.list_ontologies(p))==1
        assert iri in added.json()['turtle']
        reparsed=Ontology(added.json()['turtle'])
        assert any(str(item)==iri for item in reparsed.classes)
    finally:c.__exit__(None,None,None)


def test_backend_generates_readable_unicode_iri_when_client_omits_it(tmp_path):
    c,app,p,base,old=setup(tmp_path)
    try:
        added=c.post(base+'/ontology/terms',json={'kind':'class','label':'Protected Object','label_zh':'保护对象',
            'expected_ontology_id':old['id']})
        assert added.status_code==201,added.text
        expected='urn:knowledge:ontology:'+p+':保护对象'
        assert any(item['id']==expected for item in added.json()['summary']['classes'])
    finally:c.__exit__(None,None,None)
