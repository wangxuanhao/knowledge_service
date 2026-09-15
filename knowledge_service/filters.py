"""Validated, typed predicates evaluated before any vector retrieval."""
import json
import re

from .time import normalize_time

_MISSING = object()
_TOP = {'id', 'kind', 'type', 'source_id', 'valid_from', 'valid_until', 'recorded_at'}
_TIME = {'valid_from', 'valid_until', 'recorded_at'}
_OPS = {'eq', 'ne', 'in', 'contains', 'gt', 'gte', 'lt', 'lte', 'exists'}


def validate_filter(filters, _depth=0):
    if filters is None and _depth == 0:
        return
    if _depth > 16 or not isinstance(filters, dict):
        raise ValueError('Invalid filter or nesting exceeds 16')
    groups = set(filters) & {'and', 'or'}
    if groups:
        if len(filters) != 1:
            raise ValueError('Logical filter must have exactly one key')
        children = filters[next(iter(groups))]
        if not isinstance(children, list) or not children or len(children) > 100:
            raise ValueError('Logical filters require 1 to 100 children')
        for child in children:
            validate_filter(child, _depth + 1)
        return
    if set(filters) != {'field', 'op', 'value'}:
        raise ValueError('Filter requires field, op and value')
    field, op, value = filters['field'], filters['op'], filters['value']
    if not isinstance(field, str) or len(field) > 256 or not re.fullmatch(r'[^\W\d][\w-]*(?:\.[^\W\d][\w-]*)*', field):
        raise ValueError('Invalid field path')
    if not isinstance(op, str) or op not in _OPS:
        raise ValueError('Unsupported filter operator')
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError('Filter values must be JSON') from exc
    if op == 'in' and not isinstance(value, list):
        raise ValueError('in requires a list')
    if op == 'exists' and not isinstance(value, bool):
        raise ValueError('exists requires a boolean')
    if op in {'gt', 'gte', 'lt', 'lte'} and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
        raise ValueError('Comparison requires a number or string')
    if field in _TIME and op != 'exists':
        for item in value if op == 'in' else [value]:
            normalize_time(item)


def _equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return a == b


def matches_filter(record, filters):
    validate_filter(filters)
    return _matches(record, filters)


def _matches(record, filters):
    if filters is None:
        return True
    if 'and' in filters:
        return all(_matches(record, f) for f in filters['and'])
    if 'or' in filters:
        return any(_matches(record, f) for f in filters['or'])
    field, op, target = filters['field'], filters['op'], filters['value']
    if field in _TOP:
        actual = record.get(field, _MISSING)
    else:
        actual = record.get('metadata', {})
        parts = field.split('.')
        if parts[0] == 'metadata':
            parts = parts[1:]
        for part in parts:
            actual = actual.get(part, _MISSING) if isinstance(actual, dict) else _MISSING
    if op == 'exists':
        return (actual is not _MISSING) == target
    if actual is _MISSING:
        return False
    if field in _TIME:
        actual = normalize_time(actual)
        target = [normalize_time(v) for v in target] if op == 'in' else normalize_time(target)
    if op in {'eq', 'ne'}:
        return _equal(actual, target) if op == 'eq' else not _equal(actual, target)
    if op == 'in':
        return any(_equal(actual, v) for v in target)
    if op == 'contains':
        if isinstance(actual, list):
            return any(_equal(v, target) for v in actual)
        return isinstance(actual, str) and isinstance(target, str) and target in actual
    comparable = (isinstance(actual, (int, float)) and not isinstance(actual, bool) and isinstance(target, (int, float)) and not isinstance(target, bool)) or (isinstance(actual, str) and isinstance(target, str))
    if not comparable:
        return False
    return {'gt': lambda: actual > target, 'gte': lambda: actual >= target, 'lt': lambda: actual < target, 'lte': lambda: actual <= target}[op]()
