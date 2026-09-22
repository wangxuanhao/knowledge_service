"""断言（审核候选）存储域：表结构、校验与生命周期。

断言记录抽取结果与人工审核决定（pending → accepted/rejected/superseded），
带 decision_version 做乐观并发控制；每次状态变更追加一条 assertion_events。
"""
import json
from uuid import uuid4

from ..utils.assertions import ALLOWED_TRANSITIONS, ASSERTION_KINDS, ASSERTION_STATUSES
from ..core.time import utc_now
from .core import _json


class AssertionStore:
    """断言及其事件账本；与 Repository 共享同一连接和锁。"""

    def __init__(self, repo):
        self.repo = repo
        self._db = repo._db
        self._lock = repo._lock

    # ------------------------------------------------------------------ 行转换
    @staticmethod
    def _assertion(row):
        item = dict(row)
        item['payload'] = json.loads(item['payload'])
        return item

    @staticmethod
    def _validate_assertion(item):
        if not isinstance(item, dict):
            raise ValueError('断言必须是对象')
        forbidden = {'project_id', 'status', 'canonical_record_id', 'decision_reason',
                     'decision_version', 'created_at', 'decided_at'}
        if set(item) & forbidden:
            raise ValueError('断言决策字段不能在创建时提供')
        item = json.loads(_json(item))
        if not isinstance(item.get('id'), str) or not item['id']:
            raise ValueError('缺少断言 ID')
        if item.get('kind') not in ASSERTION_KINDS:
            raise ValueError('不支持的断言类型')
        if not isinstance(item.get('payload'), dict):
            raise ValueError('断言载荷必须是对象')
        for key in ('document_id', 'document_version_id', 'chunk_id', 'source_hash', 'quote'):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise ValueError(f'{key} 必须是字符串')
        start, end = item.get('start_char'), item.get('end_char')
        if (start is None) != (end is None):
            raise ValueError('断言来源区间必须同时提供 start_char 和 end_char')
        if start is not None and (type(start) is not int or type(end) is not int or start < 0 or end <= start):
            raise ValueError('断言来源区间无效')
        actor = item.get('actor', 'system')
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError('缺少断言操作者')
        item['actor'] = actor.strip()
        return item

    # ------------------------------------------------------------------ 创建
    def create(self, project_id, item):
        with self.repo._transaction():
            self.repo.get_project(project_id)
            return self._create(project_id, item)

    def _create(self, project_id, item):
        item = self._validate_assertion(item)
        old = self._db.execute(
            'SELECT * FROM assertions WHERE id=?', (item['id'],)
        ).fetchone()
        if old is not None:
            existing = self._assertion(old)
            comparable = {
                key: existing.get(key) for key in (
                    'id', 'kind', 'document_id', 'document_version_id', 'chunk_id',
                    'source_hash', 'start_char', 'end_char', 'quote', 'payload')
            }
            proposed = {key: item.get(key) for key in comparable}
            if existing['project_id'] == project_id and comparable == proposed:
                return existing
            raise ValueError('断言出现 ID 冲突')
        now = utc_now()
        values = (
            item['id'], project_id, item['kind'], item.get('document_id'),
            item.get('document_version_id'), item.get('chunk_id'), item.get('source_hash'),
            item.get('start_char'), item.get('end_char'), item.get('quote'),
            _json(item['payload']), 'pending', None, None, 1, now, None, item['actor'])
        self._db.execute(
            '''INSERT INTO assertions
               (id,project_id,kind,document_id,document_version_id,chunk_id,source_hash,
                start_char,end_char,quote,payload,status,canonical_record_id,decision_reason,
                decision_version,created_at,decided_at,actor)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', values)
        self._db.execute(
            '''INSERT INTO assertion_events
               (id,assertion_id,project_id,from_status,to_status,decision_version,reason,
                actor,canonical_record_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (str(uuid4()), item['id'], project_id, None, 'pending', 1, 'created',
             item['actor'], None, now))
        return self._assertion(self._db.execute(
            'SELECT * FROM assertions WHERE id=?', (item['id'],)).fetchone())

    # ------------------------------------------------------------------ 读取
    def get(self, project_id, assertion_id):
        self.repo.get_project(project_id)
        with self._lock:
            row = self._db.execute(
                'SELECT * FROM assertions WHERE project_id=? AND id=?',
                (project_id, assertion_id)).fetchone()
        if row is None:
            raise KeyError(assertion_id)
        return self._assertion(row)

    def list(self, project_id, status=None, canonical_record_id=None, document_id=None):
        self.repo.get_project(project_id)
        if status is not None and status not in ASSERTION_STATUSES:
            raise ValueError('不支持的断言状态')
        clauses, values = ['project_id=?'], [project_id]
        for column, value in (
                ('status', status), ('canonical_record_id', canonical_record_id),
                ('document_id', document_id)):
            if value is not None:
                clauses.append(f'{column}=?')
                values.append(value)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM assertions WHERE {' AND '.join(clauses)} ORDER BY created_at,id",
                values).fetchall()
        return [self._assertion(row) for row in rows]

    def list_events(self, project_id, assertion_id=None):
        self.repo.get_project(project_id)
        sql = 'SELECT * FROM assertion_events WHERE project_id=?'
        values = [project_id]
        if assertion_id is not None:
            sql += ' AND assertion_id=?'
            values.append(assertion_id)
        sql += ' ORDER BY created_at,rowid'
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, values).fetchall()]

    def export(self, project_id):
        """导出该项目的全部断言与事件（供一致性快照）。"""
        with self._lock:
            assertions = [self._assertion(row) for row in self._db.execute(
                'SELECT * FROM assertions WHERE project_id=? ORDER BY created_at,id',
                (project_id,)).fetchall()]
            events = [dict(row) for row in self._db.execute(
                'SELECT * FROM assertion_events WHERE project_id=? ORDER BY created_at,rowid',
                (project_id,)).fetchall()]
        return assertions, events

    # ------------------------------------------------------------------ 状态机
    def _transition(self, project_id, assertion_id, expected_version, status,
                    reason, actor, canonical_record_id=None, reprocess=False):
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError('期望断言版本必须是正整数')
        if status not in ASSERTION_STATUSES:
            raise ValueError('不支持的断言状态')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('缺少断言决策原因')
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError('缺少断言操作者')
        row = self._db.execute(
            'SELECT * FROM assertions WHERE project_id=? AND id=?',
            (project_id, assertion_id)).fetchone()
        if row is None:
            raise KeyError(assertion_id)
        current = self._assertion(row)
        if current['decision_version'] != expected_version:
            raise ValueError('版本冲突：断言已变更')
        old_status = current['status']
        if old_status == 'superseded':
            raise ValueError('已被取代的断言处于终态')
        allowed = reprocess and old_status == 'rejected' and status == 'pending'
        if not allowed and status not in ALLOWED_TRANSITIONS[old_status]:
            raise ValueError(f'无效的断言状态转换：{old_status} -> {status}')
        canonical = current['canonical_record_id'] if canonical_record_id is None else canonical_record_id
        if status == 'accepted' and not canonical:
            raise ValueError('接受的断言必须关联规范记录')
        if canonical is not None and (not isinstance(canonical, str) or not canonical):
            raise ValueError('规范记录 ID 必须是非空字符串')
        if status == 'pending':
            canonical = None
        now = utc_now()
        new_version = expected_version + 1
        cursor = self._db.execute(
            '''UPDATE assertions SET status=?,canonical_record_id=?,decision_reason=?,
               decision_version=?,decided_at=?,actor=?
               WHERE project_id=? AND id=? AND decision_version=?''',
            (status, canonical, reason.strip(), new_version,
             None if status == 'pending' else now, actor.strip(), project_id,
             assertion_id, expected_version))
        if cursor.rowcount != 1:
            raise ValueError('版本冲突：断言已变更')
        self._db.execute(
            '''INSERT INTO assertion_events
               (id,assertion_id,project_id,from_status,to_status,decision_version,reason,
                actor,canonical_record_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (str(uuid4()), assertion_id, project_id, old_status, status, new_version,
             reason.strip(), actor.strip(), canonical, now))
        return self._assertion(self._db.execute(
            'SELECT * FROM assertions WHERE id=?', (assertion_id,)).fetchone())

    def transition(self, project_id, assertion_id, expected_version, status,
                   reason, actor, canonical_record_id=None):
        with self.repo._transaction():
            self.repo.get_project(project_id)
            return self._transition(
                project_id, assertion_id, expected_version, status, reason, actor,
                canonical_record_id)

    def reprocess(self, project_id, assertion_id, expected_version, reason, actor):
        with self.repo._transaction():
            self.repo.get_project(project_id)
            return self._transition(
                project_id, assertion_id, expected_version, 'pending', reason, actor,
                reprocess=True)