"""Restartable jobs use persisted inputs rather than request closures."""
from pathlib import Path
from time import perf_counter

from ..models import Ingest, UploadOptions
from ..utils.diagnostics import reporting
from .document_uploads import StagedUpload


def job_handlers(service, uploads):
    def classified_failure(exc):
        from .ingest_retry import classify_error
        error=classify_error(exc)
        failure=RuntimeError(error.message)
        failure.code=error.code
        failure.retryable=error.retryable
        failure.stage=error.stage
        return failure

    def ingest(payload, project_id, progress, task_id):
        existing = [r for r in service.data.current_records(project_id)
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
                raise classified_failure(exc) from exc

    def upload(payload, project_id, progress, task_id):
        if any(r['kind'] == 'document'
               and r.get('metadata', {}).get('durable_task_id') == task_id
               and r.get('metadata', {}).get('status') == 'ready'
               for r in service.data.current_records(project_id)):
            return ingest({}, project_id, progress, task_id)
        path = Path(payload['staged']['path']).resolve()
        if not path.is_relative_to(uploads.root.resolve()):
            raise ValueError('上传任务文件不在受控暂存目录')
        staged = StagedUpload(path, payload['staged']['original_name'],
                              payload['staged']['size'], payload['staged']['digest'])
        progress('正在解析上传文档', 10)
        with reporting(progress):
            try:result = uploads.ingest(service,project_id,staged,UploadOptions.model_validate(payload['options']),task_id=task_id)
            except Exception as exc:
                from ..integrations.document_parser import DocumentParseError
                if isinstance(exc,DocumentParseError):raise
                raise classified_failure(exc) from exc
        uploads.discard(staged)
        return result

    def semantic_index(payload, project_id, progress, task_id):
        start = perf_counter()
        store = service.reconnect_vector_store()
        if store is None:
            raise RuntimeError('未配置或无法连接 Milvus，索引未建立')
        if not hasattr(service, 'projection_sync'):
            raise RuntimeError('Milvus projection coordinator is unavailable')
        progress('校验所选项目并向量化当前记录', 10)
        result = service.projection_sync.rebuild(project_id, 'milvus')
        total = int(result['indexed'])
        progress('已激活所选项目向量索引', 98)
        return {'indexed': total, 'backend': 'milvus', 'total': total,
                'scope': 'project', 'restored_projects': [project_id],
                'recreated': False, 'destination': result['destination'],
                'index_version': result['index_version'],
                'dim': store.dim,
                'embedding_model': service.encoder.identity,
                'elapsed_seconds': round(perf_counter() - start, 2)}

    return {'ingest': ingest, 'ingest_upload': upload,
            'semantic_index': semantic_index}
