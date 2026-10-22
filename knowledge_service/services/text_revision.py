"""Conservative qualification and planning for an existing entity text edit."""
from __future__ import annotations

import logging

from rdflib.namespace import RDFS, SH

from ..core.time import normalize_time
from ..domain.formal_facts import SOURCE_METADATA, manual_assertion_id
from ..models import RecordWrite
from .formal_writes import FormalFactWriter
from .ontology import Ontology


_SYSTEM = {'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}
LOG = logging.getLogger('knowledge_service.text_revision')


def _fallback(reason):
    LOG.info('实体文字修订使用完整校验：%s', reason)
    return None


def _profile(ontology):
    # Reject even untargeted/custom SHACL constructs. Labels are RDF data and
    # arbitrary shapes can observe any other node in the project graph.
    if any(str(term).startswith(str(SH))
           for triple in ontology.graph for term in triple):
        return False
    # Without attributes (certified by SQL), the remaining non-SHACL checks are
    # endpoint presence/time and relation domain/range. This profile admits only
    # constraints satisfied by every known entity class, including subclasses.
    for predicate in ontology.relations:
        for constraint in (RDFS.domain, RDFS.range):
            expected = ontology.constraint_types(predicate, constraint)
            if expected and any(
                    not any(allowed in ontology.parents(term) for allowed in expected)
                    for term in ontology.classes):
                return False
    return True


def _normalized(record):
    row = RecordWrite.model_validate(record).model_dump(exclude_none=True)
    for key in ('valid_from', 'valid_until'):
        row[key] = normalize_time(row.get(key))
    return row


def try_entity_text_revision(service, project_id, records, *, expected_version,
                             revision, completion, expected_versions, operation,
                             assertions, pending_assertions, assertion_decisions,
                             resolution_reviews, ledger, resolution_decisions,
                             suppress_auto_assertions, recorded_at,
                             operation_context, relation_constraint_mode,
                             shacl_mode, shacl_review_out, defer_milvus_sync):
    if (not revision or len(records) != 1 or operation != 'manual_write'
            or type(expected_version) is not int or expected_version < 0
            or expected_versions is not None or completion is not None
            or any((assertions, pending_assertions, assertion_decisions,
                    resolution_reviews, ledger, resolution_decisions,
                    suppress_auto_assertions))
            or recorded_at is not None or operation_context is not None
            or relation_constraint_mode != 'strict' or shacl_mode != 'strict'
            or shacl_review_out is not None):
        return _fallback('调用包含额外治理或非默认写入选项') if revision else None
    repository = service.repository
    scope = getattr(repository, 'entity_text_revision', None)
    if not callable(scope):
        return None
    candidate = _normalized(records[0])
    if candidate['kind'] != 'entity' or not candidate.get('id'):
        return None
    with scope(project_id, candidate['id'], expected_version) as session:
        if session is None or session.record is None:
            return _fallback('外层事务或记录不存在')
        old = session.record
        writable = {key: value for key, value in old.items() if key not in _SYSTEM}
        if (old['kind'] != 'entity'
                or set(writable) - RecordWrite.model_fields.keys()
                or old.get('source_id') or candidate.get('source_id')
                or old.get('metadata', {}).get('_deleted')
                or SOURCE_METADATA.intersection(old.get('metadata', {}))
                or not old.get('ontology_id')):
            return _fallback('记录包含来源锚点、额外字段或待处理状态')
        normalized_old = _normalized(writable)
        if {key: value for key, value in candidate.items() if key != 'text'} != {
                key: value for key, value in normalized_old.items() if key != 'text'}:
            return _fallback('文字以外的字段发生变化')
        bound_row = repository.get_ontology(project_id, old['ontology_id'])
        latest_row = repository.get_ontology(project_id)
        bound = Ontology(bound_row['turtle'])
        latest = bound if bound_row['id'] == latest_row['id'] else Ontology(latest_row['turtle'])
        if not _profile(bound) or not _profile(latest):
            return _fallback('本体约束需要完整图校验')
        try:
            bound.resolve(candidate.get('type', ''), bound.classes)
        except ValueError:
            return _fallback('实体类型尚未在绑定本体中解析')
        if session.requires_full_validation(bound_row['id']):
            return _fallback('项目端点、时态或跨本体依赖需要完整校验')
        assertion_id = manual_assertion_id(
            repository.storage_namespace, operation, candidate, expected_version)
        snapshot = session.snapshot(candidate, assertion_id, latest_row)
        plan = FormalFactWriter(repository).prepare(
            operation, project_id, [], {candidate['id']: expected_version},
            {'records': [candidate]}, snapshot=snapshot)
        result = session.execute(plan)
        LOG.info('实体文字修订已通过有界权威读集提交')
    # The session is deliberately outermost: these callbacks see committed data.
    accepted = result['accepted_records']
    service.annotate_structure_pending(project_id, accepted)
    if not defer_milvus_sync:
        service._drain_projections(project_id)
    return accepted
