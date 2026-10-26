"""Explicit, selected knowledge intake into published terms or a working draft.

Previews are pure reads. A confirmed selection and its receipt share the draft
CAS transaction; immutable discovery runs provide the request identity ledger.
"""
from copy import deepcopy
import hashlib

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from .ontology import Ontology
from .ontology_discovery import (
    _candidates, _iri, _materialize_candidates, _materialized_candidate_ids,
    _reuse_materialized_records,
)
from .ontology_drafts_core import (
    EDITABLE_STATES, RevisionConflict, StaleBase, StaleSource, ValidationChanged,
    ValidationFailed, _fingerprint, _status_guard,
)
from .ontology_operations import apply_operations, canonical_turtle_diff
from ..repository.discovery_run_store import DiscoveryRunConflict
from ..repository.page_reads import PageReads
from ..utils.attributes import primitive_datatype


def materialized_records(repository, project_id):
    """Attach current candidate identities from canonical assertion decisions."""
    records = [x for x in repository.current_records(project_id,
               kinds=['entity', 'relation', 'attribute']) if not x.get('metadata', {}).get('_deleted')]
    by_record = {}
    for assertion in repository.list_assertions(project_id):
        if assertion['status'] == 'accepted' and assertion.get('canonical_record_id'):
            by_record.setdefault(assertion['canonical_record_id'], []).append(assertion['id'])
    return [{**record, 'metadata': {**{k: v for k, v in record.get('metadata', {}).items()
                if k not in {'discovery_candidate_id', 'discovery_candidate_ids'}},
            'discovery_candidate_ids': by_record.get(record['id'], [])}} for record in records]


def prepare_intake_facts(repository, project_id, records, candidates):
    """Prepare canonical facts and original source assertion acceptances once."""
    from .formal_writes import FormalFactWriter
    snapshot = repository.formal_write_snapshot(project_id)
    existing = {x['id']: x for x in snapshot['records']}
    assertions = {x['id']: x for x in snapshot['assertions']}
    candidates = {x['id']: x for x in candidates}
    unique, targets = {}, {}
    explicit = []
    fields = ('id', 'kind', 'document_id', 'document_version_id', 'chunk_id',
              'source_hash', 'start_char', 'end_char', 'quote', 'payload', 'actor')
    for record in records:
        if record['id'] in existing and existing[record['id']].get('metadata', {}).get('_deleted'):
            raise StaleSource('关联正式事实已删除，请先通过知识治理恢复或重新解析来源')
        unique[record['id']] = record
        for candidate_id in _materialized_candidate_ids([record]) & candidates.keys():
            candidate = candidates.get(candidate_id)
            assertion_id = candidate.get('assertion_id') if candidate else None
            assertion = assertions.get(assertion_id)
            if assertion is None:
                raise StaleSource('候选缺少当前来源断言，请重新解析来源资料')
            if assertion_id in targets:
                if targets[assertion_id] != record['id']:
                    raise StaleSource('同一来源断言关联了不同事实')
                continue
            targets[assertion_id] = record['id']
            if assertion['status'] == 'accepted':
                if assertion['canonical_record_id'] != record['id']:
                    raise StaleSource('来源断言已关联不同的正式事实')
                continue
            if assertion['status'] != 'pending':
                raise StaleSource('来源断言已撤回或拒绝')
            explicit.append({**{key: assertion.get(key) for key in fields},
                             'canonical_record_id': record['id']})
    new = [record for id, record in unique.items() if id not in existing]
    return FormalFactWriter(repository).prepare('adopt_discovery', project_id,
        explicit, {x['id']: 0 for x in new},
        {'records': new, 'suppress_auto_assertions': True}, snapshot=snapshot)


KINDS = {'entity': 'class', 'relation': 'relation', 'attribute': 'attribute'}
RDF_KINDS = {'class': OWL.Class, 'relation': OWL.ObjectProperty,
             'attribute': OWL.DatatypeProperty}
MAPPING_GROUPS = {'entity': 'entity_types', 'relation': 'relation_types',
                  'attribute': 'attributes'}


def source_context(candidates):
    documents = {}
    for candidate in candidates:
        document_id = candidate.get('document_id')
        if not document_id:
            continue
        reference = documents.setdefault(document_id, {
            'document_id': document_id,
            'expected_document_version_id': candidate.get('document_version_id'),
            'candidate_ids': [],
        })
        reference['candidate_ids'].append(candidate['id'])
    return {
        'documents': list(documents.values()),
    }


def intake_run_ids(context):
    """Read the explicitly attached intake runs."""
    ids = [x.get('discovery_run_id') for x in context.get('intake_bindings') or []]
    return list(dict.fromkeys(x for x in ids if x))


def materialization_context(records, existing):
    """Read the affected fact component without rewriting historical records.

    Selected dependencies alone omit sibling attributes and incoming relations
    that can change SHACL cardinality/path results. Follow both relation endpoints
    so the validator sees complete connected context, including endpoint types.
    Unrelated historical components retain their original ontology identity.
    """
    by_id = {record['id']: record for record in [*records, *existing]
             if not (record.get('metadata') or {}).get('_deleted')}
    adjacent = {}
    for record in by_id.values():
        endpoints = ([record['id']] if record['kind'] == 'entity' else
                     [record.get('subject_id'), record.get('object_id')])
        for endpoint in endpoints:
            if endpoint:
                adjacent.setdefault(endpoint, []).append(record)
    pending = [endpoint for record in records for endpoint in (
        [record['id']] if record['kind'] == 'entity' else
        [record.get('subject_id'), record.get('object_id')]) if endpoint]
    visited, included = set(), set()
    while pending:
        endpoint = pending.pop()
        if endpoint in visited:
            continue
        visited.add(endpoint)
        for record in adjacent.get(endpoint, []):
            included.add(record['id'])
            if record['kind'] == 'relation':
                pending.extend(value for value in (
                    record.get('subject_id'), record.get('object_id')) if value)
    return [record for key, record in by_id.items() if key in included]


def published_terms(turtle):
    ontology = Ontology(turtle)
    result = []
    for kind, rdf_kind in RDF_KINDS.items():
        nodes = set(ontology.graph.subjects(RDF.type, rdf_kind))
        if kind == 'class':
            nodes.update(ontology.graph.subjects(RDF.type, RDFS.Class))
        for node in nodes:
            if ontology.is_active_term(node):
                result.append({'iri': str(node), 'kind': kind,
                    'label': str(next(ontology.graph.objects(node, RDFS.label), node))})
    return sorted(result, key=lambda x: (x['kind'], x['label'], x['iri']))


def assert_intake_sources(repository, project_id, context):
    """Assertions can be withdrawn independently of their source document JSON."""
    for run_id in intake_run_ids(context):
        run = repository.get_discovery_run(project_id, run_id)
        if run['status'] != 'draft_created':
            raise StaleSource('关联候选运行已不再等待本草案发布', details={'run_id': run_id})
        for expected in run.get('assertion_snapshots') or []:
            try:
                assertion = repository.get_assertion(project_id, expected['id'])
            except KeyError as exc:
                raise StaleSource('关联候选断言已撤回') from exc
            if _fingerprint(assertion) != expected['fingerprint']:
                raise StaleSource('关联候选断言已发生变化', details={'candidate_id': expected['id']})


def close_intake_runs(repository, project_id, context):
    for run_id in intake_run_ids(context):
        run = repository.get_discovery_run(project_id, run_id)
        if run['status'] != 'draft_created':
            continue
        resolved = {x['candidate_id'] for x in run.get('candidate_outcomes') or []}
        repository.transition_discovery_run(project_id, run_id, 'draft_created', 'closed',
            candidate_outcomes=[{'candidate_id': x['candidate_id'], 'status': 'skipped',
                'reason_code': 'draft_closed'} for x in run['candidate_bindings']
                if x['candidate_id'] not in resolved])


class OntologyIntake:
    def __init__(self, service):
        self.service = service
        self.repository = service.data
        self.drafts = service.ontology_drafts(publish=True)

    def inventory(self, p, *, document_id=None, batch_id=None, run_id=None, offset=0, limit=100):
        with self.repository.read_snapshot():
            versions = self.repository.list_ontologies(p)
            parent = versions[-1] if versions else None
            terms = published_terms(parent['turtle'] if parent else '')
            runs = self.repository.list_intake_runs(p)
            inputs = PageReads(self.repository).discovery_inputs(p)
            processed = _materialized_candidate_ids(inputs['materialized'])
            candidates = [x for x in _candidates(inputs['documents'], inputs['assertions']) if x.get('kind') in KINDS]
            scope_note = None
            if batch_id is not None or run_id is not None:
                # Scope precedes dependency expansion: an unrelated run cannot
                # keep a materialized entity selectable through its endpoints.
                candidates = [x for x in candidates
                    if x.get('document_id') == x.get('assertion_document_id')
                    and (document_id is None or x.get('document_id') == document_id)]
                verified = PageReads(self.repository).discovery_candidate_ids(p,
                    [x['id'] for x in candidates], batch_id=batch_id, run_id=run_id)
                candidates = [x for x in candidates if x['id'] in verified]
                scope_note = ('仅展示来源可核实的当前候选；缺少完整来源记录的候选未展示。'
                    if candidates else
                    '此范围没有可核实的当前候选；原处理结果可能已被重新处理替换，或缺少完整来源记录。')
            required_by = {}
            for candidate in candidates:
                if candidate['id'] in processed:
                    continue
                endpoints = ([candidate.get('subject_id'), candidate.get('object_id')]
                    if candidate['kind'] == 'relation' else [candidate.get('entity_id')]
                    if candidate['kind'] == 'attribute' else [])
                for endpoint in endpoints:
                    if endpoint:
                        required_by.setdefault(endpoint, []).append(candidate['id'])
            linked = {}
            for run in runs:
                if not run.get('unified_draft_id'):
                    continue
                link = {'draft_id': run['unified_draft_id'], 'run_id': run['id'], 'status': run['status']}
                for candidate in run.get('candidate_snapshot') or []:
                    linked.setdefault(candidate['id'], []).append(link)
            items = []
            for candidate in candidates:
                dependency = candidate['kind'] == 'entity' and candidate['id'] in required_by
                if candidate['id'] in processed and not dependency:
                    continue
                if document_id and candidate.get('document_id') != document_id:
                    continue
                matches = [x for x in terms if x['kind'] == KINDS[candidate['kind']]
                    and (x['iri'] == candidate.get('iri')
                         or x['label'] == candidate.get('proposed_type'))]
                suggested = ({'target_iri': matches[0]['iri'],
                    'target_kind': matches[0]['kind'], 'label': matches[0]['label']}
                    if len(matches) == 1 else None)
                links = linked.get(candidate['id'], [])
                items.append({**candidate, 'suggested_mapping': suggested,
                              'linked_drafts': links, 'materialized': candidate['id'] in processed,
                              'dependency_candidate': dependency,
                              'required_by': sorted(set(required_by.get(candidate['id'], [])))})
            items.sort(key=lambda x: (x.get('document_id') or '', x['id']))
            return {'items': items[offset:offset + limit], 'total': len(items),
                    'offset': offset, 'limit': limit, 'published_terms': terms,
                    'expected_ontology_id': parent['id'] if parent else None,
                    'scope_note': scope_note}

    def _plan(self, p, request):
        self.repository.get_project(p)
        ids = request['candidate_ids']
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('请选择不重复的候选 ID')
        versions = self.repository.list_ontologies(p)
        parent = versions[-1] if versions else None
        parent_id = parent['id'] if parent else None
        if request.get('expected_ontology_id') != parent_id:
            raise StaleBase('正式本体发布已变化，请重新预览')
        baseline = parent['turtle'] if parent else ''
        target_id = request.get('target_draft_id')
        new_draft = request.get('create_new_draft', False)
        if target_id and new_draft:
            raise ValueError('请选择现有草案或另建草案，不能同时选择')
        draft = None
        overlay = baseline
        if target_id:
            draft = self.drafts.store.get(p, target_id)
            self.drafts._assert_revision(draft, request.get('expected_revision'))
            _status_guard(draft, EDITABLE_STATES, '加入本轮修改')
            self.drafts._check_current(p, draft, draft['revision'])
            if draft['base_ontology_id'] != parent_id:
                raise StaleBase('所选草案不是基于当前正式发布，不能直接加入新候选')
            overlay, _ = self.drafts._ontology_for_draft(p, draft)
        elif request.get('expected_revision') is not None:
            raise ValueError('预期修订必须与目标草案一同提交')
        inputs = PageReads(self.repository).discovery_inputs(p)
        by_id = {x['id']: x for x in _candidates(inputs['documents'], inputs['assertions'])
                 if x.get('kind') in KINDS}
        if missing := set(ids) - set(by_id):
            raise StaleSource('所选候选已不存在', details={'candidate_ids': sorted(missing)})
        selected = [by_id[x] for x in sorted(ids)]
        mappings = request.get('confirmed_mappings') or {}
        if request.get('infer_hierarchy'):
            raise ValueError('选择加入采用确定性结构建议；父类发现请在草案中显式发起')
        if set(mappings) - set(ids):
            raise ValueError('确认映射只能引用本次所选候选')
        terms = published_terms(baseline)
        formal = {x['iri']: x for x in terms}
        graph = Graph().parse(data=overlay, format='turtle')
        original = set(graph)
        bindings, conflicts, dependencies = [], [], []
        already_bound = set()
        for run in self.repository.list_intake_runs(p):
            if run['status'] != 'draft_created':
                continue
            overlap = set(run.get('accepted_candidate_ids') or []) & set(ids)
            if not overlap:
                continue
            if run.get('unified_draft_id') == target_id:
                already_bound.update(overlap)
            else:
                conflicts.append({'candidate_id': sorted(overlap)[0],
                    'code': 'candidate_bound_to_other_draft',
                    'message': '所选候选已关联另一份待处理草案，请先处理原绑定',
                    'draft_id': run.get('unified_draft_id')})
        if set(ids) <= already_bound:
            conflicts.append({'candidate_id': sorted(ids)[0], 'code': 'candidate_already_bound',
                'message': '本次所选候选均已加入目标草案，可继续编辑现有变更，无需重复加入',
                'draft_id': target_id})
        map_groups = {group: {} for group in MAPPING_GROUPS.values()}
        datatypes = {}
        visible_candidates = _materialized_candidate_ids(inputs['materialized'])
        for candidate in selected:
            kind = KINDS[candidate['kind']]
            label = str(candidate.get('proposed_type') or '').strip()
            if not label:
                conflicts.append({'candidate_id': candidate['id'], 'code': 'missing_type',
                                  'message': '候选缺少结构名称'})
                continue
            if candidate.get('assertion_status') in {'rejected', 'withdrawn', 'retired'}:
                conflicts.append({'candidate_id': candidate['id'], 'code': 'source_withdrawn',
                                  'message': '候选来源断言已撤回或拒绝'})
            elif not candidate.get('assertion_id'):
                conflicts.append({'candidate_id': candidate['id'], 'code': 'source_assertion_missing',
                                  'message': '候选缺少当前来源断言，请重新解析来源资料'})
            elif candidate.get('assertion_status') == 'accepted' and candidate['id'] not in visible_candidates:
                conflicts.append({'candidate_id': candidate['id'], 'code': 'canonical_fact_unavailable',
                                  'message': '关联正式事实已不可见，请先在知识治理中处理'})
            iri = mappings.get(candidate['id'])
            if iri:
                if iri not in formal or formal[iri]['kind'] != kind:
                    conflicts.append({'candidate_id': candidate['id'], 'code': 'invalid_published_mapping',
                                      'message': '映射必须指向当前已发布且启用的同类术语'})
                    continue
                binding_kind = 'existing'
            else:
                matches = [x for x in terms if x['kind'] == kind and
                           (x['label'] == label or x['iri'] == candidate.get('iri'))]
                if matches:
                    conflicts.append({'candidate_id': candidate['id'], 'code': 'mapping_confirmation_required',
                        'message': '请核对并明确确认正式术语映射', 'suggested_iri': matches[0]['iri']})
                    continue
                iri = str(_iri(f'urn:knowledge:ontology:{p}:', label))
                binding_kind = 'proposed'
                node = URIRef(iri)
                other_kinds = [k for k, rdf in RDF_KINDS.items()
                               if (node, RDF.type, rdf) in graph and k != kind]
                if other_kinds:
                    conflicts.append({'candidate_id': candidate['id'], 'code': 'term_kind_conflict',
                                      'message': '同一术语身份不能属于不同定义种类'})
                    continue
                if (node, RDF.type, RDF_KINDS[kind]) not in graph:
                    graph.add((node, RDF.type, RDF_KINDS[kind]))
                    graph.add((node, RDFS.label, Literal(label, lang='zh')))
                    graph.add((node, RDFS.comment, Literal(f'由所选资料候选提出的{label}；发布前核对适用范围。', lang='zh')))
                elif not Ontology(overlay).is_active_term(node):
                    conflicts.append({'candidate_id': candidate['id'], 'code': 'draft_term_retired',
                                      'message': '目标定义已停用，需明确恢复后再加入'})
            group = map_groups[MAPPING_GROUPS[candidate['kind']]]
            mapping_name = candidate['proposed_type']
            if mapping_name in group and group[mapping_name] != iri:
                conflicts.append({'candidate_id': candidate['id'], 'code': 'mapping_conflict',
                                  'message': '同名候选在本次确认中映射至不同身份'})
            group[mapping_name] = iri
            bindings.append({'candidate_id': candidate['id'], 'target_iri': iri,
                'target_kind': kind, 'binding_kind': binding_kind,
                'required_operation_ids': [], 'optional_operation_ids': []})
            required_ids = ([candidate.get('subject_id'), candidate.get('object_id')]
                if kind == 'relation' else [candidate.get('entity_id')] if kind == 'attribute' else [])
            for endpoint in required_ids:
                if not endpoint or endpoint not in ids or by_id.get(endpoint, {}).get('kind') != 'entity':
                    dependencies.append({'candidate_id': endpoint or '',
                        'required_by': candidate['id'], 'message': '请明确选择同批关联实体候选'})
                elif by_id[endpoint].get('document_version_id') != candidate.get('document_version_id'):
                    dependencies.append({'candidate_id': endpoint, 'required_by': candidate['id'],
                        'message': '关联实体候选必须属于同一来源文档版本'})
            if kind == 'attribute' and binding_kind == 'proposed':
                try:
                    datatype = primitive_datatype(candidate.get('value'))
                except ValueError as exc:
                    conflicts.append({'candidate_id': candidate['id'], 'code': 'invalid_datatype', 'message': str(exc)})
                    continue
                if iri in datatypes and datatypes[iri] != datatype:
                    conflicts.append({'candidate_id': candidate['id'], 'code': 'datatype_conflict', 'message': '所选属性值需要不同数据类型'})
                datatypes[iri] = datatype
                existing = list(graph.objects(URIRef(iri), RDFS.range))
                if existing and existing != [URIRef(datatype)]:
                    conflicts.append({'candidate_id': candidate['id'], 'code': 'datatype_conflict', 'message': '属性数据类型与草案定义不同'})
                elif not existing:
                    graph.add((URIRef(iri), RDFS.range, URIRef(datatype)))
        for candidate in selected:
            if candidate['kind'] != 'attribute':
                continue
            attribute = map_groups['attributes'].get(candidate.get('proposed_type'))
            entity = by_id.get(candidate.get('entity_id'), {})
            domain = map_groups['entity_types'].get(entity.get('proposed_type'))
            if attribute and domain and candidate['id'] not in mappings:
                graph.add((URIRef(attribute), RDFS.domain, URIRef(domain)))
        # Only append new triples; all pre-existing manual structure survives.
        assert original <= set(graph)
        desired = graph.serialize(format='turtle')
        operations = canonical_turtle_diff(overlay, desired, published=parent is not None,
                                           base_ontology_id=parent_id)
        suggestions = [{**{key: op.get(key) for key in ('action', 'target_iri', 'before', 'after')},
                        'fingerprint': _fingerprint({key: op.get(key) for key in ('action', 'target_iri', 'before', 'after')})}
                       for op in operations]
        proposed = [x for x in bindings if x['binding_kind'] == 'proposed']
        reusable = [x for x in bindings if x['binding_kind'] == 'existing']
        existing = materialized_records(self.repository, p)
        processed = _materialized_candidate_ids(existing)
        relevant = [x for x in existing if _materialized_candidate_ids([x]) & set(ids)]
        fact_context = relevant
        binding_by_id = {x['candidate_id']: x for x in bindings}
        for record in relevant:
            for candidate_id in _materialized_candidate_ids([record]) & set(ids):
                binding = binding_by_id.get(candidate_id)
                if binding and record.get('type') != binding['target_iri']:
                    conflicts.append({'candidate_id': candidate_id, 'code': 'materialized_mapping_changed',
                        'message': '此候选已有不同映射的正式事实；不能通过再次确认隐式改写'})
        fact_outcomes = []
        if conflicts or dependencies:
            for candidate in selected:
                issue = next((x for x in conflicts if x.get('candidate_id') == candidate['id']), None)
                dependency = next((x for x in dependencies if x.get('required_by') == candidate['id']), None)
                fact_outcomes.append({'candidate_id': candidate['id'], 'outcome': 'blocked',
                    'reason_code': issue['code'] if issue else 'missing_dependency' if dependency else 'selection_blocked',
                    'message': issue['message'] if issue else dependency['message'] if dependency else '请先处理本次选择中的映射或依赖冲突'})
        else:
            materialization = {'id': target_id or 'preview', 'candidate_snapshot': selected, 'mappings': map_groups}
            records, skipped = _materialize_candidates(p, materialization, parent_id or '__PREVIEW__', repository=self.repository)
            records = _reuse_materialized_records(records, existing, processed)
            # Required fields may only be satisfied by another selected fact;
            # validating the entity prefix would drop a valid complete selection.
            accepted = records
            fact_context = materialization_context(accepted, existing)
            report = Ontology(desired).validate_timeline(fact_context)
            if not report['conforms']:
                conflicts.append({'code': 'ontology_validation_failed',
                    'message': '所选事实与现有知识合并后未通过本体校验，请核对约束和冲突事实',
                    'report': report})
            accepted_ids = _materialized_candidate_ids(accepted)
            for candidate in selected:
                candidate_id = candidate['id']
                skip = next((x for x in skipped if x.get('candidate_id') == candidate_id), None)
                if not report['conforms']:
                    outcome, code, message = 'blocked', 'ontology_validation_failed', conflicts[-1]['message']
                elif candidate_id not in accepted_ids:
                    outcome, code, message = 'skip', 'ontology_validation_failed', (skip or {}).get('reason', '所选事实未通过最终结构校验')
                elif candidate_id in processed:
                    outcome, code, message = 'reuse', 'already_materialized', '复用此候选已有的正式事实'
                elif proposed:
                    outcome, code, message = 'pending', 'requires_approved_structure', '按当前建议可通过校验；仍须关联结构最终获批，发布时重新核对'
                else:
                    outcome, code, message = 'add', 'published_mapping_valid', '已通过当前正式结构校验，确认后新增事实'
                fact_outcomes.append({'candidate_id': candidate_id, 'outcome': outcome, 'reason_code': code, 'message': message})
        target = {'draft_id': target_id, 'revision': draft['revision'] if draft else None,
                  'base_ontology_id': parent_id, 'create_new_draft': new_draft}
        source_hash = _fingerprint({'project_id': p, 'candidates': selected,
            'baseline': _fingerprint(baseline), 'mappings': mappings})
        fingerprint = _fingerprint({'source': source_hash, 'target': target,
            'name': request.get('name'), 'suggestions': suggestions,
            'fact_context': sorted((x['id'], x.get('version_id')) for x in fact_context),
            'fact_outcomes': fact_outcomes,
            'target_operations': [x['fingerprint'] for x in self.drafts._active_operations(p, target_id)] if target_id else []})
        warnings = [{'code': 'draft_terms_unpublished', 'message': '加入草案不等于正式发布；关联事实仅在获批结构下处理'}] if proposed else []
        result = {'target': target, 'candidate_ids': sorted(ids), 'suggestions': suggestions,
            'associated_fact_scope': selected, 'new': proposed, 'reused': reusable,
            'fact_outcomes': fact_outcomes,
            'conflicts': conflicts, 'warnings': warnings, 'missing_dependencies': dependencies,
            'source_fingerprint': source_hash, 'preview_fingerprint': fingerprint,
            'can_add': bool(proposed and (target_id or new_draft) and not conflicts and not dependencies),
            'can_confirm_reuse': bool(reusable and not proposed and not conflicts and not dependencies
                                      and not target_id and not new_draft)}
        return result, draft, map_groups

    def preview(self, p, request):
        with self.repository.read_snapshot():
            result, _, _ = self._plan(p, request)
            return result

    @staticmethod
    def _request_identity(p, request, actor, intent, actor_identity=None):
        key = request.get('idempotency_key', '').strip()
        if not key:
            raise ValueError('idempotency_key 不能为空')
        identity = actor_identity or actor
        run_id = 'intake-' + hashlib.sha256(f'{p}\0{identity}\0{key}'.encode()).hexdigest()
        return run_id, _fingerprint({'project_id': p, 'request': request,
                                    'actor_identity': identity, 'intent': intent})

    def _replay(self, p, run_id, request_hash):
        try:
            run = self.repository.get_discovery_run(p, run_id)
        except KeyError:
            return None
        if run.get('intake_request_hash') != request_hash:
            raise DiscoveryRunConflict('同一请求身份已用于不同写入意图', run_id=run_id)
        return deepcopy(run['intake_result'])

    def _frozen_plan(self, p, request):
        result, draft, mappings = self._plan(p, request)
        if request.get('source_fingerprint') != result['source_fingerprint']:
            raise StaleSource('所选来源已变化，请重新预览')
        if request.get('preview_fingerprint') != result['preview_fingerprint']:
            raise ValidationChanged('加入预演已变化，请重新预览')
        return result, draft, mappings

    def _run(self, p, run_id, request_hash, result, mappings, bindings, receipt, response, *, draft_id=None):
        candidates = result['associated_fact_scope']
        frozen_sources = self.drafts._refresh_source_context(p, source_context(candidates))['documents']
        assertions = []
        for candidate in candidates:
            if candidate.get('assertion_id'):
                assertion = self.repository.get_assertion(p, candidate['assertion_id'])
                assertions.append({'id': assertion['id'], 'fingerprint': _fingerprint(assertion)})
        processed = _materialized_candidate_ids(materialized_records(self.repository, p))
        return {'id': run_id, 'project_id': p,
            'source_fingerprint': result['source_fingerprint'],
            'base_ontology_id': result['target']['base_ontology_id'],
            'candidate_snapshot': candidates, 'accepted_candidate_ids': result['candidate_ids'],
            'candidate_bindings': bindings, 'mappings': mappings,
            'materialized_candidate_ids': sorted(processed & set(result['candidate_ids'])),
            'status': 'draft_created' if draft_id else 'ready_to_finalize',
            'unified_draft_id': draft_id, 'intake_request_hash': request_hash,
            'intake_receipt': receipt, 'intake_result': response,
            'assertion_snapshots': assertions, 'source_documents': frozen_sources}

    def add(self, p, request, *, actor, actor_identity=None):
        run_id, request_hash = self._request_identity(p, request, actor, 'add', actor_identity)
        with self.repository.intake_transaction():
            if replay := self._replay(p, run_id, request_hash):
                return replay
            result, draft, mappings = self._frozen_plan(p, request)
            if not result['can_add']:
                raise ValidationFailed('所选候选尚不能加入草案', details=result)
            selection = request.get('selected_suggestion_fingerprints')
            suggestions = result['suggestions']
            if selection is not None:
                known = {x['fingerprint'] for x in suggestions}
                if set(selection) - known:
                    raise ValidationChanged('所选结构建议已变化')
                suggestions = [x for x in suggestions if x['fingerprint'] in selection]
            new_context = self.drafts._refresh_source_context(p, source_context(result['associated_fact_scope']))
            if draft is None:
                draft = self.drafts.create(p, request.get('expected_ontology_id'), 'manual',
                    request.get('name') or '本轮结构补充', actor)
            context = deepcopy(draft.get('source_context') or {})
            references = {x['document_id']: x for x in context.get('documents') or []}
            for reference in new_context['documents']:
                old = references.get(reference['document_id'])
                if old:
                    reference['candidate_ids'] = sorted(set(old.get('candidate_ids') or []) | set(reference['candidate_ids']))
                    for key in ('candidate_fingerprints', 'candidate_statuses'):
                        reference[key] = {**old.get(key, {}), **reference.get(key, {})}
                references[reference['document_id']] = reference
            context['documents'] = list(references.values())
            context.setdefault('intake_bindings', []).append({'discovery_run_id': run_id,
                'selected_candidate_ids': result['candidate_ids'], 'request_id': run_id,
                'source_fingerprint': result['source_fingerprint'], 'actor': actor})
            working, previous = self.drafts._ontology_for_draft(p, draft)
            compiled = []
            # The draft guards every accumulated source, but new operations are
            # attributed only to this explicitly confirmed selection.
            source_draft = {**draft, 'source_kind': 'discovery', 'source_context': new_context}
            for suggestion in suggestions:
                command = {**suggestion, 'reason': '明确选择资料候选加入本轮修改'}
                built = self.drafts._compile_command(p, source_draft, command, Ontology(working))
                working = apply_operations(working, built)
                compiled.extend(built)
            saved = self.drafts.store.append_operations(p, draft['id'], compiled)
            all_operations = [*previous, *saved]
            bindings = deepcopy([*result['new'], *result['reused']])
            for binding in bindings:
                if binding['binding_kind'] == 'existing':
                    continue
                actions = {'create_term', 'set_datatype'} if binding['target_kind'] == 'attribute' else {'create_term'}
                for action in actions:
                    matches = [x for x in all_operations if x['target_iri'] == binding['target_iri'] and x['action'] == action]
                    if not matches:
                        raise ValidationFailed('所选建议缺少关联事实必需的结构操作', details={'candidate_id': binding['candidate_id'], 'action': action})
                    binding['required_operation_ids'].append(matches[-1]['id'])
            updated = self.drafts._cas(p, draft['id'], draft['revision'], {
                'source_context': context, 'validation_report': None,
                'validation_fingerprint': None, 'validated_at': None, 'submitted_at': None})
            current = self.drafts._preview(p, updated)
            receipt = {'request_id': run_id, 'draft_id': draft['id'], 'revision': updated['revision'],
                'run_id': run_id, 'structure': {'added': len(saved), 'reused': sum(
                    not any(x['target_iri'] == b['target_iri'] and x['action'] == 'create_term' for x in saved)
                    for b in bindings)},
                'facts': {'selected': len(bindings), 'pending': len(bindings)},
                'index': {'status': 'not_requested'}}
            response = {**result, 'suggestions': suggestions, 'draft': current, 'receipt': receipt}
            run = self._run(p, run_id, request_hash, result, mappings, bindings, receipt, response, draft_id=draft['id'])
            self.repository.create_discovery_run(p, run)
            return response

    def confirm_reuse(self, p, request, *, actor, actor_identity=None):
        run_id, request_hash = self._request_identity(p, request, actor, 'reuse', actor_identity)
        with self.repository.intake_transaction():
            if replay := self._replay(p, run_id, request_hash):
                return replay
            result, draft, mappings = self._frozen_plan(p, request)
            if draft is not None or request.get('create_new_draft') or not result['can_confirm_reuse']:
                raise ValidationFailed('只复用正式术语的确认不能建立或使用结构草案', details=result)
            parent_id = request['expected_ontology_id']
            parent = self.repository.get_ontology(p, parent_id)
            existing = materialized_records(self.repository, p)
            processed = _materialized_candidate_ids(existing)
            source = {'id': run_id, 'candidate_snapshot': result['associated_fact_scope'], 'mappings': mappings}
            proposed, skipped = _materialize_candidates(p, source, parent_id, repository=self.repository)
            proposed = _reuse_materialized_records(proposed, existing, processed)
            accepted = proposed
            report = Ontology(parent['turtle']).validate_timeline(
                materialization_context(accepted, existing))
            if not report['conforms']:
                raise ValidationFailed(
                    '所选事实与现有知识合并后未通过本体校验，请核对约束和冲突事实',
                    details={'report': report})
            validation = {'conforms': True, 'accepted_count': len(accepted),
                          'skipped_count': len(skipped)}
            existing_ids = {x['id'] for x in existing}
            fact_plan = prepare_intake_facts(self.repository, p, accepted, result['associated_fact_scope'])
            written = self.repository.execute_formal_write(fact_plan)
            saved = list({x['id']: x for x in written['accepted_records'] if x['id'] not in existing_ids}.values())
            materialized = _materialized_candidate_ids(accepted)
            batch = {'run_id': run_id, 'selected': len(result['candidate_ids']),
                'added': len(saved), 'reused': len(set(result['candidate_ids']) & materialized) - len(saved),
                'skipped': len(set(result['candidate_ids']) - materialized)}
            # Durable projection work uses the same existing outbox as ontology publication.
            from ..core.time import utc_now
            job_id = f'ontology-sync:{run_id}'
            self.repository.save_artifact('ontology_sync_job', {'id': job_id, 'project_id': p,
                'status': 'pending', 'ontology_id': parent_id, 'draft_id': None,
                'fingerprint': result['source_fingerprint'], 'record_ids': [x['id'] for x in saved],
                'created_at': utc_now(), 'completed_at': None})
            receipt = {'request_id': run_id, 'ontology': {'id': parent_id, 'published': False},
                'facts': {**{k: batch[k] for k in ('selected', 'added', 'reused', 'skipped')}, 'per_batch': [batch]},
                'index': {'task_id': job_id, 'status': 'pending'}, 'validation': validation,
                'skipped_candidates': skipped}
            response = {**result, 'receipt': receipt}
            run = self._run(p, run_id, request_hash, result, mappings, result['reused'], receipt, response)
            self.repository.create_discovery_run(p, run)
            self.repository.transition_discovery_run(p, run_id, 'ready_to_finalize', 'finalized_no_change',
                candidate_outcomes=[{'candidate_id': x, 'status': 'materialized' if x in materialized else 'skipped',
                    'reason_code': 'materialized' if x in materialized else 'ontology_validation_failed'} for x in result['candidate_ids']])
            return response
