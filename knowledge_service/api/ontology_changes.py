"""与抽取知识候选关联的受治理本体变更（路由层）。

业务逻辑与请求模型在 services.ontology_changes；本文件只负责挂载路由
/api/projects/{p}/ontology-change-proposals 并调用业务函数。
"""
import copy
from uuid import uuid4

from fastapi import APIRouter

from ..core.time import utc_now
from ..services.ontology import Ontology, generated_term_iri, absolute_iri
from ..services.ontology_adapters import DEPRECATION
from ..services.ontology_drafts import OntologyDrafts
from ..services.ontology_changes import (
    ProposalCreate, ProposalDecision, apply, impact, revalidation,
    document_and_candidate,
)


def install(app, service):
    router = APIRouter(prefix='/api/projects/{p}/ontology-change-proposals')
    governed = OntologyDrafts(service.repository, publisher=service.repository)

    @router.get('')
    def listing(p: str):
        service.repository.get_project(p)
        return {'proposals': service.repository.list_artifacts('ontology_change', p)}

    @router.post('', status_code=201)
    def create(p: str, request: ProposalCreate):
        with service.lock:
            latest = service.repository.get_ontology(p)
            if latest['id'] != request.expected_ontology_id:
                raise ValueError('版本冲突：本体已更新，请刷新后再创建草案')
            document, candidate = document_and_candidate(
                service, p, request.document_id, request.candidate_id)
            if document['version'] != request.expected_document_version:
                raise ValueError('版本冲突：审核候选已更新，请刷新')
            expected = {'entity': 'class', 'relation': 'relation', 'attribute': 'attribute'}.get(
                candidate.get('kind', 'relation'))
            if request.kind != expected:
                raise ValueError(f'该知识候选只能申请 {expected} 类型的本体变更')
            uri = request.uri.strip()
            if request.operation == 'add' and not uri:
                uri = generated_term_iri(p, request.label_zh or request.label)
            if not absolute_iri(uri):
                message = '调整现有定义时必须选择有效的本体术语' if request.operation == 'update' else '无法生成有效的本体 IRI'
                raise ValueError(message)
            proposal = {**request.model_dump(), 'uri': uri, 'id': str(uuid4()), 'project_id': p,
                        'status': 'pending', 'revision': 1,
                        'created_at': utc_now(), 'updated_at': utc_now()}
            if any(item.get('status') == 'pending' and item.get('document_id') == request.document_id and
                   item.get('candidate_id') == request.candidate_id
                   for item in service.repository.list_artifacts('ontology_change', p)):
                raise ValueError('该知识候选已有待处理的本体变更草案')
            ontology = Ontology(latest['turtle'])
            turtle, _ = apply(ontology, proposal)
            proposal['impact'] = impact(service, p, proposal, ontology, candidate)
            candidate_revalidation = revalidation(
                service, p, document, candidate, Ontology(turtle),
                '__PUBLISHED_ONTOLOGY_ID__', proposal)
            created = governed.create(
                p, latest['id'], 'candidate',
                f"候选本体变更：{proposal['label_zh'] or proposal['label']}",
                'api:ontology-change',
                source_context={
                    'legacy_route': 'POST /ontology-change-proposals',
                    'documents': [{
                        'document_id': document['id'],
                        'expected_document_version': document['version'],
                        'expected_document_version_id': document['version_id'],
                        'candidate_id': candidate['id'],
                        'expected_candidate_status': candidate.get('status'),
                    }],
                    'proposal': proposal,
                    'publication_effects': {
                        'kind': 'candidate',
                        'document_id': document['id'],
                        'candidate_id': candidate['id'],
                        'expected_document_version': document['version'],
                        'proposal_id': proposal['id'],
                        'revalidation': candidate_revalidation,
                    },
                },
                summary=proposal['rationale'])
            preview = governed.command(p, created['id'], created['revision'], {
                'action': 'diff_turtle', 'edited_turtle': turtle,
                'reason': proposal['rationale'],
            })
            proposal.update(
                draft_id=created['id'], draft_revision=preview['revision'],
                deprecation=DEPRECATION)
            service.repository.save_artifact('ontology_change', proposal)
            return proposal

    @router.get('/{proposal_id}')
    def get(p: str, proposal_id: str):
        proposal = service.repository.get_artifact('ontology_change', proposal_id)
        if proposal.get('project_id') != p:
            raise KeyError(proposal_id)
        return proposal

    @router.post('/{proposal_id}/decision')
    def decision(p: str, proposal_id: str, request: ProposalDecision):
        with service.lock:
            proposal = service.repository.get_artifact('ontology_change', proposal_id)
            if proposal.get('project_id') != p:
                raise KeyError(proposal_id)
            if proposal['status'] != 'pending':
                raise ValueError('该本体变更草案已经处理')
            if proposal['revision'] != request.expected_revision:
                raise ValueError('版本冲突：草案已更新，请刷新')
            proposal.update(decision_note=request.note, decided_at=utc_now(), updated_at=utc_now(),
                            revision=proposal['revision'] + 1)
            if request.action == 'reject':
                proposal['status'] = 'rejected'
                current = governed.get(p, proposal['draft_id'])
                closed = governed.close(
                    p, proposal['draft_id'], current['revision'],
                    'api:ontology-change', request.note)
                proposal['draft_revision'] = closed['revision']
                service.repository.save_artifact('ontology_change', proposal)
                return {'proposal': proposal, 'ontology': None, 'draft': closed,
                        'deprecation': DEPRECATION}
            latest = service.repository.get_ontology(p)
            if latest['id'] != request.expected_ontology_id or latest['id'] != proposal['expected_ontology_id']:
                raise ValueError('版本冲突：本体已更新，请重新评估草案影响')
            if proposal.get('impact', {}).get('risk') == 'high' and not request.confirm_impact:
                raise ValueError('高影响本体变更必须明确确认影响范围')
            current = governed.get(p, proposal['draft_id'])
            submitted = governed.submit(
                p, proposal['draft_id'], current['revision'])
            proposal.update(status='submitted', draft_revision=submitted['revision'],
                            submitted_at=utc_now(), deprecation=DEPRECATION)
            service.repository.save_artifact('ontology_change', proposal)
            return {'proposal': proposal, 'ontology': None, 'draft': submitted,
                    'revalidation': None, 'deprecation': DEPRECATION}

    app.include_router(router)
