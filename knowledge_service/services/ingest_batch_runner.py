"""Application-owned worker pool, lease heartbeat and durable retries."""
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
import logging
import os
import threading
from uuid import uuid4

from .document_ingest_runner import DocumentIngestRunner
from .ingest_retry import classify_error, retry_delay, should_pause

LOG = logging.getLogger(__name__)


class IngestBatchRunner:
    def __init__(self, service, store, checkpoints, sources, *, document_limit=4, llm_limit=2,
                 ocr_limit=1, lease_seconds=120, clock=None, file_limit=25_000_000):
        self.service, self.store = service, store
        self.document_limit, self.lease_seconds = document_limit, lease_seconds
        self.owner = 'batch-' + uuid4().hex
        self.clock = clock or (lambda:datetime.now(timezone.utc))
        self.pool = ThreadPoolExecutor(max_workers=document_limit, thread_name_prefix='ingest-document')
        self.llm_semaphore = threading.BoundedSemaphore(llm_limit)
        self.ocr_semaphore = threading.BoundedSemaphore(ocr_limit)
        service.llm_semaphore = self.llm_semaphore
        self.active = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.document_runner = DocumentIngestRunner(service,store,checkpoints,sources,self.ocr_semaphore,self.stop,file_limit)
        self.thread = None

    def _stopped(self):
        if getattr(getattr(self.store,'repo',None),'closed',False):
            self.stop.set()
            self.pool.shutdown(wait=False,cancel_futures=True)
        return self.stop.is_set()

    def start(self):
        if self._stopped():
            return
        if self.thread is not None:
            return
        self.store.recover_expired()
        self.thread = threading.Thread(target=self._loop, name='ingest-dispatch', daemon=True)
        self.thread.start()

    def _loop(self):
        while not self._stopped():
            try:
                self.run_once()
            except Exception:
                if self._stopped():
                    break
                LOG.exception('Batch dispatcher failed')
            self.stop.wait(1)

    def run_once(self):
        with self.lock:
            if self._stopped():return
            for item_id,(future,item) in list(self.active.items()):
                if future.done():
                    del self.active[item_id]
                else:
                    self.store.heartbeat(item_id,self.owner,item['lease_token'],self.lease_seconds)
            self.store.recover_expired()
            if self._stopped():
                return
            for batch in self.store.running_batches():
                while len(self.active)<self.document_limit:
                    if self._stopped():return
                    item=self.store.claim(batch['id'],self.owner,self.lease_seconds)
                    if item is None:
                        break
                    future=self.pool.submit(self._run,item,batch['settings'])
                    self.active[item['id']]=(future,item)

    def _run(self,item,settings):
        try:
            if self._stopped():return
            result=self.document_runner.run(item,settings,self.owner)
            if self._stopped():return
            self.store.finish(item['id'],self.owner,item['lease_token'],'succeeded',
                result=result, stage='completed', progress=100)
        except Exception as exc:
            if self._stopped():return
            error=classify_error(exc)
            if self.store.lease_valid(item['id'],self.owner,item['lease_token']):
                try:self.document_runner.mark_failure(item,self.owner,error)
                except (ValueError,RuntimeError):pass
            count=item['auto_retry_count']
            delay=retry_delay(count) if error.retryable else None
            status='retry_wait' if delay is not None else 'failed'
            next_time=(self.clock()+timedelta(seconds=delay)).isoformat() if delay is not None else None
            saved=self.store.finish(item['id'],self.owner,item['lease_token'],status,
                stage=error.stage,error_code=error.code,error_message=error.message,public_error=error.message,
                failed_chunk_ordinal=getattr(exc,'failed_chunk_ordinal',None),
                auto_retry_count=count+(1 if delay is not None else 0), next_retry_at=next_time)
            if saved:
                observations=self.store.recent_outcomes(item['batch_id'])
                if should_pause(observations,
                    minimum=max(5,int(os.getenv('KNOWLEDGE_INGEST_CIRCUIT_MINIMUM','5'))),
                    ratio=float(os.getenv('KNOWLEDGE_INGEST_CIRCUIT_RATIO','.6'))):
                    self.store.pause_for_error(item['project_id'],item['batch_id'],error.code)
        finally:
            if not self._stopped():
                self.store.release_paused(item['id'],self.owner,item['lease_token'])

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        # Wait while retaining the repository and heartbeat, so in-flight commits stay fenced.
        with self.lock:
            pending=[future for future,item in self.active.values() if not future.done()]
        if pending:
            wait(pending,timeout=float(os.getenv('KNOWLEDGE_INGEST_SHUTDOWN_SECONDS','5')))
        try:
            if not getattr(getattr(self.store,'repo',None),'closed',False):
                self.store.interrupt_owner(self.owner)
        finally:
            self.pool.shutdown(wait=False,cancel_futures=True)
