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
    batch._batch_group_errors=lambda project,groups:{}
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


def test_document_duplicate_scan_scopes_members_and_revalidates_support(tmp_path):
    repo, service, project, governance = document_supported_conflict(tmp_path)
    service.write(project, [
        dict(id='extra', kind='entity', type='Person', text='甲', source_id='doc-left'),
        *[dict(id=entity, kind='entity', type='Person', text=text, source_id=document)
          for entity, text, document in [('b1', '乙', 'doc-left'), ('b2', '乙', 'doc-left'),
                                         ('c1', '丙', 'doc-right'), ('c2', '丙', 'doc-right')]],
    ])
    version = repo.get_record(project, 'doc-left')['version_id']
    scope = dict(document_id='doc-left', document_version_id=version)
    with pytest.raises(ValueError, match='资料版本'):
        governance.scan_duplicates(project, document_id='doc-right', document_version_id=version,
                                   document_mode='within')
    with pytest.raises(ValueError):
        governance.scan_duplicates(project, document_id='doc-left')

    first = governance.scan_duplicates(project, **scope, document_mode='within', limit=1)
    second = governance.scan_duplicates(project, **scope, document_mode='within', cursor=first['next_cursor'])
    with pytest.raises(ValueError, match='游标'):
        governance.scan_duplicates(project, **scope, document_mode='related', cursor=first['next_cursor'])
    with pytest.raises(ValueError, match='游标'):
        governance.scan_duplicates(project, cursor=first['next_cursor'])
    legacy_cursor = governance.scan_duplicates(project, limit=1)['next_cursor']
    with pytest.raises(ValueError, match='游标'):
        governance.scan_duplicates(project, **scope, document_mode='within', cursor=legacy_cursor)

    within = {group['name']: group for group in first['groups'] + second['groups']}
    assert set(within) == {'甲', '乙'}
    assert {member['id'] for member in within['甲']['members']} == {'keep', 'extra'}
    related = {group['name']: group for group in governance.scan_duplicates(
        project, **scope, document_mode='related')['groups']}
    assert set(related) == {'甲', '乙'}
    assert {member['id'] for member in related['甲']['members']} == {'keep', 'extra', 'drop'}
    preview = governance.preview_duplicate_group(project, within['甲']['key'])
    assert set(preview['expected_versions']) == {'keep', 'extra'}
    support = repo.list_assertions(project, canonical_record_id='extra')[0]
    repo.transition_assertion(project, support['id'], support['decision_version'],
                              'superseded', '来源撤回', 'reviewer')
    after_withdrawal = governance.scan_duplicates(project, **scope, document_mode='within')['groups']
    assert {group['name'] for group in after_withdrawal} == {'乙'}
    still_related = governance.scan_duplicates(project, **scope, document_mode='related')['groups']
    assert {group['name'] for group in still_related} == {'甲', '乙'}
    result = governance.confirm_duplicate_groups(project, [
        dict(key=within['甲']['key'], preview_fingerprint=preview['preview_fingerprint']),
    ], actor='reviewer')['results'][0]
    assert result['status'] == 'failed'
    assert '版本冲突' in result['error']
    assert not repo.get_record(project, 'keep')['metadata'].get('_deleted')


def test_batch_preview_is_ordered_bounded_and_readable_by_viewer(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app

    app = create_app(tmp_path / 'duplicate-preview-viewer.sqlite', HashingEncoder(), auth_disabled=False)
    with TestClient(app) as client:
        admin = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).json()['access_token']
        assert client.post('/api/users', headers={'Authorization': 'Bearer ' + admin},
                           json={'username': 'duplicate-viewer', 'password': 'pass123', 'role': 'viewer'}).status_code == 201
        viewer = client.post('/api/auth/login', json={'username': 'duplicate-viewer', 'password': 'pass123'}).json()['access_token']
        headers = {'Authorization': 'Bearer ' + viewer}
        _, _, project, governance = setup_conflict(tmp_path, app.state.service)
        key = governance.scan_duplicates(project)['groups'][0]['key']
        other = app.state.service.repository.create_project('other')['id']
        endpoint = '/api/projects/' + project + '/duplicates/preview-batch'
        response = client.post(endpoint, json={'keys': ['bad', key]}, headers=headers)
        assert response.status_code == 200, response.text
        failed, ready = response.json()['results']
        assert failed['status'] == 'failed' and failed['key'] == 'bad' and failed['error']
        assert ready == {**governance.preview_duplicate_group(project, key), 'status': 'ready'}
        assert client.post(endpoint, json={'keys': []}, headers=headers).status_code == 422
        assert client.post(endpoint, json={'keys': [key] * 21}, headers=headers).status_code == 422
        foreign = client.post('/api/projects/' + other + '/duplicates/preview-batch',
                              json={'keys': [key]}, headers=headers).json()['results'][0]
        assert foreign['status'] == 'failed' and foreign['key'] == key


def test_review_reads_large_and_incompatible_duplicate_groups_without_a_merge_preview(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.api.security_middleware import requires_write_access

    app = create_app(tmp_path / 'duplicate-member-review.sqlite', HashingEncoder())
    with TestClient(app) as client:
        _, service, project, governance = setup_conflict(tmp_path, app.state.service)
        large = [dict(id=f'large-{i:02}', kind='entity', type='Person', text='大组') for i in range(24)]
        mixed = [dict(id=f'mixed-{i}', kind='entity', type='Person', text='冲突组', valid_from=start)
                 for i, start in enumerate(['2025-01-01', '2025-01-01', '2026-01-01'])]
        service.write(project, large + mixed)
        groups = {group['name']: group for group in governance.scan_duplicates(project)['groups']}
        oversized, incompatible = groups['大组'], groups['冲突组']
        assert oversized['blocked'] and oversized['member_count'] == 24

        root = f'/api/projects/{project}/duplicates'
        assert not requires_write_access('POST', root + '/members')
        result = client.post(root + '/preview-batch', json={'keys':[incompatible['key']]}).json()['results'][0]
        assert result['status'] == 'review_required' and '业务有效期' in result['error']
        single = client.post(root + '/preview', json={'key':incompatible['key']})
        assert single.status_code == 200 and single.json() == result

        for group, expected in [(oversized, large), (incompatible, mixed)]:
            reviewed = []
            after = None
            while True:
                response = client.post(root + '/members', json={'key':group['key'], 'limit':10, 'after_id':after})
                assert response.status_code == 200, response.text
                page = response.json()
                assert 0 < len(page['members']) <= 10
                assert all(row['version_id'] and 'source_contexts' in row for row in page['members'])
                reviewed.extend(row['id'] for row in page['members'])
                after = page['next_after_id']
                if after is None:
                    break
                assert after == reviewed[-1]
            assert reviewed == [row['id'] for row in expected]

        assert client.post(root + '/members', json={'key':oversized['key'], 'limit':21}).status_code == 422
        other = service.repository.create_project('other-project')['id']
        assert client.post(f'/api/projects/{other}/duplicates/members', json={'key':oversized['key']}).status_code == 422
        rows = client.post(root + '/members', json={'key':incompatible['key']}).json()['members']
        assert client.post(root + '/sources', json={'key':incompatible['key'], 'entity_id':rows[0]['id'],
            'expected_version_id':rows[0]['version_id']}).status_code == 200
        assert client.post(root + '/sources', json={'key':incompatible['key'], 'entity_id':'large-00',
            'expected_version_id':rows[0]['version_id']}).status_code == 404
        merge_preview = f'/api/projects/{project}/merge/preview'
        for drop, status in [('mixed-1', 200), ('mixed-2', 422)]:
            expected_versions = {row['id']:row['version'] for row in rows if row['id'] in ('mixed-0', drop)}
            response = client.post(merge_preview, json={'keep_id':'mixed-0', 'drop_id':drop,
                                   'expected_versions':expected_versions})
            assert response.status_code == status, response.text


def test_batch_confirm_blocks_every_overlapping_group_before_any_write(tmp_path):
    repo, service, project, governance = setup_conflict(tmp_path)
    governance.alias(project, 'keep', '乙', 1)
    service.write(project, [dict(id=entity, kind='entity', type='Person', text=name)
                            for entity, name in [('other', '乙'), ('c1', '丙'), ('c2', '丙')]])
    previews = [governance.preview_duplicate_group(project, group['key']) for group in
                governance.scan_duplicates(project)['groups']]
    independent = next(group for group in previews if set(group['expected_versions']) == {'c1', 'c2'})
    results = governance.confirm_duplicate_groups(project, [
        dict(key=group['key'], preview_fingerprint=group['preview_fingerprint']) for group in previews
    ], actor='reviewer')['results']
    for result in results:
        if result['key'] == independent['key']:
            assert result['status'] == 'merged'
        else:
            assert result['status'] == 'failed' and '重叠' in result['error']
    for entity in ('keep', 'drop', 'other'):
        assert not repo.get_record(project, entity)['metadata'].get('_deleted')


@pytest.mark.parametrize('artifact_id', ['original-artifact', None])
def test_document_duplicate_scope_matches_frozen_ingest_receipt(tmp_path, artifact_id):
    ontology = TTL + '\n:links a owl:ObjectProperty; rdfs:domain :Person; rdfs:range :Person .'
    repo, service, project, governance = document_supported_conflict(tmp_path, turtle=ontology)
    metadata = {'status': 'processing'}
    if artifact_id:
        metadata['source_document_version_id'] = artifact_id
    document = dict(id='ingested', kind='document', text='同一来源', metadata=metadata)
    receipt = repo.put_record(project, document, expected_version=0)
    run = repo.create_ingest_run(project, receipt['id'], receipt['version_id'])
    service.write(project, [
        dict(id='a', kind='entity', type='Person', text='乙', source_id=receipt['id']),
        dict(id='b', kind='entity', type='Person', text='乙', source_id=receipt['id']),
        dict(id='neighbor', kind='entity', type='Person', text='关系另一端'),
        dict(id='relation', kind='relation', type='links', subject_id='a', object_id='neighbor', text='关联'),
    ])
    ready = repo.put_record(project, {**document, 'metadata': {**metadata, 'status': 'ready', 'run_id': run['id']}},
                            expected_version=receipt['version'])
    unrelated_docs = service.write(project, [dict(id=f'n-{n}', kind='document', text='另一来源') for n in range(11)])
    service.write(project, [], operation='adopt_discovery', assertions=[
        dict(id=f'other-context-{n}', kind='entity', document_id=source['id'],
             document_version_id=source['version_id'], canonical_record_id='a', quote='另一来源',
             payload=dict(id='a', kind='entity', type='https://conflict.test/Person', text='乙'))
        for n, source in enumerate(unrelated_docs)
    ])
    governance.alias(project, 'a', '新增别名', repo.get_record(project, 'a')['version'])
    group, = governance.scan_duplicates(project, document_id=ready['id'], document_version_id=ready['version_id'],
                                         document_mode='within')['groups']
    preview = governance.preview_duplicate_group(project, group['key'])
    entity = next(member for member in preview['members'] if member['id'] == 'a')
    assert entity['document_count'] == 12
    source = entity['source_contexts'][0]
    assert source['document_version_id'] == receipt['version_id']
    assert source['source_document_version_id'] == artifact_id
    relation, = entity['relation_context']['examples']
    assert entity['relation_context']['total'] == 1
    assert relation['id'] == 'relation' and relation['object_label'] == '关系另一端'
    next_page = governance.duplicate_sources(project, group['key'], preview['preview_fingerprint'],
        entity['id'], entity['version_id'], cursor=entity['source_next_cursor'])
    contexts = entity['source_contexts'] + next_page['source_contexts']
    assert len({context['context_id'] for context in contexts}) == len(contexts) == entity['support_count']

    replacement = repo.put_record(project, {**document, 'metadata': {'source_document_version_id':'different-artifact'}},
                                  expected_version=ready['version'])
    assert governance.scan_duplicates(project, document_id=replacement['id'],
        document_version_id=replacement['version_id'], document_mode='within')['groups'] == []


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
