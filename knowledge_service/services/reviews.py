"""文档版本化的审核清单与决策业务。

路由层在 api.reviews；本文件包含请求模型、清单投影与审核决定，
批准与已批准事实原子化提交（同一 SQLite 事务）。
"""
import copy
import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator

from ..models import Request
from ..services.governance import writable
from ..services.ontology import Ontology
from ..core.time import utc_now
from ..utils.attributes import primitive_datatype


class Decision(Request):
    action: Literal['approve', 'reject', 'approve_replace']
    target_type: str = ''
    note: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)
    expected_ontology_id: str | None = None
    expected_entity_version: int | None = Field(default=None, ge=1)
    expected_attribute_versions: dict[str, int] = Field(default_factory=dict)

    @field_validator('note')
    @classmethod
    def meaningful_note(cls, value):
        if not value.strip():
            raise ValueError('请填写审核理由')
        return value.strip()


def _intervals_overlap(left, right):
    return not ((left.get('valid_until') and right.get('valid_from') and
                 left['valid_until'] <= right['valid_from']) or
                (right.get('valid_until') and left.get('valid_from') and
                 right['valid_until'] <= left['valid_from']))


def current_attribute_values(model, rows, entity_id, predicate, candidate=None):
    """Return active formal attributes for one resolved subject/predicate."""
    target = str(model.resolve(predicate, model.attributes))
    values = []
    for row in rows:
        if (row.get('kind') != 'attribute' or row.get('subject_id') != entity_id or
                row.get('metadata', {}).get('_deleted')):
            continue
        try:
            row_target = str(model.resolve(row.get('type', ''), model.attributes))
        except ValueError:
            continue
        if row_target != target or candidate is not None and not _intervals_overlap(row, candidate):
            continue
        values.append(row)
    return sorted(values, key=lambda row: row['id'])


def public_attribute_values(rows):
    return [{key: row.get(key) for key in
             ('record_id', 'value', 'datatype', 'version', 'valid_from', 'valid_until')}
            for row in ({**item, 'record_id': item['id']} for item in rows)]


def _same_attribute_value(row, value, datatype=None):
    datatype = datatype or primitive_datatype(value)
    return (row.get('datatype') == datatype and
            type(row.get('value')) is type(value) and row.get('value') == value)


def attribute_conflict(model, rows, entity, predicate, candidate):
    current = current_attribute_values(model, rows, entity['id'], predicate, candidate)
    datatype = primitive_datatype(candidate['value'])
    differing = [row for row in current
                 if not _same_attribute_value(row, candidate['value'], datatype)]
    if not differing or not model.attribute_max_count_one(predicate, entity.get('type')):
        return None, current
    return {'code': 'attribute_max_count_one',
            'current_values': public_attribute_values(differing)}, current


def list_reviews(service, p):
    result = []
    rows = service.repository.current_records(p)
    by_id = {row['id']: row for row in rows}
    try:
        model = Ontology(service.repository.get_ontology(p)['turtle'])
    except KeyError:
        model = None
    for doc in rows:
        if doc['kind'] != 'document' or doc.get('metadata', {}).get('_deleted'):
            continue
        for candidate in doc.get('metadata', {}).get('review_candidates', []):
            changed = (hashlib.sha256(doc['text'].encode('utf-8')).hexdigest() !=
                       candidate['source_hash'])
            dependencies = []
            for key in ('entity_id', 'subject_id', 'object_id'):
                if key not in candidate:
                    continue
                record_id = candidate[key]
                seen = set()
                while record_id not in seen:
                    seen.add(record_id)
                    row = by_id.get(record_id)
                    if (row and row.get('metadata', {}).get('_deleted') and
                            row['metadata'].get('merged_into')):
                        record_id = row['metadata']['merged_into']
                        continue
                    break
                row = by_id.get(record_id)
                dependencies.append(
                    row if row and not row.get('metadata', {}).get('_deleted') else None)
            entity = (dependencies[0] if candidate.get('kind') == 'attribute' and
                      dependencies else None)
            current = []
            conflict = None
            if entity and model:
                predicate = candidate.get('target_type') or candidate.get('proposed_type')
                if predicate:
                    try:
                        conflict, current = attribute_conflict(
                            model, rows, entity, predicate, candidate)
                    except ValueError:
                        pass
            attribute_values = public_attribute_values(current)
            if candidate.get('status') == 'contradicting' and conflict is None:
                conflict = {'code': candidate.get('conflict_code', 'attribute_max_count_one'),
                            'current_values': attribute_values}
            result.append({**candidate, 'kind': candidate.get('kind', 'relation'),
                'blocked': any(row is None for row in dependencies),
                'entity_version': entity['version'] if entity else None,
                'entity_type': entity.get('type') if entity else None,
                'subject_type': (dependencies[0].get('type')
                    if candidate.get('kind') == 'relation' and dependencies and dependencies[0]
                    else candidate.get('subject_type')),
                'object_type': (dependencies[1].get('type')
                    if candidate.get('kind') == 'relation' and len(dependencies) > 1 and dependencies[1]
                    else candidate.get('object_type')),
                'current_values': attribute_values,
                'current_properties': {row['type']: row['value'] for row in current},
                'conflict': conflict,
                'document_id': doc['id'],
                'document_title': doc['metadata'].get('title', doc['id']),
                'document_version': doc['version'], 'source_changed': changed,
                'evidence': candidate.get('evidence', '' if changed else
                    doc['text'][candidate['start_char']:candidate['end_char']])})
    return {'reviews': result}


def decide(service, p, doc_id, candidate_id, request):
    with service.lock:
        rows = {r['id']: r for r in service.repository.current_records(p)}
        doc = rows.get(doc_id)
        if not doc or doc['kind'] != 'document':
            raise KeyError(doc_id)
        if doc['version'] != request.expected_version:
            raise ValueError('版本冲突：请刷新审核清单')
        revised = writable(doc)
        revised['metadata'] = copy.deepcopy(doc['metadata'])
        candidate = next((c for c in revised['metadata'].get('review_candidates', [])
                          if c['id'] == candidate_id), None)
        if not candidate:
            raise KeyError(candidate_id)
        if candidate['status'] != 'pending' and not (
                candidate['status'] == 'contradicting' and
                request.action in {'reject', 'approve_replace'}):
            raise ValueError('该候选已经审核，不能重复操作')
        if request.action == 'approve_replace' and candidate['status'] != 'contradicting':
            raise ValueError('候选尚未进入单值冲突状态，请先执行普通批准检查')
        if doc.get('metadata', {}).get('_deleted') or doc['metadata'].get('status') != 'ready':
            raise ValueError('来源文档不可用')
        candidate.update(note=request.note, reviewed_at=utc_now())
        try:
            assertion = service.repository.get_assertion(p, candidate_id)
        except KeyError:
            assertion = None
        if request.action == 'reject':
            candidate['status'] = 'rejected'
            if candidate.get('kind') == 'validation':
                candidate['resolution'] = 'remediation_required'
            decisions = [] if assertion is None else [{'id': candidate_id,
                'expected_version': assertion['decision_version'], 'status': 'rejected',
                'reason': request.note, 'actor': 'reviewer'}]
            service.write(p, [], completion=(revised, doc['version']), operation='approve_review',
                          assertion_decisions=decisions)
            return {'status': 'rejected',
                    **({'resolution': 'remediation_required'} if candidate.get('kind') == 'validation' else {})}
        if hashlib.sha256(doc['text'].encode('utf-8')).hexdigest() != candidate['source_hash']:
            raise ValueError('原文已更改，请重新提取后审核')
        ontology = service.repository.get_ontology(p)
        if request.expected_ontology_id and request.expected_ontology_id != ontology['id']:
            raise ValueError('版本冲突：本体已更新，请刷新审核清单')
        kind = candidate.get('kind', 'relation')
        if request.action == 'approve_replace' and kind != 'attribute':
            raise ValueError('仅属性单值冲突支持接受新值')
        if kind == 'validation':
            candidate.update(status='approved', resolution='accepted_exception',
                             approved_ontology_id=ontology['id'])
            decisions = [] if assertion is None else [{'id': candidate_id,
                'expected_version': assertion['decision_version'], 'status': 'accepted',
                'reason': request.note, 'actor': 'reviewer',
                'canonical_record_id': candidate.get('record_id')}]
            service.write(p, [], completion=(revised, doc['version']), operation='approve_review',
                          assertion_decisions=decisions)
            return {'status': 'approved', 'resolution': 'accepted_exception',
                    'record_id': candidate.get('record_id')}
        model = Ontology(ontology['turtle'])
        target = str(model.resolve(request.target_type,
                     {'entity': model.classes, 'relation': model.relations,
                      'attribute': model.attributes}[kind]))

        def endpoint(key):
            rid = candidate[key]; seen = set()
            while rid not in seen:
                seen.add(rid); row = rows.get(rid)
                if not row or row['kind'] != 'entity':
                    break
                meta = row.get('metadata', {})
                if meta.get('_deleted') and meta.get('merged_into'):
                    rid = meta['merged_into']; continue
                if not meta.get('_deleted'):
                    return rid
                break
            raise ValueError('关联实体尚未批准、已拒绝或已删除，请先处理实体候选')

        record_id = (candidate['record_id'] if kind == 'entity' else
                     ('reviewattr_' if kind == 'attribute' else 'reviewrel_') + candidate_id)
        expected = {record_id: 0}
        common = dict(source_id=doc_id, ontology_id=ontology['id'],
            valid_from=candidate.get('valid_from', doc.get('valid_from')),
            valid_until=candidate.get('valid_until', doc.get('valid_until')),
            metadata={'review_id': candidate_id, 'review_note': request.note,
                      'confidence': candidate.get('confidence'),
                      'start_char': candidate['start_char'], 'end_char': candidate['end_char'],
                      'chunk_id': candidate['chunk_id']})
        if kind == 'entity':
            record = dict(**common, id=record_id, kind='entity', type=target, text=candidate['text'])
        elif kind == 'attribute':
            entity_id = endpoint('entity_id'); original = rows[entity_id]
            if request.expected_entity_version != original['version']:
                raise ValueError('版本冲突：请刷新并核对实体当前属性')
            from ..services.review_validation import validate_attribute
            validate_attribute(model, original['type'], target, candidate['value'])
            conflict, current_attributes = attribute_conflict(
                model, list(rows.values()), original, target, candidate)
            if conflict and request.action == 'approve':
                candidate.update(status='contradicting', target_type=target,
                                 approved_ontology_id=ontology['id'],
                                 conflict_code=conflict['code'])
                decisions = [] if assertion is None else [{'id': candidate_id,
                    'expected_version': assertion['decision_version'], 'status': 'contradicting',
                    'reason': request.note, 'actor': 'reviewer'}]
                service.write(p, [], completion=(revised, doc['version']),
                              operation='approve_review', assertion_decisions=decisions)
                return {'status': 'contradicting', 'conflict': conflict}
            if request.action == 'approve_replace' and not conflict:
                raise ValueError('当前属性值已无单值冲突，请刷新审核清单')
            evidence = candidate.get('attribute_evidence', candidate.get('evidence', ''))
            record = dict(**common, id=record_id, kind='attribute', type=target,
                subject_id=entity_id, value=candidate['value'],
                datatype=primitive_datatype(candidate['value']),
                text=(f"{original['text']} · {target} = " +
                      json.dumps(candidate['value'], ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':'))))
            record['metadata'].update(
                source_version_id=doc['version_id'], source_hash=candidate['source_hash'],
                evidence=evidence)
        else:
            record = dict(**common, id=record_id, kind='relation', type=target,
                subject_id=endpoint('subject_id'), object_id=endpoint('object_id'),
                text=doc['text'][candidate['start_char']:candidate['end_char']])
            record['metadata']['original_predicate'] = candidate['predicate']
        same_attribute = next((row for row in current_attributes
            if _same_attribute_value(row, record.get('value'), record.get('datatype'))),
            None) if kind == 'attribute' else None
        candidate.update(status='approved', target_type=target,
                         approved_ontology_id=ontology['id'],
                         record_id=same_attribute['id'] if same_attribute else record_id)
        # 校验/向量化失败会保持审核为待处理状态。批准与关系写入共享同一个 SQLite 事务。
        decisions = [] if assertion is None else [{'id': candidate_id,
            'expected_version': assertion['decision_version'], 'status': 'accepted',
            'reason': request.note, 'actor': 'reviewer', 'canonical_record_id': record_id}]
        records = [record]
        if kind == 'attribute' and request.action == 'approve_replace':
            conflicting_ids = {item['record_id'] for item in conflict['current_values']}
            actual_versions = {row['id']: row['version'] for row in current_attributes
                               if row['id'] in conflicting_ids}
            if request.expected_attribute_versions != actual_versions:
                raise ValueError('版本冲突：当前属性值已更新，请刷新审核清单')
            tombstones = []
            for current in current_attributes:
                if current['id'] not in conflicting_ids:
                    continue
                tombstone = writable(current)
                tombstone['metadata'] = {**current.get('metadata', {}), '_deleted': True,
                    'replaced_by': candidate['record_id'], 'replacement_review_id': candidate_id}
                tombstones.append(tombstone)
                for support in service.repository.list_assertions(
                        p, status='accepted', canonical_record_id=current['id']):
                    decisions.insert(0, {'id': support['id'],
                        'expected_version': support['decision_version'], 'status': 'superseded',
                        'reason': request.note, 'actor': 'reviewer',
                        'canonical_record_id': current['id']})
            records = [*tombstones, record]
            expected.update(actual_versions)
        service.write(p, records, completion=(revised, doc['version']), expected_versions=expected,
                      operation='approve_review', assertion_decisions=decisions,
                      suppress_auto_assertions=bool(assertion))
        return {'status': 'approved', 'record_id': candidate['record_id']}
