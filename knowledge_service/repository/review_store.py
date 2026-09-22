"""消歧审核（resolution_reviews）与合并账本（merge_operations）存储域。

实体语义相似合并进入人工审核队列：pending → merged/separate/rejected，
带 decision_version 乐观并发；合并操作本身落 merge_operations 账本（可回滚审计）。
"""
import json
from uuid import uuid4

from ..core.time import utc_now
from .core import _json


class ReviewStore:
    """消歧审核与合并账本；与 Repository 共享同一连接和锁。"""

    def __init__(self, repo):
        self.repo = repo
        self._db = repo._db
        self._lock = repo._lock

    @staticmethod
    def _resolution_review(row):
        item = dict(row)
        item['payload'] = json.loads(item['payload'])
        return item

    def _create(self, project_id, item):
        if not isinstance(item, dict):
            raise ValueError('消解审核必须是对象')
        for key in ('id', 'source_entity_id', 'candidate_entity_id'):
            if not isinstance(item.get(key), str) or not item[key]:
                raise ValueError(f'消解审核缺少 {key}')
        score = item.get('score')
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise ValueError('消解审核分数必须在 0 到 1 之间')
        payload = item.get('payload', {})
        if not isinstance(payload, dict):
            raise ValueError('消解审核载荷必须是对象')
        old = self._db.execute('SELECT * FROM resolution_reviews WHERE id=?', (item['id'],)).fetchone()
        if old is not None:
            existing = self._resolution_review(old)
            same = (existing['project_id'] == project_id and
                    all(existing[key] == item[key] for key in
                        ('source_entity_id', 'candidate_entity_id')) and
                    existing['score'] == float(score) and existing['payload'] == payload)
            if same:
                return existing
            raise ValueError('消解审核 ID 冲突')
        now = utc_now()
        self._db.execute(
            '''INSERT INTO resolution_reviews
               (id,project_id,source_entity_id,candidate_entity_id,score,status,
                decision_version,payload,reason,actor,created_at,decided_at)
               VALUES (?,?,?,?,?,'pending',1,?,?,?, ?,NULL)''',
            (item['id'], project_id, item['source_entity_id'], item['candidate_entity_id'],
             float(score), _json(payload), None, item.get('actor'), now))
        return self._resolution_review(self._db.execute(
            'SELECT * FROM resolution_reviews WHERE id=?', (item['id'],)).fetchone())

    def create(self, project_id, item):
        with self.repo._transaction():
            self.repo.get_project(project_id)
            return self._create(project_id, item)

    def list(self, project_id, status=None):
        self.repo.get_project(project_id)
        if status is not None and status not in {'pending', 'merged', 'separate', 'rejected'}:
            raise ValueError('不支持的消解审核状态')
        sql, values = 'SELECT * FROM resolution_reviews WHERE project_id=?', [project_id]
        if status is not None:
            sql += ' AND status=?'
            values.append(status)
        sql += ' ORDER BY created_at,id'
        with self._lock:
            return [self._resolution_review(row) for row in self._db.execute(sql, values).fetchall()]

    def export(self, project_id):
        """导出该项目的全部消歧审核（供一致性快照）。"""
        with self._lock:
            rows = self._db.execute(
                'SELECT * FROM resolution_reviews WHERE project_id=? ORDER BY created_at,id',
                (project_id,)).fetchall()
        return [self._resolution_review(row) for row in rows]

    def _decide(self, project_id, review_id, expected_version, decision, reason, actor):
        if decision not in {'merged', 'separate', 'rejected'}:
            raise ValueError('不支持的消解决策')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('缺少消解决策原因')
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError('缺少消解决策操作者')
        row = self._db.execute(
            'SELECT * FROM resolution_reviews WHERE project_id=? AND id=?',
            (project_id, review_id)).fetchone()
        if row is None:
            raise KeyError(review_id)
        current = self._resolution_review(row)
        if current['decision_version'] != expected_version:
            raise ValueError('版本冲突：消解审核已变更')
        if current['status'] != 'pending':
            raise ValueError('消解审核已作出决策')
        cursor = self._db.execute(
            '''UPDATE resolution_reviews SET status=?,decision_version=?,reason=?,actor=?,decided_at=?
               WHERE project_id=? AND id=? AND decision_version=?''',
            (decision, expected_version + 1, reason.strip(), actor.strip(), utc_now(),
             project_id, review_id, expected_version))
        if cursor.rowcount != 1:
            raise ValueError('版本冲突：消解审核已变更')
        return self._resolution_review(self._db.execute(
            'SELECT * FROM resolution_reviews WHERE id=?', (review_id,)).fetchone())

    def decide(self, project_id, review_id, expected_version, decision, reason, actor):
        with self.repo._transaction():
            self.repo.get_project(project_id)
            return self._decide(
                project_id, review_id, expected_version, decision, reason, actor)

    # ------------------------------------------------------------------ 合并账本
    def list_merge_operations(self, project_id):
        self.repo.get_project(project_id)
        with self._lock:
            rows = self._db.execute(
                'SELECT * FROM merge_operations WHERE project_id=? ORDER BY created_at,rowid',
                (project_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ('redirects', 'assertion_moves', 'before_state', 'after_state',
                        'expected_versions'):
                item[key] = json.loads(item[key])
            result.append(item)
        return result