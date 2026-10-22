from datetime import datetime, timedelta, timezone

import pytest

from knowledge_service.repository import Repository


def test_graph_session_pages_are_complete_scoped_and_bound_to_session(tmp_path):
    from knowledge_service.services.graph_sessions import GraphSessions
    repo = Repository(tmp_path / 'graph-session')
    project = repo.create_project('图谱分页')['id']
    repo.put_batch(project, [
        {'id': f'e{i}', 'kind': 'entity', 'text': f'实体{i}', 'type': 'Thing', 'metadata': {}}
        for i in range(5)
    ] + [{'id': f'r{i}', 'kind': 'relation', 'text': '关联', 'type': 'related',
          'subject_id': f'e{i}', 'object_id': f'e{i+1}', 'metadata': {}} for i in range(4)])
    sessions = GraphSessions(repo)
    manifest = sessions.create(project, {}, actor_id='a')
    assert manifest['entity_count'] == 5
    assert manifest['relation_count'] == 4
    page = sessions.page(project, manifest['id'], 'entities', actor_id='a', limit=2)
    assert len(page['records']) == 2 and page['next_cursor']
    second = sessions.create(project, {}, actor_id='a')
    with pytest.raises(ValueError, match='游标'):
        sessions.page(project, second['id'], 'entities', cursor=page['next_cursor'], actor_id='a')
    with pytest.raises(PermissionError):
        sessions.page(project, manifest['id'], 'entities', actor_id='b')
    ids = [row['id'] for row in page['records']]
    while page['next_cursor']:
        page = sessions.page(project, manifest['id'], 'entities', cursor=page['next_cursor'], actor_id='a', limit=2)
        ids.extend(row['id'] for row in page['records'])
    assert ids == ['e0', 'e1', 'e2', 'e3', 'e4']
    assert page['complete'] is True
    assert all('embedding' not in row and 'metadata' not in row for row in page['records'])


def test_graph_session_rejects_changes_and_expired_sessions(tmp_path):
    from knowledge_service.services.graph_sessions import GraphSessions
    repo = Repository(tmp_path / 'graph-change')
    project = repo.create_project('图谱水位')['id']
    repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': '甲', 'type': 'Thing'})
    sessions = GraphSessions(repo)
    manifest = sessions.create(project, {}, actor_id='a')
    repo.put_record(project, {'id': 'b', 'kind': 'entity', 'text': '乙', 'type': 'Thing'})
    with pytest.raises(ValueError, match='变化'):
        sessions.page(project, manifest['id'], 'entities', actor_id='a')
    expired = sessions.create(project, {}, actor_id='a')
    row = repo.get_artifact('graph-session', expired['id'])
    row['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    repo.save_artifact('graph-session', row)
    with pytest.raises(ValueError, match='过期'):
        sessions.page(project, expired['id'], 'entities', actor_id='a')


def test_graph_session_never_includes_out_of_scope_endpoint(tmp_path):
    from knowledge_service.services.graph_sessions import GraphSessions
    repo = Repository(tmp_path / 'graph-scope')
    project = repo.create_project('图谱范围')['id']
    repo.put_batch(project, [
        {'id': 'a', 'kind': 'entity', 'text': '甲', 'metadata': {'region': '北京'}},
        {'id': 'b', 'kind': 'entity', 'text': '乙', 'metadata': {'region': '上海'}},
        {'id': 'r', 'kind': 'relation', 'text': '跨域', 'subject_id': 'a', 'object_id': 'b', 'metadata': {'region': '北京'}},
    ])
    manifest = GraphSessions(repo).create(project, {'filters': {'field': 'metadata.region', 'op': 'eq', 'value': '北京'}})
    assert manifest['entity_count'] == 1
    assert manifest['relation_count'] == 0


def test_record_page_searches_aliases_inside_current_scope(tmp_path):
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'alias-page')
    project = repo.create_project('实体定位')['id']
    repo.put_batch(project, [
        {'id':'a','kind':'entity','text':'甲','metadata':{'aliases':['Alpha'], 'region':'北京'}},
        {'id':'b','kind':'entity','text':'乙','metadata':{'aliases':['Alpha'], 'region':'上海'}},
        {'id':'c','kind':'entity','text':'丙','metadata':{'aliases':None, 'region':'北京'}},
    ])
    page = ScopedReads(repo).page(project,
        {'filters':{'field':'metadata.region','op':'eq','value':'北京'}},query='ALPHA',limit=1)
    assert page['total'] == 1 and [row['id'] for row in page['records']] == ['a']


def test_exact_page_record_ids_do_not_filter_relation_or_attribute_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    app = create_app(tmp_path / 'exact-record-page', encoder=HashingEncoder())
    repo = app.state.service.repository
    project = repo.create_project('精确知识定位')['id']
    metadata = {'region':'北京'}
    repo.put_batch(project,[
        {'id':'a','kind':'entity','text':'甲','metadata':metadata},
        {'id':'b','kind':'entity','text':'乙','metadata':metadata},
        {'id':'r','kind':'relation','text':'关联','subject_id':'a','object_id':'b','metadata':metadata},
        {'id':'p','kind':'attribute','text':'名称','subject_id':'a','type':'name','value':'甲',
         'datatype':'http://www.w3.org/2001/XMLSchema#string','metadata':metadata},
    ])
    client = TestClient(app)
    path = f'/api/projects/{project}/records/page'
    scope = {'filters':{'field':'metadata.region','op':'eq','value':'北京'}}
    response = client.post(path,json={**scope,'record_ids':['r','p']})
    assert response.status_code == 200, response.text
    assert {row['id'] for row in response.json()['records']} == {'r','p'}
    assert client.post(path,json={'record_ids':['r'], 'filters':{'field':'metadata.region','op':'eq','value':'上海'}}).json()['records'] == []


def test_generated_graph_read_model_keeps_visibility_history_and_json_null(tmp_path):
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'graph-read-model')
    project = repo.create_project('图谱派生读取')['id']
    repo.put_batch(project, [
        {'id': 'a', 'kind': 'entity', 'text': '甲' * 200, 'type': None},
        {'id': 'b', 'kind': 'entity', 'text': '乙'},
        {'id': 'r', 'kind': 'relation', 'text': '关联', 'subject_id': 'a', 'object_id': 'b'},
        *[{'id': f'false-{index}', 'kind': 'entity', 'text': '可见',
           'metadata': {'_deleted': value, '_audit': value}}
          for index, value in enumerate([None, False, 0, '', [], {}])],
        {'id': 'hidden', 'kind': 'entity', 'text': '隐藏', 'metadata': {'_audit': {'reason': 'audit'}}},
    ])
    reads = ScopedReads(repo)
    original = repo.get_record(project, 'a')
    compact = reads.page(project, {}, kinds=['entity'], compact_graph=True)
    assert len(compact['records']) == 8
    assert next(row for row in compact['records'] if row['id'] == 'a')['text'] == '甲' * 160
    # A text read column cannot replace JSON filters: missing and explicit null
    # both project to SQL NULL but have different public predicate semantics.
    explicit_null = reads.page(project, {'filters': {'field': 'type', 'op': 'eq', 'value': None}})
    assert [row['id'] for row in explicit_null['records']] == ['a']
    editable = {key:value for key,value in original.items()
                if key not in {'project_id','version','version_id','recorded_at','superseded_at'}}
    repo.put_record(project, {**editable, 'metadata': {'_deleted': True}}, expected_version=1)
    assert reads.count(project, {}, kinds=['entity']) == 7
    assert reads.count(project, {}, kinds=['relation']) == 0
    assert reads.count(project, {'_identity_read': True}, kinds=['relation']) == 1
    historical = reads.page(project, {'known_at': original['recorded_at']}, kinds=['entity'], compact_graph=True)
    assert next(row for row in historical['records'] if row['id'] == 'a')['version_id'] == original['version_id']


def test_graph_read_indexes_accept_existing_large_repository_ids(tmp_path):
    import hashlib
    from knowledge_service.repository.scoped_reads import ScopedReads
    repo = Repository(tmp_path / 'graph-large-identities')
    project = repo.create_project('长标识兼容')['id']
    # These valid repository IDs individually fit the existing primary key.
    # Including two endpoint IDs beside a relation ID in one btree tuple would
    # introduce a new index-size rejection even though every original key fits.
    def identifier(prefix):
        return ''.join(hashlib.sha256(f'{prefix}:{index}'.encode()).hexdigest()
                       for index in range(35))[:2200]
    subject, target = (identifier(prefix) for prefix in ('subject', 'target'))
    relation = 'relation-large-endpoints'
    repo.put_batch(project, [
        {'id': subject, 'kind': 'entity', 'text': '甲'},
        {'id': target, 'kind': 'entity', 'text': '乙'},
        {'id': relation, 'kind': 'relation', 'text': '关联', 'subject_id': subject, 'object_id': target},
    ])
    page = ScopedReads(repo).page(project, {}, kinds=['relation'], compact_graph=True)
    assert [(row['id'], row['subject_id'], row['object_id']) for row in page['records']] == [
        (relation, subject, target)]
