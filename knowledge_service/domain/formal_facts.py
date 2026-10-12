"""Pure identities and value rules used by formal fact writes."""

from __future__ import annotations

import hashlib
import json


FORMAL_OPERATIONS = frozenset({
    'extract', 'manual_write', 'approve_review', 'legacy_import',
    'adopt_discovery', 'publish_discovery_draft', 'ontology_publish',
    'ontology_remap', 'merge_rewrite', 'merge_reversal', 'retract_source',
    'restore_snapshot',
})

SOURCE_METADATA = frozenset({
    'chunk_id', 'start_char', 'end_char', 'source_version_id',
    'source_hash', 'passage', 'evidence',
})


def deterministic_json(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def typed_value_identity(record):
    if record.get('kind') != 'attribute':
        return None
    return [record.get('datatype'), deterministic_json(record.get('value'))]


def canonical_relation_key(record):
    required = ('type', 'subject_id', 'object_id')
    if any(not isinstance(record.get(key), str) or not record[key]
           for key in required):
        raise ValueError('规范关系键需要谓词和实体端点')
    identity = [
        record['type'], record['subject_id'], record['object_id'],
        record.get('valid_from'), record.get('valid_until'),
    ]
    return 'fact_' + hashlib.sha256(
        deterministic_json(identity).encode('utf-8')).hexdigest()


def canonical_attribute_key(record):
    required = ('subject_id', 'type', 'datatype')
    if (any(not isinstance(record.get(key), str) or not record[key]
            for key in required) or record.get('value') is None):
        raise ValueError('规范属性键需要主体、谓词、值和数据类型')
    identity = [
        record['subject_id'], record['type'], record['value'],
        record['datatype'], record.get('valid_from'), record.get('valid_until'),
    ]
    return 'fact_' + hashlib.sha256(
        deterministic_json(identity).encode('utf-8')).hexdigest()


def manual_assertion_id(namespace, operation, record, expected_version):
    payload = json.dumps({
        'namespace': namespace, 'operation': operation,
        'record_id': record['id'], 'version': expected_version + 1,
        'kind': record['kind'], 'type': record.get('type'),
        'subject_id': record.get('subject_id'),
        'object_id': record.get('object_id'), 'text': record.get('text'),
        'typed_value': typed_value_identity(record),
    }, ensure_ascii=False, allow_nan=False, sort_keys=True,
       separators=(',', ':')).encode('utf-8')
    return 'ast_manual_' + hashlib.sha256(payload).hexdigest()


__all__ = [
    'FORMAL_OPERATIONS', 'SOURCE_METADATA', 'canonical_attribute_key',
    'canonical_relation_key', 'manual_assertion_id', 'typed_value_identity',
]
