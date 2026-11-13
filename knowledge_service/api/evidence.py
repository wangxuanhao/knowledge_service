"""证据定位路由（GET assertion evidence 与 POST formal-record evidence）。

业务逻辑在 services.evidence；本文件只把 HTTP 请求转成对业务函数的调用。
"""
from ..models import Scope
from ..services.evidence import evidence
from ..services.entity_attributes import EntityAttributes
from pydantic import Field


class AttributeValueRequest(Scope):
    expected_version_id:str|None=Field(default=None,max_length=200)


class AttributePage(AttributeValueRequest):
    limit:int=Field(default=20,ge=1,le=50)
    cursor:str|None=Field(default=None,max_length=5000)
    predicate:str|None=Field(default=None,max_length=2000)
    focus_record_id:str|None=Field(default=None,max_length=200)


def install(app, service):
    @app.post('/api/projects/{p}/entities/{entity_id}/attributes')
    def attributes(p:str,entity_id:str,request:AttributePage):
        data=request.model_dump();limit=data.pop('limit');cursor=data.pop('cursor');predicate=data.pop('predicate');data.pop('expected_version_id');focus=data.pop('focus_record_id')
        return EntityAttributes(service).page(p,entity_id,data,limit=limit,cursor=cursor,predicate=predicate,focus_record_id=focus)

    @app.post('/api/projects/{p}/entities/{entity_id}/attributes/{record_id}')
    def attribute_value(p:str,entity_id:str,record_id:str,request:AttributeValueRequest):
        data=request.model_dump();expected=data.pop('expected_version_id')
        return EntityAttributes(service).value(p,entity_id,record_id,data,expected_version_id=expected)

    @app.post('/api/projects/{p}/entities/{entity_id}/attributes/{record_id}/sources')
    def attribute_sources(p:str,entity_id:str,record_id:str,request:AttributePage):
        data=request.model_dump();limit=data.pop('limit');cursor=data.pop('cursor');data.pop('predicate');data.pop('focus_record_id');expected=data.pop('expected_version_id')
        return EntityAttributes(service).sources(p,entity_id,record_id,data,limit=limit,cursor=cursor,expected_version_id=expected)

    @app.get('/api/projects/{p}/assertions/{assertion_id}/evidence')
    def assertion_evidence(p: str, assertion_id: str):
        return service.data.assertion_evidence(p, assertion_id)

    @app.post('/api/projects/{p}/records/{record_id}/evidence')
    def source_evidence(p: str, record_id: str, request: Scope):
        return evidence(service, p, record_id, request.model_dump())
