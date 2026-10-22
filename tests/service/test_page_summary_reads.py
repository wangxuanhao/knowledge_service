"""Page reads stay bounded to relevant facts and preserve scope/provenance."""
import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services.explorer import Explorer
from knowledge_service.services.ontology_discovery_workflow import OntologyDiscoveryWorkflow
from knowledge_service.services.service import KnowledgeService


def record(repo, project, id, kind='document', **extra):
    return repo.put_record(project, {'id': id, 'kind': kind, 'text': 'body '+id,
        'valid_from': None, 'valid_until': None, 'metadata': {}, **extra})


def assertion(repo, project, id, record_id, document_id):
    repo.create_assertion(project, {'id': id, 'kind': 'entity',
        'document_id': document_id, 'payload': {'text': id}, 'actor': 'test'})
    return repo.transition_assertion(project, id, 1, 'accepted', 'confirmed', 'test',
                                     canonical_record_id=record_id)


def test_source_directory_pages_metadata_and_counts_all_states(tmp_path, monkeypatch):
    repo = Repository(tmp_path/'sources')
    p = repo.create_project('sources')['id']
    for id, status in [('a','completed'), ('b','failed'), ('c','processing')]:
        record(repo,p,id,metadata={'status':status,'title':id,'discovery_candidates':[{'text':'large'}]})
    service=KnowledgeService(repo,HashingEncoder())
    monkeypatch.setattr(service,'scoped',lambda *a,**k:pytest.fail('directory scanned whole project'))
    explorer=Explorer(service)
    first=explorer.sources(p,{},limit=1,summary_only=True)
    assert len(first['documents'])==1 and first['total']==3 and first['next_cursor']
    assert first['counts']=={'all':3,'ready':1,'failed':1,'processing':1}
    assert 'text' not in first['documents'][0]
    assert 'discovery_candidates' not in first['documents'][0]['metadata']
    second=explorer.sources(p,{},limit=1,summary_only=True,cursor=first['next_cursor'])
    assert second['documents'][0]['id']!=first['documents'][0]['id']
    failed=explorer.sources(p,{},summary_only=True,state='failed')
    assert [d['id'] for d in failed['documents']]==['b']
    assert failed['total']==1 and failed['counts']['all']==3


def test_sources_count_shared_assertion_provenance_and_skip_unrelated_legacy_rows(tmp_path):
    repo=Repository(tmp_path/'provenance');p=repo.create_project('p')['id']
    for id in ['doc-a','doc-b']:record(repo,p,id,metadata={'region':'east'})
    record(repo,p,'shared','entity',metadata={'region':'east'})
    record(repo,p,'hidden','entity',metadata={'region':'west'})
    record(repo,p,'chunk','chunk',source_id='doc-a',metadata={'region':'east'})
    record(repo,p,'unrelated','entity')
    # Retained pre-schema/capacity fixtures can omit optional validity bounds.
    repo._db.execute("UPDATE record_versions SET payload=(payload::jsonb-'valid_from'-'valid_until')::text WHERE project_id=? AND id='unrelated'",(p,))
    assertion(repo,p,'a1','shared','doc-a');assertion(repo,p,'a2','shared','doc-b')
    assertion(repo,p,'a3','hidden','doc-a')
    scope={'filters':{'field':'metadata.region','op':'eq','value':'east'}}
    docs=Explorer(KnowledgeService(repo,HashingEncoder())).sources(p,scope)['documents']
    by_id={d['id']:d for d in docs}
    assert by_id['doc-a']['derived_breakdown']=={'entity':1,'relation':0,'attribute':0,'chunk':1}
    assert by_id['doc-b']['derived_breakdown']['entity']==1
    assert [e['text'] for e in by_id['doc-a']['derived_entities']]==['body shared']


def test_source_directory_point_and_cursor_respect_project_scope_and_history(tmp_path):
    repo=Repository(tmp_path/'scope');p=repo.create_project('p')['id'];q=repo.create_project('q')['id']
    first=record(repo,p,'doc',metadata={'region':'east'})
    repo.put_record(p,{'id':'doc','kind':'document','text':'revised','valid_from':None,
        'valid_until':None,'metadata':{'region':'west'}},expected_version=1)
    record(repo,q,'other')
    explorer=Explorer(KnowledgeService(repo,HashingEncoder()))
    old=explorer.sources(p,{'known_at':first['recorded_at']},document_id='doc')
    assert old['documents'][0]['text']=='body doc'
    hidden=explorer.sources(p,{'filters':{'field':'metadata.region','op':'eq','value':'east'}},document_id='doc')
    assert hidden['documents']==[]
    assert explorer.sources(p,{},document_id='other')['documents']==[]


def test_discovery_overview_never_materializes_unrelated_facts(tmp_path,monkeypatch):
    repo=Repository(tmp_path/'discovery');p=repo.create_project('p')['id']
    record(repo,p,'doc',metadata={'title':'source','discovery_candidates':[
        {'id':'candidate','kind':'entity','text':'name','proposed_type':'Thing'}]})
    record(repo,p,'formal','entity')
    assertion(repo,p,'candidate','formal','doc')
    record(repo,p,'unrelated','entity')
    monkeypatch.setattr(repo,'current_records',lambda *a,**k:pytest.fail('overview scanned all facts'))
    overview=OntologyDiscoveryWorkflow(KnowledgeService(repo,HashingEncoder())).overview(p)
    assert overview['candidate_count']==1
    assert overview['candidate_status_counts']=={'pending':0,'included_in_draft':0,'approved':0,'materialized':1}


def test_assertion_batch_is_project_bounded_deduplicated_and_reports_evidence_limits(tmp_path):
    app=create_app(tmp_path/'batch',encoder=HashingEncoder())
    with TestClient(app) as client:
        repo=app.state.service.repository;p=repo.create_project('p')['id'];q=repo.create_project('q')['id']
        record(repo,p,'e','entity');record(repo,q,'other','entity')
        for index in range(3):assertion(repo,p,f'a{index}','e','doc')
        assertion(repo,q,'q1','other','doc')
        url=f'/api/projects/{p}/assertions/batch'
        response=client.post(url,json={'record_ids':['e','e','missing','other']})
        assert response.status_code==200,response.text
        data=response.json()
        assert set(data['assertions_by_record'])=={'e','missing','other'}
        assert len(data['assertions_by_record']['e'])==3
        assert data['assertions_by_record']['missing']==data['assertions_by_record']['other']==[]
        assert data['total']==3 and data['truncated_record_ids']==[]
        assert client.post(url,json={'record_ids':['e']*101}).status_code==422
        from knowledge_service.repository.page_reads import PageReads
        capped=PageReads(repo).assertions_batch(p,['e'],per_record_limit=2)
        assert len(capped['assertions_by_record']['e'])==2
        assert capped['total']==3 and capped['truncated_record_ids']==['e']


def test_metadata_facets_never_materializes_full_record_payloads(tmp_path,monkeypatch):
    app=create_app(tmp_path/'facets',encoder=HashingEncoder())
    with TestClient(app) as client:
        repo=app.state.service.repository;p=repo.create_project('facets')['id']
        record(repo,p,'a','entity',metadata={'region':'east','count':1})
        record(repo,p,'b','entity',metadata={'region':'east','count':1})
        def full_scan(*args,**kwargs):
            raise AssertionError('facets decoded all records')
        monkeypatch.setattr(app.state.service,'scoped',full_scan)
        r=client.post(f'/api/projects/{p}/metadata/facets',json={})
        assert r.status_code==200,r.text
        fields={row['field']:row for row in r.json()['fields']}
        assert r.json()['records']==2 and fields['metadata.region']['records']==2


def test_historical_source_links_do_not_precede_their_acceptance(tmp_path):
    repo=Repository(tmp_path/'source-link-history');p=repo.create_project('p')['id']
    record(repo,p,'doc');entity=record(repo,p,'e','entity')
    accepted=assertion(repo,p,'accepted-later','e','doc')
    later=(datetime.fromisoformat(entity['recorded_at'].replace('Z','+00:00'))+timedelta(seconds=10)).isoformat()
    repo._db.execute("UPDATE assertion_events SET created_at=? WHERE assertion_id=? AND to_status='accepted'",(later,accepted['id']))
    explorer=Explorer(KnowledgeService(repo,HashingEncoder()))
    old=explorer.sources(p,{'known_at':entity['recorded_at']})
    assert old['documents'][0]['derived_count']==0
    assert explorer.sources(p,{})['documents'][0]['derived_count']==1


def test_sources_kind_scope_remains_effective(tmp_path):
    repo=Repository(tmp_path/'source-kinds');p=repo.create_project('p')['id']
    record(repo,p,'doc');record(repo,p,'e','entity',source_id='doc')
    explorer=Explorer(KnowledgeService(repo,HashingEncoder()))
    assert explorer.sources(p,{'kinds':['entity']})['documents']==[]
    docs=explorer.sources(p,{'kinds':['document']})['documents']
    assert len(docs)==1 and docs[0]['derived_count']==0


def test_source_associations_use_assertions_and_chunk_ownership_only(tmp_path):
    from knowledge_service.repository.page_reads import PageReads
    repo = Repository(tmp_path/'canonical-source-links')
    p = repo.create_project('source links')['id']
    record(repo, p, 'doc')
    record(repo, p, 'chunk', 'chunk', source_id='doc')
    record(repo, p, 'old-fact', 'entity', source_id='doc',
           metadata={'discovery_candidate_ids': ['old-pending']})
    repo.create_assertion(p, {'id': 'old-pending', 'kind': 'entity',
                             'document_id': 'doc', 'payload': {'text': 'pending'}})
    record(repo, p, 'current-fact', 'entity')
    assertion(repo, p, 'canonical', 'current-fact', 'doc')
    docs = Explorer(KnowledgeService(repo, HashingEncoder())).sources(p, {})['documents']
    assert docs[0]['derived_breakdown'] == {'entity': 1, 'relation': 0, 'attribute': 0, 'chunk': 1}
    assert [row['text'] for row in docs[0]['derived_entities']] == ['body current-fact']
    batch = PageReads(repo).assertions_batch(p, ['old-fact', 'current-fact'])
    assert batch['assertions_by_record']['old-fact'] == []
    assert [a['id'] for a in batch['assertions_by_record']['current-fact']] == ['canonical']


def test_obsolete_project_import_and_direct_ontology_writes_are_not_exposed(tmp_path):
    app = create_app(tmp_path/'current-routes', encoder=HashingEncoder())
    routes = app.openapi()['paths']
    assert '/api/local-projects' not in routes
    assert '/api/local-projects/import' not in routes
    assert 'post' not in routes.get('/api/projects/{project_id}/ontologies', {})


def test_batch_lineage_ignores_unbound_pending_candidate_metadata(tmp_path):
    from knowledge_service.repository.page_reads import PageReads
    repo=Repository(tmp_path/'lineage-bodies');p=repo.create_project('p')['id']
    record(repo,p,'e','entity',metadata={'discovery_candidate_id':'pending'})
    repo.create_assertion(p,{'id':'pending','kind':'entity','document_id':'doc',
        'payload':{'text':'x'*50000},'quote':'q'*50000,'actor':'test'})
    result=PageReads(repo).assertions_batch(p,['e'])
    assert result['assertions_by_record']['e']==[]
    assert len(json.dumps(result))<1000
