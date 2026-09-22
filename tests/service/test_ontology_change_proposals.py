from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


TTL='''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:Person a owl:Class .'''


def test_candidate_can_propose_and_approve_versioned_ontology_change(tmp_path):
    app=create_app(tmp_path/'proposal.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'proposal','use_default_ontology':False}).json()['id']
        base='/api/projects/'+project
        ontology=client.post(base+'/ontologies',json={'turtle':TTL}).json()
        entities=client.post(base+'/records',json={'records':[
            {'id':'person-a','kind':'entity','type':'https://test/Person','text':'甲'},
            {'id':'person-b','kind':'entity','type':'https://test/Person','text':'乙'}]})
        assert entities.status_code==201,entities.text
        candidate={'id':'candidate-1','kind':'relation','predicate':'worksFor','subject_id':'person-a',
            'object_id':'person-b','subject':'甲','object':'乙','status':'pending','start_char':0,'end_char':3,
            'source_hash':'hash','chunk_id':'chunk','created_at':'2026-01-01T00:00:00Z'}
        document=app.state.service.repository.put_record(project,{'id':'doc','kind':'document','text':'甲和乙',
            'metadata':{'title':'来源','status':'ready','review_candidates':[candidate]}})
        body={'document_id':'doc','candidate_id':'candidate-1','operation':'add','kind':'relation',
            'uri':'https://test/worksFor','label':'任职于','domain':'https://test/Person',
            'range':'https://test/Person','rationale':'原文出现稳定的新关系','expected_ontology_id':ontology['id'],
            'expected_document_version':document['version']}
        created=client.post(base+'/ontology-change-proposals',json=body)
        assert created.status_code==201,created.text
        draft=created.json()
        assert draft['status']=='pending' and draft['impact']['linked_candidates']==1
        approved=client.post(base+'/ontology-change-proposals/'+draft['id']+'/decision',json={
            'action':'approve','note':'业务负责人确认','expected_revision':draft['revision'],
            'expected_ontology_id':ontology['id']})
        assert approved.status_code==200,approved.text
        result=approved.json()
        assert result['proposal']['status']=='approved'
        assert result['ontology']['id']!=ontology['id']
        assert any(x['id']=='https://test/worksFor' for x in result['ontology']['summary']['relations'])
        reviews=client.get(base+'/reviews').json()['reviews']
        assert reviews[0]['ontology_change']['status']=='ready_for_review'
        assert reviews[0]['ontology_change']['ontology_id']==result['ontology']['id']


def test_new_proposal_generates_unicode_iri_when_client_omits_it(tmp_path):
    app=create_app(tmp_path/'proposal-generated-iri.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'proposal','use_default_ontology':False}).json()['id']
        base='/api/projects/'+project
        ontology=client.post(base+'/ontologies',json={'turtle':TTL}).json()
        doc=app.state.service.repository.put_record(project,{'id':'doc','kind':'document','text':'平台规范',
            'metadata':{'status':'ready','review_candidates':[{'id':'c','kind':'entity','text':'平台规范',
                'proposed_type':'规则文件','status':'pending'}]}})
        created=client.post(base+'/ontology-change-proposals',json={'document_id':'doc','candidate_id':'c',
            'operation':'add','kind':'class','label':'规则文件','rationale':'新增业务类型',
            'expected_ontology_id':ontology['id'],'expected_document_version':doc['version']})

        assert created.status_code==201,created.text
        assert created.json()['uri']==f'urn:knowledge:ontology:{project}:规则文件'


def test_stale_or_rejected_proposal_never_changes_ontology(tmp_path):
    app=create_app(tmp_path/'proposal-safety.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'proposal','use_default_ontology':False}).json()['id']
        base='/api/projects/'+project
        ontology=client.post(base+'/ontologies',json={'turtle':TTL}).json()
        doc=app.state.service.repository.put_record(project,{'id':'doc','kind':'document','text':'甲',
            'metadata':{'status':'ready','review_candidates':[{'id':'c','kind':'entity','text':'甲',
                'proposed_type':'Worker','status':'pending'}]}})
        draft=client.post(base+'/ontology-change-proposals',json={'document_id':'doc','candidate_id':'c',
            'operation':'add','kind':'class','uri':'https://test/Worker','label':'员工','rationale':'新类型',
            'expected_ontology_id':ontology['id'],'expected_document_version':doc['version']}).json()
        rejected=client.post(base+'/ontology-change-proposals/'+draft['id']+'/decision',json={
            'action':'reject','note':'暂不纳入','expected_revision':draft['revision'],
            'expected_ontology_id':ontology['id']})
        assert rejected.status_code==200
        assert len(app.state.service.repository.list_ontologies(project))==1
        again=client.post(base+'/ontology-change-proposals/'+draft['id']+'/decision',json={
            'action':'approve','note':'重复','expected_revision':draft['revision'],
            'expected_ontology_id':ontology['id']})
        assert again.status_code==422


def test_high_impact_adjustment_requires_explicit_confirmation(tmp_path):
    app=create_app(tmp_path/'proposal-impact.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'impact','use_default_ontology':False}).json()['id']
        base='/api/projects/'+project
        ontology=client.post(base+'/ontologies',json={'turtle':TTL}).json()
        assert client.post(base+'/records',json={'records':[{'id':'p','kind':'entity','type':'https://test/Person','text':'甲'}]}).status_code==201
        doc=app.state.service.repository.put_record(project,{'id':'doc','kind':'document','text':'甲',
            'metadata':{'status':'ready','review_candidates':[{'id':'c','kind':'entity','text':'甲',
                'proposed_type':'Person','status':'pending'}]}})
        draft=client.post(base+'/ontology-change-proposals',json={'document_id':'doc','candidate_id':'c',
            'operation':'update','kind':'class','uri':'https://test/Person','label':'自然人','rationale':'调整业务定义',
            'expected_ontology_id':ontology['id'],'expected_document_version':doc['version']}).json()
        assert draft['impact']['risk']=='high' and draft['impact']['record_count']==1
        decision={'action':'approve','note':'负责人确认','expected_revision':1,'expected_ontology_id':ontology['id']}
        denied=client.post(base+'/ontology-change-proposals/'+draft['id']+'/decision',json=decision)
        assert denied.status_code==422 and '明确确认' in denied.text
        decision['confirm_impact']=True
        assert client.post(base+'/ontology-change-proposals/'+draft['id']+'/decision',json=decision).status_code==200
