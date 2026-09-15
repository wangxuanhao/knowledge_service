import json
import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.embeddings import HashingEncoder
from knowledge_service.neo4j_store import Neo4jProjection
from knowledge_service.repository import Repository


class Driver:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.records = {}; self.versions = {}; self.edges = {}; self.fingerprint = None; self.result = []

    def session(self, **kwargs): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def consume(self): pass
    def verify_connectivity(self): pass
    def close(self): pass
    def run(self, query, **params):
        self.calls.append((query, params))
        self.result = []
        if query.startswith('MERGE (p:KSProject'):
            self.fingerprint = params['fingerprint']
        if 'MERGE (v:KSVersion' in query:
            for row in params['rows']: self.versions[row['version_key']] = dict(row['props'])
        if 'SET r += row.props' in query:
            for row in params['rows']: self.records[row['key']] = dict(row['props'])
        if 'DELETE e' in query: self.edges.clear()
        if 'MERGE (a)-[e:KS_FACT' in query:
            for row in params['rows']: self.edges[row['key']] = dict(row['props'])
        if 'RETURN p.fingerprint' in query:
            self.result = [{'fingerprint': self.fingerprint}] if self.fingerprint else []
        if 'RETURN properties(r)' in query:
            group = self.versions if ':KSVersion' in query else self.edges if ':KS_FACT' in query else self.records
            self.result = [{'props': r, 'subject': r.get('subject_id'), 'object': r.get('object_id'), 'subject_ns':r['namespace'], 'object_ns':r['namespace'], 'subject_project':r['project_id'], 'object_project':r['project_id']} for r in group.values()]
        return self
    def __iter__(self): return iter(self.result)
    def execute_read(self, fn, *args): return fn(self, *args)
    def execute_write(self, fn, *args):
        if self.fail: raise OSError('secret-password must not leak')
        return fn(self, *args)


def test_snapshot_preserves_versions_and_namespace(tmp_path):
    path = tmp_path / 'local.sqlite'
    repo = Repository(path)
    pid = repo.create_project('本地保留')['id']
    row = {'id': 'e', 'kind': 'entity', 'text': 'before', 'embedding': [1.0], 'metadata': {'city': '北京'}}
    repo.put_record(pid, row)
    repo.put_record(pid, {**row, 'text': 'after'})
    snapshot = repo.export_projection(pid)
    assert len(snapshot['records']) == 2
    assert snapshot['records'][0]['superseded_at']
    assert 'embedding' not in snapshot['records'][0]
    assert repo.history(pid, 'e')[0]['embedding'] == [1.0]
    repo.close()
    repo = Repository(path)
    assert repo.export_projection(pid) == snapshot
    repo.close()


def test_sync_scope_idempotence_and_failure(tmp_path):
    repo = Repository(tmp_path / 'local.sqlite')
    pid = repo.create_project('图谱')['id']
    other = repo.create_project('不应同步')['id']
    repo.put_batch(pid, [
        {'id': 'a', 'kind': 'entity', 'text': 'a'},
        {'id': 'b', 'kind': 'entity', 'text': 'b'},
        {'id': 'r', 'kind': 'relation', 'text': '关系', 'type': 'quoted`type', 'subject_id': 'a', 'object_id': 'b', 'valid_from': '2025-01-01'},
    ])
    repo.put_record(other, {'id': 'other', 'kind': 'entity', 'text': 'not included'})
    driver = Driver()
    projection = Neo4jProjection(repo, driver, settings={})
    assert projection.project_status(pid)['local_changes_pending']
    receipt = projection.sync(pid)
    assert not projection.project_status(pid)['local_changes_pending']
    projection.sync(pid)
    assert len(repo.list_artifacts('neo4j_sync')) == 1
    edge_calls = [params for query, params in driver.calls if 'MERGE (a)-[e:KS_FACT' in query]
    assert edge_calls[0]['rows'][0]['props']['valid_from'].startswith('2025-01-01')
    assert all('quoted`type' not in query and 'DETACH DELETE' not in query for query, _ in driver.calls)
    assert 'not included' not in json.dumps(driver.calls)
    repo.put_record(pid, {'id': 'a', 'kind': 'entity', 'text': 'deleted', 'metadata': {'_deleted': True}})
    assert projection.project_status(pid)['local_changes_pending']
    before = repo.export_projection(pid)
    driver.fail = True
    with pytest.raises(RuntimeError, match='local data preserved') as error:
        projection.sync(pid)
    assert 'secret-password' not in str(error.value)
    assert repo.export_projection(pid) == before
    assert repo.list_artifacts('neo4j_sync')[0]['fingerprint'] == receipt['fingerprint']
    driver.fail = False
    driver.calls.clear()
    projection.sync(pid)
    assert not any('MERGE (a)-[e:KS_FACT' in query for query, _ in driver.calls)
    assert len(repo.history(pid, 'a')) == 2
    repo.close()


def test_unconfigured_api_keeps_local_functional(tmp_path, monkeypatch):
    monkeypatch.delenv('KG_NEO4J_PASSWORD', raising=False)
    with TestClient(create_app(tmp_path / 'api.sqlite', HashingEncoder())) as client:
        assert client.get('/api/storage').json()['primary'] == 'sqlite'
        assert client.get('/api/storage').json()['configured'] is False
        assert client.post('/api/storage/neo4j/check').status_code == 503
        pid = client.post('/api/projects', json={'name': '仍然可用'}).json()['id']
        assert client.get(f'/api/projects/{pid}/storage/neo4j').json()['local_changes_pending']
        assert client.post('/api/projects/missing/storage/neo4j/sync').status_code == 404
        assert client.get('/api/projects').json()['projects'][0]['name'] == '仍然可用'


def test_receipt_cannot_hide_remote_deletion_or_changed_content(tmp_path):
    repo = Repository(tmp_path / 'verify.sqlite')
    p = repo.create_project('核验')['id']
    repo.put_record(p, {'id':'a', 'kind':'entity', 'text':'original'})
    driver = Driver(); projection = Neo4jProjection(repo, driver, settings={})
    projection.sync(p)
    assert projection.project_status(p)['in_sync']
    next(iter(driver.records.values()))['text'] = 'tampered'
    status = projection.project_status(p)
    assert not status['in_sync'] and status['sync_required']
    assert not status['local_changes_pending']
    driver.records.clear()
    assert projection.project_status(p)['verification']['remote']['entities'] == 0
    projection.sync(p)
    assert projection.project_status(p)['in_sync']
    repo.close()
