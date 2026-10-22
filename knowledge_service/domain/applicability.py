"""Pure applicability normalization, identity and query selection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata

from ..core.time import normalize_time


DIMENSIONS = ('platforms', 'businesses', 'audiences')
MODES = frozenset({'specific', 'all', 'unknown'})
MAX_DIMENSION_VALUES = 64
MAX_VALUE_CHARACTERS = 128
MAX_VALUE_UTF8_BYTES = 256
PLATFORM_ALIASES = {
    '美团': '美团', 'meituan': '美团',
    '淘宝': '淘宝', 'taobao': '淘宝',
    '京东': '京东', 'jd': '京东',
}


def _normalized_value(item, dimension):
    if not isinstance(item, str):
        raise ValueError('适用范围维度 values 必须为字符串列表')
    item = unicodedata.normalize('NFKC', item).strip()
    if not item:
        return None
    if (len(item) > MAX_VALUE_CHARACTERS or
            len(item.encode('utf-8')) > MAX_VALUE_UTF8_BYTES):
        raise ValueError('适用范围值超过长度限制')
    if dimension == 'platforms':
        item = PLATFORM_ALIASES.get(item.casefold(), item)
    return item


def _normalized_vocabulary_values(values, dimension):
    if not isinstance(values, (list, tuple)):
        raise ValueError('适用范围词典必须为字符串列表')
    return sorted(filter(None, {
        _normalized_value(item, dimension) for item in values
    }))


def _normalized_values(values, dimension):
    if not isinstance(values, (list, tuple)):
        raise ValueError('适用范围维度 values 必须为字符串列表')
    if len(values) > MAX_DIMENSION_VALUES:
        raise ValueError('适用范围每个维度最多包含 64 个值')
    return _normalized_vocabulary_values(values, dimension)


def normalize_dimension(value, name):
    """Normalize the current mode/values dimension contract."""
    if name not in DIMENSIONS:
        raise ValueError('未知适用范围维度')
    if hasattr(value, 'model_dump'):
        value = value.model_dump()
    if value is None:
        return {'mode': 'unknown', 'values': []}
    if not isinstance(value, dict):
        raise ValueError('适用范围维度必须为 mode/values 对象')
    if set(value) - {'mode', 'values'}:
        raise ValueError('适用范围维度包含未知字段')
    mode = value.get('mode')
    if mode not in MODES:
        raise ValueError('适用范围 mode 必须为 specific、all 或 unknown')
    values = _normalized_values(value.get('values', []), name)
    if mode == 'specific' and not values:
        raise ValueError('specific 适用范围必须包含至少一个值')
    if mode != 'specific' and values:
        raise ValueError('all/unknown 适用范围不能包含值')
    return {'mode': mode, 'values': values}


def normalize_applicability(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump()
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('适用范围必须为对象')
    if set(value) - set(DIMENSIONS):
        raise ValueError('适用范围包含未知维度')
    return {
        dimension: normalize_dimension(value.get(dimension), dimension)
        for dimension in DIMENSIONS
    }


def dimension_values(scope, name):
    return list(normalize_applicability(scope)[name]['values'])


def dimension_mode(scope, name):
    return normalize_applicability(scope)[name]['mode']


def scope_identity(applicability):
    normalized = normalize_applicability(applicability)
    if all(item['mode'] == 'unknown' for item in normalized.values()):
        return ''
    identity = {'schema_version': 2, **normalized}
    payload = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _record_applicability(record):
    if not isinstance(record, dict):
        raise ValueError('待匹配记录或适用范围必须为对象')
    if ('metadata' in record or 'valid_from' in record or
            'valid_until' in record or 'kind' in record):
        return (record.get('metadata') or {}).get('applicability')
    return record


@dataclass(frozen=True)
class ScopePredicate:
    scope: dict
    valid_at: str | None = None
    include_unknown: bool = True

    @property
    def applicability(self):
        return self.scope

    def matches(self, record):
        actual = normalize_applicability(_record_applicability(record))
        for dimension in DIMENSIONS:
            requested = self.scope[dimension]
            if requested['mode'] != 'specific':
                continue
            candidate = actual[dimension]
            if candidate['mode'] == 'all':
                continue
            if (candidate['mode'] != 'specific' or not
                    set(requested['values']).intersection(candidate['values'])):
                return False
        if self.valid_at is None:
            return True
        start = record.get('valid_from') if isinstance(record, dict) else None
        end = record.get('valid_until') if isinstance(record, dict) else None
        if start is None:
            if not self.include_unknown:
                return False
        elif normalize_time(start) > self.valid_at:
            return False
        if end is not None and self.valid_at >= normalize_time(end):
            return False
        return True


def build_scope_predicate(scope=None, valid_at=None, include_unknown=True):
    return ScopePredicate(
        normalize_applicability(scope),
        normalize_time(valid_at) if valid_at is not None else None,
        include_unknown is not False,
    )


def matches_applicability(record, selected):
    return build_scope_predicate(selected).matches(record)


def _is_english_term(value):
    alphanumeric = [character for character in value if character.isalnum()]
    return bool(alphanumeric) and all(character.isascii()
                                      for character in alphanumeric)


def _is_english_token_character(character):
    return (character == '_' or character.isdigit() or
            unicodedata.category(character).startswith('M') or
            'LATIN' in unicodedata.name(character, ''))


def _surface_occurrences(text, surface):
    occurrences = [
        (match.start(), match.end())
        for match in re.finditer(re.escape(surface), text)
    ]
    if not _is_english_term(surface):
        return occurrences
    return [
        (start, end) for start, end in occurrences
        if (start == 0 or not _is_english_token_character(text[start - 1]))
        and (end == len(text) or not _is_english_token_character(text[end]))
    ]


class ApplicabilityVocabulary:
    """Project applicability terms plus stable platform aliases."""

    def __init__(self, platforms=(), businesses=(), audiences=()):
        if isinstance(platforms, dict) and not businesses and not audiences:
            source = platforms
            platforms = source.get('platforms', ())
            businesses = source.get('businesses', ())
            audiences = source.get('audiences', ())
        supplied = {
            'platforms': _normalized_vocabulary_values(
                platforms or (), 'platforms'),
            'businesses': _normalized_vocabulary_values(
                businesses or (), 'businesses'),
            'audiences': _normalized_vocabulary_values(
                audiences or (), 'audiences'),
        }
        surfaces = {dimension: {} for dimension in DIMENSIONS}
        for dimension in DIMENSIONS:
            for value in supplied[dimension]:
                surfaces[dimension][value.casefold()] = value
        for alias, canonical in PLATFORM_ALIASES.items():
            surfaces['platforms'][alias.casefold()] = canonical
        self._surfaces = surfaces

    def match(self, query):
        text = unicodedata.normalize('NFKC', query or '').casefold()
        result = {}
        for dimension in DIMENSIONS:
            candidates = []
            for surface, canonical in self._surfaces[dimension].items():
                for start, end in _surface_occurrences(text, surface):
                    candidates.append((-(end - start), start, end, canonical))
            occupied = bytearray(len(text))
            matched = set()
            for _, start, end, canonical in sorted(candidates):
                if any(occupied[start:end]):
                    continue
                occupied[start:end] = b'\x01' * (end - start)
                matched.add(canonical)
            result[dimension] = (
                {'mode': 'specific', 'values': sorted(matched)}
                if matched else {'mode': 'unknown', 'values': []})
        return result


_DEFAULT_VOCABULARY = ApplicabilityVocabulary()


def resolve_query_applicability(query, explicit=None, vocabulary=None):
    selected = normalize_applicability(explicit)
    if vocabulary is None:
        vocabulary = _DEFAULT_VOCABULARY
    elif isinstance(vocabulary, dict):
        vocabulary = ApplicabilityVocabulary(vocabulary)
    if not isinstance(vocabulary, ApplicabilityVocabulary):
        raise ValueError('适用范围词典类型无效')
    mentioned = vocabulary.match(query)
    labels = {'platforms': '平台', 'businesses': '业务', 'audiences': '对象'}
    for dimension in DIMENSIONS:
        inferred = mentioned[dimension]
        if inferred['mode'] != 'specific':
            continue
        explicit_dimension = selected[dimension]
        values = set(inferred['values'])
        if explicit_dimension['mode'] == 'specific':
            values.intersection_update(explicit_dimension['values'])
            if not values:
                raise ValueError(
                    f"问题中的{labels[dimension]}与显式适用范围冲突")
        selected[dimension] = {'mode': 'specific', 'values': sorted(values)}
    return selected


__all__ = [
    'ApplicabilityVocabulary', 'DIMENSIONS', 'PLATFORM_ALIASES',
    'ScopePredicate', 'build_scope_predicate', 'dimension_mode',
    'dimension_values', 'matches_applicability', 'normalize_applicability',
    'normalize_dimension', 'resolve_query_applicability', 'scope_identity',
]
