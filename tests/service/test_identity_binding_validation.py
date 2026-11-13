"""Accepted identity keys are transactionally validated and learned on every path."""
import copy

import pytest

from test_identity_source_contexts import entity, system
from knowledge_service.repository.connection import IntegrityError
from knowledge_service.services.formal_writes import FormalFactWriter
from knowledge_service.services.governance import Governance


def pending(repo, project, id, **kwargs):
    return repo.create_assertion(project, {'id': id, 'kind': 'entity',
        'payload': entity(id+'-mention', **kwargs), 'actor': 'reviewer'})


def accept(id, target='canonical'):
    return {'id': id, 'expected_version': 1, 'status': 'accepted',
            'canonical_record_id': target, 'reason': 'identity review', 'actor': 'reviewer'}


@pytest.mark.parametrize('invalid', [{'code':'B'}, {'kind':'Event'}, {'identity_scope':'other'}])
def test_bad_pending_entity_binding_rolls_back_before_acceptance(tmp_path, invalid):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical', code='A')])
    raw = entity('mention', code='A')
    if 'identity_scope' in invalid:
        raw['metadata']['identity_scope'] = invalid['identity_scope']
    else:
        raw = entity('mention', **invalid)
    repo.create_assertion(project, {'id':'bad', 'kind':'entity', 'payload':raw, 'actor':'reviewer'})
    before = repo.list_assertion_events(project)
    with pytest.raises(ValueError):
        FormalFactWriter(repo).apply('approve_review', project, [], {},
            {'records':[], 'assertion_decisions':[accept('bad')]})
    assert repo.get_assertion(project, 'bad')['status'] == 'pending'
    assert repo.get_record(project, 'canonical')['version'] == 1
    assert repo.list_assertion_events(project) == before


def test_cross_source_transition_is_gated_then_learns_key_and_exact_final_mapping(tmp_path, monkeypatch):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical', start='2020-01-01', end='2021-01-01')])
    source = pending(repo, project, 'source', code='A', start='2022-01-01', end='2023-01-01')
    policy = {'records':[], 'assertion_decisions':[accept(source['id'])]}
    with pytest.raises(ValueError, match='索引|投影'):
        FormalFactWriter(repo).apply('approve_review', project, [], {}, policy)
    assert repo.get_assertion(project, source['id'])['status'] == 'pending'
    monkeypatch.setattr(repo, 'source_identity_projection_readiness', lambda p:{'ready':True, 'reason':''})
    result = FormalFactWriter(repo).apply('approve_review', project, [], {}, policy)
    canonical = repo.get_record(project, 'canonical')
    assert canonical['metadata']['identity_keys'] == {'code':'A'}
    assert canonical['version'] == 2
    assert len(result['accepted_records']) == 1
    maps = repo.list_record_version_assertions(project, assertion_id=source['id'])
    assert maps[0]['record_version_id'] == canonical['version_id']
    assert repo.get_assertion(project, source['id'])['payload'] == source['payload']


def test_two_pending_conflicting_keys_are_atomically_rejected(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical')])
    pending(repo, project, 'a', code='A')
    pending(repo, project, 'b', code='B')
    before = repo.list_assertion_events(project)
    with pytest.raises(ValueError):
        FormalFactWriter(repo).apply('approve_review', project, [], {}, {
            'records':[], 'assertion_decisions':[accept('a'),accept('b')]})
    assert repo.list_assertion_events(project) == before
    assert {a['status'] for a in repo.list_assertions(project) if a['id'] in {'a','b'}} == {'pending'}
    assert repo.get_record(project, 'canonical')['metadata'].get('identity_keys') is None


def test_key_learning_replay_preserves_historical_mapping_and_is_idempotent(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical')])
    old = repo.get_record(project, 'canonical')
    source = pending(repo, project, 'legacy', code='A')
    repo.transition_assertion(project, source['id'], 1, 'accepted', 'old acceptance', 'reviewer', 'canonical')
    event = repo.list_assertion_events(project, source['id'])[-1]
    repo.add_record_version_assertion(project, 'canonical', old['version_id'], source['id'], event['id'])
    replay = {key:value for key,value in source.items() if key not in {
        'project_id','status','canonical_record_id','decision_reason','decision_version','created_at','decided_at'}}
    replay['canonical_record_id'] = 'canonical'
    result = FormalFactWriter(repo).apply('manual_write', project, [replay], {}, {'records':[]})
    learned = repo.get_record(project, 'canonical')
    assert learned['metadata'].get('identity_keys') == {'code':'A'}
    assert learned['version'] == 2
    assert len(result['accepted_records']) == 1
    assert repo.list_record_version_assertions(project, assertion_id=source['id'])[0]['record_version_id'] == old['version_id']
    before = copy.deepcopy(repo.list_assertion_events(project))
    FormalFactWriter(repo).apply('manual_write', project, [replay], {}, {'records':[]})
    assert repo.get_record(project, 'canonical')['version'] == 2
    assert repo.list_assertion_events(project) == before


def test_key_transfer_merge_and_reversal_preserve_unique_registry(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('keep'), entity('drop', code='A')])
    merged = Governance(service).merge(project, 'keep', 'drop', {'keep':1,'drop':1})
    assert repo.get_record(project, 'keep')['metadata']['identity_keys'] == {'code':'A'}
    rows = repo._db.execute('SELECT record_id FROM entity_identity_keys WHERE project_id=?', (project,)).fetchall()
    assert [row['record_id'] for row in rows] == ['keep']
    Governance(service).undo_merge(project, merged['operation_id'])
    assert not repo.get_record(project, 'keep')['metadata'].get('identity_keys')
    rows = repo._db.execute('SELECT record_id FROM entity_identity_keys WHERE project_id=?', (project,)).fetchall()
    assert [row['record_id'] for row in rows] == ['drop']


def test_unique_key_release_never_releases_unchanged_owner_and_rolls_back(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('owner', code='A')])
    before = repo.list_assertion_events(project)
    with pytest.raises(IntegrityError):
        service.write(project, [entity('new', code='A')])
    assert repo.list_assertion_events(project) == before
    assert [row['id'] for row in repo.current_records(project) if row['kind']=='entity'] == ['owner']
    assert repo._db.execute('SELECT record_id FROM entity_identity_keys WHERE project_id=?', (project,)).fetchone()[0] == 'owner'


def test_two_final_survivors_cannot_share_key_after_release(tmp_path):
    repo, service, project = system(tmp_path)
    with pytest.raises(IntegrityError):
        service.write(project, [entity('a', code='A'),entity('b', code='A')])
    assert repo.current_records(project) == []
    assert repo.list_assertion_events(project) == []
    assert repo._db.execute('SELECT count(*) FROM entity_identity_keys WHERE project_id=?', (project,)).fetchone()[0] == 0


def test_same_batch_duplicate_acceptance_shares_new_final_mapping(tmp_path):
    repo, service, project = system(tmp_path)
    service.write(project, [entity('canonical')])
    source = {'id':'duplicated', 'kind':'entity', 'payload':entity('mention', code='A'),
              'canonical_record_id':'canonical', 'actor':'reviewer'}
    result = FormalFactWriter(repo).apply('manual_write', project,
        [source,copy.deepcopy(source)], {}, {'records':[]})
    canonical = repo.get_record(project, 'canonical')
    assert canonical['version'] == 2
    assert len(result['accepted_records']) == 1
    assert [row['id'] for row in result['assertion_updates']] == ['duplicated','duplicated']
    mappings = repo.list_record_version_assertions(project, assertion_id='duplicated')
    assert len(mappings) == 1 and mappings[0]['record_version_id'] == canonical['version_id']


def test_legacy_source_release_survives_audited_remap_move_and_unrelated_write(tmp_path):
    from ontology_seed import seed_ontology
    from knowledge_service.services.ontology import Ontology
    from knowledge_service.services.reclassify import Reclassify
    from test_identity_source_contexts import TTL

    repo, service, project = system(tmp_path)
    old_turtle=TTL+'\n<http://ex/Event> <http://purl.org/dc/terms/isReplacedBy> <http://ex/Platform> .'
    old=seed_ontology(repo,project,old_turtle,Ontology(old_turtle).summary())
    service.write(project,[{**entity('drop',kind='Event'),'ontology_id':old['id']}])
    source=pending(repo,project,'legacy',kind='Event')
    original_payload=copy.deepcopy(source['payload'])
    FormalFactWriter(repo).apply('approve_review',project,[],{},
        {'records':[],'assertion_decisions':[accept(source['id'],'drop')]})
    original_map=repo.list_record_version_assertions(project,assertion_id=source['id'])[0]

    new_turtle=TTL.replace('ex:Event a owl:Class; kg:identityPolicy "scoped" .','')
    new=seed_ontology(repo,project,new_turtle,Ontology(new_turtle).summary())
    reclassify=Reclassify(service)
    group=next(group for group in reclassify.plan(project)['groups']
               if group['from_type']=='http://ex/Event')
    remap=reclassify.apply(project,[group['key']],'reviewer')
    assert repo.get_record(project,'audit:'+remap['operation_id'])['metadata']['operation']=='reclassify'
    assert repo.get_record(project,'drop')['type']=='http://ex/Platform'
    service.write(project,[{**entity('keep'),'ontology_id':new['id']}])
    Governance(service).merge(project,'keep','drop',
        {'keep':repo.get_record(project,'keep')['version'],'drop':repo.get_record(project,'drop')['version']})
    moved=repo.get_assertion(project,source['id'])
    assert moved['canonical_record_id']=='keep' and moved['status']=='accepted'
    mappings=repo.list_record_version_assertions(project,assertion_id=source['id'])
    assert original_map in mappings and len(mappings)==2
    assert {repo.get_record_version(project,item['record_version_id'])['ontology_id']
            for item in mappings}=={old['id'],new['id']}

    service.write(project,[{**entity('unrelated'),'ontology_id':new['id']}])

    assert repo.get_record(project,'unrelated')['version']==1
    assert repo.get_assertion(project,source['id'])['payload']==original_payload
    assert original_payload['type']=='Event' and 'ontology_id' not in original_payload


def test_audited_remap_undo_and_different_remap_preserve_source_and_history(tmp_path):
    from ontology_seed import seed_ontology
    from knowledge_service.services.ontology import Ontology
    from knowledge_service.services.reclassify import Reclassify
    from test_identity_source_contexts import TTL

    repo, service, project=system(tmp_path)
    old=repo.get_ontology(project)
    service.write(project,[{**entity('canonical',kind='Event'),'ontology_id':old['id']}])
    source=pending(repo,project,'legacy',kind='Event')
    original_payload=copy.deepcopy(source['payload'])
    FormalFactWriter(repo).apply('approve_review',project,[],{},
        {'records':[],'assertion_decisions':[accept(source['id'])]})
    original_mappings=repo.list_record_version_assertions(project,assertion_id=source['id'])

    first_turtle=TTL+'\n<http://ex/Event> <http://www.w3.org/2002/07/owl#deprecated> true; '+\
        '<http://purl.org/dc/terms/isReplacedBy> <http://ex/Platform> .'
    seed_ontology(repo,project,first_turtle,Ontology(first_turtle).summary())
    reclassify=Reclassify(service)
    first_group=next(group for group in reclassify.plan(project)['groups']
                     if group['from_type']=='http://ex/Event')
    first=reclassify.apply(project,[first_group['key']],'reviewer')
    assert repo.get_record(project,'canonical')['type']=='http://ex/Platform'
    undo=Governance(service).undo_merge(project,first['operation_id'])
    assert repo.get_record(project,'canonical')['type']=='http://ex/Event'

    alternate_turtle=TTL+'\n<http://ex/Organization> a <http://www.w3.org/2002/07/owl#Class>; '+\
        '<https://knowledge.local/schema/identityPolicy> "reference" .\n'+\
        '<http://ex/Event> <http://www.w3.org/2002/07/owl#deprecated> true; '+\
        '<http://purl.org/dc/terms/isReplacedBy> <http://ex/Organization> .'
    alternate=seed_ontology(repo,project,alternate_turtle,Ontology(alternate_turtle).summary())
    alternate_group=next(group for group in reclassify.plan(project)['groups']
                         if group['from_type']=='http://ex/Event')
    second=reclassify.apply(project,[alternate_group['key']],'reviewer')
    service.write(project,[{**entity('unrelated'),'ontology_id':alternate['id']}])

    assert [row['type'] for row in repo.history(project,'canonical')]==[
        'http://ex/Event','http://ex/Platform','http://ex/Event','http://ex/Organization']
    ledgers={row['id']:row for row in repo.list_merge_operations(project)}
    assert ledgers[undo['operation_id']]['reversal_of']==first['operation_id']
    assert ledgers[first['operation_id']]['operation']=='ontology_remap'
    assert ledgers[second['operation_id']]['operation']=='ontology_remap'
    assert repo.get_record(project,'unrelated')['version']==1
    assert repo.get_assertion(project,source['id'])['payload']==original_payload
    assert repo.list_record_version_assertions(project,assertion_id=source['id'])==original_mappings
