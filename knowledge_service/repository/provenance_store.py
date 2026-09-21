"""版本支撑映射与答案溯源账本。

公开方法负责开启唯一外层事务；以下划线开头的写入原语只供已经持有
``Repository._transaction`` 的组合操作调用，绝不自行开启嵌套事务。
"""
import json
import sqlite3
from uuid import uuid4

from ..core.time import utc_now
from .core import _json


_ACTIVITY_KINDS = {'retrieval', 'answer'}
_TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}
_EDGE_RELATIONS = {
    'considered', 'used', 'offered', 'cites', 'supported-by', 'decided-by',
    'extracted-from', 'sourced-from', 'processed-by',
}


class ProvenanceStore:
    """Repository 共享连接上的 project-scoped provenance 深模块。"""

    def __init__(self, repo):
        self.repo = repo
        self._db = repo._db
        self._lock = repo._lock

    @staticmethod
    def _payload(value):
        """经统一 JSON 边界验证并返回无共享引用的 Python 值。"""
        return json.loads(_json(value))

    @staticmethod
    def _canonical_json(value):
        """生成严格 JSON 身份；bool 与 number 不混同，对象 key 顺序不敏感。"""
        normalized = json.loads(_json(value))
        return json.dumps(
            normalized, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(',', ':'))

    @classmethod
    def _strict_json_equal(cls, left, right):
        return cls._canonical_json(left) == cls._canonical_json(right)

    @staticmethod
    def _activity(row):
        item = dict(row)
        item['payload'] = json.loads(item['payload'])
        return item

    @staticmethod
    def _edge(row):
        item = dict(row)
        item['payload'] = json.loads(item['payload'])
        return item

    # ---------------------------------------------------------- 版本支撑映射
    def _insert_mapping(self, project_id, record_id, record_version_id,
                        assertion_id, assertion_event_id, created_at=None):
        values = (project_id, record_id, record_version_id, assertion_id,
                  assertion_event_id)
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError('版本支撑映射字段必须是非空字符串')
        existing = self._db.execute(
            '''SELECT * FROM record_version_assertions
               WHERE project_id=? AND record_version_id=?
                 AND assertion_id=? AND assertion_event_id=?''',
            (project_id, record_version_id, assertion_id, assertion_event_id)
        ).fetchone()
        if existing is not None:
            item = dict(existing)
            if item['record_id'] != record_id or (
                    created_at is not None and item['created_at'] != created_at):
                raise ValueError('版本支撑映射冲突')
            return item
        created_at = created_at or utc_now()
        self._db.execute(
            '''INSERT INTO record_version_assertions
               (project_id,record_id,record_version_id,assertion_id,
                assertion_event_id,created_at) VALUES (?,?,?,?,?,?)''',
            (*values, created_at))
        return dict(self._db.execute(
            '''SELECT * FROM record_version_assertions
               WHERE project_id=? AND record_version_id=?
                 AND assertion_id=? AND assertion_event_id=?''',
            (project_id, record_version_id, assertion_id, assertion_event_id)
        ).fetchone())

    def insert_mapping(self, project_id, record_id, record_version_id,
                       assertion_id, assertion_event_id, created_at=None):
        with self.repo._transaction():
            self.repo.get_project(project_id)
            return self._insert_mapping(
                project_id, record_id, record_version_id, assertion_id,
                assertion_event_id, created_at)

    def list_mappings(self, project_id, *, record_version_id=None, assertion_id=None):
        self.repo.get_project(project_id)
        clauses, values = ['project_id=?'], [project_id]
        for column, value in (
                ('record_version_id', record_version_id), ('assertion_id', assertion_id)):
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError('映射过滤字段必须是非空字符串')
                clauses.append(f'{column}=?')
                values.append(value)
        with self._lock:
            rows = self._db.execute(
                f'''SELECT * FROM record_version_assertions
                    WHERE {' AND '.join(clauses)}
                    ORDER BY record_version_id,assertion_id,assertion_event_id''',
                values).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------- 活动生命周期
    def _begin_activity(self, project_id, activity_id, kind, payload, started_at=None):
        if not isinstance(activity_id, str) or not activity_id:
            raise ValueError('缺少溯源活动 ID')
        if kind not in _ACTIVITY_KINDS:
            raise ValueError('不支持的溯源活动类型')
        payload = self._payload(payload)
        encoded = _json(payload)
        existing = self._db.execute(
            'SELECT * FROM provenance_activities WHERE project_id=? AND id=?',
            (project_id, activity_id)).fetchone()
        if existing is not None:
            item = self._activity(existing)
            same = (item['kind'] == kind and item['status'] == 'running'
                    and self._strict_json_equal(item['payload'], payload)
                    and (started_at is None or item['started_at'] == started_at))
            if same:
                return item
            raise ValueError('版本冲突：溯源活动已存在')
        started_at = started_at or utc_now()
        self._db.execute(
            '''INSERT INTO provenance_activities
               (id,project_id,kind,status,payload,started_at,completed_at)
               VALUES (?,?,?,'running',?,?,NULL)''',
            (activity_id, project_id, kind, encoded, started_at))
        return self._activity(self._db.execute(
            'SELECT * FROM provenance_activities WHERE project_id=? AND id=?',
            (project_id, activity_id)).fetchone())

    def begin_activity(self, project_id, activity_id, kind, payload, started_at=None):
        with self.repo._transaction():
            self.repo.get_project(project_id)
            return self._begin_activity(project_id, activity_id, kind, payload, started_at)

    def get_activity(self, project_id, activity_id):
        self.repo.get_project(project_id)
        with self._lock:
            row = self._db.execute(
                'SELECT * FROM provenance_activities WHERE project_id=? AND id=?',
                (project_id, activity_id)).fetchone()
        if row is None:
            raise KeyError(activity_id)
        return self._activity(row)

    def list_activities(self, project_id, *, kind=None, status=None):
        self.repo.get_project(project_id)
        clauses, values = ['project_id=?'], [project_id]
        if kind is not None:
            if kind not in _ACTIVITY_KINDS:
                raise ValueError('不支持的溯源活动类型')
            clauses.append('kind=?')
            values.append(kind)
        if status is not None:
            if status not in {'running', *_TERMINAL_STATUSES}:
                raise ValueError('不支持的溯源活动状态')
            clauses.append('status=?')
            values.append(status)
        with self._lock:
            rows = self._db.execute(
                f'''SELECT * FROM provenance_activities
                    WHERE {' AND '.join(clauses)} ORDER BY started_at,id''',
                values).fetchall()
        return [self._activity(row) for row in rows]

    # -------------------------------------------------------------------- 边
    @staticmethod
    def _validate_edge(edge):
        if not isinstance(edge, dict):
            raise ValueError('溯源边必须是对象')
        required = ('activity_id', 'source_ref', 'relation', 'target_ref', 'ordinal')
        if any(not isinstance(edge.get(key), str) or not edge[key]
               for key in required[:-1]):
            raise ValueError('溯源边引用字段必须是非空字符串')
        if edge['relation'] not in _EDGE_RELATIONS:
            raise ValueError('不支持的溯源关系')
        if type(edge.get('ordinal')) is not int or edge['ordinal'] < 0:
            raise ValueError('溯源边 ordinal 必须是非负整数')
        if edge.get('id') is not None and (
                not isinstance(edge['id'], str) or not edge['id']):
            raise ValueError('溯源边 ID 必须是非空字符串')
        return edge

    def _matching_edge(self, project_id, edge):
        return self._db.execute(
            '''SELECT * FROM provenance_edges
               WHERE project_id=? AND activity_id=? AND source_ref=?
                 AND relation=? AND target_ref=? AND ordinal=?''',
            (project_id, edge['activity_id'], edge['source_ref'], edge['relation'],
             edge['target_ref'], edge['ordinal'])).fetchone()

    @staticmethod
    def _edge_key(edge):
        return (edge['activity_id'], edge['source_ref'], edge['relation'],
                edge['target_ref'], edge['ordinal'])

    def _edge_is_identical(self, existing, payload):
        # 复合唯一键才是边的幂等身份。重试可能重新生成 surrogate id/时间，
        # 只要同一逻辑边的审计 payload 未改变，就返回已经提交的那一行。
        return self._strict_json_equal(json.loads(existing['payload']), payload)

    def _insert_edge(self, project_id, edge, default_created_at=None):
        edge = self._validate_edge(edge)
        payload = self._payload(edge.get('payload', {}))
        existing = self._matching_edge(project_id, edge)
        if existing is not None:
            if self._edge_is_identical(existing, payload):
                return self._edge(existing)
            raise ValueError('版本冲突：溯源边已存在')
        edge_id = edge.get('id') or str(uuid4())
        created_at = edge.get('created_at') or default_created_at or utc_now()
        try:
            self._db.execute(
                '''INSERT INTO provenance_edges
                   (id,project_id,activity_id,source_ref,relation,target_ref,
                    ordinal,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                (edge_id, project_id, edge['activity_id'], edge['source_ref'],
                 edge['relation'], edge['target_ref'], edge['ordinal'],
                 _json(payload), created_at))
        except sqlite3.IntegrityError:
            # 将主键碰撞转换为稳定的领域冲突；复合 FK 等完整性错误仍应原样暴露。
            by_id = self._db.execute(
                'SELECT * FROM provenance_edges WHERE id=?', (edge_id,)).fetchone()
            if by_id is not None:
                raise ValueError('版本冲突：溯源边 ID 已存在') from None
            raise
        return self._edge(self._db.execute(
            'SELECT * FROM provenance_edges WHERE id=?', (edge_id,)).fetchone())

    def _reconcile_activity_edges(self, project_id, activity_id, edges,
                                  *, allow_insert, default_created_at=None):
        """校验调用方给出的完整冻结边集合，并在首次终态提交时补入新边。"""
        proposed = {}
        for edge in edges:
            edge = self._validate_edge(edge)
            if edge['activity_id'] != activity_id:
                raise ValueError('版本冲突：溯源边不属于该活动')
            payload = self._payload(edge.get('payload', {}))
            key = self._edge_key(edge)
            duplicate = proposed.get(key)
            if duplicate is not None and not self._strict_json_equal(
                    duplicate[1], payload):
                raise ValueError('版本冲突：同一溯源边载荷不一致')
            proposed[key] = (edge, payload)

        rows = self._db.execute(
            '''SELECT * FROM provenance_edges
               WHERE project_id=? AND activity_id=?''',
            (project_id, activity_id)).fetchall()
        existing = {self._edge_key(row): row for row in rows}
        existing_keys, proposed_keys = set(existing), set(proposed)
        if existing_keys - proposed_keys:
            raise ValueError('版本冲突：终态提交缺少已冻结的溯源边')
        if not allow_insert and proposed_keys - existing_keys:
            raise ValueError('版本冲突：终态活动不能追加溯源边')

        for key, (edge, payload) in proposed.items():
            row = existing.get(key)
            if row is not None:
                if not self._edge_is_identical(row, payload):
                    raise ValueError('版本冲突：溯源边已存在')
                continue
            self._insert_edge(project_id, edge, default_created_at)

    def _transition_activity(self, project_id, activity_id, status, payload, edges,
                             completed_at=None):
        if status not in _TERMINAL_STATUSES:
            raise ValueError('不支持的溯源活动终态')
        payload = self._payload(payload)
        row = self._db.execute(
            'SELECT * FROM provenance_activities WHERE project_id=? AND id=?',
            (project_id, activity_id)).fetchone()
        if row is None:
            raise KeyError(activity_id)
        current = self._activity(row)
        edges = list(edges)
        if current['status'] != 'running':
            same = (current['status'] == status
                    and self._strict_json_equal(current['payload'], payload)
                    and (completed_at is None
                         or current['completed_at'] == completed_at))
            if not same:
                raise ValueError('版本冲突：溯源活动已经结束')
            self._reconcile_activity_edges(
                project_id, activity_id, edges, allow_insert=False)
            return current

        completed_at = completed_at or utc_now()
        cursor = self._db.execute(
            '''UPDATE provenance_activities
               SET status=?,payload=?,completed_at=?
               WHERE project_id=? AND id=? AND status='running' ''',
            (status, _json(payload), completed_at, project_id, activity_id))
        if cursor.rowcount != 1:
            raise ValueError('版本冲突：溯源活动已经结束')
        self._reconcile_activity_edges(
            project_id, activity_id, edges, allow_insert=True,
            default_created_at=completed_at)
        return self._activity(self._db.execute(
            'SELECT * FROM provenance_activities WHERE project_id=? AND id=?',
            (project_id, activity_id)).fetchone())

    def _finish_activity(self, project_id, activity_id, status, payload, edges=(),
                         completed_at=None):
        with self.repo._transaction():
            self.repo.get_project(project_id)
            return self._transition_activity(
                project_id, activity_id, status, payload, edges, completed_at)

    def complete_activity(self, project_id, activity_id, payload, edges=(),
                          completed_at=None):
        return self._finish_activity(
            project_id, activity_id, 'completed', payload, edges, completed_at)

    def fail_activity(self, project_id, activity_id, payload, edges=(), completed_at=None):
        return self._finish_activity(
            project_id, activity_id, 'failed', payload, edges, completed_at)

    def cancel_activity(self, project_id, activity_id, payload, edges=(), completed_at=None):
        return self._finish_activity(
            project_id, activity_id, 'cancelled', payload, edges, completed_at)

    def list_edges(self, project_id, *, activity_id=None, source_ref=None, target_ref=None):
        self.repo.get_project(project_id)
        clauses, values = ['project_id=?'], [project_id]
        for column, value in (
                ('activity_id', activity_id), ('source_ref', source_ref),
                ('target_ref', target_ref)):
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError('溯源边过滤字段必须是非空字符串')
                clauses.append(f'{column}=?')
                values.append(value)
        with self._lock:
            rows = self._db.execute(
                f'''SELECT * FROM provenance_edges WHERE {' AND '.join(clauses)}
                    ORDER BY ordinal,id''', values).fetchall()
        return [self._edge(row) for row in rows]

    def migrate_legacy_answer_graph(
            self, project_id, *, answer_id, retrieval_id, expected_payload,
            answer_payload, answer_edges, retrieval_edges):
        """Atomically replace an edge-v0 payload graph with its edge-v1 ledger.

        This is a one-way data migration used on first legacy trace access.  The
        compare-and-swap guard prevents a concurrent reader from overwriting an
        already migrated activity.
        """
        with self.repo._transaction():
            self.repo.get_project(project_id)
            row = self._db.execute(
                'SELECT * FROM provenance_activities WHERE project_id=? AND id=?',
                (project_id, answer_id)).fetchone()
            if row is None:
                raise KeyError(answer_id)
            current = self._activity(row)
            if current['payload'].get('graph_schema') == 'edge-v1':
                return current
            if current['kind'] != 'answer' or not self._strict_json_equal(
                    current['payload'], expected_payload):
                raise ValueError('版本冲突：旧溯源活动已改变')
            retrieval = self._db.execute(
                '''SELECT kind FROM provenance_activities
                   WHERE project_id=? AND id=?''',
                (project_id, retrieval_id)).fetchone()
            if retrieval is None or retrieval['kind'] != 'retrieval':
                raise ValueError('旧溯源活动缺少对应检索运行')
            grouped = ((answer_id, list(answer_edges)),
                       (retrieval_id, list(retrieval_edges)))
            for activity_id, edges in grouped:
                for edge in edges:
                    self._validate_edge(edge)
                    if edge['activity_id'] != activity_id:
                        raise ValueError('迁移边不属于指定溯源活动')
            self._db.execute(
                'UPDATE provenance_activities SET payload=? WHERE project_id=? AND id=?',
                (_json(self._payload(answer_payload)), project_id, answer_id))
            for activity_id, edges in grouped:
                self._db.execute(
                    'DELETE FROM provenance_edges WHERE project_id=? AND activity_id=?',
                    (project_id, activity_id))
                for edge in edges:
                    self._insert_edge(project_id, edge)
            return self._activity(self._db.execute(
                'SELECT * FROM provenance_activities WHERE project_id=? AND id=?',
                (project_id, answer_id)).fetchone())

    # -------------------------------------------------------------- 原子组合入口
    def complete_retrieval_and_begin_answer(
            self, project_id, *, retrieval_id, answer_id, retrieval_payload=None,
            answer_payload=None, edges=None, completed_at=None, answer_started_at=None,
            snapshot_factory=None):
        """在唯一事务中解析来源、完成 retrieval、创建 answer 并冻结全部边。

        snapshot_factory 只执行读取并返回 (retrieval_payload, answer_payload,
        edges)。它在 BEGIN IMMEDIATE 后调用，不得自行开启嵌套事务。
        已构造 payload/edges 的调用方仍可使用原有参数。
        """
        with self.repo._transaction():
            self.repo.get_project(project_id)
            if retrieval_id == answer_id:
                raise ValueError('retrieval 和 answer 必须使用不同活动 ID')
            retrieval_row = self._db.execute(
                'SELECT kind FROM provenance_activities WHERE project_id=? AND id=?',
                (project_id, retrieval_id)).fetchone()
            if retrieval_row is None:
                raise KeyError(retrieval_id)
            if retrieval_row['kind'] != 'retrieval':
                raise ValueError('指定活动不是 retrieval')
            if snapshot_factory is not None:
                if any(value is not None for value in (retrieval_payload, answer_payload, edges)):
                    raise ValueError('来源快照工厂不能与已构造的 payload/edges 混用')
                retrieval_payload, answer_payload, edges = snapshot_factory()
            grouped = {retrieval_id: [], answer_id: []}
            for edge in edges or ():
                edge = self._validate_edge(edge)
                if edge['activity_id'] not in grouped:
                    raise ValueError('版本冲突：组合提交含有其他活动的溯源边')
                grouped[edge['activity_id']].append(edge)
            retrieval = self._transition_activity(
                project_id, retrieval_id, 'completed', retrieval_payload,
                grouped[retrieval_id], completed_at)
            answer_existed = self._db.execute(
                '''SELECT 1 FROM provenance_activities
                   WHERE project_id=? AND id=?''',
                (project_id, answer_id)).fetchone() is not None
            answer = self._begin_activity(
                project_id, answer_id, 'answer', answer_payload, answer_started_at)
            edge_time = retrieval['completed_at']
            self._reconcile_activity_edges(
                project_id, answer_id, grouped[answer_id],
                allow_insert=not answer_existed, default_created_at=edge_time)
            return retrieval, answer
