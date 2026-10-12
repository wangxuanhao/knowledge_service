"""Service-owned planning for atomic formal fact writes.

The planner consumes one detached repository snapshot and turns every business
decision into an immutable action.  The repository later performs only CAS,
fingerprint validation, and mechanical execution of those actions.
"""

from __future__ import annotations

import copy
import hashlib
import json

from ..domain.formal_facts import (
    FORMAL_OPERATIONS, SOURCE_METADATA, canonical_attribute_key,
    canonical_relation_key, manual_assertion_id, typed_value_identity)
from ..utils.assertions import ALLOWED_TRANSITIONS, occurrence_id
from .ontology import Ontology


def _fingerprint(value):
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _writable(record):
    return {key: copy.deepcopy(value) for key, value in record.items() if key not in {
        'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}}


def _assertion_comparable(item):
    return {key: item.get(key) for key in (
        'id', 'kind', 'document_id', 'document_version_id', 'chunk_id',
        'source_hash', 'start_char', 'end_char', 'quote', 'payload')}


class _Planner:
    def __init__(self, snapshot, operation, assertions, expected, policy):
        self.snapshot = copy.deepcopy(snapshot)
        self.operation = operation
        self.expected = expected
        self.policy = copy.deepcopy(policy)
        self.records = {row['id']: row for row in self.snapshot['records']}
        self.assertions = {row['id']: row for row in self.snapshot['assertions']}
        self.reviews = {
            row['id']: row for row in self.snapshot['resolution_reviews']}
        self.merge_operations = {
            row['id']: row for row in self.snapshot['merge_operations']}
        self.fact_keys = [dict(row) for row in self.snapshot['fact_keys']]
        self.mappings = list(self.snapshot['record_version_assertions'])
        self.actions = []
        self.result_refs = []
        self.assertion_result_ids = []
        self.canonical_by_input = {}
        self.record_refs = {
            row['id']: {'version_id': row['version_id']}
            for row in self.snapshot['records']}
        self.assertion_moves = []
        self.fact_redirects = []
        self.queued_move_ids = set()
        self.input_assertions = list(copy.deepcopy(assertions or []))
        self.records_input = list(self.policy.get('records', []))
        self._token_sequence = 0

    def _token(self):
        self._token_sequence += 1
        return f'record-{self._token_sequence}'

    def _plan_record_put(self, record, expected_version):
        token = self._token()
        action = {
            'kind': 'record_put', 'token': token,
            'record': copy.deepcopy(record),
            'expected_version': expected_version,
        }
        self.actions.append(action)
        self.result_refs.append({'token': token})
        previous = self.records.get(record['id'])
        simulated = copy.deepcopy(record)
        simulated.update({
            'project_id': self.snapshot['project_id'],
            'version': (previous['version'] + 1 if previous else 1),
            'version_id': None,
        })
        self.records[record['id']] = simulated
        self.record_refs[record['id']] = {'token': token}
        return {'token': token}

    def _return_existing(self, record_id):
        ref = copy.deepcopy(self.record_refs[record_id])
        self.result_refs.append(ref)
        return ref

    def _active_key_for_record(self, record_id):
        return next((row for row in self.fact_keys
                     if row['canonical_record_id'] == record_id
                     and row.get('retired_at') is None), None)

    def _active_key(self, fact_key):
        return next((row for row in self.fact_keys
                     if row['fact_key'] == fact_key
                     and row.get('retired_at') is None), None)

    def _retire_key(self, record_id):
        active = self._active_key_for_record(record_id)
        if active is None:
            return
        self.actions.append({
            'kind': 'fact_key_retire', 'record_id': record_id,
            'fact_key': active['fact_key'],
        })
        active['retired_at'] = '<planned>'

    def _insert_key(self, fact_key, record_id):
        self.actions.append({
            'kind': 'fact_key_insert', 'fact_key': fact_key,
            'record_id': record_id,
        })
        self.fact_keys.append({
            'fact_key': fact_key, 'canonical_record_id': record_id,
            'retired_at': None,
        })

    def _retarget_key(self, fact_key, source, target):
        self.actions.append({
            'kind': 'fact_key_retarget', 'fact_key': fact_key,
            'from_record_id': source, 'to_record_id': target,
        })
        active = self._active_key(fact_key)
        if active is None or active['canonical_record_id'] != source:
            raise ValueError('版本冲突：事实键已变更')
        active['canonical_record_id'] = target

    @staticmethod
    def _canonical_record(record):
        canonical = copy.deepcopy(record)
        if canonical['kind'] in {'entity', 'relation', 'attribute'}:
            canonical.pop('source_id', None)
            canonical['metadata'] = {
                key: value for key, value in canonical.get('metadata', {}).items()
                if key not in SOURCE_METADATA}
        return canonical

    def _source_assertion(self, record, expected_version, ordinal):
        metadata = record.get('metadata', {})
        raw_terms = [str(record.get(key, '')) for key in
                     ('subject_id', 'type', 'object_id', 'text')]
        typed_value = typed_value_identity(record)
        if typed_value is not None:
            raw_terms.extend(typed_value)
        document_version_id = metadata.get('source_version_id')
        chunk_id = metadata.get('chunk_id')
        if document_version_id and chunk_id:
            assertion_id = occurrence_id(
                self.snapshot['storage_namespace'], document_version_id, chunk_id,
                record['kind'], ordinal, raw_terms)
        else:
            assertion_id = manual_assertion_id(
                self.snapshot['storage_namespace'], self.operation, record,
                expected_version)
        start_char = metadata.get('start_char')
        end_char = metadata.get('end_char')
        if type(start_char) is int and end_char is None:
            end_char = start_char + len(record.get('text', ''))
        return {
            'id': assertion_id, 'kind': record['kind'],
            'document_id': record.get('source_id'),
            'document_version_id': document_version_id,
            'chunk_id': chunk_id, 'source_hash': metadata.get('source_hash'),
            'start_char': start_char, 'end_char': end_char,
            'quote': (metadata.get('passage') or metadata.get('evidence')
                      or record.get('text')),
            'payload': {key: value for key, value in record.items()
                        if key not in {'embedding', 'embedding_model'}},
            'actor': 'formal-writer',
        }

    def _queue_assertion_accept(self, item, canonical_id, record_ref):
        assertion = copy.deepcopy(item)
        assertion.pop('canonical_record_id', None)
        assertion.pop('record_id', None)
        existing = self.assertions.get(assertion['id'])
        if existing is None:
            mode = 'create'
            resulting_version = 2
        else:
            if _assertion_comparable(existing) != _assertion_comparable(assertion):
                raise ValueError('断言出现 ID 冲突')
            if existing['status'] == 'pending':
                mode = 'pending'
                resulting_version = existing['decision_version'] + 1
            elif (existing['status'] == 'accepted'
                  and existing['canonical_record_id'] == canonical_id):
                mode = 'replay'
                resulting_version = existing['decision_version']
                if existing.get('_planned_accept_ref') not in (None, record_ref):
                    raise ValueError('断言重放与其版本支撑映射冲突')
            else:
                raise ValueError('断言重放与其规范决策冲突')
        self.actions.append({
            'kind': 'assertion_accept', 'mode': mode,
            'operation': self.operation,
            'assertion': assertion, 'canonical_record_id': canonical_id,
            'record_ref': copy.deepcopy(record_ref),
            'resulting_version': resulting_version,
            'reason': f'{self.operation}: 规范写入已接受',
            'actor': assertion.get('actor', 'formal-writer'),
            'result': True,
        })
        resulting = copy.deepcopy(existing or assertion)
        resulting.update({
            'status': 'accepted', 'canonical_record_id': canonical_id,
            'decision_version': resulting_version,
            '_planned_accept_ref': copy.deepcopy(record_ref),
        })
        self.assertions[assertion['id']] = resulting
        self.assertion_result_ids.append(assertion['id'])

    def _queue_transition(self, decision, *, ledger=False, include_result=True):
        current = self.assertions.get(decision['id'])
        if current is None:
            raise KeyError(decision['id'])
        if current['decision_version'] != decision['expected_version']:
            raise ValueError('版本冲突：断言已变更')
        status = decision['status']
        old_status = current['status']
        if status not in ALLOWED_TRANSITIONS[old_status]:
            raise ValueError(f'无效的断言状态转换：{old_status} -> {status}')
        target = decision.get('canonical_record_id')
        target = self.canonical_by_input.get(target, target)
        if target is None:
            target = current.get('canonical_record_id')
        if status == 'accepted' and not target:
            raise ValueError('接受的断言必须关联规范记录')
        record_ref = copy.deepcopy(self.record_refs[target]) if status == 'accepted' else None
        action = {
            'kind': 'assertion_transition', 'id': decision['id'],
            'expected_version': decision['expected_version'],
            'status': status, 'reason': decision['reason'],
            'actor': decision['actor'], 'canonical_record_id': target,
            'record_ref': record_ref,
            'result': include_result,
        }
        self.actions.append(action)
        updated = copy.deepcopy(current)
        updated.update({
            'status': status,
            'canonical_record_id': None if status == 'pending' else target,
            'decision_version': decision['expected_version'] + 1,
        })
        self.assertions[decision['id']] = updated
        if include_result:
            self.assertion_result_ids.append(decision['id'])
        if ledger:
            move = {
                'id': decision['id'], 'move_kind': 'status',
                'from': current.get('canonical_record_id'), 'to': target,
                'status_before': old_status, 'status_after': status,
                'decision_version_before': current['decision_version'],
                'decision_version_after': updated['decision_version'],
                'reason': decision['reason'], 'actor': decision['actor'],
            }
            self.assertion_moves.append(move)
            self.queued_move_ids.add(decision['id'])

    def _plan_records(self):
        completion = self.policy.get('completion')
        if completion:
            document, document_version = completion
            self._plan_record_put(
                document, self.expected.get(document['id'], document_version))
        auto = []
        for ordinal, original in enumerate(self.records_input):
            record = self._canonical_record(original)
            expected_version = self.expected.get(record['id'])
            canonical_id = record['id']
            selected_ref = None
            if record['kind'] in {'relation', 'attribute'}:
                old_key = self._active_key_for_record(record['id'])
                if record.get('metadata', {}).get('_deleted'):
                    self._retire_key(record['id'])
                else:
                    fact_key = (canonical_relation_key(record)
                                if record['kind'] == 'relation'
                                else canonical_attribute_key(record))
                    mapped = self._active_key(fact_key)
                    if mapped and mapped['canonical_record_id'] != record['id']:
                        mapped_id = mapped['canonical_record_id']
                        if mapped_id not in self.records:
                            raise RuntimeError('事实键指向不存在的规范记录')
                        if self.operation == 'merge_rewrite' and expected_version:
                            winner = min(mapped_id, record['id'])
                            if old_key:
                                self._retire_key(record['id'])
                                old_key = None
                            if winner == mapped_id:
                                record['metadata'] = {
                                    **record.get('metadata', {}), '_deleted': True,
                                    'merged_into_fact': winner, 'fact_collision': True}
                                self._plan_record_put(record, expected_version)
                                self.canonical_by_input[original['id']] = winner
                                self.fact_redirects.append((record['id'], winner))
                                continue
                            occupied = self.records[mapped_id]
                            tombstone = _writable(occupied)
                            tombstone['metadata'] = {
                                **tombstone.get('metadata', {}), '_deleted': True,
                                'merged_into_fact': winner, 'fact_collision': True,
                                **({'_operation_id': record['metadata']['_operation_id']}
                                   if record.get('metadata', {}).get('_operation_id')
                                   else {}),
                            }
                            self._plan_record_put(tombstone, occupied['version'])
                            self._retarget_key(fact_key, mapped_id, winner)
                            for audit in self.records_input:
                                if (audit['kind'] == 'document'
                                        and audit.get('metadata', {}).get('_audit')):
                                    before = audit['metadata'].setdefault('before', [])
                                    if not any(item.get('id') == mapped_id for item in before):
                                        before.append({key: value for key, value in occupied.items()
                                                       if key != 'embedding'})
                            self.fact_redirects.append((mapped_id, winner))
                            canonical_id = winner
                            # The occupied key was explicitly retargeted above;
                            # keep it marked as mapped so the generic new-key
                            # path cannot insert a second active row.
                            mapped = {'canonical_record_id': winner}
                        else:
                            canonical_id = mapped_id
                            selected_ref = copy.deepcopy(self.record_refs[canonical_id])
                            self.canonical_by_input[original['id']] = canonical_id
                            if old_key:
                                source = self.records.get(record['id'])
                                if source is None:
                                    raise RuntimeError('旧事实键指向不存在的源记录')
                                tombstone = _writable(source)
                                tombstone['metadata'] = {
                                    **tombstone.get('metadata', {}), '_deleted': True,
                                    'merged_into_fact': canonical_id,
                                    'fact_collision': True,
                                }
                                self._retire_key(record['id'])
                                self._plan_record_put(tombstone, expected_version)
                                self._return_existing(canonical_id)
                            elif self.operation == 'legacy_import':
                                record['metadata'] = {
                                    **record.get('metadata', {}), '_deleted': True,
                                    'merged_into_fact': canonical_id,
                                    'legacy_parallel_occurrence': True,
                                }
                                self._plan_record_put(record, expected_version)
                            else:
                                self._return_existing(canonical_id)
                            if not self.policy.get('suppress_auto_assertions'):
                                auto.append((
                                    self._source_assertion(
                                        original, expected_version or 0, ordinal),
                                    canonical_id, selected_ref))
                            continue
                    if old_key and old_key['fact_key'] != fact_key:
                        self._retire_key(record['id'])
                    if mapped is None:
                        self._insert_key(fact_key, record['id'])
            selected_ref = self._plan_record_put(record, expected_version)
            self.canonical_by_input[original['id']] = canonical_id
            if (self.operation == 'merge_rewrite' and expected_version is not None
                    and record['kind'] in {'relation', 'attribute'}
                    and not record.get('metadata', {}).get('_deleted')):
                self.fact_redirects.append((canonical_id, canonical_id))
            if (not self.policy.get('suppress_auto_assertions')
                    and self.operation in {'extract', 'manual_write', 'approve_review',
                                           'legacy_import', 'adopt_discovery'}
                    and original['kind'] in {'entity', 'relation', 'attribute'}
                    and not record.get('metadata', {}).get('_deleted')):
                auto.append((
                    self._source_assertion(
                        original, expected_version or 0, ordinal),
                    canonical_id, selected_ref))
        return auto

    def _plan_assertions(self, auto):
        explicit = [(item, None) for item in self.input_assertions]
        explicit.extend(({
            **assertion, 'canonical_record_id': canonical_id}, record_ref)
                        for assertion, canonical_id, record_ref in auto)
        for item, selected_ref in explicit:
            canonical_id = (item.get('canonical_record_id')
                            or item.get('record_id'))
            canonical_id = self.canonical_by_input.get(canonical_id, canonical_id)
            if not canonical_id:
                raise ValueError('正式断言需要规范记录目标')
            record_ref = selected_ref or self.record_refs.get(canonical_id)
            if record_ref is None:
                raise ValueError('正式断言的规范记录目标不存在')
            self._queue_assertion_accept(item, canonical_id, record_ref)
        for item in self.policy.get('pending_assertions', []):
            existing = self.assertions.get(item['id'])
            if existing is not None and _assertion_comparable(existing) != _assertion_comparable(item):
                raise ValueError('断言出现 ID 冲突')
            self.actions.append({
                'kind': 'assertion_create', 'assertion': copy.deepcopy(item),
                'result': True})
            if existing is None:
                created = copy.deepcopy(item)
                created.update({'status': 'pending', 'canonical_record_id': None,
                                'decision_version': 1})
                self.assertions[item['id']] = created
            self.assertion_result_ids.append(item['id'])
        for decision in self.policy.get('assertion_decisions', []):
            self._queue_transition(decision, ledger=bool(self.policy.get('ledger')))

    def _plan_reviews(self):
        for review in self.policy.get('resolution_reviews', []):
            self.actions.append({
                'kind': 'resolution_review_create',
                'review': copy.deepcopy(review)})
        for decision in self.policy.get('resolution_decisions', []):
            current = self.reviews.get(decision['id'])
            if current is None:
                raise KeyError(decision['id'])
            self.actions.append({
                'kind': 'resolution_review_decide', **copy.deepcopy(decision)})

    def _plan_source_retraction(self):
        if self.operation != 'retract_source':
            return
        source_ids = {
            record['id'] for record in self.records_input
            if record['kind'] == 'document'
            and record.get('metadata', {}).get('_deleted')}
        affected = set()
        for assertion in sorted(self.assertions.values(), key=lambda row: row['id']):
            if (assertion.get('document_id') not in source_ids
                    or assertion['status'] not in {'pending', 'accepted', 'contradicting'}):
                continue
            if assertion.get('canonical_record_id'):
                affected.add(assertion['canonical_record_id'])
            decision = {
                'id': assertion['id'],
                'expected_version': assertion['decision_version'],
                'status': 'superseded', 'reason': '来源文档已撤回',
                'actor': 'formal-writer',
                'canonical_record_id': assertion.get('canonical_record_id'),
            }
            self._queue_transition(decision, include_result=False)
        for canonical_id in sorted(affected):
            current = self.records.get(canonical_id)
            if current is None:
                continue
            current_ref = self.record_refs[canonical_id]
            current_version_id = current_ref.get('version_id')
            supported = any(
                row['status'] == 'accepted'
                and row.get('canonical_record_id') == canonical_id
                and row.get('_planned_accept_ref') == current_ref
                for row in self.assertions.values())
            if current_version_id is not None:
                accepted = {
                    row['id'] for row in self.assertions.values()
                    if row['status'] == 'accepted'
                    and row.get('canonical_record_id') == canonical_id}
                supported = supported or any(
                    row['assertion_id'] in accepted
                    and row['record_id'] == canonical_id
                    and row['record_version_id'] == current_version_id
                    for row in self.mappings)
            if supported:
                continue
            writable = _writable(current)
            if current['kind'] in {'relation', 'attribute'}:
                writable['metadata'] = {
                    **current.get('metadata', {}), '_deleted': True,
                    'unsupported_reason': '最后一个已接受的来源已被撤回'}
                self._retire_key(canonical_id)
            elif current['kind'] == 'entity':
                writable['metadata'] = {
                    **current.get('metadata', {}), '_unsupported': True,
                    'unsupported_reason': '最后一个已接受的来源已被撤回'}
            else:
                continue
            self._plan_record_put(writable, current['version'])

    def _queue_move(self, assertion_id, canonical_after, status_after=None):
        current = self.assertions.get(assertion_id)
        if current is None:
            raise ValueError('版本冲突：合并后断言已变更')
        status_after = current['status'] if status_after is None else status_after
        reason = (f'{self.operation}: {current.get("canonical_record_id")} -> '
                  f'{canonical_after}; {current["status"]} -> {status_after}')
        record_ref = (copy.deepcopy(self.record_refs[canonical_after])
                      if status_after == 'accepted' else None)
        action = {
            'kind': 'assertion_move', 'id': assertion_id,
            'canonical_before': current.get('canonical_record_id'),
            'canonical_after': canonical_after,
            'status_before': current['status'], 'status_after': status_after,
            'expected_version': current['decision_version'],
            'reason': reason, 'actor': 'merge-ledger',
            'record_ref': record_ref,
        }
        self.actions.append(action)
        move = {
            'id': assertion_id,
            'move_kind': ('status' if current['status'] != status_after
                          else 'canonical'),
            'from': current.get('canonical_record_id'), 'to': canonical_after,
            'status_before': current['status'], 'status_after': status_after,
            'decision_version_before': current['decision_version'],
            'decision_version_after': current['decision_version'] + 1,
            'reason': reason, 'actor': 'merge-ledger',
        }
        self.assertion_moves.append(move)
        updated = copy.deepcopy(current)
        updated.update({
            'canonical_record_id': canonical_after,
            'status': status_after,
            'decision_version': current['decision_version'] + 1,
        })
        self.assertions[assertion_id] = updated
        self.queued_move_ids.add(assertion_id)

    def _plan_ledger(self):
        ledger = self.policy.get('ledger')
        if not ledger:
            return
        reversal_of = ledger.get('reversal_of')
        if reversal_of:
            if self.operation != 'merge_reversal':
                raise ValueError('只有合并回退操作可以反向应用断言移动账本')
            prior = self.merge_operations.get(reversal_of)
            if prior is None:
                raise ValueError('合并回退目标不存在')
            grouped = {}
            for move in prior['assertion_moves']:
                grouped.setdefault(move['id'], []).append(move)
            for assertion_id in sorted(grouped):
                historical = grouped[assertion_id]
                current = self.assertions.get(assertion_id)
                if current is None:
                    raise ValueError('版本冲突：合并后断言已变更')
                normalized = [{
                    'canonical_before': move.get('from'),
                    'canonical_after': move.get('to'),
                    'status_before': move.get('status_before', current['status']),
                    'status_after': move.get('status_after', current['status']),
                    'decision_version_before': move.get(
                        'decision_version_before', move.get('expected_version')),
                    'decision_version_after': move['decision_version_after'],
                } for move in historical]
                for previous, following in zip(normalized, normalized[1:]):
                    if (previous['canonical_after'] != following['canonical_before']
                            or previous['status_after'] != following['status_before']
                            or previous['decision_version_after']
                            != following['decision_version_before']):
                        raise ValueError('版本冲突：合并断言移动账本不连续')
                latest = normalized[-1]
                if (current.get('canonical_record_id') != latest['canonical_after']
                        or current['status'] != latest['status_after']
                        or current['decision_version'] != latest['decision_version_after']):
                    raise ValueError('版本冲突：合并后断言已变更')
                for move in reversed(normalized):
                    self._queue_move(
                        assertion_id, move['canonical_before'],
                        move['status_before'])
        else:
            redirects = list(ledger.get('redirects', {}).items())
            redirects.extend(dict.fromkeys(self.fact_redirects))
            for source, target in redirects:
                matching = sorted(
                    (row for row in self.assertions.values()
                     if row.get('canonical_record_id') == source
                     and row['id'] not in self.queued_move_ids),
                    key=lambda row: row['id'])
                for assertion in matching:
                    self._queue_move(assertion['id'], target)
        self.actions.append({
            'kind': 'merge_ledger', 'id': ledger['id'],
            'operation': ledger.get('operation', self.operation),
            'redirects': copy.deepcopy(ledger.get('redirects', {})),
            'assertion_moves': copy.deepcopy(self.assertion_moves),
            'before_state': copy.deepcopy(ledger.get('before_state', [])),
            'expected_versions': copy.deepcopy(ledger.get('expected_versions', {})),
            'reversal_of': reversal_of,
            'after_state_refs': copy.deepcopy(self.result_refs),
        })

    def build(self):
        auto = self._plan_records()
        self._plan_assertions(auto)
        self._plan_reviews()
        self._plan_source_retraction()
        self._plan_ledger()
        return self.actions, self.result_refs, self.assertion_result_ids


class FormalFactWriter:
    """Prepare a deterministic action plan and delegate atomic execution."""

    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _reject_deprecated_write_term(record, ontology, operation):
        if (ontology is None
                or record.get('kind') not in {'entity', 'relation', 'attribute'}
                or record.get('metadata', {}).get('_deleted')):
            return
        declared = ontology.classes | ontology.relations | ontology.attributes
        exact = [term for term in declared if str(term) == record.get('type', '')]
        try:
            term = exact[0] if len(exact) == 1 else ontology.resolve(
                record.get('type', ''))
        except ValueError:
            return
        if not ontology.is_active_term(term) and operation != 'merge_reversal':
            raise ValueError(f'不能使用已停用的本体术语创建新事实：{term}')

    @staticmethod
    def _dependencies(snapshot):
        return {
            'snapshot_fingerprint': snapshot['fingerprint'],
            'ontology_id': snapshot.get('ontology_id'),
            'records': {row['id']: row['version_id'] for row in snapshot['records']},
            'assertions': {
                row['id']: row['decision_version'] for row in snapshot['assertions']},
            'fact_keys': _fingerprint(snapshot['fact_keys']),
            'merge_operations': {
                row['id']: _fingerprint(row) for row in snapshot['merge_operations']},
            'resolution_reviews': {
                row['id']: row['decision_version']
                for row in snapshot['resolution_reviews']},
        }

    def prepare(self, operation, project_id, assertions, expected, policy,
                recorded_at=None, operation_context=None):
        if operation not in FORMAL_OPERATIONS:
            raise ValueError('不支持的正式操作')
        if not isinstance(expected, dict) or any(
                not isinstance(key, str) or type(value) is not int or value < 0
                for key, value in expected.items()):
            raise ValueError('正式期望必须将 ID 映射到版本')
        if operation_context is not None and recorded_at is not None:
            raise ValueError('写入操作上下文不能与 recorded_at 同时指定')
        snapshot = self.repository.formal_write_snapshot(project_id)
        ontology_row = snapshot.get('ontology')
        ontology = Ontology(ontology_row['turtle']) if ontology_row else None
        frozen_policy = copy.deepcopy(policy)
        for record in frozen_policy.get('records', []):
            self._reject_deprecated_write_term(record, ontology, operation)
        planner = _Planner(
            snapshot, operation, assertions, expected, frozen_policy)
        actions, result_refs, assertion_result_ids = planner.build()
        plan = {
            'operation': operation, 'project_id': project_id,
            'actions': actions, 'result_refs': result_refs,
            'assertion_result_ids': assertion_result_ids,
            'review_items': copy.deepcopy(
                frozen_policy.get('resolution_reviews', [])),
            'dependencies': self._dependencies(snapshot),
            'recorded_at': recorded_at,
            'operation_context': self.repository.formal_write_context(
                operation_context),
        }
        plan['fingerprint'] = _fingerprint(plan)
        return plan

    def apply(self, operation, project_id, assertions, expected, policy,
              recorded_at=None, operation_context=None):
        return self.repository.execute_formal_write(self.prepare(
            operation, project_id, assertions, expected, policy,
            recorded_at=recorded_at, operation_context=operation_context))


__all__ = [
    'FormalFactWriter', 'canonical_attribute_key', 'canonical_relation_key',
]
