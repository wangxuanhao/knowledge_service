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

from ..core.config import load_environment
from ..utils.diagnostics import redact
from ..integrations.embeddings import configured_encoder
from ..integrations.document_parser import DocumentParseError
from ..repository import (
    OntologyNotPublished,
    OntologyPublicationConflict,
    Repository,
)
from ..services.document_uploads import DocumentUploads
from ..services.ontology_drafts import OntologyDraftError
from ..services.service import KnowledgeService
from ..core.time import utc_now

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger('knowledge_service')


def _resolve_milvus_dim(encoder=None):
    """决定 Milvus 的向量维度：显式 KG_MILVUS_DIM 优先，否则问编码器。

    以前这里写死 `int(os.environ.get('KG_MILVUS_DIM', '1024'))` —— 于是换个编码器
    （例如 demo 哈希编码器是 256 维）就会出现"建表 1024、写入 256"的错配，
    而报错发生在写入时、只说"维度不对"，看不出该改哪里。
    现在维度只认一个来源：显式配置 > 编码器自己的 dimension > 1024 兜底，
    并且**配置与编码器不一致时立刻告警并说清怎么改**（不是等写入时才炸）。
    """
    declared = (os.environ.get('KG_MILVUS_DIM') or '').strip()
    from ..integrations.embeddings import encoder_dimension
    encoder_dim = encoder_dimension(encoder)
    if declared:
        dim = int(declared)
        if encoder_dim and dim != encoder_dim:
            identity = getattr(encoder, 'identity', '') if encoder is not None else ''
            LOG.warning(
                'Milvus 维度配置不一致：KG_MILVUS_DIM=%s，而当前编码器 %s 产出 %s 维。'
                '写入/检索都会失败（collection 维度建表时固定）。'
                '两种改法：把 KG_MILVUS_DIM 改成 %s 并重建索引；'
                '或换回对应维度的编码器（KG_EMBEDDING_BACKEND/模型）。',
                dim, identity or '(未知编码器)', encoder_dim, encoder_dim)
        return dim
    if encoder_dim:
        LOG.info('KG_MILVUS_DIM 未设置：按当前编码器产出的 %s 维建/用索引。', encoder_dim)
        return encoder_dim
    LOG.info('KG_MILVUS_DIM 未设置且编码器维度未知（如远端 embedding）：按默认 1024 维处理。')
    return 1024


def _build_milvus_store(encoder=None):
    """按需创建 Milvus 检索索引；未显式开启或连接失败时返回 None（不再降级到本地向量）。

    通过环境变量 KG_VECTOR_BACKEND=milvus 显式开启（默认关闭，保持向后兼容）。
    """
    if os.environ.get('KG_VECTOR_BACKEND', '') != 'milvus':
        return None
    try:
        from ..integrations.milvus_store import MilvusStore
        host = os.environ.get('KG_MILVUS_HOST', 'localhost')
        port = os.environ.get('KG_MILVUS_PORT', '19530')
        dim = _resolve_milvus_dim(encoder)
        identity = getattr(encoder, 'identity', '') if encoder is not None else ''
        store = MilvusStore(host=host, port=port, dim=dim, encoder_identity=identity)
        store.connect()
        store.ensure_collection()
        return store
    except Exception as exc:
        # 维度不一致时这里会打印带"怎么修"的说明（VectorDimensionMismatch），
        # 而不是一句无法下手的内部错误。
        LOG.warning('Milvus 不可用，降级本地检索: %s', exc)
        return None


def _ensure_seed_superadmin(users) -> None:
    """保证系统始终有一个可登录的**超级管理员**，否则没人能进入与管理服务。

    三种情况：
      1. 空库（一个用户都没有）：播种超级管理员。
         凭据来自 ``KG_SEED_ADMIN_USERNAME`` / ``KG_SEED_ADMIN_PASSWORD``，
         默认 ``admin`` / ``admin123`` —— 仅为本地开发零配置可用，故打印醒目告警，
         提醒尽快改口令；生产环境务必通过环境变量提供强口令。
      2. 已有用户、但**没有任何超级管理员**（从 0008 两档时代升级上来的老库）：
         把种子账号（默认 admin；不存在则取最早创建的管理员）**就地提升**为
         超级管理员，保留其原口令，不新建重复账号。
      3. 已存在超级管理员：什么都不做，不重复建、不覆盖既有权限。
    """
    from ..core.security import hash_password

    if users.count() == 0:
        username = (os.environ.get('KG_SEED_ADMIN_USERNAME') or 'admin').strip()
        password = (os.environ.get('KG_SEED_ADMIN_PASSWORD') or 'admin123').strip()
        users.create(username, hash_password(password), role='superadmin',
                     display_name='系统管理员')
        LOG.warning('已创建初始超级管理员账号 %s（默认口令，登录后请尽快修改）。', username)
        return

    # 老库升级：没有超级管理员时，把种子管理员提升为超级管理员。
    if users.count_active_superadmins() == 0:
        username = (os.environ.get('KG_SEED_ADMIN_USERNAME') or 'admin').strip()
        seed_row = users.get_by_username(username)
        if seed_row is None or seed_row['role'] != 'admin':
            # 种子账号不在或不是管理员：退而取最早创建的一个管理员来提升。
            managers = [u for u in users.list_users() if u['role'] == 'admin']
            if not managers:
                # 既无超级管理员也无管理员（极端脏数据）：直接补建一个超级管理员。
                password = (os.environ.get('KG_SEED_ADMIN_PASSWORD') or 'admin123').strip()
                users.create(username, hash_password(password),
                             role='superadmin', display_name='系统管理员')
                LOG.warning('未发现可提升的管理员，已补建超级管理员账号 %s。', username)
                return
            seed_row = managers[0]
        users.update(seed_row['id'], role='superadmin')
        LOG.warning('已把账号 %s 提升为超级管理员（老库权限升级）。', seed_row['username'])
        return

    _warn_if_default_password(users)


def _warn_if_default_password(users) -> None:
    """默认口令还在用就每次都告警。

    只在**没设置** ``KG_SEED_ADMIN_PASSWORD``（也就是默认值 ``admin123``）时检查：
    设置了自定义口令的项目，播种口令由环境变量保证，不必每次启动白跑一次 24 万次
    PBKDF2。命中时说明这个账号能被任何知道默认值的人登录并写入 —— 这是安全风险，
    不是提醒。
    """
    if (os.environ.get('KG_SEED_ADMIN_PASSWORD') or '').strip():
        return
    from ..core.security import verify_password
    username = (os.environ.get('KG_SEED_ADMIN_USERNAME') or 'admin').strip()
    try:
        row = users.get_by_username(username)
    except Exception:                      # 用户表还没建（未迁移）时不要卡住启动
        return
    if row is not None and verify_password('admin123', row['password_hash']):
        LOG.warning('账号 %s 仍在使用默认口令 admin123 —— 任何知道它的人都能登录。'
                    '请登录后在「用户与权限」里修改口令。', username)


def create_app(dsn=None, encoder=None, upload_temp=None, auth_disabled=None):
    """装配应用。

    第一个参数是**连接目标**：显式传入时原样转交 `Repository`（测试靠它注入
    隔离的 PostgreSQL 槽位），否则从环境解析 PostgreSQL DSN。

    刻意**不再回退**到遗留的 `KG_DATABASE` / 本地 `data/service/knowledge.sqlite`：
    那种静默回退会让人以为数据写进了 PostgreSQL，实际写到了别处（规划 §5.1）。
    缺配置时 `resolve_dsn()` 会直接抛错并说明缺哪个键，而不是悄悄建一个 SQLite 文件。

    :param auth_disabled: 是否关闭认证授权中间件。
       * None（默认）：读环境变量 ``KG_AUTH_DISABLED``，等于 ``1`` 时关闭；
       * True/False：显式决定，环境变量不再参与。
       关闭只应用于**测试进程**（conftest 设置），生产部署绝不设置该变量；
       关闭时 /api/* 与接入鉴权前行为一致。
    """
    if auth_disabled is None:
        auth_disabled = os.environ.get('KG_AUTH_DISABLED', '').strip() == '1'
    # 统一配置入口：**任何**构造应用的路径都先加载仓库根 `.env`
    # （`python -m knowledge_service`、直接 uvicorn、脚本、测试……）。
    # load_environment 只补缺失的键，不覆盖已存在的 shell 环境变量，
    # 因此重复调用是安全的、且 shell 优先级仍然最高。
    load_environment(ROOT)
    from ..repository.connection import resolve_dsn
    repository = Repository(dsn or resolve_dsn())
    encoder = encoder or configured_encoder()
    service = KnowledgeService(repository, encoder, milvus_store=_build_milvus_store(encoder))
    document_uploads = DocumentUploads(upload_temp)

    # ── 认证基础设施：用户仓储 / JWT 密钥 / 访问令牌有效期 ──
    from ..repository.users_store import UserStore
    from ..core.security import resolve_jwt_secret
    users = UserStore(repository)
    jwt_secret, jwt_ephemeral = resolve_jwt_secret()
    if jwt_ephemeral:
        # 进程级随机密钥：零配置可用，但重启后所有人需重新登录。生产应显式设置。
        LOG.warning('未配置 KG_JWT_SECRET，已生成进程级随机签名密钥；'
                    '服务重启后现有登录将全部失效。生产环境请在 .env 设置 KG_JWT_SECRET。')
    jwt_ttl = int(os.environ.get('KG_JWT_TTL_SECONDS', '43200'))  # 默认 12 小时
    _ensure_seed_superadmin(users)

    def ontology_sync_runner(job):
        if service.milvus_store is None:
            return
        record_ids = set(job.get('record_ids') or [])
        if not record_ids:
            return
        records = [
            row for row in repository.current_records(job['project_id'])
            if row['id'] in record_ids
            and row['kind'] in {'entity', 'relation', 'chunk'}]
        if not records:
            return
        vectors = service.encoder.encode([row['text'] for row in records])
        for row, vector in zip(records, vectors):
            row['embedding'] = vector
            row['embedding_model'] = service.encoder.identity
            row['project_id'] = job['project_id']
        service.milvus_store.upsert(records, flush=True)

    repository._ontology_sync_runner = ontology_sync_runner

    @asynccontextmanager
    async def lifespan(app):
        document_uploads.cleanup_old()
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
    app.state.document_uploads = document_uploads

    # ── 认证 / 授权：默认挂载真实中间件；测试进程显式 auth_disabled 时跳过 ──
    if not auth_disabled:
        from .security_middleware import install_auth_middleware
        install_auth_middleware(app, users=users, jwt_secret=jwt_secret)
    from .auth import install as install_auth
    install_auth(app, users=users, jwt_secret=jwt_secret, ttl_seconds=jwt_ttl)
    # 用户管理（列表 / 建号 / 改角色 / 停启用 / 重置口令 / 删除）。
    # "是否管理员" 的访问策略写在 security_middleware 的 _MANAGER_ONLY_PATTERNS 里，
    # 三级角色之间的越权与超级管理员保护写在 api/users.py —— 两处分工不重复。
    from .users import install as install_users
    install_users(app, users=users)

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
    from .ontology_drafts import install as install_ontology_drafts
    install_ontology_drafts(app, service)

    # ── 异常处理 ──
    @app.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={'detail': f'未找到：{exc.args[0]}'})

    @app.exception_handler(OntologyNotPublished)
    async def ontology_missing(request: Request, exc: OntologyNotPublished):
        return JSONResponse(status_code=404, content={
            'detail': '本项目尚未发布本体。可先持续开放发现并累计候选，再在本体发现中生成、审核和发布本体版本；仅文档检索模式不产生图谱本体。',
            'code': 'ontology_not_published'})

    @app.exception_handler(OntologyDraftError)
    async def ontology_draft_error(request: Request, exc: OntologyDraftError):
        status = 409 if exc.code in {
            'revision_conflict', 'stale_base', 'stale_source',
            'validation_changed',
        } else 422
        return JSONResponse(status_code=status, content={
            'detail': str(exc), 'code': exc.code, 'details': exc.details})

    @app.exception_handler(OntologyPublicationConflict)
    async def ontology_publication_error(
            request: Request, exc: OntologyPublicationConflict):
        status = 422 if exc.code in {
            'validation_failed', 'batch_not_allowed',
        } else 409
        return JSONResponse(status_code=status, content={
            'detail': str(exc), 'code': exc.code, 'details': exc.details})

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse(status_code=409 if '版本冲突' in str(exc) else 422, content={'detail': str(exc)})

    @app.exception_handler(DocumentParseError)
    async def document_parse_error(request: Request, exc: DocumentParseError):
        return JSONResponse(status_code=exc.status_code, content={
            'detail': {'code': exc.code, 'message': exc.message},
            'code': exc.code,
            'message': exc.message,
        })

    @app.exception_handler(ValidationError)
    async def invalid_model(request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={'detail': str(exc)})

    @app.exception_handler(RuntimeError)
    async def unavailable(request: Request, exc: RuntimeError):
        LOG.warning('操作不可用：%s', type(exc).__name__)
        return JSONResponse(status_code=503, content={'detail': str(exc)})

    @app.exception_handler(httpx.HTTPError)
    async def provider_error(request: Request, exc: httpx.HTTPError):
        # 带上底层原因（经 redact 脱敏）：只回异常类型无法区分
        # 「模型名不存在」「鉴权失败」「网络超时」，用户无法自查。
        return JSONResponse(status_code=503,
                            content={'detail': f'模型提供商不可用：{redact(exc)}'})

    # ── 健康检查 ──
    @app.get('/api/health')
    def health():
        try:
            semantica = version('semantica')
        except PackageNotFoundError:
            semantica = None
        import sys
        return {'status': 'ok', 'version': '1.1.0',
                'database': service.repository.backend_label, 'time_model': 'bitemporal',
                'python_executable': sys.executable,
                'capabilities': ['legacy_import', 'interactive_graph_api', 'governance', 'semantica_merge',
                                 'jobs', 'snapshots', 'dual_channel_qa', 'typed_reviews',
                                 'ontology_change_proposals', 'ontology_discovery', 'unified_provenance',
                                 'semantica_document_uploads', 'docling_ocr_ready'],
                'semantica_version': semantica, 'embedding_model': service.encoder.identity,
                'semantic': service.encoder.semantic, 'time': utc_now(),
                # 公开字段 auth_enabled：告诉前端本进程是否真的启用了鉴权，
                # 前端只在**确知启用**时才要求先登录；测试桩 / KG_AUTH_DISABLED=1 的
                # 起法没这个字段或为 false，按"不等"处理，免得页面永远拉不到数据。
                'auth_enabled': not auth_disabled}

    # ── 静态资源 ──
    web = Path(__file__).resolve().parents[1] / 'web'
    if web.exists():
        app.mount('/assets', StaticFiles(directory=web), name='assets')

        @app.get('/')
        def index():
            return FileResponse(web / 'index.html')

        # 独立登录页：未登录 / 令牌失效时前端跳到这里。与主页分开，不加载业务脚本。
        @app.get('/login')
        def login_page():
            return FileResponse(web / 'login.html')

    return app
