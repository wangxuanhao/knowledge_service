"""Durable ontology outbox consumer and project index reconciliation."""
from datetime import datetime, timedelta, timezone
import logging
import hashlib
import json
import threading
from uuid import uuid4

from ..core.time import utc_now
from ..utils.diagnostics import redact

LOG = logging.getLogger(__name__)


class OntologySync:
    def __init__(self, service, *, configured=False, poll_seconds=2):
        self.service = service
        self.repo = service.data
        self.configured = configured
        self.poll_seconds = poll_seconds
        self.owner = str(uuid4())
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        if self.configured:
            for task in self.repo.list_artifacts('ontology_sync_job'):
                if task.get('status') == 'disabled':
                    self.repo.control_task('ontology_sync_job', task['id'], 'retry')
        self.thread = threading.Thread(target=self._loop,
            name='ontology-outbox', daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _loop(self):
        while not self.stopped.is_set():
            if self.repo.closed:
                return
            try:
                if self.run_once():
                    continue
            except Exception:
                LOG.exception('Ontology outbox polling failed')
            self.stopped.wait(self.poll_seconds)

    def run_once(self):
        task = self.repo.claim_task('ontology_sync_job', self.owner,
                                    lease_seconds=300)
        if task is None:
            return False
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(60):
                lease = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(
                    timespec='microseconds').replace('+00:00', 'Z')
                if self.repo.update_task('ontology_sync_job', task,
                                         {'lease_until': lease}) is None:
                    return

        monitor = threading.Thread(target=heartbeat, daemon=True)
        monitor.start()
        try:
            if not self.configured:
                self.repo.update_task('ontology_sync_job', task,
                    {'status': 'disabled', 'error': 'Milvus 未启用；索引未同步'})
                return True
            store = self.service.reconnect_vector_store()
            if store is None or not store.available:
                raise RuntimeError('Milvus 已配置但不可用')
            try:
                state = self.repo.get_artifact('ontology_index_state',
                                               'ontology-index:' + task['project_id'])
            except KeyError:
                state = {}
            if state.get('status') == 'rebuilding':
                raise RuntimeError('索引重建中，发布同步等待重建完成')
            # A pending old event repairs the current authoritative projection,
            # never pushes an obsolete ontology over a newer index.
            ontology = self.repo.get_ontology(task['project_id'])
            rows = [r for r in self.repo.current_records(task['project_id'])
                    if r['kind'] in {'entity','relation','chunk'}
                    and not r.get('metadata', {}).get('_deleted')]
            signature = self._signature(rows)
            store.ensure_collection()
            for offset in range(0, len(rows), 64):
                batch = rows[offset:offset + 64]
                vectors = self.service.encoder.encode([r['text'] for r in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError('embedding 输出数量不匹配')
                for row, vector in zip(batch, vectors):
                    row.update(embedding=vector,
                               embedding_model=self.service.encoder.identity)
                # An expired worker cannot continue publishing batches after a
                # newer worker has reclaimed the event.
                if self.repo.update_task('ontology_sync_job', task, {}) is None:
                    return True
                store.upsert(batch, flush=True)
            current = self.repo.get_ontology(task['project_id'])
            latest_rows = [r for r in self.repo.current_records(task['project_id'])
                          if r['kind'] in {'entity','relation','chunk'}
                          and not r.get('metadata', {}).get('_deleted')]
            if (current['id'] != ontology['id']
                    or self._signature(latest_rows) != signature):
                raise RuntimeError('同步期间权威版本已变更，重新排队补偿')
            with self.repo.unit_of_work():
                result = self.repo.update_task('ontology_sync_job', task,
                    {'status': 'completed', 'completed_at': utc_now(), 'error': None,
                     'indexed_ontology_id': ontology['id'],
                     'embedding_model': self.service.encoder.identity,
                     'record_count': len(rows), 'record_signature': signature})
                if result is not None:
                    try:
                        state = self.repo.get_artifact('ontology_index_state',
                                                       'ontology-index:' + task['project_id'])
                    except KeyError:
                        state = {}
                    if state.get('status') == 'rebuilding':
                        raise RuntimeError('索引重建中，不能覆盖重建状态')
                    self.repo.save_artifact('ontology_index_state', {
                        'id': 'ontology-index:' + task['project_id'],
                        'project_id': task['project_id'],
                        'status': 'completed',
                        'ontology_id': ontology['id'],
                        'record_signature': signature,
                        'embedding_model': self.service.encoder.identity,
                        'updated_at': utc_now()})
        except Exception as exc:
            attempts = task['attempts']
            delay = min(300, 2 ** min(attempts, 8))
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(
                timespec='microseconds').replace('+00:00', 'Z')
            self.repo.update_task('ontology_sync_job', task, {
                'status': 'failed' if attempts >= 5 else 'retry_wait',
                'error': redact(exc), 'next_attempt_at': retry_at,
                'error_type': type(exc).__name__})
        finally:
            heartbeat_stop.set()
        return True

    def reconcile(self, project_id):
        ontology = self.repo.get_ontology(project_id)
        try:
            index = self.repo.get_artifact('ontology_index_state',
                                          'ontology-index:' + project_id)
        except KeyError:
            index = None
        rows = [r for r in self.repo.current_records(project_id)
                if r['kind'] in {'entity','relation','chunk'}
                and not r.get('metadata', {}).get('_deleted')]
        ready = bool(self.configured and self.service.milvus_store is not None
                     and self.service.milvus_store.available
                     and index and index.get('ontology_id') == ontology['id']
                     and index.get('status') != 'rebuilding'
                     and index.get('embedding_model') == self.service.encoder.identity
                     and index.get('record_signature') == self._signature(rows))
        return {'authoritative_ontology_id': ontology['id'],
                'indexed_ontology_id': index.get('ontology_id') if index else None,
                'ready': ready, 'configured': self.configured,
                'tasks': self.repo.list_artifacts('ontology_sync_job', project_id)}

    @staticmethod
    def _signature(rows):
        encoded = json.dumps(sorted((r['id'], r['version_id']) for r in rows),
                             ensure_ascii=False).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def repair(self, project_id):
        ontology = self.repo.get_ontology(project_id)
        task = {'id': 'ontology-repair:' + str(uuid4()), 'project_id': project_id,
                'ontology_id': ontology['id'], 'status': 'pending',
                'created_at': utc_now(), 'record_ids': [], 'attempts': 0}
        self.repo.save_artifact('ontology_sync_job', task)
        return task
