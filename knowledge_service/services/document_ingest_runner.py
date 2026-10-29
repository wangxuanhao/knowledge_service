"""One leased document, immutable source and raw slice receipts."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile

from ..models import Ingest
from ..integrations.document_parser import DocumentParser
from .chunking import split_source_blocks, split_mapped_chunks
from .ingest_checkpoints import IngestCheckpoints


class DocumentCheckpointContext:
    def __init__(self, store, checkpoints, item, owner, sources, chunks, halt=None, mode=None):
        self.store, self.checkpoints, self.item = store, checkpoints, item
        self.owner, self.sources, self.chunks = owner, sources, chunks
        self.file_hash = item['file_sha256']
        self.ontology_fingerprint = None
        self.halt=halt
        self.expected_active_ontology=None
        self.ontology_checked=False
        self.mode=mode
        self.runtime_identity=self._runtime_identity()

    def _runtime_identity(self):
        root=Path(__file__).resolve().parents[1]
        versions=['integrations/markdown_parser.py','services/chunking.py','services/image_ocr.py']
        settings=()
        if self.mode!='documents':
            versions.append('integrations/semantica_adapter.py')
            settings=tuple(os.getenv(name,'') for name in ('KG_LLM_MODEL','KG_LLM_BASE_URL','KG_LLM_MAX_TOKENS'))
        return settings,tuple((name,(root/name).stat().st_mtime_ns,(root/name).stat().st_size) for name in versions)

    def guard(self):
        if self.halt is not None and self.halt.is_set():
            raise InterruptedError('ingest worker shutdown')
        if self._runtime_identity()!=self.runtime_identity:
            error=ValueError('ingest configuration changed during execution')
            error.code='checkpoint_incompatible'
            raise error
        if not self.store.lease_valid(self.item['id'], self.owner, self.item['lease_token']):
            raise ValueError('stale ingest lease')

    def commit_guard(self):
        return self.store.commit_guard(self.item['id'], self.owner, self.item['lease_token'])

    @contextmanager
    def formal_guard(self):
        self.guard()
        with self.commit_guard():
            if self.ontology_checked:
                from ..repository import OntologyNotPublished
                try:active=self.store.repo.get_ontology(self.item['project_id'])['id']
                except OntologyNotPublished:active=None
                if active!=self.expected_active_ontology:
                    error=ValueError('ontology changed during extraction')
                    error.code='ontology_version_changed'
                    raise error
            yield
            self.guard()

    def execute(self, ordinal, fingerprint, extract):
        self.guard()
        old = self.checkpoints.get(self.item['document_id'], ordinal, fingerprint)
        if old:
            from copy import deepcopy
            return deepcopy(old['output'])
        if self.checkpoints.has_other_configuration(self.item['document_id'],ordinal,
                self.item.get('document_generation',0),fingerprint):
            error=ValueError('checkpoint configuration changed')
            error.code='checkpoint_incompatible'
            raise error
        if (self.item.get('retry_mode')=='retry_failed_chunks'
                and ordinal!=self.item.get('failed_chunk_ordinal')):
            error=ValueError('未完成的切片需要从检查点继续')
            error.code='checkpoint_pending'
            raise error
        try:
            output = extract()
        except Exception as exc:
            exc.failed_chunk_ordinal = ordinal
            raise
        if self.halt is not None and self.halt.is_set():
            raise InterruptedError('ingest worker shutdown')
        with self.store.checkpoint_guard(self.item['id'],self.owner,self.item['lease_token']):
            self.checkpoints.put(self.item['document_id'], ordinal, fingerprint, output,
                batch_item_id=self.item['id'], attempt=self.item['attempt'],
                document_generation=self.item.get('document_generation',0),
                chunk_sha256=hashlib.sha256(self.chunks[ordinal]['text'].encode()).hexdigest())
            if self.store.lease_valid(self.item['id'],self.owner,self.item['lease_token']):
                self.store.update_progress(self.item['id'], self.owner, self.item['lease_token'],
                    stage='extracting', progress=15 + 60 * (ordinal + 1) / len(self.chunks))
        from copy import deepcopy
        return deepcopy(output)

    def map_chunks(self, project_id, saved):
        for row, chunk in zip(saved, self.chunks):
            if chunk.get('source_spans'):
                self.sources.store.add_spans(project_id, self.item['source_version_id'],
                    row['version_id'], chunk['source_spans'])


class DocumentIngestRunner:
    def __init__(self, service, store, checkpoints, sources, ocr_semaphore=None, halt=None, file_limit=25_000_000):
        self.service, self.store, self.checkpoints, self.sources = service, store, checkpoints, sources
        self.ocr_semaphore = ocr_semaphore
        self.halt=halt
        self.file_limit=file_limit

    def run(self, item, settings, owner):
        if self.halt is not None and self.halt.is_set():raise InterruptedError('ingest worker shutdown')
        self.store.update_progress(item['id'], owner, item['lease_token'], stage='parsing', progress=3)
        options = {**settings, **(item.get('options') or {})}
        options.pop('requested_concurrency', None)
        title = options.pop('title', None) or item['original_name']
        if item.get('source_version_id'):
            source = self.sources.get(item['project_id'], item['source_version_id'])
        else:
            source=self._parse_source(item)
            item['source_version_id'] = source['id']
            self.store.update_progress(item['id'], owner, item['lease_token'],
                source_version_id=source['id'], source_document_id=item['document_id'])
        # A failed OCR still has a document receipt and a source-reader entry point.
        from ..core.ingest_context import ingest_commit_guard
        provisional=DocumentCheckpointContext(self.store,self.checkpoints,item,owner,self.sources,[],self.halt,options.get('extraction_mode'))
        guard_token=ingest_commit_guard.set(provisional.formal_guard)
        try:
            with self.service.repository._formal_write_transaction():
                existing=self._ensure_receipt(item,source,options,title)
        finally:
            ingest_commit_guard.reset(guard_token)
        if (existing and existing.get('metadata',{}).get('status')=='ready'
                and existing.get('metadata',{}).get('document_generation',0)==item.get('document_generation',0)):
            return {'document':existing,'records':[],'replayed':True}
        from .source_ingest import prepare_source_ingest
        chunks,text,warnings=prepare_source_ingest(self.sources,item['project_id'],source,options,
            ocr_semaphore=self.ocr_semaphore,progress=lambda:self.store.update_progress(item['id'],owner,item['lease_token'],stage='ocr',progress=8))
        metadata = {**options.pop('metadata', {}),**{k:v for k,v in source.get('parser_metadata',{}).items() if k!='provenance'},'warnings':warnings,'source_document_version_id':source['id'],
                    'batch_id':item['batch_id'], 'batch_item_id':item['id'], 'file_sha256':item['file_sha256'],
                    'document_generation':item.get('document_generation',0)}
        payload = Ingest.model_validate(dict(options, title=title, text=text, metadata=metadata)).model_dump()
        context = DocumentCheckpointContext(self.store,self.checkpoints,item,owner,self.sources,chunks,self.halt,payload.get('extraction_mode'))
        payload.update(_document_id=item['document_id'], _checkpoint_context=context, _prepared_chunks=chunks)
        token=ingest_commit_guard.set(context.formal_guard)
        try:
            return self.service.ingest(item['project_id'], payload)
        finally:
            ingest_commit_guard.reset(token)

    def _ensure_receipt(self,item,source,options,title):
        # Formal table locks precede the lease lock; keep one order across document workers.
        try:document=self.service.repository.get_record(item['project_id'],item['document_id'])
        except KeyError:document=None
        generation=item.get('document_generation',0)
        if (document and document.get('metadata',{}).get('status')=='ready'
                and document.get('metadata',{}).get('document_generation',0)==generation):
            return document
        if document is None or document.get('metadata',{}).get('source_document_version_id')!=source['id']:
            return self.service.repository.put_record(item['project_id'],dict(id=item['document_id'],
                kind='document',text=source['source_text'],metadata={
                    **options.get('metadata',{}),'title':title,'status':'processing','active_stage':'ocr',
                    'document_generation':generation,'source_document_version_id':source['id'],
                    'batch_id':item['batch_id'],'batch_item_id':item['id']}),expected_version=document['version'] if document else 0)
        return document

    def _parse_source(self,item):
        if item.get('source_restart_of'):
            previous=self.sources.source(item['project_id'],item['source_restart_of'])
            if previous.get('package_data'):
                from ..integrations.markdown_package import MarkdownPackageReader
                package=MarkdownPackageReader().read(previous['package_data'])
                entry=next(row for row in package['documents'] if row['path']==item['original_name'])
                return self.sources.persist(item['project_id'],item['document_id'],previous['package_data'],
                    item['original_name'],text=entry['text'],assets=entry['assets'])
        data=item.get('content')
        if data is None:
            obj=self.sources.store.get_upload_object(item['project_id'],item['id'])
            data=self.sources.object_store.read(obj['storage_key'])
            if len(data)!=obj['size_bytes'] or hashlib.sha256(data).hexdigest()!=obj['sha256']:
                raise ValueError('Upload object checksum or size mismatch')
            if (item.get('options') or {}).get('metadata',{}).get('package_entry_path'):
                from ..integrations.markdown_package import MarkdownPackageReader
                package=MarkdownPackageReader().read(data)
                entry=next(row for row in package['documents'] if row['path']==item['original_name'])
                return self.sources.persist(item['project_id'],item['document_id'],data,
                    item['original_name'],text=entry['text'],assets=entry['assets'])
        with tempfile.TemporaryDirectory(prefix='knowledge-batch-') as directory:
            path=Path(directory)/('document'+Path(item['original_name']).suffix)
            path.write_bytes(data)
            parsed=DocumentParser(max_upload_bytes=self.file_limit).parse(path,item['original_name'])
        return self.sources.persist(item['project_id'],item['document_id'],data,
            item['original_name'],text=parsed.text,assets=parsed.assets,metadata=parsed.metadata,provenance=parsed.provenance)

    def mark_failure(self,item,owner,error):
        from ..core.ingest_context import ingest_commit_guard
        context=DocumentCheckpointContext(self.store,self.checkpoints,item,owner,self.sources,[],self.halt)
        token=ingest_commit_guard.set(context.formal_guard)
        try:
            with self.service.repository._formal_write_transaction():
                try:document=self.service.repository.get_record(item['project_id'],item['document_id'])
                except KeyError:return
                if document.get('metadata',{}).get('status') in ('ready','failed'):return
                record=dict(id=document['id'],kind='document',text=document['text'],
                    metadata={**document.get('metadata',{}),'status':'failed','active_stage':error.stage,
                        'error_code':error.code,'public_error':error.message})
                self.service.repository.put_record(item['project_id'],record,expected_version=document['version'])
        finally:
            ingest_commit_guard.reset(token)
