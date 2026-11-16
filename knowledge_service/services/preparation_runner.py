"""Bounded parse workers with PostgreSQL leases and independent heartbeats."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import hashlib
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from uuid import uuid4

from ..integrations.document_parser import DocumentParser
from ..integrations.document_package import SingleDocumentPackageReader
from .preparations import fingerprint, recognition_error
from .source_ingest import prepare_source_ingest
from .ingest_retry import classify_error

LOG=logging.getLogger(__name__)


class CachedDocumentParser(DocumentParser):
    """Converters are isolated to one worker thread and one OCR configuration."""
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.converters={}

    def _new_docling_parser(self,extension=None):
        if extension not in self.converters:
            self.converters[extension]=super()._new_docling_parser(extension)
        return self.converters[extension]


class PreparationRunner:
    def __init__(self,operations,limit=1,ocr_semaphore=None):
        self.operations=operations
        self.store=operations.store
        self.sources=operations.sources
        self.limit=max(1,limit)
        self.pool=ThreadPoolExecutor(max_workers=self.limit,thread_name_prefix='preparation-parse')
        self.stop=threading.Event()
        self.thread=None
        self.active={}
        self.local=threading.local()
        self.lock=threading.RLock()
        self.ocr_semaphore=ocr_semaphore
        self.timeout=float(os.getenv('KNOWLEDGE_PREPARATION_TIMEOUT_SECONDS','3600'))
        self.last_cleanup=0

    def start(self):
        if self.thread:return
        self.thread=threading.Thread(target=self._loop,daemon=True,name='preparation-dispatch')
        self.thread.start()

    def _loop(self):
        while not self.stop.is_set():
            try:self.run_once()
            except Exception:
                if not self.stop.is_set():LOG.exception('Preparation dispatcher failed')
            self.stop.wait(.25)

    def run_once(self):
        with self.lock:
            if self.stop.is_set():return
            now=time.monotonic()
            for identifier,(future,task,started) in list(self.active.items()):
                if future.done():
                    del self.active[identifier]
                elif now-started>self.timeout:
                    self.store.update(task,'failed','timeout',error='解析超过服务端时间上限，请重试或拆分原文件')
                else:self.store.heartbeat(task)
            if now-self.last_cleanup>60:
                if self.store.cleanup():self.sources.collect_unreferenced_objects()
                self.last_cleanup=now
            while len(self.active)<self.limit:
                task=self.store.claim()
                if not task:break
                self.active[task['id']]=(self.pool.submit(self._run,task),task,time.monotonic())

    def _guard(self,task,stage=None):
        if self.stop.is_set() or not self.store.heartbeat(task,stage):raise InterruptedError('parse lease cancelled or expired')
        current=fingerprint(task['options']['_file_sha256'],task['original_name'],task['options'].get('parse_config',{}))
        if current!=task['fingerprint']:raise ValueError('解析器或模型配置已变化，请重新开始解析')

    def _run(self,task):
        try:
            data=self.store.file(task['file_id'])
            task['options']['_file_sha256']=hashlib.sha256(data).hexdigest()
            self._guard(task,'parsing')
            config=task['options'].get('parse_config',{})
            mode=config.get('ocr_mode',os.getenv('KG_DOCUMENT_OCR_MODE','auto'))
            runtime=fingerprint('','',config)
            if getattr(self.local,'runtime',None)!=runtime:
                self.local.parser=CachedDocumentParser(ocr_mode=mode,max_upload_bytes=self.operations.batches.limits.max_file_bytes)
                from .source_documents import SourceDocumentService
                from ..integrations.image_ocr import RapidOCRAdapter
                from ..integrations.video_asr import FunASRAdapter
                ocr=self.sources.image_ocr.adapter
                asr=self.sources.video_asr.adapter
                self.local.sources=SourceDocumentService(self.operations.service.repository,self.sources.object_store,
                    ocr_adapter=RapidOCRAdapter() if type(ocr) is RapidOCRAdapter else ocr,
                    asr_adapter=FunASRAdapter() if type(asr) is FunASRAdapter else asr,
                    remote_image_importer=self.sources.remote_image_importer)
                self.local.runtime=runtime
            parser=self.local.parser
            sources=self.local.sources
            if Path(task['original_name']).suffix.lower()=='.zip':
                reader=SingleDocumentPackageReader(parser=parser)
                reader.validate_upload(data)
                package=reader.read(data,task['original_name'])
                prepared=sources.prepare_package(data,task['original_name'],package['documents'][0],package.get('root_prefix',''))
            else:
                with tempfile.TemporaryDirectory(prefix='preparation-') as directory:
                    path=Path(directory)/('source'+Path(task['original_name']).suffix.lower())
                    path.write_bytes(data)
                    slot=self.ocr_semaphore if path.suffix=='.pdf' and mode=='auto' and self.ocr_semaphore is not None else nullcontext()
                    with slot:
                        self._guard(task,'parsing')
                        parsed=parser.parse(path,task['original_name'])
                prepared=sources.prepare(data,task['original_name'],text=parsed.text,assets=parsed.assets,metadata=parsed.metadata,provenance=parsed.provenance)
            self._guard(task,'preparing_source')
            prepared['parsed'].setdefault('metadata',{})['preparation_task_id']=task['id']
            source=sources.publish(task['project_id'],'preparation-'+task['id'],prepared)
            self._guard(task,'preparing_text')
            def progress():
                media='transcribing' if Path(task['original_name']).suffix.lower() in ('.mp4','.webm') else 'ocr'
                self._guard(task,media)
            parse_options={key:value for key,value in task['options'].items() if not key.startswith('_') and key!='parse_config'}
            parse_options['title']=parse_options.get('title') or task['original_name']
            parse_options['parse_config']={'ocr_mode':mode}
            # Preparation produces reviewable content regardless of the later
            # knowledge extraction mode. Recognition failures block publication.
            parse_options['extraction_mode']='documents'
            chunks,text,warnings=prepare_source_ingest(sources,task['project_id'],source,parse_options,
                ocr_semaphore=self.ocr_semaphore,progress=progress,strict_media=True)
            self._guard(task,'saving_result')
            error=recognition_error({'warnings':warnings})
            if error:
                from ..integrations.document_parser import DocumentParseError
                raise DocumentParseError(error['code'],error.get('message') or '附件识别未完成，请重试')
            if not text.strip():
                from ..integrations.document_parser import DocumentParseError
                error=next((w for w in warnings if isinstance(w,dict) and w.get('code','').startswith(('ocr_','asr_')) and w.get('code')!='ocr_disabled'),None)
                if error:raise DocumentParseError(error['code'],error.get('message') or '识别未完成，请检查模型后重试')
                raise ValueError('未识别到可审核的正文，请检查原件或识别模型')
            result=dict(text=text,chunks=chunks,warnings=warnings,source_version_id=source['id'],
                chunk_settings={k:parse_options[k] for k in ('chunk_size','chunk_overlap','chunk_strategy')})
            encoder = getattr(self.operations.service, 'document_encoder', None)
            if encoder is not None and getattr(encoder, 'semantic', False):
                from ..domain.search_projection import document_heading
                # Publication clones source_file to the uploaded container name,
                # including ZIP; use that same heading for reusable vectors.
                heading = document_heading(parse_options['title'], task['original_name'])
                try:
                    for start in range(0, len(chunks), 64):
                        self._guard(task, 'vectorizing')
                        encoder.encode([heading + '\n' + chunk['text'] for chunk in chunks[start:start + 64]])
                    result['vectorization'] = {'status': 'ready', 'model': encoder.identity,
                        'chunks': len(chunks), 'heading': heading}
                except InterruptedError:
                    raise
                except Exception as exc:
                    from ..utils.diagnostics import redact
                    result['vectorization'] = {'status': 'failed', 'model': encoder.identity,
                        'error': redact(exc)}
                    LOG.warning('切片向量预计算失败，提交后由索引任务重试: %s', redact(exc))
            else:
                result['vectorization'] = {'status': 'unavailable'}
            self._guard(task, 'saving_result')
            self.store.update(task,'completed','completed',result=result)
        except InterruptedError:
            self.store.acknowledge_cancel(task)
        except Exception as exc:
            from ..utils.diagnostics import redact
            LOG.warning('Preparation parse failed (%s): %s',type(exc).__name__,redact(exc))
            public=classify_error(exc)
            message=public.message
            from ..integrations.document_parser import DocumentParseError
            if isinstance(exc,DocumentParseError):message=exc.message
            from ..integrations.image_ocr import OCRError
            from ..integrations.video_asr import ASRError
            if isinstance(exc,(OCRError,ASRError)):message=str(exc)
            if isinstance(exc,ValueError) and '未识别' in str(exc):message=str(exc)
            self.store.update(task,'failed','failed',error=message)

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=3)
        with self.lock:
            for future,task,started in self.active.values():self.store.interrupt(task)
        self.pool.shutdown(wait=False,cancel_futures=True)
