"""应用装配：create_app 只负责搭骨架（lifespan/中间件/异常处理/路由挂载/静态资源）。

路由按资源拆分在 api/ 下的独立模块，每个模块提供 install(app, service)：
- projects.py          项目管理（列表/创建/查看/改名/删除）
- knowledge.py         知识 CRUD + 摄取 + 检索/问答/图谱 + 本体/SPARQL
- workspace.py         工作台 API（探索/实体选项/时间线/本体维护/索引任务）
- parity.py            旧版兼容 API（本地项目导入/预览/快照）
- evidence.py          证据定位
- reviews.py           知识审核
- ontology_changes.py  本体变更草案
- ontology_discovery.py 开放本体发现
"""
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
import httpx

from ..integrations.embeddings import configured_encoder
from ..repository import OntologyNotPublished, Repository
from ..services.service import KnowledgeService
from ..core.time import utc_now

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger('knowledge_service')


def _build_milvus_store():
    """按需创建 Milvus 检索索引；未显式开启或连接失败返回 None，降级本地 SQLite 向量。

    通过环境变量 KG_VECTOR_BACKEND=milvus 显式开启（默认关闭，保持向后兼容）。
    """
    if os.environ.get('KG_VECTOR_BACKEND', '') != 'milvus':
        return None
    try:
        from ..integrations.milvus_store import MilvusStore
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

    # ── 路由挂载（按资源拆分，统一 install(app, service) 模式）──
    from .projects import install as install_projects
    install_projects(app, service)
    from .knowledge import install as install_knowledge
    install_knowledge(app, service)
    from .parity import install
    install(app, service)
    from ..integrations.neo4j_store import install as install_neo4j
    install_neo4j(app, service)
    from .workspace import install as install_workspace
    install_workspace(app, service)
    from .evidence import install as install_evidence
    install_evidence(app, service)
    from .provenance import install as install_provenance
    install_provenance(app, service)
    from .reviews import install as install_reviews
    install_reviews(app, service)
    from .ontology_changes import install as install_ontology_changes
    install_ontology_changes(app, service)
    from .ontology_discovery import install as install_ontology_discovery
    install_ontology_discovery(app, service)

    # ── 异常处理 ──
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

    # ── 健康检查 ──
    @app.get('/api/health')
    def health():
        try:
            semantica = version('semantica')
        except PackageNotFoundError:
            semantica = None
        import sys
        return {'status': 'ok', 'version': '1.1.0', 'database': 'sqlite', 'time_model': 'bitemporal',
                'python_executable': sys.executable,
                'capabilities': ['legacy_import', 'interactive_graph_api', 'governance', 'semantica_merge',
                                 'jobs', 'snapshots', 'dual_channel_qa', 'typed_reviews',
                                 'ontology_change_proposals', 'ontology_discovery', 'unified_provenance'],
                'semantica_version': semantica, 'embedding_model': service.encoder.identity,
                'semantic': service.encoder.semantic, 'time': utc_now()}

    # ── 静态资源 ──
    web = Path(__file__).resolve().parents[1] / 'web'
    if web.exists():
        app.mount('/assets', StaticFiles(directory=web), name='assets')

        @app.get('/')
        def index():
            return FileResponse(web / 'index.html')

    return app
