"""Fenced PG delivery to project/build-scoped rows in the shared Milvus index."""
from __future__ import annotations

import logging
import threading
import time
from itertools import islice
from uuid import uuid4

from ..repository.core import drain_projection_cleanups
from ..repository.projection_store import ProjectionLeaseLost, ProjectionRebuildRequired
from ..integrations.milvus_store import MilvusSchemaMismatch


LOG = logging.getLogger('knowledge_service.projection_runtime')


class ProjectedRows:
    """Rebuild rows rehydrate the captured PG version pages on each pass."""

    def __init__(self, inputs, catalog, index_version, count):
        self.inputs, self.catalog = inputs, catalog
        self.index_version, self.count = index_version, count

    def __len__(self):
        return self.count

    def __iter__(self):
        from ..domain.search_projection import build_projection
        for value in self.inputs:
            row = build_projection(value, self.catalog, index_version=self.index_version)
            if row is not None:
                yield row


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
    """Apply frozen PG projection envelopes to one project/build scope."""

    INDEXED_KINDS = frozenset({'entity', 'relation', 'chunk'})

    def __init__(self, store, *, fence_is_current=None, encoder=None):
        self._store_source = store
        self._fence_is_current = fence_is_current
        self._encoder = encoder
        self._lock = threading.RLock()

    def _store(self):
        store = self._store_source() if callable(self._store_source) else self._store_source
        if store is None:
            raise RuntimeError('Milvus projection store is unavailable')
        return store

    def _check(self, project_id, seq, fence):
        if not isinstance(fence, str) or not fence:
            raise ValueError('Milvus projection apply requires a fence token')
        if self._fence_is_current is not None and not self._fence_is_current(project_id, seq, fence):
            raise ProjectionLeaseLost('Milvus projection lease was lost')

    def _encode(self, rows):
        if not rows:
            return []
        if self._encoder is None:
            raise RuntimeError('Milvus projection encoder is unavailable')
        vectors = self._encoder.encode([row['text'] for row in rows])
        if len(vectors) != len(rows):
            raise RuntimeError('embedding output count mismatch')
        return [dict(row, embedding=vector) for row, vector in zip(rows, vectors)]

    def apply(self, project_id, event, destination=None, fence=None, *, index_version, catalog,
              lease_is_current=None):
        # A never-built project's CRUD event contains no Milvus envelope.
        envelope = (event.get('payload') or {}).get('_milvus_projection')
        if envelope is None:
            return True
        if (envelope.get('index_version') != index_version or
                envelope.get('catalog_fingerprint') != catalog.fingerprint):
            raise ValueError('outbox 投影与冻结项目构建版本不符')
        if self._encoder is None or envelope.get('embedding_model') != self._encoder.identity:
            raise MilvusSchemaMismatch('项目构建模型与当前编码器不符，请重新向量化')
        seq = int(event['seq'])
        def current():
            if lease_is_current is not None and not lease_is_current():
                raise ProjectionLeaseLost('Milvus projection heartbeat was lost')
            self._check(project_id, seq, fence)
        with self._lock:
            current()
            if event['operation'] == 'tombstone':
                self._store().delete_records([envelope['record_id']], project_id=project_id,
                                            index_version=index_version, catalog=catalog)
            elif event['operation'] == 'upsert':
                row = envelope.get('row')
                if row is None:
                    return True
                if row['project_id'] != project_id or row['index_version'] != index_version:
                    raise ValueError('outbox 投影项目范围不符')
                # Missing/incompatible storage must fail before loading or
                # invoking a potentially expensive semantic model.
                self._store().check_collection(catalog=catalog)
                current()
                prepared = self._encode([row])
                current()
                self._store().upsert_rows(prepared, project_id=project_id,
                                         index_version=index_version, catalog=catalog)
            else:
                raise ValueError('unsupported projection operation')
            current()
            return True

    def index_configuration(self):
        from ..integrations.milvus_store import VectorDimensionMismatch
        if self._encoder is None:
            raise RuntimeError('Milvus projection encoder is unavailable')
        dimension = int(self._store().dim)
        declared = getattr(self._encoder, 'dimension', None)
        if declared is not None and int(declared) != dimension:
            raise VectorDimensionMismatch('knowledge_vectors', dimension, int(declared), self._encoder.identity)
        return {'embedding_model': self._encoder.identity, 'dim': dimension}

    def preflight(self, project_id, state, inputs):
        from ..domain.search_filter_catalog import default_catalog
        from ..domain.search_projection import build_projection
        configuration = self.index_configuration()
        if any(key in state and state[key] != value for key, value in configuration.items()):
            raise MilvusSchemaMismatch('项目构建模型已改变，需要新构建版本重新向量化')
        catalog = default_catalog()
        if state.get('catalog') and state['catalog'] != catalog.to_dict():
            raise MilvusSchemaMismatch('旧扩展字段构建不能继续，请显式重新向量化当前项目')
        from ..integrations.milvus_store import row_bytes
        if iter(inputs) is inputs:
            raise ValueError('构建输入需要可重复读取的 PG 快照页')
        count = 0
        for value in inputs:
            row = build_projection(value, catalog, index_version=state['index_version'])
            if row is not None:
                if row_bytes(row) + int(self._store().dim) * 4 + 128 > 24 * 1024 * 1024:
                    raise ValueError('平铺投影单行超过 RPC 字节预算')
                count += 1
        if self._encoder is None:
            raise RuntimeError('Milvus projection encoder is unavailable')
        state.update(catalog=catalog.to_dict(), **configuration, destination='knowledge_vectors')
        # All type/capacity checks above complete before any encoder or write RPC.
        self._store().ensure_collection(project_id=project_id,
                                        index_version=state['index_version'], catalog=catalog)
        return catalog, ProjectedRows(inputs, catalog, state['index_version'], count)

    def load_rows(self, project_id, rows, state, catalog, *, lease_is_current,
                  receipt_page=None):
        def check_lease():
            if not lease_is_current():
                raise ProjectionLeaseLost('Milvus rebuild lease was lost')
        indexed = 0
        iterator = iter(rows)
        while True:
            check_lease()
            page = list(islice(iterator, 64))
            if not page:
                break
            prepared = self._encode(page)
            check_lease()
            self._store().upsert_rows(prepared, project_id=project_id,
                                     index_version=state['index_version'], catalog=catalog)
            check_lease()
            if receipt_page is not None:
                receipt_page(page)
            indexed += len(prepared)
        return indexed

    def validate(self, project_id, state, catalog, *, expected):
        return self._store().validate_projection(expected,
                  project_id=project_id, index_version=state['index_version'], catalog=catalog)

    def retire_versions(self, project_id, versions):
        return self._store().delete_versions(project_id, versions)


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

    def drain_project(self, project_id):
        delivered = 0
        for target in self.targets:
            for _ in range(self.max_batches_per_project):
                try:
                    result = self.sync.drain_once(project_id, target)
                except (ProjectionRebuildRequired, MilvusSchemaMismatch):
                    LOG.warning('投影需要在项目页面显式重建，项目=%s，目标=%s',
                                project_id, target)
                    break
                except Exception:
                    if self.reconnect is not None:
                        self.reconnect(force=True)
                    raise
                count = int(result)
                delivered += count
                if count == 0:
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
                compact = getattr(adapter, 'audit_authority_manifest', None)
                manifest_reader = getattr(self.repository, 'projection_authority_manifest', None)
                if compact is not None and manifest_reader is not None:
                    sequence, expected = manifest_reader(project_id, adapter.INDEXED_KINDS)
                else:
                    with self.repository.read_snapshot():
                        sequence = self.repository.projections.project_watermark(project_id)
                        records = self.repository.current_records(project_id)
                def current():
                    renewed = self.repository.projections.renew_maintenance(
                        project_id, target, claim.owner, claim.token)
                    return bool(renewed and
                                self.repository.projections.maintenance_is_current(
                                    claim, sequence))
                with LeaseHeartbeat(current) as heartbeat:
                    if compact is not None and manifest_reader is not None:
                        compact(project_id, expected, sequence, heartbeat.check,
                            load_records=lambda versions: self.repository.projection_authority_records(project_id, versions),
                            before_write=lambda: heartbeat.check() and current())
                    else:
                        audit(project_id, records, sequence, heartbeat.check)
            except Exception as exc:
                try:
                    self.repository.projections.mark_failed(
                        claim, exc, self.sync.schema_version)
                except ProjectionLeaseLost:
                    pass
                raise
            finally:
                self.repository.projections.release_maintenance(claim)

    def run_once(self):
        delivered = 0
        if self.reconnect is not None:
            try:
                self.reconnect()
            except Exception:
                LOG.exception('Milvus 重连失败，后台循环稍后重试')
        projects = self.repository.list_project_directory()
        for project in projects:
            try:
                delivered += self.drain_project(project['id'])
            except Exception:
                LOG.exception('投影投递失败，项目=%s', project['id'])
        now = time.monotonic()
        if now >= self._next_audit:
            for project in projects:
                try:
                    self.audit_project(project['id'])
                except Exception:
                    LOG.exception('投影权威审计失败，项目=%s', project['id'])
            self._next_audit = now + self.audit_interval_seconds
        cleanup = drain_projection_cleanups(
            self.repository, self.cleanup_handlers, self.owner)
        maintain_generations = getattr(self.sync, 'maintain_generations', None)
        if maintain_generations is not None:
            try:
                maintain_generations()
            except Exception:
                LOG.exception('清理退役 Milvus 代际失败，后台稍后重试')
        for target in self.targets:
            try:
                if target != 'milvus':
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
