"""开放本体发现的路由层（install 挂载 /api/projects/{p}/ontology-discovery）。

业务逻辑（候选聚合、归纳、物化、生命周期）在 services.ontology_discovery；
本文件只负责把 HTTP 请求转成对业务函数和 repository 的调用。
"""
from collections import Counter
from copy import deepcopy
from uuid import uuid4

from fastapi import APIRouter
from pydantic import Field
from rdflib import Graph, Literal, RDFS, URIRef

from ..models import Request
from ..services.ontology import Ontology
from ..services.ontology_adapters import (
    DEPRECATION,
    compatibility_payload,
)
from ..services.ontology_drafts import OntologyDrafts
from ..services.ontology_discovery import (
    _candidates, _candidate_lifecycle, _candidate_mindmap, _induce,
    _materialize_candidates, _validated_materialization, _ontology_diff,
    _normalize_induction_candidates, _quality_warnings, _summary, _literal_language,
)
from ..utils.diagnostics import timed
from ..core.time import utc_now


class DraftRequest(Request):
    name: str = Field(default='发现本体', min_length=1, max_length=200)


class DraftReview(Request):
    excluded_candidate_ids: list[str] = Field(default_factory=list, max_length=10000)
    excluded_terms: list[str] = Field(default_factory=list, max_length=1000)
    term_labels: dict[str, str] = Field(default_factory=dict)


def install(app, service):
    router = APIRouter(prefix='/api/projects/{p}/ontology-discovery')
    governed = OntologyDrafts(service.repository, publisher=service.repository)

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
        drafts=service.repository.list_artifacts('ontology_discovery_draft',p)
        ontologies=service.repository.list_ontologies(p)
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
            'unpublished_candidate_count':status_counts['pending']+status_counts['included_in_draft'],
            'requires_candidate_review':bool(status_counts['pending']+status_counts['included_in_draft']+status_counts['approved']),
            'requires_controlled_reingest':False}

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
            drafts=service.repository.list_artifacts('ontology_discovery_draft',p)
            states,_=_candidate_lifecycle(service.repository,p,candidates,drafts,records)
            payload=_candidate_mindmap(candidates,states,limit)
            record['nodes']=len(payload.get('nodes') or [])
            record['edges']=len(payload.get('edges') or [])
            return payload

    @router.post('/drafts',status_code=201)
    def create_draft(p:str,request:DraftRequest):
        service.repository.get_project(p)
        candidates=[item for item in _candidates(service.repository,p)
            if item.get('kind') in {'entity','relation','attribute'}]
        if not candidates:raise ValueError('尚无开放发现候选，请先用“开放本体发现”模式解析文档')
        ontologies=service.repository.list_ontologies(p)
        parent=ontologies[-1] if ontologies else None
        baseline=parent['turtle'] if parent else None
        induction_candidates,normalization=_normalize_induction_candidates(candidates,baseline)
        turtle,mappings,inferred=_induce(
            p,request.name,induction_candidates,baseline_turtle=baseline)
        summary=Ontology(turtle).summary();diff=_ontology_diff(baseline,turtle)
        candidate_ids=[x['id'] for x in candidates]
        effect_draft={
            'id':'__DRAFT_ID__','candidate_snapshot':candidates,
            'excluded_candidate_ids':[],'mappings':mappings,
        }
        provisional,initial_skipped=_materialize_candidates(
            p,effect_draft,'__PUBLISHED_ONTOLOGY_ID__',normalization.conflicts)
        context=source_context(candidates)
        context['publication_effects']={
            'kind':'discovery','records':provisional,
            'skipped_candidates':initial_skipped,
        }
        created=governed.create(
            p,parent['id'] if parent else None,'discovery',request.name,
            'api:ontology-discovery',source_context=context,
            summary='开放发现候选归纳')
        preview=governed.command(p,created['id'],created['revision'],{
            'action':'diff_turtle','edited_turtle':turtle,
            'reason':'Semantica 0.6.7 开放发现建议'})
        draft_id=created['id']
        draft={'id':draft_id,'project_id':p,'name':request.name,'status':'draft','revision':1,
            'generator_backend':'semantica','created_at':utc_now(),'candidate_ids':candidate_ids,
            'candidate_snapshot':candidates,
            'candidate_outcomes':[dict(item) for item in normalization.conflicts],
            'candidate_count':len(candidates),'turtle':turtle,'mappings':mappings,'summary':summary,
            'review_base_turtle':turtle,'review_base_mappings':deepcopy(mappings),
            'parent_ontology_id':parent['id'] if parent else None,'diff':diff,
            'quality_warnings':_quality_warnings(candidates),
            'inference':{'metadata':inferred.get('metadata',{}),'validation':inferred.get('validation',{})},
            'ontology_metadata':{'parent_version_id':parent['id'] if parent else None,
                'source_draft_id':draft_id,'diff':diff,'candidate_ids':candidate_ids},
            'unified_draft_id':draft_id,'draft_revision':preview['revision'],
            'deprecation':DEPRECATION}
        service.repository.save_artifact('ontology_discovery_draft',draft)
        return {**draft,'operations':preview['operations']}

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
        for source,label in request.term_labels.items():
            label=label.strip()
            if not label:raise ValueError('本体术语名称不能为空')
            iri=iri_by_source[source];graph.remove((iri,RDFS.label,None));graph.add((iri,RDFS.label,Literal(label,lang=_literal_language(label))))
        for source in request.excluded_terms:
            iri=iri_by_source[source];graph.remove((iri,None,None));graph.remove((None,None,iri))
            for group in mappings.values():group.pop(source,None)
        turtle=graph.serialize(format='turtle');summary=Ontology(turtle).summary()
        # Mirror legacy review edits into the authoritative append-only draft.
        current=governed.get(p,draft_id)
        for source in request.excluded_terms:
            iri=str(iri_by_source[source])
            targeted=[operation for operation in current['operations']
                      if operation['target_iri']==iri]
            if targeted:
                for operation in reversed(targeted):
                    current=governed.command(p,draft_id,current['revision'],{
                        'action':'withdraw_operation',
                        'operation_id':operation['id'],
                        'reason':'发现审核排除术语'})
            else:
                current=governed.command(p,draft_id,current['revision'],{
                    'action':'retire_term','target_iri':iri,
                    'reason':'发现审核停用已发布术语'})
        desired_turtle=turtle
        baseline_exclusion=False
        if draft.get('parent_ontology_id') and request.excluded_terms:
            baseline_graph=Graph();baseline_graph.parse(
                data=service.repository.get_ontology(
                    p,draft['parent_ontology_id'])['turtle'],format='turtle')
            baseline_exclusion=any(
                any(baseline_graph.triples((iri_by_source[source],None,None)))
                for source in request.excluded_terms)
        if baseline_exclusion:
            adjusted=Graph();adjusted.parse(data=current['turtle'],format='turtle')
            for source,label in request.term_labels.items():
                if source in request.excluded_terms:
                    continue
                iri=iri_by_source[source]
                adjusted.remove((iri,RDFS.label,None))
                adjusted.add((iri,RDFS.label,Literal(
                    label.strip(),lang=_literal_language(label))))
            desired_turtle=adjusted.serialize(format='turtle')
        current=governed.command(p,draft_id,current['revision'],{
            'action':'diff_turtle','edited_turtle':desired_turtle,
            'reason':'发现审核调整术语选择与标签'})
        effect_draft={**draft,'id':draft_id,
            'excluded_candidate_ids':request.excluded_candidate_ids,
            'mappings':mappings}
        provisional,initial_skipped=_materialize_candidates(
            p,effect_draft,'__PUBLISHED_ONTOLOGY_ID__',draft.get('candidate_outcomes'))
        current=governed.update_publication_effects(
            p,draft_id,current['revision'],{
                'kind':'discovery','records':provisional,
                'skipped_candidates':initial_skipped,
            })
        draft.update(excluded_candidate_ids=request.excluded_candidate_ids,excluded_terms=request.excluded_terms,
            term_labels=request.term_labels,turtle=turtle,summary=summary,mappings=mappings,
            review_base_turtle=base_turtle,review_base_mappings=base_mappings,
            diff=_ontology_diff(None if not draft.get('parent_ontology_id') else service.repository.get_ontology(p,draft['parent_ontology_id'])['turtle'],turtle),
            revision=draft.get('revision',1)+1,draft_revision=current['revision'],
            reviewed_at=utc_now(),deprecation=DEPRECATION)
        return service.repository.save_artifact('ontology_discovery_draft',draft)

    app.include_router(router)
