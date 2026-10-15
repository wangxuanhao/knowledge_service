"""可选、显式的 PostgreSQL -> Neo4j 投影。PostgreSQL 仍是权威数据源。

一次项目同步即一次远程事务。失败的同步不会回滚本地写入。
固定标签与参数化值隔离了本应用的数据。
"""
import hashlib
import json
import os
import threading
from datetime import datetime
from urllib.parse import urlsplit
from uuid import uuid4

from ..core.time import utc_now
from ..domain.applicability import normalize_applicability, scope_identity


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
        return {'mode': 'postgres_with_optional_neo4j_projection', 'primary': 'postgres',
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

        与 PostgreSQL 删除独立——PostgreSQL 仍是权威源，这里只清理远端副本；
        未配置/连接失败时抛出异常，由调用方决定是否吞掉。
        """
        with self.lock:
            namespace = self.repo.storage_namespace
            driver = self._driver()
            with driver.session(database=self.database) as session:
                for label in ('KSRecordV3', 'KSScope', 'KSScopeValue',
                              'KSProjection', 'KSProjectionPointer'):
                    session.run(
                        f'MATCH (n:{label} {{namespace:$ns, project_id:$pid}}) '
                        'DETACH DELETE n', ns=namespace, pid=project_id).consume()
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


class Neo4jSchemaMismatch(RuntimeError):
    requires_rebuild = True


class Neo4jProjectionAdapter:
    """Ordered, generation-scoped Neo4j projection used by the outbox worker."""

    INDEXED_KINDS = frozenset({'entity', 'relation', 'chunk'})
    PROJECTED_KINDS = INDEXED_KINDS | frozenset({'attribute'})

    def __init__(self, driver, *, namespace, database='neo4j',
                 fence_is_current=None, repair_authority=None):
        self.driver = driver
        self.namespace = namespace
        self.database = database
        self._fence_is_current = fence_is_current
        self._repair_authority = repair_authority
        self._lock = threading.RLock()
        self._positions = {}

    def _driver(self):
        return self.driver() if callable(self.driver) else self.driver

    def _session(self):
        return self._driver().session(database=self.database)

    @staticmethod
    def _instant(value):
        if value is None:
            return False, 0
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return True, int(parsed.timestamp() * 1000)

    def _record_row(self, project_id, generation, record, seq, fence):
        applicability = normalize_applicability(
            (record.get('metadata') or {}).get('applicability'))
        start_known, start_epoch = self._instant(record.get('valid_from'))
        until_known, until_epoch = self._instant(record.get('valid_until'))
        props = record_properties(record, self.namespace, project_id)
        props.update(
            generation=generation, projection_seq=int(seq), fence=fence,
            active=True,
            platform_mode=applicability['platforms']['mode'],
            platform_values=applicability['platforms']['values'],
            business_mode=applicability['businesses']['mode'],
            business_values=applicability['businesses']['values'],
            audience_mode=applicability['audiences']['mode'],
            audience_values=applicability['audiences']['values'],
            scope_identity=scope_identity(applicability),
            valid_from_known=start_known, valid_from_epoch_ms=start_epoch,
            valid_until_known=until_known, valid_until_epoch_ms=until_epoch,
        )
        key = digest([self.namespace, project_id, generation, record['id']])
        scope_key = digest([self.namespace, project_id, generation,
                            props['scope_identity']])
        return {'key': key, 'scope_key': scope_key, 'props': props}

    @staticmethod
    def _consume(result):
        consume = getattr(result, 'consume', None)
        if consume is not None:
            consume()

    @classmethod
    def _write_rows(cls, tx, namespace, project_id, generation, rows):
        result = tx.run(
            'UNWIND $keys AS key MATCH (r:KSRecordV3 {key:key})'
            '-[old:KS_SCOPE]->() DELETE old',
            keys=[row['key'] for row in rows])
        cls._consume(result)
        result = tx.run(
            'UNWIND $rows AS row '
            'MATCH (g:KSProjection {namespace:$ns,project_id:$pid,generation:$generation}) '
            'MERGE (r:KSRecordV3 {key:row.key}) SET r += row.props '
            'MERGE (g)-[:KS_CONTAINS]->(r) '
            'MERGE (s:KSScope {key:row.scope_key}) '
            'SET s.namespace=$ns,s.project_id=$pid,s.generation=$generation,'
            's.scope_identity=row.props.scope_identity '
            'MERGE (r)-[:KS_SCOPE]->(s)',
            ns=namespace, pid=project_id, generation=generation, rows=rows)
        cls._consume(result)
        values = []
        for row in rows:
            for dimension, field in (('platform', 'platform_values'),
                                     ('business', 'business_values'),
                                     ('audience', 'audience_values')):
                for value in row['props'][field]:
                    values.append({
                        'scope_key': row['scope_key'], 'dimension': dimension,
                        'value': value,
                        'key': digest([namespace, project_id, generation,
                                       dimension, value]),
                    })
        if values:
            result = tx.run(
                'UNWIND $rows AS row MATCH (s:KSScope {key:row.scope_key}) '
                'MERGE (v:KSScopeValue {key:row.key}) '
                'SET v.namespace=$ns,v.project_id=$pid,v.generation=$generation,'
                'v.dimension=row.dimension,v.value=row.value '
                'MERGE (s)-[:KS_VALUE]->(v)',
                ns=namespace, pid=project_id, generation=generation,
                rows=values)
            cls._consume(result)
        result = tx.run(
            'MATCH (s:KSScope {namespace:$ns,project_id:$pid,generation:$generation}) '
            'WHERE NOT ()-[:KS_SCOPE]->(s) DETACH DELETE s',
            ns=namespace, pid=project_id, generation=generation)
        cls._consume(result)
        result = tx.run(
            'MATCH (v:KSScopeValue {namespace:$ns,project_id:$pid,generation:$generation}) '
            'WHERE NOT ()-[:KS_VALUE]->(v) DETACH DELETE v',
            ns=namespace, pid=project_id, generation=generation)
        cls._consume(result)
        relations = [
            {'key': row['key'],
             'subject_key': digest([namespace, project_id, generation,
                                    row['props']['subject_id']]),
             'object_key': digest([namespace, project_id, generation,
                                   row['props']['object_id']]),
             'props': row['props']}
            for row in rows
            if row['props']['kind'] == 'relation'
            and not row['props']['deleted']
        ]
        if relations:
            cls._write_relations(tx, relations)
        attributes = [
            {'key': row['key'],
             'subject_key': digest([namespace, project_id, generation,
                                    row['props']['subject_id']]),
             'props': row['props']}
            for row in rows
            if row['props']['kind'] == 'attribute'
            and not row['props']['deleted']
        ]
        if attributes:
            cls._write_attributes(tx, attributes)

    @classmethod
    def _write_relations(cls, tx, relations):
        if relations:
            result = tx.run(
                'MATCH ()-[old:KS_FACT]->() WHERE old.key IN $keys DELETE old',
                keys=[row['key'] for row in relations])
            cls._consume(result)
            result = tx.run(
                'UNWIND $rows AS row '
                'MATCH (a:KSRecordV3 {key:row.subject_key}),'
                '(b:KSRecordV3 {key:row.object_key}) '
                'MERGE (a)-[e:KS_FACT {key:row.key}]->(b) SET e += row.props',
                rows=relations)
            cls._consume(result)

    @classmethod
    def _write_attributes(cls, tx, attributes):
        if attributes:
            result = tx.run(
                'MATCH ()-[old:KS_ATTRIBUTE]->() WHERE old.key IN $keys DELETE old',
                keys=[row['key'] for row in attributes])
            cls._consume(result)
            result = tx.run(
                'UNWIND $rows AS row '
                'MATCH (a:KSRecordV3 {key:row.subject_key}),'
                '(v:KSRecordV3 {key:row.key}) '
                'MERGE (a)-[e:KS_ATTRIBUTE {key:row.key}]->(v) SET e += row.props',
                rows=attributes)
            cls._consume(result)

    def _ensure_schema(self, session):
        statements = (
            'CREATE CONSTRAINT ksprojection_key IF NOT EXISTS FOR (n:KSProjection) REQUIRE (n.namespace,n.project_id,n.generation) IS UNIQUE',
            'CREATE CONSTRAINT kspointer_key IF NOT EXISTS FOR (n:KSProjectionPointer) REQUIRE (n.namespace,n.project_id) IS UNIQUE',
            'CREATE CONSTRAINT ksrecordv3_key IF NOT EXISTS FOR (n:KSRecordV3) REQUIRE n.key IS UNIQUE',
            'CREATE CONSTRAINT ksscope_key IF NOT EXISTS FOR (n:KSScope) REQUIRE n.key IS UNIQUE',
            'CREATE CONSTRAINT ksscopevalue_key IF NOT EXISTS FOR (n:KSScopeValue) REQUIRE n.key IS UNIQUE',
            'CREATE INDEX ksrecordv3_lookup IF NOT EXISTS FOR (n:KSRecordV3) ON (n.namespace,n.project_id,n.generation,n.id)',
            'CREATE INDEX ksscope_lookup IF NOT EXISTS FOR (n:KSScope) ON (n.namespace,n.project_id,n.scope_identity)',
            'CREATE INDEX ksscopevalue_lookup IF NOT EXISTS FOR (n:KSScopeValue) ON (n.namespace,n.project_id,n.dimension,n.value)',
        )
        for statement in statements:
            self._consume(session.run(statement))

    def begin_rebuild(self, project_id, schema_version, state, fence=None):
        generation = str(state.get('build_id') or uuid4())
        with self._lock, self._session() as session:
            self._ensure_schema(session)
            self._consume(session.run(
                'MERGE (g:KSProjection {namespace:$ns,project_id:$pid,generation:$generation}) '
                'SET g.schema_version=$schema_version,g.active=false,g.fence=$fence,'
                'g.projection_seq=0',
                ns=self.namespace, pid=project_id, generation=generation,
                schema_version=int(schema_version), fence=fence or ''))
        return generation

    def reset_rebuild(self, project_id, destination, build_id, fence=None):
        with self._lock, self._session() as session:
            self._consume(session.run(
                'MATCH (g:KSProjection {namespace:$ns,project_id:$pid,generation:$generation}) '
                'OPTIONAL MATCH (g)-[:KS_CONTAINS]->(r:KSRecordV3) '
                'OPTIONAL MATCH (r)-[:KS_SCOPE]->(s:KSScope) '
                'OPTIONAL MATCH (s)-[:KS_VALUE]->(v:KSScopeValue) '
                'DETACH DELETE r,s,v', ns=self.namespace, pid=project_id,
                generation=destination))

    def load_snapshot(self, project_id, destination, records, snapshot_seq,
                      fence=None):
        selected = [row for row in records
                    if row.get('kind') in self.PROJECTED_KINDS
                    and not (row.get('metadata') or {}).get('_deleted')]
        rows = [self._record_row(project_id, destination, row, snapshot_seq,
                                 fence or '') for row in selected]
        with self._lock, self._session() as session:
            self._ensure_schema(session)
            self._consume(session.run(
                'MERGE (g:KSProjection {namespace:$ns,project_id:$pid,generation:$generation}) '
                'SET g.projection_seq=$seq,g.fence=$fence',
                ns=self.namespace, pid=project_id, generation=destination,
                seq=int(snapshot_seq), fence=fence or ''))
            for offset in range(0, len(rows), 256):
                session.execute_write(self._write_rows, self.namespace,
                                      project_id, destination,
                                      rows[offset:offset + 256])
            relation_rows = [
                {'key': row['key'],
                 'subject_key': digest([self.namespace, project_id, destination,
                                        row['props']['subject_id']]),
                 'object_key': digest([self.namespace, project_id, destination,
                                       row['props']['object_id']]),
                 'props': row['props']}
                for row in rows if row['props']['kind'] == 'relation']
            for offset in range(0, len(relation_rows), 256):
                session.execute_write(
                    self._write_relations,
                    relation_rows[offset:offset + 256])
            attribute_rows = [
                {'key': row['key'],
                 'subject_key': digest([self.namespace, project_id, destination,
                                        row['props']['subject_id']]),
                 'props': row['props']}
                for row in rows if row['props']['kind'] == 'attribute']
            for offset in range(0, len(attribute_rows), 256):
                session.execute_write(
                    self._write_attributes,
                    attribute_rows[offset:offset + 256])
        return len(rows)

    def current_destination(self, project_id):
        with self._lock, self._session() as session:
            result = session.run(
                'MATCH (:KSProjectionPointer {namespace:$ns,project_id:$pid})'
                '-[:KS_ACTIVE]->(g:KSProjection) RETURN g.generation AS generation',
                ns=self.namespace, pid=project_id)
            row = result.single() if hasattr(result, 'single') else next(iter(result), None)
            return row.get('generation') if row else None

    def apply(self, project_id, event, destination=None, fence=None):
        if not isinstance(fence, str) or not fence:
            raise ValueError('Neo4j projection apply requires a fence token')
        generation = destination or self.current_destination(project_id)
        if not generation:
            raise Neo4jSchemaMismatch('Neo4j active generation is missing')
        seq = int(event['seq'])
        key = (project_id, generation)
        with self._lock:
            previous = self._positions.get(key)
            if previous is not None and seq <= previous[0]:
                return False
            payload = dict(event.get('payload') or {})
            operation = event.get('operation')
            with self._session() as session:
                if operation == 'upsert' and payload.get('kind') in self.PROJECTED_KINDS:
                    row = self._record_row(project_id, generation, payload, seq, fence)
                    session.execute_write(self._write_rows, self.namespace,
                                          project_id, generation, [row])
                elif operation == 'tombstone':
                    record_id = payload.get('record_id') or payload.get('id')
                    if not record_id:
                        raise ValueError('Neo4j tombstone requires record_id')
                    tombstone_key=digest([self.namespace,project_id,generation,
                                          record_id])
                    self._consume(session.run(
                        'MATCH ()-[e:KS_FACT {key:$key}]->() DELETE e',
                        key=tombstone_key))
                    self._consume(session.run(
                        'MATCH (r:KSRecordV3 {namespace:$ns,project_id:$pid,'
                        'generation:$generation,id:$record_id}) DETACH DELETE r',
                        ns=self.namespace, pid=project_id, generation=generation,
                        record_id=record_id))
                    self._consume(session.run(
                        'MATCH (s:KSScope {namespace:$ns,project_id:$pid,generation:$generation}) '
                        'WHERE NOT ()-[:KS_SCOPE]->(s) DETACH DELETE s',
                        ns=self.namespace,pid=project_id,generation=generation))
                    self._consume(session.run(
                        'MATCH (v:KSScopeValue {namespace:$ns,project_id:$pid,generation:$generation}) '
                        'WHERE NOT ()-[:KS_VALUE]->(v) DETACH DELETE v',
                        ns=self.namespace,pid=project_id,generation=generation))
                elif operation != 'upsert':
                    raise ValueError(f'unsupported projection operation: {operation}')
                self._consume(session.run(
                    'MATCH (g:KSProjection {namespace:$ns,project_id:$pid,generation:$generation}) '
                    'SET g.projection_seq=$seq,g.fence=$fence',
                    ns=self.namespace, pid=project_id, generation=generation,
                    seq=seq, fence=fence))
            record_id = payload.get('id') or payload.get('record_id')
            if (self._fence_is_current is not None
                    and not self._fence_is_current(project_id, seq, fence)):
                if self._repair_authority is not None:
                    self._repair_authority(project_id, record_id, generation)
                return False
            self._positions[key] = (seq, fence)
            return True

    def repair_record(self, project_id, record_id, records, destination, seq):
        """Restore a record and its authoritative current incident bundle."""
        bundle = ([] if records is None else
                  [records] if isinstance(records, dict) else list(records))
        with self._lock, self._session() as session:
            self._consume(session.run(
                'MATCH ()-[e:KS_FACT {namespace:$ns,project_id:$pid,'
                'generation:$generation}]->() '
                'WHERE e.id=$record_id OR e.subject_id=$record_id OR '
                'e.object_id=$record_id DELETE e',
                ns=self.namespace,pid=project_id,generation=destination,
                record_id=record_id))
            self._consume(session.run(
                'MATCH (relation:KSRecordV3 {namespace:$ns,project_id:$pid,'
                'generation:$generation,kind:"relation"}) '
                'WHERE relation.id=$record_id OR relation.subject_id=$record_id '
                'OR relation.object_id=$record_id '
                'DETACH DELETE relation',ns=self.namespace,pid=project_id,
                generation=destination,record_id=record_id))
            self._consume(session.run(
                'MATCH (r:KSRecordV3 {namespace:$ns,project_id:$pid,'
                'generation:$generation,id:$record_id}) DETACH DELETE r',
                ns=self.namespace, pid=project_id, generation=destination,
                record_id=record_id))
            selected=[row for row in bundle
                      if row.get('kind') in self.PROJECTED_KINDS
                      and not (row.get('metadata') or {}).get('_deleted')]
            if selected:
                rows=[self._record_row(project_id,destination,row,seq,
                                       'authority-repair') for row in selected]
                session.execute_write(self._write_rows, self.namespace,
                                      project_id, destination, rows)

    def validate_rebuild(self, project_id, destination, fence=None, state=None):
        expected = dict((state or {}).get('expected_versions') or {})
        if not expected and 'expected_versions' not in (state or {}):
            return {'ok': False, 'reason': 'expected versions missing'}
        with self._lock, self._session() as session:
            result = session.run(
                'MATCH (g:KSProjection {namespace:$ns,project_id:$pid,generation:$generation}) '
                'OPTIONAL MATCH (g)-[:KS_CONTAINS]->(r:KSRecordV3) '
                'WHERE r.kind IN ["entity","relation","chunk"] '
                'RETURN g.schema_version AS schema_version, '
                'collect([r.id,r.version_id]) AS versions,'
                'collect({id:r.id,version_id:r.version_id,'
                'platform:{mode:r.platform_mode,values:r.platform_values},'
                'business:{mode:r.business_mode,values:r.business_values},'
                'audience:{mode:r.audience_mode,values:r.audience_values},'
                'scope_identity:r.scope_identity,'
                'valid_from:[r.valid_from_known,r.valid_from_epoch_ms],'
                'valid_until:[r.valid_until_known,r.valid_until_epoch_ms]}) AS projections',
                ns=self.namespace, pid=project_id, generation=destination)
            row = result.single() if hasattr(result, 'single') else next(iter(result), None)
        if row is None:
            return {'ok': False, 'reason': 'generation missing'}
        actual = {item[0]: item[1] for item in row.get('versions', []) if item[0]}
        expected_projection=(state or {}).get('expected_projection')
        projections={item['id']:{key:value for key,value in item.items()
                                  if key!='id'}
                     for item in row.get('projections',[]) if item.get('id')}
        scope_ok=(expected_projection is None or projections==expected_projection)
        return {'ok': actual == expected and scope_ok,
                'scope_ok':scope_ok,'expected_count': len(expected),
                'actual_count': len(actual)}

    def switch_alias(self, project_id, destination, build_id=None, fence=None):
        def switch(tx):
            self._consume(tx.run(
                'MATCH (g:KSProjection {namespace:$ns,project_id:$pid,generation:$generation}) '
                'MERGE (p:KSProjectionPointer {namespace:$ns,project_id:$pid}) '
                'OPTIONAL MATCH (p)-[old:KS_ACTIVE]->(:KSProjection) DELETE old '
                'MERGE (p)-[:KS_ACTIVE]->(g) '
                'WITH p,g MATCH (other:KSProjection {namespace:$ns,project_id:$pid}) '
                'SET other.active=(other.generation=$generation),p.fence=$fence,'
                'p.projection_seq=g.projection_seq,p.schema_version=g.schema_version',
                ns=self.namespace, pid=project_id, generation=destination,
                fence=fence or ''))
        with self._lock, self._session() as session:
            return session.execute_write(switch)

    def retire(self, project_id, destination):
        if not destination or destination == self.current_destination(project_id):
            return False
        with self._lock, self._session() as session:
            self._consume(session.run(
                'MATCH (g:KSProjection {namespace:$ns,project_id:$pid,generation:$generation}) '
                'OPTIONAL MATCH (g)-[:KS_CONTAINS]->(r:KSRecordV3) '
                'OPTIONAL MATCH (r)-[:KS_SCOPE]->(s:KSScope) '
                'OPTIONAL MATCH (s)-[:KS_VALUE]->(v:KSScopeValue) '
                'DETACH DELETE g,r,s,v', ns=self.namespace, pid=project_id,
                generation=destination))
        return True

    @staticmethod
    def _scope_predicate(alias):
        return (
            f'{alias}.namespace=$ns AND {alias}.project_id=$pid AND '
            f'{alias}.generation=$generation AND {alias}.active=true AND '
            f'($platform_mode <> "specific" OR {alias}.platform_mode = "all" OR '
            f'({alias}.platform_mode = "specific" AND '
            f'{alias}.scope_identity IN allowed_platform_scope_ids)) AND '
            f'($business_mode <> "specific" OR {alias}.business_mode = "all" OR '
            f'({alias}.business_mode = "specific" AND '
            f'{alias}.scope_identity IN allowed_business_scope_ids)) AND '
            f'($audience_mode <> "specific" OR {alias}.audience_mode = "all" OR '
            f'({alias}.audience_mode = "specific" AND '
            f'{alias}.scope_identity IN allowed_audience_scope_ids)) AND '
            f'($valid_epoch_ms IS NULL OR (($include_unknown OR {alias}.valid_from_known) '
            f'AND (NOT {alias}.valid_from_known OR {alias}.valid_from_epoch_ms <= $valid_epoch_ms) '
            f'AND (NOT {alias}.valid_until_known OR {alias}.valid_until_epoch_ms > $valid_epoch_ms)))')

    def expand(self, project_id, seeds, hops, applicability=None, valid_at=None,
               include_unknown=True, known_at=None):
        if not isinstance(hops, int) or not 0 <= hops <= 2:
            raise ValueError('Neo4j expand hops 必须为 0 到 2')
        if known_at is not None:
            raise ValueError('Neo4j current projection 不支持 known_at 历史读取')
        scope = normalize_applicability(applicability)
        generation = self.current_destination(project_id)
        if not generation:
            raise Neo4jSchemaMismatch('Neo4j active generation is missing')
        params = {
            'ns': self.namespace, 'pid': project_id, 'generation': generation,
            'seed_ids': list(dict.fromkeys(seeds)),
            'platform_mode': scope['platforms']['mode'],
            'platform_values': scope['platforms']['values'],
            'business_mode': scope['businesses']['mode'],
            'business_values': scope['businesses']['values'],
            'audience_mode': scope['audiences']['mode'],
            'audience_values': scope['audiences']['values'],
            'include_unknown': include_unknown is not False,
            'valid_epoch_ms': self._instant(valid_at or utc_now())[1],
        }
        n0 = self._scope_predicate('n0')
        imports=('WITH g,allowed_platform_scope_ids,allowed_business_scope_ids,'
                 'allowed_audience_scope_ids ')
        branches = [
            f'{imports}MATCH (n0:KSRecordV3) WHERE n0.id IN $seed_ids AND {n0} '
            'RETURN n0.version_id AS node_version, null AS relation_version']
        if hops >= 1:
            e1, n1 = self._scope_predicate('e1'), self._scope_predicate('n1')
            branches.append(
                f'{imports}MATCH (n0:KSRecordV3)-[e1:KS_FACT]-(n1:KSRecordV3) '
                f'WHERE n0.id IN $seed_ids AND {n0} AND {e1} AND {n1} '
                'RETURN n1.version_id AS node_version,e1.version_id AS relation_version')
        if hops >= 2:
            e1, n1 = self._scope_predicate('e1'), self._scope_predicate('n1')
            e2, n2 = self._scope_predicate('e2'), self._scope_predicate('n2')
            branches.append(
                f'{imports}MATCH (n0:KSRecordV3)-[e1:KS_FACT]-(n1:KSRecordV3)'
                f'-[e2:KS_FACT]-(n2:KSRecordV3) WHERE n0.id IN $seed_ids '
                f'AND {n0} AND {e1} AND {n1} '
                f'AND {e2} AND {n2} '
                'RETURN n2.version_id AS node_version,e2.version_id AS relation_version')
        scope_lookup = (
            'MATCH (pointer:KSProjectionPointer {namespace:$ns,project_id:$pid})'
            '-[:KS_ACTIVE]->(g:KSProjection {generation:$generation}) '
            'WHERE g.namespace=$ns AND g.project_id=$pid AND g.active=true '
            'CALL { WITH g MATCH (sv:KSScopeValue {namespace:$ns,project_id:$pid,'
            'generation:$generation,dimension:"platform"}) '
            'WHERE sv.value IN $platform_values MATCH (s:KSScope)-[:KS_VALUE]->(sv) '
            'WHERE s.namespace=$ns AND s.project_id=$pid AND s.generation=$generation '
            'RETURN collect(DISTINCT s.scope_identity) AS allowed_platform_scope_ids } '
            'CALL { WITH g MATCH (sv:KSScopeValue {namespace:$ns,project_id:$pid,'
            'generation:$generation,dimension:"business"}) '
            'WHERE sv.value IN $business_values MATCH (s:KSScope)-[:KS_VALUE]->(sv) '
            'WHERE s.namespace=$ns AND s.project_id=$pid AND s.generation=$generation '
            'RETURN collect(DISTINCT s.scope_identity) AS allowed_business_scope_ids } '
            'CALL { WITH g MATCH (sv:KSScopeValue {namespace:$ns,project_id:$pid,'
            'generation:$generation,dimension:"audience"}) '
            'WHERE sv.value IN $audience_values MATCH (s:KSScope)-[:KS_VALUE]->(sv) '
            'WHERE s.namespace=$ns AND s.project_id=$pid AND s.generation=$generation '
            'RETURN collect(DISTINCT s.scope_identity) AS allowed_audience_scope_ids } ')
        query = (scope_lookup + 'CALL { ' + ' UNION ALL '.join(branches) + ' } '
                 'WITH collect(DISTINCT node_version) AS node_versions,'
                 'collect(DISTINCT relation_version) AS relation_versions,g,'
                 'allowed_platform_scope_ids,allowed_business_scope_ids,'
                 'allowed_audience_scope_ids UNWIND node_versions AS selected_version '
                 'MATCH (selected:KSRecordV3 {version_id:selected_version}) '
                 'WHERE ' + self._scope_predicate('selected') + ' '
                 'OPTIONAL MATCH (selected)-[ae:KS_ATTRIBUTE]->(attr:KSRecordV3) '
                 'WHERE ' + self._scope_predicate('ae') + ' AND ' +
                 self._scope_predicate('attr') + ' '
                 'RETURN node_versions + relation_versions + '
                 'collect(DISTINCT attr.version_id) AS version_ids')
        with self._lock, self._session() as session:
            result = session.run(query, **params)
            row = result.single() if hasattr(result, 'single') else next(iter(result), None)
        return list(dict.fromkeys(
            item for item in (row.get('version_ids', []) if row else []) if item))


def install(app, service):
    projection = Neo4jProjection(service.repository)
    app.state.neo4j = projection
    app.state.jobs.handlers['neo4j_sync'] = (
        lambda payload, project_id, progress, task_id:
        app.state.projection_sync.rebuild(project_id, 'neo4j'))

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
        return app.state.jobs.enqueue('neo4j_sync', {'sync': True}, project_id)
