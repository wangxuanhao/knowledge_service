"""HTTP transport for owned file preparations; expensive work runs in workers."""
import json
from typing import Literal
from urllib.parse import quote
import zipfile

from fastapi import APIRouter,File,Form,Header,HTTPException,Request as HttpRequest,UploadFile
from fastapi.responses import Response
from pydantic import Field,model_validator
from ..models import Request
from ..services.preparations import Preparations,PreparationConflict
from ..services.preparation_runner import PreparationRunner
from .audit_identity import audit_identity

class CreatePreparation(Request):
    defaults:dict=Field(default_factory=dict)

class PreparationSettings(Request):
    settings:dict
    expected_version:int=Field(ge=0)

class PatchPreparationItem(Request):
    options:dict
    expected_version:int=Field(ge=1)

class PreparationSelection(Request):
    item_ids:list[str]=Field(min_length=1,max_length=1000)
    @model_validator(mode='after')
    def unique(self):
        if len(self.item_ids)!=len(set(self.item_ids)):raise ValueError('文件选择不得重复')
        return self

class ParsePreparation(PreparationSelection):
    versions:dict[str,int]
    idempotency_key:str=Field(min_length=1,max_length=200)

class BulkPreparation(PreparationSelection):
    fields:dict
    versions:dict[str,int]
    mode:Literal['overwrite','fill_missing']='overwrite'

class PublishPreparation(ParsePreparation):
    result_versions:dict[str,str]
    settings:dict=Field(default_factory=dict)
    settings_version:int|None=Field(default=None,ge=0)
    independent_item_ids:list[str]=Field(default_factory=list,max_length=1000)
    @model_validator(mode='after')
    def independent_selection(self):
        if len(set(self.independent_item_ids))!=len(self.independent_item_ids) or set(self.independent_item_ids)-set(self.item_ids):
            raise ValueError('independent_item_ids 必须是所选文件的不重复子集')
        return self


def install(app,service):
    operations=Preparations(service,app.state.document_uploads,app.state.source_documents,app.state.ingest_batches)
    app.state.preparations=operations
    app.state.preparation_runner=PreparationRunner(operations,
        limit=min(2,app.state.ingest_batches.limits.server_concurrency_limit),
        ocr_semaphore=app.state.ingest_batch_runner.ocr_semaphore)
    router=APIRouter(prefix='/api/projects/{p}/preparations')

    def owner(http):return audit_identity(http)['id']
    def invoke(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except PreparationConflict as exc:raise HTTPException(409,detail={'code':'preparation_conflict','message':str(exc),'items':getattr(exc,'items',[])}) from exc
        except KeyError as exc:raise HTTPException(404,detail='文件准备会话或文件不存在，或已过期') from exc
        except (ValueError,zipfile.BadZipFile,RuntimeError) as exc:
            from ..utils.diagnostics import redact
            raise HTTPException(getattr(exc,'status_code',422),detail={'code':getattr(exc,'code','invalid_preparation_request'),'message':redact(str(exc))}) from exc

    @router.post('',status_code=201)
    def create(p:str,body:CreatePreparation,http:HttpRequest):
        return invoke(operations.create,p,owner(http),body.defaults)

    @router.get('')
    def listing(p:str,http:HttpRequest):return invoke(operations.list,p,owner(http))

    @router.get('/{identifier}')
    def get(p:str,identifier:str,http:HttpRequest):return invoke(operations.get,p,identifier,owner(http))

    @router.patch('/{identifier}/settings')
    def settings(p:str,identifier:str,body:PreparationSettings,http:HttpRequest):
        return invoke(operations.settings,p,identifier,owner(http),body.settings,body.expected_version)

    @router.post('/{identifier}/files',status_code=201)
    def upload(p:str,identifier:str,http:HttpRequest,file:UploadFile=File(...),options:str=Form('{}'),
               key:str|None=Header(None,alias='Idempotency-Key')):
        try:raw=json.loads(options)
        except ValueError as exc:raise HTTPException(422,detail='options 必须为 JSON 对象') from exc
        if not isinstance(raw,dict):raise HTTPException(422,detail='options 必须为 JSON 对象')
        if key is not None and (not key or len(key)>200):raise HTTPException(422,detail='幂等键长度必须为 1–200')
        return invoke(operations.upload,p,identifier,owner(http),file,raw,key)

    @router.patch('/{identifier}/items/{item_id}')
    def patch(p:str,identifier:str,item_id:str,body:PatchPreparationItem,http:HttpRequest):
        return invoke(operations.patch,p,identifier,owner(http),item_id,body.options,body.expected_version)

    @router.post('/{identifier}/bulk')
    def bulk(p:str,identifier:str,body:BulkPreparation,http:HttpRequest):
        return invoke(operations.bulk,p,identifier,owner(http),body.item_ids,body.fields,body.versions,body.mode)

    @router.post('/{identifier}/parse',status_code=202)
    def parse(p:str,identifier:str,body:ParsePreparation,http:HttpRequest):
        return invoke(operations.parse,p,identifier,owner(http),body.item_ids,body.versions,body.idempotency_key)

    @router.post('/{identifier}/retry',status_code=202)
    def retry(p:str,identifier:str,body:ParsePreparation,http:HttpRequest):
        return invoke(operations.parse,p,identifier,owner(http),body.item_ids,body.versions,body.idempotency_key,True)

    @router.post('/{identifier}/cancel')
    def cancel(p:str,identifier:str,body:PreparationSelection,http:HttpRequest):
        return invoke(operations.cancel,p,identifier,owner(http),body.item_ids)

    @router.delete('/{identifier}/items/{item_id}')
    def remove(p:str,identifier:str,item_id:str,http:HttpRequest):
        return invoke(operations.cancel,p,identifier,owner(http),[item_id],True)

    @router.get('/{identifier}/items/{item_id}/result')
    def result(p:str,identifier:str,item_id:str,http:HttpRequest):
        return invoke(operations.result,p,identifier,owner(http),item_id,include_document_details=True)

    @router.get('/{identifier}/items/{item_id}/original')
    def original(p:str,identifier:str,item_id:str,http:HttpRequest):
        value=invoke(operations.original,p,identifier,owner(http),item_id)
        return Response(content=value['data'],media_type=value['mime_type'],headers={
            'Content-Disposition':"inline; filename*=UTF-8''"+quote(value['original_name'],safe=''),
            'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"})

    @router.get('/{identifier}/items/{item_id}/preview')
    def preview(p:str,identifier:str,item_id:str,http:HttpRequest):
        return invoke(operations.preview,p,identifier,owner(http),item_id)

    @router.post('/{identifier}/publish',status_code=202)
    def publish(p:str,identifier:str,body:PublishPreparation,http:HttpRequest):
        return invoke(operations.publish,p,identifier,owner(http),body.item_ids,body.versions,body.result_versions,body.settings,body.idempotency_key,body.settings_version,body.independent_item_ids)

    app.include_router(router)
