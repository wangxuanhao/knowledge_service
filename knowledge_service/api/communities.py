"""Project and single-platform knowledge community routes."""
import asyncio
import json
import logging
from threading import Event
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..models import QuestionContextTurn
from ..services.communities import (
    JOB_KIND, Communities, CommunityRefresh, CommunitySnapshotStale, platform_name,
)


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CommunityScope(BaseModel):
    model_config = ConfigDict(extra='forbid')

    kind: Literal['project', 'platform'] = 'project'
    platform: str | None = Field(default=None, max_length=128)

    @model_validator(mode='after')
    def normalize(self):
        if self.kind == 'platform':
            if not self.platform or not self.platform.strip():
                raise ValueError('平台范围必须指定单个平台')
            self.platform = platform_name(self.platform)
        elif self.platform is not None:
            raise ValueError('项目范围不能附带平台')
        return self


class CommunityRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    scope: CommunityScope = Field(default_factory=CommunityScope)

    def scope_data(self):
        return self.scope.model_dump(exclude_none=True)


class CommunityList(CommunityRequest):
    keyword: str = Field(default='', max_length=400)
    mode: Literal['keyword', 'semantic'] = 'keyword'
    anchor: str = Field(default='', max_length=200)
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CommunityBuild(CommunityRequest):
    retry_failed: bool = False
    vectorize: bool = False


class CommunityDetail(CommunityRequest):
    community_id: Identifier
    expected_revision: Identifier | None = None
    limit: int = Field(default=200, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class CommunityAsk(CommunityRequest):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    community_ids: list[Identifier] = Field(default_factory=list, max_length=32)
    expected_revision: Identifier | None = None
    generate: bool = False
    match_only: bool = False
    mode: Literal['keyword', 'semantic', 'hybrid', 'global'] = 'keyword'
    plan: bool = False
    level: int | None = Field(default=None, ge=0)
    conversation_context: list[QuestionContextTurn] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def select_coverage(self):
        if self.mode == 'global' and (self.level is None or self.community_ids):
            raise ValueError('全局综合必须选择一个社区层级')
        if self.mode != 'global' and self.level is not None:
            raise ValueError('社区层级用于全局综合')
        if self.mode in ('semantic', 'hybrid') and self.community_ids:
            raise ValueError('自动召回社区前请清空已选社区')
        return self


router = APIRouter(prefix='/api/projects/{project_id}/communities', tags=['communities'])


@router.post('')
def list_communities(project_id: str, body: CommunityList, request: Request):
    try:
        return request.app.state.communities.list(project_id, body.scope_data(), keyword=body.keyword.strip(),
                                                  anchor=body.anchor.strip(), limit=body.limit, offset=body.offset,
                                                  mode=body.mode)
    except CommunitySnapshotStale as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/status')
def community_status(project_id: str, body: CommunityRequest, request: Request):
    return request.app.state.communities.status(project_id, body.scope_data())


@router.post('/build')
def build_communities(project_id: str, body: CommunityBuild, request: Request):
    try:
        return request.app.state.communities.build(project_id, body.scope_data(), retry_failed=body.retry_failed,
                                                    vectorize=body.vectorize)
    except CommunitySnapshotStale as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/detail')
def community_detail(project_id: str, body: CommunityDetail, request: Request):
    try:
        return request.app.state.communities.detail(project_id, body.scope_data(), body.community_id,
                                                     limit=body.limit, offset=body.offset,
                                                     expected_revision=body.expected_revision)
    except CommunitySnapshotStale as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _answer(communities, project_id, body, **kwargs):
    if body.conversation_context:
        kwargs['conversation_context'] = [item.model_dump() for item in body.conversation_context]
    if body.plan:
        kwargs['plan'] = True
    return communities.ask(project_id, body.scope_data(), body.community_ids, body.question,
        generate=body.generate, expected_revision=body.expected_revision, mode=body.mode, level=body.level,
        match_only=body.match_only, **kwargs)


async def _stream_answer(communities, project_id, body):
    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    closed = Event()

    def emit(name, data):
        if closed.is_set():
            raise asyncio.CancelledError
        loop.call_soon_threadsafe(events.put_nowait, (name, data))

    def produce():
        try:
            emit('done', _answer(communities, project_id, body, emit=emit))
        except CommunitySnapshotStale as exc:
            emit('error', {'status': 409, 'detail': str(exc)})
        except KeyError:
            emit('error', {'status': 404, 'detail': '项目或社区资源不存在'})
        except Exception:
            logging.getLogger(__name__).exception('社区问答流失败')
            emit('error', {'status': 500, 'detail': '社区问答失败，请重试。'})

    worker = asyncio.create_task(asyncio.to_thread(produce))
    try:
        yield 'event: progress\ndata: {"stage":"retrieving"}\n\n'
        while True:
            try:
                name, data = await asyncio.wait_for(events.get(), timeout=15)
            except TimeoutError:
                yield ': keepalive\n\n'
                continue
            yield f'event: {name}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n'
            if name in ('done', 'error'):
                return
    finally:
        closed.set()
        worker.cancel()


@router.post('/ask')
def ask_communities(project_id: str, body: CommunityAsk, request: Request):
    communities = request.app.state.communities
    if 'text/event-stream' in request.headers.get('accept', ''):
        return StreamingResponse(_stream_answer(communities, project_id, body), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    try:
        return _answer(communities, project_id, body)
    except CommunitySnapshotStale as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def install(app, service):
    communities = Communities.for_service(service, app.state.jobs)
    app.state.communities = communities
    app.state.community_refresh = CommunityRefresh(communities)
    app.state.jobs.handlers[JOB_KIND] = communities.run_job
    app.include_router(router)
