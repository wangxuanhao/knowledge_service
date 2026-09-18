"""与抽取知识候选关联的受治理本体变更。"""
from __future__ import annotations

import copy
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter
from pydantic import Field
from rdflib import RDF, RDFS, URIRef, Literal as RDFLiteral
from rdflib.namespace import OWL

from .models import Request
from .ontology import Ontology, generated_term_iri, local_name
from .time import utc_now
from .workspace_api import _absolute_iri, _set_term_constraints, _term_kind, _term_impact


KIND_FOR_CANDIDATE={'entity':'class','relation':'relation','attribute':'attribute'}


class ProposalCreate(Request):
    document_id:str
    candidate_id:str
    operation:Literal['add','update']='add'
    kind:Literal['class','relation','attribute']
    uri:str=Field(default='',max_length=500)
    label:str=Field(min_length=1,max_length=200)
    label_zh:str=Field(default='',max_length=200)
    description:str=Field(default='',max_length=2000)
    parent:str=''
    domain:str=''
    range:str=''
    rationale:str=Field(min_length=1,max_length=2000)
    expected_ontology_id:str
    expected_document_version:int=Field(ge=1)


class ProposalDecision(Request):
    action:Literal['approve','reject']
    note:str=Field(min_length=1,max_length=2000)
    expected_revision:int=Field(ge=1)
    expected_ontology_id:str
    confirm_impact:bool=False


def _document_and_candidate(service,p,document_id,candidate_id):
    document=next((r for r in service.repository.current_records(p) if r['id']==document_id and r['kind']=='document'),None)
    if not document:raise KeyError(document_id)
    candidate=next((c for c in document.get('metadata',{}).get('review_candidates',[]) if c.get('id')==candidate_id),None)
    if not candidate:raise KeyError(candidate_id)
    if candidate.get('status')!='pending':raise ValueError('只有待审核知识才能发起本体变更')
    return document,candidate


def _apply(ontology,proposal):
    node=URIRef(proposal['uri']);kind=proposal['kind'];existing=_term_kind(ontology,node)
    if proposal['operation']=='add':
        if existing:raise ValueError('本体术语已存在；请改为调整现有定义')
        ontology.graph.add((node,RDF.type,{'class':OWL.Class,'relation':OWL.ObjectProperty,
                                          'attribute':OWL.DatatypeProperty}[kind]))
    elif existing!=kind:
        raise ValueError('要调整的本体术语不存在或类型不一致')
    ontology.graph.remove((node,RDFS.label,None))
    if proposal.get('label'):ontology.graph.add((node,RDFS.label,RDFLiteral(proposal['label'])))
    if proposal.get('label_zh'):ontology.graph.add((node,RDFS.label,RDFLiteral(proposal['label_zh'],lang='zh')))
    ontology.graph.remove((node,RDFS.comment,None))
    if proposal.get('description'):ontology.graph.add((node,RDFS.comment,RDFLiteral(proposal['description'])))
    _set_term_constraints(ontology,node,kind,proposal.get('parent',''),proposal.get('domain',''),proposal.get('range',''))
    turtle=ontology.graph.serialize(format='turtle')
    return turtle,Ontology(turtle)


def _impact(service,p,proposal,ontology,candidate):
    existing=_term_kind(ontology,URIRef(proposal['uri']))
    term_impact=_term_impact(service,p,proposal['uri'],ontology) if existing else {
        'record_count':0,'record_ids':[],'record_ids_truncated':False,'constraint_count':0,
        'constraints':[],'pending_review_count':0,'pending_review_ids':[]}
    rows=service.repository.current_records(p);needle={proposal['uri'],local_name(proposal['uri']),
        candidate.get('proposed_type'),candidate.get('predicate')}
    linked=[]
    for doc in rows:
        for item in doc.get('metadata',{}).get('review_candidates',[]):
            if item.get('status')=='pending' and needle & {item.get('proposed_type'),item.get('predicate')}:
                linked.append({'document_id':doc['id'],'candidate_id':item['id']})
    return {**term_impact,'linked_candidates':len(linked),'linked_candidate_refs':linked[:100],
            'risk':'high' if proposal['operation']=='update' and (term_impact['record_count'] or term_impact['constraint_count']) else 'controlled'}


def _revalidation(service,p,document,candidate,ontology,ontology_id,proposal):
    status='ready_for_review';issues=[];rows={r['id']:r for r in service.repository.current_records(p)}
    try:
        target=str(ontology.resolve(proposal['uri'],{'class':ontology.classes,'relation':ontology.relations,
                                                     'attribute':ontology.attributes}[proposal['kind']]))
        if candidate.get('kind')=='relation':
            subject=rows.get(candidate.get('subject_id'));obj=rows.get(candidate.get('object_id'))
            if not subject or not obj:
                status='waiting_for_entities';issues=['请先批准关系两端的实体候选']
            else:
                issues=ontology.relation_constraint_issues(target,subject.get('type',''),obj.get('type',''))
                if issues:status='constraint_conflict'
        elif candidate.get('kind')=='attribute':
            entity=rows.get(candidate.get('entity_id'))
            if not entity:
                status='waiting_for_entities';issues=['请先批准属性所属实体候选']
            else:
                from .review_validation import validate_attribute
                validate_attribute(ontology,entity.get('type',''),target,candidate.get('value'))
    except ValueError as exc:
        status='needs_revision';issues=[str(exc)]
    return {'proposal_id':proposal['id'],'status':status,'ontology_id':ontology_id,
            'target_type':proposal['uri'],'checked_at':utc_now(),'issues':issues}


def install(app,service):
    router=APIRouter(prefix='/api/projects/{p}/ontology-change-proposals')

    @router.get('')
    def listing(p:str):
        service.repository.get_project(p)
        return {'proposals':service.repository.list_artifacts('ontology_change',p)}

    @router.post('',status_code=201)
    def create(p:str,request:ProposalCreate):
        with service.lock:
            latest=service.repository.get_ontology(p)
            if latest['id']!=request.expected_ontology_id:raise ValueError('版本冲突：本体已更新，请刷新后再创建草案')
            document,candidate=_document_and_candidate(service,p,request.document_id,request.candidate_id)
            if document['version']!=request.expected_document_version:raise ValueError('版本冲突：审核候选已更新，请刷新')
            expected=KIND_FOR_CANDIDATE.get(candidate.get('kind','relation'))
            if request.kind!=expected:raise ValueError(f'该知识候选只能申请 {expected} 类型的本体变更')
            uri=request.uri.strip()
            if request.operation=='add' and not uri:
                uri=generated_term_iri(p,request.label_zh or request.label)
            if not _absolute_iri(uri):
                message='调整现有定义时必须选择有效的本体术语' if request.operation=='update' else '无法生成有效的本体 IRI'
                raise ValueError(message)
            proposal={**request.model_dump(),'uri':uri,'id':str(uuid4()),'project_id':p,'status':'pending','revision':1,
                'created_at':utc_now(),'updated_at':utc_now()}
            if any(item.get('status')=='pending' and item.get('document_id')==request.document_id and
                   item.get('candidate_id')==request.candidate_id
                   for item in service.repository.list_artifacts('ontology_change',p)):
                raise ValueError('该知识候选已有待处理的本体变更草案')
            ontology=Ontology(latest['turtle']);_apply(ontology,proposal)
            proposal['impact']=_impact(service,p,proposal,ontology,candidate)
            service.repository.save_artifact('ontology_change',proposal)
            return proposal

    @router.get('/{proposal_id}')
    def get(p:str,proposal_id:str):
        proposal=service.repository.get_artifact('ontology_change',proposal_id)
        if proposal.get('project_id')!=p:raise KeyError(proposal_id)
        return proposal

    @router.post('/{proposal_id}/decision')
    def decision(p:str,proposal_id:str,request:ProposalDecision):
        with service.lock:
            proposal=service.repository.get_artifact('ontology_change',proposal_id)
            if proposal.get('project_id')!=p:raise KeyError(proposal_id)
            if proposal['status']!='pending':raise ValueError('该本体变更草案已经处理')
            if proposal['revision']!=request.expected_revision:raise ValueError('版本冲突：草案已更新，请刷新')
            proposal.update(decision_note=request.note,decided_at=utc_now(),updated_at=utc_now(),revision=proposal['revision']+1)
            if request.action=='reject':
                proposal['status']='rejected';service.repository.save_artifact('ontology_change',proposal)
                return {'proposal':proposal,'ontology':None}
            latest=service.repository.get_ontology(p)
            if latest['id']!=request.expected_ontology_id or latest['id']!=proposal['expected_ontology_id']:
                raise ValueError('版本冲突：本体已更新，请重新评估草案影响')
            if proposal.get('impact',{}).get('risk')=='high' and not request.confirm_impact:
                raise ValueError('高影响本体变更必须明确确认影响范围')
            document,candidate=_document_and_candidate(service,p,proposal['document_id'],proposal['candidate_id'])
            turtle,ontology=_apply(Ontology(latest['turtle']),proposal)
            saved={'id':str(uuid4()),'project_id':p,'turtle':turtle,'summary':ontology.summary(),'created_at':utc_now()}
            revised=copy.deepcopy(document);revised={k:v for k,v in revised.items() if k not in ('project_id','version','version_id','recorded_at','superseded_at')}
            revised_candidate=next(c for c in revised['metadata']['review_candidates'] if c['id']==proposal['candidate_id'])
            revised_candidate['ontology_change']=_revalidation(service,p,document,candidate,ontology,saved['id'],proposal)
            proposal.update(status='approved',approved_ontology_id=saved['id'],revalidation=revised_candidate['ontology_change'])
            service.repository.commit_ontology_change(p,saved,proposal,revised,document['version'],latest['id'])
            return {'proposal':proposal,'ontology':saved,'revalidation':revised_candidate['ontology_change']}

    app.include_router(router)
