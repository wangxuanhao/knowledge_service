"""Production delivery loop for durable external projections.

The current Milvus adapter deliberately provides only the guarantees available
with the existing single collection: version-id upserts are idempotent, and a
process-local sequence/fence watermark rejects obvious late delivery.  The
database claim is renewed immediately before every apply by :class:`ProjectionSync`.

Task 4 must persist ``projection_seq`` and the fence in each new Milvus
destination and validate them before an alias switch.  Until then this module
does not claim cross-process atomic compare-and-set semantics in Milvus.
"""
from __future__ import annotations

import logging
import threading
import time
from uuid import uuid4

from ..repository.core import drain_projection_cleanups
from ..repository.projection_store import ProjectionRebuildRequired
from ..integrations.milvus_store import MilvusSchemaMismatch


LOG = logging.getLogger('knowledge_service.projection_runtime')


class LeaseHeartbeat:
    """Renew a maintenance lease while encoding or an external RPC blocks."""

    def __init__(self, renew, *, interval_seconds=10.0, waiter=None):
        self.renew = renew
        self.interval_seconds = min(10.0, max(0.01, float(interval_seconds)))
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._waiter = waiter or self._stop.wait
        self._thread = None

    def __enter__(self):
        if not self.renew():
            self._lost.set()
            return self
        self._thread = threading.Thread(
            target=self._run, name='projection-maintenance-heartbeat',
            daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._waiter(self.interval_seconds):
            try:
                if not self.renew():
                    self._lost.set()
                    return
            except Exception:
                self._lost.set()
                return

    def check(self):
        return not self._lost.is_set()

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1.0)


class MilvusProjectionAdapter:
    """Apply ordered outbox events to the existing Milvus store."""

    INDEXED_KINDS = frozenset({'entity', 'relation', 'chunk'})

    def __init__(self, store, *, fence_is_current=None, repair_authority=None,
                 encoder=None):
        self._store_source = store
        self._fence_is_current = fence_is_current
        self._repair_authority = repair_authority
        self._encoder = encoder
        self._lock = threading.RLock()
        self._positions = {}
        self._expected = {}

    def _store(self):
        store = (self._store_source() if callable(self._store_source)
                 else self._store_source)
        if store is None:
            raise RuntimeError('Milvus projection store is unavailable')
        return store

    def apply(self, project_id, event, destination=None, fence=None):
        seq = int(event['seq'])
        if not isinstance(fence, str) or not fence:
            raise ValueError('Milvus projection apply requires a fence token')
        store = self._store()
        selected = destination
        if selected is None:
            resolver = getattr(store, 'current_destination', None)
            selected = resolver(project_id) if resolver is not None else None
        selected = selected or getattr(store, 'collection_name', 'active')
        key = (project_id, str(selected))
        with self._lock:
            if (self._fence_is_current is not None
                    and not self._fence_is_current(project_id, seq, fence)):
                return False
            position = self._positions.get(key)
            if position is not None and seq <= position[0]:
                # A failed batch is retried from its unacknowledged start,
                # possibly under a new lease. The local prefix was recorded
                # only after its write, marker and fence check succeeded.
                # Skip it without rewriting newer state, but only when the
                # authoritative claim verifies this retry's fence and range.
                return self._fence_is_current is not None
            operation = event.get('operation')
            payload = dict(event.get('payload') or {})
            if operation == 'upsert':
                if payload.get('kind') in self.INDEXED_KINDS:
                    if payload.get('embedding') is None:
                        if self._encoder is None:
                            raise RuntimeError(
                                'indexed Milvus projection record has no embedding')
                        vectors = self._encoder.encode([str(payload.get('text') or '')])
                        if len(vectors) != 1:
                            raise RuntimeError('embedding output count mismatch')
                        payload['embedding'] = vectors[0]
                        payload['embedding_model'] = self._encoder.identity
                        if (self._fence_is_current is not None
                                and not self._fence_is_current(
                                    project_id, seq, fence)):
                            return False
                    payload['project_id'] = project_id
                    try:
                        store.upsert([payload], flush=True, destination=selected,
                                     projection_seq=seq, fence=fence)
                    except TypeError as exc:
                        # Compatibility for lightweight adapters used by callers/tests.
                        if 'unexpected keyword argument' not in str(exc):
                            raise
                        store.upsert([payload], flush=True)
            elif operation == 'tombstone':
                record_id = payload.get('record_id')
                if not record_id:
                    raise ValueError('Milvus tombstone requires record_id')
                try:
                    store.delete(project_id, record_id=record_id,
                                 destination=selected)
                except TypeError as exc:
                    if 'unexpected keyword argument' not in str(exc):
                        raise
                    store.delete(project_id, record_id=record_id)
            else:
                raise ValueError(f'unsupported projection operation: {operation}')
            marker = getattr(store, 'write_fence_marker', None)
            if marker is not None:
                marker(project_id, selected, seq, fence)
            if (self._fence_is_current is not None
                    and not self._fence_is_current(project_id, seq, fence)):
                if self._repair_authority is None:
                    raise RuntimeError('Milvus projection fence expired after write')
                self._repair_authority(
                    project_id, payload.get('id') or payload.get('record_id'),
                    selected)
                return False
            expected = self._expected.get((project_id, str(selected)))
            if expected is not None:
                record_id = payload.get('id') or payload.get('record_id')
                if operation == 'tombstone':
                    expected.discard(record_id)
                elif payload.get('kind') in self.INDEXED_KINDS:
                    expected.add(record_id)
            self._positions[key] = (seq, fence)
            return True

    def current_destination(self, project_id):
        store = self._store()
        resolver = getattr(store, 'current_destination', None)
        if resolver is not None:
            return resolver(project_id)
        return getattr(store, 'collection_name', 'active')

    def begin_rebuild(self, project_id, schema_version, state, fence=None):
        store = self._store()
        destination = store.generation_name(project_id, state['build_id'])
        store.ensure_collection(destination=destination)
        return destination

    def reset_rebuild(self, project_id, destination, build_id, fence=None):
        with self._lock:
            self._store().recreate_collection(destination=destination)
            self._expected.pop((project_id, destination), None)
            self._positions.pop((project_id, str(destination)), None)

    def _prepare_records(self, project_id, records, projection_seq, fence,
                         heartbeat=None):
        prepared = []
        missing = []
        for record in records:
            if record.get('kind') not in self.INDEXED_KINDS:
                continue
            row = dict(record, project_id=project_id,
                       projection_seq=int(projection_seq), fence=fence)
            if row.get('embedding') is None:
                missing.append(row)
            prepared.append(row)
        if missing:
            if self._encoder is None:
                raise RuntimeError('indexed Milvus snapshot record has no embedding')
            for offset in range(0, len(missing), 64):
                if heartbeat is not None and not heartbeat():
                    raise RuntimeError('projection maintenance lease was lost')
                batch = missing[offset:offset + 64]
                vectors = self._encoder.encode(
                    [str(row.get('text') or '') for row in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError('embedding output count mismatch')
                for row, vector in zip(batch, vectors):
                    row['embedding'] = vector
                    row['embedding_model'] = self._encoder.identity
        return prepared

    def load_snapshot(self, project_id, destination, records, snapshot_seq,
                      fence=None):
        prepared = self._prepare_records(
            project_id, records, snapshot_seq, fence or '')
        for offset in range(0, len(prepared), 256):
            self._store().upsert(
                prepared[offset:offset + 256], flush=True,
                destination=destination, projection_seq=snapshot_seq,
                fence=fence or '')
        marker = getattr(self._store(), 'write_fence_marker', None)
        if marker is not None:
            marker(project_id, destination, snapshot_seq, fence or '')
        self._expected[(project_id, destination)] = {
            row['id'] for row in prepared}
        return len(prepared)

    def validate_rebuild(self, project_id, destination, fence=None, state=None):
        store = self._store()
        store.ensure_collection(destination=destination)
        validator = getattr(store, 'validate_destination', None)
        state = dict(state or {})
        expected_ids = list((state.get('expected_versions') or {}).values())
        expected = set(expected_ids) if 'expected_versions' in state else None
        if validator is None:
            return {'ok': expected is not None, 'expected_count': len(expected or ())}
        return validator(
            project_id, destination,
            expected_count=(None if expected is None else len(expected)),
            expected_version_ids=(None if expected is None else expected_ids),
            expected_projection=state.get('expected_projection'),
            expected_digest=state.get('expected_digest'), fence=fence)

    def switch_alias(self, project_id, destination, build_id=None, fence=None):
        return self._store().switch_alias(project_id, destination)

    def audit_authority(self, project_id, records, projection_seq,
                        lease_is_current):
        store = self._store()
        destination = self.current_destination(project_id)
        if destination is None:
            return {'checked': 0, 'repaired': 0}
        expected = {str(row['id']): str(row['version_id']) for row in records
                    if row.get('kind') in self.INDEXED_KINDS
                    and not (row.get('metadata') or {}).get('_deleted')}
        repair_ids = store.authority_repair_ids(
            project_id, destination, expected, heartbeat=lease_is_current)
        if not lease_is_current():
            return {'aborted': True, 'repaired': 0}
        selected = [row for row in records if str(row.get('id')) in repair_ids]
        prepared = self._prepare_records(
            project_id, selected, projection_seq, 'authority-audit',
            heartbeat=lease_is_current)
        if not lease_is_current():
            return {'aborted': True, 'repaired': 0}
        result = store.apply_authority_repairs(
            project_id, destination, prepared, repair_ids,
            projection_seq=projection_seq, before_write=lease_is_current)
        if not lease_is_current():
            return {'aborted': True, **result}
        store.write_fence_marker(
            project_id, destination, projection_seq, 'authority-audit')
        return {'aborted': False, **result}

    def retire(self, project_id, destination):
        current = self.current_destination(project_id)
        if destination is None or destination == current:
            return False
        self._expected.pop((project_id, destination), None)
        return self._store().drop_destination(destination)


class ProjectionWorker:
    """One bounded, lease-safe loop shared by startup and write-through nudges."""

    def __init__(self, repository, sync, *, cleanup_handlers,
                 interval_seconds=2.0, max_batches_per_project=10,
                 reconnect=None, audit_interval_seconds=60.0):
        self.repository = repository
        self.sync = sync
        self.cleanup_handlers = dict(cleanup_handlers)
        self.interval_seconds = float(interval_seconds)
        self.max_batches_per_project = int(max_batches_per_project)
        self.reconnect = reconnect
        self.audit_interval_seconds = float(audit_interval_seconds)
        self._next_audit = 0.0
        self.owner = f'projection-worker:{uuid4()}'
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.thread = None

    @property
    def targets(self):
        return tuple(self.sync.adapters)

    def resume_pending(self):
        for target in self.targets:
            try:
                self.sync.resume_pending(target)
            except Exception:
                LOG.exception('恢复 %s 投影重建失败，后台循环稍后重试', target)

    def drain_project(self, project_id):
        delivered = 0
        for target in self.targets:
            for _ in range(self.max_batches_per_project):
                try:
                    result = self.sync.drain_once(project_id, target)
                except (ProjectionRebuildRequired, MilvusSchemaMismatch):
                    self.sync.rebuild(project_id, target)
                    break
                except Exception:
                    if self.reconnect is not None:
                        try:
                            self.reconnect(force=True)
                        except TypeError:
                            self.reconnect()
                    raise
                count = int(result)
                delivered += count
                if count == 0:
                    readiness = getattr(self.repository, 'projection_ready', None)
                    if (getattr(result, 'status', 'empty') == 'empty'
                            and not getattr(result, 'active_lease', False)
                            and readiness is not None
                            and not readiness(project_id, target,
                                              self.sync.schema_version)):
                        self.sync.rebuild(project_id, target)
                    break
        return delivered

    def audit_project(self, project_id):
        for target, adapter in self.sync.adapters.items():
            audit = getattr(adapter, 'audit_authority', None)
            if audit is None:
                continue
            claim = self.repository.projections.claim_maintenance(
                project_id, target, self.sync.schema_version, self.owner)
            if claim is None:
                continue
            try:
                with self.repository.read_snapshot():
                    sequence = self.repository.projections.project_watermark(
                        project_id)
                    records = self.repository.current_records(
                        project_id, vectors='none')
                def current():
                    renewed = self.repository.projections.renew_maintenance(
                        project_id, target, claim.owner, claim.token)
                    return bool(renewed and
                                self.repository.projections.maintenance_is_current(
                                    claim, sequence))
                with LeaseHeartbeat(current) as heartbeat:
                    audit(project_id, records, sequence, heartbeat.check)
            finally:
                self.repository.projections.release_maintenance(claim)

    def run_once(self):
        delivered = 0
        if self.reconnect is not None:
            try:
                self.reconnect()
            except Exception:
                LOG.exception('Milvus 重连失败，后台循环稍后重试')
        for project in self.repository.list_projects():
            try:
                delivered += self.drain_project(project['id'])
            except Exception:
                LOG.exception('投影投递失败，项目=%s', project['id'])
        now = time.monotonic()
        if now >= self._next_audit:
            for project in self.repository.list_projects():
                try:
                    self.audit_project(project['id'])
                except Exception:
                    LOG.exception('投影权威审计失败，项目=%s', project['id'])
            self._next_audit = now + self.audit_interval_seconds
        cleanup = drain_projection_cleanups(
            self.repository, self.cleanup_handlers, self.owner)
        for target in self.targets:
            try:
                self.sync.maintain_rollbacks(target)
            except Exception:
                LOG.exception('维护 %s 投影回滚窗口失败', target)
        return {'delivered': delivered, 'cleanup': cleanup}

    def _run(self):
        while not self._stop.wait(self.interval_seconds):
            try:
                self.run_once()
            except Exception:
                LOG.exception('投影后台循环失败')

    def start(self):
        with self._lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.resume_pending()
            self._stop.clear()
            self.thread = threading.Thread(
                target=self._run, name='projection-worker', daemon=True)
            self.thread.start()

    def close(self):
        with self._lock:
            thread = self.thread
            if thread is None:
                return
            self._stop.set()
        thread.join(timeout=max(1.0, min(self.interval_seconds + 1.0, 5.0)))
        with self._lock:
            if self.thread is thread:
                self.thread = None


__all__ = ['LeaseHeartbeat', 'MilvusProjectionAdapter', 'ProjectionWorker']
