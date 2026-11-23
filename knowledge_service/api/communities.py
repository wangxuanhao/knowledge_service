"""Project and single-platform knowledge community routes."""
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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
    mode: Literal['keyword', 'semantic', 'global'] = 'keyword'
    level: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def select_coverage(self):
        if self.mode == 'global' and (self.level is None or self.community_ids):
            raise ValueError('全局综合必须选择一个社区层级')
        if self.mode != 'global' and self.level is not None:
            raise ValueError('社区层级用于全局综合')
        if self.mode == 'semantic' and self.community_ids:
            raise ValueError('语义检索会自动召回社区，请清空已选社区')
        return self


router = APIRouter(prefix='/api/projects/{project_id}/communities', tags=['communities'])


@router.post('')
def list_communities(project_id: str, body: CommunityList, request: Request):
    return request.app.state.communities.list(project_id, body.scope_data(), keyword=body.keyword.strip(),
                                              anchor=body.anchor.strip(), limit=body.limit, offset=body.offset)


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


@router.post('/ask')
def ask_communities(project_id: str, body: CommunityAsk, request: Request):
    try:
        return request.app.state.communities.ask(
            project_id, body.scope_data(), body.community_ids, body.question,
            generate=body.generate, expected_revision=body.expected_revision, mode=body.mode, level=body.level)
    except CommunitySnapshotStale as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def install(app, service):
    communities = Communities(service.repository, app.state.jobs, service.llm_models, search_service=service)
    app.state.communities = communities
    app.state.community_refresh = CommunityRefresh(communities)
    app.state.jobs.handlers[JOB_KIND] = communities.run_job
    app.include_router(router)
