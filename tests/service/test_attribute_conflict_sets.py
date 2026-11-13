"""Identity merges quarantine every disputed value; reviewers commit exact sets."""
import pytest

from ontology_seed import seed_ontology
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services.governance import Governance
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.service import KnowledgeService


TTL = '''@prefix : <https://conflict.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
:Person a owl:Class . :count a owl:DatatypeProperty ;
rdfs:domain :Person ; rdfs:range xsd:integer .
:Shape a sh:NodeShape ; sh:targetClass :Person ;
sh:property [ sh:path :count ; sh:maxCount 1 ] .'''


def setup_conflict(tmp_path,service=None,*,turtle=TTL):
    repo = service.repository if service else Repository(tmp_path / 'conflicts.sqlite')
    service = service or KnowledgeService(repo, HashingEncoder())
    project = repo.create_project('conflicts')['id']
    seed_ontology(repo, project, turtle, Ontology(turtle).summary())
    service.write(project, [
        dict(id='keep', kind='entity', type='Person', text='甲'),
        dict(id='drop', kind='entity', type='Person', text='甲'),
        dict(id='left', kind='attribute', type='count', text='数量1', value=1,
             datatype='http://www.w3.org/2001/XMLSchema#integer', subject_id='keep'),
        dict(id='right', kind='attribute', type='count', text='数量2', value=2,
             datatype='http://www.w3.org/2001/XMLSchema#integer', subject_id='drop'),
    ])
    return repo, service, project, Governance(service)


def quarantine(gov, project):
    preview = gov.merge_preview(project, 'keep', 'drop', {'keep': 1, 'drop': 1})
    return gov.merge(project, 'keep', 'drop', preview['expected_versions'],
                     preview_fingerprint=preview['preview_fingerprint'],
                     confirmed_identity=True)


def resolve_payload(detail, accepted='left'):
    return dict(expected_version=detail['version'], expected_members=detail['members'],
                decisions=[dict(attribute_id=member['record_id'],
                                decision='accept' if member['record_id'] == accepted else 'reject')
                           for member in detail['members']], reason='已逐值核对原文')


def test_identity_merge_quarantines_without_winner_and_preserves_history(tmp_path):
    repo, service, p, gov = setup_conflict(tmp_path)
    prior = repo.get_record(p, 'left')['recorded_at']
    result = quarantine(gov, p)
    assert len(result['conflict_sets']) == 1
    assert not [r for r in service.scoped(p, {}) if r['kind'] == 'attribute']
    assert {r['id'] for r in service.scoped(p, {'known_at': prior}) if r['kind'] == 'attribute'} == {'left', 'right'}
    for record_id in ('left', 'right'):
        versions = repo.history(p, record_id)
        assert versions[-1]['metadata']['_quarantined'] is True
        assert versions[-1]['subject_id'] == 'keep'
        assert versions[0]['value'] == versions[-1]['value']
        assert {a['status'] for a in repo.list_assertions(p, canonical_record_id=record_id)} == {'contradicting'}


def test_conflict_resolution_exact_cas_and_replay_rejection(tmp_path):
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo, service, p, gov = setup_conflict(tmp_path)
    result = quarantine(gov, p)
    conflicts = AttributeConflicts(service)
    detail = conflicts.read(p, result['conflict_sets'][0]['id'], {})
    payload = resolve_payload(detail)
    resolved = conflicts.resolve(p, detail['id'], payload, actor='reviewer')
    assert resolved['state'] == 'resolved'
    assert [r['id'] for r in service.scoped(p, {}) if r['kind'] == 'attribute'] == ['left']
    assert repo.get_record(p, 'left')['value'] == 1
    with pytest.raises(ValueError, match='版本冲突'):
        conflicts.resolve(p, detail['id'], payload, actor='reviewer')


def test_conflict_resolution_revalidates_members_and_rolls_back(tmp_path):
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo, service, p, gov = setup_conflict(tmp_path)
    result = quarantine(gov, p)
    conflicts = AttributeConflicts(service)
    detail = conflicts.read(p, result['conflict_sets'][0]['id'], {})
    payload = resolve_payload(detail)
    payload['decisions'][1]['decision'] = 'accept'
    before = {r:len(repo.history(p,r)) for r in ('left','right')}
    with pytest.raises(ValueError):
        conflicts.resolve(p, detail['id'], payload, actor='reviewer')
    assert {r:len(repo.history(p,r)) for r in before} == before
    assert conflicts.read(p, detail['id'], {})['state'] == 'open'


def test_reject_all_conflict_values_preserves_required_property_and_open_set(tmp_path):
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    from knowledge_service.services.service import OntologyValidationError
    required=TTL.replace('sh:maxCount 1','sh:minCount 1 ; sh:maxCount 1')
    repo,service,p,gov=setup_conflict(tmp_path,turtle=required)
    result=quarantine(gov,p);conflicts=AttributeConflicts(service)
    detail=conflicts.read(p,result['conflict_sets'][0]['id'],{})
    before={id:len(repo.history(p,id)) for id in ('left','right')}
    payload=resolve_payload(detail)
    payload['decisions']=[dict(attribute_id=m['record_id'],decision='reject') for m in detail['members']]
    with pytest.raises(OntologyValidationError) as caught:
        conflicts.resolve(p,detail['id'],payload,actor='reviewer')
    assert any(v['constraint']=='MinCountConstraintComponent' for v in caught.value.errors)
    assert conflicts.read(p,detail['id'],{})['state']=='open'
    assert {id:len(repo.history(p,id)) for id in before}==before


def test_conflict_resolution_rejects_changed_assertion(tmp_path):
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo, service, p, gov = setup_conflict(tmp_path)
    result = quarantine(gov, p)
    conflicts = AttributeConflicts(service)
    detail = conflicts.read(p, result['conflict_sets'][0]['id'], {})
    assertion = repo.list_assertions(p, canonical_record_id='left')[0]
    repo.transition_assertion(p, assertion['id'], assertion['decision_version'],
                              'superseded', '来源已撤回', 'reviewer')
    with pytest.raises(ValueError, match='版本冲突'):
        conflicts.resolve(p, detail['id'], resolve_payload(detail), actor='reviewer')


def test_duplicate_scan_is_paged_and_batch_keeps_groups_independent(tmp_path):
    repo, service, p, gov = setup_conflict(tmp_path)
    scan = gov.scan_duplicates(p, limit=1)
    assert len(scan['groups']) == 1
    group = scan['groups'][0]
    preview = gov.preview_duplicate_group(p, group['key'])
    assert preview['attribute_conflicts']
    result = gov.confirm_duplicate_groups(p, [
        dict(key=group['key'], preview_fingerprint='0'*64),
        dict(key=group['key'], preview_fingerprint=preview['preview_fingerprint']),
    ], actor='reviewer')
    assert [item['status'] for item in result['results']] == ['failed', 'merged']
    assert len(result['results'][1]['conflict_sets']) == 1


def test_conflict_resolution_can_approve_nonoverlapping_corrected_intervals(tmp_path):
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo, service, p, gov = setup_conflict(tmp_path)
    result = quarantine(gov, p)
    conflicts = AttributeConflicts(service)
    detail = conflicts.read(p, result['conflict_sets'][0]['id'], {})
    payload = resolve_payload(detail)
    payload['decisions'] = [
        dict(attribute_id='left', decision='accept', valid_from='2026-01-01T00:00:00Z', valid_until='2026-06-01T00:00:00Z'),
        dict(attribute_id='right', decision='accept', valid_from='2026-06-01T00:00:00Z', valid_until='2027-01-01T00:00:00Z')]
    conflicts.resolve(p, detail['id'], payload, actor='reviewer')
    assert [r['id'] for r in service.scoped(p, {'valid_at':'2026-03-01T00:00:00Z'}) if r['kind']=='attribute']==['left']
    assert [r['id'] for r in service.scoped(p, {'valid_at':'2026-07-01T00:00:00Z'}) if r['kind']=='attribute']==['right']


def test_quarantine_merge_can_be_reversed_before_review(tmp_path):
    from knowledge_service.repository.attribute_conflicts import ConflictReads
    repo, service, p, gov = setup_conflict(tmp_path)
    result = quarantine(gov, p)
    gov.undo_merge(p, result['operation_id'])
    assert {r['id'] for r in service.scoped(p,{}) if r['kind']=='attribute'}=={'left','right'}
    assert not ConflictReads(repo).list_for_entity(p,'keep',{})['conflict_sets']


def test_quarantine_allowance_rejects_unrelated_constraints_and_preexisting_deficit():
    from knowledge_service.services.attribute_conflicts import allow_quarantine_deficits
    violation=dict(record_id='keep',path='p',constraint='MinCountConstraintComponent')
    report=dict(conforms=False,violations=[violation],errors=[violation])
    context=dict(entity_id='keep',predicates=['p'],member_ids=['a','b'],preview_fingerprint='a'*64)
    assert allow_quarantine_deficits(report,{'conforms':True},context)
    assert not allow_quarantine_deficits(report,report,context)
    assert not allow_quarantine_deficits({**report,'errors':[{'message':'unrelated'}]}, {'conforms':True},context)
    assert not allow_quarantine_deficits({**report,'violations':[{**violation,'path':'other'}]}, {'conforms':True},context)


def test_conflict_read_contract_is_scoped_and_viewer_read_only():
    from knowledge_service.api.security_middleware import requires_write_access
    for path in ('attribute-conflicts/set/read','attribute-conflicts/set/members/a/read',
                 'attribute-conflicts/set/members/a/sources','duplicates/scan','duplicates/preview'):
        assert not requires_write_access('POST','/api/projects/p/'+path)
    assert requires_write_access('POST','/api/projects/p/attribute-conflicts/set/resolve')
    assert requires_write_access('POST','/api/projects/p/duplicates/confirm')


def test_disjoint_explicit_scopes_do_not_create_maxcount_conflicts():
    from types import SimpleNamespace
    rows=[dict(id='owner',kind='entity',text='甲',type='https://conflict.test/Person'),
        *[dict(id=id,kind='attribute',type='https://conflict.test/count',subject_id='owner',
          value=value,datatype='http://www.w3.org/2001/XMLSchema#integer',
          metadata={'applicability':{'platforms':{'mode':'specific','values':[platform]}}})
          for id,value,platform in [('left',1,'美团'),('right',2,'淘宝')]]]
    ontology=Ontology(TTL)
    assert ontology.validate_timeline(rows)['conforms']
    repo=SimpleNamespace(get_ontology=lambda *args:{'turtle':TTL})
    assert Governance(SimpleNamespace(repository=repo))._attribute_conflicts('p',rows[0],rows[1:])==[]
    rows[2]['metadata']['applicability']['platforms']['values']=['美团']
    assert not ontology.validate_timeline(rows)['conforms']


def test_full_snapshot_preserves_open_conflict_and_rejects_overwriting_closed_set(tmp_path):
    from knowledge_service.repository.attribute_conflicts import ConflictReads
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo, service, p, gov = setup_conflict(tmp_path)
    result=quarantine(gov,p);set_id=result['conflict_sets'][0]['id']
    snapshot=repo.export_projection(p)
    assert snapshot['governance']['attribute_conflict_sets'][0]['id']==set_id
    target=Repository(tmp_path/'restored-conflicts.sqlite');target.restore_projection(snapshot)
    restored=KnowledgeService(target,HashingEncoder());detail=ConflictReads(target).read(p,set_id,{})
    assert detail['state']=='open'
    AttributeConflicts(restored).resolve(p,set_id,resolve_payload(detail),actor='reviewer')
    with pytest.raises(ValueError,match='已存在'):
        target.restore_projection(snapshot)


def test_reopened_conflict_can_reject_withdrawn_support_and_accept_remaining_value(tmp_path):
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo, service, p, gov = setup_conflict(tmp_path)
    result=quarantine(gov,p);conflicts=AttributeConflicts(service);set_id=result['conflict_sets'][0]['id']
    support=repo.list_assertions(p,canonical_record_id='left')[0]
    repo.transition_assertion(p,support['id'],support['decision_version'],'superseded','来源撤回','reviewer')
    refreshed=conflicts.read(p,set_id,{})
    assert refreshed['members'][0]['assertion_versions'][0]['status']=='superseded'
    conflicts.resolve(p,set_id,resolve_payload(refreshed,accepted='right'),actor='reviewer')
    assert [r['id'] for r in service.scoped(p,{}) if r['kind']=='attribute']==['right']


def test_resolving_to_existing_same_value_retires_original_quarantined_id(tmp_path):
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo, service, p, gov = setup_conflict(tmp_path)
    result=quarantine(gov,p);set_id=result['conflict_sets'][0]['id']
    service.write(p,[dict(id='new-fact',kind='attribute',type='count',subject_id='keep',text='已核对数量1',
                         value=1,datatype='http://www.w3.org/2001/XMLSchema#integer')])
    conflicts=AttributeConflicts(service);detail=conflicts.read(p,set_id,{})
    conflicts.resolve(p,set_id,resolve_payload(detail),actor='reviewer')
    latest=repo.history(p,'left')[-1]
    assert latest['metadata'].get('_deleted') is True
    assert not latest['metadata'].get('_quarantined')
    assert latest['metadata']['merged_into_fact']=='new-fact'


def test_authenticated_viewer_reads_attributes_and_conflict_evidence_but_cannot_resolve(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    app=create_app(tmp_path/'viewer-attributes.sqlite',HashingEncoder(),auth_disabled=False)
    with TestClient(app) as client:
        admin=client.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()['access_token']
        response=client.post('/api/users',headers={'Authorization':'Bearer '+admin},
            json={'username':'attribute-viewer','password':'pass123','role':'viewer'})
        assert response.status_code==201,response.text
        viewer=client.post('/api/auth/login',json={'username':'attribute-viewer','password':'pass123'}).json()['access_token']
        headers={'Authorization':'Bearer '+viewer}
        repo,service,p,gov=setup_conflict(tmp_path,app.state.service);base='/api/projects/'+p
        for suffix in ('/entities/keep/attributes','/entities/keep/attributes/left','/entities/keep/attributes/left/sources'):
            response=client.post(base+suffix,json={},headers=headers)
            assert response.status_code==200,response.text
        merged=quarantine(gov,p);set_id=merged['conflict_sets'][0]['id']
        for suffix in ('/attribute-conflicts','/attribute-conflicts/'+set_id+'/read',
                       '/attribute-conflicts/'+set_id+'/members/left/read','/attribute-conflicts/'+set_id+'/members/left/sources'):
            response=client.post(base+suffix,json={},headers=headers)
            assert response.status_code==200,response.text
        assert client.post(base+'/attribute-conflicts/'+set_id+'/resolve',json={},headers=headers).status_code==403
        assert client.post(base+'/duplicates/confirm',json={},headers=headers).status_code==403


def test_batch_reports_uncertain_group_failures_and_continues_other_groups():
    from knowledge_service.services.duplicate_governance import DuplicateGovernance
    batch=DuplicateGovernance.__new__(DuplicateGovernance)
    def confirm(project,key,fingerprint,*,actor):
        if key=='one':raise RuntimeError('transport interrupted')
        return dict(key=key,status='merged')
    batch.confirm=confirm
    result=batch.batch('p',[dict(key=k,preview_fingerprint='a'*64) for k in ('one','two')],actor='reviewer')
    assert [r['status'] for r in result['results']]==['failed','merged']
    assert result['results'][0]['refresh_required'] is True


def document_supported_conflict(tmp_path,right_value=2,*,turtle=TTL):
    repo=Repository(tmp_path/'document-conflict.sqlite');service=KnowledgeService(repo,HashingEncoder())
    p=repo.create_project('document conflict')['id'];seed_ontology(repo,p,turtle,Ontology(turtle).summary())
    service.write(p,[dict(id='doc-left',kind='document',text='来源甲'),dict(id='doc-right',kind='document',text='来源乙')])
    service.write(p,[
        dict(id=entity,kind='entity',type='Person',text='甲',source_id=document)
        for entity,document in [('keep','doc-left'),('drop','doc-right')]]+[
        dict(id=id,kind='attribute',type='count',text='数量'+str(value),value=value,
             datatype='http://www.w3.org/2001/XMLSchema#integer',subject_id=entity,source_id=document)
        for id,value,entity,document in [('left',1,'keep','doc-left'),('right',right_value,'drop','doc-right')]])
    return repo,service,p,Governance(service)


def test_complete_conflict_read_resolves_two_independent_platform_pairs(tmp_path):
    from knowledge_service.repository.attribute_conflicts import ConflictReads
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo=Repository(tmp_path/'four-conflicts.sqlite');service=KnowledgeService(repo,HashingEncoder())
    p=repo.create_project('platform pairs')['id'];seed_ontology(repo,p,TTL,Ontology(TTL).summary())
    service.write(p,[dict(id=id,kind='entity',type='Person',text='甲') for id in ('keep','drop')]+[
        dict(id=id,kind='attribute',type='count',text='值'+str(value),value=value,
             datatype='http://www.w3.org/2001/XMLSchema#integer',subject_id=entity,
             metadata={'applicability':{'platforms':{'mode':'specific','values':[platform]}}})
        for id,value,entity,platform in [('a',1,'keep','美团'),('b',2,'drop','美团'),('c',3,'keep','淘宝'),('d',4,'drop','淘宝')]])
    merged=quarantine(Governance(service),p);set_id=merged['conflict_sets'][0]['id'];reads=ConflictReads(repo)
    scoped=reads.read(p,set_id,{'applicability':{'platforms':{'mode':'specific','values':['美团']}}})
    assert len(scoped['members'])==2
    complete=reads.read(p,set_id,{'include_unknown':True});assert len(complete['members'])==4
    payload=resolve_payload(complete);payload['decisions']=[dict(attribute_id=m['record_id'],decision='accept' if m['record_id'] in {'a','c'} else 'reject') for m in complete['members']]
    AttributeConflicts(service).resolve(p,set_id,payload,actor='reviewer')
    assert {r['id'] for r in service.scoped(p,{}) if r['kind']=='attribute'}=={'a','c'}


def test_withdrawing_all_owner_documents_keeps_frozen_conflict_rejectable(tmp_path):
    from knowledge_service.repository.attribute_conflicts import ConflictReads
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    repo,service,p,gov=document_supported_conflict(tmp_path);merged=quarantine(gov,p);set_id=merged['conflict_sets'][0]['id']
    gov.delete(p,'doc-left',1);gov.delete(p,'doc-right',1)
    assert not [r for r in service.scoped(p,{}) if r['kind']=='entity']
    page=ConflictReads(repo).page(p,{});assert len(page['conflict_sets'])==1
    detail=ConflictReads(repo).read(p,set_id,{});assert detail['owner_available'] is False
    with pytest.raises(ValueError,match='撤回|来源'):
        AttributeConflicts(service).resolve(p,set_id,resolve_payload(detail),actor='reviewer')
    payload=resolve_payload(detail);payload['decisions']=[dict(attribute_id=m['record_id'],decision='reject') for m in detail['members']]
    assert AttributeConflicts(service).resolve(p,set_id,payload,actor='reviewer')['state']=='resolved'


def test_withdrawn_required_property_owner_can_close_conflict_by_rejecting_values(tmp_path):
    from knowledge_service.repository.attribute_conflicts import ConflictReads
    from knowledge_service.services.attribute_conflicts import AttributeConflicts
    required=TTL.replace('sh:maxCount 1','sh:minCount 1 ; sh:maxCount 1')
    repo,service,p,gov=document_supported_conflict(tmp_path,turtle=required)
    result=quarantine(gov,p);set_id=result['conflict_sets'][0]['id']
    gov.delete(p,'doc-left',1);gov.delete(p,'doc-right',1)
    detail=ConflictReads(repo).read(p,set_id,{})
    assert detail['owner_available'] is False
    payload=resolve_payload(detail)
    payload['decisions']=[dict(attribute_id=m['record_id'],decision='reject') for m in detail['members']]
    assert AttributeConflicts(service).resolve(p,set_id,payload,actor='reviewer')['state']=='resolved'


def test_duplicate_preview_freezes_member_sources_and_exposes_bounded_paging(tmp_path):
    repo,service,p,gov=document_supported_conflict(tmp_path)
    group=gov.scan_duplicates(p)['groups'][0];preview=gov.preview_duplicate_group(p,group['key'])
    assert all(member['document_count']==1 and member['support_count']==1 for member in preview['members'])
    assert {m['source_contexts'][0]['document_id'] for m in preview['members']}=={'doc-left','doc-right'}
    assert all(m['source_contexts'][0]['document_version_id'] for m in preview['members'])
    assert 'fact_deduplication' in preview


def test_duplicate_preview_reports_same_fact_support_union_and_source_cursor(tmp_path):
    repo,service,p,gov=document_supported_conflict(tmp_path,right_value=1)
    documents=service.write(p,[dict(id='extra-'+str(n),kind='document',text='补充来源'+str(n)) for n in range(10)])
    service.write(p,[],operation='adopt_discovery',assertions=[dict(id='extra-mention-'+str(n),kind='entity',
        document_id=document['id'],document_version_id=document['version_id'],canonical_record_id='keep',quote='甲',
        payload=dict(id='keep',kind='entity',type='https://conflict.test/Person',text='甲')) for n,document in enumerate(documents)])
    group=gov.scan_duplicates(p)['groups'][0];preview=gov.preview_duplicate_group(p,group['key'])
    keeper=next(m for m in preview['members'] if m['id']=='keep')
    assert keeper['document_count']==11 and keeper['support_count']==11
    assert len(keeper['source_contexts'])==10 and keeper['source_contexts_truncated']
    rest=gov.duplicate_sources(p,group['key'],preview['preview_fingerprint'],'keep',keeper['version_id'],cursor=keeper['source_next_cursor'])
    assert len(rest['source_contexts'])==1 and rest['next_cursor'] is None
    assert len({s['context_id'] for s in [*keeper['source_contexts'],*rest['source_contexts']]})==11
    dedup=preview['fact_deduplication'][0]
    assert dedup['record_ids']==['left','right'] and dedup['support_count']==2 and dedup['document_count']==2
