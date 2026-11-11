"""Superadministrator-only platform model management endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from ..services.llm_models import LLMKeyError


class CreateModel(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    name: str = Field(max_length=128)
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=256)
    api_key: str = Field(max_length=8192, repr=False)


class UpdateModel(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    name: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=256)
    api_key: str | None = Field(default=None, max_length=8192, repr=False)


class _PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except RequestValidationError:
                # FastAPI's default errors include the raw rejected value,
                # including API keys and credential-bearing invalid URLs.
                return JSONResponse(status_code=422, content={
                    'detail': '模型配置参数无效，请检查名称、地址、模型名和密钥长度'})
        return handle


def _superadmin(request: Request):
    if getattr(request.app.state, 'auth_disabled', False):
        return
    actor = getattr(request.state, 'user', None) or {}
    if actor.get('role') != 'superadmin':
        raise HTTPException(403, '只有超级管理员可以管理平台模型')


def _run(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except KeyError:
        raise HTTPException(404, '模型配置不存在') from None
    except LLMKeyError as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def register(app, models):
    router = APIRouter(prefix='/api/llm-models', dependencies=[Depends(_superadmin)],
                       route_class=_PrivateValidationRoute)

    @router.get('')
    def list_models():
        return _run(models.list)

    @router.post('', status_code=201)
    def create_model(body: CreateModel):
        return _run(models.create, **body.model_dump())

    @router.patch('/{profile_id}')
    def update_model(profile_id: str, body: UpdateModel):
        return _run(models.update, profile_id, **body.model_dump(exclude_none=True))

    @router.post('/{profile_id}/activate')
    def activate_model(profile_id: str):
        return _run(models.activate, profile_id)

    @router.post('/{profile_id}/test')
    def test_model(profile_id: str):
        return _run(models.test_connection, profile_id)

    app.include_router(router)
