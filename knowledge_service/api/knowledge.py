"""知识操作路由（/api/projects/{p}/records|documents|search|qa|graph|ontology|sparql）。

负责知识 CRUD、摄取、检索、问答、图谱、本体查询与 SPARQL。
业务逻辑全部委托 services/repository，本文件只做请求解析与响应包装。
"""
from fastapi import APIRouter, File, Form, Response, UploadFile

from ..models import (
    Ingest, OntologyWrite, Question, RecordBatch, ResolutionReviewDecision,
    Revision, Scope, Search, Sparql,
)
from ..services.ontology import Ontology
from ..services.ontology_adapters import (
    add_deprecation_headers,
    turtle_draft,
)
from ..services.service import public
from ..services import structure_pending
from ..core.time import utc_now


def install(app, service):
    router = APIRouter()
    repository = service.repository

    # ------------------------------------------------------------------ 知识 CRUD
    @router.post('/api/projects/{project_id}/records', status_code=201)
    def write(project_id: str, request: RecordBatch):
        rows = service.write(project_id, [r.model_dump(exclude_none=True) for r in request.records])
        return {'records': [public(r) for r in rows]}

    @router.get('/api/projects/{project_id}/records/{record_id}')
    def read(project_id: str, record_id: str, valid_at: str | None = None,
             known_at: str | None = None):
        row = repository.get_record(
            project_id, record_id, valid_at=valid_at, known_at=known_at)
        detail = public(row)
        # 实体详情要能看出"它属于哪个类、父类是谁"：层级在类之间，实体节点上没有，
        # 所以这里补上它类型的父类与祖先链（与 /subgraph 同一份数据：service.ontology_family）。
        if detail.get('kind') == 'entity':
            entry = service.ontology_family(project_id).get(detail.get('type') or '') or {}
            detail['class_label'] = entry.get('label') or detail.get('type') or ''
            detail['class_parents'] = entry.get('parents') or []
            detail['class_ancestors'] = entry.get('ancestors') or []
        return detail

    @router.put('/api/projects/{project_id}/records/{record_id}')
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

    @router.get('/api/projects/{project_id}/records/{record_id}/history')
    def history(project_id: str, record_id: str):
        versions = repository.history(project_id, record_id)
        if not versions:
            raise KeyError(record_id)
        return {'versions': [public(r) for r in versions]}

    @router.post('/api/projects/{project_id}/records/query')
    def query_records(project_id: str, request: Scope, limit: int = 200, offset: int = 0):
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError('limit 必须在 1..1000 且 offset 不能为负')
        records = service.scoped(project_id, request.model_dump())
        return {'total': len(records), 'records': [public(r) for r in records[offset:offset + limit]]}

    @router.get('/api/projects/{project_id}/assertions')
    def assertions(project_id: str, status: str | None = None,
                   canonical_record_id: str | None = None, document_id: str | None = None):
        items = repository.list_assertions(project_id, status=status,
            canonical_record_id=canonical_record_id, document_id=document_id)
        return {'assertions': items, 'total': len(items)}

    @router.get('/api/projects/{project_id}/ingest-runs')
    def ingest_runs(project_id: str, document_id: str | None = None):
        items = repository.list_ingest_runs(project_id, document_id=document_id)
        return {'runs': items, 'total': len(items)}

    @router.get('/api/projects/{project_id}/resolution-reviews')
    def resolution_reviews(project_id: str, status: str | None = None):
        items = repository.list_resolution_reviews(project_id, status=status)
        return {'reviews': items, 'total': len(items)}

    @router.post('/api/projects/{project_id}/resolution-reviews/{review_id}')
    def decide_resolution(project_id: str, review_id: str, request: ResolutionReviewDecision):
        review = next((item for item in repository.list_resolution_reviews(project_id)
                       if item['id'] == review_id), None)
        if review is None:
            raise KeyError(review_id)
        if request.decision == 'merged':
            from ..services.governance import Governance
            decision = {'id': review_id, 'expected_version': request.expected_version,
                'decision': 'merged', 'reason': request.reason, 'actor': request.actor}
            operation = Governance(service).merge(project_id, review['candidate_entity_id'],
                review['source_entity_id'], request.expected_entity_versions,
                resolution_decision=decision)
            return {'review': repository.list_resolution_reviews(project_id, status='merged')[-1],
                    'merge': operation}
        return {'review': repository.decide_resolution_review(project_id, review_id,
            request.expected_version, request.decision, request.reason, request.actor)}

    @router.get('/api/projects/{project_id}/merge-operations')
    def merge_operations(project_id: str):
        items = repository.list_merge_operations(project_id)
        return {'operations': items, 'total': len(items)}

    # ------------------------------------------------------------------ 摄取
    @router.post('/api/projects/{project_id}/documents', status_code=201)
    def ingest(project_id: str, request: Ingest):
        return service.ingest(project_id, request.model_dump())

    @router.post('/api/projects/{project_id}/documents/upload', status_code=201)
    def upload(project_id: str, file: UploadFile = File(...), options: str = Form('{}')):
        service.repository.get_project(project_id)
        uploads = app.state.document_uploads
        parsed_options = uploads.parse_options(options)
        staged = uploads.stage(file)
        try:
            payload, _ = uploads.payload(staged, parsed_options)
            return service.ingest(project_id, Ingest.model_validate(payload).model_dump())
        finally:
            uploads.discard(staged)

    # ------------------------------------------------------------------ 检索/问答/图谱
    @router.post('/api/projects/{project_id}/search')
    def search(project_id: str, request: Search):
        return service.search(project_id, request.model_dump())

    @router.post('/api/projects/{project_id}/qa')
    def answer(project_id: str, request: Question):
        return service.answer(project_id, request.model_dump())

    @router.post('/api/projects/{project_id}/graph')
    def graph(project_id: str, request: Scope):
        # 下面的投影只保留实体和关系，因此只读取这两类。
        rows = service.scoped(project_id, {**request.model_dump(), 'kinds': ['entity', 'relation']})
        nodes = [public(r) for r in rows if r['kind'] == 'entity']
        ids = {r['id'] for r in nodes}
        edges = [public(r) for r in rows if r['kind'] == 'relation' and r['subject_id'] in ids and r['object_id'] in ids]
        return {'nodes': nodes, 'edges': edges}

    @router.post('/api/projects/{project_id}/graph/semantica')
    def semantica_snapshot(project_id: str, request: Scope):
        from ..integrations.semantica_adapter import snapshot
        rows = service.scoped(project_id, request.model_dump())
        return snapshot(rows, request.valid_at or utc_now())

    # ------------------------------------------------------------------ 本体
    @router.get('/api/projects/{project_id}/ontologies')
    def ontology_history(project_id: str):
        # 版本管理页要回答四件事：**第几版 / 什么时候发的 / 谁发的 / 为什么发**。
        # 版本号按发布顺序现算（库里不存序号 —— 存了就会与顺序脱节）；
        # 发布说明与发布人取自版本 metadata.publication（发布时必填，见 PublishRequest.note）。
        versions = repository.list_ontologies(project_id)
        history = []
        for index, item in enumerate(versions, start=1):
            publication = (item.get('metadata') or {}).get('publication') or {}
            history.append({
                **item,
                'summary': Ontology(item['turtle']).summary(),
                'version': index,
                'note': publication.get('note') or '',
                'actor': publication.get('actor') or '',
                'draft_id': publication.get('draft_id') or '',
                'base_ontology_id': publication.get('base_ontology_id') or '',
            })
        return {'versions': history}

    @router.get('/api/projects/{project_id}/ontology')
    def ontology(project_id: str, ontology_id: str | None = None):
        item = repository.get_ontology(project_id, ontology_id)
        # 已保存的摘要是缓存。重新构建，使新摘要字段引入之前创建的版本
        # 仍保持 API 兼容。
        return {**item, 'summary': Ontology(item['turtle']).summary()}

    @router.post('/api/projects/{project_id}/ontologies', status_code=201)
    def save_ontology(project_id: str, request: OntologyWrite, response: Response):
        parsed = Ontology(request.turtle)
        with service.lock:
            versions = repository.list_ontologies(project_id)
            # Compatibility for a project explicitly created without a base
            # ontology.  Subsequent edits are always governed drafts.
            if not versions and request.expected_ontology_id is None:
                add_deprecation_headers(response)
                return repository.bootstrap_ontology(
                    project_id, request.turtle, parsed.summary(), {
                        'actor': 'api:knowledge',
                        'write_path': 'compatibility_initial_bootstrap',
                    })
            result = turtle_draft(
                repository, project_id, request.turtle, source_kind='turtle',
                title='Turtle 编辑草案', actor='api:knowledge',
                expected_ontology_id=request.expected_ontology_id,
                source_context={'legacy_route': 'POST /ontologies'},
                summary='由旧 Turtle 写接口转换')
            add_deprecation_headers(response)
            return result

    @router.post('/api/projects/{project_id}/ontology/validate')
    def validate(project_id: str, request: Scope, ontology_id: str | None = None):
        version = repository.get_ontology(project_id, ontology_id)
        rows = service.scoped(project_id, request.model_dump())
        # C1「结构待定」的记录不作为约束校验的输入：它们的类型还没进本体，
        # 校验它们只会得到一堆"未知术语"，既不是用户的问题，也会把真违规淹掉。
        # 但也不能装作没看见 —— 单独列出来并说明为什么跳过。
        checkable = [row for row in rows if not structure_pending.is_pending(row)]
        skipped = [{'id': row['id'], 'kind': row.get('kind'),
                    'terms': (row.get('structure_pending') or {}).get('terms') or []}
                   for row in rows if structure_pending.is_pending(row)]
        return {**Ontology(version['turtle']).validate(checkable),
                'ontology_id': version['id'],
                'structure_pending_skipped': skipped,
                'structure_pending_note': (
                    '这些记录的类型不在本体里（结构待定），没有参与约束校验。'
                    '去「本体建模层」补齐概念并发布后，它们会自动重新纳入校验。')
                if skipped else ''}

    @router.post('/api/projects/{project_id}/sparql')
    def sparql(project_id: str, request: Sparql):
        version = repository.get_ontology(project_id, request.ontology_id)
        return Ontology(version['turtle']).query(service.scoped(project_id, request.model_dump()), request.query)

    app.include_router(router)
