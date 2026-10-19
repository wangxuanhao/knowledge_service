"""HTTP adapter for the governed ontology discovery workflow."""
from fastapi import APIRouter, Request as HttpRequest
from fastapi.responses import JSONResponse
from pydantic import Field

from .audit_identity import audit_actor
from ..models import Request
from ..services.ontology_discovery_workflow import (
    OntologyDiscoveryWorkflow, DiscoveryRunConflict,
)
from ..services.discovery_vocabulary import (
    GeneratedVocabularyConflict, InvalidBaselineVocabulary,
)

class DraftRequest(Request):
    name: str = Field(default='发现本体', min_length=1, max_length=200)
    # 是否让模型额外推断「谁是谁的父类」。省略时保持旧 API 的推断行为；
    # 建类型本身不需要模型（候选类型 → owl:Class/ObjectProperty 是确定性操作，秒级完成）；
    # 新前端会显式传 false 走快路径，需要自动层级的调用方也可显式传 true。
    infer_hierarchy: bool = True


class DraftReview(Request):
    excluded_candidate_ids: list[str] = Field(default_factory=list, max_length=10000)
    excluded_terms: list[str] = Field(default_factory=list, max_length=1000)
    term_labels: dict[str, str] = Field(default_factory=dict)


def install(app, service):
    router = APIRouter(prefix='/api/projects/{p}/ontology-discovery')
    governed = service.ontology_drafts(publish=True)
    workflow = OntologyDiscoveryWorkflow(service)

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
    def overview(p:str, compact:bool=False):
        return workflow.overview(p, compact=compact)

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
    def candidate_mindmap(p:str,limit:int=500):
        return workflow.candidate_mindmap(p, limit)

    @router.post('/drafts',status_code=201)
    def create_draft(p:str,request:DraftRequest,http_request:HttpRequest):
        return workflow.create_run(p, request, actor=audit_actor(http_request))

    @router.post('/runs/{run_id}/finalize')
    def finalize_run(p: str, run_id: str):
        return workflow.finalize_run(p, run_id)

    @router.post('/drafts/{draft_id}/publish', status_code=202)
    def publish(p:str,draft_id:str):
        return workflow.publish(p, draft_id)

    @router.put('/drafts/{draft_id}')
    def review_draft(p:str,draft_id:str,request:DraftReview):
        return workflow.review_draft(p, draft_id, request)

    app.include_router(router)
