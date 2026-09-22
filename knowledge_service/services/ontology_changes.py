"""与抽取知识候选关联的受治理本体变更（业务逻辑）。

路由层在 api.ontology_changes；本文件只含本体变更的业务函数与请求模型。
"""
from __future__ import annotations

import copy
from typing import Literal
from uuid import uuid4

from pydantic import Field
from rdflib import RDF, RDFS, URIRef, Literal as RDFLiteral
from rdflib.namespace import OWL

from ..models import Request
from ..services.ontology import (
    Ontology, generated_term_iri, local_name,
    absolute_iri, set_term_constraints, term_kind, term_impact,
)
from ..core.time import utc_now

KIND_FOR_CANDIDATE = {'entity': 'class', 'relation': 'relation', 'attribute': 'attribute'}


class ProposalCreate(Request):
    document_id: str
    candidate_id: str
    operation: Literal['add', 'update'] = 'add'
    kind: Literal['class', 'relation', 'attribute']
    uri: str = Field(default='', max_length=500)
    label: str = Field(min_length=1, max_length=200)
    label_zh: str = Field(default='', max_length=200)
    description: str = Field(default='', max_length=2000)
    parent: str = ''
    domain: str = ''
    range: str = ''
    rationale: str = Field(min_length=1, max_length=2000)
    expected_ontology_id: str
    expected_document_version: int = Field(ge=1)


class ProposalDecision(Request):
    action: Literal['approve', 'reject']
    note: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=1)
    expected_ontology_id: str
    confirm_impact: bool = False


def document_and_candidate(service, p, document_id, candidate_id):
    """定位来源文档与待审核候选；候选非 pending 时拒绝发起变更。"""
    document = next((r for r in service.repository.current_records(p)
                     if r['id'] == document_id and r['kind'] == 'document'), None)
    if not document:
        raise KeyError(document_id)
    candidate = next((c for c in document.get('metadata', {}).get('review_candidates', [])
                      if c.get('id') == candidate_id), None)
    if not candidate:
        raise KeyError(candidate_id)
    if candidate.get('status') != 'pending':
        raise ValueError('只有待审核知识才能发起本体变更')
    return document, candidate


def apply(ontology, proposal):
    """把提案应用到本体图：新增/调整术语、标签、说明与约束，返回新 Turtle。"""
    node = URIRef(proposal['uri']); kind = proposal['kind']; existing = term_kind(ontology, node)
    if proposal['operation'] == 'add':
        if existing:
            raise ValueError('本体术语已存在；请改为调整现有定义')
        ontology.graph.add((node, RDF.type, {'class': OWL.Class, 'relation': OWL.ObjectProperty,
                                             'attribute': OWL.DatatypeProperty}[kind]))
    elif existing != kind:
        raise ValueError('要调整的本体术语不存在或类型不一致')
    ontology.graph.remove((node, RDFS.label, None))
    if proposal.get('label'):
        ontology.graph.add((node, RDFS.label, RDFLiteral(proposal['label'])))
    if proposal.get('label_zh'):
        ontology.graph.add((node, RDFS.label, RDFLiteral(proposal['label_zh'], lang='zh')))
    ontology.graph.remove((node, RDFS.comment, None))
    if proposal.get('description'):
        ontology.graph.add((node, RDFS.comment, RDFLiteral(proposal['description'])))
    set_term_constraints(ontology, node, kind, proposal.get('parent', ''),
                          proposal.get('domain', ''), proposal.get('range', ''))
    turtle = ontology.graph.serialize(format='turtle')
    return turtle, Ontology(turtle)


def impact(service, p, proposal, ontology, candidate):
    """评估提案影响：被引用记录/约束数量 + 关联待审核候选 + 风险等级。"""
    existing = term_kind(ontology, URIRef(proposal['uri']))
    base_impact = term_impact(service, p, proposal['uri'], ontology) if existing else {
        'record_count': 0, 'record_ids': [], 'record_ids_truncated': False, 'constraint_count': 0,
        'constraints': [], 'pending_review_count': 0, 'pending_review_ids': []}
    rows = service.repository.current_records(p)
    needle = {proposal['uri'], local_name(proposal['uri']),
              candidate.get('proposed_type'), candidate.get('predicate')}
    linked = []
    for doc in rows:
        for item in doc.get('metadata', {}).get('review_candidates', []):
            if item.get('status') == 'pending' and needle & {item.get('proposed_type'), item.get('predicate')}:
                linked.append({'document_id': doc['id'], 'candidate_id': item['id']})
    return {**base_impact, 'linked_candidates': len(linked), 'linked_candidate_refs': linked[:100],
            'risk': 'high' if proposal['operation'] == 'update' and (base_impact['record_count'] or base_impact['constraint_count']) else 'controlled'}


def revalidation(service, p, document, candidate, ontology, ontology_id, proposal):
    """发布后重新校验关联候选：关系端点/属性值是否满足新本体约束。"""
    status = 'ready_for_review'; issues = []
    rows = {r['id']: r for r in service.repository.current_records(p)}
    try:
        target = str(ontology.resolve(proposal['uri'], {'class': ontology.classes,
                                                        'relation': ontology.relations,
                                                        'attribute': ontology.attributes}[proposal['kind']]))
        if candidate.get('kind') == 'relation':
            subject = rows.get(candidate.get('subject_id')); obj = rows.get(candidate.get('object_id'))
            if not subject or not obj:
                status = 'waiting_for_entities'; issues = ['请先批准关系两端的实体候选']
            else:
                issues = ontology.relation_constraint_issues(target, subject.get('type', ''), obj.get('type', ''))
                if issues:
                    status = 'constraint_conflict'
        elif candidate.get('kind') == 'attribute':
            entity = rows.get(candidate.get('entity_id'))
            if not entity:
                status = 'waiting_for_entities'; issues = ['请先批准属性所属实体候选']
            else:
                from ..services.review_validation import validate_attribute
                validate_attribute(ontology, entity.get('type', ''), target, candidate.get('value'))
    except ValueError as exc:
        status = 'needs_revision'; issues = [str(exc)]
    return {'proposal_id': proposal['id'], 'status': status, 'ontology_id': ontology_id,
            'target_type': proposal['uri'], 'checked_at': utc_now(), 'issues': issues}
