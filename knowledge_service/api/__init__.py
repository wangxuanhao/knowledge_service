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
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
import httpx

from ..core.config import load_environment
from ..utils.diagnostics import redact
from ..bootstrap import (
    DocumentParseError,
    EmbeddingDimensionConfigurationError,
    MilvusStore,
    MilvusSchemaMismatch,
    Neo4jProjectionAdapter,
    VectorDimensionMismatch,
    OntologyNotPublished,
    OntologyPublicationConflict,
    Repository,
    UserStore,
    configured_encoder,
    encoder_dimension,
    install_neo4j,
    resolve_dsn,
)
from ..services.document_uploads import DocumentUploads
from ..services.ontology_drafts import OntologyDraftError
from ..services.service import KnowledgeService
from ..services.runtime_capabilities import RuntimeCapabilities
from ..services.ontology_sync import OntologySync
from ..services.projection_sync import ProjectionSync
from ..services.projection_runtime import (
    MilvusProjectionAdapter,
    ProjectionWorker,
)
from ..core.time import utc_now

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger('knowledge_service')


def _resolve_milvus_dim(encoder=None):
    """在连接 Milvus 前解析并校验向量维度。

    编码器能声明维度时，它是唯一事实来源；``KG_MILVUS_DIM`` 只做旧配置兼容
    校验，不能覆盖编码器。远端等无法自描述的编码器则必须显式给出正整数，绝不
    猜测 1024。
    """
    declared = (os.environ.get('KG_MILVUS_DIM') or '').strip()
    declared_dim = None
    if declared:
        try:
            declared_dim = int(declared)
        except ValueError as exc:
            raise EmbeddingDimensionConfigurationError(
                'KG_MILVUS_DIM 必须是正整数。') from exc
        if declared_dim <= 0:
            raise EmbeddingDimensionConfigurationError('KG_MILVUS_DIM 必须是正整数。')

    encoder_dim = encoder_dimension(encoder)
    if encoder_dim is not None:
        try:
            encoder_dim = int(encoder_dim)
        except (TypeError, ValueError) as exc:
            raise EmbeddingDimensionConfigurationError(
                '编码器声明的 embedding 维度必须是正整数。') from exc
        if encoder_dim <= 0:
            raise EmbeddingDimensionConfigurationError(
                '编码器声明的 embedding 维度必须是正整数。')
        if declared_dim is not None and declared_dim != encoder_dim:
            raise EmbeddingDimensionConfigurationError(
                f'KG_MILVUS_DIM={declared_dim} 与编码器维度 {encoder_dim} 不一致；'
                f'请把 KG_MILVUS_DIM 改成 {encoder_dim}，并显式重建已有 collection。')
        LOG.info('按编码器声明的 %s 维建/用 Milvus 索引。', encoder_dim)
        return encoder_dim
    if declared_dim is not None:
        LOG.info('编码器维度未知：按显式 KG_MILVUS_DIM=%s 建/用 Milvus 索引。', declared_dim)
        return declared_dim
    raise EmbeddingDimensionConfigurationError(
        '无法确定 embedding 维度；远端编码器请设置正整数 KG_EMBEDDING_DIMENSION'
        '（或为旧部署显式设置 KG_MILVUS_DIM），拒绝猜测 1024。')


def _build_milvus_store(encoder=None, diagnostics=None):
    """按需创建 Milvus 检索索引；未显式开启或连接失败时返回 None（不再降级到本地向量）。

    通过环境变量 KG_VECTOR_BACKEND=milvus 显式开启（默认关闭，保持向后兼容）。
    """
    if os.environ.get('KG_VECTOR_BACKEND', '') != 'milvus':
        return None
    try:
        host = os.environ.get('KG_MILVUS_HOST', 'localhost')
        port = os.environ.get('KG_MILVUS_PORT', '19530')
        dim = _resolve_milvus_dim(encoder)
        identity = getattr(encoder, 'identity', '') if encoder is not None else ''
        store = MilvusStore(
            host=host, port=port, dim=dim, encoder_identity=identity)
        store.connect()
        store.ensure_collection()
        return store
    except (VectorDimensionMismatch, MilvusSchemaMismatch) as exc:
        LOG.warning('Milvus collection 需要重建: %s', redact(exc))
        if diagnostics is not None:
            diagnostics.append(redact(exc))
        return store
    except EmbeddingDimensionConfigurationError:
        # 配置矛盾不是暂时性外部故障；静默降级会让部署看似成功、实际永不写 Milvus。
        raise
    except Exception as exc:
        # 维度不一致时这里会打印带"怎么修"的说明（VectorDimensionMismatch），
        # 而不是一句无法下手的内部错误。
        LOG.warning('Milvus 不可用，降级本地检索: %s', redact(exc))
        if diagnostics is not None:
            diagnostics.append(redact(exc))
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
    repository = Repository(dsn or resolve_dsn())
    encoder = encoder or configured_encoder()
    milvus_errors = []
    milvus_store = _build_milvus_store(encoder, diagnostics=milvus_errors)
    service = KnowledgeService(repository, encoder, milvus_store=milvus_store)
    service.vector_connector = lambda: _build_milvus_store(encoder)
    service.runtime_capabilities = RuntimeCapabilities(
        encoder=encoder,
        milvus_store=milvus_store,
        milvus_configured=os.environ.get('KG_VECTOR_BACKEND', '') == 'milvus',
        milvus_error=milvus_errors[-1] if milvus_errors else None,
    )
    document_uploads = DocumentUploads(upload_temp)

    # ── 认证基础设施：用户仓储 / JWT 密钥 / 访问令牌有效期 ──
    from ..core.security import resolve_jwt_secret
    users = UserStore(repository)
    jwt_secret, jwt_ephemeral = resolve_jwt_secret()
    if jwt_ephemeral:
        # 进程级随机密钥：零配置可用，但重启后所有人需重新登录。生产应显式设置。
        LOG.warning('未配置 KG_JWT_SECRET，已生成进程级随机签名密钥；'
                    '服务重启后现有登录将全部失效。生产环境请在 .env 设置 KG_JWT_SECRET。')
    jwt_ttl = int(os.environ.get('KG_JWT_TTL_SECONDS', '43200'))  # 默认 12 小时
    _ensure_seed_superadmin(users)

    ontology_sync = OntologySync(service,
        configured=os.environ.get('KG_VECTOR_BACKEND', '') == 'milvus')
    projection_worker = None

    @asynccontextmanager
    async def lifespan(app):
        retained_uploads = [job['payload']['staged']['path']
            for job in service.data.list_artifacts('job')
            if job.get('kind') == 'ingest_upload' and job.get('status') != 'completed'
            and job.get('payload', {}).get('staged', {}).get('path')]
        document_uploads.cleanup_old(protected_paths=retained_uploads)
        # 启动后异步预热 embedding 模型（首次 encode ~141s），不阻塞启动，消除首次检索卡顿
        import threading
        threading.Thread(target=service.warm_up, daemon=True).start()
        ontology_sync.start()
        projection_worker.start()
        app.state.jobs.start()
        app.state.ingest_batch_runner.start()
        yield
        ontology_sync.close()
        projection_worker.close()
        app.state.jobs.close()
        app.state.ingest_batch_runner.close()
        app.state.neo4j.close()
        repository.close()

    app = FastAPI(title='Knowledge Service', version='1.0.0', lifespan=lifespan,
                  description='项目本体、双时态知识与 metadata 前置过滤检索。时间区间为左闭右开。')
    app.state.service = service
    app.state.ontology_sync = ontology_sync
    app.state.document_uploads = document_uploads

    app.state.auth_disabled = bool(auth_disabled)
    @app.get('/livez')
    def livez():
        return {'status': 'alive'}

    @app.get('/readyz')
    def readyz():
        checks = {}
        try:
            checks.update(service.data.storage_readiness())
        except Exception as exc:
            checks.update(database='degraded', migrations='degraded', error=redact(exc))
        runtime = service.runtime_capabilities.snapshot()
        checks['embedding'] = 'ready' if runtime['embedding']['ready'] else 'degraded'
        required_semantic = os.environ.get('KG_VECTOR_BACKEND', '') == 'milvus'
        if required_semantic:
            checks['semantic'] = runtime['capability_matrix']['semantic']['status']
        ready = all(value == 'ready' for value in checks.values())
        return JSONResponse({'status': 'ready' if ready else 'degraded',
                             'checks': checks}, status_code=200 if ready else 503)
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
    from .source_documents import install as install_source_documents
    install_source_documents(app, service)
    from .parity import install
    install(app, service)
    install_neo4j(app, service)
    projection_adapters = {}
    cleanup_handlers = {'neo4j': app.state.neo4j.delete_project}
    if app.state.neo4j.status()['configured']:
        def neo4j_fence_is_current(project_id, seq, fence):
            cursor = repository.projections.cursor(project_id, 'neo4j') or {}
            expiry = cursor.get('claim_until')
            return bool(
                cursor.get('claim_token') == fence
                and int(cursor.get('claimed_from_seq') or 0) <= int(seq)
                <= int(cursor.get('claimed_to_seq') or -1)
                and expiry
                and datetime.fromisoformat(expiry.replace('Z', '+00:00'))
                > datetime.now(timezone.utc))

        neo4j_adapter = Neo4jProjectionAdapter(
            app.state.neo4j._driver,
            namespace=repository.storage_namespace,
            database=app.state.neo4j.database,
            fence_is_current=neo4j_fence_is_current)

        def repair_neo4j_authority(project_id, record_id, destination):
            bundle = repository.projections.authority_bundle(project_id, record_id)
            return neo4j_adapter.repair_record(
                project_id, record_id, bundle, destination,
                repository.projections.project_watermark(project_id))

        neo4j_adapter._repair_authority = repair_neo4j_authority
        projection_adapters['neo4j'] = neo4j_adapter
        service.neo4j_projection = neo4j_adapter
    if os.environ.get('KG_VECTOR_BACKEND', '') == 'milvus':
        def fence_is_current(project_id, seq, fence):
            cursor = repository.projections.cursor(project_id, 'milvus') or {}
            expiry = cursor.get('claim_until')
            return bool(
                cursor.get('claim_token') == fence
                and int(cursor.get('claimed_from_seq') or 0) <= int(seq)
                <= int(cursor.get('claimed_to_seq') or -1)
                and expiry
                and datetime.fromisoformat(expiry.replace('Z', '+00:00'))
                > datetime.now(timezone.utc))

        def repair_authority(project_id, record_id, destination):
            store = service.milvus_store
            if store is None:
                raise RuntimeError('Milvus projection store is unavailable')
            try:
                record = repository.get_record(project_id, record_id)
            except KeyError:
                return store.delete(
                    project_id, record_id=record_id, destination=destination)
            if ((record.get('metadata') or {}).get('_deleted')
                    or record.get('kind') not in {'entity', 'relation', 'chunk'}):
                return store.delete(
                    project_id, record_id=record_id, destination=destination)
            # version_id is the primary key, so an upsert alone cannot remove a
            # late worker's obsolete version. Delete the whole record lineage
            # in this destination before restoring the authoritative version.
            store.delete(project_id, record_id=record_id,
                         destination=destination)
            if record.get('embedding') is None:
                vectors = service.encoder.encode([str(record.get('text') or '')])
                if len(vectors) != 1:
                    raise RuntimeError('embedding output count mismatch')
                record = dict(record, embedding=vectors[0],
                              embedding_model=service.encoder.identity)
            return store.upsert(
                [dict(record, project_id=project_id)], flush=True,
                destination=destination,
                projection_seq=repository.projections.project_watermark(project_id),
                fence='authority-repair')

        projection_adapters['milvus'] = MilvusProjectionAdapter(
            lambda: service.milvus_store, fence_is_current=fence_is_current,
            repair_authority=repair_authority, encoder=service.encoder)

        def cleanup_milvus(project_id):
            store = service.milvus_store
            if store is None:
                raise RuntimeError('Milvus projection store is unavailable')
            return store.cleanup_project(project_id)

        cleanup_handlers['milvus'] = cleanup_milvus
    projection_sync = ProjectionSync(
        repository.projections, projection_adapters, schema_version=3)
    projection_worker = ProjectionWorker(
        repository, projection_sync, cleanup_handlers=cleanup_handlers,
        reconnect=(service.reconnect_vector_store
                   if 'milvus' in projection_adapters else None))
    service.projection_drain = projection_worker.drain_project
    service.projection_sync = projection_sync
    service.projection_worker = projection_worker
    app.state.projection_sync = projection_sync
    app.state.projection_worker = projection_worker
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
        runtime = service.runtime_capabilities.snapshot()
        return {'status': runtime['status'], 'version': '1.1.0',
                'database': service.data.backend_label, 'time_model': 'bitemporal',
                'python_executable': sys.executable,
                'capabilities': ['legacy_import', 'interactive_graph_api', 'governance', 'semantica_merge',
                                 'jobs', 'snapshots', 'dual_channel_qa', 'typed_reviews',
                                 'ontology_change_proposals', 'ontology_discovery', 'unified_provenance',
                                 'semantica_document_uploads', 'docling_ocr_ready'],
                'semantica_version': semantica, 'embedding_model': service.encoder.identity,
                'semantic': service.encoder.semantic, 'time': utc_now(),
                'embedding': runtime['embedding'], 'vector': runtime['vector'],
                'capability_matrix': runtime['capability_matrix'],
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
