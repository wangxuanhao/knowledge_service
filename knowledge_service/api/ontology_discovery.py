"""开放本体发现的路由层（install 挂载 /api/projects/{p}/ontology-discovery）。

业务逻辑（候选聚合、归纳、物化、生命周期）在 services.ontology_discovery；
本文件只负责把 HTTP 请求转成对业务函数和 repository 的调用。
"""
from collections import Counter
from copy import deepcopy
import hashlib
from importlib import metadata as importlib_metadata
import json
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import Field
from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from ..models import Request
from ..services.ontology import Ontology
from ..services.ontology_adapters import (
    DEPRECATION,
    compatibility_payload,
)
from ..repository.discovery_run_store import (
    BINDING_OPTIONAL_ACTIONS,
    DiscoveryRunConflict,
    discovery_result_kind,
)
from ..services.discovery_vocabulary import (
    GeneratedVocabularyConflict,
    InvalidBaselineVocabulary,
    audit_formal_vocabulary,
    discovery_source_fingerprint,
)
from ..services.ontology_drafts import OntologyDrafts, StaleBase, StaleSource
from ..services.ontology_operations import canonical_turtle_diff
from ..services.ontology_discovery import (
    _candidates, _candidate_lifecycle, _candidate_mindmap, _induce,
    _materialize_candidates, _validated_materialization, _ontology_diff,
    _normalize_induction_candidates, _quality_warnings, _summary, _literal_language,
    _materialized_candidate_ids, _reuse_materialized_records,
)
from ..utils.diagnostics import timed
from ..core.time import utc_now


NORMALIZER_VERSION = 'v1'
GENERATOR_CONTRACT = 'semantica-0.6.7+materialization-context-v2'
ATTRIBUTE_THRESHOLD = 2


def _semantica_runtime_version():
    try:
        return importlib_metadata.version('semantica')
    except importlib_metadata.PackageNotFoundError:
        return 'unavailable'


def _canonical_copy(value):
    return json.loads(json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':')))


def _canonical_candidates(candidates):
    snapshot = _canonical_copy(candidates)
    return sorted(snapshot, key=lambda candidate: json.dumps(
        candidate, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':')))


def _initial_candidate_outcomes(normalization):
    outcomes = []
    for conflict in normalization.conflicts:
        reason_code = (
            'low_frequency_attribute'
            if conflict.get('code') == 'low_frequency_attribute'
            else 'ontology_term_conflict')
        outcomes.append({
            'candidate_id': conflict['candidate_id'],
            'status': (
                'deferred'
                if reason_code == 'low_frequency_attribute' else 'skipped'),
            'reason_code': reason_code,
            'diagnostic_code': conflict.get('code'),
        })
    return outcomes


def _expected_run_snapshot(source_fingerprint, candidates, normalization,
                           induction_candidates, mappings, parent, request,
                           runtime_version, generation_options):
    initial_outcomes = _initial_candidate_outcomes(normalization)
    return {
        'source_fingerprint': source_fingerprint,
        'candidate_snapshot': _canonical_copy(candidates),
        'accepted_candidate_ids': [item['id'] for item in induction_candidates],
        'merged_groups': _canonical_copy(normalization.merged_groups),
        'conflicts': _canonical_copy(normalization.conflicts),
        'mappings': _canonical_copy(mappings),
        'initial_candidate_outcomes': initial_outcomes,
        'diagnostics': _canonical_copy(normalization.diagnostics),
        'base_ontology_id': parent['id'] if parent else None,
        'base_ontology_fingerprint': generation_options[
            'baseline_turtle_sha256'],
        'normalizer_version': NORMALIZER_VERSION,
        'generator_version': GENERATOR_CONTRACT,
        'runtime_version': runtime_version,
        'attribute_threshold': ATTRIBUTE_THRESHOLD,
        'generation_options': generation_options,
        'request_name': request.name,
    }


def _existing_run_result(repository, governed, run, expected):
    run_id = run['id']
    if run.get('source_fingerprint') != expected['source_fingerprint']:
        raise DiscoveryRunConflict(
            'deterministic discovery run id has a different fingerprint',
            run_id=run_id)
    if any(run.get(key) != value for key, value in expected.items()):
        raise DiscoveryRunConflict(
            'deterministic discovery run id has a different snapshot',
            run_id=run_id)
    result_kind = discovery_result_kind(run)
    if result_kind != 'draft':
        return {'result_kind': result_kind, 'run': run, 'discovery_run': run}
    draft_id = run['unified_draft_id']
    legacy = repository.get_artifact('ontology_discovery_draft', draft_id)
    current = governed.get(run['project_id'], draft_id)
    return {
        **legacy,
        'candidate_snapshot': run['candidate_snapshot'],
        'candidate_outcomes': run['candidate_outcomes'],
        'mappings': run['mappings'],
        'result_kind': 'draft',
        'run': run,
        'discovery_run': run,
        'operations': current['operations'],
    }


def _hydrate_legacy_draft(repository, project_id, draft):
    run_id = draft.get('discovery_run_id')
    if not run_id:
        return draft
    run = repository.get_discovery_run(project_id, run_id)
    candidate_snapshot = run['candidate_snapshot']
    return {
        **draft,
        'candidate_ids': [item['id'] for item in candidate_snapshot],
        'candidate_count': len(candidate_snapshot),
        'candidate_snapshot': candidate_snapshot,
        'candidate_outcomes': run['candidate_outcomes'],
        'mappings': run['mappings'],
    }


def _generation_options(request, baseline):
    payload = (request.model_dump() if hasattr(request, 'model_dump')
               else request.dict())
    payload.pop('name', None)
    return {
        'request': payload,
        'build_hierarchy': True,
        'min_occurrences': 1,
        'baseline_turtle_sha256': hashlib.sha256(
            (baseline or '').encode('utf-8')).hexdigest(),
    }


def _source_fingerprint(project_id, parent, candidates, request, *,
                        runtime_version=None, generation_options=None):
    baseline = parent['turtle'] if parent else None
    if runtime_version is None:
        runtime_version = _semantica_runtime_version()
    if generation_options is None:
        generation_options = _generation_options(request, baseline)
    return discovery_source_fingerprint(
        project_id, parent['id'] if parent else None, candidates,
        normalizer_version=NORMALIZER_VERSION,
        generator_contract=GENERATOR_CONTRACT,
        runtime_version=runtime_version,
        attribute_threshold=ATTRIBUTE_THRESHOLD,
        generation_options=generation_options,
        request_name=request.name)


def _run_id(project_id, fingerprint):
    digest = hashlib.sha256(
        f'{project_id}\0{fingerprint}'.encode('utf-8')).hexdigest()
    return f'ontology-discovery-run:{digest}'


def _target_for_candidate(candidate, mappings):
    group = {
        'entity': 'entity_types', 'relation': 'relation_types',
        'attribute': 'attributes',
    }.get(candidate.get('kind'))
    return (mappings.get(group) or {}).get(
        candidate.get('vocabulary_name', candidate.get('proposed_type'))) \
        if group else None


def _candidate_bindings(normalization, induction_candidates, original_candidates,
                        mappings, operations, baseline_turtle=None):
    accepted = {item['id']: item for item in induction_candidates}
    originals = {item['id']: item for item in original_candidates}
    operation_by_target = {}
    for operation in operations:
        operation_by_target.setdefault(operation['target_iri'], []).append(operation)
    baseline_graph=Graph()
    if baseline_turtle:
        baseline_graph.parse(data=baseline_turtle,format='turtle')
    existing_iris={str(subject) for kind in (
        OWL.Class,RDFS.Class,OWL.ObjectProperty,OWL.DatatypeProperty)
        for subject in baseline_graph.subjects(RDF.type,kind)}
    result = []
    for binding in normalization.candidate_bindings:
        if binding.get('status') == 'quarantined':
            continue
        candidate_id = binding['candidate_id']
        leader_id = binding.get('accepted_candidate_id') or candidate_id
        leader = accepted.get(leader_id) or accepted.get(candidate_id) or {}
        original = originals.get(candidate_id) or {}
        target_iri = binding.get('iri') or _target_for_candidate(leader, mappings)
        if not target_iri:
            continue
        target_kind = ('class' if original.get('kind') == 'entity'
                       else original.get('kind'))
        binding_kind = ('existing' if target_iri in existing_iris
                        else 'proposed')
        target_operations = operation_by_target.get(target_iri, [])
        required_actions = {'create_term'}
        if original.get('kind') == 'attribute':
            required_actions.add('set_datatype')
        required = ([] if binding_kind == 'existing' else [
            operation['id'] for operation in target_operations
            if operation['action'] in required_actions])
        optional = ([] if binding_kind == 'existing' else [
            operation['id'] for operation in target_operations
            if operation['action'] in BINDING_OPTIONAL_ACTIONS[target_kind]])
        result.append({
            'candidate_id': candidate_id,
            'target_iri': target_iri,
            'target_kind': target_kind,
            'binding_kind': binding_kind,
            'required_operation_ids': required,
            'optional_operation_ids': optional,
        })
    return result


def _compact_run_summary(candidate_count, normalization, binding_count):
    return {
        'candidate_count': candidate_count,
        'accepted_count': normalization.diagnostics.get('accepted_count', 0),
        'conflict_count': len(normalization.conflicts),
        'binding_count': binding_count,
    }


def _finalize_result(run, *, materialized_count=None, skipped_candidates=None,
                     validation=None):
    result = {
        'result_kind': 'mapping_only',
        'run': run,
        'discovery_run': run,
    }
    if materialized_count is not None:
        result['materialized_count'] = materialized_count
    if skipped_candidates is not None:
        result['skipped_candidates'] = skipped_candidates
    if validation is not None:
        result['validation'] = validation
    return result


def _current_finalize_fingerprint(project_id, run, parent, candidates):
    generation_options = _canonical_copy(run['generation_options'])
    generation_options['baseline_turtle_sha256'] = hashlib.sha256(
        ((parent or {}).get('turtle') or '').encode('utf-8')).hexdigest()
    return discovery_source_fingerprint(
        project_id, parent['id'] if parent else None, candidates,
        normalizer_version=NORMALIZER_VERSION,
        generator_contract=GENERATOR_CONTRACT,
        runtime_version=_semantica_runtime_version(),
        attribute_threshold=ATTRIBUTE_THRESHOLD,
        generation_options=generation_options,
        request_name=run['request_name'])


def _processed_discovery_candidate_ids(repository, project_id):
    return _materialized_candidate_ids(repository.current_records(
        project_id, vectors='none', kinds=['entity', 'relation', 'attribute']))


class DraftRequest(Request):
    name: str = Field(default='发现本体', min_length=1, max_length=200)


class DraftReview(Request):
    excluded_candidate_ids: list[str] = Field(default_factory=list, max_length=10000)
    excluded_terms: list[str] = Field(default_factory=list, max_length=1000)
    term_labels: dict[str, str] = Field(default_factory=dict)


def install(app, service):
    router = APIRouter(prefix='/api/projects/{p}/ontology-discovery')
    governed = OntologyDrafts(service.repository, publisher=service.repository)

    @app.exception_handler(GeneratedVocabularyConflict)
    async def generated_vocabulary_error(_request, exc):
        return JSONResponse(status_code=422,content={
            'detail':'Generated ontology vocabulary conflicts with governed RDF shape',
            'code':'generated_vocabulary_conflict',
            'details':{'reason':exc.reason,'detail':exc.detail}})

    @app.exception_handler(InvalidBaselineVocabulary)
    async def invalid_baseline_error(_request, exc):
        return JSONResponse(status_code=422,content={
            'detail':'Published baseline contains an IRI with multiple governed kinds',
            'code':'invalid_baseline_dual_kind','details':{}})

    @app.exception_handler(DiscoveryRunConflict)
    async def discovery_run_error(_request, exc):
        return JSONResponse(status_code=409,content={
            'detail':str(exc),'code':'discovery_run_conflict',
            'details':exc.details})

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
            'legacy_route': 'POST /ontology-discovery/drafts',
            'documents': list(documents.values()),
        }

    @router.get('')
    def overview(p:str):
        service.repository.get_project(p)
        # 一次 current_records 扫描同时供给候选摊平与生命周期分类；
        # 过去两者各自读取整个项目。二者都不看向量，因此完全跳过 float32 列。
        records=service.repository.current_records(p, vectors='none')
        candidates=_candidates(service.repository,p,records)
        stored_drafts=service.repository.list_artifacts(
            'ontology_discovery_draft',p)
        drafts=[_hydrate_legacy_draft(service.repository,p,draft)
                for draft in stored_drafts]
        ontologies=service.repository.list_ontologies(p)
        latest_run=service.repository.latest_discovery_run(p)
        states,status_counts=_candidate_lifecycle(service.repository,p,candidates,drafts,records)
        enriched_drafts=[]
        for draft in drafts:
            try:summary=draft.get('summary') or Ontology(draft.get('turtle','')).summary()
            except (ValueError,TypeError):summary={'classes':[],'relations':[],'attributes':[]}
            enriched_drafts.append({**draft,'schema_summary':summary})
        return {**_summary(candidates),
            'drafts':enriched_drafts,
            'quality_warnings':_quality_warnings(candidates),
            'relation_constraint_policy':'open_no_domain_range',
            'published':bool(ontologies),
            'ontology_id':ontologies[-1]['id'] if ontologies else None,
            'candidate_status_counts':status_counts,
            'latest_run':({
                'id':latest_run['id'],
                'status':latest_run['status'],
                'result_kind':discovery_result_kind(latest_run),
                'unified_draft_id':latest_run.get('unified_draft_id'),
                'diagnostics':latest_run.get('diagnostics') or {},
                'created_at':latest_run.get('created_at'),
                'updated_at':latest_run.get('updated_at'),
            } if latest_run else None),
            'unpublished_candidate_count':status_counts['pending']+status_counts['included_in_draft'],
            'requires_candidate_review':bool(status_counts['pending']+status_counts['included_in_draft']+status_counts['approved']),
            'requires_controlled_reingest':False}

    @router.get('/runs')
    def list_runs(p: str):
        service.repository.get_project(p)
        items = service.repository.list_discovery_runs(p)
        return {'items': items, 'total': len(items)}

    @router.get('/runs/{run_id}')
    def get_run(p: str, run_id: str):
        service.repository.get_project(p)
        return service.repository.get_discovery_run(p, run_id)

    @router.get('/audit')
    def audit(p: str):
        service.repository.get_project(p)
        ontologies = service.repository.list_ontologies(p)
        turtle = ontologies[-1]['turtle'] if ontologies else ''
        return audit_formal_vocabulary(turtle)

    @router.get('/candidate-mindmap')
    def candidate_mindmap(p:str,limit:int=500):
        # PERF：过去的两次读取曾是三次——`_candidates` 和 `_candidate_lifecycle`
        # 各自扫描全部当前记录，`list_artifacts` 还会解码每个项目的草案。
        # 计时日志会分别列出剩余的每次读取。
        service.repository.get_project(p)
        with timed('候选脑图', limit=limit) as record:
            # 两种聚合都不看向量，因此完全跳过 float32 列。
            records=service.repository.current_records(p, vectors='none')
            candidates=_candidates(service.repository,p,records)
            stored_drafts=service.repository.list_artifacts(
                'ontology_discovery_draft',p)
            drafts=[_hydrate_legacy_draft(service.repository,p,draft)
                    for draft in stored_drafts]
            states,_=_candidate_lifecycle(service.repository,p,candidates,drafts,records)
            payload=_candidate_mindmap(candidates,states,limit)
            record['nodes']=len(payload.get('nodes') or [])
            record['edges']=len(payload.get('edges') or [])
            return payload

    @router.post('/drafts',status_code=201)
    def create_draft(p:str,request:DraftRequest):
        service.repository.get_project(p)
        candidates=_canonical_candidates([
            item for item in _candidates(service.repository,p)
            if item.get('kind') in {'entity','relation','attribute'}])
        if not candidates:raise ValueError('尚无开放发现候选，请先用“开放本体发现”模式解析文档')
        ontologies=service.repository.list_ontologies(p)
        parent=ontologies[-1] if ontologies else None
        baseline=parent['turtle'] if parent else None
        processed_ids=_processed_discovery_candidate_ids(service.repository,p)
        processed_ids.intersection_update(item['id'] for item in candidates)
        induction_candidates,normalization=_normalize_induction_candidates(candidates,baseline)
        if induction_candidates:
            try:
                turtle,mappings,inferred=_induce(
                    p,request.name,induction_candidates,baseline_turtle=baseline)
            except GeneratedVocabularyConflict:
                raise
            except ValueError as exc:
                raise GeneratedVocabularyConflict(
                    'generated_vocabulary_invalid',{
                        'error_type':type(exc).__name__}) from exc
        else:
            turtle=baseline or ''
            mappings={'entity_types':{},'relation_types':{},'attributes':{}}
            inferred={'metadata':{},'validation':{}}
        try:
            summary=(Ontology(turtle).summary() if turtle.strip() else
                     {'classes':[],'relations':[],'attributes':[],'triples':0})
            diff=_ontology_diff(baseline,turtle)
            proposed_operations=(canonical_turtle_diff(
                baseline or '',turtle,published=parent is not None,
                base_ontology_id=parent['id'] if parent else None)
                if induction_candidates else [])
        except Exception as exc:
            raise GeneratedVocabularyConflict(
                'generated_vocabulary_invalid',{
                    'error_type':type(exc).__name__}) from exc
        runtime_version=_semantica_runtime_version()
        generation_options=_generation_options(request,baseline)
        source_fingerprint=_source_fingerprint(
            p,parent,candidates,request,runtime_version=runtime_version,
            generation_options=generation_options)
        run_id=_run_id(p,source_fingerprint)
        candidate_outcomes=_initial_candidate_outcomes(normalization)
        expected_run=_expected_run_snapshot(
            source_fingerprint,candidates,normalization,
            induction_candidates,mappings,parent,request,runtime_version,
            generation_options)

        with service.repository._transaction():
            current_ontologies=service.repository.list_ontologies(p)
            current_parent=current_ontologies[-1] if current_ontologies else None
            current_parent_id=current_parent['id'] if current_parent else None
            expected_parent_id=parent['id'] if parent else None
            if (current_parent_id!=expected_parent_id
                    or (current_parent or {}).get('turtle')!=(parent or {}).get('turtle')):
                raise StaleBase(
                    'discovery base changed while creating the run',details={
                        'base_ontology_id':expected_parent_id,
                        'current_ontology_id':current_parent_id})
            current_candidates=_canonical_candidates([
                item for item in _candidates(service.repository,p)
                if item.get('kind') in {'entity','relation','attribute'}])
            current_runtime_version=_semantica_runtime_version()
            current_generation_options=_generation_options(
                request,(current_parent or {}).get('turtle'))
            current_fingerprint=_source_fingerprint(
                p,current_parent,current_candidates,request,
                runtime_version=current_runtime_version,
                generation_options=current_generation_options)
            if current_fingerprint!=source_fingerprint:
                raise StaleSource(
                    'discovery source changed while creating the run',details={
                        'expected_source_fingerprint':source_fingerprint,
                        'current_source_fingerprint':current_fingerprint})
            try:
                existing=service.repository.get_discovery_run(p,run_id)
            except KeyError:
                existing=None
            if existing is not None:
                return _existing_run_result(
                    service.repository,governed,existing,expected_run)

            current_processed=(_processed_discovery_candidate_ids(service.repository,p)
                & {item['id'] for item in current_candidates})
            if current_processed!=processed_ids:
                raise StaleSource('discovery materialization changed while creating the run')

            predecessor=service.repository.latest_discovery_run(
                p,statuses={'stale_base','stale_source','closed'})
            draft_id=None;preview=None
            binding_shapes=_candidate_bindings(
                normalization,induction_candidates,current_candidates,
                mappings,[],baseline)
            has_proposed_binding=any(
                item['binding_kind']=='proposed' for item in binding_shapes)
            if proposed_operations and has_proposed_binding:
                context=source_context(current_candidates)
                preview=governed.create_with_command(
                    p,expected_parent_id,'discovery',request.name,
                    'api:ontology-discovery',source_context=context,
                    summary='开放发现候选归纳',command={
                        'action':'diff_turtle','edited_turtle':turtle,
                        'reason':'Semantica 0.6.7 开放发现建议'})
                draft_id=preview['id']
                bindings=_candidate_bindings(
                    normalization,induction_candidates,current_candidates,
                    mappings,preview['operations'],baseline)
                compact=_compact_run_summary(
                    len(current_candidates),normalization,len(bindings))
                preview=governed.update_publication_effects(
                    p,draft_id,preview['revision'],{
                        'discovery_run_id':run_id,'summary':compact})
                status='draft_created'
            else:
                bindings=binding_shapes
                reusable=[item for item in bindings
                          if item['binding_kind']=='existing']
                status=('ready_to_finalize' if reusable
                        else 'diagnosed_no_change')
                compact=_compact_run_summary(
                    len(current_candidates),normalization,len(bindings))

            run=service.repository.create_discovery_run(p,{
                'id':run_id,'project_id':p,
                'base_ontology_id':expected_parent_id,
                'base_ontology_fingerprint':generation_options[
                    'baseline_turtle_sha256'],
                'source_fingerprint':source_fingerprint,
                'normalizer_version':NORMALIZER_VERSION,
                'generator_version':GENERATOR_CONTRACT,
                'runtime_version':runtime_version,
                'attribute_threshold':ATTRIBUTE_THRESHOLD,
                'generation_options':generation_options,
                'materialized_candidate_ids':sorted(processed_ids),
                'request_name':request.name,
                'candidate_snapshot':_canonical_copy(current_candidates),
                'accepted_candidate_ids':[item['id'] for item in induction_candidates],
                'merged_groups':_canonical_copy(normalization.merged_groups),
                'conflicts':_canonical_copy(normalization.conflicts),
                'mappings':_canonical_copy(mappings),
                'candidate_bindings':bindings,
                'initial_candidate_outcomes':candidate_outcomes,
                'candidate_outcomes':candidate_outcomes,
                'diagnostics':_canonical_copy(normalization.diagnostics),
                'status':status,'unified_draft_id':draft_id,
                'supersedes_run_id':predecessor['id'] if predecessor else None,
            })
            if preview is None:
                result_kind=discovery_result_kind(run)
                return {'result_kind':result_kind,'run':run,
                        'discovery_run':run}

            draft={'id':draft_id,'project_id':p,'name':request.name,'status':'draft',
                'revision':1,'generator_backend':'semantica','created_at':utc_now(),
                'turtle':turtle,'summary':summary,
                'review_base_turtle':turtle,
                'parent_ontology_id':expected_parent_id,'diff':diff,
                'quality_warnings':_quality_warnings(candidates),
                'inference':{'metadata':inferred.get('metadata',{}),
                    'validation':inferred.get('validation',{})},
                'ontology_metadata':{'parent_version_id':expected_parent_id,
                    'source_draft_id':draft_id,'diff':diff},
                'unified_draft_id':draft_id,'draft_revision':preview['revision'],
                'discovery_run_id':run_id,'discovery_summary':compact,
                'deprecation':DEPRECATION}
            service.repository.save_artifact('ontology_discovery_draft',draft)
            return {**draft,'candidate_snapshot':run['candidate_snapshot'],
                'candidate_outcomes':run['candidate_outcomes'],
                'mappings':run['mappings'],'review_base_mappings':deepcopy(mappings),
                'result_kind':'draft','run':run,'discovery_run':run,
                'operations':preview['operations']}

    @router.post('/runs/{run_id}/finalize')
    def finalize_run(p: str, run_id: str):
        service.repository.get_project(p)
        stale_error = None
        result = None
        with service.repository._transaction():
            run = service.repository.get_discovery_run(p, run_id)
            if run['status'] == 'finalized_no_change':
                return _finalize_result(run)
            if run['status'] != 'ready_to_finalize':
                raise DiscoveryRunConflict(
                    'discovery run is not ready to finalize',
                    run_id=run_id, expected_status='ready_to_finalize',
                    current_status=run['status'])

            ontologies = service.repository.list_ontologies(p)
            parent = ontologies[-1] if ontologies else None
            parent_id = parent['id'] if parent else None
            if parent_id != run.get('base_ontology_id'):
                service.repository.transition_discovery_run(
                    p, run_id, 'ready_to_finalize', 'stale_base')
                stale_error = StaleBase(
                    'discovery base changed before finalization', details={
                        'base_ontology_id': run.get('base_ontology_id'),
                        'current_ontology_id': parent_id})
            else:
                candidates = _canonical_candidates([
                    item for item in _candidates(service.repository, p)
                    if item.get('kind') in {
                        'entity', 'relation', 'attribute'}])
                existing_records = service.repository.current_records(
                    p, vectors='none', kinds=['entity','relation','attribute'])
                processed_ids = _materialized_candidate_ids(existing_records)
                previous_ids = set(run.get(
                    'materialized_candidate_ids') or [])
                current_fingerprint = _current_finalize_fingerprint(
                    p, run, parent, candidates)
                accepted_ids = set(run.get('accepted_candidate_ids') or [])
                if (current_fingerprint != run['source_fingerprint']
                        or previous_ids != processed_ids & {item['id'] for item in candidates}
                        or accepted_ids & (processed_ids - previous_ids)):
                    service.repository.transition_discovery_run(
                        p, run_id, 'ready_to_finalize', 'stale_source')
                    stale_error = StaleSource(
                        'discovery source changed before finalization', details={
                            'expected_source_fingerprint': run[
                                'source_fingerprint'],
                            'current_source_fingerprint': current_fingerprint,
                        })
                else:
                    draft = {
                        **run,
                        'id': run_id,
                        'excluded_candidate_ids': [],
                    }
                    proposed, skipped = _materialize_candidates(
                        p, draft, parent_id,
                        run.get('candidate_outcomes') or [])
                    proposed = _reuse_materialized_records(
                        proposed, existing_records, previous_ids)
                    accepted, skipped, validation = _validated_materialization(
                        parent['turtle'], proposed, skipped)
                    existing_record_ids = {item['id'] for item in existing_records
                        if _materialized_candidate_ids([item]) & previous_ids}
                    saved = [
                        service.repository._put(p, record, 0)
                        for record in accepted
                        if record['id'] not in existing_record_ids
                    ]
                    materialized_ids = _materialized_candidate_ids(accepted)
                    terminal_outcomes = [{
                        'candidate_id': binding['candidate_id'],
                        'status': (
                            'materialized'
                            if binding['candidate_id'] in materialized_ids
                            else 'skipped'),
                        'reason_code': (
                            'materialized'
                            if binding['candidate_id'] in materialized_ids
                            else 'ontology_validation_failed'),
                    } for binding in run.get('candidate_bindings') or []
                        if binding['candidate_id'] not in {
                            outcome['candidate_id']
                            for outcome in run.get('initial_candidate_outcomes')
                            or []}]
                    transitioned = service.repository.transition_discovery_run(
                        p, run_id, 'ready_to_finalize',
                        'finalized_no_change',
                        candidate_outcomes=terminal_outcomes)
                    result = _finalize_result(
                        transitioned, materialized_count=len(saved),
                        skipped_candidates=skipped, validation=validation)
        if stale_error is not None:
            raise stale_error
        return result

    @router.post('/drafts/{draft_id}/publish', status_code=202)
    def publish(p:str,draft_id:str):
        """Compatibility action: submit the unified draft for human review."""
        service.repository.get_project(p)
        draft=service.repository.get_artifact('ontology_discovery_draft',draft_id)
        if draft.get('project_id')!=p:raise KeyError(draft_id)
        if draft.get('status')!='draft':raise ValueError('版本冲突：该发现草案已经提交，不能重复提交')
        # 提交前乐观锁：父本体已变化时直接拒绝，避免任务跑一半才失败
        ontologies=service.repository.list_ontologies(p)
        latest_id=ontologies[-1]['id'] if ontologies else None
        if latest_id!=draft.get('parent_ontology_id'):
            raise ValueError('版本冲突：本体已更新，请基于当前版本重新生成发现草案')
        current=governed.get(p,draft_id)
        submitted=governed.submit(p,draft_id,current['revision'])
        draft.update(status='submitted',draft_revision=submitted['revision'],
                     submitted_at=utc_now(),deprecation=DEPRECATION)
        service.repository.save_artifact('ontology_discovery_draft',draft)
        return compatibility_payload(submitted,legacy_discovery=draft)

    @router.put('/drafts/{draft_id}')
    def review_draft(p:str,draft_id:str,request:DraftReview):
        draft=service.repository.get_artifact('ontology_discovery_draft',draft_id)
        if draft.get('project_id')!=p:raise KeyError(draft_id)
        if draft.get('status')!='draft':raise ValueError('版本冲突：已发布草案不能编辑')
        run=service.repository.get_discovery_run(p,draft['discovery_run_id'])
        draft={**draft,
            'candidate_snapshot':run['candidate_snapshot'],
            'candidate_outcomes':run['candidate_outcomes'],
            'mappings':run['mappings'],
            'review_base_mappings':deepcopy(run['mappings'])}
        known={item.get('id') for item in draft.get('candidate_snapshot',[])}
        unknown=set(request.excluded_candidate_ids)-known
        if unknown:raise ValueError('审核结果包含未知候选')
        base_turtle=draft.get('review_base_turtle') or draft['turtle']
        base_mappings=deepcopy(draft.get('review_base_mappings') or draft.get('mappings') or {})
        known_terms={source for group in base_mappings.values() for source in group}
        if set(request.excluded_terms)-known_terms or set(request.term_labels)-known_terms:
            raise ValueError('审核结果包含未知本体术语')
        graph=Graph();graph.parse(data=base_turtle,format='turtle');mappings=deepcopy(base_mappings)
        iri_by_source={source:URIRef(iri) for group in base_mappings.values() for source,iri in group.items()}
        excluded_iris={iri_by_source[source] for source in request.excluded_terms}
        label_edits={}
        for source,label in request.term_labels.items():
            iri=iri_by_source[source]
            if iri in excluded_iris:continue
            label=label.strip()
            if not label:raise ValueError('本体术语名称不能为空')
            label_edits[iri]=label
        for iri,label in label_edits.items():
            graph.remove((iri,RDFS.label,None));graph.add((iri,RDFS.label,Literal(label,lang=_literal_language(label))))
        for iri in excluded_iris:
            graph.remove((iri,None,None));graph.remove((None,None,iri))
        for group in mappings.values():
            for source,iri in list(group.items()):
                if URIRef(iri) in excluded_iris:group.pop(source)
        turtle=graph.serialize(format='turtle');summary=Ontology(turtle).summary()
        # Mirror legacy review edits into the authoritative append-only draft.
        current=governed.get(p,draft_id)
        baseline_excluded_iris=set()
        if draft.get('parent_ontology_id') and excluded_iris:
            baseline_graph=Graph();baseline_graph.parse(
                data=service.repository.get_ontology(
                    p,draft['parent_ontology_id'])['turtle'],format='turtle')
            baseline_excluded_iris={
                iri for iri in excluded_iris
                if any(baseline_graph.triples((iri,None,None)))}
        for target_iri in sorted(excluded_iris,key=str):
            iri=str(target_iri)
            targeted=[operation for operation in current['operations']
                      if operation['target_iri']==iri]
            if targeted:
                for operation in reversed(targeted):
                    current=governed.command(p,draft_id,current['revision'],{
                        'action':'withdraw_operation',
                        'operation_id':operation['id'],
                        'reason':'发现审核排除术语'})
            if target_iri in baseline_excluded_iris or not targeted:
                current=governed.command(p,draft_id,current['revision'],{
                    'action':'retire_term','target_iri':iri,
                    'reason':'发现审核停用已发布术语'})
        desired_turtle=turtle
        baseline_exclusion=bool(baseline_excluded_iris)
        if baseline_exclusion:
            adjusted=Graph();adjusted.parse(data=current['turtle'],format='turtle')
            for iri,label in label_edits.items():
                adjusted.remove((iri,RDFS.label,None))
                adjusted.add((iri,RDFS.label,Literal(
                    label,lang=_literal_language(label))))
            desired_turtle=adjusted.serialize(format='turtle')
        current=governed.command(p,draft_id,current['revision'],{
            'action':'diff_turtle','edited_turtle':desired_turtle,
            'reason':'发现审核调整术语选择与标签'})
        existing_effects=(current.get('source_context') or {}).get(
            'publication_effects') or {}
        current=governed.update_publication_effects(
            p,draft_id,current['revision'],existing_effects)
        draft.update(excluded_candidate_ids=request.excluded_candidate_ids,excluded_terms=request.excluded_terms,
            term_labels=request.term_labels,turtle=turtle,summary=summary,mappings=mappings,
            review_base_turtle=base_turtle,review_base_mappings=base_mappings,
            diff=_ontology_diff(None if not draft.get('parent_ontology_id') else service.repository.get_ontology(p,draft['parent_ontology_id'])['turtle'],turtle),
            revision=draft.get('revision',1)+1,draft_revision=current['revision'],
            reviewed_at=utc_now(),deprecation=DEPRECATION)
        response=dict(draft)
        stored={key:value for key,value in draft.items() if key not in {
            'candidate_snapshot','candidate_outcomes','mappings',
            'review_base_mappings'}}
        service.repository.save_artifact('ontology_discovery_draft',stored)
        return response

    app.include_router(router)
