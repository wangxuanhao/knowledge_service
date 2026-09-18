"""规范图谱变更的唯一事务入口。"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from .assertions import occurrence_id
from .time import utc_now


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


def canonical_relation_key(record):
    required = ('type', 'subject_id', 'object_id')
    if any(not isinstance(record.get(key), str) or not record[key] for key in required):
        raise ValueError('规范关系键需要谓词和实体端点')
    identity = [
        record['type'], record['subject_id'], record['object_id'],
        record.get('valid_from'), record.get('valid_until'),
    ]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return 'fact_' + hashlib.sha256(encoded).hexdigest()


def _manual_assertion_id(namespace, operation, record, expected_version):
    payload = json.dumps({
        'namespace': namespace, 'operation': operation, 'record_id': record['id'],
        'version': expected_version + 1, 'kind': record['kind'],
        'type': record.get('type'), 'subject_id': record.get('subject_id'),
        'object_id': record.get('object_id'), 'text': record.get('text'),
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
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
        if canonical['kind'] in {'entity', 'relation'}:
            canonical.pop('source_id', None)
            canonical['metadata'] = {
                key: value for key, value in canonical.get('metadata', {}).items()
                if key not in _SOURCE_METADATA
            }
        return canonical

    def apply(self, operation, project_id, assertions, expected, policy, recorded_at=None):
        if operation not in FORMAL_OPERATIONS:
            raise ValueError('不支持的正式操作')
        if not isinstance(expected, dict) or any(
                not isinstance(key, str) or type(value) is not int or value < 0
                for key, value in expected.items()):
            raise ValueError('正式期望必须将 ID 映射到版本')
        # 批次系统时间点：整批共享一个 recorded_at，落库循环不再逐条 utc_now()，
        # 否则一次批量写入会被 timeline 拆成与记录数等量的时间点。
        ts = recorded_at or utc_now()
        records = list(policy.get('records', []))
        completion = policy.get('completion')
        explicit_assertions = list(assertions or [])
        result_records = []
        assertion_updates = []
        canonical_by_input = {}
        fact_redirects = []
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
                    project_id, document, expected.get(document['id'], document_version), ts))
            for ordinal, original in enumerate(records):
                record = self._canonical_record(original)
                expected_version = expected.get(record['id'])
                canonical_id = record['id']
                if record['kind'] == 'relation':
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
                        fact_key = canonical_relation_key(record)
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
                                raise RuntimeError('事实键指向不存在的规范关系')
                            if operation == 'merge_rewrite' and expected_version:
                                winner=min(canonical_id,record['id'])
                                if old_key:
                                    self.repository._db.execute(
                                        '''UPDATE fact_keys SET retired_at=? WHERE project_id=?
                                           AND canonical_record_id=? AND retired_at IS NULL''',
                                        (utc_now(),project_id,record['id']))
                                if winner == canonical_id:
                                    record['metadata']={**record.get('metadata',{}),'_deleted':True,
                                        'merged_into_fact':winner,'fact_collision':True}
                                    saved=self.repository._put(project_id,record,expected_version,ts)
                                    canonical_by_input[original['id']]=winner
                                    result_records.append(saved)
                                    fact_redirects.append((record['id'],winner))
                                    continue
                                mapped_record=self.repository._record(row)
                                mapped_tombstone={key:value for key,value in mapped_record.items() if key not in {
                                    'project_id','version','version_id','recorded_at','superseded_at'}}
                                mapped_tombstone['metadata']={**mapped_tombstone.get('metadata',{}),
                                    '_deleted':True,'merged_into_fact':winner,'fact_collision':True}
                                result_records.append(self.repository._put(
                                    project_id,mapped_tombstone,mapped_record['version'],ts))
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
                                canonical_by_input[original['id']] = canonical_id
                                if operation=='legacy_import':
                                    record['metadata']={**record.get('metadata',{}),'_deleted':True,
                                        'merged_into_fact':canonical_id,'legacy_parallel_occurrence':True}
                                    result_records.append(self.repository._put(
                                        project_id,record,expected_version,ts))
                                else:
                                    result_records.append(saved)
                                source = self._source_assertion(operation, original, expected_version or 0, ordinal)
                                explicit_assertions.append({**source, 'canonical_record_id': canonical_id})
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
                saved = self.repository._put(project_id, record, expected_version, ts)
                result_records.append(saved)
                canonical_by_input[original['id']] = canonical_id
                if (not policy.get('suppress_auto_assertions') and
                        operation in {'extract','manual_write','approve_review','legacy_import','adopt_discovery'} and
                        original['kind'] in {'entity', 'relation'} and
                        not record.get('metadata', {}).get('_deleted')):
                    source = self._source_assertion(operation, original, expected_version or 0, ordinal)
                    explicit_assertions.append({**source, 'canonical_record_id': canonical_id})
            for item in explicit_assertions:
                assertion = dict(item)
                canonical_id = assertion.pop('canonical_record_id', None) or assertion.pop('record_id', None)
                if canonical_id in canonical_by_input:
                    canonical_id = canonical_by_input[canonical_id]
                if not canonical_id:
                    raise ValueError('正式断言需要规范记录目标')
                created = self.repository._create_assertion(project_id, assertion)
                if created['status'] == 'pending':
                    created = self.repository._transition_assertion(
                        project_id, created['id'], created['decision_version'], 'accepted',
                        f'{operation}: 规范写入已接受', assertion.get('actor', 'formal-writer'),
                        canonical_id)
                elif created['status'] != 'accepted' or created['canonical_record_id'] != canonical_id:
                    raise ValueError('断言重放与其规范决策冲突')
                assertion_updates.append(created)
            for item in policy.get('pending_assertions', []):
                assertion_updates.append(self.repository._create_assertion(project_id, item))
            for decision in policy.get('assertion_decisions', []):
                decision_target=decision.get('canonical_record_id')
                if decision_target in canonical_by_input:
                    decision_target=canonical_by_input[decision_target]
                assertion_updates.append(self.repository._transition_assertion(
                    project_id, decision['id'], decision['expected_version'],
                    decision['status'], decision['reason'], decision['actor'],
                    decision_target))
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
                    support = self.repository._db.execute(
                        '''SELECT COUNT(*) FROM assertions WHERE project_id=?
                           AND canonical_record_id=? AND status='accepted' ''',
                        (project_id, canonical_id)).fetchone()[0]
                    if support:
                        continue
                    current_row = self.repository._db.execute(
                        '''SELECT * FROM record_versions WHERE project_id=? AND id=?
                           AND superseded_at IS NULL''',
                        (project_id, canonical_id)).fetchone()
                    if current_row is None:
                        continue
                    current = self.repository._record(current_row)
                    writable = {key: value for key, value in current.items() if key not in {
                        'project_id','version','version_id','recorded_at','superseded_at'}}
                    if current['kind'] == 'relation':
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
                        project_id, writable, current['version'], ts))
            ledger = policy.get('ledger')
            assertion_moves = []
            if ledger:
                reversal_of = ledger.get('reversal_of')
                if reversal_of:
                    prior = self.repository._db.execute(
                        'SELECT assertion_moves FROM merge_operations WHERE project_id=? AND id=?',
                        (project_id, reversal_of)).fetchone()
                    if prior is None:
                        raise ValueError('合并回退目标不存在')
                    requested_moves = [{
                        'id': move['id'], 'from': move['to'], 'to': move['from'],
                        'expected_version': move['decision_version_after'],
                    } for move in json.loads(prior['assertion_moves'])]
                else:
                    requested_moves = []
                    for source, target in ledger.get('redirects', {}).items():
                        rows = self.repository._db.execute(
                            '''SELECT id,decision_version FROM assertions
                               WHERE project_id=? AND canonical_record_id=?''',
                            (project_id, source)).fetchall()
                        requested_moves.extend({'id': row['id'], 'from': source, 'to': target,
                                                'expected_version': row['decision_version']}
                                               for row in rows)
                    for source,target in fact_redirects:
                        rows=self.repository._db.execute(
                            '''SELECT id,decision_version FROM assertions
                               WHERE project_id=? AND canonical_record_id=?''',
                            (project_id,source)).fetchall()
                        requested_moves.extend({'id':row['id'],'from':source,'to':target,
                            'expected_version':row['decision_version']} for row in rows)
                for move in requested_moves:
                    row = self.repository._db.execute(
                        'SELECT * FROM assertions WHERE project_id=? AND id=?',
                        (project_id, move['id'])).fetchone()
                    if row is None or row['canonical_record_id'] != move['from'] or \
                            row['decision_version'] != move['expected_version']:
                        raise ValueError('版本冲突：合并后断言已变更')
                    new_version = row['decision_version'] + 1
                    now = utc_now()
                    self.repository._db.execute(
                        '''UPDATE assertions SET canonical_record_id=?,decision_version=?,
                           decision_reason=?,actor=?,decided_at=? WHERE id=?''',
                        (move['to'], new_version, f'{operation}: 规范重指派',
                         'merge-ledger', now, move['id']))
                    self.repository._db.execute(
                        '''INSERT INTO assertion_events
                           (id,assertion_id,project_id,from_status,to_status,decision_version,
                            reason,actor,canonical_record_id,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (str(uuid4()), move['id'], project_id, row['status'], row['status'],
                         new_version, f'{operation}: {move["from"]} -> {move["to"]}',
                         'merge-ledger', move['to'], now))
                    assertion_moves.append({**move, 'decision_version_after': new_version})
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
