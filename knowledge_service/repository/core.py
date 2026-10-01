"""基于 PostgreSQL 的事务性真值存储主域：项目、记录、本体、制品、FTS 检索与查询。

一次修订修正一整条稳定记录；现实世界的事实/策略需要各自独立的 ID，
修正从不拆分旧的业务区间；历史系统时间查询保留原始版本。

断言/摄取运行/消歧审核等独立生命周期域拆在 repository/ 包下的
assertions_store / ingest_store / review_store，Repository 在此组合它们并保持
方法名不变转发。
"""
import json
import hashlib
import math
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np

from ..utils.assertions import ALLOWED_TRANSITIONS, ASSERTION_KINDS, ASSERTION_STATUSES
from ..utils.diagnostics import timed, warn_scan
from ..utils.filters import matches_filter, validate_filter
from ..utils.ingest_runs import merge_readiness, readiness
from ..core.time import normalize_time, utc_now
from ..utils.attributes import primitive_datatype
from .snapshot_validation import validate_restore_snapshot


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError('载荷必须只含有限 JSON 值') from exc


class _RecordOperation:
    """由 Repository 签发的单次逻辑写入上下文。

    上下文的身份而非时间字符串本身授权多阶段写入共享系统时间点，
    避免不相干的调用方仅凭相同 recorded_at 意外合并历史。
    """
    __slots__ = ('_token', '_project_id', '_recorded_at')

    def __init__(self, token, project_id, recorded_at):
        self._token = token
        self._project_id = project_id
        self._recorded_at = recorded_at

    @property
    def token(self):
        return self._token

    @property
    def project_id(self):
        return self._project_id

    @property
    def recorded_at(self):
        return self._recorded_at


class OntologyNotPublished(Exception):
    """项目已存在，但还没有任何本体版本（例如 discovery/documents 模式）。"""
    def __init__(self, project_id):
        super().__init__(project_id)
        self.project_id = project_id


class OntologyPublicationConflict(ValueError):
    """Stable conflict raised by the atomic ontology publication boundary."""

    def __init__(self, code, message, *, details=None):
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


# ────────────────────────────────────────────────────────────────────────────
# 向量列工具：仅供旧调用方兼容。
#
# 向量由 Milvus 承载；PostgreSQL 的 record_versions 没有 vector 列，`_record()`
# 里的 `'vector' in keys` 判断因此恒为假，这两个函数在运行期不再被调用；
# 保留是因为它们是 `repository/__init__.py` 的导出符号。
# ────────────────────────────────────────────────────────────────────────────
def vector_blob(vector):
    """将数值向量打包为列中存储的小端 float32 字节。"""
    return np.asarray(vector, dtype='<f4').tobytes()


def vector_array(blob):
    """把存储的字节读回为 numpy 向量（返回副本，调用方可安全写入）。"""
    return np.frombuffer(blob, dtype='<f4').copy()


class Repository:
    # 如实标识底层存储：作业日志与健康检查都从这里取文案，避免**写死**导致
    # 「数据进了 A、日志却说 B」这种误导性输出。
    backend_label = 'PostgreSQL'

    def __init__(self, dsn):
        """按 PostgreSQL DSN 打开存储。

        schema 由编号迁移（`migrate.py` + `migrations/*.sql`）负责，这里**不建表**：
        应用内自建表/自迁移已整体删除，保留会让两套建表逻辑并存，而它们必然漂移。

        传入非 PostgreSQL DSN 会立即报错，不做静默回退 —— 静默回退到本地文件
        会让人以为数据写进了 PG，实际写到了别处。
        """
        from .pg_engine import SqliteishConnection

        text = str(dsn or '').strip()
        if not text or text.startswith('sqlite') or text.endswith('.sqlite') \
                or text.endswith('.db') or text == ':memory:':
            raise ValueError(
                f'Repository 现在只接受 PostgreSQL DSN，收到 {text!r}。'
                'SQLite 后端已在 T03 移除；测试请用 tests/service/pg_support.target()。')
        self._db = SqliteishConnection(text)
        self._lock = threading.RLock()
        self._verify_schema()
        from .assertions_store import AssertionStore
        from .ingest_store import IngestRunStore
        from .review_store import ReviewStore
        from .provenance_store import ProvenanceStore
        from .ontology_draft_store import OntologyDraftStore
        from .discovery_run_store import DiscoveryRunStore
        self._assertions = AssertionStore(self)
        self._ingest = IngestRunStore(self)
        self._reviews = ReviewStore(self)
        self._provenance = ProvenanceStore(self)
        self._ontology_drafts = OntologyDraftStore(self)
        self._discovery_runs = DiscoveryRunStore(self)
        with self._transaction():
            self._db.execute(
                'INSERT INTO service_settings VALUES (?,?) ON CONFLICT (key) DO NOTHING',
                ('storage_namespace', str(uuid4())))
            self._db.execute(
                '''UPDATE ingest_runs SET status='interrupted',failure=?,version=version+1,updated_at=?
                   WHERE status IN ('queued','running')''',
                (_json({'type': 'ProcessRestart', 'message': 'Ingest process restarted'}), utc_now()))

    def _verify_schema(self):
        """确认迁移已应用到最新版本。

        没有这层检查，漏跑迁移会表现成"列不存在"之类的偶发错误，而且要等到
        某个无关用例上才暴露；在这里一次性说清楚要便宜得多。
        """
        from .migrate import discover_migrations
        expected = max((m.version for m in discover_migrations()), default=0)
        with self._db.execute(
                'SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations') as cursor:
            current = cursor.fetchone()['version']
        if current < expected:
            raise RuntimeError(
                f'schema 版本落后：库内={current}，代码期望={expected}。'
                '请先执行 `python -m knowledge_service.repository.migrate`')
        missing = self._db.execute('''
            SELECT t.name FROM (VALUES
                ('projects'),('record_versions'),('ontologies'),('artifacts'),
                ('service_settings'),('record_fts'),('ingest_runs')
            ) AS t(name)
            WHERE NOT EXISTS (
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = t.name AND n.nspname = current_schema())''').fetchall()
        if missing:
            raise RuntimeError(
                '当前 schema 缺少表：' + '、'.join(row['name'] for row in missing)
                + '。请先执行迁移。')

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
            ontology_governance = self._ontology_drafts.export(project_id)
            fact_keys = [dict(row) for row in self._db.execute(
                'SELECT * FROM fact_keys WHERE project_id=? ORDER BY fact_key,created_at',(project_id,)).fetchall()]
            merge_operations = self.list_merge_operations(project_id)
            artifacts = []
            for row in self._db.execute(
                    'SELECT id,kind,project_id,payload FROM artifacts WHERE project_id=? ORDER BY seq',
                    (project_id,)).fetchall():
                artifact = dict(row)
                artifact['payload'] = json.loads(artifact['payload'])
                artifacts.append(artifact)
            # 写**快照线格式版本**，不是数据库迁移编号 —— 见 snapshot_validation 的说明。
            # 两者编号体系不同，混用会让备份恢复整体失败。
            from .snapshot_validation import LATEST_FULL_SNAPSHOT_FORMAT
            return {'namespace': namespace, 'schema_version': LATEST_FULL_SNAPSHOT_FORMAT,
                    'governance_history_included': True,
                    'project': project, 'records': records,
                    'ontologies': self.list_ontologies(project_id),
                    'artifacts': artifacts,
                    'provenance': {
                        'record_version_assertions': self.list_record_version_assertions(project_id),
                        'activities': self.list_provenance_activities(project_id),
                        'edges': self.list_provenance_edges(project_id)},
                    'governance': {'assertions': assertions, 'assertion_events': assertion_events,
                        'fact_keys': fact_keys, 'ingest_runs': ingest_runs,
                        'resolution_reviews': resolution_reviews, 'merge_operations': merge_operations,
                        'ontology': ontology_governance}}

    def restore_projection(self, snapshot):
        """Restore a complete project snapshot while preserving stable audit IDs.

        The dependency order is deliberate: artifacts and ontology versions must
        exist before draft references; immutable draft history must exist before
        provenance can refer to it.
        """
        from .snapshot_validation import LATEST_FULL_SNAPSHOT_FORMAT
        validated = validate_restore_snapshot(snapshot, LATEST_FULL_SNAPSHOT_FORMAT)
        governance = validated['governance']
        ontology_history = validated['ontology']
        provenance = validated['provenance']
        artifacts = validated['artifacts']
        project = snapshot['project']
        project_id = validated['project_id']

        def insert_rows(table, rows, json_fields=()):
            # 列清单查 information_schema。表名走参数而不是字符串插值
            # （插值是 S03 记录在案的隐患）。
            columns = {row['column_name'] for row in self._db.execute(
                'SELECT column_name FROM information_schema.columns'
                ' WHERE table_schema = current_schema() AND table_name = ?',
                (table,))}
            for exported in rows:
                values = {key: value for key, value in exported.items() if key in columns}
                for key in json_fields:
                    if key in values and values[key] is not None:
                        values[key] = _json(values[key])
                names = list(values)
                self._db.execute(
                    f"INSERT INTO {table} ({','.join(names)}) VALUES "
                    f"({','.join('?' for _ in names)})",
                    [values[name] for name in names])

        with self._transaction():
            if self._db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
                raise ValueError('目标存储已存在同 ID 项目')
            self._db.execute(
                'INSERT INTO projects (id,name,metadata,created_at) VALUES (?,?,?,?)',
                (project_id, project['name'], _json(project.get('metadata', {})),
                 project['created_at']))

            # Artifacts and immutable ontology versions precede draft references.
            insert_rows('artifacts', artifacts, ('payload',))
            insert_rows('ontologies', snapshot.get('ontologies', ()), ('summary', 'metadata'))
            self._ontology_drafts._restore(project_id, ontology_history)

            for record in snapshot.get('records', ()):
                payload = {key: value for key, value in record.items() if key not in {
                    'project_id', 'version', 'version_id', 'recorded_at',
                    'superseded_at', 'embedding'}}
                self._db.execute(
                    '''INSERT INTO record_versions
                       (project_id,id,version,version_id,payload,recorded_at,superseded_at)
                       VALUES (?,?,?,?,?,?,?)''',
                    (project_id, record['id'], record['version'], record['version_id'],
                     _json(payload), record['recorded_at'], record.get('superseded_at')))
            if snapshot.get('records'):
                self.rebuild_fts(project_id)

            if isinstance(governance, dict):
                insert_rows('assertions', governance.get('assertions', ()), ('payload',))
                insert_rows('assertion_events', governance.get('assertion_events', ()))
                insert_rows('fact_keys', governance.get('fact_keys', ()))
                insert_rows(
                    'ingest_runs', governance.get('ingest_runs', ()),
                    ('readiness', 'counts', 'failure'))
                insert_rows(
                    'resolution_reviews', governance.get('resolution_reviews', ()),
                    ('payload',))
                insert_rows(
                    'merge_operations', governance.get('merge_operations', ()),
                    ('redirects', 'assertion_moves', 'before_state', 'after_state',
                     'expected_versions'))

            insert_rows(
                'record_version_assertions',
                provenance.get('record_version_assertions', ()))
            insert_rows('provenance_activities', provenance.get('activities', ()), ('payload',))
            insert_rows('provenance_edges', provenance.get('edges', ()), ('payload',))
            if not self._db.execute(
                    'SELECT 1 FROM projects WHERE id<>? LIMIT 1', (project_id,)).fetchone():
                namespace = snapshot.get('namespace')
                if isinstance(namespace, str) and namespace:
                    self._db.execute(
                        '''INSERT INTO service_settings(key,value) VALUES ('storage_namespace',?)
                           ON CONFLICT(key) DO UPDATE SET value=excluded.value''',
                        (namespace,))
        return self.get_project(project_id)

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
            self._db.execute('BEGIN')
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    @contextmanager
    def _allow_ontology_history_delete(self):
        """Temporarily authorize immutable-ledger deletion inside a repo transaction."""
        with self._lock:
            if not self._db.in_transaction:
                raise RuntimeError('本体治理历史只能在 Repository 事务内删除')
            # 删除开关用**事务级 GUC** 表达，由 0001 的触发器读取。GUC 随事务结束
            # 自动失效，因此不需要手工配平深度 —— 一次开、本事务内一直有效，
            # 比进程内计数器更难用错。
            self._db.execute("SET LOCAL knowledge.ontology_history_delete_allowed = '1'")
            yield

    def close(self):
        with self._lock:
            self._db.close()

    def store_embeddings(self, project_id, rows, vectors, model):
        """向量已迁到 Milvus；保留为兼容 no-op（向量由 _sync_milvus / rebuild 写 Milvus）。

        向量只走 Milvus、不存 PostgreSQL（record_versions 无 vector 列），此方法返回 0 以兼容旧调用方。
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
            ontology_governance_deleted = self._ontology_drafts.delete_counts(project_id)
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
                **provenance_deleted, **ontology_governance_deleted}

    def list_projects(self):
        with self._lock:
            rows = self._db.execute('SELECT * FROM projects ORDER BY created_at,id').fetchall()
            counts_rows = self._db.execute(
                "SELECT project_id, (payload::jsonb->>'kind') AS kind, COUNT(*) AS n "
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

    @staticmethod
    def _next_recorded_at(recorded_at):
        return normalize_time(
            datetime.fromisoformat(recorded_at.replace('Z', '+00:00'))
            + timedelta(microseconds=1))

    def _reserve_record_operation(self, project_id, recorded_at=None):
        """预留严格晚于项目高水位的逻辑操作时间点。"""
        with self._transaction():
            self.get_project(project_id)
            now = normalize_time(recorded_at) if recorded_at is not None else utc_now()
            stored_times = []
            for row in self._db.execute(
                    'SELECT recorded_at FROM record_versions WHERE project_id=?',
                    (project_id,)).fetchall():
                try:
                    stored_times.append(normalize_time(row['recorded_at'], allow_none=False))
                except ValueError:
                    # 旧库时间不属于新写入合同；保留原值，不让它阻断新高水位。
                    continue
            stored = max(stored_times, default=None)
            reserved = self._db.execute(
                '''SELECT MAX(recorded_at) FROM record_operation_reservations
                   WHERE project_id=?''', (project_id,)).fetchone()[0]
            latest = max((value for value in (stored, reserved) if value is not None),
                         default=None)
            if latest and now <= latest:
                now = self._next_recorded_at(latest)
            token = str(uuid4())
            self._db.execute(
                '''INSERT INTO record_operation_reservations(token,project_id,recorded_at)
                   VALUES (?,?,?)''', (token, project_id, now))
            return _RecordOperation(token, project_id, now)

    def _record_operation_time(self, project_id, operation):
        if not isinstance(operation, _RecordOperation) or operation.project_id != project_id:
            raise ValueError('写入操作上下文无效')
        with self._lock:
            row = self._db.execute(
                '''SELECT recorded_at FROM record_operation_reservations
                   WHERE token=? AND project_id=?''',
                (operation.token, project_id)).fetchone()
        if row is None or row['recorded_at'] != operation.recorded_at:
            raise ValueError('写入操作上下文无效')
        return row['recorded_at']

    def _put(self, project_id, record, expected_version=None, recorded_at=None,
             operation=None):
        record = self._validate_record(record)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
            raise ValueError('期望版本必须是非负整数')
        old = self._db.execute('SELECT version,recorded_at FROM record_versions WHERE project_id=? AND id=? AND superseded_at IS NULL', (project_id, record['id'])).fetchone()
        version = old['version'] if old else 0
        if expected_version is not None and expected_version != version:
            raise ValueError('版本冲突：记录已变更')
        if operation is not None and recorded_at is not None:
            raise ValueError('写入操作上下文不能与 recorded_at 同时指定')
        now = (self._record_operation_time(project_id, operation) if operation is not None
               else normalize_time(recorded_at) if recorded_at is not None else utc_now())
        old_recorded_at = None
        if old:
            try:
                old_recorded_at = normalize_time(old['recorded_at'], allow_none=False)
            except ValueError as exc:
                raise ValueError(
                    '当前记录的旧系统时间戳无法解析，无法安全修订') from exc
        # 独立修订必须具有严格递增的系统时间。若两个独立版本共享 recorded_at，
        # 旧版会在该精确时间点同时满足 recorded_at<=known_at，又因
        # superseded_at==known_at 被严格上界排除，导致历史查询看不到任何版本。
        # 只有仓储签发的操作上下文可以在同一时间点内修订记录。
        # 独立写入的相等时间仍必须推进，精确 known_at 才不会出现空洞。
        if old and operation is not None and now < old_recorded_at:
            raise ValueError('写入操作时间点已被更新的修订超过')
        if old and operation is None and now <= old_recorded_at:
            now = self._next_recorded_at(old_recorded_at)
        self._db.execute('UPDATE record_versions SET superseded_at=? WHERE project_id=? AND id=? AND superseded_at IS NULL', (now, project_id, record['id']))
        version_id = str(uuid4())
        # 向量以 float32 字节存储，不放在 payload 的 JSON 里（避免反序列化出浮点对象列表）。
        # 列清单是显式写出的，因为该表在最初的按位置 INSERT 之后新增了 `vector` 列。
        embedding = record.pop('embedding', None)  # 向量只走 Milvus，不存 PostgreSQL
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
                         AND (payload::jsonb->>'kind') IN ('entity','chunk')''',
                    (project_id,)).fetchall()
            else:
                self._db.execute('DELETE FROM record_fts')
                rows = self._db.execute(
                    '''SELECT project_id,id,payload FROM record_versions
                       WHERE superseded_at IS NULL
                         AND (payload::jsonb->>'kind') IN ('entity','chunk')''').fetchall()
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
        text = query.strip()
        # record_fts 是真表 + pg_trgm GIN，命中与打分这样设计：
        #   * 命中：子串出现（trgm 加速）或英文全文匹配（tsvector GIN）；
        #   * 打分：trgm 相似度与 ts_rank_cd 取大者，越大越相关。
        #
        # 为什么用 trgm：它按**字符 n-gram**匹配，对中文是合适的 —— 中文没有
        # 空格分词，英文全文检索的 tsvector 切不开中文，整串会被当成一个词元，
        # 只有完全相同的整串才命中；trgm 的子串匹配则能命中片段。
        #
        # 对外契约：`keyword_score` 越大越相关，`ORDER BY rank ASC` 即最优在前。
        escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f'%{escaped}%'
        clauses = [
            "(text LIKE ? ESCAPE '\\'"
            " OR to_tsvector('english', text) @@ plainto_tsquery('english', ?))",
            'project_id=?',
        ]
        values = [pattern, text, project_id]
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
            # SELECT 里的两个占位符在文本上位于 WHERE 之前，因此参数要前置。
            # `rank` 取负，保持"升序即最优"的对外契约。
            hits = self._db.execute(
                f'''SELECT record_id,
                           -greatest(similarity(text, ?),
                                     ts_rank_cd(to_tsvector('english', text),
                                                plainto_tsquery('english', ?))) AS rank
                    FROM record_fts
                    WHERE {' AND '.join(clauses)} ORDER BY rank,record_id LIMIT ?''',
                [text, text] + values).fetchall()
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

        多阶段操作由仓储签发的上下文复用时间点；普通调用无法仅凭
        相同 ``recorded_at`` 合并历史。
        """
        with self._transaction():
            self.get_project(project_id)
            return self._put(project_id, record, expected_version, recorded_at)

    def _put_record_for_operation(self, project_id, record, expected_version, operation):
        with self._transaction():
            self.get_project(project_id)
            return self._put(project_id, record, expected_version, operation=operation)

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
        operation = self._reserve_record_operation(project_id)
        with self._transaction():
            self.get_project(project_id)
            return [self._put(project_id, record, expected_versions.get(record['id']),
                              operation=operation) for record in records]

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

        ``kinds`` 把类型过滤下推到 SQL（payload::jsonb->>'kind'），避免调用方在 Python 侧过滤时
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
                    statement += " AND (payload::jsonb->>'kind') IN (%s)" % ','.join('?' * len(kinds))
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

        1. 用 ``(payload::jsonb->>'kind') IN (...)`` 下推 ``kinds``；
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
                # `kinds` 现在总是下推：jsonb 取值在 470 条记录的项目上约耗 78 ms，
                # 但能省掉每个被排除行上的 json_remove 工作，实测解码量从
                # 3.55 MB 降到 1.20 MB、耗时从 119 ms 降到 98 ms。
                # chunk 密集的项目最多可借此跳过 44% 的行。
                if kinds:
                    statement += " AND (payload::jsonb->>'kind') IN (%s)" % ','.join('?' * len(kinds))
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
    @staticmethod
    def _publish_fingerprint(value):
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(',', ':'))
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

    def _insert_ontology_version(self, project_id, turtle, summary, metadata=None,
                                 *, ontology_id=None, created_at=None):
        metadata = {} if metadata is None else metadata
        if not isinstance(turtle, str) or not isinstance(summary, dict) or not isinstance(metadata, dict):
            raise ValueError('本体需要 Turtle 文本和摘要对象')
        item = {
            'id': ontology_id or str(uuid4()), 'project_id': project_id,
            'turtle': turtle, 'summary': json.loads(_json(summary)),
            'created_at': created_at or utc_now(),
            'metadata': json.loads(_json(metadata)),
        }
        self._db.execute(
            '''INSERT INTO ontologies
               (id,project_id,turtle,summary,created_at,metadata)
               VALUES (?,?,?,?,?,?)''',
            (item['id'], project_id, turtle, _json(item['summary']),
             item['created_at'], _json(item['metadata'])))
        return item

    def bootstrap_ontology(self, project_id, turtle, summary, metadata=None):
        """Explicit administrative/bootstrap insertion outside draft governance."""
        metadata = {**(metadata or {}), 'write_path': 'bootstrap'}
        with self._transaction():
            self.get_project(project_id)
            item = self._insert_ontology_version(
                project_id, turtle, summary, metadata)
            activity_id = f"ontology-publish:bootstrap:{item['id']}"
            payload = {
                'ontology_id': item['id'], 'source_kind': 'bootstrap',
                'actor': metadata.get('actor', 'system'),
            }
            self._provenance._begin_activity(
                project_id, activity_id, 'ontology_publish', payload,
                item['created_at'])
            self._provenance._transition_activity(
                project_id, activity_id, 'completed', payload, [{
                    'activity_id': activity_id,
                    'source_ref': f"ontology-version:{item['id']}",
                    'relation': 'proposed-by',
                    'target_ref': 'source-kind:bootstrap',
                    'ordinal': 0, 'payload': {},
                }], item['created_at'])
            return item

    def save_ontology(self, project_id, turtle, summary, metadata=None):
        """Compatibility alias; production routes migrate to bootstrap/publish wrappers."""
        metadata = {**(metadata or {}), 'write_path': 'compatibility'}
        with self._transaction():
            self.get_project(project_id)
            return self._insert_ontology_version(
                project_id, turtle, summary, metadata)

    def _publish_legacy_ontology_discovery_draft(
            self, project_id, draft, expected_parent_id):
        """Compatibility transaction retained until Task 7 converts legacy artifacts."""
        if not isinstance(draft, dict) or draft.get('project_id') != project_id:
            raise ValueError('本体发现草案不属于此项目')
        summary = draft.get('summary')
        metadata = draft.get('ontology_metadata', {})
        if not isinstance(draft.get('turtle'), str) or not isinstance(summary, dict) or not isinstance(metadata, dict):
            raise ValueError('本体发现草案不完整')
        published = {**draft, 'status': 'published', 'revision': draft.get('revision', 1) + 1,
                     'published_at': utc_now(),
                     'mapped_entities': 0, 'mapped_relations': 0, 'mapped_attributes': 0,
                     'requires_controlled_reingest': False}
        with self._transaction():
            self.get_project(project_id)
            latest = self._db.execute(
                'SELECT id FROM ontologies WHERE project_id=? ORDER BY seq DESC LIMIT 1',
                (project_id,)).fetchone()
            latest_id = latest['id'] if latest else None
            if latest_id != expected_parent_id:
                raise ValueError('版本冲突：本体已更新，请基于当前版本重新生成发现草案')
            ontology = self._insert_ontology_version(
                project_id, draft['turtle'], summary,
                {**metadata, 'write_path': 'legacy_discovery'})
            published['ontology_id'] = ontology['id']
            self._db.execute(
                'INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                (published['id'], 'ontology_discovery_draft', project_id, _json(published)))
        return ontology, published

    @staticmethod
    def _ontology_source_references(context):
        if not isinstance(context, dict):
            return []
        references = context.get('documents')
        if isinstance(references, list):
            return [row for row in references if isinstance(row, dict)]
        return [context] if context.get('document_id') else []

    @staticmethod
    def _ontology_candidate(document, candidate_id):
        metadata = document.get('metadata') or {}
        candidates = [*(metadata.get('review_candidates') or []),
                      *(metadata.get('discovery_candidates') or [])]
        return next((row for row in candidates if row.get('id') == candidate_id), None)

    def _recheck_ontology_publish_source(self, project_id, draft):
        if draft['source_kind'] not in {'discovery', 'candidate'}:
            return
        for reference in self._ontology_source_references(
                draft.get('source_context') or {}):
            document_id = reference.get('document_id') or reference.get('id')
            try:
                document = self.get_record(project_id, document_id)
            except KeyError as exc:
                raise OntologyPublicationConflict(
                    'stale_source', 'ontology source document no longer exists',
                    details={'document_id': document_id}) from exc
            expected_version = reference.get(
                'expected_document_version', reference.get('version'))
            expected_version_id = reference.get(
                'expected_document_version_id', reference.get('version_id'))
            if (expected_version is not None
                    and document.get('version') != expected_version):
                raise OntologyPublicationConflict(
                    'stale_source', 'ontology source document revision changed',
                    details={'document_id': document_id})
            if (expected_version_id is not None
                    and document.get('version_id') != expected_version_id):
                raise OntologyPublicationConflict(
                    'stale_source', 'ontology source document version changed',
                    details={'document_id': document_id})
            candidate_ids = [*(reference.get('candidate_ids') or [])]
            if reference.get('candidate_id'):
                candidate_ids.append(reference['candidate_id'])
            statuses = reference.get('candidate_statuses') or {}
            fingerprints = reference.get('candidate_fingerprints') or {}
            for candidate_id in dict.fromkeys(candidate_ids):
                candidate = self._ontology_candidate(document, candidate_id)
                if candidate is None:
                    raise OntologyPublicationConflict(
                        'stale_source', 'ontology source candidate no longer exists',
                        details={'candidate_id': candidate_id})
                if (candidate_id in statuses
                        and candidate.get('status') != statuses[candidate_id]):
                    raise OntologyPublicationConflict(
                        'stale_source', 'ontology source candidate status changed',
                        details={'candidate_id': candidate_id})
                if (candidate_id in fingerprints
                        and self._publish_fingerprint(candidate)
                        != fingerprints[candidate_id]):
                    raise OntologyPublicationConflict(
                        'stale_source', 'ontology source candidate changed',
                        details={'candidate_id': candidate_id})

    def _recheck_governed_publish(self, prepared):
        project_id, draft_id = prepared['project_id'], prepared['draft_id']
        draft = self._ontology_drafts.get(project_id, draft_id)
        if draft['revision'] != prepared['expected_revision']:
            raise OntologyPublicationConflict(
                'revision_conflict', 'ontology draft revision changed',
                details={'current_revision': draft['revision']})
        if draft['status'] != 'reviewed':
            raise OntologyPublicationConflict(
                'revision_conflict', 'ontology draft is no longer reviewed')
        latest = self._db.execute(
            'SELECT id FROM ontologies WHERE project_id=? ORDER BY seq DESC LIMIT 1',
            (project_id,)).fetchone()
        latest_id = latest['id'] if latest else None
        if (draft['base_ontology_id'] != latest_id
                or prepared['expected_ontology_id'] != latest_id):
            raise OntologyPublicationConflict(
                'stale_base', 'ontology draft base is no longer current', details={
                    'base_ontology_id': draft['base_ontology_id'],
                    'current_ontology_id': latest_id})
        if (draft.get('validation_fingerprint')
                != prepared['validation_fingerprint']):
            raise OntologyPublicationConflict(
                'validation_changed', 'ontology validation fingerprint changed')
        if json.loads(_json(draft.get('validation_report'))) != json.loads(
                _json(prepared.get('validation_report'))):
            raise OntologyPublicationConflict(
                'validation_changed', 'ontology validation report changed')
        self._recheck_ontology_publish_source(project_id, draft)

        operations = [row for row in self._ontology_drafts.effective_operations(
            project_id, draft_id)
            if row['action'] != 'withdraw_operation'
            and not (row.get('validation') or {}).get('withdrawn')]
        decisions = {
            row['operation_id']: row
            for row in self._ontology_drafts.effective_decisions(project_id, draft_id)
        }
        approved = [row for row in operations
                    if decisions.get(row['id'], {}).get('action') == 'approve'
                    and decisions[row['id']]['operation_fingerprint'] == row['fingerprint']]
        expected = [(row['id'], row['fingerprint']) for row in approved]
        supplied = [(row['id'], row['fingerprint'])
                    for row in prepared.get('operations') or []]
        if supplied != expected:
            raise OntologyPublicationConflict(
                'validation_changed', 'approved ontology operation set changed')
        supplied_decisions = [(row['id'], row['operation_id'])
                              for row in prepared.get('decisions') or []]
        expected_decisions = [(decisions[row['id']]['id'], row['id'])
                              for row in approved]
        if supplied_decisions != expected_decisions:
            raise OntologyPublicationConflict(
                'validation_changed', 'ontology review decision set changed')
        return draft

    @staticmethod
    def _provenance_edge(activity_id, source_ref, relation, target_ref, ordinal,
                         payload=None):
        return {
            'activity_id': activity_id, 'source_ref': source_ref,
            'relation': relation, 'target_ref': target_ref,
            'ordinal': ordinal, 'payload': payload or {},
        }

    def _record_ontology_publish_provenance(
            self, prepared, draft, ontology, request, completed_at):
        draft_ref = f"ontology-draft:{draft['id']}"
        version_ref = f"ontology-version:{ontology['id']}"
        draft_activity = draft_ref
        publish_activity = f"ontology-publish:{request['id']}"
        draft_payload = {
            'draft_id': draft['id'], 'source_kind': draft['source_kind'],
            'base_ontology_id': draft['base_ontology_id'],
            'validation_fingerprint': prepared['validation_fingerprint'],
            'actor': prepared['actor'], 'status': 'published',
        }
        publish_payload = {
            'draft_id': draft['id'], 'ontology_id': ontology['id'],
            'request_hash': prepared['request_hash'],
            'idempotency_key': prepared['idempotency_key'],
            'actor': prepared['actor'],
        }
        self._provenance._begin_activity(
            prepared['project_id'], draft_activity, 'ontology_draft', draft_payload,
            draft['created_at'])
        self._provenance._begin_activity(
            prepared['project_id'], publish_activity, 'ontology_publish',
            publish_payload, completed_at)
        draft_edges, publish_edges = [], []
        for ordinal, (operation, decision) in enumerate(zip(
                prepared['operations'], prepared['decisions'])):
            operation_ref = f"ontology-operation:{operation['id']}"
            decision_ref = f"ontology-decision:{decision['id']}"
            draft_edges.append(self._provenance_edge(
                draft_activity, draft_ref, 'contains-operation', operation_ref,
                ordinal, {'fingerprint': operation['fingerprint'],
                          'risk': operation['risk']}))
            draft_edges.append(self._provenance_edge(
                draft_activity, operation_ref, 'decided-by', decision_ref,
                ordinal, {'actor': decision['actor'], 'reason': decision['reason'],
                          'created_at': decision['created_at']}))
            draft_edges.append(self._provenance_edge(
                draft_activity, operation_ref, 'proposed-by',
                f"source-kind:{draft['source_kind']}", ordinal,
                {'reason': operation.get('reason'),
                 'created_at': operation['created_at']}))
            for evidence_ordinal, evidence in enumerate(operation.get('evidence') or []):
                draft_edges.append(self._provenance_edge(
                    draft_activity, operation_ref, 'supported-by', evidence,
                    evidence_ordinal, {
                        'operation_fingerprint': operation['fingerprint']}))
            publish_edges.append(self._provenance_edge(
                publish_activity, version_ref, 'contains-operation', operation_ref,
                ordinal, {'fingerprint': operation['fingerprint']}))
        if draft['base_ontology_id'] is not None:
            draft_edges.append(self._provenance_edge(
                draft_activity, draft_ref, 'based-on',
                f"ontology-version:{draft['base_ontology_id']}", 0))
            publish_edges.append(self._provenance_edge(
                publish_activity, version_ref, 'based-on',
                f"ontology-version:{draft['base_ontology_id']}", 0))
        publish_edges.append(self._provenance_edge(
            publish_activity, version_ref, 'published-from', draft_ref, 0))
        self._provenance._transition_activity(
            prepared['project_id'], draft_activity, 'completed', draft_payload,
            draft_edges, completed_at)
        self._provenance._transition_activity(
            prepared['project_id'], publish_activity, 'completed', publish_payload,
            publish_edges, completed_at)

    def _apply_ontology_source_effects(self, prepared, draft, ontology):
        """Apply frozen discovery/candidate effects in the publish transaction."""
        effects = (draft.get('source_context') or {}).get('publication_effects')
        if not effects:
            return []
        if not isinstance(effects, dict):
            raise OntologyPublicationConflict(
                'stale_source', 'ontology draft publication effects are invalid')
        effect_kind = effects.get('kind')
        if (effect_kind is None and draft['source_kind'] == 'discovery'
                and effects.get('discovery_run_id')):
            effect_kind = 'discovery'
        if effect_kind != draft['source_kind']:
            raise OntologyPublicationConflict(
                'stale_source', 'ontology draft publication effects are invalid')

        project_id = prepared['project_id']
        if effect_kind == 'discovery':
            from ..services.ontology_discovery import (
                _materialize_candidates,
                _materialized_candidate_ids,
                _reuse_materialized_records,
                _validated_materialization,
            )

            run_id = effects.get('discovery_run_id')
            if not run_id:
                if 'records' not in effects:
                    raise OntologyPublicationConflict(
                        'stale_source', 'discovery publication run is missing')
                proposed = json.loads(_json(effects.get('records') or []))
                for record in proposed:
                    record['ontology_id'] = ontology['id']
                    metadata = dict(record.get('metadata') or {})
                    metadata['discovery_draft_id'] = draft['id']
                    record['metadata'] = metadata
                accepted, skipped, validation = _validated_materialization(
                    prepared['turtle'], proposed,
                    json.loads(_json(
                        effects.get('skipped_candidates') or [])))
                saved = [
                    self._put(project_id, record, 0)
                    for record in accepted]
                counts = {
                    kind: sum(row['kind'] == kind for row in saved)
                    for kind in ('entity', 'relation', 'attribute')}
                row = self._db.execute(
                    '''SELECT payload FROM artifacts
                       WHERE id=? AND kind='ontology_discovery_draft' ''',
                    (draft['id'],)).fetchone()
                if row is not None:
                    artifact = json.loads(row['payload'])
                    artifact.update({
                        'status': 'published',
                        'ontology_id': ontology['id'],
                        'mapped_entities': counts['entity'],
                        'mapped_relations': counts['relation'],
                        'mapped_attributes': counts['attribute'],
                        'skipped_candidates': skipped,
                        'validation': validation,
                        'requires_candidate_review': bool(
                            skipped
                            or artifact.get('excluded_candidate_ids')),
                        'requires_controlled_reingest': False,
                    })
                    self._db.execute(
                        '''UPDATE artifacts SET payload=?
                           WHERE id=? AND kind='ontology_discovery_draft' ''',
                        (_json(artifact), draft['id']))
                return [record['id'] for record in saved]
            try:
                run = self.get_discovery_run(project_id, run_id)
            except KeyError as exc:
                raise OntologyPublicationConflict(
                    'stale_source', 'discovery publication run does not exist') \
                    from exc
            if (run.get('status') != 'draft_created'
                    or run.get('unified_draft_id') != draft['id']):
                raise OntologyPublicationConflict(
                    'stale_source', 'discovery publication run is not current')

            approved_ids = {
                operation['id'] for operation in prepared['operations']}
            effective_ids = {
                operation['id']
                for operation in self._ontology_drafts.effective_operations(
                    project_id, draft['id'])}
            initial_ids = {
                outcome['candidate_id']
                for outcome in run.get('initial_candidate_outcomes') or []}
            required_outcomes = []
            for binding in run.get('candidate_bindings') or []:
                if binding['candidate_id'] in initial_ids:
                    continue
                missing = [
                    operation_id
                    for operation_id in binding.get(
                        'required_operation_ids') or []
                    if operation_id not in approved_ids]
                if not missing:
                    continue
                if any(operation_id in effective_ids for operation_id in missing):
                    reason_code = 'required_operation_rejected'
                elif any(self._db.execute(
                        '''SELECT 1 FROM ontology_operations
                           WHERE project_id=? AND draft_id=? AND id=?''',
                        (project_id, draft['id'], operation_id)).fetchone()
                         for operation_id in missing):
                    reason_code = 'required_operation_superseded'
                else:
                    reason_code = 'required_operation_missing'
                required_outcomes.append({
                    'candidate_id': binding['candidate_id'],
                    'status': 'skipped',
                    'reason_code': reason_code,
                })

            materialization_source = {
                **run,
                'id': draft['id'],
                'excluded_candidate_ids': [],
            }
            proposed, skipped = _materialize_candidates(
                project_id, materialization_source, ontology['id'],
                [*(run.get('candidate_outcomes') or []), *required_outcomes])
            previous_ids = set(run.get(
                'materialized_candidate_ids') or [])
            existing_records = self.current_records(
                project_id, vectors='none', kinds=['entity','relation','attribute'])
            processed_ids = _materialized_candidate_ids(existing_records)
            if (not previous_ids <= processed_ids
                    or (set(run.get('accepted_candidate_ids') or [])
                        & (processed_ids - previous_ids))):
                raise OntologyPublicationConflict(
                    'stale_source', 'discovery materialization context changed')
            proposed = _reuse_materialized_records(
                proposed, existing_records, previous_ids)
            accepted, skipped, validation = _validated_materialization(
                prepared['turtle'], proposed, skipped)
            existing_record_ids = {item['id'] for item in existing_records
                if _materialized_candidate_ids([item]) & previous_ids}
            saved = [self._put(project_id, record, 0) for record in accepted
                     if record['id'] not in existing_record_ids]
            materialized_ids = _materialized_candidate_ids(accepted)
            required_by_candidate = {
                outcome['candidate_id']: outcome
                for outcome in required_outcomes}
            terminal_outcomes = []
            for binding in run.get('candidate_bindings') or []:
                candidate_id = binding['candidate_id']
                if candidate_id in initial_ids:
                    continue
                if candidate_id in required_by_candidate:
                    terminal_outcomes.append(required_by_candidate[candidate_id])
                elif candidate_id in materialized_ids:
                    terminal_outcomes.append({
                        'candidate_id': candidate_id,
                        'status': 'materialized',
                        'reason_code': 'materialized',
                    })
                else:
                    terminal_outcomes.append({
                        'candidate_id': candidate_id,
                        'status': 'skipped',
                        'reason_code': 'ontology_validation_failed',
                    })
            self.transition_discovery_run(
                project_id, run_id, 'draft_created', 'published',
                candidate_outcomes=terminal_outcomes)
            counts = {
                kind: sum(row['kind'] == kind for row in saved)
                for kind in ('entity', 'relation', 'attribute')}
            row = self._db.execute(
                "SELECT payload FROM artifacts WHERE id=? AND kind='ontology_discovery_draft'",
                (draft['id'],)).fetchone()
            if row is not None:
                artifact = json.loads(row['payload'])
                artifact.update({
                    'status': 'published', 'ontology_id': ontology['id'],
                    'mapped_entities': counts['entity'],
                    'mapped_relations': counts['relation'],
                    'mapped_attributes': counts['attribute'],
                    'skipped_candidates': skipped, 'validation': validation,
                    'requires_candidate_review': bool(
                        skipped or artifact.get('excluded_candidate_ids')),
                    'requires_controlled_reingest': False,
                })
                self._db.execute(
                    "UPDATE artifacts SET payload=? WHERE id=? AND kind='ontology_discovery_draft'",
                    (_json(artifact), draft['id']))
            return [row['id'] for row in saved]

        if effect_kind == 'candidate':
            document = self.get_record(project_id, effects.get('document_id'))
            if document['version'] != effects.get('expected_document_version'):
                raise OntologyPublicationConflict(
                    'stale_source', 'candidate source document changed')
            revised = json.loads(_json({
                key: value for key, value in document.items()
                if key not in {
                    'project_id', 'version', 'version_id', 'recorded_at',
                    'superseded_at',
                }}))
            candidates = (revised.get('metadata') or {}).get(
                'review_candidates') or []
            candidate = next((item for item in candidates
                              if item.get('id') == effects.get('candidate_id')), None)
            if candidate is None:
                raise OntologyPublicationConflict(
                    'stale_source', 'candidate source no longer exists')
            proposal_row = self._db.execute(
                "SELECT payload FROM artifacts WHERE id=? AND kind='ontology_change'",
                (effects.get('proposal_id'),)).fetchone()
            if proposal_row is None:
                raise OntologyPublicationConflict(
                    'stale_source', 'candidate proposal no longer exists')
            proposal = json.loads(proposal_row['payload'])
            from ..services.ontology import Ontology
            from ..services.ontology_changes import revalidation

            result = revalidation(
                SimpleNamespace(repository=self), project_id, document,
                candidate, Ontology(prepared['turtle']), ontology['id'], proposal)
            candidate['ontology_change'] = result
            self._put(project_id, revised, document['version'])
            proposal.update({
                'status': 'published', 'approved_ontology_id': ontology['id'],
                'revalidation': result,
            })
            self._db.execute(
                "UPDATE artifacts SET payload=? WHERE id=? AND kind='ontology_change'",
                (_json(proposal), effects['proposal_id']))
            return [document['id']]

        if effect_kind == 'import':
            proposed = json.loads(_json(effects.get('records') or []))
            for record in proposed:
                if record.get('kind') in {'entity', 'relation', 'attribute'}:
                    record['ontology_id'] = ontology['id']
            from ..services.ontology import Ontology
            report = Ontology(prepared['turtle']).validate_timeline(proposed)
            if not report['conforms']:
                raise OntologyPublicationConflict(
                    'validation_failed', 'imported records do not conform',
                    details={'report': report})
            saved = [self._put(project_id, record, 0) for record in proposed]
            return [row['id'] for row in saved]

        raise OntologyPublicationConflict(
            'stale_source', 'unsupported ontology publication effect kind')

    def _create_ontology_sync_job(self, prepared, ontology, completed_at,
                                  record_ids=None):
        draft = prepared['draft']
        if draft['source_kind'] not in {'discovery', 'candidate', 'import'}:
            return None
        job = {
            'id': f"ontology-sync:{ontology['id']}",
            'project_id': prepared['project_id'], 'status': 'pending',
            'ontology_id': ontology['id'], 'draft_id': draft['id'],
            'fingerprint': self._publish_fingerprint({
                'ontology_id': ontology['id'],
                'source_context': draft.get('source_context') or {},
            }),
            'record_ids': list(record_ids or []),
            'created_at': completed_at, 'completed_at': None,
        }
        self._db.execute(
            '''INSERT INTO artifacts(id,kind,project_id,payload)
               VALUES (?,'ontology_sync_job',?,?)''',
            (job['id'], prepared['project_id'], _json(job)))
        return job

    def _run_ontology_sync_after_commit(self, job):
        runner = getattr(self, '_ontology_sync_runner', None)
        if job is None or not callable(runner):
            return
        try:
            runner(dict(job))
        except Exception:
            return
        completed = {**job, 'status': 'completed', 'completed_at': utc_now()}
        with self._transaction():
            self._db.execute(
                '''UPDATE artifacts SET payload=?
                   WHERE id=? AND kind='ontology_sync_job' ''',
                (_json(completed), job['id']))

    def replay_ontology_publish(self, project_id, draft_id, idempotency_key,
                                client_request_hash):
        request = self._ontology_drafts.get_publish_request(
            project_id, draft_id, idempotency_key)
        if request is None or request['result_ontology_id'] is None:
            raise OntologyPublicationConflict(
                'idempotency_conflict', 'ontology publish request does not exist')
        ontology = self.get_ontology(project_id, request['result_ontology_id'])
        publication = ontology.get('metadata', {}).get('publication', {})
        if publication.get('client_request_hash') != client_request_hash:
            raise OntologyPublicationConflict(
                'idempotency_conflict',
                'idempotency key was already used with a different request')
        return ontology

    def _publish_governed_ontology_draft(self, prepared):
        required = {
            'project_id', 'draft', 'draft_id', 'expected_revision',
            'expected_ontology_id', 'validation_fingerprint',
            'validation_report', 'operations', 'decisions', 'turtle', 'summary',
            'acknowledged_warning_codes', 'idempotency_key', 'actor',
            'request_hash', 'client_request_hash',
        }
        if missing := required - set(prepared):
            raise ValueError(f'ontology publish request is missing {sorted(missing)}')
        calculated = self._publish_fingerprint({
            key: value for key, value in prepared.items()
            if key not in {'draft', 'validation_report', 'summary', 'request_hash'}
        })
        if calculated != prepared['request_hash']:
            raise ValueError('ontology publish request hash is invalid')

        sync_job = None
        with self._transaction():
            self.get_project(prepared['project_id'])
            existing = self._ontology_drafts.get_publish_request(
                prepared['project_id'], prepared['draft_id'],
                prepared['idempotency_key'])
            if existing is not None:
                if existing['request_hash'] != prepared['request_hash']:
                    raise OntologyPublicationConflict(
                        'idempotency_conflict',
                        'idempotency key was already used with a different request')
                if existing['result_ontology_id'] is None:
                    raise OntologyPublicationConflict(
                        'idempotency_conflict',
                        'ontology publish request is incomplete; retry after recovery')
                return self.get_ontology(
                    prepared['project_id'], existing['result_ontology_id'])

            draft = self._recheck_governed_publish(prepared)
            request = self._ontology_drafts._insert_publish_request(
                prepared['project_id'], prepared['draft_id'],
                prepared['idempotency_key'], prepared['request_hash'])
            completed_at = utc_now()
            metadata = {
                'write_path': 'governed_publish',
                'publication': {
                    'draft_id': draft['id'],
                    'base_ontology_id': draft['base_ontology_id'],
                    'validation_fingerprint': prepared['validation_fingerprint'],
                    'request_hash': prepared['request_hash'],
                    'client_request_hash': prepared['client_request_hash'],
                    'actor': prepared['actor'],
                },
            }
            ontology = self._insert_ontology_version(
                prepared['project_id'], prepared['turtle'], prepared['summary'],
                metadata, created_at=completed_at)
            effect_record_ids = self._apply_ontology_source_effects(
                prepared, draft, ontology)
            self._record_ontology_publish_provenance(
                prepared, draft, ontology, request, completed_at)
            cursor = self._db.execute(
                '''UPDATE ontology_drafts
                   SET status='published',revision=revision+1,
                       published_ontology_id=?,updated_at=?
                   WHERE project_id=? AND id=? AND revision=?
                     AND status='reviewed' AND validation_fingerprint=?''',
                (ontology['id'], completed_at, prepared['project_id'],
                 prepared['draft_id'], prepared['expected_revision'],
                 prepared['validation_fingerprint']))
            if cursor.rowcount != 1:
                raise OntologyPublicationConflict(
                    'revision_conflict', 'ontology draft changed while publishing')
            self._ontology_drafts._complete_publish_request(
                request['id'], ontology['id'], completed_at)
            sync_job = self._create_ontology_sync_job(
                prepared, ontology, completed_at, effect_record_ids)
        self._run_ontology_sync_after_commit(sync_job)
        return ontology

    def publish_ontology_draft(self, project_id, draft, expected_parent_id=None,
                               **request):
        """Dispatch legacy discovery drafts or governed atomic publications."""
        if 'expected_revision' not in request:
            return self._publish_legacy_ontology_discovery_draft(
                project_id, draft, expected_parent_id)
        prepared = {'project_id': project_id, 'draft': draft, **request}
        return self._publish_governed_ontology_draft(prepared)

    def commit_ontology_change(self, project_id, ontology, proposal, document, expected_document_version,
                               expected_ontology_id):
        """原子地发布本体版本、提案决策和候选实体复核结果。"""
        if not all(isinstance(item, dict) for item in (ontology, proposal, document)):
            raise ValueError('本体变更提交需要对象形式的载荷')
        with self._transaction():
            self.get_project(project_id)
            latest = self._db.execute('SELECT id FROM ontologies WHERE project_id=? ORDER BY seq DESC LIMIT 1',
                                      (project_id,)).fetchone()
            if not latest or latest['id'] != expected_ontology_id:
                raise ValueError('版本冲突：本体已更新，请重新评估草案影响')
            self._insert_ontology_version(
                project_id, ontology['turtle'], ontology['summary'],
                {**ontology.get('metadata', {}), 'write_path': 'legacy_change'},
                ontology_id=ontology['id'], created_at=ontology['created_at'])
            saved_document = self._put(project_id, document, expected_document_version)
            self._db.execute('INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                             (proposal['id'], 'ontology_change', project_id, _json(proposal)))
        return ontology, saved_document

    def list_ontologies(self, project_id):
        self.get_project(project_id)
        with self._lock:
            rows = self._db.execute('SELECT id,project_id,turtle,summary,created_at,metadata FROM ontologies WHERE project_id=? ORDER BY seq', (project_id,)).fetchall()
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
        if kind == 'ontology_discovery_run':
            raise ValueError(
                'ontology discovery runs must use create_discovery_run or '
                'transition_discovery_run')
        with self._transaction():
            existing = self._db.execute(
                'SELECT kind FROM artifacts WHERE id=?', (item['id'],)).fetchone()
            if existing is not None and existing['kind'] == 'ontology_discovery_run':
                raise ValueError(
                    'ontology discovery runs must use create_discovery_run or '
                    'transition_discovery_run')
            if item.get('project_id'): self.get_project(item['project_id'])
            self._db.execute('INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                             (item['id'], kind, item.get('project_id'), _json(item)))
        return item

    # Discovery runs are immutable snapshots with a narrow lifecycle CAS.  Do
    # not route these methods through save_artifact, whose contract is upsert.
    def create_discovery_run(self, project_id_or_item, item=None):
        if item is None:
            item = project_id_or_item
            project_id = item.get('project_id') if isinstance(item, dict) else None
        else:
            project_id = project_id_or_item
        return self._discovery_runs.create(project_id, item)

    def get_discovery_run(self, project_id, run_id):
        return self._discovery_runs.get(project_id, run_id)

    def list_discovery_runs(self, project_id):
        return self._discovery_runs.list(project_id)

    def latest_discovery_run(self, project_id, *, statuses=None):
        return self._discovery_runs.latest(project_id, statuses=statuses)

    def transition_discovery_run(self, project_id, run_id, expected_status,
                                 new_status, **changes):
        return self._discovery_runs.transition(
            project_id, run_id, expected_status, new_status, **changes)

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
                    rows = self._db.execute('SELECT payload FROM artifacts WHERE kind=? ORDER BY seq DESC', (kind,)).fetchall()
                else:
                    rows = self._db.execute('SELECT payload FROM artifacts WHERE kind=? AND project_id=? ORDER BY seq DESC', (kind, project_id)).fetchall()
            record['decoded'] = len(rows)
            kept = [json.loads(row[0]) for row in rows]
            record['kept'] = len(kept)
        return kept
