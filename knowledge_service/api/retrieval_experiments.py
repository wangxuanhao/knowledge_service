"""Authenticated project experiment endpoints; execution uses durable Jobs."""
from typing import Literal

from fastapi import APIRouter, Request as HttpRequest
from fastapi.responses import JSONResponse
from pydantic import Field

from ..models import Request, Scope
from ..services.retrieval_experiments import RetrievalExperiments
from .audit_identity import audit_identity


class ExperimentQuestion(Request):
    id: str | None = Field(default=None, max_length=100)
    query: str = Field(min_length=1, max_length=10000)
    expected_record_ids: list[str] = Field(default_factory=list, max_length=1000)
    expected_answer: str | None = Field(default=None, max_length=20000)


class ExperimentCreate(Request):
    name: str = Field(default='检索与问答消融', max_length=200)
    questions: list[ExperimentQuestion] = Field(min_length=1, max_length=100)
    variants: list[Literal['basic', 'document_only', 'no_graph', 'no_ontology', 'combined']] = Field(
        default_factory=lambda: ['basic', 'document_only', 'no_graph', 'no_ontology', 'combined'], min_length=1, max_length=5)
    scope: Scope = Field(default_factory=Scope)
    retrieval_mode: Literal['keyword', 'semantic', 'hybrid'] = 'hybrid'
    k: int = Field(default=10, ge=1, le=100)
    k_entities: int = Field(default=5, ge=0, le=50)
    k_chunks: int = Field(default=5, ge=0, le=50)
    k_relations: int = Field(default=5, ge=0, le=50)
    k_per_platform: int = Field(default=5, ge=1, le=20)
    k_unknown: int = Field(default=3, ge=1, le=20)
    hops: int = Field(default=2, ge=0, le=3)
    include_references: bool = True
    reference_k: int = Field(default=5, ge=1, le=20)
    generate: bool = False


def install(app, service):
    manager = RetrievalExperiments(service, app.state.jobs,
        auth_disabled=getattr(app.state, 'auth_disabled', False))
    app.state.retrieval_experiments = manager
    app.state.jobs.handlers['retrieval_experiment'] = manager.run_job
    router = APIRouter(prefix='/api/projects/{project}/retrieval-experiments')

    def call(operation):
        try:
            return operation()
        except PermissionError as exc:
            return JSONResponse(status_code=403, content={'detail': str(exc)})
        except KeyError:
            return JSONResponse(status_code=404, content={'detail': '实验或项目不存在'})
        except ValueError as exc:
            return JSONResponse(status_code=409, content={'detail': str(exc), 'code': 'experiment_invalid'})

    @router.post('')
    def create(project: str, body: ExperimentCreate, request: HttpRequest):
        actor = audit_identity(request)['id']
        return call(lambda: manager.create(project, body.model_dump(), actor_id=actor))

    @router.get('')
    def list_runs(project: str, request: HttpRequest):
        return call(lambda: manager.list(project, actor_id=audit_identity(request)['id']))

    @router.get('/{run_id}')
    def detail(project: str, run_id: str, request: HttpRequest):
        return call(lambda: manager.get(project, run_id, actor_id=audit_identity(request)['id']))

    @router.post('/{run_id}/cancel')
    def cancel(project: str, run_id: str, request: HttpRequest):
        return call(lambda: manager.cancel(project, run_id, actor_id=audit_identity(request)['id']))

    @router.post('/{run_id}/retry')
    def retry(project: str, run_id: str, request: HttpRequest):
        return call(lambda: manager.retry(project, run_id, actor_id=audit_identity(request)['id']))

    @router.get('/{run_id}/export')
    def export(project: str, run_id: str, request: HttpRequest):
        return call(lambda: manager.export(project, run_id, actor_id=audit_identity(request)['id']))

    app.include_router(router)
