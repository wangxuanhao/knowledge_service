"""HTTP boundary for the governed ontology workbench.

The handlers intentionally contain no lifecycle decisions.  They validate the
wire contract and delegate to :class:`OntologyDrafts`, which remains the single
owner of draft state transitions and DAG projections.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from ..services.ontology_drafts import OntologyDrafts


class _Request(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DraftCreate(_Request):
    base_ontology_id: str | None = None
    source_kind: str = 'manual'
    title: str
    actor: str
    source_context: dict[str, Any] = Field(default_factory=dict)
    summary: str = ''


class RevisionRequest(_Request):
    expected_revision: int = Field(ge=1)


class CommandRequest(RevisionRequest):
    command: dict[str, Any]


class DecisionsRequest(RevisionRequest):
    expected_ontology_id: str | None
    validation_fingerprint: str
    acknowledged_warning_codes: list[str] = Field(default_factory=list)
    decisions: list[dict[str, Any]]
    actor: str


class RebaseRequest(RevisionRequest):
    expected_ontology_id: str | None


class CloseRequest(RevisionRequest):
    actor: str
    reason: str


class PublishRequest(RevisionRequest):
    expected_ontology_id: str | None
    validation_fingerprint: str
    acknowledged_warning_codes: list[str] = Field(default_factory=list)
    idempotency_key: str
    actor: str


def install(app, service):
    drafts = OntologyDrafts(service.repository, publisher=service.repository)
    app.state.ontology_drafts = drafts

    router = APIRouter(prefix='/api/projects/{p}/ontology-drafts')

    @router.post('', status_code=201)
    def create(p: str, request: DraftCreate):
        return drafts.create(
            p, request.base_ontology_id, request.source_kind,
            request.title, request.actor,
            source_context=request.source_context, summary=request.summary)

    @router.get('')
    def listing(p: str, status: str | None = None):
        items = drafts.list(p, status=status)
        return {'items': items, 'total': len(items)}

    @router.get('/{draft_id}')
    def get(p: str, draft_id: str):
        return drafts.get(p, draft_id)

    @router.post('/{draft_id}/commands')
    def command(p: str, draft_id: str, request: CommandRequest):
        return drafts.command(
            p, draft_id, request.expected_revision, request.command)

    @router.post('/{draft_id}/validate')
    def validate(p: str, draft_id: str, request: RevisionRequest):
        return drafts.validate(p, draft_id, request.expected_revision)

    @router.post('/{draft_id}/submit')
    def submit(p: str, draft_id: str, request: RevisionRequest):
        return drafts.submit(p, draft_id, request.expected_revision)

    @router.post('/{draft_id}/decisions')
    def decisions(p: str, draft_id: str, request: DecisionsRequest):
        return drafts.decide(
            p, draft_id, request.expected_revision,
            request.expected_ontology_id, request.validation_fingerprint,
            request.decisions, request.acknowledged_warning_codes,
            request.actor)

    @router.post('/{draft_id}/rebase')
    def rebase(p: str, draft_id: str, request: RebaseRequest):
        return drafts.rebase(
            p, draft_id, request.expected_revision,
            request.expected_ontology_id)

    @router.post('/{draft_id}/close')
    def close(p: str, draft_id: str, request: CloseRequest):
        return drafts.close(
            p, draft_id, request.expected_revision,
            request.actor, request.reason)

    @router.post('/{draft_id}/publish')
    def publish(p: str, draft_id: str, request: PublishRequest):
        return drafts.publish(
            p, draft_id, request.expected_revision,
            request.expected_ontology_id, request.validation_fingerprint,
            request.acknowledged_warning_codes, request.idempotency_key,
            request.actor)

    app.include_router(router)

    hierarchy = APIRouter(prefix='/api/projects/{p}/ontology-hierarchy')

    @hierarchy.get('/roots')
    def roots(p: str, ontology_id: str | None = None,
              draft_id: str | None = None, cursor: str | None = None,
              limit: int = Query(50, ge=1, le=200)):
        return drafts.roots(
            p, ontology_id=ontology_id, draft_id=draft_id,
            cursor=cursor, limit=limit)

    @hierarchy.get('/search')
    def search(p: str, q: str, ontology_id: str | None = None,
               draft_id: str | None = None, cursor: str | None = None,
               limit: int = Query(50, ge=1, le=200)):
        return drafts.search(
            p, q, ontology_id=ontology_id, draft_id=draft_id,
            cursor=cursor, limit=limit)

    @hierarchy.get('/children')
    def children(p: str, iri: str, ontology_id: str | None = None,
                 draft_id: str | None = None, cursor: str | None = None,
                 limit: int = Query(50, ge=1, le=200)):
        return drafts.children(
            p, iri, ontology_id=ontology_id, draft_id=draft_id,
            cursor=cursor, limit=limit)

    @hierarchy.get('/neighborhood')
    def neighborhood(p: str, iri: str, ontology_id: str | None = None,
                     draft_id: str | None = None, cursor: str | None = None,
                     limit: int = Query(50, ge=1, le=200)):
        return drafts.neighborhood(
            p, iri, ontology_id=ontology_id, draft_id=draft_id,
            cursor=cursor, limit=limit)

    app.include_router(hierarchy)

    matrix = APIRouter(prefix='/api/projects/{p}')

    @matrix.get('/ontology-matrix')
    def ontology_matrix(p: str, ontology_id: str | None = None,
                        draft_id: str | None = None,
                        cursor: str | None = None,
                        limit: int = Query(50, ge=1, le=200)):
        return drafts.matrix(
            p, ontology_id=ontology_id, draft_id=draft_id,
            cursor=cursor, limit=limit)

    app.include_router(matrix)


__all__ = ['install']
