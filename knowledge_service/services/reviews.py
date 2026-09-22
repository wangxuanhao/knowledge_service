"""文档版本化的审核决策业务（模型 + decide 逻辑）。

路由层在 api.reviews；本文件只含请求模型与审核决定业务函数，
批准与已批准事实原子化提交（同一 SQLite 事务）。
"""
import copy
import hashlib
from typing import Literal

from pydantic import Field, field_validator

from ..models import Request
from ..services.governance import writable
from ..services.ontology import Ontology
from ..core.time import utc_now


class Decision(Request):
    action: Literal['approve', 'reject']
    target_type: str = ''
    note: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)
    expected_ontology_id: str | None = None
    expected_entity_version: int | None = Field(default=None, ge=1)

    @field_validator('note')
    @classmethod
    def meaningful_note(cls, value):
        if not value.strip():
            raise ValueError('请填写审核理由')
        return value.strip()


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
        if candidate['status'] != 'pending':
            raise ValueError('该候选已经审核，不能重复操作')
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

        record_id = candidate['record_id'] if kind == 'entity' else 'reviewrel_' + candidate_id
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
            record_id = endpoint('entity_id'); original = rows[record_id]
            if request.expected_entity_version != original['version']:
                raise ValueError('版本冲突：请刷新并核对实体当前属性')
            from ..services.review_validation import validate_attribute
            validate_attribute(model, original['type'], target, candidate['value'])
            props = copy.deepcopy(original.get('properties', {}))
            for key, value in props.items():
                try:
                    resolved = str(model.resolve(key, model.attributes))
                except ValueError:
                    continue
                if resolved == target and value != candidate['value']:
                    raise ValueError('实体已有不同属性值，不会覆盖；请先通过知识编辑核对处理')
            record = writable(original)
            record['properties'] = {**props, target: candidate['value']}
            record['ontology_id'] = ontology['id']
            record['metadata'] = copy.deepcopy(original['metadata'])
            record['metadata']['attribute_reviews'] = [
                *record['metadata'].get('attribute_reviews', []),
                {'review_id': candidate_id, 'source_id': doc_id,
                 'source_version_id': doc['version_id'], 'attribute': target,
                 'value': candidate['value'], 'note': request.note,
                 'reviewed_at': candidate['reviewed_at'],
                 'evidence': candidate.get('attribute_evidence', candidate.get('evidence', ''))}]
            expected = {record_id: original['version']}
        else:
            record = dict(**common, id=record_id, kind='relation', type=target,
                subject_id=endpoint('subject_id'), object_id=endpoint('object_id'),
                text=doc['text'][candidate['start_char']:candidate['end_char']])
            record['metadata']['original_predicate'] = candidate['predicate']
        candidate.update(status='approved', target_type=target,
                         approved_ontology_id=ontology['id'], record_id=record_id)
        # 校验/向量化失败会保持审核为待处理状态。批准与关系写入共享同一个 SQLite 事务。
        decisions = [] if assertion is None else [{'id': candidate_id,
            'expected_version': assertion['decision_version'], 'status': 'accepted',
            'reason': request.note, 'actor': 'reviewer', 'canonical_record_id': record_id}]
        service.write(p, [record], completion=(revised, doc['version']), expected_versions=expected,
                      operation='approve_review', assertion_decisions=decisions,
                      suppress_auto_assertions=bool(assertion))
        return {'status': 'approved', 'record_id': record_id}
