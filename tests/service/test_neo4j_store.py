import json
import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.integrations.neo4j_store import Neo4jProjection, digest
from knowledge_service.repository import Repository


class Driver:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.records = {}; self.versions = {}; self.edges = {}; self.attributes = {}; self.attribute_edges = {}; self.fingerprint = None; self.result = []

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
        if 'SET r:AttributeFact' in query:
            for row in params['rows']: self.attributes[row['key']] = dict(row['props'])
        if 'KS_FACT' in query and 'DELETE e' in query: self.edges.clear()
        if 'KS_ATTRIBUTE' in query and 'DELETE e' in query: self.attribute_edges.clear()
        if 'MERGE (a)-[e:KS_FACT' in query:
            for row in params['rows']: self.edges[row['key']] = dict(row['props'])
        if 'MERGE (a)-[e:KS_ATTRIBUTE' in query:
            for row in params['rows']: self.attribute_edges[row['key']] = dict(row['props'])
        if 'RETURN p.fingerprint' in query:
            self.result = [{'fingerprint': self.fingerprint}] if self.fingerprint else []
        if 'RETURN properties(r)' in query:
            group = (self.versions if ':KSVersion' in query else
                     self.attribute_edges if ':KS_ATTRIBUTE' in query else
                     self.edges if ':KS_FACT' in query else
                     self.attributes if ':AttributeFact' in query else self.records)
            self.result = [{'props': r, 'subject': r.get('subject_id'),
                            'object': r.get('object_id'), 'attribute': r.get('id'),
                            'subject_ns':r['namespace'], 'object_ns':r['namespace'],
                            'subject_project':r['project_id'], 'object_project':r['project_id']}
                           for r in group.values()]
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
    assert 'embedding' not in repo.history(pid, 'e')[0]  # 删列后向量不存 SQLite
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
    with pytest.raises(RuntimeError, match='本地数据已保留') as error:
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


def test_delete_project_removes_only_that_projects_nodes(tmp_path):
    """delete_project 按 namespace+project_id 精确下发远端删除，不波及同库其他项目。"""
    repo = Repository(tmp_path / 'del.sqlite')
    gone = repo.create_project('删除项目')['id']
    repo.put_record(gone, {'id': 'g', 'kind': 'entity', 'text': 'gone'})
    driver = Driver()
    projection = Neo4jProjection(repo, driver, settings={})
    projection.sync(gone)
    # 模拟远端库里还存在另一个项目的数据（driver 不区分项目，手动塞一条）
    driver.records[digest(['other-ns', 'keep-pid', 'k'])] = {
        'project_id': 'keep-pid', 'namespace': repo.storage_namespace, 'id': 'k', 'kind': 'entity', 'text': 'kept'}
    driver.calls.clear()

    projection.delete_project(gone)

    deletes = [params for query, params in driver.calls
               if 'DETACH DELETE p, r, v, o' in query]
    assert len(deletes) == 1
    assert deletes[0]['pid'] == gone
    assert deletes[0]['ns'] == repo.storage_namespace
    # 只有一条 DETACH DELETE 查询，且没有针对其他项目的删除
    assert not any('keep-pid' in str(params) for query, params in driver.calls
                   if 'DETACH DELETE' in query)
    repo.close()


def test_current_attribute_projects_as_typed_node_and_entity_edge(tmp_path):
    repo = Repository(tmp_path / 'attributes.sqlite')
    project_id = repo.create_project('属性投影')['id']
    repo.put_batch(project_id, [
        {'id': 'person', 'kind': 'entity', 'text': '张三'},
        {'id': 'age', 'kind': 'attribute', 'type': 'http://ex/age', 'text': '年龄 20',
         'subject_id': 'person', 'value': 20,
         'datatype': 'http://www.w3.org/2001/XMLSchema#integer'},
    ])
    driver = Driver()
    projection = Neo4jProjection(repo, driver, settings={})

    projection.sync(project_id)

    attribute = next(iter(driver.attributes.values()))
    assert attribute['id'] == 'age'
    assert attribute['value_json'] == '20'
    assert attribute['datatype'] == 'http://www.w3.org/2001/XMLSchema#integer'
    assert next(iter(driver.attribute_edges.values()))['id'] == 'age'
    verification = projection.project_status(project_id)['verification']
    assert verification['verified']
    assert verification['remote']['attribute_nodes'] == 1
    assert verification['remote']['attribute_edges'] == 1
    repo.close()
