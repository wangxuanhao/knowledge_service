"""探索、迁移、治理与持久化任务的 HTTP 契约。"""
from pydantic import Field
from fastapi import APIRouter
from fastapi.responses import StreamingResponse, FileResponse
from ..models import Request, Scope, Ingest, Question
from ..services.explorer import Explorer
from ..services.governance import Governance
from ..services.jobs import Jobs
from ..services.answers import stream_events


class LocalImport(Request):
    name:str=Field(min_length=1,max_length=300)
    kind:str='project'
    build_vectors:bool=False

class Subgraph(Scope):
    node_id:str|None=None
    hops:int=Field(default=1,ge=0,le=5)

class Mindmap(Scope):
    root_id:str
    depth:int=Field(default=3,ge=0,le=5)

class Resolve(Scope):
    text:str=Field(min_length=1,max_length=500)
    threshold:float=Field(default=.7,ge=0,le=1)

class Alias(Request):
    entity_id:str
    alias:str
    expected_version:int=Field(ge=1)

class Merge(Request):
    keep_id:str
    drop_id:str
    expected_versions:dict[str,int]
    attribute_winners:list[str]=[]

class Delete(Request):
    record_id:str
    expected_version:int=Field(ge=1)

class Restore(Delete):
    version:int=Field(ge=1)

class Snapshot(Request):
    name:str=Field(default='手动快照',min_length=1,max_length=200)

class Evaluate(Scope):
    entities:list[str]=Field(default_factory=list,max_length=10000)
    relations:list[tuple[str,str,str]]=Field(default_factory=list,max_length=10000)


def install(app,service):
    router=APIRouter(prefix='/api')
    explorer=Explorer(service);governance=Governance(service);jobs=Jobs(service.repository)
    app.state.jobs=jobs

    @app.get('/vendor/echarts.min.js')
    def echarts():
        from pathlib import Path
        return FileResponse(Path(__file__).resolve().parents[1]/'web/vendor/echarts.min.js',media_type='application/javascript')

    @router.get('/local-projects')
    def local_projects():
        from ..services.legacy_import import LegacyImporter
        return {'projects':LegacyImporter(service).list_projects()}

    @router.post('/local-projects/import',status_code=202)
    def local_import(request:LocalImport):
        from ..services.legacy_import import LegacyImporter
        def run(progress):
            progress('正在读取旧版项目；保留原始文件',10)
            result=LegacyImporter(service).import_project(request.name,request.kind,progress=progress,build_vectors=request.build_vectors)
            progress('项目已加载；语义索引与图谱展示相互独立',95)
            return result
        return jobs.submit('legacy_import',run)

    @router.get('/jobs/{job_id}')
    def job(job_id:str):return service.repository.get_artifact('job',job_id)

    @router.get('/jobs')
    def all_jobs():return {'jobs':service.repository.list_artifacts('job')[:100]}

    @router.get('/projects/{p}/jobs')
    def project_jobs(p:str):
        service.repository.get_project(p)
        return {'jobs':service.repository.list_artifacts('job',p)}

    @router.post('/projects/{p}/documents/preview')
    def preview_document(p:str,request:Ingest):
        service.repository.get_project(p)
        from ..services.chunking import split_document
        chunks=split_document(request.text, request.model_dump())
        return {'total':len(chunks),'chunks':chunks[:20],'preview_limit':20,
                'strategy':request.chunk_strategy,'chunk_size':request.chunk_size,'chunk_overlap':request.chunk_overlap}

    @router.post('/projects/{p}/documents/jobs',status_code=202)
    def document_job(p:str,request:Ingest):
        service.repository.get_project(p)
        def run(progress):
            from ..utils.diagnostics import reporting
            with reporting(progress):
                result=service.ingest(p,request.model_dump())
            progress('文档及派生知识已提交',99)
            return result
        return jobs.submit('ingest',run,p)

    @router.post('/projects/{p}/dashboard')
    def dashboard(p:str,request:Scope):return explorer.dashboard(p,request.model_dump())

    @router.post('/projects/{p}/sources')
    def sources(p:str,request:Scope):return explorer.sources(p,request.model_dump())

    @router.post('/projects/{p}/subgraph')
    def subgraph(p:str,request:Subgraph):return explorer.graph(p,request.model_dump(),request.node_id,request.hops)

    @router.post('/projects/{p}/mindmap')
    def mindmap(p:str,request:Mindmap):return explorer.mindmap(p,request.model_dump(),request.root_id,request.depth)

    @router.post('/projects/{p}/resolve')
    def resolve(p:str,request:Resolve):return governance.resolve(p,request.text,request.model_dump(),request.threshold)

    @router.post('/projects/{p}/aliases')
    def alias(p:str,request:Alias):return governance.alias(p,request.entity_id,request.alias,request.expected_version)

    @router.post('/projects/{p}/merge')
    def merge(p:str,request:Merge):return governance.merge(
        p,request.keep_id,request.drop_id,request.expected_versions,
        attribute_winners=request.attribute_winners)

    @router.post('/projects/{p}/delete')
    def delete(p:str,request:Delete):return governance.delete(p,request.record_id,request.expected_version)

    @router.post('/projects/{p}/restore')
    def restore(p:str,request:Restore):return governance.restore(p,request.record_id,request.version,request.expected_version)

    @router.get('/projects/{p}/operations')
    def operations(p:str):return {'operations':governance.operations(p)}

    @router.post('/projects/{p}/operations/{operation_id}/undo')
    def undo(p:str,operation_id:str):return governance.undo_merge(p,operation_id)

    @router.post('/projects/{p}/snapshots')
    def snapshot(p:str,request:Snapshot):return explorer.snapshot(p,request.name)

    @router.get('/projects/{p}/snapshots')
    def snapshots(p:str):
        service.repository.get_project(p)
        return {'snapshots':[{k:v for k,v in s.items() if k not in ('records','ontologies')} for s in service.repository.list_artifacts('snapshot',p)]}

    @router.post('/projects/{p}/snapshots/{snapshot_id}/restore')
    def restore_snapshot(p:str,snapshot_id:str):return explorer.restore_snapshot(p,snapshot_id)

    @router.post('/projects/{p}/evaluate')
    def evaluate(p:str,request:Evaluate):return explorer.evaluate(p,request.model_dump(),request.entities,request.relations)

    @router.get('/projects/{p}/export')
    def export(p:str):
        return service.repository.export_projection(p)

    @router.post('/projects/{p}/qa/stream')
    def qa_stream(p:str,request:Question):
        service.repository.get_project(p)
        return StreamingResponse(stream_events(service,p,request.model_dump()),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    app.include_router(router)
