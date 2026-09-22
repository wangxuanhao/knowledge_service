"""项目管理路由（/api/projects*）。

负责项目生命周期：列表、创建、查看、改名、删除。删除会同时清理
SQLite 主表 + FTS + Milvus 分区 + Neo4j 副本（外部副本失败不阻断，只告警）。
"""
import logging
from pathlib import Path

from fastapi import APIRouter

from ..models import ProjectCreate, ProjectUpdate
from ..services.ontology import Ontology

LOG = logging.getLogger('knowledge_service')


def install(app, service):
    router = APIRouter()
    repository = service.repository

    @router.get('/api/projects')
    def projects():
        return {'projects': repository.list_projects()}

    @router.post('/api/projects', status_code=201)
    def create_project(request: ProjectCreate):
        mode = request.ontology_mode or 'ontology'
        metadata = {**request.metadata, 'ontology_mode': mode}
        p = repository.create_project(request.name, metadata)
        if request.use_default_ontology and mode == 'ontology':
            turtle = (Path(__file__).resolve().parents[1] / 'resources/default_ontology.ttl').read_text(encoding='utf-8')
            repository.save_ontology(p['id'], turtle, Ontology(turtle).summary())
        return p

    @router.get('/api/projects/{project_id}')
    def project(project_id: str):
        return repository.get_project(project_id)

    @router.put('/api/projects/{project_id}')
    def rename_project(project_id: str, request: ProjectUpdate):
        return repository.rename_project(project_id, request.name)

    @router.delete('/api/projects/{project_id}')
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

    app.include_router(router)
