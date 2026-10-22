import pytest
import threading

from knowledge_service.services.projection_runtime import (
    LeaseHeartbeat,
    MilvusProjectionAdapter,
    ProjectionWorker,
)
from knowledge_service.services.service import KnowledgeService
from knowledge_service.services.projection_sync import DrainResult
from knowledge_service.repository.projection_store import ProjectionRebuildRequired


class RecordingMilvus:
    collection_name = 'knowledge_records'

    def __init__(self):
        self.upserts = []
        self.deletes = []

    def check_destination(self, *, destination):
        return self

    def upsert(self, records, flush=True, *, destination, projection_seq, fence):
        self.upserts.append((list(records), flush))
        return len(records)

    def delete(self, project_id, record_id=None, *, destination):
        self.deletes.append((project_id, record_id))
        return 1


def event(seq, operation='upsert', record_id='record'):
    payload = {
        'id': record_id, 'version_id': f'version-{seq}', 'kind': 'chunk',
        'text': 'text', 'embedding': [1.0], 'embedding_model': 'test',
        'metadata': {},
    }
    if operation == 'tombstone':
        payload = {'record_id': record_id, 'version_id': f'version-{seq}'}
    return {'seq': seq, 'operation': operation,
            'version_id': f'version-{seq}', 'payload': payload}


def test_milvus_adapter_applies_events_idempotently_and_rejects_late_sequences():
    store = RecordingMilvus()
    adapter = MilvusProjectionAdapter(store)

    assert adapter.apply('project', event(2), fence='lease-new') is True
    assert adapter.apply('project', event(2), fence='lease-retry') is False
    assert adapter.apply('project', event(1), fence='lease-old') is False
    assert adapter.apply(
        'project', event(3, 'tombstone'), fence='lease-next') is True

    assert store.upserts == [([{
        **event(2)['payload'], 'project_id': 'project',
    }], True)]
    assert store.deletes == [('project', 'record')]


def test_milvus_adapter_resolves_a_store_reconnected_after_startup():
    current = {'store': None}
    adapter = MilvusProjectionAdapter(lambda: current['store'])

    try:
        adapter.apply('project', event(1), fence='lease-one')
    except RuntimeError as exc:
        assert 'unavailable' in str(exc)
    else:
        raise AssertionError('an unavailable store must leave the outbox unacked')

    current['store'] = RecordingMilvus()
    assert adapter.apply('project', event(1), fence='lease-two') is True
    assert len(current['store'].upserts) == 1


def test_milvus_adapter_repairs_authority_when_persistent_fence_is_lost_after_write():
    store = RecordingMilvus()
    current = {'valid': True}
    repaired = []

    def upsert_then_lose_fence(records, flush=True, *, destination, projection_seq, fence):
        store.upserts.append((list(records), flush))
        current['valid'] = False
        return len(records)

    store.upsert = upsert_then_lose_fence
    adapter = MilvusProjectionAdapter(
        store,
        fence_is_current=lambda project, seq, fence: current['valid'],
        repair_authority=lambda project, record, destination: repaired.append(
            (project, record, destination)))

    assert adapter.apply('project', event(4), fence='expired-lease') is False
    assert repaired == [('project', 'record', 'knowledge_records')]


def test_tombstone_writes_persistent_fence_marker():
    class MarkedMilvus(RecordingMilvus):
        def __init__(self):
            super().__init__()
            self.markers = []
        def write_fence_marker(self, project, destination, seq, fence):
            self.markers.append((project, destination, seq, fence))

    store = MarkedMilvus()
    assert MilvusProjectionAdapter(store).apply(
        'project', event(8, 'tombstone'), fence='claim-8')
    assert store.markers == [
        ('project', 'knowledge_records', 8, 'claim-8')]


def test_milvus_adapter_never_acknowledges_an_indexed_record_without_embedding():
    adapter = MilvusProjectionAdapter(RecordingMilvus())
    missing = event(5)
    missing['payload'].pop('embedding')
    with pytest.raises(RuntimeError, match='no embedding'):
        adapter.apply('project', missing, fence='claim')


class FakeRepository:
    def __init__(self):
        self.cleanup_acks = []
        self.directory_calls = 0

    def list_projects(self):
        raise AssertionError('projection worker must not load full project statistics')

    def list_project_directory(self):
        self.directory_calls += 1
        return [{'id': 'p1'}, {'id': 'p2'}]

    def claim_projection_cleanups(self, owner, limit=100):
        return [{
            'project_id': 'gone', 'target': 'milvus',
            'claim_owner': owner, 'claim_token': 'cleanup-token',
        }]

    def ack_projection_cleanup(self, project_id, target, owner, token):
        self.cleanup_acks.append((project_id, target, owner, token))
        return True

    def release_projection_cleanup(self, *args):
        raise AssertionError('cleanup must succeed')


class FakeSync:
    def __init__(self):
        self.adapters = {'milvus': object()}
        self.resumed = []
        self.drained = []
        self.maintained = []

    def resume_pending(self, target=None):
        self.resumed.append(target)
        return []

    def drain_once(self, project_id, target):
        self.drained.append((project_id, target))
        return 0

    def maintain_rollbacks(self, target=None):
        self.maintained.append(target)
        return 0


def test_projection_worker_leaves_schema_mismatch_for_explicit_rebuild():
    class RebuildingSync(FakeSync):
        def __init__(self):
            super().__init__()
            self.rebuilt = []
        def drain_once(self, project_id, target):
            raise ProjectionRebuildRequired(
                project_id, target, current_schema=2, required_schema=3)
        def rebuild(self, project_id, target):
            self.rebuilt.append((project_id, target))

    sync = RebuildingSync()
    ProjectionWorker(FakeRepository(), sync, cleanup_handlers={}).drain_project('p1')
    assert sync.rebuilt == []


def test_projection_worker_never_rebuilds_while_another_worker_holds_lease():
    class LeasedSync(FakeSync):
        schema_version = 3
        def __init__(self):
            super().__init__(); self.rebuilt = []
        def drain_once(self, project_id, target):
            return DrainResult('leased', 0, True)
        def rebuild(self, project_id, target):
            self.rebuilt.append((project_id, target))
    class ReadyAware(FakeRepository):
        def projection_ready(self, *args): return False

    sync = LeasedSync()
    ProjectionWorker(ReadyAware(), sync, cleanup_handlers={}).drain_project('p1')
    assert sync.rebuilt == []


def test_authority_audit_lost_lease_stops_before_encoding_or_writes():
    class AuditStore:
        def current_destination(self, project): return 'generation'
        def authority_repair_ids(self, project, destination, expected,
                                 heartbeat=None):
            assert heartbeat()
            return {'r'}
        def apply_authority_repairs(self, *args, **kwargs):
            raise AssertionError('lost audit must not mutate Milvus')
    class Encoder:
        identity = 'test'
        def encode(self, texts):
            raise AssertionError('lost audit must not encode')

    checks = iter([True, False])
    result = MilvusProjectionAdapter(
        AuditStore(), encoder=Encoder()).audit_authority(
            'p', [{'id': 'r', 'version_id': 'v', 'kind': 'chunk',
                   'text': 'x', 'metadata': {}}], 1,
            lambda: next(checks))
    assert result == {'aborted': True, 'repaired': 0}


def test_lease_heartbeat_renews_while_external_call_is_blocked_and_marks_loss():
    renewed = []
    second = threading.Event()
    waits = iter([False, True])

    def renew_ok():
        renewed.append(True)
        if len(renewed) == 2:
            second.set()
        return True

    with LeaseHeartbeat(
            renew_ok, interval_seconds=.01,
            waiter=lambda interval: next(waits)) as heartbeat:
        assert second.wait(1)
        assert heartbeat.check()
    assert len(renewed) >= 2

    lost = threading.Event()
    outcomes = iter([True, False])
    def renew_lost():
        value = next(outcomes)
        if not value:
            lost.set()
        return value
    with LeaseHeartbeat(
            renew_lost, interval_seconds=.01,
            waiter=lambda interval: False) as heartbeat:
        assert lost.wait(1)
        assert heartbeat.check() is False


def test_snapshot_load_chunks_milvus_upserts_at_256_records():
    class ChunkStore:
        def __init__(self): self.sizes = []; self.markers = []
        def upsert(self, records, **kwargs): self.sizes.append(len(records))
        def write_fence_marker(self, *args): self.markers.append(args)
    store = ChunkStore()
    records = [{'id': f'r{i}', 'version_id': f'v{i}', 'kind': 'chunk',
                'text': 'x', 'embedding': [1], 'metadata': {}}
               for i in range(600)]
    MilvusProjectionAdapter(store).load_snapshot(
        'p', 'generation', records, 9, fence='claim', lease_is_current=lambda: True)
    assert store.sizes == [256, 256, 88]


def test_projection_worker_runs_delivery_cleanup_and_maintenance_without_recovery():
    repository = FakeRepository()
    sync = FakeSync()
    cleaned = []
    worker = ProjectionWorker(
        repository, sync,
        cleanup_handlers={'milvus': cleaned.append}, interval_seconds=60)

    result = worker.run_once()

    assert sync.resumed == []
    assert sync.drained == [('p1', 'milvus'), ('p2', 'milvus')]
    assert sync.maintained == ['milvus']
    assert repository.directory_calls == 1
    assert cleaned == ['gone']
    assert result['cleanup'] == {'claimed': 1, 'cleaned': 1, 'failed': 0}


def test_projection_worker_start_and_close_are_idempotent_and_join_thread():
    worker = ProjectionWorker(
        FakeRepository(), FakeSync(), cleanup_handlers={}, interval_seconds=60)

    worker.start()
    thread = worker.thread
    worker.start()
    worker.close()
    worker.close()

    assert thread is not None and not thread.is_alive()
    assert worker.thread is None


def test_service_write_hook_only_requests_outbox_drain():
    drained = []
    service = KnowledgeService.__new__(KnowledgeService)
    service.projection_drain = drained.append
    service.milvus_store = RecordingMilvus()

    service._drain_projections('project')

    assert drained == ['project']
    assert service.milvus_store.upserts == []
    assert service.milvus_store.deletes == []
    assert not hasattr(KnowledgeService, '_sync_milvus')
