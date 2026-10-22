"""HTTP adapter for the governed ontology discovery workflow."""
from fastapi import APIRouter, Query, Request as HttpRequest
from fastapi.responses import JSONResponse
from pydantic import Field

from .audit_identity import audit_actor, audit_identity
from ..models import Request
from ..services.ontology_discovery_workflow import (
    OntologyDiscoveryWorkflow,
)
from ..services.discovery_vocabulary import (
    GeneratedVocabularyConflict, InvalidBaselineVocabulary,
)
from ..services.ontology_intake import OntologyIntake
from ..repository.discovery_run_store import DiscoveryRunConflict


class SelectionRequest(Request):
    candidate_ids: list[str] = Field(min_length=1, max_length=10000)
    expected_ontology_id: str | None
    target_draft_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    create_new_draft: bool = False
    name: str = Field(default='本轮结构补充', min_length=1, max_length=200)
    confirmed_mappings: dict[str, str] = Field(default_factory=dict)
    infer_hierarchy: bool = False


class ConfirmSelectionRequest(SelectionRequest):
    preview_fingerprint: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    selected_suggestion_fingerprints: list[str] | None = None





def install(app, service):
    router = APIRouter(prefix='/api/projects/{p}/ontology-discovery')
    workflow = OntologyDiscoveryWorkflow(service)
    intake = OntologyIntake(service)

    @router.get('/candidates')
    def candidates(p: str, document_id: str | None = None,
                   offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
        return intake.inventory(p, document_id=document_id, offset=offset, limit=limit)

    @router.post('/selection-preview')
    def selection_preview(p: str, request: SelectionRequest):
        return intake.preview(p, request.model_dump())

    @router.post('/add-selection')
    def add_selection(p: str, request: ConfirmSelectionRequest, http_request: HttpRequest):
        return intake.add(p, request.model_dump(), actor=audit_actor(http_request),
                          actor_identity=audit_identity(http_request)['id'])

    @router.post('/confirm-reuse')
    def confirm_reuse(p: str, request: ConfirmSelectionRequest, http_request: HttpRequest):
        return intake.confirm_reuse(p, request.model_dump(), actor=audit_actor(http_request),
                                    actor_identity=audit_identity(http_request)['id'])

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

    @router.get('')
    def overview(p:str):
        return workflow.overview(p)

    @router.get('/runs')
    def list_runs(p: str):
        return workflow.list_runs(p)

    @router.get('/runs/{run_id}')
    def get_run(p: str, run_id: str):
        return workflow.get_run(p, run_id)

    @router.get('/audit')
    def audit(p: str):
        return workflow.audit(p)

    @router.get('/candidate-mindmap')
    def candidate_mindmap(p:str,limit:int=Query(500, ge=1, le=2000)):
        return workflow.candidate_mindmap(p, limit)





    app.include_router(router)
