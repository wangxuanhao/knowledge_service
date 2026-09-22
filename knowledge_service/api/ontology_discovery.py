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
from ..services.ontology_discovery import (
    _candidates, _candidate_lifecycle, _candidate_mindmap, _induce,
    _materialize_candidates, _validated_materialization, _ontology_diff,
    _quality_warnings, _summary, _literal_language,
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
        service.repository.get_project(p);candidates=_candidates(service.repository,p)
        if not candidates:raise ValueError('尚无开放发现候选，请先用“开放本体发现”模式解析文档')
        ontologies=service.repository.list_ontologies(p)
        parent=ontologies[-1] if ontologies else None
        baseline=parent['turtle'] if parent else None
        turtle,mappings,inferred=_induce(p,request.name,candidates,baseline_turtle=baseline)
        draft_id=str(uuid4());summary=Ontology(turtle).summary();diff=_ontology_diff(baseline,turtle)
        candidate_ids=[x['id'] for x in candidates]
        draft={'id':draft_id,'project_id':p,'name':request.name,'status':'draft','revision':1,
            'generator_backend':'semantica','created_at':utc_now(),'candidate_ids':candidate_ids,
            'candidate_snapshot':candidates,
            'candidate_count':len(candidates),'turtle':turtle,'mappings':mappings,'summary':summary,
            'review_base_turtle':turtle,'review_base_mappings':deepcopy(mappings),
            'parent_ontology_id':parent['id'] if parent else None,'diff':diff,
            'quality_warnings':_quality_warnings(candidates),
            'inference':{'metadata':inferred.get('metadata',{}),'validation':inferred.get('validation',{})},
            'ontology_metadata':{'parent_version_id':parent['id'] if parent else None,
                'source_draft_id':draft_id,'diff':diff,'candidate_ids':candidate_ids}}
        return service.repository.save_artifact('ontology_discovery_draft',draft)

    @router.post('/drafts/{draft_id}/publish', status_code=202)
    def publish(p:str,draft_id:str):
        """提交发布任务：物化→校验→发布本体→写正式图谱，进度上报到后台任务卡。"""
        service.repository.get_project(p)
        draft=service.repository.get_artifact('ontology_discovery_draft',draft_id)
        if draft.get('project_id')!=p:raise KeyError(draft_id)
        if draft.get('status')!='draft':raise ValueError('版本冲突：该发现草案已经发布，不能重复发布')
        # 提交前乐观锁：父本体已变化时直接拒绝，避免任务跑一半才失败
        ontologies=service.repository.list_ontologies(p)
        latest_id=ontologies[-1]['id'] if ontologies else None
        if latest_id!=draft.get('parent_ontology_id'):
            raise ValueError('版本冲突：本体已更新，请基于当前版本重新生成发现草案')
        def run(progress):
            progress('物化候选 → 正式记录雏形（不调 LLM）',5)
            provisional_records,skipped=_materialize_candidates(p,draft,f"draft:{draft_id}")
            progress(f'候选物化完成 · {len(provisional_records)} 条待校验',15)
            records,skipped,validation=_validated_materialization(draft['turtle'],provisional_records,skipped)
            progress(f'本体校验完成 · {len(records)} 条通过 · {len(skipped)} 条跳过',40)
            with service.lock,service.repository._transaction():
                ontology,published=service.repository.publish_ontology_draft(
                    p,draft,draft.get('parent_ontology_id'))
                for record in records:record['ontology_id']=ontology['id']
                saved=service.write(p,records,relation_constraint_mode='strict',
                    defer_milvus_sync=True) if records else []
                progress(f'正式知识写入完成 · {len(saved)} 条',90)
                counts=Counter(row['kind'] for row in saved)
                published.update(mapped_entities=counts['entity'],mapped_relations=counts['relation'],
                    mapped_attributes=len({row['id'] for row in saved if row['kind']=='attribute'}),
                    skipped_candidates=skipped,validation=validation,
                    requires_candidate_review=bool(skipped or published.get('excluded_candidate_ids')),
                    requires_controlled_reingest=False)
                service.repository.save_artifact('ontology_discovery_draft',published)
            service._sync_milvus(p,saved)
            progress('发布完成 · 正式知识已写入',100)
            return published
        return app.state.jobs.submit('ontology_publish', run, p)

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
        draft.update(excluded_candidate_ids=request.excluded_candidate_ids,excluded_terms=request.excluded_terms,
            term_labels=request.term_labels,turtle=turtle,summary=summary,mappings=mappings,
            review_base_turtle=base_turtle,review_base_mappings=base_mappings,
            diff=_ontology_diff(None if not draft.get('parent_ontology_id') else service.repository.get_ontology(p,draft['parent_ontology_id'])['turtle'],turtle),
            revision=draft.get('revision',1)+1,reviewed_at=utc_now())
        return service.repository.save_artifact('ontology_discovery_draft',draft)

    app.include_router(router)
