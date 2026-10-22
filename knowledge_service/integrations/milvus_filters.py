"""Compile validated public predicates to controlled native-column filters.

Only identifiers from the field directory and fixed manifest enter expressions.
All user values use Milvus SDK template parameters. ``canonical_filter`` contains
ordinary JSON values and preserves the original decimal meaning for PG rechecks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import re
from typing import NamedTuple

from ..core.time import normalize_time
from ..domain.applicability import normalize_applicability
from ..domain.search_filter_catalog import (
    DOUBLE_BOUND, INT64_MAX, INT64_MIN, TOP_LEVEL_FIELDS, FilterCatalog,
    checked_double, exact_decimal,
)

_OPS = frozenset({'eq', 'ne', 'in', 'gt', 'gte', 'lt', 'lte', 'exists', 'contains'})
_PATH = re.compile(r'[^\W\d][\w-]*(?:\.[^\W\d][\w-]*)*')
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CompiledFilter(NamedTuple):
    expr: str
    filter_params: dict
    canonical_filter: dict | None


def datetime_to_us(value):
    normalized = normalize_time(value, allow_none=False)
    moment = datetime.fromisoformat(normalized.replace('Z', '+00:00'))
    delta = moment - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _number(value):
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _reject_structured(value):
    if isinstance(value, dict):
        raise ValueError('在线过滤不支持结构对象目标')
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                raise ValueError('在线过滤不支持对象或嵌套数组目标')
            _reject_structured(item)
    elif value is not None and not isinstance(value, (str, bool, int, float, Decimal)):
        raise ValueError('过滤器目标必须是 JSON 值')
    if _number(value):
        exact_decimal(value)


class _Compiler:
    def __init__(self, catalog):
        if not isinstance(catalog, FilterCatalog):
            raise ValueError('过滤器需要固定字段目录')
        self.catalog = catalog
        self.params = {}
        self._presence = {}

    def param(self, value):
        name = 'p' + str(len(self.params))
        self.params[name] = value
        return '{' + name + '}'

    def binding(self, path, prefix):
        if prefix != '':
            raise ValueError('页面筛选不展开端点列')
        if not isinstance(path, str) or len(path) > 256 or not _PATH.fullmatch(path):
            raise ValueError('无效的过滤字段路径')
        if path == 'recorded_at':
            raise ValueError('在线检索不支持 recorded_at；历史关键词检索使用 PostgreSQL')
        if path in TOP_LEVEL_FIELDS:
            column, field_type = TOP_LEVEL_FIELDS[path]
            field = None
        else:
            field = self.catalog.resolve(path)
            path, column, field_type = field.path, field.column_name, field.field_type
        if path not in self._presence:
            self._presence[path] = 'array_contains(filter_present, ' + self.param(path) + ')'
        return path, column, self._presence[path], field_type, field

    @staticmethod
    def atom(path, op, value):
        return {'field': path, 'op': op, 'value': value}

    def constant(self, path, presence, truth):
        group = 'or' if truth else 'and'
        return (f'({presence} {group} not ({presence}))',
                ({group: [self.atom(path, 'exists', True), self.atom(path, 'exists', False)]}
                 if truth else self.atom(path, 'in', [])))

    def nonnull(self, path, column, presence):
        return f'({presence} and {column} is not null)', self.atom(path, 'ne', None)

    def integer(self, path, column, presence, op, target):
        number = exact_decimal(target)
        if op == 'eq':
            if number < INT64_MIN or number > INT64_MAX or number != number.to_integral_value():
                return self.constant(path, presence, False)
            integer = int(number)
            # Milvus's parser rejects the magnitude of INT64_MIN as a positive
            # integer before applying unary minus; avoid that literal entirely.
            expr = (f'{column} < {self.param(INT64_MIN + 1)}' if integer == INT64_MIN
                    else f'{column} == {self.param(integer)}')
            return expr, self.atom(path, 'eq', integer)
        lower = op in {'gt', 'gte'}
        if (lower and number >= INT64_MAX and op == 'gt') or (lower and number > INT64_MAX):
            return self.constant(path, presence, False)
        if (not lower and number <= INT64_MIN and op == 'lt') or (not lower and number < INT64_MIN):
            return self.constant(path, presence, False)
        if (lower and number <= INT64_MIN and op == 'gte') or (lower and number < INT64_MIN):
            return self.nonnull(path, column, presence)
        if (not lower and number >= INT64_MAX and op == 'lte') or (not lower and number > INT64_MAX):
            return self.nonnull(path, column, presence)
        floor = int(number.to_integral_value(rounding=ROUND_FLOOR))
        ceil = int(number.to_integral_value(rounding=ROUND_CEILING))
        boundary = {'gt': floor + 1, 'gte': ceil, 'lt': ceil - 1, 'lte': floor}[op]
        normalized_op = 'gte' if lower else 'lte'
        if lower and boundary <= INT64_MIN or not lower and boundary >= INT64_MAX:
            return self.nonnull(path, column, presence)
        if lower and boundary > INT64_MAX or not lower and boundary < INT64_MIN:
            return self.constant(path, presence, False)
        expr = (f'{column} < {self.param(INT64_MIN + 1)}' if boundary == INT64_MIN
                else f'{column} {">=" if lower else "<="} {self.param(boundary)}')
        return expr, self.atom(path, normalized_op, boundary)

    def double(self, path, column, presence, op, target):
        number = exact_decimal(target)
        if number.copy_abs() > DOUBLE_BOUND:
            qualifies = ((number > DOUBLE_BOUND and op in {'lt', 'lte'}) or
                         (number < -DOUBLE_BOUND and op in {'gt', 'gte'}))
            return self.nonnull(path, column, presence) if qualifies else self.constant(path, presence, False)
        native = checked_double(number)
        comparator = {'eq': '==', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
        return f'{column} {comparator} {self.param(native)}', self.atom(path, op, native)

    def equality(self, binding, target):
        path, column, presence, field_type, field = binding
        if target is None:
            return column + ' is null', self.atom(path, 'eq', None)
        if field_type in {'int64', 'double'} and _number(target):
            operation = self.integer if field_type == 'int64' else self.double
            return operation(path, column, presence, 'eq', target)
        if field_type == 'time':
            if isinstance(target, str):
                value = normalize_time(target, allow_none=False)
                return f'{column} == {self.param(datetime_to_us(value))}', self.atom(path, 'eq', value)
            return self.constant(path, presence, False)
        if field_type == 'varchar' and isinstance(target, str) or field_type == 'bool' and type(target) is bool:
            return f'{column} == {self.param(target)}', self.atom(path, 'eq', target)
        if field_type == 'array_varchar' and isinstance(target, list):
            value = field.native_value(target)
            parts = [f'array_length({column}) == {self.param(len(value))}']
            parts.extend(f'{column}[{index}] == {self.param(item)}' for index, item in enumerate(value))
            return '(' + ' and '.join(parts) + ')', self.atom(path, 'eq', value)
        return self.constant(path, presence, False)

    def compile(self, ast, prefix='', depth=0):
        if ast is None and depth == 0:
            return '', None
        if depth > 16 or not isinstance(ast, dict):
            raise ValueError('无效的过滤器或嵌套超过 16 层')
        groups = set(ast) & {'and', 'or'}
        if groups:
            if len(ast) != 1:
                raise ValueError('逻辑过滤器必须恰好有一个键')
            group = next(iter(groups))
            children = ast[group]
            if not isinstance(children, list) or not 1 <= len(children) <= 100:
                raise ValueError('逻辑过滤器需要 1 到 100 个子过滤器')
            results = [self.compile(child, prefix, depth + 1) for child in children]
            return '(' + f' {group} '.join(expr for expr, _ in results) + ')', {group: [canonical for _, canonical in results]}
        if set(ast) != {'field', 'op', 'value'}:
            raise ValueError('过滤器需要 field、op 和 value')
        op, target = ast['op'], ast['value']
        if not isinstance(op, str) or op not in _OPS:
            raise ValueError('不支持的过滤器运算符')
        binding = self.binding(ast['field'], prefix)
        path, column, presence, field_type, field = binding
        if op == 'exists':
            if type(target) is not bool:
                raise ValueError('exists 运算符需要布尔值')
            return (presence if target else f'not ({presence})'), self.atom(path, op, target)
        if op == 'in':
            if not isinstance(target, list):
                raise ValueError('in 运算符需要列表')
            if not target:
                return self.constant(path, presence, False)
            results = [self.compile(self.atom(path, 'eq', item), prefix, depth) for item in target]
            # Retain the public IN leaf rather than expanding its canonical AST
            # into >100 OR children or increasing the user's nesting depth.
            values = [canonical['value'] for _, canonical in results if canonical.get('op') == 'eq']
            return '(' + ' or '.join(expr for expr, _ in results) + ')', self.atom(path, 'in', values)
        _reject_structured(target)
        if isinstance(target, str) and field_type in {'varchar', 'array_varchar'}:
            max_length = field.max_length if field is not None else 800
            if len(target.encode('utf-8')) > max_length:
                raise ValueError('VARCHAR 查询目标超过目录 UTF-8 字节容量: ' + path)
        if field_type == 'time':
            normalize_time(target)
        if op in {'eq', 'ne'}:
            expression, canonical = self.equality(binding, target)
            if op == 'eq':
                return f'({presence} and ({expression}))', canonical
            if target is None:
                return self.nonnull(path, column, presence)
            if canonical.get('op') != 'eq':
                # Comparing any present value (including NULL) with a target
                # outside the registered type/domain is unequal.
                return f'{presence}', self.atom(path, 'exists', True)
            return (f'({presence} and ({column} is null or not ({expression})))',
                    self.atom(path, 'ne', canonical['value']))
        if op == 'contains':
            if isinstance(target, list):
                raise ValueError('在线 contains 不支持数组或对象目标')
            if field_type == 'time' and isinstance(target, str):
                expression, canonical = self.equality(binding, target)
                return f'({presence} and ({expression}))', canonical
            if field_type == 'array_varchar' and isinstance(target, str):
                expression = f'array_contains({column}, {self.param(target)})'
            elif field_type == 'varchar' and isinstance(target, str):
                pattern = '%' + target.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                expression = f'{column} like {self.param(pattern)}'
            else:
                return self.constant(path, presence, False)
            return f'({presence} and {expression})', self.atom(path, op, target)
        if isinstance(target, bool) or not isinstance(target, (str, int, float, Decimal)):
            raise ValueError('比较运算需要数字或字符串')
        if field_type in {'int64', 'double'} and _number(target):
            operation = self.integer if field_type == 'int64' else self.double
            expression, canonical = operation(path, column, presence, op, target)
        elif field_type == 'time' and isinstance(target, str):
            value = normalize_time(target, allow_none=False)
            comparator = {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
            expression = f'{column} {comparator} {self.param(datetime_to_us(value))}'
            canonical = self.atom(path, op, value)
        elif field_type == 'varchar' and isinstance(target, str):
            comparator = {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
            expression, canonical = f'{column} {comparator} {self.param(target)}', self.atom(path, op, target)
        else:
            return self.constant(path, presence, False)
        return f'({presence} and ({expression}))', canonical


def compile_filter(filters, catalog, *, prefix=''):
    compiler = _Compiler(catalog)
    expression, canonical = compiler.compile(filters, prefix)
    return CompiledFilter(expression, compiler.params, canonical)


def compile_scope(project_id, scope, catalog, *, kinds=None, platform_match=None):
    """Compile only the current page's predicate on the returned row.

    PG performs bounded authority/endpoint checks after native ranking. The
    manifest contains no endpoint, audit, state or historical-time columns.
    """
    if not isinstance(project_id, str) or not project_id or len(project_id.encode('utf-8')) > 800:
        raise ValueError('无效的项目 ID')
    if scope is None:
        scope = {}
    if hasattr(scope, 'model_dump'):
        scope = scope.model_dump()
    if not isinstance(scope, dict):
        raise ValueError('检索范围必须是对象')
    allowed = {'applicability', 'filters', 'valid_at', 'known_at', 'include_unknown',
               'kinds', 'ontology_scope', 'ontology_ids', '_platform_match'}
    if set(scope) - allowed:
        raise ValueError('不支持的在线检索范围字段: ' + ', '.join(sorted(set(scope) - allowed)))
    compiler = _Compiler(catalog)
    selection = normalize_applicability(scope.get('applicability'))
    selected_kinds = scope.get('kinds') if kinds is None else kinds
    if selected_kinds is not None and (not isinstance(selected_kinds, (list, tuple)) or any(
            kind not in {'entity', 'relation', 'chunk', 'document', 'attribute'} for kind in selected_kinds)):
        raise ValueError('无效的记录类型')
    if 'include_unknown' in scope and type(scope['include_unknown']) is not bool:
        raise ValueError('include_unknown 必须为布尔值')
    ontology = scope.get('ontology_scope', 'all')
    ontology_ids = scope.get('ontology_ids')
    if ontology not in {'all', 'ids', 'unknown'}:
        raise ValueError('无效的本体范围')
    if ontology == 'ids':
        if not isinstance(ontology_ids, list) or not ontology_ids or any(not isinstance(item, str) or not item for item in ontology_ids):
            raise ValueError('ids 本体范围需要非空 ontology_ids')
    elif ontology_ids is not None:
        raise ValueError('只有 ids 本体范围可以提供 ontology_ids')
    match = platform_match or scope.get('_platform_match')
    modes = {'all_only': 'all', 'unknown_only': 'unknown', 'specific_only': 'specific'}
    if match is not None and match not in modes:
        raise ValueError('无效的平台组范围')
    valid = datetime_to_us(scope['valid_at']) if scope.get('valid_at') is not None else None
    if scope.get('known_at') is not None:
        raise ValueError('在线检索不支持历史 known_at；历史关键词检索使用 PostgreSQL')
    parts = ['project_id == ' + compiler.param(project_id)]
    if selected_kinds is not None:
        parts.append('kind in ' + compiler.param(list(selected_kinds)))
    if ontology == 'ids':
        parts.append('ontology_id in ' + compiler.param(list(ontology_ids)))
    elif ontology == 'unknown':
        parts.append('ontology_id is null')
    for dimension, name in (('platforms', 'platform'), ('businesses', 'business'), ('audiences', 'audience')):
        requested = selection[dimension]
        if requested['mode'] == 'specific':
            values = compiler.param(requested['values'])
            parts.append(f'({name}_mode == {compiler.param("all")} or '
                         f'({name}_mode == {compiler.param("specific")} and '
                         f'array_contains_any({name}_values, {values})))')
        if dimension == 'platforms' and match is not None:
            parts.append(name + '_mode == ' + compiler.param(modes[match]))
    if valid is not None:
        if scope.get('include_unknown', True):
            parts.append(f'(valid_from_us is null or valid_from_us <= {compiler.param(valid)})')
        else:
            parts.append(f'(valid_from_us is not null and valid_from_us <= {compiler.param(valid)})')
        parts.append(f'(valid_until_us is null or valid_until_us > {compiler.param(valid)})')
    expression, canonical = compiler.compile(scope.get('filters'))
    if expression:
        parts.append(expression)
    return CompiledFilter(' and '.join(parts), compiler.params, canonical)
