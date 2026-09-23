"""规范图谱变更的唯一事务入口。"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from ..utils.assertions import occurrence_id
from ..core.time import utc_now


FORMAL_OPERATIONS = frozenset({
    'extract', 'manual_write', 'approve_review', 'legacy_import',
    'adopt_discovery', 'publish_discovery_draft', 'ontology_publish',
    'ontology_remap', 'merge_rewrite', 'merge_reversal', 'retract_source',
    'restore_snapshot',
})

_SOURCE_METADATA = frozenset({
    'chunk_id', 'start_char', 'end_char', 'source_version_id',
    'source_hash', 'passage', 'evidence',
})


def _deterministic_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _typed_value_identity(record):
    if record.get('kind') != 'attribute':
        return None
    return [record.get('datatype'), _deterministic_json(record.get('value'))]


def canonical_relation_key(record):
    required = ('type', 'subject_id', 'object_id')
    if any(not isinstance(record.get(key), str) or not record[key] for key in required):
        raise ValueError('规范关系键需要谓词和实体端点')
    identity = [
        record['type'], record['subject_id'], record['object_id'],
        record.get('valid_from'), record.get('valid_until'),
    ]
    encoded = _deterministic_json(identity).encode('utf-8')
    return 'fact_' + hashlib.sha256(encoded).hexdigest()


def canonical_attribute_key(record):
    required = ('subject_id', 'type', 'datatype')
    if (any(not isinstance(record.get(key), str) or not record[key] for key in required)
            or record.get('value') is None):
        raise ValueError('规范属性键需要主体、谓词、值和数据类型')
    identity = [
        record['subject_id'], record['type'], record['value'], record['datatype'],
        record.get('valid_from'), record.get('valid_until'),
    ]
    encoded = _deterministic_json(identity).encode('utf-8')
    return 'fact_' + hashlib.sha256(encoded).hexdigest()


def _manual_assertion_id(namespace, operation, record, expected_version):
    payload = json.dumps({
        'namespace': namespace, 'operation': operation, 'record_id': record['id'],
        'version': expected_version + 1, 'kind': record['kind'],
        'type': record.get('type'), 'subject_id': record.get('subject_id'),
        'object_id': record.get('object_id'), 'text': record.get('text'),
        'typed_value': _typed_value_identity(record),
    }, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'ast_manual_' + hashlib.sha256(payload).hexdigest()


class FormalFactWriter:
    """校验期望版本，并将事实及其支撑断言原子化提交。"""

    def __init__(self, repository):
        self.repository = repository

    def _source_assertion(self, operation, record, expected_version, ordinal):
        metadata = record.get('metadata', {})
        document_id = record.get('source_id')
        document_version_id = metadata.get('source_version_id')
        chunk_id = metadata.get('chunk_id')
        raw_terms = [str(record.get(key, '')) for key in
                     ('subject_id', 'type', 'object_id', 'text')]
        typed_value = _typed_value_identity(record)
        if typed_value is not None:
            raw_terms.extend(typed_value)
        if document_version_id and chunk_id:
            assertion_id = occurrence_id(
                self.repository.storage_namespace, document_version_id, chunk_id,
                record['kind'], ordinal, raw_terms)
        else:
            assertion_id = _manual_assertion_id(
                self.repository.storage_namespace, operation, record, expected_version)
        start_char = metadata.get('start_char')
        end_char = metadata.get('end_char')
        if type(start_char) is int and end_char is None:
            end_char = start_char + len(record.get('text', ''))
        return {
            'id': assertion_id,
            'kind': record['kind'],
            'document_id': document_id,
            'document_version_id': document_version_id,
            'chunk_id': chunk_id,
            'source_hash': metadata.get('source_hash'),
            'start_char': start_char,
            'end_char': end_char,
            'quote': metadata.get('passage') or metadata.get('evidence') or record.get('text'),
            'payload': {key: value for key, value in record.items()
                        if key not in {'embedding', 'embedding_model'}},
            'actor': 'formal-writer',
        }

    @staticmethod
    def _canonical_record(record):
        canonical = dict(record)
        if canonical['kind'] in {'entity', 'relation', 'attribute'}:
            canonical.pop('source_id', None)
            canonical['metadata'] = {
                key: value for key, value in canonical.get('metadata', {}).items()
                if key not in _SOURCE_METADATA
            }
        return canonical

    def _select_record_version(self, project_id, canonical_id, selected_versions):
        """在调用方持有的写事务内、决策发生前选定支撑的精确版本。"""
        if canonical_id not in selected_versions:
            row = self.repository._db.execute(
                '''SELECT version_id FROM record_versions
                   WHERE project_id=? AND id=? AND superseded_at IS NULL''',
                (project_id, canonical_id)).fetchone()
            if row is None:
                raise ValueError('正式断言的规范记录目标不存在')
            selected_versions[canonical_id] = row['version_id']
        return selected_versions[canonical_id]

    def _map_accepted_assertion(self, project_id, assertion, record_version_id, *, replay=False):
        """只连接本次接受/重指派的 event；同 event 的版本身份不可改变。"""
        events = self.repository._db.execute(
            '''SELECT id,to_status,canonical_record_id FROM assertion_events
               WHERE project_id=? AND assertion_id=? AND decision_version=?''',
            (project_id, assertion['id'], assertion['decision_version'])).fetchall()
        if (len(events) != 1 or events[0]['to_status'] != 'accepted' or
                events[0]['canonical_record_id'] != assertion['canonical_record_id']):
            raise ValueError('断言接受事件与规范决策冲突')
        event_id = events[0]['id']
        existing = self.repository._db.execute(
            '''SELECT record_id,record_version_id FROM record_version_assertions
               WHERE project_id=? AND assertion_id=? AND assertion_event_id=?''',
            (project_id, assertion['id'], event_id)).fetchall()
        if replay and not existing:
            raise ValueError('断言重放冲突：历史接受事件缺少精确版本支撑映射')
        if any(row['record_id'] != assertion['canonical_record_id'] or
               row['record_version_id'] != record_version_id for row in existing):
            raise ValueError('断言重放与其版本支撑映射冲突')
        self.repository._insert_record_version_assertion(
            project_id, assertion['canonical_record_id'], record_version_id,
            assertion['id'], event_id)

    def apply(self, operation, project_id, assertions, expected, policy, recorded_at=None,
              operation_context=None):
        if operation not in FORMAL_OPERATIONS:
            raise ValueError('不支持的正式操作')
        if not isinstance(expected, dict) or any(
                not isinstance(key, str) or type(value) is not int or value < 0
                for key, value in expected.items()):
            raise ValueError('正式期望必须将 ID 映射到版本')
        # 批次系统时间点：整批共享一个 recorded_at，落库循环不再逐条 utc_now()，
        # 否则一次批量写入会被 timeline 拆成与记录数等量的时间点。
        records = list(policy.get('records', []))
        completion = policy.get('completion')
        explicit_assertions = [(item, None) for item in assertions or []]
        result_records = []
        assertion_updates = []
        canonical_by_input = {}
        selected_versions = {}
        fact_redirects = []
        ledger = policy.get('ledger')
        assertion_moves = []
        if operation_context is None:
            operation_context = self.repository._reserve_record_operation(
                project_id, recorded_at)
        elif recorded_at is not None:
            raise ValueError('写入操作上下文不能与 recorded_at 同时指定')
        else:
            self.repository._record_operation_time(project_id, operation_context)
        with self.repository._transaction():
            self.repository.get_project(project_id)
            for record_id, wanted in expected.items():
                row = self.repository._db.execute(
                    '''SELECT version FROM record_versions
                       WHERE project_id=? AND id=? AND superseded_at IS NULL''',
                    (project_id, record_id)).fetchone()
                actual = row['version'] if row else 0
                if actual != wanted:
                    raise ValueError('版本冲突：记录已变更')
            if completion:
                document, document_version = completion
                result_records.append(self.repository._put(
                    project_id, document, expected.get(document['id'], document_version),
                    operation=operation_context))
            for ordinal, original in enumerate(records):
                record = self._canonical_record(original)
                expected_version = expected.get(record['id'])
                canonical_id = record['id']
                if record['kind'] in {'relation', 'attribute'}:
                    old_key = self.repository._db.execute(
                        '''SELECT fact_key FROM fact_keys
                           WHERE project_id=? AND canonical_record_id=? AND retired_at IS NULL''',
                        (project_id, record['id'])).fetchone()
                    if record.get('metadata', {}).get('_deleted'):
                        if old_key:
                            self.repository._db.execute(
                                '''UPDATE fact_keys SET retired_at=?
                                   WHERE project_id=? AND canonical_record_id=? AND retired_at IS NULL''',
                                (utc_now(), project_id, record['id']))
                    else:
                        fact_key = (canonical_relation_key(record) if record['kind'] == 'relation'
                                    else canonical_attribute_key(record))
                        mapped = self.repository._db.execute(
                            '''SELECT canonical_record_id FROM fact_keys
                               WHERE project_id=? AND fact_key=? AND retired_at IS NULL''',
                            (project_id, fact_key)).fetchone()
                        if mapped and mapped['canonical_record_id'] != record['id']:
                            canonical_id = mapped['canonical_record_id']
                            row = self.repository._db.execute(
                                '''SELECT * FROM record_versions WHERE project_id=? AND id=?
                                   AND superseded_at IS NULL''',
                                (project_id, canonical_id)).fetchone()
                            if row is None:
                                raise RuntimeError('事实键指向不存在的规范记录')
                            if operation == 'merge_rewrite' and expected_version:
                                winner=min(canonical_id,record['id'])
                                if old_key:
                                    self.repository._db.execute(
                                        '''UPDATE fact_keys SET retired_at=? WHERE project_id=?
                                           AND canonical_record_id=? AND retired_at IS NULL''',
                                        (utc_now(),project_id,record['id']))
                                    # The mapped key may be retargeted to this source below.
                                    # Do not let the generic old-key cleanup retire that newly
                                    # surviving mapping by canonical_record_id afterwards.
                                    old_key = None
                                if winner == canonical_id:
                                    selected_versions[winner] = row['version_id']
                                    record['metadata']={**record.get('metadata',{}),'_deleted':True,
                                        'merged_into_fact':winner,'fact_collision':True}
                                    saved=self.repository._put(
                                        project_id, record, expected_version,
                                        operation=operation_context)
                                    canonical_by_input[original['id']]=winner
                                    result_records.append(saved)
                                    fact_redirects.append((record['id'],winner))
                                    continue
                                mapped_record=self.repository._record(row)
                                mapped_tombstone={key:value for key,value in mapped_record.items() if key not in {
                                    'project_id','version','version_id','recorded_at','superseded_at'}}
                                mapped_tombstone['metadata']={**mapped_tombstone.get('metadata',{}),
                                    '_deleted':True,'merged_into_fact':winner,'fact_collision':True,
                                    **({'_operation_id':record['metadata']['_operation_id']}
                                       if record.get('metadata',{}).get('_operation_id') else {})}
                                result_records.append(self.repository._put(
                                    project_id, mapped_tombstone, mapped_record['version'],
                                    operation=operation_context))
                                self.repository._db.execute(
                                    '''UPDATE fact_keys SET canonical_record_id=? WHERE project_id=?
                                       AND fact_key=? AND retired_at IS NULL''',
                                    (winner,project_id,fact_key))
                                for audit_record in records:
                                    if audit_record['kind']=='document' and audit_record.get('metadata',{}).get('_audit'):
                                        before=audit_record['metadata'].setdefault('before',[])
                                        if not any(item.get('id')==mapped_record['id'] for item in before):
                                            before.append({key:value for key,value in mapped_record.items()
                                                if key!='embedding'})
                                fact_redirects.append((canonical_id,winner))
                                canonical_id=winner
                                mapped={'canonical_record_id':winner}
                            else:
                                saved = self.repository._record(row)
                                selected_versions[canonical_id] = saved['version_id']
                                canonical_by_input[original['id']] = canonical_id
                                if old_key:
                                    source_row = self.repository._db.execute(
                                        '''SELECT * FROM record_versions WHERE project_id=? AND id=?
                                           AND superseded_at IS NULL''',
                                        (project_id, record['id'])).fetchone()
                                    if source_row is None:
                                        raise RuntimeError('旧事实键指向不存在的源记录')
                                    source_record = self.repository._record(source_row)
                                    source_tombstone = {
                                        key: value for key, value in source_record.items()
                                        if key not in {'project_id', 'version', 'version_id',
                                                       'recorded_at', 'superseded_at'}
                                    }
                                    source_tombstone['metadata'] = {
                                        **source_tombstone.get('metadata', {}), '_deleted': True,
                                        'merged_into_fact': canonical_id, 'fact_collision': True,
                                    }
                                    self.repository._db.execute(
                                        '''UPDATE fact_keys SET retired_at=? WHERE project_id=?
                                           AND canonical_record_id=? AND retired_at IS NULL''',
                                        (utc_now(), project_id, record['id']))
                                    result_records.append(self.repository._put(
                                        project_id, source_tombstone, expected_version,
                                        operation=operation_context))
                                    result_records.append(saved)
                                elif operation=='legacy_import':
                                    record['metadata']={**record.get('metadata',{}),'_deleted':True,
                                        'merged_into_fact':canonical_id,'legacy_parallel_occurrence':True}
                                    result_records.append(self.repository._put(
                                        project_id, record, expected_version,
                                        operation=operation_context))
                                else:
                                    result_records.append(saved)
                                if not policy.get('suppress_auto_assertions'):
                                    source = self._source_assertion(
                                        operation, original, expected_version or 0, ordinal)
                                    explicit_assertions.append((
                                        {**source, 'canonical_record_id': canonical_id},
                                        saved['version_id']))
                                continue
                        if old_key and old_key['fact_key'] != fact_key:
                            self.repository._db.execute(
                                '''UPDATE fact_keys SET retired_at=? WHERE project_id=?
                                   AND canonical_record_id=? AND retired_at IS NULL''',
                                (utc_now(), project_id, record['id']))
                        if not mapped:
                            self.repository._db.execute(
                                '''INSERT INTO fact_keys
                                   (project_id,fact_key,canonical_record_id,created_at,retired_at)
                                   VALUES (?,?,?,?,NULL)''',
                                (project_id, fact_key, record['id'], utc_now()))
                saved = self.repository._put(
                    project_id, record, expected_version,
                    operation=operation_context)
                result_records.append(saved)
                canonical_by_input[original['id']] = canonical_id
                selected_versions[canonical_id] = saved['version_id']
                if (operation == 'merge_rewrite' and expected_version is not None and
                        record['kind'] in {'relation', 'attribute'} and
                        not record.get('metadata', {}).get('_deleted')):
                    # Rewriting an endpoint/subject creates a new current fact version even
                    # when its canonical record ID is unchanged.  A self-redirect records a
                    # fresh accepted event and freezes existing support on that exact version.
                    fact_redirects.append((canonical_id, canonical_id))
                if (not policy.get('suppress_auto_assertions') and
                        operation in {'extract','manual_write','approve_review','legacy_import','adopt_discovery'} and
                        original['kind'] in {'entity', 'relation', 'attribute'} and
                        not record.get('metadata', {}).get('_deleted')):
                    source = self._source_assertion(operation, original, expected_version or 0, ordinal)
                    explicit_assertions.append((
                        {**source, 'canonical_record_id': canonical_id}, saved['version_id']))
            for item, selected_version in explicit_assertions:
                assertion = dict(item)
                canonical_id = assertion.pop('canonical_record_id', None) or assertion.pop('record_id', None)
                if canonical_id in canonical_by_input:
                    canonical_id = canonical_by_input[canonical_id]
                if not canonical_id:
                    raise ValueError('正式断言需要规范记录目标')
                record_version_id = selected_version or self._select_record_version(
                    project_id, canonical_id, selected_versions)
                created = self.repository._create_assertion(project_id, assertion)
                replay = created['status'] == 'accepted'
                if created['status'] == 'pending':
                    created = self.repository._transition_assertion(
                        project_id, created['id'], created['decision_version'], 'accepted',
                        f'{operation}: 规范写入已接受', assertion.get('actor', 'formal-writer'),
                        canonical_id)
                elif created['status'] != 'accepted' or created['canonical_record_id'] != canonical_id:
                    raise ValueError('断言重放与其规范决策冲突')
                self._map_accepted_assertion(
                    project_id, created, record_version_id, replay=replay)
                assertion_updates.append(created)
            for item in policy.get('pending_assertions', []):
                assertion_updates.append(self.repository._create_assertion(project_id, item))
            for decision in policy.get('assertion_decisions', []):
                before_decision = self.repository._db.execute(
                    'SELECT * FROM assertions WHERE project_id=? AND id=?',
                    (project_id, decision['id'])).fetchone()
                if before_decision is None:
                    raise KeyError(decision['id'])
                decision_target=decision.get('canonical_record_id')
                if decision_target in canonical_by_input:
                    decision_target=canonical_by_input[decision_target]
                if decision['status'] == 'accepted':
                    if decision_target is None:
                        current = self.repository.get_assertion(project_id, decision['id'])
                        decision_target = current['canonical_record_id']
                    record_version_id = self._select_record_version(
                        project_id, decision_target, selected_versions)
                updated = self.repository._transition_assertion(
                    project_id, decision['id'], decision['expected_version'],
                    decision['status'], decision['reason'], decision['actor'],
                    decision_target)
                if updated['status'] == 'accepted':
                    self._map_accepted_assertion(project_id, updated, record_version_id)
                assertion_updates.append(updated)
                if ledger:
                    assertion_moves.append({
                        'id':updated['id'],'move_kind':'status',
                        'from':before_decision['canonical_record_id'],
                        'to':updated['canonical_record_id'],
                        'status_before':before_decision['status'],
                        'status_after':updated['status'],
                        'decision_version_before':before_decision['decision_version'],
                        'decision_version_after':updated['decision_version'],
                        'reason':decision['reason'],'actor':decision['actor'],
                    })
            for review in policy.get('resolution_reviews', []):
                self.repository._create_resolution_review(project_id, review)
            for decision in policy.get('resolution_decisions', []):
                self.repository._decide_resolution_review(
                    project_id,decision['id'],decision['expected_version'],decision['decision'],
                    decision['reason'],decision['actor'])
            if operation == 'retract_source':
                source_ids = [record['id'] for record in records
                              if record['kind'] == 'document'
                              and record.get('metadata', {}).get('_deleted')]
                affected_canonical = set()
                for source_id in source_ids:
                    rows = self.repository._db.execute(
                        '''SELECT * FROM assertions WHERE project_id=? AND document_id=?
                           AND status IN ('pending','accepted','contradicting')''',
                        (project_id, source_id)).fetchall()
                    for row in rows:
                        if row['canonical_record_id']:
                            affected_canonical.add(row['canonical_record_id'])
                        self.repository._transition_assertion(
                            project_id, row['id'], row['decision_version'], 'superseded',
                            '来源文档已撤回', 'formal-writer',
                            row['canonical_record_id'])
                for canonical_id in sorted(affected_canonical):
                    current_row = self.repository._db.execute(
                        '''SELECT * FROM record_versions WHERE project_id=? AND id=?
                           AND superseded_at IS NULL''',
                        (project_id, canonical_id)).fetchone()
                    if current_row is None:
                        continue
                    support = self.repository._db.execute(
                        '''SELECT COUNT(DISTINCT assertions.id)
                           FROM assertions
                           JOIN record_version_assertions AS support
                             ON support.project_id=assertions.project_id
                            AND support.assertion_id=assertions.id
                            AND support.record_id=assertions.canonical_record_id
                           WHERE assertions.project_id=?
                             AND assertions.canonical_record_id=?
                             AND assertions.status='accepted'
                             AND support.record_version_id=?''',
                        (project_id, canonical_id, current_row['version_id'])).fetchone()[0]
                    if support:
                        continue
                    current = self.repository._record(current_row)
                    writable = {key: value for key, value in current.items() if key not in {
                        'project_id','version','version_id','recorded_at','superseded_at'}}
                    if current['kind'] in {'relation', 'attribute'}:
                        writable['metadata'] = {**current.get('metadata', {}), '_deleted': True,
                            'unsupported_reason': '最后一个已接受的来源已被撤回'}
                        self.repository._db.execute(
                            '''UPDATE fact_keys SET retired_at=? WHERE project_id=?
                               AND canonical_record_id=? AND retired_at IS NULL''',
                            (utc_now(), project_id, canonical_id))
                    elif current['kind'] == 'entity':
                        writable['metadata'] = {**current.get('metadata', {}), '_unsupported': True,
                            'unsupported_reason': '最后一个已接受的来源已被撤回'}
                    else:
                        continue
                    result_records.append(self.repository._put(
                        project_id, writable, current['version'],
                        operation=operation_context))
            if ledger:
                reversal_of = ledger.get('reversal_of')
                if reversal_of:
                    if operation != 'merge_reversal':
                        raise ValueError('只有合并回退操作可以反向应用断言移动账本')
                    prior = self.repository._db.execute(
                        'SELECT assertion_moves FROM merge_operations WHERE project_id=? AND id=?',
                        (project_id, reversal_of)).fetchone()
                    if prior is None:
                        raise ValueError('合并回退目标不存在')
                    grouped = {}
                    for move in json.loads(prior['assertion_moves']):
                        grouped.setdefault(move['id'], []).append(move)
                    requested_moves = []
                    for assertion_id, historical in grouped.items():
                        row = self.repository._db.execute(
                            'SELECT * FROM assertions WHERE project_id=? AND id=?',
                            (project_id, assertion_id)).fetchone()
                        if row is None:
                            raise ValueError('版本冲突：合并后断言已变更')
                        normalized=[]
                        for move in historical:
                            normalized.append({
                                'canonical_before':move.get('from'),
                                'canonical_after':move.get('to'),
                                'status_before':move.get('status_before',row['status']),
                                'status_after':move.get('status_after',row['status']),
                                'decision_version_before':move.get(
                                    'decision_version_before',move.get('expected_version')),
                                'decision_version_after':move['decision_version_after'],
                            })
                        for previous,following in zip(normalized,normalized[1:]):
                            if (previous['canonical_after']!=following['canonical_before'] or
                                    previous['status_after']!=following['status_before'] or
                                    previous['decision_version_after']!=
                                    following['decision_version_before']):
                                raise ValueError('版本冲突：合并断言移动账本不连续')
                        latest=normalized[-1]
                        if (row['canonical_record_id']!=latest['canonical_after'] or
                                row['status']!=latest['status_after'] or
                                row['decision_version']!=latest['decision_version_after']):
                            raise ValueError('版本冲突：合并后断言已变更')
                        expected=row['decision_version']
                        canonical=row['canonical_record_id']
                        status=row['status']
                        for move in reversed(normalized):
                            requested_moves.append({
                                'id':assertion_id,'canonical_before':canonical,
                                'canonical_after':move['canonical_before'],
                                'status_before':status,'status_after':move['status_before'],
                                'expected_version':expected,
                            })
                            canonical=move['canonical_before']
                            status=move['status_before']
                            expected+=1
                else:
                    requested_moves = []
                    queued_ids={move['id'] for move in assertion_moves}
                    def queue_redirect(source,target):
                        rows=self.repository._db.execute(
                            '''SELECT * FROM assertions
                               WHERE project_id=? AND canonical_record_id=?''',
                            (project_id,source)).fetchall()
                        for row in rows:
                            if row['id'] in queued_ids:
                                continue
                            queued_ids.add(row['id'])
                            requested_moves.append({
                                'id':row['id'],'canonical_before':source,
                                'canonical_after':target,'status_before':row['status'],
                                'status_after':row['status'],
                                'expected_version':row['decision_version'],
                            })
                    for source, target in ledger.get('redirects', {}).items():
                        queue_redirect(source,target)
                    for source,target in dict.fromkeys(fact_redirects):
                        queue_redirect(source,target)
                for move in requested_moves:
                    row = self.repository._db.execute(
                        'SELECT * FROM assertions WHERE project_id=? AND id=?',
                        (project_id, move['id'])).fetchone()
                    if (row is None or
                            row['canonical_record_id']!=move['canonical_before'] or
                            row['status']!=move['status_before'] or
                            row['decision_version']!=move['expected_version']):
                        raise ValueError('版本冲突：合并后断言已变更')
                    new_version = row['decision_version'] + 1
                    if move['status_after'] == 'accepted':
                        record_version_id = self._select_record_version(
                            project_id, move['canonical_after'], selected_versions)
                    now = utc_now()
                    reason=(f'{operation}: {move["canonical_before"]} -> '
                            f'{move["canonical_after"]}; {move["status_before"]} -> '
                            f'{move["status_after"]}')
                    cursor=self.repository._db.execute(
                        '''UPDATE assertions SET status=?,canonical_record_id=?,decision_version=?,
                           decision_reason=?,actor=?,decided_at=?
                           WHERE project_id=? AND id=? AND decision_version=?''',
                        (move['status_after'],move['canonical_after'],new_version,reason,
                         'merge-ledger',None if move['status_after']=='pending' else now,
                         project_id,move['id'],move['expected_version']))
                    if cursor.rowcount!=1:
                        raise ValueError('版本冲突：合并后断言已变更')
                    self.repository._db.execute(
                        '''INSERT INTO assertion_events
                           (id,assertion_id,project_id,from_status,to_status,decision_version,
                            reason,actor,canonical_record_id,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (str(uuid4()), move['id'], project_id, move['status_before'],
                         move['status_after'],new_version,reason,'merge-ledger',
                         move['canonical_after'],now))
                    if move['status_after'] == 'accepted':
                        self._map_accepted_assertion(project_id, {
                            'id': move['id'], 'decision_version': new_version,
                            'canonical_record_id': move['canonical_after'],
                        }, record_version_id)
                    assertion_moves.append({
                        'id':move['id'],
                        'move_kind':('status' if move['status_before']!=move['status_after']
                                     else 'canonical'),
                        'from':move['canonical_before'],'to':move['canonical_after'],
                        'status_before':move['status_before'],
                        'status_after':move['status_after'],
                        'decision_version_before':move['expected_version'],
                        'decision_version_after':new_version,
                        'reason':reason,'actor':'merge-ledger',
                    })
                self.repository._db.execute(
                    '''INSERT INTO merge_operations
                       (id,project_id,operation,status,redirects,assertion_moves,before_state,
                        after_state,expected_versions,reversal_of,created_at)
                       VALUES (?,?,?,'applied',?,?,?,?,?,?,?)''',
                    (ledger['id'], project_id, ledger.get('operation', operation),
                     json.dumps(ledger.get('redirects', {}), ensure_ascii=False, sort_keys=True),
                     json.dumps(assertion_moves, ensure_ascii=False, sort_keys=True),
                     json.dumps(ledger.get('before_state', []), ensure_ascii=False, sort_keys=True),
                     json.dumps([{key: value for key, value in row.items() if key != 'embedding'}
                                 for row in result_records], ensure_ascii=False, sort_keys=True),
                     json.dumps(ledger.get('expected_versions', {}), ensure_ascii=False, sort_keys=True),
                     reversal_of, utc_now()))
        return {
            'accepted_records': result_records,
            'assertion_updates': assertion_updates,
            'review_items': policy.get('resolution_reviews', []),
            'ontology_proposals': [],
            'diagnostics': {'operation': operation, 'record_count': len(result_records),
                            'assertion_count': len(assertion_updates)},
        }
