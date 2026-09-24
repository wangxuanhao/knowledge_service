"""可选、显式的 SQLite -> Neo4j 投影。SQLite 仍是权威数据源。

一次项目同步即一次远程事务。失败的同步不会回滚本地写入。
固定标签与参数化值隔离了本应用的数据。
"""
import hashlib
import json
import os
import threading
from urllib.parse import urlsplit

from ..core.time import utc_now


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def record_properties(record, ns, pid):
    props = {k: record.get(k) for k in ('id', 'kind', 'text', 'type', 'datatype',
             'version', 'version_id', 'recorded_at', 'superseded_at', 'valid_from',
             'valid_until', 'source_id', 'subject_id', 'object_id', 'ontology_id')}
    props.update(namespace=ns, project_id=pid, metadata_json=encoded(record.get('metadata', {})),
                 payload_json=encoded(record), deleted=bool(record.get('metadata', {}).get('_deleted')),
                 audit=bool(record.get('metadata', {}).get('_audit')))
    if record.get('kind') == 'attribute':
        props['value_json'] = encoded(record.get('value'))
    return props


def projection_digest(snapshot):
    """Hash projection state without local artifacts such as the sync receipt."""
    return digest({key: value for key, value in snapshot.items() if key != 'artifacts'})


class Neo4jProjection:
    def __init__(self, repository, driver=None, settings=None):
        self.repo = repository
        env = os.environ if settings is None else settings
        self.uri = env.get('KG_NEO4J_URI', 'neo4j://127.0.0.1:7687')
        self.database = env.get('KG_NEO4J_DATABASE', 'neo4j')
        self.username = env.get('KG_NEO4J_USERNAME', 'neo4j')
        self.password = env.get('KG_NEO4J_PASSWORD', '')
        self.driver = driver
        self.lock = threading.RLock()

    def status(self):
        return {'mode': 'sqlite_with_optional_neo4j_projection', 'primary': 'sqlite',
                'configured': bool(self.password or self.driver),
                'database': self.database, 'sync_mode': 'manual_per_project',
                'retrieval_backend': 'local', 'embeddings_synced': False}

    def _driver(self):
        if self.driver is not None:
            return self.driver
        parsed = urlsplit(self.uri)
        if parsed.scheme not in {'neo4j', 'neo4j+s', 'neo4j+ssc', 'bolt', 'bolt+s', 'bolt+ssc'} or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError('KG_NEO4J_URI 无效；请单独配置凭据')
        if not self.password:
            raise RuntimeError('请在服务器环境中设置 KG_NEO4J_PASSWORD；本地存储不受影响')
        try:
            from neo4j import GraphDatabase
        except ImportError:
            raise RuntimeError('请在 llm_model 环境中安装可选的 neo4j 驱动') from None
        self.driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password),
                                          connection_timeout=5, connection_acquisition_timeout=10,
                                          max_transaction_retry_time=10)
        return self.driver

    def check(self):
        with self.lock:
            try:
                driver = self._driver()
                driver.verify_connectivity()
                with driver.session(database=self.database) as session:
                    session.run('RETURN 1 AS ok').consume()
                return {**self.status(), 'connected': True}
            except Exception as exc:
                # 绝不返回驱动消息：其中可能包含连接细节。
                raise RuntimeError(f'Neo4j 连接不可用（{type(exc).__name__}）；请检查驱动、凭据和数据库') from None

    def close(self):
        if self.driver is not None:
            self.driver.close()

    def project_status(self, project_id):
        snapshot = self.repo.export_projection(project_id)
        key = digest([snapshot['namespace'], project_id, self.uri, self.database])
        try:
            receipt = self.repo.get_artifact('neo4j_sync', key)
        except KeyError:
            receipt = None
        pending = not receipt or receipt['fingerprint'] != projection_digest(snapshot)
        try:
            with self.lock, self._driver().session(database=self.database) as session:
                verification = session.execute_read(self._verify, snapshot)
        except Exception:
            verification = {'verified': False, 'state': 'unavailable', 'message': '无法核验远端，请检查连接、凭据和数据库。'}
        return {**self.status(), 'last_sync': receipt, 'local_changes_pending': pending,
                'verification': verification, 'in_sync': verification['verified'],
                'sync_required': True if pending or verification['state'] == 'mismatch' else (None if verification['state'] == 'unavailable' else False)}

    def delete_project(self, project_id):
        """从 Neo4j 投影中删除整个项目：项目节点、记录/版本/本体节点及其关系。

        与 SQLite 删除独立——SQLite 仍是权威源，这里只清理远端副本；
        未配置/连接失败时抛出异常，由调用方决定是否吞掉。
        """
        with self.lock:
            namespace = self.repo.storage_namespace
            driver = self._driver()
            with driver.session(database=self.database) as session:
                session.run(
                    'MATCH ()-[e:KS_FACT {namespace:$ns, project_id:$pid}]->() DELETE e',
                    ns=namespace, pid=project_id).consume()
                session.run(
                    'MATCH ()-[e:KS_ATTRIBUTE {namespace:$ns, project_id:$pid}]->() DELETE e',
                    ns=namespace, pid=project_id).consume()
                session.run(
                    'MATCH (p:KSProject {namespace:$ns, project_id:$pid}) '
                    'OPTIONAL MATCH (p)-[:KS_CONTAINS]->(r:KSRecord) '
                    'OPTIONAL MATCH (r)-[:KS_VERSION]->(v:KSVersion) '
                    'OPTIONAL MATCH (p)-[:KS_ONTOLOGY]->(o:KSOntology) '
                    'DETACH DELETE p, r, v, o',
                    ns=namespace, pid=project_id).consume()
            return True

    @staticmethod
    def _verify(tx, snapshot):
        ns, pid = snapshot['namespace'], snapshot['project']['id']
        all_rows = [record_properties(r, ns, pid) for r in snapshot['records']]
        current = [r for r in all_rows if r['superseded_at'] is None]
        entities = {r['id'] for r in current if r['kind'] == 'entity' and not r['deleted'] and not r['audit']}
        edges = [r for r in current if r['kind'] == 'relation' and not r['deleted'] and not r['audit'] and r['subject_id'] in entities and r['object_id'] in entities]
        attributes = [r for r in current if r['kind'] == 'attribute' and not r['deleted']
                      and not r['audit'] and r['subject_id'] in entities]
        params = dict(ns=ns, pid=pid)
        project = list(tx.run('MATCH (p:KSProject {namespace:$ns, project_id:$pid}) RETURN p.fingerprint AS fingerprint', **params))
        groups = [('records', current, 'id', 'MATCH (r:KSRecord {namespace:$ns, project_id:$pid}) RETURN properties(r) AS props'),
                  ('versions', all_rows, 'version_id', 'MATCH (r:KSVersion {namespace:$ns, project_id:$pid}) RETURN properties(r) AS props'),
                  ('relations', edges, 'id', 'MATCH (a)-[r:KS_FACT {namespace:$ns, project_id:$pid}]->(b) RETURN properties(r) AS props, a.id AS subject, b.id AS object, a.namespace AS subject_ns, b.namespace AS object_ns, a.project_id AS subject_project, b.project_id AS object_project'),
                  ('attribute_nodes', attributes, 'id', 'MATCH (r:KSRecord:AttributeFact {namespace:$ns, project_id:$pid}) RETURN properties(r) AS props'),
                  ('attribute_edges', attributes, 'id', 'MATCH (a)-[r:KS_ATTRIBUTE {namespace:$ns, project_id:$pid}]->(b:AttributeFact) RETURN properties(r) AS props, a.id AS subject, b.id AS attribute, a.namespace AS subject_ns, b.namespace AS object_ns, a.project_id AS subject_project, b.project_id AS object_project')]
        differences = {}; remote_counts = {}; local_counts = {}; remote_entities = 0
        for name, expected, key, query in groups:
            found = list(tx.run(query, **params)); actual = []
            endpoints_ok = True
            for row in found:
                props = dict(row['props'])
                if name == 'relations':
                    props['subject_id'], props['object_id'] = row['subject'], row['object']
                    endpoints_ok &= row['subject_ns'] == row['object_ns'] == ns and row['subject_project'] == row['object_project'] == pid
                elif name == 'attribute_edges':
                    props['subject_id'], props['id'] = row['subject'], row['attribute']
                    endpoints_ok &= row['subject_ns'] == row['object_ns'] == ns and row['subject_project'] == row['object_project'] == pid
                actual.append(props)
            if name == 'records':
                remote_entities = sum(r.get('kind') == 'entity' and not r.get('deleted') and not r.get('audit') for r in actual)
            wanted = {r[key]: r for r in expected}; got = {r.get(key): r for r in actual}
            changed = sum(any(got[i].get(k) != v for k, v in r.items()) for i, r in wanted.items() if i in got)
            differences[name] = {'missing': len(wanted.keys()-got.keys()), 'extra': len(got.keys()-wanted.keys()), 'changed': changed, 'duplicates': len(actual)-len(got), 'wrong_endpoints': not endpoints_ok}
            local_counts[name], remote_counts[name] = len(expected), len(actual)
        ontologies = list(tx.run('MATCH (p:KSProject {namespace:$ns, project_id:$pid})-[:KS_ONTOLOGY]->(o:KSOntology) RETURN o.payload_json AS payload, o.turtle AS turtle', **params))
        ontology_ok = sorted((x['payload'], x['turtle']) for x in ontologies) == sorted((encoded(x), x['turtle']) for x in snapshot['ontologies'])
        local_counts.update(entities=len(entities), ontologies=len(snapshot['ontologies']))
        remote_counts.update(entities=remote_entities, ontologies=len(ontologies))
        matched = len(project) == 1 and project[0]['fingerprint'] == projection_digest(snapshot) and ontology_ok and not any(any(d.values()) for d in differences.values())
        return {'verified': matched, 'state': 'matched' if matched else 'mismatch', 'checked_at': utc_now(),
                'local': local_counts, 'remote': remote_counts, 'differences': differences}

    def sync(self, project_id, progress=lambda *args: None):
        # 串行化快照与提交，防止旧同步超越新同步。
        with self.lock:
            snapshot = self.repo.export_projection(project_id)
            fingerprint = projection_digest(snapshot)
            progress('已捕获本地快照；正在连接 Neo4j', 15)
            try:
                driver = self._driver()
                with driver.session(database=self.database) as session:
                    for label in ('KSProject', 'KSRecord', 'KSVersion', 'KSOntology'):
                        session.run(f'CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS FOR (n:{label}) REQUIRE n.key IS UNIQUE').consume()
                    session.execute_write(self._write, snapshot, fingerprint)
                    progress('远端写入完成，正在核验记录、版本和关系', 90)
                    verification = session.execute_read(self._verify, snapshot)
                    if not verification['verified']:
                        raise RuntimeError('远端核验不一致')
            except Exception as exc:
                raise RuntimeError(f'Neo4j 同步失败（{type(exc).__name__}）；本地数据已保留，检查连接与权限后重试') from None
            receipt = {'id': digest([snapshot['namespace'], project_id, self.uri, self.database]),
                       'project_id': project_id, 'fingerprint': fingerprint, 'synced_at': utc_now(),
                       'versions': len(snapshot['records']), 'ontologies': len(snapshot['ontologies']), 'verification': verification}
            self.repo.save_artifact('neo4j_sync', receipt)
            progress('Neo4j 已提交；本地同步收据已保存', 100)
            return receipt

    @staticmethod
    def _write(tx, snapshot, fingerprint):
        ns, project = snapshot['namespace'], snapshot['project']
        pid = project['id']
        project_key = digest([ns, pid])
        def run(query, **params):
            tx.run(query, **params).consume()
        run('MERGE (p:KSProject {key:$key}) SET p.namespace=$ns, p.project_id=$pid, p.name=$name, p.payload_json=$payload, p.fingerprint=$fingerprint',
            key=project_key, ns=ns, pid=pid, name=project['name'], payload=encoded(project), fingerprint=fingerprint)
        versions = []
        current = []
        for record in snapshot['records']:
            props = record_properties(record, ns, pid)
            row = {'key': digest([ns, pid, record['id']]), 'version_key': digest([ns, pid, record['version_id']]), 'props': props}
            versions.append(row)
            if record['superseded_at'] is None:
                current.append(row)
        for start in range(0, len(versions), 500):
            run('UNWIND $rows AS row MATCH (p:KSProject {key:$project_key}) '
                'MERGE (r:KSRecord {key:row.key}) MERGE (p)-[:KS_CONTAINS]->(r) '
                'MERGE (v:KSVersion {key:row.version_key}) SET v += row.props MERGE (r)-[:KS_VERSION]->(v)',
                rows=versions[start:start+500], project_key=project_key)
        for start in range(0, len(current), 500):
            run('UNWIND $rows AS row MATCH (r:KSRecord {key:row.key}) SET r += row.props', rows=current[start:start+500])
        run('MATCH (r:KSRecord {namespace:$ns, project_id:$pid}) REMOVE r:Entity:AttributeFact',
            ns=ns, pid=pid)
        entity_rows=[row for row in current if row['props']['kind']=='entity'
                     and not row['props']['deleted'] and not row['props']['audit']]
        attribute_rows=[row for row in current if row['props']['kind']=='attribute'
                        and not row['props']['deleted'] and not row['props']['audit']]
        for start in range(0,len(entity_rows),500):
            run('UNWIND $rows AS row MATCH (r:KSRecord {key:row.key}) SET r:Entity',
                rows=entity_rows[start:start+500])
        for start in range(0,len(attribute_rows),500):
            run('UNWIND $rows AS row MATCH (r:KSRecord {key:row.key}) SET r:AttributeFact',
                rows=attribute_rows[start:start+500])
        # 只重建属于本来源数据库/项目的边。绝不清空数据库。
        run('MATCH ()-[e:KS_FACT {namespace:$ns, project_id:$pid}]->() DELETE e', ns=ns, pid=pid)
        run('MATCH ()-[e:KS_ATTRIBUTE {namespace:$ns, project_id:$pid}]->() DELETE e', ns=ns, pid=pid)
        entities = {r['props']['id'] for r in current if r['props']['kind'] == 'entity' and not r['props']['deleted'] and not r['props']['audit']}
        edges = []
        for row in current:
            props = row['props']
            if props['kind'] == 'relation' and not props['deleted'] and not props['audit'] and props['subject_id'] in entities and props['object_id'] in entities:
                edges.append({'key': row['key'], 'subject': digest([ns, pid, props['subject_id']]),
                              'object': digest([ns, pid, props['object_id']]), 'props': props})
        for start in range(0, len(edges), 500):
            run('UNWIND $rows AS row MATCH (a:KSRecord {key:row.subject}), (b:KSRecord {key:row.object}) '
                'MERGE (a)-[e:KS_FACT {key:row.key}]->(b) SET e += row.props', rows=edges[start:start+500])
        attribute_edges=[]
        for row in attribute_rows:
            props=row['props']
            if props['subject_id'] in entities:
                attribute_edges.append({'key':row['key'],
                    'subject':digest([ns,pid,props['subject_id']]),
                    'attribute':row['key'],'props':props})
        for start in range(0,len(attribute_edges),500):
            run('UNWIND $rows AS row MATCH (a:KSRecord:Entity {key:row.subject}), '
                '(b:KSRecord:AttributeFact {key:row.attribute}) '
                'MERGE (a)-[e:KS_ATTRIBUTE {key:row.key}]->(b) SET e += row.props',
                rows=attribute_edges[start:start+500])
        for ontology in snapshot['ontologies']:
            run('MATCH (p:KSProject {key:$project_key}) MERGE (o:KSOntology {key:$key}) '
                'SET o.id=$id, o.turtle=$turtle, o.payload_json=$payload MERGE (p)-[:KS_ONTOLOGY]->(o)',
                project_key=project_key, key=digest([ns, pid, ontology['id']]), id=ontology['id'],
                turtle=ontology['turtle'], payload=encoded(ontology))


def install(app, service):
    projection = Neo4jProjection(service.repository)
    app.state.neo4j = projection

    @app.get('/api/storage')
    def storage():
        return projection.status()

    @app.post('/api/storage/neo4j/check')
    def check():
        return projection.check()

    @app.get('/api/projects/{project_id}/storage/neo4j')
    def project_status(project_id: str):
        return projection.project_status(project_id)

    @app.post('/api/projects/{project_id}/storage/neo4j/sync', status_code=202)
    def sync(project_id: str):
        service.repository.get_project(project_id)
        return app.state.jobs.submit('neo4j_sync', lambda progress: projection.sync(project_id, progress), project_id)
