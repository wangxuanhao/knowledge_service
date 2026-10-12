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
            return service.ingest(project_id, Ingest.model_validate(data).model_dump())

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
        from ..integrations.milvus_store import VectorDimensionMismatch
        from ..core.time import utc_now
        from .ontology_sync import OntologySync
        from ..repository import OntologyNotPublished

        def ontology_id(target):
            try:
                return service.data.get_ontology(target)['id']
            except OntologyNotPublished:
                # Discovery projects may have searchable chunks before their
                # first ontology publication; they still share the collection.
                return None
        start = perf_counter()
        store = service.reconnect_vector_store()
        if store is None:
            raise RuntimeError('未配置或无法连接 Milvus，索引未建立')
        recreated = False
        try:
            store.ensure_collection()
        except VectorDimensionMismatch:
            recreated = True
        # The collection is shared. Persist the whole migration scope BEFORE
        # recreation; retry must restore other projects even if the new schema
        # now matches and the original process died midway through encoding.
        unfinished = [s for s in service.data.list_artifacts('ontology_index_state')
                      if s.get('status') == 'rebuilding'
                      and s.get('scope') == 'collection']
        collection_scope = recreated or bool(unfinished)
        targets = ([p['id'] for p in service.data.list_projects()]
                   if collection_scope else [project_id])
        with service.data.unit_of_work():
            for target in targets:
                service.data.save_artifact('ontology_index_state', {
                    'id': 'ontology-index:' + target, 'project_id': target,
                    'status': 'rebuilding', 'scope': 'collection' if collection_scope else 'project',
                    'rebuild_task_id': task_id, 'updated_at': utc_now()})
        if recreated:
            progress('迁移共享 Milvus 集合；恢复全部项目', 5)
            store.recreate_collection()
        counts = {}
        for target in targets:
            version = ontology_id(target)
            rows = [r for r in service.data.current_records(target)
                    if r['kind'] in {'entity','relation','chunk'}
                    and not r.get('metadata', {}).get('_deleted')]
            signature = OntologySync._signature(rows)
            encoded = []
            for offset in range(0, len(rows), 16):
                progress(f'{target}: 向量编码 {offset}/{len(rows)}', int(90*offset/max(1,len(rows))))
                batch = rows[offset:offset + 16]
                vectors = service.encoder.encode([r['text'] for r in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError('embedding 输出数量不匹配')
                for row, vector in zip(batch, vectors):
                    row.update(embedding=vector, embedding_model=service.encoder.identity,
                               project_id=target)
                encoded.extend(batch)
            progress(f'{target}: 写入 Milvus', 95)
            saved = store.rebuild_project(target, encoded)
            latest = [r for r in service.data.current_records(target)
                      if r['kind'] in {'entity','relation','chunk'}
                      and not r.get('metadata', {}).get('_deleted')]
            if (ontology_id(target) != version
                    or OntologySync._signature(latest) != signature):
                raise RuntimeError('重建期间权威版本已变更，请重试补偿')
            counts[target] = (saved, len(rows))
            progress(f'{target}: 校验索引收据', 98)
            service.data.save_artifact('ontology_index_state', {
                'id': 'ontology-index:' + target, 'project_id': target,
                'status': 'completed', 'ontology_id': version,
                'record_signature': signature, 'embedding_model': service.encoder.identity,
                'updated_at': utc_now()})
        saved, total = counts[project_id]
        return {'indexed': saved, 'backend': 'milvus', 'total': total,
                'scope': 'collection' if collection_scope else 'project',
                'restored_projects': targets,
                'recreated': recreated, 'dim': store.dim,
                'embedding_model': service.encoder.identity,
                'elapsed_seconds': round(perf_counter() - start, 2)}

    return {'ingest': ingest, 'ingest_upload': upload, 'legacy_import': legacy,
            'semantic_index': semantic_index}
