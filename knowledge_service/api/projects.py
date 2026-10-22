"""项目管理路由（/api/projects*）。

负责项目生命周期：列表、创建、查看、改名、删除。删除会同时清理
PostgreSQL 主表 + FTS + Milvus 分区 + Neo4j 副本（外部副本失败不阻断，只告警）。
"""
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter

from ..models import ProjectCreate, ProjectUpdate
from ..services.ontology import Ontology

LOG = logging.getLogger('knowledge_service')


def _cleanup_project_projections(app, service, repository, project_id):
    """Try cleanup now; durable events remain when a projection is unavailable."""
    cleaned = {'milvus': False, 'neo4j': False}
    owner = f'api-delete:{uuid4()}'
    targets = []
    if service.milvus_store is not None:
        targets.append(('milvus', lambda: service.milvus_store.delete(project_id)))
    targets.append(('neo4j', lambda: app.state.neo4j.delete_project(project_id)))
    for target, cleanup in targets:
        try:
            claim = repository.claim_projection_cleanup(
                project_id, target, owner)
        except Exception:
            LOG.exception('领取项目 %s 的 %s 清理事件时出错', project_id, target)
            continue
        if claim is None:
            continue
        token = claim['claim_token']
        try:
            cleanup()
            cleaned[target] = True
        except Exception as exc:
            try:
                repository.release_projection_cleanup(
                    project_id, target, owner, token, exc)
            except Exception:
                LOG.exception('记录项目 %s 的 %s 清理失败状态时出错', project_id, target)
            LOG.warning('项目删除后清理 %s 投影失败，已保留重试事件: %s', target, exc)
            continue
        try:
            repository.ack_projection_cleanup(
                project_id, target, owner, token)
        except Exception:
            # External deletion is idempotent. Keeping the event safely retries it.
            LOG.exception('确认项目 %s 的 %s 清理事件时出错', project_id, target)
    return cleaned


def install(app, service):
    router = APIRouter()
    repository = service.data

    @router.get('/api/projects')
    def projects(include_counts: bool = True):
        if not include_counts:
            with repository.independent_read_view() as reader:
                return {'projects': reader.list_project_directory()}
        return {'projects': repository.list_projects()}

    @router.post('/api/projects', status_code=201)
    def create_project(request: ProjectCreate):
        mode = request.ontology_mode or 'ontology'
        metadata = {**request.metadata, 'ontology_mode': mode}
        p = repository.create_project(request.name, metadata)
        if request.use_default_ontology and mode == 'ontology':
            turtle = (Path(__file__).resolve().parents[1] / 'resources/default_ontology.ttl').read_text(encoding='utf-8')
            repository.bootstrap_ontology(
                p['id'], turtle, Ontology(turtle).summary(),
                {'actor': 'api:projects'})
        return p

    @router.get('/api/projects/{project_id}')
    def project(project_id: str):
        return repository.get_project(project_id)

    @router.get('/api/projects/{project_id}/applicability-values')
    def applicability_values(project_id: str):
        repository.get_project(project_id)
        return repository.distinct_applicability_values(project_id)

    @router.put('/api/projects/{project_id}')
    def rename_project(project_id: str, request: ProjectUpdate):
        return repository.rename_project(project_id, request.name)

    @router.delete('/api/projects/{project_id}')
    def delete_project(project_id: str):
        deleted = repository.delete_project(project_id)
        cleaned = _cleanup_project_projections(
            app, service, repository, project_id)
        return {'deleted': deleted, 'cleaned': cleaned}

    app.include_router(router)
