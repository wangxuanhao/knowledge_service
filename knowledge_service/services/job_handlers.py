"""Restartable jobs use persisted inputs rather than request closures."""
from pathlib import Path
from time import perf_counter

from ..models import Ingest, UploadOptions
from ..utils.diagnostics import reporting
from .document_uploads import StagedUpload


def job_handlers(service, uploads):
    def ingest(payload, project_id, progress, task_id):
        existing = [r for r in service.data.current_records(project_id, vectors='none')
                    if r['kind'] == 'document'
                    and r.get('metadata', {}).get('durable_task_id') == task_id]
        complete = next((r for r in existing
                         if r.get('metadata', {}).get('status') == 'ready'), None)
        if complete:
            return {'document': complete, 'replayed': True,
                    'records': [r for r in service.data.current_records(project_id)
                                if r.get('source_id') == complete['id']]}
        data = dict(payload)
        data['metadata'] = {**data.get('metadata', {}), 'durable_task_id': task_id,
                            'retry_of_documents': [r['id'] for r in existing]}
        with reporting(progress):
            try:
                return service.ingest(project_id, Ingest.model_validate(data).model_dump())
            except Exception as exc:
                from .ingest_retry import classify_error
                error=classify_error(exc)
                failure=RuntimeError(error.message)
                failure.code=error.code
                failure.retryable=error.retryable
                failure.stage=error.stage
                raise failure from exc

    def upload(payload, project_id, progress, task_id):
        if any(r['kind'] == 'document'
               and r.get('metadata', {}).get('durable_task_id') == task_id
               and r.get('metadata', {}).get('status') == 'ready'
               for r in service.data.current_records(project_id, vectors='none')):
            return ingest({}, project_id, progress, task_id)
        path = Path(payload['staged']['path']).resolve()
        if not path.is_relative_to(uploads.root.resolve()):
            raise ValueError('上传任务文件不在受控暂存目录')
        staged = StagedUpload(path, payload['staged']['original_name'],
                              payload['staged']['size'], payload['staged']['digest'])
        progress('正在解析上传文档', 10)
        data, _ = uploads.payload(staged, UploadOptions.model_validate(payload['options']))
        result = ingest(data, project_id, progress, task_id)
        uploads.discard(staged)
        return result

    def legacy(payload, project_id, progress, task_id):
        from .legacy_import import LegacyImporter
        return LegacyImporter(service).import_project(
            payload['name'], payload['kind'], progress=progress,
            build_vectors=payload['build_vectors'], project_id=payload.get('project_id'))

    def semantic_index(payload, project_id, progress, task_id):
        start = perf_counter()
        store = service.reconnect_vector_store()
        if store is None:
            raise RuntimeError('未配置或无法连接 Milvus，索引未建立')
        if not hasattr(service, 'projection_sync'):
            raise RuntimeError('Milvus projection coordinator is unavailable')
        progress('创建隔离的 Milvus 索引代并加载快照', 10)
        result = service.projection_sync.rebuild(project_id, 'milvus')
        total = len([r for r in service.data.current_records(project_id)
                     if r['kind'] in {'entity', 'relation', 'chunk'}
                     and not r.get('metadata', {}).get('_deleted')])
        progress('切换 Milvus 项目别名', 98)
        return {'indexed': total, 'backend': 'milvus', 'total': total,
                'scope': 'project', 'restored_projects': [project_id],
                'recreated': True, 'destination': result['destination'],
                'dim': store.dim,
                'embedding_model': service.encoder.identity,
                'elapsed_seconds': round(perf_counter() - start, 2)}

    return {'ingest': ingest, 'ingest_upload': upload, 'legacy_import': legacy,
            'semantic_index': semantic_index}
