import pytest

from ontology_seed import seed_ontology
from test_attribute_conflict_sets import TTL
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.repository.attribute_conflicts import ConflictReads
from knowledge_service.repository.duplicate_entities import DuplicateReads
from knowledge_service.services.governance import Governance
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.service import KnowledgeService


RELATION = '\n:knows a owl:ObjectProperty ; rdfs:domain :Person ; rdfs:range :Person .'


def setup_group(tmp_path, members, facts=(), *, required=False, service=None):
    service = service or KnowledgeService(Repository(tmp_path / 'partitions'), HashingEncoder())
    repo = service.repository
    project = repo.create_project('partition review')['id']
    turtle = TTL + RELATION
    if required:
        turtle = turtle.replace('sh:maxCount 1', 'sh:minCount 1 ; sh:maxCount 1')
    seed_ontology(repo, project, turtle, Ontology(turtle).summary())
    service.write(project, [*(dict(kind='entity', type='Person', text='同名', **member) for member in members), *facts])
    gov = Governance(service)
    group = gov.scan_duplicates(project)['groups'][0]
    return repo, service, project, gov, group['key']


def relation(id, subject, object):
    return dict(id=id, kind='relation', type='knows', text=id, subject_id=subject, object_id=object)


def test_deferred_duplicate_groups_are_private_recoverable_and_tied_to_members(tmp_path):
    repo, service, project, gov, key = setup_group(tmp_path, [dict(id='a'), dict(id='b')])
    candidate = gov.scan_duplicates(project, reviewer='alice')['groups'][0]
    saved = gov.defer_duplicate_group(project, key, candidate['membership'], reviewer='alice')
    assert gov.defer_duplicate_group(project, key, candidate['membership'], reviewer='alice') == saved
    reopened = Governance(KnowledgeService(Repository(tmp_path / 'partitions'), HashingEncoder()))
    assert reopened.scan_duplicates(project, reviewer='alice')['groups'] == []
    assert reopened.scan_duplicates(project, reviewer='bob')['groups'][0]['key'] == key
    service.write(project, [dict(id=id, kind='entity', type='Person', text='另一组') for id in ('x', 'y')])
    page = reopened.scan_duplicates(project, reviewer='alice', limit=1)
    assert [group['name'] for group in page['groups']] == ['另一组'] and page['next_cursor'] is None
    assert reopened.deferred_duplicate_groups(project, reviewer='alice')['groups'] == [saved]
    assert reopened.deferred_duplicate_groups(project, reviewer='bob')['groups'] == []
    assert not reopened.restore_duplicate_group(project, saved['id'], reviewer='bob')['restored']

    restored = reopened.restore_duplicate_group(project, saved['id'], reviewer='alice')
    assert restored['restored'] and restored['group']['key'] == key
    assert 'deferred_id' not in restored['group']
    assert reopened.deferred_duplicate_groups(project, reviewer='alice')['groups'] == []
    assert not reopened.restore_duplicate_group(project, saved['id'], reviewer='alice')['restored']
    assert all(len(repo.history(project, id)) == 1 for id in ('a', 'b'))

    reopened.defer_duplicate_group(project, key, candidate['membership'], reviewer='alice')
    service.write(project, [dict(id='c', kind='entity', type='Person', text='同名')])
    changed = next(group for group in reopened.scan_duplicates(project, reviewer='alice')['groups'] if group['key'] == key)
    assert changed['member_count'] == 3
    with pytest.raises(ValueError, match='成员已变化'):
        reopened.defer_duplicate_group(project, key, candidate['membership'], reviewer='alice')


def test_duplicate_deferral_routes_use_authenticated_owner_without_granting_merge(tmp_path):
    from test_auth_rbac import _client, _h, _login, _make_user, _super

    with _client(tmp_path) as client:
        _, _, project, _, key = setup_group(tmp_path, [dict(id='a'), dict(id='b')], service=client.app.state.service)
        path = f'/api/projects/{project}/duplicates'
        admin = _super(client)
        assert _make_user(client, admin, 'reviewer').status_code == 201
        viewer = _h(_login(client, 'reviewer', 'pass123').json()['access_token'])
        assert client.get(path + '/deferred').status_code == 401
        candidate = client.post(path + '/scan', json={}, headers=viewer).json()['groups'][0]
        body = {'key': key, 'membership': candidate['membership']}
        response = client.post(path + '/deferred/defer', json=body, headers=viewer)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert client.post(path + '/scan', json={}, headers=viewer).json()['groups'] == []
        assert client.get(path + '/deferred', headers=viewer).json()['groups'] == [saved]
        assert client.get(path + '/deferred', headers=_h(admin)).json()['groups'] == []
        assert not client.post(path + '/deferred/restore', json={'id': saved['id']}, headers=_h(admin)).json()['restored']
        assert client.post(path + '/confirm', json={'groups': []}, headers=viewer).status_code == 403
        result = client.post(path + '/deferred/restore', json={'id': saved['id']}, headers=viewer)
        assert result.status_code == 200 and result.json()['group']['key'] == key


def test_seven_to_two_is_one_commit_with_cross_target_facts_evidence_and_two_conflict_sets(tmp_path):
    members = [dict(id=letter) for letter in 'abcdefg']
    facts = [dict(id='count-' + letter, kind='attribute', type='count', text=str(n), subject_id=letter, value=n,
                  datatype='http://www.w3.org/2001/XMLSchema#integer') for n, letter in enumerate('abcdefg')]
    facts.extend([relation('edge-b', 'b', 'f'), relation('edge-a', 'a', 'e'), relation('edge-return', 'g', 'b')])
    repo, service, p, gov, key = setup_group(tmp_path, members, facts, required=True)
    assignments = {**dict.fromkeys('abcd', 'd'), **dict.fromkeys('efg', 'g')}
    versions = dict.fromkeys(assignments, 1)
    body = dict(key=key, assignments=assignments, expected_versions=versions)
    original_evidence = {a['id'] for a in repo.list_assertions(p, canonical_record_id='b', status='accepted')}
    preview = gov.preview_duplicate_partition(p, **body)
    assert [(t['keep_id'], t['drop_ids']) for t in preview['targets']] == [('d', list('abc')), ('g', list('ef'))]
    assert [t['affected_records']['attributes'] for t in preview['targets']] == [4, 3]
    assert len(preview['targets'][0]['attribute_conflicts']) == len(preview['targets'][1]['attribute_conflicts']) == 1
    assert all(len(repo.history(p, letter)) == 1 for letter in assignments)
    body['preview_fingerprint'] = preview['preview_fingerprint']
    result = gov.confirm_duplicate_partition(p, **body, actor='reviewer')
    assert [result[k] for k in ('status', 'assigned_count', 'kept_count', 'merged_count')] == ['completed', 7, 2, 5]
    assert len(repo.list_merge_operations(p)) == 1
    assert {r['id'] for r in service.scoped(p, {'kinds': ['entity']})} == {'d', 'g'}
    assert {r['id']: (r['subject_id'], r['object_id']) for r in service.scoped(p, {'kinds': ['relation']})} == {
        'edge-a': ('d','g'), 'edge-return': ('g','d')}
    assert {a['id'] for a in repo.list_assertions(p, canonical_record_id='d', status='accepted')} >= original_evidence
    assert repo.history(p, 'edge-b')[-1]['metadata']['merged_into_fact'] == 'edge-a'
    assert {a['canonical_record_id'] for a in repo.list_assertions(p) if a['id'] in original_evidence} == {'d'}
    for letter in assignments:
        attr_id = 'count-' + letter
        assert repo.history(p, attr_id)[-1]['metadata']['_quarantined']
        assert repo.history(p, attr_id)[-1]['subject_id'] == assignments[letter]
        assert {a['status'] for a in repo.list_assertions(p, canonical_record_id=attr_id)} == {'contradicting'}
    for id in ('d', 'g'):
        assert len(ConflictReads(repo).list_for_entity(p, id, {})['conflict_sets']) == 1
    reopened = Governance(KnowledgeService(Repository(tmp_path / 'partitions'), HashingEncoder()))
    assert reopened.confirm_duplicate_partition(p, **body, actor='another reviewer') == result
    receipt = reopened.scan_duplicates(p)['groups'][0]['review_receipt']
    assert receipt == {k: v for k, v in result.items() if k != 'records'}
    assert all(row['id'] and row['version'] == 2 for row in result['records'])
    with pytest.raises(ValueError, match='另一份分配'):
        reopened.confirm_duplicate_partition(p, **{**body, 'assignments': dict.fromkeys(assignments, 'd')}, actor='reviewer')
    reopened.undo_merge(p, result['operation_id'])
    after_undo = reopened.scan_duplicates(p)['groups'][0]
    assert len(after_undo['members']) == 7 and after_undo['review_receipt'] is None


def test_keep_all_persists_without_revisions_and_reopens_when_evidence_changes(tmp_path):
    members = [dict(id='a'), dict(id='b', valid_from='2026-01-01T00:00:00Z'), dict(id='c')]
    repo, service, p, gov, key = setup_group(tmp_path, members)
    versions = dict.fromkeys('abc', 1)
    with pytest.raises(ValueError, match=r'b.*a.*业务有效期'):
        gov.preview_duplicate_partition(p, key, dict.fromkeys('abc', 'a'), versions)
    # An incompatible entity can remain independent while others are merged.
    preview = gov.preview_duplicate_partition(p, key, {'a':'c','b':'b','c':'c'}, versions)
    assert [t['drop_ids'] for t in preview['targets']] == [[], ['a']]
    assignments = {id:id for id in 'abc'}
    fp = gov.preview_duplicate_partition(p, key, assignments, versions)['preview_fingerprint']
    result = gov.confirm_duplicate_partition(p, key, assignments, versions, fp, actor='reviewer')
    assert result['assigned_count'] == 3 and result['merged_count'] == 0 and result['records'] == []
    assert repo.list_merge_operations(p) == []
    reopened = Governance(KnowledgeService(Repository(tmp_path / 'partitions'), HashingEncoder()))
    assert reopened.scan_duplicates(p)['groups'][0]['review_receipt']['actor'] == 'reviewer'
    assert reopened.confirm_duplicate_partition(p, key, assignments, versions, fp, actor='reviewer') == result
    assert all(len(repo.history(p, id)) == 1 for id in assignments)
    service.write(p, [relation('additional', 'a', 'c')])
    assert reopened.scan_duplicates(p)['groups'][0]['review_receipt'] is None
    new_fp = reopened.preview_duplicate_partition(p, key, assignments, versions)['preview_fingerprint']
    assert new_fp != fp
    reopened.confirm_duplicate_partition(p, key, assignments, versions, new_fp, actor='reviewer')
    evidence = repo.list_assertions(p, canonical_record_id='additional', status='accepted')[0]
    repo.transition_assertion(p, evidence['id'], evidence['decision_version'], 'superseded', '资料支持已撤回', 'reviewer')
    assert reopened.scan_duplicates(p)['groups'][0]['review_receipt'] is None


def test_keep_all_prevents_stale_batch_and_partition_writes_from_other_clients(tmp_path):
    _, _, p, gov, key = setup_group(tmp_path, [dict(id=letter) for letter in 'abc'])
    versions = dict.fromkeys('abc', 1)
    separate = {id:id for id in 'abc'}
    together = dict.fromkeys('abc', 'b')
    old_group = gov.preview_duplicate_group(p, key)['preview_fingerprint']
    old_partition = gov.preview_duplicate_partition(p, key, together, versions)['preview_fingerprint']
    keep = gov.preview_duplicate_partition(p, key, separate, versions)['preview_fingerprint']
    gov.confirm_duplicate_partition(p, key, separate, versions, keep, actor='reviewer')
    other = Governance(KnowledgeService(Repository(tmp_path / 'partitions'), HashingEncoder()))
    result = other.confirm_duplicate_groups(p, [dict(key=key, preview_fingerprint=old_group)], actor='other')
    assert result['results'][0]['status'] == 'failed' and '独立' in result['results'][0]['error']
    assert other.preview_duplicate_groups(p, [key])['results'][0]['status'] == 'review_required'
    with pytest.raises(ValueError, match='版本冲突'):
        other.confirm_duplicate_partition(p, key, together, versions, old_partition, actor='other')
    assert other.repo.list_merge_operations(p) == []


def test_invalid_partitions_stale_dependencies_and_failed_receipt_leave_no_partial_merge(tmp_path, monkeypatch):
    repo, service, p, gov, key = setup_group(tmp_path, [dict(id=id) for id in 'abcd'])
    versions = dict.fromkeys('abcd', 1)
    assignments = {'a':'b', 'b':'b', 'c':'d', 'd':'d'}
    for invalid, message in [
            ({'a':'b','b':'b'}, '未分配成员'), ({**assignments, 'extra':'b'}, '不属于本组'),
            ({**assignments,'c':'foreign'}, '不属于当前重复组'), ({**assignments, 'd':'a'}, '其他实体')]:
        with pytest.raises(ValueError, match=message):
            gov.preview_duplicate_partition(p, key, invalid, versions)
    with pytest.raises(ValueError, match='版本冲突'):
        gov.preview_duplicate_partition(p, key, assignments, {**versions,'a':2})
    fp = gov.preview_duplicate_partition(p, key, assignments, versions)['preview_fingerprint']
    service.write(p, [relation('new-edge','a','c')])
    with pytest.raises(ValueError, match='版本冲突'):
        gov.confirm_duplicate_partition(p, key, assignments, versions, fp, actor='reviewer')
    fp = gov.preview_duplicate_partition(p, key, assignments, versions)['preview_fingerprint']
    def fail_after_facts(*args):
        raise RuntimeError('receipt write failed')
    monkeypatch.setattr(DuplicateReads, 'save_review', fail_after_facts)
    with pytest.raises(RuntimeError, match='receipt write failed'):
        gov.confirm_duplicate_partition(p, key, assignments, versions, fp, actor='reviewer')
    assert len(repo.history(p, 'new-edge')) == 1
    assert all(len(repo.history(p, id)) == 1 for id in assignments)
    assert not repo.list_merge_operations(p)
    assert gov.scan_duplicates(p)['groups'][0]['review_receipt'] is None


def test_partition_http_permissions_preview_members_and_confirm(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    app = create_app(tmp_path/'partition-http', HashingEncoder(), auth_disabled=False)
    with TestClient(app) as client:
        token = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()['access_token']
        admin = {'Authorization':'Bearer ' + token}
        client.post('/api/users', headers=admin, json={'username':'viewer','password':'pass123','role':'viewer'}).raise_for_status()
        token = client.post('/api/auth/login', json={'username':'viewer','password':'pass123'}).json()['access_token']
        viewer = {'Authorization':'Bearer ' + token}
        _, _, p, _, key = setup_group(tmp_path, [dict(id=id) for id in 'ab'], [relation('edge','a','b')],
                                     service=app.state.service)
        base = '/api/projects/' + p + '/duplicates/'
        members = client.post(base + 'members', json={'key':key}, headers=viewer).json()['members']
        assert all(m['relation_context']['total'] == 1 for m in members)
        assert members[0]['relation_context']['examples'][0]['object_label'] == '同名'
        body = dict(key=key, assignments={'a':'b','b':'b'}, expected_versions={'a':1,'b':1})
        response = client.post(base + 'partition/preview', json=body, headers=viewer)
        assert response.status_code == 200, response.text
        body['preview_fingerprint'] = response.json()['preview_fingerprint']
        assert client.post(base + 'partition/confirm', json=body, headers=viewer).status_code == 403
        response = client.post(base + 'partition/confirm', json=body, headers=admin)
        assert response.status_code == 200, response.text
        assert response.json()['merged_count'] == 1


def test_concurrent_identical_confirmation_returns_one_durable_result(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    repo, _, p, gov, key = setup_group(tmp_path, [dict(id=id) for id in 'abc'])
    other = Governance(KnowledgeService(Repository(tmp_path / 'partitions'), HashingEncoder()))
    versions = dict.fromkeys('abc', 1)
    assignments = {'a':'a','b':'a','c':'c'}
    fp = gov.preview_duplicate_partition(p, key, assignments, versions)['preview_fingerprint']
    ready = Barrier(2)
    def confirm(client):
        ready.wait(timeout=15)
        return client.confirm_duplicate_partition(p, key, assignments, versions, fp, actor='reviewer')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, retry = pool.map(confirm, (gov, other))
    assert first == retry
    assert len(repo.list_merge_operations(p)) == 1
