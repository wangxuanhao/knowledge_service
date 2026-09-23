"""基于 SQLite 的事务性真值存储主域：项目、记录、本体、制品、FTS 检索与查询。

一次修订修正一整条稳定记录；现实世界的事实/策略需要各自独立的 ID，
修正从不拆分旧的业务区间；历史系统时间查询保留原始版本。

断言/摄取运行/消歧审核等独立生命周期域拆在 repository/ 包下的
assertions_store / ingest_store / review_store，Repository 在此组合它们并保持
方法名不变转发。
"""
import json
import hashlib
import math
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import numpy as np

from ..utils.assertions import ALLOWED_TRANSITIONS, ASSERTION_KINDS, ASSERTION_STATUSES
from ..utils.diagnostics import timed, warn_scan
from ..utils.filters import matches_filter, validate_filter
from ..utils.ingest_runs import merge_readiness, readiness
from ..core.time import normalize_time, utc_now
from ..utils.attributes import primitive_datatype


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError('载荷必须只含有限 JSON 值') from exc


class OntologyNotPublished(Exception):
    """项目已存在，但还没有任何本体版本（例如 discovery/documents 模式）。"""
    def __init__(self, project_id):
        super().__init__(project_id)
        self.project_id = project_id


def _create_initial_schema(db):
    statements = (
        '''CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS record_versions (
          project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
          version INTEGER NOT NULL, version_id TEXT NOT NULL UNIQUE,
          payload TEXT NOT NULL, recorded_at TEXT NOT NULL, superseded_at TEXT,
          PRIMARY KEY(project_id,id,version))''',
        'CREATE INDEX IF NOT EXISTS versions_project ON record_versions(project_id,recorded_at,superseded_at)',
        '''CREATE UNIQUE INDEX IF NOT EXISTS versions_current
           ON record_versions(project_id,id) WHERE superseded_at IS NULL''',
        '''CREATE TABLE IF NOT EXISTS ontologies (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
          turtle TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL,
          metadata TEXT NOT NULL DEFAULT '{}')''',
        '''CREATE TABLE IF NOT EXISTS artifacts (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT, payload TEXT NOT NULL)''',
        'CREATE TABLE IF NOT EXISTS service_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    )
    for statement in statements:
        db.execute(statement)
    ontology_columns = {row['name'] for row in db.execute('PRAGMA table_info(ontologies)').fetchall()}
    if 'metadata' not in ontology_columns:
        db.execute("ALTER TABLE ontologies ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")


def _add_vector_column(db):
    """向 `record_versions` 添加 float32 向量列（迁移 9）。

    向量过去以 JSON 数组形式存放在 `payload` 内：每条向量约 22 KB 文本，
    json.loads 会把它变成约 32 KB 的 Python float 对象列表。本列以
    小端 float32 保存同样的数值（每维度 4 字节，bge-m3 约 4 KB），
    可直接读入 numpy 数组。

    这必须做成带版本号的迁移，而不是放进 `_create_initial_schema`：
    已存在的数据库早就应用过版本 1，不会再跑一次，否则每次读取都会报
    "no such column: vector"。payload 中的副本由迁移 10 负责搬移。
    """
    columns = {row['name'] for row in db.execute('PRAGMA table_info(record_versions)').fetchall()}
    if 'vector' not in columns:
        db.execute('ALTER TABLE record_versions ADD COLUMN vector BLOB')


def _move_vectors_to_column(db):
    """把 payload 中的嵌入向量搬进 float32 列（迁移 10）。

    在启动时、迁移事务内执行一次。它必须是迁移而不是读路径上的惰性回填：
    `_transaction` 会发出 `BEGIN IMMEDIATE` 且不可重入，若在恰好处于写事务内的
    读取中执行回填，会中止调用方的工作（写入会静默消失）。放在这里还意味着
    读取方可以放心剔除 payload 副本，仍能在列中找到向量。
    """
    rows = db.execute(
        '''SELECT version_id,payload FROM record_versions
           WHERE vector IS NULL AND payload LIKE '%"embedding"%' ''').fetchall()
    moved = 0
    for row in rows:
        vector = json.loads(row['payload']).get('embedding')
        if not isinstance(vector, list) or not vector:
            continue
        db.execute(
            """UPDATE record_versions
               SET vector=?, payload=json_remove(payload,'$.embedding')
               WHERE version_id=?""",
            (vector_blob(vector), row['version_id']))
        moved += 1
    return moved


def vector_blob(vector):
    """将数值向量打包为列中存储的小端 float32 字节。"""
    return np.asarray(vector, dtype='<f4').tobytes()


def vector_array(blob):
    """把存储的字节读回为 numpy 向量（返回副本，调用方可安全写入）。"""
    return np.frombuffer(blob, dtype='<f4').copy()


def _drop_vector_column(db):
    """迁移 11：删除 record_versions.vector 列——向量已迁到 Milvus，SQLite 只存元数据。"""
    columns = {row['name'] for row in db.execute('PRAGMA table_info(record_versions)')}
    if 'vector' in columns:
        db.execute('ALTER TABLE record_versions DROP COLUMN vector')


def _create_provenance_schema(db):
    """迁移 12：创建权威版本支撑映射和不可变溯源账本。"""
    statements = (
        '''CREATE UNIQUE INDEX record_versions_provenance_fk
           ON record_versions(project_id,id,version_id)''',
        '''CREATE UNIQUE INDEX assertion_events_provenance_fk
           ON assertion_events(project_id,assertion_id,id,canonical_record_id)''',
        '''CREATE TABLE record_version_assertions (
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          record_id TEXT NOT NULL,
          record_version_id TEXT NOT NULL,
          assertion_id TEXT NOT NULL,
          assertion_event_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(project_id,record_version_id,assertion_id,assertion_event_id),
          FOREIGN KEY(project_id,record_id,record_version_id)
            REFERENCES record_versions(project_id,id,version_id) ON DELETE CASCADE,
          FOREIGN KEY(project_id,assertion_id,assertion_event_id,record_id)
            REFERENCES assertion_events(project_id,assertion_id,id,canonical_record_id)
            ON DELETE CASCADE)''',
        '''CREATE INDEX record_version_assertions_version
           ON record_version_assertions(project_id,record_version_id)''',
        '''CREATE INDEX record_version_assertions_assertion
           ON record_version_assertions(project_id,assertion_id)''',
        '''CREATE TABLE provenance_activities (
          id TEXT NOT NULL,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK(kind IN ('retrieval','answer')),
          status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled')),
          payload TEXT NOT NULL CHECK(json_valid(payload)),
          started_at TEXT NOT NULL,
          completed_at TEXT,
          UNIQUE(project_id,id))''',
        '''CREATE TABLE provenance_edges (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          activity_id TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          relation TEXT NOT NULL CHECK(relation IN (
            'considered','used','offered','cites','supported-by','decided-by',
            'extracted-from','sourced-from','processed-by')),
          target_ref TEXT NOT NULL,
          ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
          payload TEXT NOT NULL CHECK(json_valid(payload)),
          created_at TEXT NOT NULL,
          UNIQUE(project_id,activity_id,source_ref,relation,target_ref,ordinal),
          FOREIGN KEY(project_id,activity_id)
            REFERENCES provenance_activities(project_id,id) ON DELETE CASCADE)''',
        '''CREATE INDEX provenance_edges_source
           ON provenance_edges(project_id,source_ref)''',
        '''CREATE INDEX provenance_edges_target
           ON provenance_edges(project_id,target_ref)''',
        '''CREATE INDEX provenance_edges_activity
           ON provenance_edges(project_id,activity_id)''',
    )
    for statement in statements:
        db.execute(statement)

    # 旧数据只能在时间、canonical target 和唯一 accepted event 都精确一致时回填。
    # 多个接受事件意味着无法知道哪个决定产生了该版本，宁缺毋滥。
    db.execute(
        '''INSERT INTO record_version_assertions
           (project_id,record_id,record_version_id,assertion_id,assertion_event_id,created_at)
           SELECT rv.project_id,rv.id,rv.version_id,a.id,e.id,e.created_at
           FROM record_versions AS rv
           JOIN assertions AS a
             ON a.project_id=rv.project_id
            AND a.canonical_record_id=rv.id
            AND a.created_at=rv.recorded_at
           JOIN assertion_events AS e
             ON e.project_id=a.project_id
            AND e.assertion_id=a.id
            AND e.to_status='accepted'
            AND e.canonical_record_id=rv.id
           WHERE (SELECT COUNT(*)
                  FROM assertion_events AS accepted
                  WHERE accepted.project_id=a.project_id
                    AND accepted.assertion_id=a.id
                    AND accepted.to_status='accepted'
                    AND accepted.canonical_record_id=rv.id)=1''')


def _create_assertion_schema(db):
    statements = (
        '''CREATE TABLE assertions (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          kind TEXT NOT NULL,
          document_id TEXT,
          document_version_id TEXT,
          chunk_id TEXT,
          source_hash TEXT,
          start_char INTEGER,
          end_char INTEGER,
          quote TEXT,
          payload TEXT NOT NULL,
          status TEXT NOT NULL,
          canonical_record_id TEXT,
          decision_reason TEXT,
          decision_version INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          decided_at TEXT,
          actor TEXT NOT NULL)''',
        'CREATE INDEX assertions_project_status ON assertions(project_id,status,created_at)',
        'CREATE INDEX assertions_document ON assertions(project_id,document_id,document_version_id)',
        'CREATE INDEX assertions_canonical ON assertions(project_id,canonical_record_id,status)',
        '''CREATE TABLE assertion_events (
          id TEXT PRIMARY KEY,
          assertion_id TEXT NOT NULL REFERENCES assertions(id) ON DELETE CASCADE,
          project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          from_status TEXT,
          to_status TEXT NOT NULL,
          decision_version INTEGER NOT NULL,
          reason TEXT NOT NULL,
          actor TEXT NOT NULL,
          canonical_record_id TEXT,
          created_at TEXT NOT NULL)''',
        'CREATE INDEX assertion_events_assertion ON assertion_events(project_id,assertion_id,decision_version)',
    )
    for statement in statements:
        db.execute(statement)


def _create_search_schema(db):
    db.execute('''CREATE VIRTUAL TABLE record_fts USING fts5(
        project_id UNINDEXED, record_id UNINDEXED, kind UNINDEXED, text, metadata,
        tokenize='unicode61')''')
    rows = db.execute(
        '''SELECT project_id,id,payload FROM record_versions
           WHERE superseded_at IS NULL
             AND json_extract(payload,'$.kind') IN ('entity','chunk')''').fetchall()
    for row in rows:
        payload = json.loads(row['payload'])
        if payload.get('metadata', {}).get('_deleted'):
            continue
        db.execute(
            'INSERT INTO record_fts(project_id,record_id,kind,text,metadata) VALUES (?,?,?,?,?)',
            (row['project_id'], row['id'], payload['kind'], payload['text'],
             _json(payload.get('metadata', {}))))


def _create_ingest_run_schema(db):
    db.execute('''CREATE TABLE ingest_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        document_id TEXT NOT NULL,
        document_version_id TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        retry_of TEXT REFERENCES ingest_runs(id),
        status TEXT NOT NULL,
        active_stage TEXT,
        readiness TEXT NOT NULL,
        counts TEXT NOT NULL,
        failure TEXT,
        version INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(document_version_id,attempt))''')
    db.execute('CREATE INDEX ingest_runs_project_document ON ingest_runs(project_id,document_id,attempt)')
    db.execute('''CREATE TABLE ingest_stage_outputs (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        document_version_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        stage TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(document_version_id,chunk_id,stage,input_hash))''')


def _create_fact_key_schema(db):
    db.execute('''CREATE TABLE fact_keys (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        fact_key TEXT NOT NULL,
        canonical_record_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        retired_at TEXT,
        PRIMARY KEY(project_id,fact_key,created_at))''')
    db.execute('''CREATE UNIQUE INDEX fact_keys_current_key
        ON fact_keys(project_id,fact_key) WHERE retired_at IS NULL''')
    db.execute('''CREATE INDEX fact_keys_current_record
        ON fact_keys(project_id,canonical_record_id) WHERE retired_at IS NULL''')


def _create_resolution_review_schema(db):
    db.execute('''CREATE TABLE resolution_reviews (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        source_entity_id TEXT NOT NULL,
        candidate_entity_id TEXT NOT NULL,
        score REAL NOT NULL,
        status TEXT NOT NULL,
        decision_version INTEGER NOT NULL,
        payload TEXT NOT NULL,
        reason TEXT,
        actor TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT)''')
    db.execute('''CREATE INDEX resolution_reviews_project_status
        ON resolution_reviews(project_id,status,created_at)''')


def _create_merge_ledger_schema(db):
    db.execute('''CREATE TABLE merge_operations (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        operation TEXT NOT NULL,
        status TEXT NOT NULL,
        redirects TEXT NOT NULL,
        assertion_moves TEXT NOT NULL,
        before_state TEXT NOT NULL,
        after_state TEXT NOT NULL,
        expected_versions TEXT NOT NULL,
        reversal_of TEXT REFERENCES merge_operations(id),
        created_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX merge_operations_project ON merge_operations(project_id,created_at)')


def _backfill_legacy_governance(db):
    namespace_row=db.execute(
        'SELECT value FROM service_settings WHERE key=?',('storage_namespace',)).fetchone()
    namespace=namespace_row['value'] if namespace_row else str(uuid4())
    db.execute('INSERT OR IGNORE INTO service_settings VALUES (?,?)',('storage_namespace',namespace))
    rows=db.execute(
        '''SELECT project_id,id,version,version_id,payload,recorded_at
           FROM record_versions WHERE superseded_at IS NULL
             AND json_extract(payload,'$.kind') IN ('entity','relation')
           ORDER BY project_id,id''').fetchall()
    for row in rows:
        exists=db.execute(
            '''SELECT 1 FROM assertions WHERE project_id=? AND canonical_record_id=? LIMIT 1''',
            (row['project_id'],row['id'])).fetchone()
        payload=json.loads(row['payload'])
        metadata=payload.get('metadata',{})
        if not exists:
            identity=json.dumps([namespace,row['project_id'],row['id'],row['version_id']],
                ensure_ascii=False,separators=(',',':')).encode('utf-8')
            assertion_id='ast_legacy_'+hashlib.sha256(identity).hexdigest()
            actor='migration-v8';created=row['recorded_at']
            db.execute(
                '''INSERT INTO assertions
                   (id,project_id,kind,document_id,document_version_id,chunk_id,source_hash,
                    start_char,end_char,quote,payload,status,canonical_record_id,decision_reason,
                    decision_version,created_at,decided_at,actor)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'accepted',?,'legacy governance backfill',1,?,?,?)''',
                (assertion_id,row['project_id'],payload['kind'],payload.get('source_id'),
                 metadata.get('source_version_id'),metadata.get('chunk_id'),metadata.get('source_hash'),
                 metadata.get('start_char'),metadata.get('end_char'),
                 metadata.get('passage') or payload.get('text'),row['payload'],row['id'],
                 created,created,actor))
            db.execute(
                '''INSERT INTO assertion_events
                   (id,assertion_id,project_id,from_status,to_status,decision_version,reason,
                    actor,canonical_record_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (str(uuid4()),assertion_id,row['project_id'],None,'accepted',1,
                 'legacy governance backfill',actor,row['id'],created))
        if payload['kind']=='relation' and all(payload.get(key) for key in ('type','subject_id','object_id')):
            identity=[payload['type'],payload['subject_id'],payload['object_id'],
                      payload.get('valid_from'),payload.get('valid_until')]
            fact_key='fact_'+hashlib.sha256(json.dumps(identity,ensure_ascii=False,
                separators=(',',':')).encode('utf-8')).hexdigest()
            db.execute(
                '''INSERT OR IGNORE INTO fact_keys
                   (project_id,fact_key,canonical_record_id,created_at,retired_at)
                   VALUES (?,?,?,?,NULL)''',
                (row['project_id'],fact_key,row['id'],row['recorded_at']))
            mapped=db.execute(
                '''SELECT canonical_record_id FROM fact_keys
                   WHERE project_id=? AND fact_key=? AND retired_at IS NULL''',
                (row['project_id'],fact_key)).fetchone()
            if mapped and mapped['canonical_record_id']!=row['id']:
                now=utc_now();winner=mapped['canonical_record_id']
                claims=db.execute(
                    'SELECT id,status,decision_version FROM assertions WHERE project_id=? AND canonical_record_id=?',
                    (row['project_id'],row['id'])).fetchall()
                for claim in claims:
                    next_version=claim['decision_version']+1
                    db.execute(
                        '''UPDATE assertions SET canonical_record_id=?,decision_version=?,
                           decision_reason=?,actor=?,decided_at=? WHERE id=?''',
                        (winner,next_version,'legacy duplicate Fact consolidation','migration-v8',now,claim['id']))
                    db.execute(
                        '''INSERT INTO assertion_events
                           (id,assertion_id,project_id,from_status,to_status,decision_version,
                            reason,actor,canonical_record_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (str(uuid4()),claim['id'],row['project_id'],claim['status'],claim['status'],
                         next_version,'legacy duplicate Fact consolidation','migration-v8',winner,now))
                tombstone={**payload,'metadata':{**metadata,'_deleted':True,
                    'merged_into_fact':winner,'migration':'v8_fact_consolidation'}}
                db.execute(
                    '''UPDATE record_versions SET superseded_at=?
                       WHERE project_id=? AND id=? AND superseded_at IS NULL''',
                    (now,row['project_id'],row['id']))
                db.execute(
                    '''INSERT INTO record_versions
                       (project_id,id,version,version_id,payload,recorded_at,superseded_at)
                       VALUES (?,?,?,?,?,?,NULL)''',
                    (row['project_id'],row['id'],row['version']+1,str(uuid4()),_json(tombstone),now))


_SCHEMA_MIGRATIONS = (
    (1, _create_initial_schema),
    (2, _create_assertion_schema),
    (3, _create_search_schema),
    (4, _create_ingest_run_schema),
    (5, _create_fact_key_schema),
    (6, _create_resolution_review_schema),
    (7, _create_merge_ledger_schema),
    (8, _backfill_legacy_governance),
    (9, _add_vector_column),
    (10, _move_vectors_to_column),
    (11, _drop_vector_column),
    (12, _create_provenance_schema),
)


def _run_schema_migrations(db):
    # 从包命名空间读迁移表：tests 会 monkeypatch `knowledge_service.repository._SCHEMA_MIGRATIONS`
    # 来模拟旧库升级，必须让这里读到同一个对象（core.py 的模块级值经包 __init__ 导出）。
    import sys
    package = sys.modules.get(__package__)
    migrations = getattr(package, '_SCHEMA_MIGRATIONS', _SCHEMA_MIGRATIONS) if package else _SCHEMA_MIGRATIONS
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)''')
        applied = {row['version'] for row in db.execute('SELECT version FROM schema_migrations')}
        for version, migration in migrations:
            if version in applied:
                continue
            migration(db)
            db.execute(
                'INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)',
                (version, utc_now()),
            )
        db.commit()
    except BaseException:
        db.rollback()
        raise


class Repository:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA foreign_keys=ON')
        _run_schema_migrations(self._db)
        from .assertions_store import AssertionStore
        from .ingest_store import IngestRunStore
        from .review_store import ReviewStore
        from .provenance_store import ProvenanceStore
        self._assertions = AssertionStore(self)
        self._ingest = IngestRunStore(self)
        self._reviews = ReviewStore(self)
        self._provenance = ProvenanceStore(self)
        with self._transaction():
            self._db.execute('INSERT OR IGNORE INTO service_settings VALUES (?,?)', ('storage_namespace', str(uuid4())))
            self._db.execute(
                '''UPDATE ingest_runs SET status='interrupted',failure=?,version=version+1,updated_at=?
                   WHERE status IN ('queued','running')''',
                (_json({'type': 'ProcessRestart', 'message': 'Ingest process restarted'}), utc_now()))

    def export_projection(self, project_id):
        """一致性的本地快照，包含历史记录；嵌入向量保持在本地。"""
        with self._transaction():
            project = self.get_project(project_id)
            namespace = self._db.execute('SELECT value FROM service_settings WHERE key=?', ('storage_namespace',)).fetchone()[0]
            rows = self._db.execute('SELECT * FROM record_versions WHERE project_id=? ORDER BY id,version', (project_id,)).fetchall()
            records = [self._record(row) for row in rows]
            for record in records:
                record.pop('embedding', None)
            assertions, assertion_events = self._assertions.export(project_id)
            ingest_runs = self._ingest.export(project_id)
            resolution_reviews = self._reviews.export(project_id)
            fact_keys = [dict(row) for row in self._db.execute(
                'SELECT * FROM fact_keys WHERE project_id=? ORDER BY fact_key,created_at',(project_id,)).fetchall()]
            merge_operations = self.list_merge_operations(project_id)
            schema_version = self._db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
            return {'namespace': namespace, 'schema_version': schema_version,
                    'project': project, 'records': records,
                    'ontologies': self.list_ontologies(project_id),
                    'provenance': {
                        'record_version_assertions': self.list_record_version_assertions(project_id),
                        'activities': self.list_provenance_activities(project_id),
                        'edges': self.list_provenance_edges(project_id)},
                    'governance': {'assertions': assertions, 'assertion_events': assertion_events,
                        'fact_keys': fact_keys, 'ingest_runs': ingest_runs,
                        'resolution_reviews': resolution_reviews, 'merge_operations': merge_operations}}

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._db.in_transaction:
                savepoint='nested_'+uuid4().hex
                self._db.execute(f'SAVEPOINT {savepoint}')
                try:
                    yield
                    self._db.execute(f'RELEASE SAVEPOINT {savepoint}')
                except BaseException:
                    self._db.execute(f'ROLLBACK TO SAVEPOINT {savepoint}')
                    self._db.execute(f'RELEASE SAVEPOINT {savepoint}')
                    raise
                return
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def close(self):
        with self._lock:
            self._db.close()

    def store_embeddings(self, project_id, rows, vectors, model):
        """向量已迁到 Milvus；保留为兼容 no-op（向量由 _sync_milvus / rebuild 写 Milvus）。

        向量不再存 SQLite（vector 列已在迁移 11 删除），此方法返回 0 以兼容旧调用方。
        """
        return 0

    def create_project(self, name, metadata=None):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('缺少项目名称')
        metadata = {} if metadata is None else metadata
        if not isinstance(metadata, dict):
            raise ValueError('项目元数据必须是对象')
        project = {'id': str(uuid4()), 'name': name, 'metadata': json.loads(_json(metadata)), 'created_at': utc_now()}
        with self._transaction():
            self._db.execute('INSERT INTO projects VALUES (?,?,?,?)', (project['id'], name, _json(metadata), project['created_at']))
        return project

    def get_project(self, project_id):
        with self._lock:
            row = self._db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return {**dict(row), 'metadata': json.loads(row['metadata'])}

    def rename_project(self, project_id, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('缺少项目名称')
        with self._transaction():
            self.get_project(project_id)
            self._db.execute('UPDATE projects SET name=? WHERE id=?', (name.strip(), project_id))
        return self.get_project(project_id)

    def delete_project(self, project_id):
        with self._transaction():
            self.get_project(project_id)
            assertions_deleted = self._db.execute(
                'SELECT COUNT(*) FROM assertions WHERE project_id=?', (project_id,)
            ).fetchone()[0]
            # Delete dependants before their FK parents, preserving exact counts
            # before record/project cascades remove them.
            provenance_deleted = {
                table: self._db.execute(
                    f'DELETE FROM {table} WHERE project_id=?', (project_id,)).rowcount
                for table in ('provenance_edges', 'provenance_activities', 'record_version_assertions')
            }
            r = self._db.execute('DELETE FROM record_versions WHERE project_id=?', (project_id,))
            records_deleted = r.rowcount
            # record_fts 是 FTS5 虚拟表，无外键不参与级联，必须显式删除，
            # 否则项目删除后全文索引残留（检索已按 project_id 隔离，但数据没删干净）
            self._db.execute('DELETE FROM record_fts WHERE project_id=?', (project_id,))
            o = self._db.execute('DELETE FROM ontologies WHERE project_id=?', (project_id,))
            ontologies_deleted = o.rowcount
            a = self._db.execute('DELETE FROM artifacts WHERE project_id=?', (project_id,))
            artifacts_deleted = a.rowcount
            self._db.execute('DELETE FROM projects WHERE id=?', (project_id,))
        return {'records': records_deleted, 'ontologies': ontologies_deleted,
                'artifacts': artifacts_deleted, 'assertions': assertions_deleted,
                **provenance_deleted}

    def list_projects(self):
        with self._lock:
            rows = self._db.execute('SELECT * FROM projects ORDER BY created_at,id').fetchall()
            counts_rows = self._db.execute(
                "SELECT project_id, json_extract(payload,'$.kind') AS kind, COUNT(*) AS n "
                "FROM record_versions WHERE superseded_at IS NULL GROUP BY project_id, kind"
            ).fetchall()
        counts_map = {}
        for cr in counts_rows:
            pid, kind, n = cr['project_id'], cr['kind'], cr['n']
            if pid not in counts_map:
                counts_map[pid] = {'documents': 0, 'entities': 0, 'relations': 0,
                                   'attributes': 0, 'chunks': 0}
            key = kind + 's' if kind != 'entity' else 'entities'
            if key in counts_map[pid]:
                counts_map[pid][key] = n
        return [{**dict(row), 'metadata': json.loads(row['metadata']),
                 'counts': counts_map.get(row['id'], {'documents': 0, 'entities': 0,
                                                       'relations': 0, 'attributes': 0,
                                                       'chunks': 0})}
                for row in rows]

    def _validate_record(self, record):
        if not isinstance(record, dict):
            raise ValueError('记录必须是对象')
        if set(record) & {'version', 'version_id', 'recorded_at', 'superseded_at', 'project_id'}:
            raise ValueError('系统版本字段不能由客户端提供')
        record = json.loads(_json(record))
        if record.get('kind') not in {'document', 'entity', 'relation', 'attribute', 'chunk'} or not isinstance(record.get('text'), str):
            raise ValueError('记录需要受支持的 kind 和字符串文本')
        record.setdefault('id', str(uuid4()))
        if not isinstance(record['id'], str) or not record['id']:
            raise ValueError('记录 ID 必须是非空字符串')
        for key in ('type', 'source_id', 'subject_id', 'object_id', 'embedding_model',
                    'ontology_id', 'datatype'):
            if record.get(key) is not None and not isinstance(record[key], str):
                raise ValueError(f'{key} 必须是字符串')
        record.setdefault('metadata', {})
        if not isinstance(record['metadata'], dict) or ('properties' in record and not isinstance(record['properties'], dict)):
            raise ValueError('元数据和属性必须是对象')
        if record['kind'] == 'attribute':
            if (not record.get('subject_id') or not record.get('type') or
                    record.get('value') is None or not record.get('datatype')):
                raise ValueError('属性记录需要 subject_id、type、value 和 datatype')
            if record['datatype'] != primitive_datatype(record['value']):
                raise ValueError('属性 datatype 必须与值的原始类型一致')
        for key in ('valid_from', 'valid_until'):
            record[key] = normalize_time(record.get(key))
        if record['valid_from'] and record['valid_until'] and record['valid_from'] >= record['valid_until']:
            raise ValueError('业务区间必须具有正时长')
        embedding = record.get('embedding')
        if embedding is not None and (not isinstance(embedding, list) or not embedding or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in embedding)):
            raise ValueError('嵌入向量必须是非空有限数值列表')
        return record

    def _put(self, project_id, record, expected_version=None, recorded_at=None):
        record = self._validate_record(record)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise ValueError('期望版本必须是非负整数')
        old = self._db.execute('SELECT version,recorded_at FROM record_versions WHERE project_id=? AND id=? AND superseded_at IS NULL', (project_id, record['id'])).fetchone()
        version = old['version'] if old else 0
        if expected_version is not None and expected_version != version:
            raise ValueError('版本冲突：记录已变更')
        now = recorded_at or utc_now()
        # 同一逻辑记录的连续版本必须具有严格递增的系统时间。若两个版本共享
        # recorded_at，旧版会在该精确时间点同时满足 recorded_at<=known_at，
        # 又因 superseded_at==known_at 被严格上界排除，导致历史查询看不到任何版本。
        # 批次内不同记录仍共享时间点；这里只规范化同一记录的修订时间。
        if old and now <= old['recorded_at']:
            now = normalize_time(datetime.fromisoformat(old['recorded_at']) + timedelta(microseconds=1))
        self._db.execute('UPDATE record_versions SET superseded_at=? WHERE project_id=? AND id=? AND superseded_at IS NULL', (now, project_id, record['id']))
        version_id = str(uuid4())
        # 向量以 float32 字节存储，不放在 payload 的 JSON 里（避免反序列化出浮点对象列表）。
        # 列清单是显式写出的，因为该表在最初的按位置 INSERT 之后新增了 `vector` 列。
        embedding = record.pop('embedding', None)  # 向量不再存 SQLite（只走 Milvus）
        self._db.execute('''INSERT INTO record_versions
            (project_id,id,version,version_id,payload,recorded_at,superseded_at)
            VALUES (?,?,?,?,?,?,NULL)''',
            (project_id, record['id'], version + 1, version_id, _json(record), now))
        self._refresh_fts_record(project_id, record)
        saved = {**record, 'project_id': project_id, 'version': version + 1, 'version_id': version_id, 'recorded_at': now, 'superseded_at': None}
        if embedding is not None:
            saved['embedding'] = embedding
        return saved

    def _refresh_fts_record(self, project_id, record):
        self._db.execute(
            'DELETE FROM record_fts WHERE project_id=? AND record_id=?',
            (project_id, record['id']))
        if record['kind'] not in {'entity', 'chunk'} or record.get('metadata', {}).get('_deleted'):
            return
        self._db.execute(
            'INSERT INTO record_fts(project_id,record_id,kind,text,metadata) VALUES (?,?,?,?,?)',
            (project_id, record['id'], record['kind'], record['text'],
             _json(record.get('metadata', {}))))

    def rebuild_fts(self, project_id=None):
        with self._transaction():
            if project_id is not None:
                self.get_project(project_id)
                self._db.execute('DELETE FROM record_fts WHERE project_id=?', (project_id,))
                rows = self._db.execute(
                    '''SELECT project_id,id,payload FROM record_versions
                       WHERE project_id=? AND superseded_at IS NULL
                         AND json_extract(payload,'$.kind') IN ('entity','chunk')''',
                    (project_id,)).fetchall()
            else:
                self._db.execute('DELETE FROM record_fts')
                rows = self._db.execute(
                    '''SELECT project_id,id,payload FROM record_versions
                       WHERE superseded_at IS NULL
                         AND json_extract(payload,'$.kind') IN ('entity','chunk')''').fetchall()
            count = 0
            for row in rows:
                payload = json.loads(row['payload'])
                if payload.get('metadata', {}).get('_deleted'):
                    continue
                self._refresh_fts_record(row['project_id'], payload)
                count += 1
            return count

    def keyword_candidates(self, project_id, query, kinds=None, limit=200, candidate_ids=None,
                           records=None):
        self.get_project(project_id)
        if not isinstance(query, str) or not query.strip():
            raise ValueError('缺少关键词查询')
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('关键词候选数量上限必须在 1 到 1000 之间')
        kinds = ['entity', 'chunk'] if kinds is None else list(kinds)
        if any(kind not in {'entity', 'chunk'} for kind in kinds):
            raise ValueError('FTS 只支持 entity 和 chunk 内容')
        if not kinds:
            return []
        phrase = '"' + query.strip().replace('"', '""') + '"'
        clauses = ['record_fts MATCH ?', 'project_id=?']
        values = [phrase, project_id]
        clauses.append('kind IN (' + ','.join('?' for _ in kinds) + ')')
        values.extend(kinds)
        if candidate_ids is not None:
            candidate_ids = list(dict.fromkeys(candidate_ids))
            if not candidate_ids:
                return []
            clauses.append('record_id IN (' + ','.join('?' for _ in candidate_ids) + ')')
            values.extend(candidate_ids)
        values.append(limit)
        with self._lock:
            hits = self._db.execute(
                f'''SELECT record_id,bm25(record_fts) AS rank FROM record_fts
                    WHERE {' AND '.join(clauses)} ORDER BY rank,record_id LIMIT ?''',
                values).fetchall()
            if not hits:
                return []
            by_id = ({row['id']: row for row in records} if records is not None else
                     {row['id']: self._record(row) for row in self._db.execute(
                         '''SELECT * FROM record_versions
                            WHERE project_id=? AND superseded_at IS NULL''', (project_id,)).fetchall()})
        return [{**by_id[row['record_id']], 'keyword_score': -float(row['rank'])}
                for row in hits if row['record_id'] in by_id]

    def list_fact_keys(self, project_id, include_retired=False):
        self.get_project(project_id)
        sql = 'SELECT * FROM fact_keys WHERE project_id=?'
        if not include_retired:
            sql += ' AND retired_at IS NULL'
        sql += ' ORDER BY fact_key,created_at'
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, (project_id,)).fetchall()]

    def put_record(self, project_id, record, expected_version=None, recorded_at=None):
        """写单条记录；``recorded_at`` 可显式指定系统时间点，默认取当前时刻。

        批次语义：同一批写入（含单文档 ingest 的全部记录）应共享同一个
        ``recorded_at``，否则 timeline 会把每条记录当成独立时间点。
        """
        with self._transaction():
            self.get_project(project_id)
            return self._put(project_id, record, expected_version, recorded_at)

    def put_batch(self, project_id, records, expected_versions=None, formal_operation=None):
        if not isinstance(records, list):
            raise ValueError('记录必须是列表')
        if formal_operation is not None:
            from ..services.formal_writes import FormalFactWriter
            result = FormalFactWriter(self).apply(formal_operation, project_id, [],
                expected_versions or {}, {'records': records})
            return result['accepted_records']
        expected_versions = {} if expected_versions is None else expected_versions
        if not isinstance(expected_versions, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in expected_versions.items()):
            raise ValueError('期望版本必须把记录 ID 映射到非负整数')
        records = [self._validate_record(record) for record in records]
        ids = {record['id'] for record in records}
        if len(ids) != len(records):
            raise ValueError('批次记录 ID 必须唯一')
        if not set(expected_versions).issubset(ids):
            raise ValueError('期望版本引用了批次外的记录')
        with self._transaction():
            self.get_project(project_id)
            latest = self._db.execute('SELECT MAX(recorded_at) FROM record_versions WHERE project_id=?', (project_id,)).fetchone()[0]
            now = utc_now()
            if latest and now <= latest:
                now = normalize_time(datetime.fromisoformat(latest) + timedelta(microseconds=1))
            return [self._put(project_id, record, expected_versions.get(record['id']), now) for record in records]

    @staticmethod
    def _record(row, vectors='none'):
        """从一行数据库记录重建一条记录。

        `vectors` 决定存储的 float32 列如何暴露：

        * ``'none'``  —— 不附带向量；payload 里残留的字段原样保留
          （仅旧版行）。所有需要 JSON 序列化记录的地方都用它，因为
          numpy 数组无法 JSON 序列化。
        * ``'list'``  —— 以普通 float 列表附带，与 payload 曾经产出的字节完全一致，
          让 `/export` 和历史查询保持现有形状。
        * ``'array'`` —— 以 numpy float32 数组附带，供相似度排序使用；
          这些调用方会先剔除该字段，因此它永远不会进入 JSON。
        """
        keys = row.keys()
        record = {**json.loads(row['payload']), **{key: row[key] for key in ('project_id', 'version', 'version_id', 'recorded_at', 'superseded_at')}}
        if vectors != 'none' and 'vector' in keys and row['vector'] is not None:
            array = vector_array(row['vector'])
            record['embedding'] = array if vectors == 'array' else array.tolist()
        return record

    def history(self, project_id, record_id):
        self.get_project(project_id)
        with self._lock:
            rows = self._db.execute('SELECT * FROM record_versions WHERE project_id=? AND id=? ORDER BY version', (project_id, record_id)).fetchall()
        return [self._record(row, vectors='list') for row in rows]

    def get_record_version(self, project_id, version_id):
        """按项目和不可变 version_id 精确读取历史版本，不回退到当前版本。"""
        self.get_project(project_id)
        with self._lock:
            row = self._db.execute(
                'SELECT * FROM record_versions WHERE project_id=? AND version_id=?',
                (project_id, version_id)).fetchone()
        if row is None:
            raise KeyError(version_id)
        return self._record(row, vectors='list')

    def current_records(self, project_id, vectors='list', kinds=None):
        """读取当前活跃版本（含已过期和未来事实）。

        ``kinds`` 把类型过滤下推到 SQL（json_extract），避免调用方在 Python 侧过滤时
        先解码所有 payload——这是"读放大"优化：查 entity 就不解码 document/chunk 的
        payload，成本与目标类型成正比而非全项目。
        """
        if kinds is not None and (not isinstance(kinds, (list, tuple, set)) or any(
                k not in {'document', 'entity', 'relation', 'attribute', 'chunk'} for k in kinds)):
            raise ValueError('无效的记录类型')
        self.get_project(project_id)
        with timed('当前记录读取', vectors=vectors, kinds=len(kinds) if kinds else 0) as stage:
            with self._lock:
                statement = ('SELECT project_id,id,version,version_id,recorded_at,superseded_at,payload '
                             'FROM record_versions WHERE project_id=? AND superseded_at IS NULL')
                params = [project_id]
                if kinds:
                    statement += " AND json_extract(payload,'$.kind') IN (%s)" % ','.join('?' * len(kinds))
                    params.extend(kinds)
                statement += ' ORDER BY id'
                rows = self._db.execute(statement, tuple(params)).fetchall()
            stage['rows'] = len(rows)
            warn_scan('当前记录读取', len(rows))
            records = [self._record(row, vectors=vectors) for row in rows]
        return records

    def timeline_events(self, project_id, limit=100):
        """返回真实的系统时间变更点，而不是人为划分的时间桶。"""
        self.get_project(project_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('时间线事件数量上限必须在 1 到 200 之间')
        with self._lock:
            total = self._db.execute(
                'SELECT COUNT(DISTINCT recorded_at) FROM record_versions WHERE project_id=?',
                (project_id,)).fetchone()[0]
            rows = self._db.execute('''
                SELECT recorded_at, COUNT(*) AS changed,
                       SUM(CASE WHEN version=1 THEN 1 ELSE 0 END) AS created,
                       SUM(CASE WHEN version>1 THEN 1 ELSE 0 END) AS revised
                FROM record_versions WHERE project_id=?
                GROUP BY recorded_at ORDER BY recorded_at DESC LIMIT ?
            ''', (project_id, limit)).fetchall()
        events = [{'known_at': row['recorded_at'], 'changed': row['changed'],
                   'created': row['created'], 'revised': row['revised']} for row in reversed(rows)]
        return {'events': events, 'total': total, 'truncated': total > len(events)}

    def query(self, project_id, filters=None, valid_at=None, known_at=None, kinds=None,
              include_unknown=True, include_embeddings=True, vectors='list'):
        """读取 ``known_at`` 时刻可见的每个系统版本，并在 Python 侧过滤。

        PERF：下面的语句只下推了 *系统时间* 条件。``kinds``、业务有效性和
        元数据谓词都在行解码之后求值，因此工作量是 O(项目版本历史) 而不是
        O(结果集)。只需要一部分数据的调用方（图邻域、选项列表、单个元数据
        分面）仍要付出全量扫描的成本。若需要加快读取速度，按顺序可用的手段：

        1. 用 ``json_extract(payload,'$.kind') IN (...)`` 下推 ``kinds``；
        2. 除非调用方要按向量排序，否则保持 ``include_embeddings=False``，
           因为解码带向量的 payload 远比不带向量的贵；
        3. 只取前 N 行的调用方加 ``LIMIT``。

        嵌入被包含时 ``vectors`` 决定表示形式：``'list'``（默认）保持历史
        的 JSON 友好 float 列表；``'array'`` 返回从紧凑列读出的 numpy float32
        数组。只有相似度排序用 ``'array'``；任何要 JSON 序列化记录的调用方
        必须停留在 ``'list'`` 或干脆省略向量。
        """
        validate_filter(filters)
        now = utc_now()
        valid_at = normalize_time(valid_at) or now
        known_at = normalize_time(known_at) or now
        if kinds is not None and (not isinstance(kinds, (list, tuple, set)) or any(k not in {'document', 'entity', 'relation', 'attribute', 'chunk'} for k in kinds)):
            raise ValueError('无效的记录类型')
        self.get_project(project_id)
        with timed('存储查询', embeddings=include_embeddings,
                   kinds=len(kinds) if kinds else 0, filtered=bool(filters)) as stage:
            with self._lock:
                # 读取 payload 时总是剔除嵌入向量：数值改从 float32 列取，
                # 这样 `json.loads` 永远不会构造出 float 对象列表。
                # 迁移 10 已把每个 payload 副本搬进该列，因此下面的读取
                # 不会出现拿不到向量的情况。
                statement = '''SELECT project_id,id,version,version_id,recorded_at,superseded_at,
                    payload
                    FROM record_versions WHERE project_id=? AND recorded_at<=?
                    AND (superseded_at IS NULL OR superseded_at>?)'''
                params = [project_id, known_at, known_at]
                # `kinds` 现在总是下推：json_extract 在 470 条记录的项目上约耗 78 ms，
                # 但能省掉每个被排除行上的 json_remove 工作，实测解码量从
                # 3.55 MB 降到 1.20 MB、耗时从 119 ms 降到 98 ms。
                # chunk 密集的项目最多可借此跳过 44% 的行。
                if kinds:
                    statement += " AND json_extract(payload,'$.kind') IN (%s)" % ','.join('?' * len(kinds))
                    params.extend(kinds)
                rows = self._db.execute(statement + ' ORDER BY id,version', tuple(params)).fetchall()
            stage['rows'] = len(rows)
            warn_scan('存储查询', len(rows))
            result = []
            for row in rows:
                record = self._record(row, vectors=vectors if include_embeddings else 'none')
                start, end = record['valid_from'], record['valid_until']
                if (not include_unknown and start is None) or (start is not None and start > valid_at) or (end is not None and end <= valid_at):
                    continue
                if kinds is not None and record['kind'] not in kinds:
                    continue
                if matches_filter(record, filters):
                    result.append(record)
            stage['kept'] = len(result)
        return result

    def get_record(self, project_id, record_id, valid_at=None, known_at=None):
        records = self.query(project_id, {'field': 'id', 'op': 'eq', 'value': record_id}, valid_at, known_at)
        if not records:
            raise KeyError(record_id)
        return records[0]

    @property
    def storage_namespace(self):
        with self._lock:
            return self._db.execute(
                'SELECT value FROM service_settings WHERE key=?', ('storage_namespace',)
            ).fetchone()[0]

    # ------------------------------------------------------------------ 断言（转发到 AssertionStore）
    def _create_assertion(self, project_id, item):
        # 供 FormalFactWriter 等内部调用方在已有事务内直接落库（跳过公开方法的独立事务）
        return self._assertions._create(project_id, item)

    def _transition_assertion(self, project_id, assertion_id, expected_version, status,
                              reason, actor, canonical_record_id=None, reprocess=False):
        return self._assertions._transition(
            project_id, assertion_id, expected_version, status, reason, actor,
            canonical_record_id, reprocess)

    def create_assertion(self, project_id, item):
        return self._assertions.create(project_id, item)

    def get_assertion(self, project_id, assertion_id):
        return self._assertions.get(project_id, assertion_id)

    def list_assertions(self, project_id, status=None, canonical_record_id=None, document_id=None):
        return self._assertions.list(project_id, status, canonical_record_id, document_id)

    def transition_assertion(self, project_id, assertion_id, expected_version, status,
                             reason, actor, canonical_record_id=None):
        return self._assertions.transition(
            project_id, assertion_id, expected_version, status, reason, actor, canonical_record_id)

    def reprocess_assertion(self, project_id, assertion_id, expected_version, reason, actor):
        return self._assertions.reprocess(project_id, assertion_id, expected_version, reason, actor)

    def list_assertion_events(self, project_id, assertion_id=None):
        return self._assertions.list_events(project_id, assertion_id)

    # ------------------------------------------------------------------ 溯源账本（转发到 ProvenanceStore）
    def _insert_record_version_assertion(self, project_id, record_id, record_version_id,
                                         assertion_id, assertion_event_id, created_at=None):
        return self._provenance._insert_mapping(
            project_id, record_id, record_version_id, assertion_id,
            assertion_event_id, created_at)

    def add_record_version_assertion(self, project_id, record_id, record_version_id,
                                     assertion_id, assertion_event_id, created_at=None):
        return self._provenance.insert_mapping(
            project_id, record_id, record_version_id, assertion_id,
            assertion_event_id, created_at)

    def list_record_version_assertions(self, project_id, record_version_id=None,
                                       assertion_id=None):
        return self._provenance.list_mappings(
            project_id, record_version_id=record_version_id, assertion_id=assertion_id)

    def begin_provenance_activity(self, project_id, activity_id, kind, payload,
                                  started_at=None):
        return self._provenance.begin_activity(
            project_id, activity_id, kind, payload, started_at)

    def get_provenance_activity(self, project_id, activity_id):
        return self._provenance.get_activity(project_id, activity_id)

    def list_provenance_activities(self, project_id, kind=None, status=None):
        return self._provenance.list_activities(
            project_id, kind=kind, status=status)

    def complete_provenance_activity(self, project_id, activity_id, payload, edges=(),
                                     completed_at=None):
        return self._provenance.complete_activity(
            project_id, activity_id, payload, edges, completed_at)

    def fail_provenance_activity(self, project_id, activity_id, payload, edges=(),
                                 completed_at=None):
        return self._provenance.fail_activity(
            project_id, activity_id, payload, edges, completed_at)

    def cancel_provenance_activity(self, project_id, activity_id, payload, edges=(),
                                   completed_at=None):
        return self._provenance.cancel_activity(
            project_id, activity_id, payload, edges, completed_at)

    def list_provenance_edges(self, project_id, activity_id=None, source_ref=None,
                              target_ref=None):
        return self._provenance.list_edges(
            project_id, activity_id=activity_id, source_ref=source_ref,
            target_ref=target_ref)

    def migrate_legacy_answer_graph(
            self, project_id, *, answer_id, retrieval_id, expected_payload,
            answer_payload, answer_edges, retrieval_edges):
        return self._provenance.migrate_legacy_answer_graph(
            project_id, answer_id=answer_id, retrieval_id=retrieval_id,
            expected_payload=expected_payload, answer_payload=answer_payload,
            answer_edges=answer_edges, retrieval_edges=retrieval_edges)

    def complete_retrieval_and_begin_answer(
            self, project_id, *, retrieval_id, answer_id, retrieval_payload=None,
            answer_payload=None, edges=None, completed_at=None, answer_started_at=None,
            snapshot_factory=None):
        return self._provenance.complete_retrieval_and_begin_answer(
            project_id, retrieval_id=retrieval_id, answer_id=answer_id,
            retrieval_payload=retrieval_payload, answer_payload=answer_payload,
            edges=edges, completed_at=completed_at,
            answer_started_at=answer_started_at, snapshot_factory=snapshot_factory)

    # ------------------------------------------------------------------ 摄取运行（转发到 IngestRunStore）
    def create_ingest_run(self, project_id, document_id, document_version_id, retry_of=None):
        return self._ingest.create(project_id, document_id, document_version_id, retry_of)

    def get_ingest_run(self, project_id, run_id):
        return self._ingest.get(project_id, run_id)

    def list_ingest_runs(self, project_id, document_id=None):
        return self._ingest.list(project_id, document_id)

    def update_ingest_run(self, project_id, run_id, expected_version, *, status=None,
                          active_stage=None, readiness_patch=None, counts_patch=None,
                          failure=None):
        return self._ingest.update(
            project_id, run_id, expected_version, status=status, active_stage=active_stage,
            readiness_patch=readiness_patch, counts_patch=counts_patch, failure=failure)

    def store_ingest_stage_output(self, project_id, document_version_id, chunk_id,
                                  stage, input_hash, payload):
        return self._ingest.store_stage_output(
            project_id, document_version_id, chunk_id, stage, input_hash, payload)

    def get_ingest_stage_output(self, document_version_id, chunk_id, stage, input_hash):
        return self._ingest.get_stage_output(document_version_id, chunk_id, stage, input_hash)

    # ------------------------------------------------------------------ 消歧审核（转发到 ReviewStore）
    def _create_resolution_review(self, project_id, item):
        # 供 FormalFactWriter 等内部调用方在已有事务内直接落库
        return self._reviews._create(project_id, item)

    def _decide_resolution_review(self, project_id, review_id, expected_version, decision, reason, actor):
        return self._reviews._decide(
            project_id, review_id, expected_version, decision, reason, actor)

    def create_resolution_review(self, project_id, item):
        return self._reviews.create(project_id, item)

    def list_resolution_reviews(self, project_id, status=None):
        return self._reviews.list(project_id, status)

    def decide_resolution_review(self, project_id, review_id, expected_version, decision, reason, actor):
        return self._reviews.decide(
            project_id, review_id, expected_version, decision, reason, actor)

    def list_merge_operations(self, project_id):
        return self._reviews.list_merge_operations(project_id)

    # ------------------------------------------------------------------ 本体与制品（主存储内）
    def save_ontology(self, project_id, turtle, summary, metadata=None):
        metadata = {} if metadata is None else metadata
        if not isinstance(turtle, str) or not isinstance(summary, dict) or not isinstance(metadata, dict):
            raise ValueError('本体需要 Turtle 文本和摘要对象')
        item = {'id': str(uuid4()), 'project_id': project_id, 'turtle': turtle,
                'summary': json.loads(_json(summary)), 'created_at': utc_now(),
                'metadata': json.loads(_json(metadata))}
        with self._transaction():
            self.get_project(project_id)
            self._db.execute('''INSERT INTO ontologies
                (id,project_id,turtle,summary,created_at,metadata) VALUES (?,?,?,?,?,?)''',
                (item['id'], project_id, turtle, _json(summary), item['created_at'], _json(metadata)))
        return item

    def publish_ontology_draft(self, project_id, draft, expected_parent_id):
        """原子地发布发现草案及其不可变的本体版本。"""
        if not isinstance(draft, dict) or draft.get('project_id') != project_id:
            raise ValueError('本体发现草案不属于此项目')
        summary = draft.get('summary')
        metadata = draft.get('ontology_metadata', {})
        if not isinstance(draft.get('turtle'), str) or not isinstance(summary, dict) or not isinstance(metadata, dict):
            raise ValueError('本体发现草案不完整')
        ontology = {'id': str(uuid4()), 'project_id': project_id, 'turtle': draft['turtle'],
                    'summary': json.loads(_json(summary)), 'created_at': utc_now(),
                    'metadata': json.loads(_json(metadata))}
        published = {**draft, 'status': 'published', 'revision': draft.get('revision', 1) + 1,
                     'published_at': utc_now(), 'ontology_id': ontology['id'],
                     'mapped_entities': 0, 'mapped_relations': 0, 'mapped_attributes': 0,
                     'requires_controlled_reingest': False}
        with self._transaction():
            self.get_project(project_id)
            latest = self._db.execute(
                'SELECT id FROM ontologies WHERE project_id=? ORDER BY rowid DESC LIMIT 1',
                (project_id,)).fetchone()
            latest_id = latest['id'] if latest else None
            if latest_id != expected_parent_id:
                raise ValueError('版本冲突：本体已更新，请基于当前版本重新生成发现草案')
            self._db.execute('''INSERT INTO ontologies
                (id,project_id,turtle,summary,created_at,metadata) VALUES (?,?,?,?,?,?)''',
                (ontology['id'], project_id, ontology['turtle'], _json(ontology['summary']),
                 ontology['created_at'], _json(ontology['metadata'])))
            self._db.execute(
                'INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                (published['id'], 'ontology_discovery_draft', project_id, _json(published)))
        return ontology, published

    def commit_ontology_change(self, project_id, ontology, proposal, document, expected_document_version,
                               expected_ontology_id):
        """原子地发布本体版本、提案决策和候选实体复核结果。"""
        if not all(isinstance(item, dict) for item in (ontology, proposal, document)):
            raise ValueError('本体变更提交需要对象形式的载荷')
        with self._transaction():
            self.get_project(project_id)
            latest = self._db.execute('SELECT id FROM ontologies WHERE project_id=? ORDER BY rowid DESC LIMIT 1',
                                      (project_id,)).fetchone()
            if not latest or latest['id'] != expected_ontology_id:
                raise ValueError('版本冲突：本体已更新，请重新评估草案影响')
            self._db.execute('''INSERT INTO ontologies
                (id,project_id,turtle,summary,created_at,metadata) VALUES (?,?,?,?,?,?)''',
                (ontology['id'], project_id, ontology['turtle'], _json(ontology['summary']),
                 ontology['created_at'], _json(ontology.get('metadata', {}))))
            saved_document = self._put(project_id, document, expected_document_version)
            self._db.execute('INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                             (proposal['id'], 'ontology_change', project_id, _json(proposal)))
        return ontology, saved_document

    def list_ontologies(self, project_id):
        self.get_project(project_id)
        with self._lock:
            rows = self._db.execute('SELECT * FROM ontologies WHERE project_id=? ORDER BY rowid', (project_id,)).fetchall()
        return [{**dict(row), 'summary': json.loads(row['summary']),
                 'metadata': json.loads(row['metadata'] or '{}')} for row in rows]

    def get_ontology(self, project_id, ontology_id=None):
        items = self.list_ontologies(project_id)
        for item in reversed(items):
            if ontology_id is None or item['id'] == ontology_id:
                return item
        if ontology_id is None:
            raise OntologyNotPublished(project_id)
        raise KeyError(ontology_id)

    def save_artifact(self, kind, item):
        with self._transaction():
            if item.get('project_id'): self.get_project(item['project_id'])
            self._db.execute('INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                             (item['id'], kind, item.get('project_id'), _json(item)))
        return item

    def get_artifact(self, kind, artifact_id):
        with self._lock:
            row = self._db.execute('SELECT payload FROM artifacts WHERE id=? AND kind=?', (artifact_id, kind)).fetchone()
        if not row: raise KeyError(artifact_id)
        return json.loads(row[0])

    def list_artifacts(self, kind, project_id=None):
        """列出某一类型的工件，可按项目收窄范围。

        PERF：项目过滤已下推到 SQL。过去它是在每个 payload 都完成 JSON 解码
        之后于 Python 侧执行的，而 ``ontology_discovery_draft`` 的 payload
        内嵌了完整的 ``candidate_snapshot``，因此查询单个项目会解码其他所有
        项目的草案再丢弃（实测：解码 5.78 MB 只保留 0.06 MB）。未带项目写入的
        工件列仍为 NULL，项目范围的调用照旧排除它们，行为与之前完全一致。
        """
        with timed('工件列表', kind=kind, scoped=project_id is not None) as record:
            with self._lock:
                if project_id is None:
                    rows = self._db.execute('SELECT payload FROM artifacts WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
                else:
                    rows = self._db.execute('SELECT payload FROM artifacts WHERE kind=? AND project_id=? ORDER BY rowid DESC', (kind, project_id)).fetchall()
            record['decoded'] = len(rows)
            kept = [json.loads(row[0]) for row in rows]
            record['kept'] = len(kept)
        return kept
