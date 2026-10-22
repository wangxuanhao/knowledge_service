"""Internal native manifest for the fields the current page actually filters.

This module deliberately has no SDK, repository or runtime dependencies. JSON
numbers must reach ``native_value`` as Decimal (or as unmodified JSON scalars);
``parse_filter_json`` preserves their source decimal value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import math
import re
from types import MappingProxyType

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
DOUBLE_BOUND = 2**53
MAX_NATIVE_FIELDS = 256
MISSING = object()
FIELD_TYPES = frozenset({'bool', 'int64', 'double', 'varchar', 'array_varchar'})
_PATH = re.compile(r'[^\W\d][\w-]*(?:\.[^\W\d][\w-]*)*')
_COLUMN = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,62}')


def exact_decimal(value):
    """Read a JSON number without coercing an original Decimal through float."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError('数值字段需要原始 JSON 数字或 Decimal')
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError('数值必须有限')
    return number


def checked_double(value):
    number = exact_decimal(value)
    if number.copy_abs() > DOUBLE_BOUND:
        raise ValueError('DOUBLE 数值超过精确支持域 ±2^53')
    native = float(number)
    if not math.isfinite(native) or Decimal(str(native)) != number:
        raise ValueError('DOUBLE 原始十进制不能无损往返')
    return native


def parse_filter_json(raw):
    """Parse public filter JSON before any binary-float information loss."""
    def reject_constant(value):
        raise ValueError('JSON 数值必须有限: ' + value)
    return json.loads(raw, parse_float=Decimal, parse_int=Decimal,
                      parse_constant=reject_constant)


@dataclass(frozen=True)
class ColumnDef:
    name: str
    field_type: str
    nullable: bool = False
    max_length: int | None = None
    max_capacity: int | None = None
    index: bool = False


# ``filter_present`` stores these public paths and actual registered metadata
# paths. The builder collects presence once; each field has only one value column.
TOP_LEVEL_FIELDS = MappingProxyType({
    'id': ('record_id', 'varchar'),
    'kind': ('kind', 'varchar'),
    'type': ('type', 'varchar'),
    'source_id': ('chunk_owner_id', 'varchar'),
    'valid_from': ('valid_from_us', 'time'),
    'valid_until': ('valid_until_us', 'time'),
})


SYSTEM_COLUMN_DEFS = (
    ColumnDef('vector_id', 'varchar', max_length=64),
    ColumnDef('index_version', 'int64', index=True),
    ColumnDef('version_id', 'varchar', max_length=800, index=True),
    ColumnDef('project_id', 'varchar', max_length=800, index=True),
    ColumnDef('record_id', 'varchar', max_length=800, index=True),
    ColumnDef('kind', 'varchar', max_length=800, index=True),
    ColumnDef('type', 'varchar', nullable=True, max_length=800, index=True),
    ColumnDef('ontology_id', 'varchar', nullable=True, max_length=800, index=True),
    ColumnDef('text', 'varchar', max_length=65535),
    ColumnDef('embedding', 'float_vector'),
    ColumnDef('sparse', 'sparse_float_vector'),
    *(column for name in ('platform', 'business', 'audience') for column in (
        ColumnDef(name + '_mode', 'varchar', max_length=16, index=True),
        ColumnDef(name + '_values', 'array_varchar', max_length=256, max_capacity=64, index=True))),
    ColumnDef('valid_from_us', 'int64', nullable=True, index=True),
    ColumnDef('valid_until_us', 'int64', nullable=True, index=True),
    ColumnDef('chunk_owner_id', 'varchar', nullable=True, max_length=800, index=True),
    ColumnDef('filter_present', 'array_varchar', max_length=1024, max_capacity=256, index=True),
)


@dataclass(frozen=True)
class FieldSpec:
    path: str
    field_type: str
    column_name: str | None = None
    max_length: int = 8192
    max_capacity: int = 256
    index: bool = True
    display_name: str | None = None

    def __post_init__(self):
        if (not isinstance(self.path, str) or len(self.path) > 256 or
                not self.path.startswith('metadata.') or not _PATH.fullmatch(self.path)):
            raise ValueError('业务字段必须是有效的 metadata 叶子路径')
        if self.field_type not in FIELD_TYPES:
            raise ValueError('不支持的在线原生字段类型')
        # Extra scalar columns require a deliberate schema change. Never infer
        # their existence or generate opaque names from arbitrary record keys.
        column = self.column_name
        if (not isinstance(column, str) or not _COLUMN.fullmatch(column)
                or column.startswith('bf_')
                or column in {item.name for item in SYSTEM_COLUMN_DEFS}):
            raise ValueError('业务字段需要显式、可读且不冲突的列名')
        if type(self.max_length) is not int or not 1 <= self.max_length <= 65535:
            raise ValueError('VARCHAR UTF-8 字节容量必须为 1 到 65535')
        if type(self.max_capacity) is not int or not 1 <= self.max_capacity <= 4096:
            raise ValueError('ARRAY 容量必须为 1 到 4096')
        if type(self.index) is not bool:
            raise ValueError('index 必须为布尔值')
        if self.display_name is not None and (not isinstance(self.display_name, str)
                or not self.display_name.strip() or len(self.display_name) > 100):
            raise ValueError('字段显示名必须为 1 到 100 个字符的非空字符串')
        object.__setattr__(self, 'column_name', column)

    @property
    def label(self):
        return self.path if self.display_name is None else self.display_name

    def native_value(self, value):
        if value is None:
            return None
        if self.field_type == 'bool' and type(value) is bool:
            return value
        if self.field_type == 'int64':
            number = exact_decimal(value)
            if number != number.to_integral_value() or not INT64_MIN <= number <= INT64_MAX:
                raise ValueError('INT64 需要范围内的精确整数')
            return int(number)
        if self.field_type == 'double':
            return checked_double(value)
        if self.field_type == 'varchar' and isinstance(value, str):
            if len(value.encode('utf-8')) > self.max_length:
                raise ValueError('VARCHAR 超过 UTF-8 字节容量: ' + self.path)
            return value
        if self.field_type == 'array_varchar' and isinstance(value, list):
            if len(value) > self.max_capacity:
                raise ValueError('ARRAY 超过容量: ' + self.path)
            if any(not isinstance(item, str) for item in value):
                raise ValueError('ARRAY 必须是同质字符串列表，不能包含对象或嵌套数组')
            if any(len(item.encode('utf-8')) > self.max_length for item in value):
                raise ValueError('ARRAY 元素超过 UTF-8 字节容量: ' + self.path)
            return list(value)
        raise ValueError('字段值与目录类型不符: ' + self.path)

    def project(self, record, *, prefix=''):
        if prefix != '':
            raise ValueError('页面筛选不展开端点列')
        actual = record
        for part in self.path.split('.'):
            actual = actual.get(part, MISSING) if isinstance(actual, dict) else MISSING
        return {self.column_name: None if actual is MISSING else self.native_value(actual)}


@dataclass(frozen=True)
class FilterCatalog:
    revision: int
    fields: tuple[FieldSpec, ...]

    def __post_init__(self):
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError('目录 revision 必须是正整数')
        fields = tuple(self.fields)
        if any(not isinstance(field, FieldSpec) for field in fields):
            raise ValueError('目录字段必须是 FieldSpec')
        if len({field.path for field in fields}) != len(fields):
            raise ValueError('目录字段路径重复')
        columns = [field.column_name for field in fields]
        if len(set(columns)) != len(columns):
            raise ValueError('目录列名冲突')
        object.__setattr__(self, 'fields', tuple(sorted(fields, key=lambda field: field.path)))

    def resolve(self, path):
        if not isinstance(path, str):
            raise ValueError('无效的字段路径')
        normalized = path if path.startswith('metadata.') else 'metadata.' + path
        for field in self.fields:
            if field.path == normalized:
                return field
        raise ValueError('字段未登记到在线筛选目录: ' + path)

    def to_dict(self):
        return {'revision': self.revision, 'fields': [asdict(field) for field in self.fields]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {'revision', 'fields'}:
            raise ValueError('无效的筛选字段目录')
        return cls(value['revision'], tuple(FieldSpec(**field) for field in value['fields']))

    @property
    def fingerprint(self):
        canonical = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False,
                               separators=(',', ':'), allow_nan=False)
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def column_defs(self, *, max_fields=MAX_NATIVE_FIELDS):
        columns = list(SYSTEM_COLUMN_DEFS)
        for field in self.fields:
            columns.append(ColumnDef(field.column_name, field.field_type, True,
                                     field.max_length if field.field_type in {'varchar', 'array_varchar'} else None,
                                     field.max_capacity if field.field_type == 'array_varchar' else None,
                                     field.index))
        if len(columns) > max_fields:
            raise ValueError(f'平铺字段展开后 {len(columns)} 列，超过 {max_fields} 列预算')
        return tuple(columns)


def default_catalog():
    return FilterCatalog(1, ())


def discover_page_catalog(records):
    """Return the fixed online manifest without inspecting project metadata.

    PG facets keep their independent metadata discovery. Future Milvus columns
    must be explicitly declared and deployed, never inferred while indexing.
    """
    return default_catalog()
