import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository


# ── Repository-level tests ──────────────────────────────────────────


def test_rename_project_changes_name_and_persists(tmp_path):
    repo = Repository(tmp_path / 'db')
    p = repo.create_project('旧名')
    pid = p['id']
    renamed = repo.rename_project(pid, '新名')
    assert renamed['name'] == '新名'
    assert repo.get_project(pid)['name'] == '新名'


def test_rename_project_strips_whitespace(tmp_path):
    repo = Repository(tmp_path / 'db')
    pid = repo.create_project('a')['id']
    renamed = repo.rename_project(pid, '  padded  ')
    assert renamed['name'] == 'padded'


def test_rename_project_empty_name_raises(tmp_path):
    repo = Repository(tmp_path / 'db')
    pid = repo.create_project('a')['id']
    with pytest.raises(ValueError):
        repo.rename_project(pid, '')
    with pytest.raises(ValueError):
        repo.rename_project(pid, '   ')


def test_rename_project_unknown_id_raises(tmp_path):
    repo = Repository(tmp_path / 'db')
    with pytest.raises(KeyError):
        repo.rename_project('nonexistent', 'new')


def test_delete_project_removes_all_children(tmp_path):
    repo = Repository(tmp_path / 'db')
    pid = repo.create_project('to-delete')['id']
    # add records
    repo.put_record(pid, {'id': 'r1', 'kind': 'document', 'text': 'doc1'})
    repo.put_record(pid, {'id': 'r2', 'kind': 'entity', 'text': 'ent1'})
    # add ontology
    repo.save_ontology(pid, 'turtle', {'classes': ['A']})
    # add artifact
    repo.save_artifact('summary', {'id': 'art1', 'project_id': pid, 'data': 1})
    result = repo.delete_project(pid)
    assert result['records'] == 2
    assert result['ontologies'] == 1
    assert result['artifacts'] == 1
    with pytest.raises(KeyError):
        repo.get_project(pid)


def test_delete_project_explicitly_counts_ontology_governance_history(tmp_path):
    repo = Repository(tmp_path / 'db')
    pid = repo.create_project('governed-delete')['id']
    draft = repo._ontology_drafts.create(pid, {
        'id': 'draft-delete', 'project_id': pid, 'base_ontology_id': None,
        'source_kind': 'manual', 'status': 'editing', 'revision': 1,
        'title': 'Delete me', 'summary': 'Delete the whole project',
        'source_context': {},
    })
    repo._ontology_drafts.append_operations(pid, draft['id'], [{
        'id': 'op-delete', 'action': 'create_term', 'target_iri': 'urn:delete',
        'before': None, 'after': {'kind': 'class'}, 'evidence': [], 'impact': {},
        'validation': {}, 'risk': 'low', 'fingerprint': 'sha256:delete',
        'reason': 'test',
    }])
    repo._ontology_drafts.append_decisions(pid, draft['id'], [{
        'id': 'decision-delete', 'operation_id': 'op-delete',
        'operation_fingerprint': 'sha256:delete', 'action': 'approve',
        'reason': 'reviewed', 'actor': 'reviewer',
    }])
    with repo._transaction():
        repo._db.execute(
            '''INSERT INTO ontology_publish_requests
               (id,project_id,draft_id,idempotency_key,request_hash,result_ontology_id,
                created_at,completed_at) VALUES (?,?,?,?,?,?,?,?)''',
            ('publish-delete', pid, draft['id'], 'key', 'sha256:request', None,
             '2026-01-01T00:00:00.000000Z', None))

    deleted = repo.delete_project(pid)

    assert deleted['ontology_drafts'] == 1
    assert deleted['ontology_operations'] == 1
    assert deleted['ontology_review_decisions'] == 1
    assert deleted['ontology_publish_requests'] == 1
    for table in ('ontology_drafts', 'ontology_operations',
                  'ontology_review_decisions', 'ontology_publish_requests'):
        assert repo._db.execute(
            f'SELECT COUNT(*) FROM {table} WHERE project_id=?', (pid,)
        ).fetchone()[0] == 0


def test_delete_project_cleans_fts_index(tmp_path):
    """record_fts 是 FTS5 虚拟表，无外键不参与级联，删除项目必须显式清掉全文索引。"""
    repo = Repository(tmp_path / 'db')
    pid = repo.create_project('to-delete-fts')['id']
    repo.put_record(pid, {'id': 'e1', 'kind': 'entity', 'text': '美团商户规则'})
    repo.put_record(pid, {'id': 'c1', 'kind': 'chunk', 'text': '退款需要原始凭证'})
    with repo._lock:
        before = repo._db.execute('SELECT COUNT(*) FROM record_fts WHERE project_id=?', (pid,)).fetchone()[0]
    assert before == 2  # 删除前该项目的全文索引存在
    repo.delete_project(pid)
    with repo._lock:
        rows = repo._db.execute('SELECT COUNT(*) FROM record_fts WHERE project_id=?', (pid,)).fetchone()[0]
    assert rows == 0  # 删除后该项目的全文索引必须清零
    with pytest.raises(KeyError):
        repo.keyword_candidates(pid, '美团')  # 项目已删，入口先按 project_id 校验并抛错


def test_delete_project_leaves_other_projects_untouched(tmp_path):
    repo = Repository(tmp_path / 'db')
    p1 = repo.create_project('keep')['id']
    p2 = repo.create_project('delete-me')['id']
    repo.put_record(p1, {'id': 'a', 'kind': 'entity', 'text': 'kept'})
    repo.put_record(p2, {'id': 'b', 'kind': 'entity', 'text': 'gone'})
    repo.save_artifact('summary', {'id': 'global-art', 'data': 1})  # NULL project_id
    repo.delete_project(p2)
    assert repo.get_project(p1)['name'] == 'keep'
    assert len(repo.current_records(p1)) == 1
    # global artifact untouched
    artifacts = repo.list_artifacts('summary')
    assert len(artifacts) == 1 and artifacts[0]['id'] == 'global-art'


def test_delete_project_unknown_id_raises(tmp_path):
    repo = Repository(tmp_path / 'db')
    with pytest.raises(KeyError):
        repo.delete_project('nonexistent')


def test_list_projects_includes_counts(tmp_path):
    repo = Repository(tmp_path / 'db')
    p1 = repo.create_project('alpha')['id']
    p2 = repo.create_project('beta')['id']
    # p1: 2 documents, 1 entity, 1 formal attribute
    repo.put_record(p1, {'id': 'd1', 'kind': 'document', 'text': 'doc1'})
    repo.put_record(p1, {'id': 'd2', 'kind': 'document', 'text': 'doc2'})
    repo.put_record(p1, {'id': 'e1', 'kind': 'entity', 'text': 'ent1'})
    repo.put_record(p1, {'id': 'a1', 'kind': 'attribute', 'type': 'age',
                         'text': 'age 20', 'subject_id': 'e1', 'value': 20,
                         'datatype': 'http://www.w3.org/2001/XMLSchema#integer'})
    # p2: 1 relation
    repo.put_record(p2, {'id': 'r1', 'kind': 'relation', 'text': 'rel1', 'subject_id': 'e1', 'object_id': 'e1'})
    projects = repo.list_projects()
    by_id = {p['id']: p for p in projects}
    assert by_id[p1]['counts'] == {
        'documents': 2, 'entities': 1, 'relations': 0, 'attributes': 1, 'chunks': 0}
    assert by_id[p2]['counts'] == {
        'documents': 0, 'entities': 0, 'relations': 1, 'attributes': 0, 'chunks': 0}


def test_list_projects_zero_counts_for_empty_project(tmp_path):
    repo = Repository(tmp_path / 'db')
    pid = repo.create_project('empty')['id']
    projects = repo.list_projects()
    assert projects[0]['counts'] == {
        'documents': 0, 'entities': 0, 'relations': 0, 'attributes': 0, 'chunks': 0}


def test_list_projects_counts_exclude_superseded(tmp_path):
    repo = Repository(tmp_path / 'db')
    pid = repo.create_project('p')['id']
    repo.put_record(pid, {'id': 'a', 'kind': 'entity', 'text': 'v1'})
    repo.put_record(pid, {'id': 'a', 'kind': 'entity', 'text': 'v2'}, expected_version=1)
    counts = repo.list_projects()[0]['counts']
    assert counts['entities'] == 1  # only current version counts


# ── API-level tests ─────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'service.sqlite', encoder=HashingEncoder())) as c:
        yield c


def _create_project(client, name='test-project'):
    r = client.post('/api/projects', json={'name': name})
    assert r.status_code == 201, r.text
    return r.json()['id']


def test_api_rename_project(client):
    pid = _create_project(client, 'old')
    r = client.put(f'/api/projects/{pid}', json={'name': 'new'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['name'] == 'new'
    assert body['id'] == pid
    # verify via GET
    g = client.get(f'/api/projects/{pid}')
    assert g.json()['name'] == 'new'


def test_api_rename_project_empty_name_returns_422(client):
    pid = _create_project(client)
    r = client.put(f'/api/projects/{pid}', json={'name': ''})
    assert r.status_code == 422


def test_api_rename_project_not_found_returns_404(client):
    r = client.put('/api/projects/nonexistent', json={'name': 'x'})
    assert r.status_code == 404


def test_api_delete_project(client):
    pid = _create_project(client)
    # add a record
    r = client.post(f'/api/projects/{pid}/records', json={
        'records': [{'id': 'r1', 'kind': 'document', 'text': 'hello'}]
    })
    assert r.status_code == 201, r.text
    r = client.delete(f'/api/projects/{pid}')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['deleted']['records'] >= 1
    # subsequent GET returns 404
    g = client.get(f'/api/projects/{pid}')
    assert g.status_code == 404


def test_api_delete_project_not_found_returns_404(client):
    r = client.delete('/api/projects/nonexistent')
    assert r.status_code == 404


def test_api_list_projects_includes_counts(client):
    pid = _create_project(client, 'counted')
    r = client.post(f'/api/projects/{pid}/records', json={
        'records': [
            {'id': 'd1', 'kind': 'document', 'text': 'doc'},
            {'id': 'd2', 'kind': 'document', 'text': 'doc2'},
        ]
    })
    assert r.status_code == 201, r.text
    r = client.get('/api/projects')
    assert r.status_code == 200
    projects = r.json()['projects']
    target = next(p for p in projects if p['id'] == pid)
    assert target['counts'] == {
        'documents': 2, 'entities': 0, 'relations': 0, 'attributes': 0, 'chunks': 0}
