"""Service-owned preparation for atomic formal fact writes."""

from __future__ import annotations

import copy
import hashlib
import json

from ..domain.formal_facts import (
    FORMAL_OPERATIONS, canonical_attribute_key, canonical_relation_key)
from ..repository import OntologyNotPublished
from .ontology import Ontology


def _fingerprint(value):
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


class FormalFactWriter:
    """Prepare a frozen write plan and delegate its atomic execution."""

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
        try:
            ontology_row = self.repository.get_ontology(project_id)
            ontology = Ontology(ontology_row['turtle'])
            ontology_id = ontology_row['id']
        except OntologyNotPublished:
            ontology = None
            ontology_id = None
        frozen_policy = copy.deepcopy(policy)
        for record in frozen_policy.get('records', []):
            self._reject_deprecated_write_term(record, ontology, operation)
        plan = {
            'operation': operation, 'project_id': project_id,
            'assertions': copy.deepcopy(assertions or []),
            'expected': copy.deepcopy(expected), 'policy': frozen_policy,
            'recorded_at': recorded_at,
            'operation_context': self.repository.formal_write_context(
                operation_context),
            'expected_ontology_id': ontology_id,
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
