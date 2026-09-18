"""HTTP 接口；所有知识读取共享同一个项目及时间/过滤范围。"""
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
import httpx

from .embeddings import configured_encoder
from .models import ProjectCreate, ProjectUpdate, RecordBatch, Revision, Scope, Search, Question, Ingest, OntologyWrite, Sparql, ResolutionReviewDecision
from .ontology import Ontology
from .repository import OntologyNotPublished, Repository
from .service import KnowledgeService, public
from .time import utc_now

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger('knowledge_service')


def _build_milvus_store():
    """按需创建 Milvus 检索索引；未显式开启或连接失败返回 None，降级本地 SQLite 向量。

    通过环境变量 KG_VECTOR_BACKEND=milvus 显式开启（默认关闭，保持向后兼容）。
    """
    if os.environ.get('KG_VECTOR_BACKEND', '') != 'milvus':
        return None
    try:
        from .milvus_store import MilvusStore
        host = os.environ.get('KG_MILVUS_HOST', 'localhost')
        port = os.environ.get('KG_MILVUS_PORT', '19530')
        dim = int(os.environ.get('KG_MILVUS_DIM', '1024'))
        store = MilvusStore(host=host, port=port, dim=dim)
        store.connect()
        store.ensure_collection()
        return store
    except Exception as exc:
        LOG.warning('Milvus 不可用，降级本地检索: %s', exc)
        return None


def create_app(db_path=None, encoder=None):
    repository = Repository(db_path or os.environ.get('KG_DATABASE', str(ROOT / 'data/service/knowledge.sqlite')))
    encoder = encoder or configured_encoder()
    service = KnowledgeService(repository, encoder, milvus_store=_build_milvus_store())

    @asynccontextmanager
    async def lifespan(app):
        # 启动后异步预热 embedding 模型（首次 encode ~141s），不阻塞启动，消除首次检索卡顿
        import threading
        threading.Thread(target=service.warm_up, daemon=True).start()
        yield
        app.state.jobs.close()
        app.state.neo4j.close()
        repository.close()

    app = FastAPI(title='Knowledge Service', version='1.0.0', lifespan=lifespan,
                  description='项目本体、双时态知识与 metadata 前置过滤检索。时间区间为左闭右开。')
    app.state.service = service
    @app.middleware('http')
    async def fresh_workbench_assets(request, call_next):
        response = await call_next(request)
        if request.url.path == '/' or request.url.path.startswith('/assets/'):
            response.headers['Cache-Control'] = 'no-cache'
        return response
    from .parity_api import install
    install(app,service)
    from .neo4j_store import install as install_neo4j
    install_neo4j(app, service)
    from .workspace_api import install as install_workspace
    install_workspace(app, service)
    from .evidence import install as install_evidence
    install_evidence(app, service)
    from .reviews import install as install_reviews
    install_reviews(app, service)
    from .ontology_changes import install as install_ontology_changes
    install_ontology_changes(app, service)
    from .ontology_discovery import install as install_ontology_discovery
    install_ontology_discovery(app, service)

    @app.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={'detail': f'未找到：{exc.args[0]}'})

    @app.exception_handler(OntologyNotPublished)
    async def ontology_missing(request: Request, exc: OntologyNotPublished):
        return JSONResponse(status_code=404, content={
            'detail': '本项目尚未发布本体。可先持续开放发现并累计候选，再在本体发现中生成、审核和发布本体版本；仅文档检索模式不产生图谱本体。',
            'code': 'ontology_not_published'})

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse(status_code=409 if '版本冲突' in str(exc) else 422, content={'detail': str(exc)})

    @app.exception_handler(ValidationError)
    async def invalid_model(request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={'detail': str(exc)})

    @app.exception_handler(RuntimeError)
    async def unavailable(request: Request, exc: RuntimeError):
        LOG.warning('操作不可用：%s', type(exc).__name__)
        return JSONResponse(status_code=503, content={'detail': str(exc)})

    @app.exception_handler(httpx.HTTPError)
    async def provider_error(request: Request, exc: httpx.HTTPError):
        return JSONResponse(status_code=503, content={'detail': f'模型提供商不可用（{type(exc).__name__}）'})

    @app.get('/api/health')
    def health():
        try:
            semantica = version('semantica')
        except PackageNotFoundError:
            semantica = None
        import sys
        return {'status':'ok', 'version':'1.1.0', 'database':'sqlite', 'time_model':'bitemporal', 'python_executable':sys.executable,
                'capabilities':['legacy_import','interactive_graph_api','governance','semantica_merge','jobs','snapshots','dual_channel_qa','typed_reviews','ontology_change_proposals','ontology_discovery'],
                'semantica_version':semantica, 'embedding_model':service.encoder.identity,
                'semantic':service.encoder.semantic, 'time':utc_now()}

    @app.get('/api/projects')
    def projects():
        return {'projects': repository.list_projects()}

    @app.post('/api/projects', status_code=201)
    def create_project(request: ProjectCreate):
        mode=request.ontology_mode or 'ontology'
        metadata={**request.metadata,'ontology_mode':mode}
        p = repository.create_project(request.name, metadata)
        if request.use_default_ontology and mode=='ontology':
            turtle = (Path(__file__).parent / 'resources/default_ontology.ttl').read_text(encoding='utf-8')
            repository.save_ontology(p['id'], turtle, Ontology(turtle).summary())
        return p

    @app.get('/api/projects/{project_id}')
    def project(project_id: str):
        return repository.get_project(project_id)

    @app.put('/api/projects/{project_id}')
    def rename_project(project_id: str, request: ProjectUpdate):
        return repository.rename_project(project_id, request.name)

    @app.delete('/api/projects/{project_id}')
    def delete_project(project_id: str):
        deleted = repository.delete_project(project_id)
        # SQLite 删除成功后，再清理可选的派生存储（Milvus 向量分区 / Neo4j 副本）。
        # 任一外部存储失败都不回滚 SQLite 删除，只记录告警——它们都是可重建的副本。
        cleaned = {'milvus': False, 'neo4j': False}
        if service.milvus_store is not None:
            try:
                service.milvus_store.delete(project_id)
                cleaned['milvus'] = True
            except Exception as exc:
                LOG.warning('项目删除后清理 Milvus 分区失败（可经重建索引恢复）: %s', exc)
        try:
            cleaned['neo4j'] = app.state.neo4j.delete_project(project_id)
        except Exception as exc:
            LOG.warning('项目删除后清理 Neo4j 副本失败（可手动重新同步清理）: %s', exc)
        return {'deleted': deleted, 'cleaned': cleaned}

    @app.post('/api/projects/{project_id}/records', status_code=201)
    def write(project_id: str, request: RecordBatch):
        rows = service.write(project_id, [r.model_dump(exclude_none=True) for r in request.records])
        return {'records':[public(r) for r in rows]}

    @app.put('/api/projects/{project_id}/records/{record_id}')
    def revise(project_id: str, record_id: str, request: Revision):
        row = request.record.model_dump(exclude_none=True)
        if row.get('id') and row['id'] != record_id:
            raise ValueError('路径与记录 ID 不一致')
        row['id'] = record_id
        with service.lock:
            history = repository.history(project_id, record_id)
            if not history:
                raise KeyError(record_id)
            if history[-1]['kind'] != row['kind']:
                raise ValueError('修订期间记录类型不能改变')
            return public(service.write(project_id, [row], request.expected_version, revision=True)[0])

    @app.get('/api/projects/{project_id}/records/{record_id}/history')
    def history(project_id: str, record_id: str):
        versions = repository.history(project_id, record_id)
        if not versions:
            raise KeyError(record_id)
        return {'versions':[public(r) for r in versions]}

    @app.post('/api/projects/{project_id}/records/query')
    def query_records(project_id: str, request: Scope, limit: int = 200, offset: int = 0):
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError('limit 必须在 1..1000 且 offset 不能为负')
        records = service.scoped(project_id, request.model_dump())
        return {'total':len(records), 'records':[public(r) for r in records[offset:offset+limit]]}

    @app.get('/api/projects/{project_id}/assertions')
    def assertions(project_id: str, status: str | None = None,
                   canonical_record_id: str | None = None, document_id: str | None = None):
        items=repository.list_assertions(project_id,status=status,
            canonical_record_id=canonical_record_id,document_id=document_id)
        return {'assertions':items,'total':len(items)}

    @app.get('/api/projects/{project_id}/ingest-runs')
    def ingest_runs(project_id: str, document_id: str | None = None):
        items=repository.list_ingest_runs(project_id,document_id=document_id)
        return {'runs':items,'total':len(items)}

    @app.get('/api/projects/{project_id}/resolution-reviews')
    def resolution_reviews(project_id: str, status: str | None = None):
        items=repository.list_resolution_reviews(project_id,status=status)
        return {'reviews':items,'total':len(items)}

    @app.post('/api/projects/{project_id}/resolution-reviews/{review_id}')
    def decide_resolution(project_id: str,review_id: str,request: ResolutionReviewDecision):
        review=next((item for item in repository.list_resolution_reviews(project_id)
                     if item['id']==review_id),None)
        if review is None:raise KeyError(review_id)
        if request.decision=='merged':
            from .governance import Governance
            decision={'id':review_id,'expected_version':request.expected_version,
                'decision':'merged','reason':request.reason,'actor':request.actor}
            operation=Governance(service).merge(project_id,review['candidate_entity_id'],
                review['source_entity_id'],request.expected_entity_versions,
                resolution_decision=decision)
            return {'review':repository.list_resolution_reviews(project_id,status='merged')[-1],
                    'merge':operation}
        return {'review':repository.decide_resolution_review(project_id,review_id,
            request.expected_version,request.decision,request.reason,request.actor)}

    @app.get('/api/projects/{project_id}/merge-operations')
    def merge_operations(project_id: str):
        items=repository.list_merge_operations(project_id)
        return {'operations':items,'total':len(items)}

    @app.post('/api/projects/{project_id}/documents', status_code=201)
    def ingest(project_id: str, request: Ingest):
        return service.ingest(project_id, request.model_dump())

    @app.post('/api/projects/{project_id}/documents/upload', status_code=201)
    def upload(project_id: str, file: UploadFile = File(...), options: str = Form('{}')):
        import json
        if not file.filename or Path(file.filename).suffix.lower() not in {'.txt', '.md'}:
            raise ValueError('请上传 UTF-8 编码的 .txt 或 .md 文件')
        content = file.file.read(4_000_001)
        if len(content) > 4_000_000:
            raise ValueError('上传文件最大为 4 MB')
        try:
            text = content.decode('utf-8-sig')
            payload = json.loads(options)
            if not isinstance(payload, dict):
                raise ValueError('options 必须是 JSON 对象')
        except (UnicodeError, ValueError) as exc:
            raise ValueError('文件必须是 UTF-8 编码；options 必须是 JSON 对象') from exc
        payload.update(title=Path(file.filename).name, text=text)
        return service.ingest(project_id, Ingest.model_validate(payload).model_dump())

    @app.post('/api/projects/{project_id}/search')
    def search(project_id: str, request: Search):
        return service.search(project_id, request.model_dump())

    @app.post('/api/projects/{project_id}/qa')
    def answer(project_id: str, request: Question):
        return service.answer(project_id, request.model_dump())

    @app.post('/api/projects/{project_id}/graph')
    def graph(project_id: str, request: Scope):
        # 下面的投影只保留实体和关系，因此只读取这两类。
        rows = service.scoped(project_id, {**request.model_dump(), 'kinds': ['entity', 'relation']})
        nodes = [public(r) for r in rows if r['kind'] == 'entity']
        ids = {r['id'] for r in nodes}
        edges = [public(r) for r in rows if r['kind'] == 'relation' and r['subject_id'] in ids and r['object_id'] in ids]
        return {'nodes':nodes, 'edges':edges}

    @app.post('/api/projects/{project_id}/graph/semantica')
    def semantica_snapshot(project_id: str, request: Scope):
        from .semantica_adapter import snapshot
        rows = service.scoped(project_id, request.model_dump())
        return snapshot(rows, request.valid_at or utc_now())

    @app.get('/api/projects/{project_id}/ontologies')
    def ontology_history(project_id: str):
        return {'versions': [{**item,'summary':Ontology(item['turtle']).summary()}
                             for item in repository.list_ontologies(project_id)]}

    @app.get('/api/projects/{project_id}/ontology')
    def ontology(project_id: str, ontology_id: str | None = None):
        item=repository.get_ontology(project_id, ontology_id)
        # 已保存的摘要是缓存。重新构建，使新摘要字段引入之前创建的版本
        # 仍保持 API 兼容。
        return {**item,'summary':Ontology(item['turtle']).summary()}

    @app.post('/api/projects/{project_id}/ontologies', status_code=201)
    def save_ontology(project_id: str, request: OntologyWrite):
        parsed = Ontology(request.turtle)
        with service.lock:
            return repository.save_ontology(project_id, request.turtle, parsed.summary())

    @app.post('/api/projects/{project_id}/ontology/validate')
    def validate(project_id: str, request: Scope, ontology_id: str | None = None):
        version = repository.get_ontology(project_id, ontology_id)
        return {**Ontology(version['turtle']).validate(service.scoped(project_id, request.model_dump())), 'ontology_id':version['id']}

    @app.post('/api/projects/{project_id}/sparql')
    def sparql(project_id: str, request: Sparql):
        version = repository.get_ontology(project_id, request.ontology_id)
        return Ontology(version['turtle']).query(service.scoped(project_id, request.model_dump()), request.query)

    web = Path(__file__).parent / 'web'
    if web.exists():
        app.mount('/assets', StaticFiles(directory=web), name='assets')

        @app.get('/')
        def index():
            return FileResponse(web / 'index.html')

    return app
