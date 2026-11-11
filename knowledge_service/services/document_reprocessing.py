"""Create explicit, auditable processing generations from a document's frozen source."""
from copy import deepcopy

from ..models import IngestBatchSettings


class DocumentReprocessingError(ValueError):
    def __init__(self, code, message, status_code=422, details=None):
        super().__init__(message)
        self.code, self.status_code, self.details = code, status_code, details or {}


class DocumentReprocessing:
    def __init__(self, service, batches, sources):
        self.service, self.batches, self.sources = service, batches, sources
        self.store = batches.store

    def create(self, project_id, document_id, extraction_mode, idempotency_key, actor=None):
        self.service.repository.get_project(project_id)
        with self.store.document_reprocessing_guard(project_id, document_id):
            document = self.service.repository.get_record(project_id, document_id)
            if document.get('kind') != 'document' or document.get('metadata', {}).get('_deleted'):
                raise KeyError(document_id)
            previous = self.store.reprocessing_operation(project_id, document_id, idempotency_key)
            if previous:
                if previous['batch']['settings'].get('extraction_mode') != extraction_mode:
                    raise DocumentReprocessingError('reprocess_idempotency_conflict',
                        '此请求编号已用于其他处理方式，请刷新后重新选择。', 409)
                return previous
            active = self.store.active_document_item(project_id, document_id)
            if active:
                raise DocumentReprocessingError('document_reprocessing_active',
                    '此资料已有未结束的处理任务，请先完成或取消该任务。', 409,
                    {'batch_id': active['batch_id'], 'item_id': active['id']})
            metadata = deepcopy(document.get('metadata') or {})
            receipt_item = self.store.document_item(project_id, document_id, metadata.get('batch_item_id'))
            if metadata.get('status') == 'processing' and receipt_item is None:
                raise DocumentReprocessingError('document_reprocessing_active',
                    '此资料的原上传任务仍在处理中，请先完成或取消该任务。', 409)
            task_id = metadata.get('durable_task_id')
            if task_id:
                try:
                    task = self.service.repository.get_artifact('job', task_id)
                except KeyError:
                    task = None
                if (task and task.get('project_id') == project_id
                        and task.get('status') in ('queued', 'running')):
                    raise DocumentReprocessingError('document_reprocessing_active',
                        '此资料的原上传任务仍未结束，请先完成或取消该任务。', 409,
                        {'job_id': task_id})
            version_id = metadata.get('source_document_version_id')
            if not version_id:
                raise DocumentReprocessingError('reprocess_source_missing',
                    '此旧资料没有关联固定原始来源，请重新上传原文件并选择处理方式。')
            source = self.sources.get(project_id, version_id)
            if source['document_id'] != document_id:
                raise KeyError('Source version does not belong to this document')
            settings = self.store.latest_document_settings(project_id, document_id)
            settings = {key: value for key, value in settings.items() if key in IngestBatchSettings.model_fields}
            # Preserve the source's scope and business time, dropping previous execution outputs.
            for field in ('status', 'active_stage', 'readiness', 'run_id', 'warnings', 'batch_id',
                          'batch_item_id', 'uploaded_at', 'indexed_at', 'reprocessing',
                          'discovery_candidates', 'review_candidates', 'durable_task_id',
                          'retry_of_documents', 'error_code', 'public_error', 'error_type',
                          'failed_at', 'validation_error_count', 'validation_errors'):
                metadata.pop(field, None)
            metadata['reprocessing'] = {'kind': 'document_reprocessing', 'extraction_mode': extraction_mode,
                'source_version_id': version_id, 'previous_document_version_id': document['version_id']}
            settings.update(extraction_mode=extraction_mode, requested_concurrency=1, metadata=metadata,
                applicability=metadata.get('applicability'), published_at=metadata.get('published_at'),
                valid_from=document.get('valid_from'), valid_until=document.get('valid_until'), ontology_id=None)
            if extraction_mode == 'ontology':
                settings['ontology_id'] = self.service.repository.get_ontology(project_id)['id']
            settings = IngestBatchSettings.model_validate(settings).model_dump()
            batch = self.batches.create(project_id, settings, actor)
            item = self.store.add_existing_source(project_id, batch['id'], document_id, version_id,
                idempotency_key, options={'title': metadata.get('title') or source['original_name']},
                generation_floor=document.get('metadata', {}).get('document_generation', 0))
            batch = self.batches.control(project_id, batch['id'], 'start')
            return {'batch': batch, 'item': item}
