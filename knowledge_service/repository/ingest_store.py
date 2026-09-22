"""摄取运行（ingest_runs）存储域：文档解析任务的运行状态与阶段输出。

每次文档解析是一个 run：带 attempt 重试语义、readiness 就绪位、counts 计数，
与 ingestion 阶段输出的确定性键去重（ingest_stage_outputs）。
"""
import json
from uuid import uuid4

from ..utils.ingest_runs import merge_readiness, readiness
from ..core.time import utc_now
from .core import _json


class IngestRunStore:
    """摄取运行与阶段输出；与 Repository 共享同一连接和锁。"""

    def __init__(self, repo):
        self.repo = repo
        self._db = repo._db
        self._lock = repo._lock

    @staticmethod
    def _ingest_run(row):
        item = dict(row)
        item['readiness'] = json.loads(item['readiness'])
        item['counts'] = json.loads(item['counts'])
        item['failure'] = json.loads(item['failure']) if item['failure'] else None
        return item

    def create(self, project_id, document_id, document_version_id, retry_of=None):
        if not all(isinstance(value, str) and value for value in
                   (project_id, document_id, document_version_id)):
            raise ValueError('摄取运行需要文档身份标识')
        with self.repo._transaction():
            self.repo.get_project(project_id)
            source = self._db.execute(
                '''SELECT payload FROM record_versions
                   WHERE project_id=? AND id=? AND version_id=?''',
                (project_id, document_id, document_version_id)).fetchone()
            if source is None or json.loads(source['payload']).get('kind') != 'document':
                raise ValueError('摄取运行的源文档版本不存在')
            if retry_of is not None:
                prior = self._db.execute(
                    '''SELECT * FROM ingest_runs
                       WHERE id=? AND project_id=? AND document_id=? AND document_version_id=?''',
                    (retry_of, project_id, document_id, document_version_id)).fetchone()
                if prior is None:
                    raise ValueError('重试运行与其源尝试不匹配')
            attempt = self._db.execute(
                'SELECT COALESCE(MAX(attempt),0)+1 FROM ingest_runs WHERE document_version_id=?',
                (document_version_id,)).fetchone()[0]
            now = utc_now()
            run_id = str(uuid4())
            state = readiness(document_ready=True)
            counts = {'chunks_total': 0, 'chunks_succeeded': 0, 'chunks_failed': 0,
                      'assertions_pending': 0, 'records_accepted': 0}
            self._db.execute(
                '''INSERT INTO ingest_runs
                   (id,project_id,document_id,document_version_id,attempt,retry_of,status,
                    active_stage,readiness,counts,failure,version,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (run_id, project_id, document_id, document_version_id, attempt, retry_of,
                 'queued', None, _json(state), _json(counts), None, 1, now, now))
            return self._ingest_run(self._db.execute(
                'SELECT * FROM ingest_runs WHERE id=?', (run_id,)).fetchone())

    def get(self, project_id, run_id):
        self.repo.get_project(project_id)
        with self._lock:
            row = self._db.execute(
                'SELECT * FROM ingest_runs WHERE project_id=? AND id=?',
                (project_id, run_id)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._ingest_run(row)

    def list(self, project_id, document_id=None):
        self.repo.get_project(project_id)
        sql, values = 'SELECT * FROM ingest_runs WHERE project_id=?', [project_id]
        if document_id is not None:
            sql += ' AND document_id=?'
            values.append(document_id)
        sql += ' ORDER BY created_at,id'
        with self._lock:
            return [self._ingest_run(row) for row in self._db.execute(sql, values).fetchall()]

    def update(self, project_id, run_id, expected_version, *, status=None,
               active_stage=None, readiness_patch=None, counts_patch=None, failure=None):
        statuses = {'queued', 'running', 'completed', 'failed', 'interrupted'}
        if status is not None and status not in statuses:
            raise ValueError('不支持的摄取运行状态')
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError('期望摄取运行版本必须是正整数')
        if active_stage is not None and (not isinstance(active_stage, str) or not active_stage):
            raise ValueError('活动摄取阶段必须是非空字符串')
        with self.repo._transaction():
            row = self._db.execute(
                'SELECT * FROM ingest_runs WHERE project_id=? AND id=?',
                (project_id, run_id)).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = self._ingest_run(row)
            if current['version'] != expected_version:
                raise ValueError('版本冲突：摄取运行已变更')
            next_readiness = merge_readiness(current['readiness'], readiness_patch or {})
            next_counts = dict(current['counts'])
            if counts_patch is not None:
                if not isinstance(counts_patch, dict) or any(
                        not isinstance(key, str) or type(value) is not int or value < 0
                        for key, value in counts_patch.items()):
                    raise ValueError('摄取计数必须是非负整数')
                next_counts.update(counts_patch)
            next_status = status or current['status']
            next_failure = current['failure'] if failure is None else failure
            if next_status == 'completed':
                next_failure = None
            now = utc_now()
            cursor = self._db.execute(
                '''UPDATE ingest_runs SET status=?,active_stage=?,readiness=?,counts=?,failure=?,
                   version=?,updated_at=? WHERE project_id=? AND id=? AND version=?''',
                (next_status, active_stage if active_stage is not None else current['active_stage'],
                 _json(next_readiness), _json(next_counts),
                 _json(next_failure) if next_failure is not None else None,
                 expected_version + 1, now, project_id, run_id, expected_version))
            if cursor.rowcount != 1:
                raise ValueError('版本冲突：摄取运行已变更')
            return self._ingest_run(self._db.execute(
                'SELECT * FROM ingest_runs WHERE id=?', (run_id,)).fetchone())

    def export(self, project_id):
        """导出该项目的全部摄取运行（供一致性快照）。"""
        with self._lock:
            rows = self._db.execute(
                'SELECT * FROM ingest_runs WHERE project_id=? ORDER BY created_at,id',
                (project_id,)).fetchall()
        return [self._ingest_run(row) for row in rows]

    # ------------------------------------------------------------------ 阶段输出
    def store_stage_output(self, project_id, document_version_id, chunk_id,
                           stage, input_hash, payload):
        if not all(isinstance(value, str) and value for value in
                   (document_version_id, chunk_id, stage, input_hash)):
            raise ValueError('阶段输出需要确定性键')
        with self.repo._transaction():
            self.repo.get_project(project_id)
            encoded = _json(payload)
            old = self._db.execute(
                '''SELECT payload FROM ingest_stage_outputs
                   WHERE document_version_id=? AND chunk_id=? AND stage=? AND input_hash=?''',
                (document_version_id, chunk_id, stage, input_hash)).fetchone()
            if old is not None and json.loads(old['payload']) != json.loads(encoded):
                raise ValueError('阶段输出键冲突')
            self._db.execute(
                '''INSERT OR IGNORE INTO ingest_stage_outputs
                   (project_id,document_version_id,chunk_id,stage,input_hash,payload,created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (project_id, document_version_id, chunk_id, stage, input_hash, encoded, utc_now()))
        return payload

    def get_stage_output(self, document_version_id, chunk_id, stage, input_hash):
        with self._lock:
            row = self._db.execute(
                '''SELECT payload FROM ingest_stage_outputs
                   WHERE document_version_id=? AND chunk_id=? AND stage=? AND input_hash=?''',
                (document_version_id, chunk_id, stage, input_hash)).fetchone()
        return json.loads(row['payload']) if row else None