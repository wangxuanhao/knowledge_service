"""Pure, complete native Milvus rows from one authority dependency snapshot."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from decimal import Decimal

from .applicability import normalize_applicability
from .search_filter_catalog import INT64_MAX, INT64_MIN, TOP_LEVEL_FIELDS
from ..core.time import normalize_time


class ProjectionFieldMismatch(ValueError):
    """Current page fields exceeded the immutable project build manifest."""
    requires_rebuild = True


@dataclass(frozen=True)
class ProjectionInput:
    """Canonical raw PG row and its single ontology existence predicate."""
    record: dict
    ontology_known: bool = False
    owner_label: str = ''
    property_label: str = ''
    source_contexts: tuple = ()


def _microseconds(value):
    normalized = normalize_time(value)
    if normalized is None:
        return None
    delta = (datetime.fromisoformat(normalized.replace('Z', '+00:00')) -
             datetime(1970, 1, 1, tzinfo=timezone.utc))
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds)


def _metadata(record):
    value = record.get('metadata')
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('投影 metadata 必须是对象')
    return value


def _visible(record):
    return record is not None and not any(_metadata(record).get(key)
                                         for key in ('_deleted', '_audit', '_quarantined'))


def _validate_columns(row, columns):
    for column in columns:
        if column.name in {'embedding', 'sparse'}:
            continue
        value = row[column.name]
        if value is None:
            if not column.nullable:
                raise ValueError('投影必填列缺失: ' + column.name)
            continue
        if column.field_type == 'bool':
            valid = type(value) is bool
        elif column.field_type == 'int64':
            valid = type(value) is int and INT64_MIN <= value <= INT64_MAX
        elif column.field_type == 'double':
            # Business values have already passed FieldSpec.native_value.
            valid = type(value) is float
        elif column.field_type == 'varchar':
            valid = isinstance(value, str)
            if valid and len(value.encode('utf-8')) > column.max_length:
                raise ValueError('投影列超过 UTF-8 字节容量: ' + column.name)
        elif column.field_type == 'array_varchar':
            valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
            if valid and len(value) > column.max_capacity:
                raise ValueError('投影数组超过容量: ' + column.name)
            if valid and any(len(item.encode('utf-8')) > column.max_length for item in value):
                raise ValueError('投影数组元素超过 UTF-8 字节容量: ' + column.name)
        else:
            valid = False
        if not valid:
            raise ValueError('投影列类型不符: ' + column.name)


def projection_fingerprint(row):
    """PG receipt identity; fingerprints are never stored in Milvus rows."""
    canonical = json.dumps({key: value for key, value in row.items()
                            if key not in {'embedding', 'sparse'}},
                           ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _body_projection(value, catalog, *, index_version):
    """Return the current visible body row, without externally generated vectors."""
    if not isinstance(value, ProjectionInput) or not isinstance(value.record, dict):
        raise ValueError('投影输入必须包含规范记录')
    if type(index_version) is not int or not 1 <= index_version <= INT64_MAX:
        raise ValueError('index_version 必须为正 INT64')
    record = value.record
    if (record.get('kind') not in {'entity', 'relation', 'attribute', 'chunk'}
            or record.get('superseded_at') is not None or not _visible(record)):
        return None
    for key in ('project_id', 'id', 'version_id', 'kind'):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError('投影记录缺少身份字段: ' + key)
    if type(value.ontology_known) is not bool:
        raise ValueError('ontology_known 必须为布尔值')
    identity = json.dumps([record['project_id'], index_version, record['version_id']],
                          ensure_ascii=False, separators=(',', ':'))
    row = dict(vector_id=hashlib.sha256(identity.encode('utf-8')).hexdigest(),
               index_version=index_version, project_id=record['project_id'],
               version_id=record['version_id'], ontology_id=(record.get('ontology_id')
                   if value.ontology_known else None), text='' if record.get('text') is None else record['text'],
               owner_id=record.get('subject_id') if record['kind']=='attribute' else None,
               support_context_id=None, support_context_version=None,
               window_index=0,window_start=0,window_end=len(record.get('text') or ''))
    present = []
    for path, (column, field_type) in TOP_LEVEL_FIELDS.items():
        has_value = path in record and (path != 'source_id' or record['kind'] == 'chunk')
        actual = record.get(path) if has_value else None
        row[column] = _microseconds(actual) if field_type == 'time' else actual
        if has_value:
            present.append(path)
    applicability = normalize_applicability(_metadata(record).get('applicability'))
    for dimension, prefix in (('platforms', 'platform'), ('businesses', 'business'),
                              ('audiences', 'audience')):
        row[prefix + '_mode'] = applicability[dimension]['mode']
        row[prefix + '_values'] = applicability[dimension]['values']
    for field in catalog.fields:
        try:
            row.update(field.project(record))
        except ValueError as exc:
            raise ProjectionFieldMismatch('业务筛选字段与冻结类型不符: ' + field.path + ': ' + str(exc)) from exc
        current = record
        for part in field.path.split('.'):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            present.append(field.path)
    row['filter_present'] = sorted(present)
    _validate_columns(row, catalog.column_defs())
    return row


def text_windows(text, *, size=6000, overlap=400):
    """Yield complete deterministic character windows without truncating the tail."""
    if not isinstance(text,str) or type(size) is not int or type(overlap) is not int or not 0<=overlap<size:
        raise ValueError('检索窗口需要文本及 0 <= overlap < size')
    start=0
    while True:
        end=min(len(text),start+size)
        yield start,end,text[start:end]
        if end==len(text):return
        start=end-overlap


def build_projections(value, catalog, *, index_version):
    """Stream fact windows and correlated source contexts, retaining authority IDs."""
    if not isinstance(value,ProjectionInput) or not isinstance(value.record,dict):
        raise ValueError('投影输入必须包含规范记录')
    record=value.record
    if record.get('kind') not in {'entity','relation','attribute','chunk'} or not _visible(record) or record.get('superseded_at') is not None:
        return
    from .entity_identity import source_qualified
    reference=source_qualified(record)
    if reference and not value.source_contexts:return
    if record.get('kind')=='attribute':
        actual=record.get('value')
        if actual is None or type(actual) not in (str,bool,int,float,Decimal):
            raise ValueError('属性投影需要原生标量值')
        from ..utils.filters import encode_filter_json
        body=actual if isinstance(actual,str) else encode_filter_json(actual)
        prefix='\n'.join(filter(None,[value.owner_label[:2000],value.property_label[:2000],str(record.get('type') or '')[:2000],str(record.get('datatype') or '')]))+'\n'
        windows=tuple(text_windows(body))
    else:
        body='' if record.get('text') is None else record['text']
        if not isinstance(body,str):raise ValueError('投影正文必须是字符串')
        prefix='';windows=((0,len(body),body),)
    count=max(1,len(value.source_contexts))
    if count*len(windows)>20000 or count*sum(len((prefix+text).encode('utf-8'))+1024 for _,_,text in windows)>16*1024*1024:
        raise ValueError('单条事实的来源与文本窗口超过投影预算，请减少单次来源支持或拆分属性正文')
    for context in value.source_contexts or (None,):
        source_record=record
        if context is not None:
            if not isinstance(context.get('id'),str) or not context['id'] or type(context.get('version')) is not int or context['version']<1:
                raise ValueError('来源上下文缺少稳定身份或版本')
            if reference:
                source_record={**record,'valid_from':context.get('valid_from'),'valid_until':context.get('valid_until'),
                    'metadata':{**_metadata(record),'applicability':(context.get('metadata') or {}).get('applicability')}}
        for ordinal,(start,end,text) in enumerate(windows):
            row=_body_projection(replace(value,record={**source_record,'text':prefix+text}),catalog,index_version=index_version)
            if row is None:continue
            row.update(window_index=ordinal,window_start=start,window_end=end)
            if context is not None:
                row.update(support_context_id=context['id'],support_context_version=context['version'])
            if context is not None or record['kind']=='attribute':
                identity=json.dumps([record['project_id'],index_version,record['version_id'],
                    row['support_context_id'],row['support_context_version'],ordinal],ensure_ascii=False,separators=(',',':'))
                row['vector_id']=hashlib.sha256(identity.encode('utf-8')).hexdigest()
            yield row


def build_projection(value, catalog, *, index_version):
    """Compatibility single-body view; publishers must use build_projections."""
    return next(build_projections(value,catalog,index_version=index_version),None)


__all__ = ['ProjectionInput','build_projection','build_projections','text_windows','projection_fingerprint']
