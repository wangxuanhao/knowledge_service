"""Mechanical executor for service-prepared formal-write action plans."""
from __future__ import annotations

import json
from uuid import uuid4

from ..core.time import utc_now


class FormalWriteExecutor:
    """Execute explicit actions without making domain-policy decisions."""

    def __init__(self, repository):
        self.repository = repository
        self.tokens = {}

    def _record_for_ref(self, project_id, ref):
        if set(ref) == {'token'}:
            try:
                return self.tokens[ref['token']]
            except KeyError as exc:
                raise ValueError('正式写入计划引用了未知记录令牌') from exc
        if set(ref) == {'version_id'}:
            row = self.repository._db.execute(
                '''SELECT * FROM record_versions
                   WHERE project_id=? AND version_id=?''',
                (project_id, ref['version_id'])).fetchone()
            if row is None:
                raise ValueError('正式写入计划引用的记录版本不存在')
            return self.repository._record(row)
        raise ValueError('正式写入计划记录引用无效')

    def _version_for_ref(self, project_id, ref):
        return self._record_for_ref(project_id, ref)['version_id']

    def _map_accepted_assertion(self, project_id, assertion, record_version_id,
                                *, replay=False):
        events = self.repository._db.execute(
            '''SELECT id,to_status,canonical_record_id FROM assertion_events
               WHERE project_id=? AND assertion_id=? AND decision_version=?''',
            (project_id, assertion['id'], assertion['decision_version'])).fetchall()
        if (len(events) != 1 or events[0]['to_status'] != 'accepted'
                or events[0]['canonical_record_id']
                != assertion['canonical_record_id']):
            raise ValueError('断言接受事件与规范决策冲突')
        event_id = events[0]['id']
        existing = self.repository._db.execute(
            '''SELECT record_id,record_version_id
               FROM record_version_assertions
               WHERE project_id=? AND assertion_id=? AND assertion_event_id=?''',
            (project_id, assertion['id'], event_id)).fetchall()
        if replay and not existing:
            raise ValueError('断言重放冲突：历史接受事件缺少精确版本支撑映射')
        if any(row['record_id'] != assertion['canonical_record_id']
               or row['record_version_id'] != record_version_id
               for row in existing):
            raise ValueError('断言重放与其版本支撑映射冲突')
        self.repository._insert_record_version_assertion(
            project_id, assertion['canonical_record_id'], record_version_id,
            assertion['id'], event_id)

    def _execute_assertion_accept(self, project_id, action):
        created = self.repository._create_assertion(
            project_id, action['assertion'])
        mode = action['mode']
        if mode in {'create', 'pending'}:
            expected = action['resulting_version'] - 1
            if (created['status'] != 'pending'
                    or created['decision_version'] != expected):
                raise ValueError('版本冲突：断言已变更')
            created = self.repository._transition_assertion(
                project_id, created['id'], expected, 'accepted',
                action['reason'], action['actor'],
                action['canonical_record_id'])
            replay = False
        elif mode == 'replay':
            if (created['status'] != 'accepted'
                    or created['canonical_record_id']
                    != action['canonical_record_id']
                    or created['decision_version'] != action['resulting_version']):
                raise ValueError('断言重放与其规范决策冲突')
            replay = True
        else:
            raise ValueError('正式写入计划断言接受模式无效')
        self._map_accepted_assertion(
            project_id, created,
            self._version_for_ref(project_id, action['record_ref']),
            replay=replay)
        return created

    def _execute_assertion_transition(self, project_id, action):
        updated = self.repository._transition_assertion(
            project_id, action['id'], action['expected_version'],
            action['status'], action['reason'], action['actor'],
            action.get('canonical_record_id'))
        if updated['status'] == 'accepted':
            self._map_accepted_assertion(
                project_id, updated,
                self._version_for_ref(project_id, action['record_ref']))
        return updated

    def _execute_assertion_move(self, project_id, action):
        row = self.repository._db.execute(
            'SELECT * FROM assertions WHERE project_id=? AND id=?',
            (project_id, action['id'])).fetchone()
        if (row is None
                or row['canonical_record_id'] != action['canonical_before']
                or row['status'] != action['status_before']
                or row['decision_version'] != action['expected_version']):
            raise ValueError('版本冲突：合并后断言已变更')
        new_version = action['expected_version'] + 1
        now = utc_now()
        cursor = self.repository._db.execute(
            '''UPDATE assertions
               SET status=?,canonical_record_id=?,decision_version=?,
                   decision_reason=?,actor=?,decided_at=?
               WHERE project_id=? AND id=? AND decision_version=?''',
            (action['status_after'], action['canonical_after'], new_version,
             action['reason'], action['actor'],
             None if action['status_after'] == 'pending' else now,
             project_id, action['id'], action['expected_version']))
        if cursor.rowcount != 1:
            raise ValueError('版本冲突：合并后断言已变更')
        self.repository._db.execute(
            '''INSERT INTO assertion_events
               (id,assertion_id,project_id,from_status,to_status,decision_version,
                reason,actor,canonical_record_id,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (str(uuid4()), action['id'], project_id, action['status_before'],
             action['status_after'], new_version, action['reason'],
             action['actor'], action['canonical_after'], now))
        if action['status_after'] == 'accepted':
            self._map_accepted_assertion(project_id, {
                'id': action['id'], 'decision_version': new_version,
                'canonical_record_id': action['canonical_after'],
            }, self._version_for_ref(project_id, action['record_ref']))

    def _execute_fact_key(self, project_id, action):
        kind = action['kind']
        if kind == 'fact_key_insert':
            self.repository._db.execute(
                '''INSERT INTO fact_keys
                   (project_id,fact_key,canonical_record_id,created_at,retired_at)
                   VALUES (?,?,?,?,NULL)''',
                (project_id, action['fact_key'], action['record_id'], utc_now()))
            return
        if kind == 'fact_key_retire':
            cursor = self.repository._db.execute(
                '''UPDATE fact_keys SET retired_at=?
                   WHERE project_id=? AND fact_key=? AND canonical_record_id=?
                     AND retired_at IS NULL''',
                (utc_now(), project_id, action['fact_key'], action['record_id']))
            if cursor.rowcount != 1:
                raise ValueError('版本冲突：事实键已变更')
            return
        cursor = self.repository._db.execute(
            '''UPDATE fact_keys SET canonical_record_id=?
               WHERE project_id=? AND fact_key=? AND canonical_record_id=?
                 AND retired_at IS NULL''',
            (action['to_record_id'], project_id, action['fact_key'],
             action['from_record_id']))
        if cursor.rowcount != 1:
            raise ValueError('版本冲突：事实键已变更')

    def _execute_merge_ledger(self, project_id, action):
        after_state = [
            {key: value for key, value in self._record_for_ref(project_id, ref).items()
             if key != 'embedding'}
            for ref in action['after_state_refs']]
        self.repository._db.execute(
            '''INSERT INTO merge_operations
               (id,project_id,operation,status,redirects,assertion_moves,
                before_state,after_state,expected_versions,reversal_of,created_at)
               VALUES (?,?,?,'applied',?,?,?,?,?,?,?)''',
            (action['id'], project_id, action['operation'],
             json.dumps(action['redirects'], ensure_ascii=False, sort_keys=True),
             json.dumps(action['assertion_moves'], ensure_ascii=False,
                        sort_keys=True),
             json.dumps(action['before_state'], ensure_ascii=False, sort_keys=True),
             json.dumps(after_state, ensure_ascii=False, sort_keys=True),
             json.dumps(action['expected_versions'], ensure_ascii=False,
                        sort_keys=True),
             action.get('reversal_of'), utc_now()))

    def execute(self, plan, operation_context):
        project_id = plan['project_id']
        assertion_results = []
        for action in plan['actions']:
            kind = action.get('kind')
            if kind == 'record_put':
                saved = self.repository._put(
                    project_id, action['record'], action['expected_version'],
                    operation=operation_context)
                self.tokens[action['token']] = saved
            elif kind in {
                    'fact_key_insert', 'fact_key_retire', 'fact_key_retarget'}:
                self._execute_fact_key(project_id, action)
            elif kind == 'assertion_accept':
                updated = self._execute_assertion_accept(project_id, action)
                if action['result']:
                    assertion_results.append(updated)
            elif kind == 'assertion_create':
                created = self.repository._create_assertion(
                    project_id, action['assertion'])
                if action['result']:
                    assertion_results.append(created)
            elif kind == 'assertion_transition':
                updated = self._execute_assertion_transition(project_id, action)
                if action['result']:
                    assertion_results.append(updated)
            elif kind == 'assertion_move':
                self._execute_assertion_move(project_id, action)
            elif kind == 'resolution_review_create':
                self.repository._create_resolution_review(
                    project_id, action['review'])
            elif kind == 'resolution_review_decide':
                self.repository._decide_resolution_review(
                    project_id, action['id'], action['expected_version'],
                    action['decision'], action['reason'], action['actor'])
            elif kind == 'merge_ledger':
                self._execute_merge_ledger(project_id, action)
            else:
                raise ValueError(f'正式写入计划包含未知动作：{kind!r}')
        records = [
            self._record_for_ref(project_id, ref) for ref in plan['result_refs']]
        if ([row['id'] for row in assertion_results]
                != plan['assertion_result_ids']):
            raise ValueError('正式写入计划断言结果清单无效')
        return {
            'accepted_records': records,
            'assertion_updates': assertion_results,
            'review_items': plan['review_items'],
            'ontology_proposals': [],
            'diagnostics': {
                'operation': plan['operation'], 'record_count': len(records),
                'assertion_count': len(assertion_results),
            },
        }
