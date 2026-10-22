"""Complete graph sessions and explicitly budgeted local exploration."""
from fastapi import APIRouter, Request as HttpRequest
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator
from typing import Literal

from ..models import Scope, Request
from ..services.graph_workspace import GraphWorkspace


class GraphPage(Request):
    kind: Literal['entities', 'relations', 'connected']
    cursor: str | None = Field(default=None, max_length=10000)
    limit: int = Field(default=400, ge=1, le=10000)

    @model_validator(mode='after')
    def bound_connected_page(self):
        if self.kind == 'connected' and self.limit > 500:
            raise ValueError('渐进图谱每批最多读取 500 条关系')
        return self


class GraphNeighborhood(Scope):
    seeds: list[str] = Field(min_length=1, max_length=100)
    hops: int = Field(default=1, ge=0, le=10, strict=True)
    direction: Literal['forward', 'backward', 'both'] = 'both'
    max_nodes: int = Field(default=500, ge=1, le=10000)
    max_edges: int = Field(default=1000, ge=1, le=10000)


class RecordsPage(Scope):
    query: str = Field(default='', max_length=2000)
    cursor: str | None = Field(default=None, max_length=10000)
    limit: int = Field(default=50, ge=1, le=1000)
    include_total: bool = True
    record_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)


def install(app, service):
    router = APIRouter(prefix='/api/projects/{project}')
    workspace = GraphWorkspace(service)

    @router.post('/graph-sessions')
    def create(project: str, body: Scope, request: HttpRequest):
        actor = getattr(request.state, 'user', None) or {}
        return workspace.sessions.create(project, body.model_dump(), actor_id=actor.get('id'))

    @router.post('/graph-sessions/{session_id}/page')
    def page(project: str, session_id: str, body: GraphPage, request: HttpRequest):
        actor = getattr(request.state, 'user', None) or {}
        try:
            # The service emits compact JSON primitives. Avoid walking every
            # field again in FastAPI's generic Python object encoder.
            return JSONResponse(content=workspace.sessions.page(
                project, session_id, body.kind, cursor=body.cursor,
                limit=body.limit, actor_id=actor.get('id')))
        except PermissionError as exc:
            return JSONResponse(status_code=403, content={'detail': str(exc)})
        except ValueError as exc:
            return JSONResponse(status_code=409, content={'detail': str(exc), 'code': 'graph_session_invalid'})

    @router.post('/graph-neighborhood')
    def neighborhood(project: str, body: GraphNeighborhood):
        return workspace.neighborhood(project, body.model_dump())

    @router.post('/records/page')
    def records_page(project: str, body: RecordsPage):
        return workspace.records_page(project, body.model_dump())

    app.include_router(router)
