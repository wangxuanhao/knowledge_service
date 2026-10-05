"""探索、迁移、治理与持久化任务的 HTTP 契约。"""
from typing import Literal
from pydantic import Field
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse, FileResponse
from ..models import Request, Scope, Ingest, Question
from ..services.explorer import Explorer
from ..services.governance import Governance
from ..services.jobs import Jobs
from ..services.reclassify import Reclassify
from ..services.answers import stream_events


class LocalImport(Request):
    name:str=Field(min_length=1,max_length=300)
    kind:str='project'
    build_vectors:bool=False
    project_id:str|None=None

class Subgraph(Scope):
    node_id:str|None=None
    hops:int=Field(default=1,ge=0,le=5)
    attribute_mode:Literal['none','summary','expanded']='summary'
    entity_type:str|None=None
    predicate:str|None=None

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

class ReclassifyGroups(Request):
    """C2 受控重分类：要迁哪几组（分组 key 由面板给出）。**没有默认值** —— 默认不跑。"""
    groups:list[str]=Field(default_factory=list,max_length=500)

class ReclassifyApply(ReclassifyGroups):
    actor:str=Field(default='人工',max_length=200)


def install(app,service):
    router=APIRouter(prefix='/api')
    explorer=Explorer(service);governance=Governance(service);jobs=Jobs(service.repository)
    reclassify=Reclassify(service)
    app.state.jobs=jobs

    @app.get('/vendor/echarts.min.js')
    def echarts():
        from pathlib import Path
        return FileResponse(Path(__file__).resolve().parents[1]/'web/vendor/echarts.min.js',media_type='application/javascript')

    # P1（2026-10-04）：本体建模层单画布用 Cytoscape.js 做图编辑，vendor 是逐文件显式路由
    # （不是目录映射），所以新库要在这里补一条，否则 /vendor/cytoscape.min.js 会 404、画布渲染不出来。
    @app.get('/vendor/cytoscape.min.js')
    def cytoscape():
        from pathlib import Path
        return FileResponse(Path(__file__).resolve().parents[1]/'web/vendor/cytoscape.min.js',media_type='application/javascript')

    @router.get('/local-projects')
    def local_projects():
        from ..services.legacy_import import LegacyImporter
        return {'projects':LegacyImporter(service).list_projects()}

    @router.post('/local-projects/import',status_code=202)
    def local_import(request:LocalImport):
        from ..services.legacy_import import LegacyImporter
        def run(progress):
            progress('正在读取旧版项目；保留原始文件',10)
            result=LegacyImporter(service).import_project(
                request.name,request.kind,progress=progress,
                build_vectors=request.build_vectors,project_id=request.project_id)
            progress('项目已加载；语义索引与图谱展示相互独立',95)
            return result
        return jobs.submit('legacy_import',run)

    @router.get('/jobs/{job_id}')
    def job(job_id:str):return service.repository.get_artifact('job',job_id)

    @router.get('/jobs/{job_id}/stream')
    def job_stream(job_id:str):
        """SSE：实时推送某个后台任务的进度／完成／失败。

        替代前端每 2 秒轮询：任务跑多久就推多久，无超时窗口。
        连接建立时先回放任务当前快照（状态/进度/阶段），再实时推送。
        """
        import queue as queue_lib
        # 先确认任务存在（不存在会抛 KeyError → 404）
        snapshot=service.repository.get_artifact('job',job_id)

        def sse(event_type,payload):
            import json as _json
            return ('event: '+event_type+'\ndata: '
                    +_json.dumps(payload,ensure_ascii=False)+'\n\n')

        def generate():
            q=jobs.subscribe(job_id)
            try:
                # ① 回放当前快照：客户端中途打开也能立刻看到已有进度
                yield sse('snapshot',{
                    'id':snapshot['id'],'kind':snapshot.get('kind'),
                    'status':snapshot['status'],'progress':snapshot.get('progress',0),
                    'stage':snapshot.get('stage',''),'error':snapshot.get('error'),
                })
                # 任务在连接前就已结束：补一个终态事件后收尾
                if snapshot['status'] in ('completed','failed','interrupted'):
                    yield sse(snapshot['status'],{
                        'status':snapshot['status'],
                        'progress':100 if snapshot['status']=='completed' else snapshot.get('progress',0),
                        'error':snapshot.get('error')})
                    return
                # ② 实时消费订阅队列（阻塞读，靠心跳保活；客户端断开时 GeneratorExit）
                while True:
                    try:
                        message=q.get(timeout=15)
                    except queue_lib.Empty:
                        # 保活注释行：防止代理因空闲掐断连接
                        yield ': keep-alive\n\n'
                        continue
                    event_type=message.pop('event')
                    yield sse(event_type,message)
                    if event_type in ('completed','failed'):
                        return
            except GeneratorExit:
                raise
            finally:
                jobs.unsubscribe(job_id,q)

        return StreamingResponse(
            generate(),media_type='text/event-stream',
            headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no','Connection':'keep-alive'})

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

    @router.post('/projects/{p}/documents/upload/preview')
    def preview_upload(p: str, file: UploadFile = File(...), options: str = Form('{}')):
        service.repository.get_project(p)
        uploads = app.state.document_uploads
        parsed_options = uploads.parse_options(options)
        staged = uploads.stage(file)
        try:
            payload, parsed = uploads.payload(staged, parsed_options)
            from ..services.chunking import split_document
            chunks = split_document(payload['text'], payload)
            return {'total': len(chunks), 'chunks': chunks[:20], 'preview_limit': 20,
                    'strategy': parsed_options.chunk_strategy,
                    'chunk_size': parsed_options.chunk_size,
                    'chunk_overlap': parsed_options.chunk_overlap,
                    'parsed': parsed.metadata}
        finally:
            uploads.discard(staged)

    @router.post('/projects/{p}/documents/upload/jobs', status_code=202)
    def document_upload_job(p: str, file: UploadFile = File(...), options: str = Form('{}')):
        service.repository.get_project(p)
        uploads = app.state.document_uploads
        parsed_options = uploads.parse_options(options)
        staged = uploads.stage(file)

        def run(progress):
            try:
                progress('正在解析上传文档', 10)
                payload, _ = uploads.payload(staged, parsed_options)
                from ..utils.diagnostics import reporting
                with reporting(progress):
                    result = service.ingest(p, Ingest.model_validate(payload).model_dump())
                progress('文档及派生知识已提交', 99)
                return result
            finally:
                uploads.discard(staged)

        try:
            return jobs.submit('ingest_upload', run, p)
        except Exception:
            uploads.discard(staged)
            raise

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
    def subgraph(p:str,request:Subgraph):return explorer.graph(
        p,request.model_dump(exclude={'node_id','hops','attribute_mode'}),
        request.node_id,request.hops,request.attribute_mode)

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

    @router.get('/projects/{p}/duplicate-groups')
    def duplicate_groups(p:str):return {'groups':governance.duplicate_groups(p)}

    @router.get('/projects/{p}/structure-pending')
    def structure_pending(p:str):
        """C1 待建模收件箱：抽出来但本体里还没有的概念，各被哪些知识用到。

        只读：这里**不提供**"手动清除"接口 —— 概念进了本体（发布新版本）以后状态自动变
        「已解除」，两个清除入口必然打架。
        """
        return service.structure_pending_report(p)

    @router.get('/projects/{p}/reclassify')
    def reclassify_plan(p:str):
        """C2 受控重分类 · 面板：还挂在旧版本体上的知识，按旧类型分组，逐组给处置。

        只读。三档处置见 services/reclassify.py：原样搬到新版本 / 按替代映射改类型 /
        没有去处（没有去处的**不会**被迁移，页面会如实说"先去本体建模层建概念"）。
        这里是"默认不跑"里的"看"：不点下面的确认就什么都不会发生。
        """
        service.repository.get_project(p)
        return reclassify.plan(p)

    @router.post('/projects/{p}/reclassify/preview')
    def reclassify_preview(p:str,request:ReclassifyGroups):
        """C2 预演（干跑）：这几组迁过去会不会被本体校验拦下。**不写任何东西**。

        用的是与真正写入同一个校验函数，所以"预演通过 → 写入不会被拦"是硬承诺。
        """
        service.repository.get_project(p)
        return reclassify.preview(p,request.groups)

    @router.post('/projects/{p}/reclassify')
    def reclassify_apply(p:str,request:ReclassifyApply):
        """C2 迁移：把选中的组迁到当前本体。

        每条记录**产生新版本**（不原地改），整批写成**一条**操作 —— 撤销走
        ``POST /operations/{operation_id}/undo``（与合并/删除同一套回滚通道）。
        """
        service.repository.get_project(p)
        return reclassify.apply(p,request.groups,request.actor)

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
