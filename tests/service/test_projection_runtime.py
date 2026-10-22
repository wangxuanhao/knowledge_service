import pytest
import threading
from types import SimpleNamespace

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection
from knowledge_service.integrations.milvus_store import (
    MilvusSchemaMismatch, MilvusStore, VectorDimensionMismatch,
)
from knowledge_service.services.projection_runtime import (
    LeaseHeartbeat,
    MilvusProjectionAdapter,
    ProjectionWorker,
)
from knowledge_service.services.service import KnowledgeService
from knowledge_service.services.projection_sync import DrainResult
from knowledge_service.repository.projection_store import (
    ProjectionLeaseLost, ProjectionRebuildRequired,
)


CATALOG = default_catalog()


class RecordingEncoder:
    identity = 'test'

    def __init__(self):
        self.encoded = []

    def encode(self, texts):
        self.encoded.append(list(texts))
        return [[1.0] for _ in texts]


class RecordingMilvus:
    collection_name = 'knowledge_vectors'

    def __init__(self):
        self.upserts = []
        self.deletes = []
        self.rows = {}

    def check_collection(self, *, catalog):
        assert catalog == CATALOG
        return self

    def upsert_rows(self, records, *, project_id, index_version, catalog):
        self.upserts.append((list(records), project_id, index_version, catalog.fingerprint))
        for row in records:
            self.rows[(project_id, index_version, row['record_id'])] = row
        return len(records)

    def delete_records(self, record_ids, *, project_id, index_version, catalog):
        self.deletes.append((list(record_ids), project_id, index_version, catalog.fingerprint))
        for record_id in record_ids:
            self.rows.pop((project_id, index_version, record_id), None)
        return len(record_ids)


def event(seq, operation='upsert', record_id='record', *, project='project', index_version=1):
    payload = {
        'project_id': project, 'id': record_id, 'version_id': f'version-{seq}', 'kind': 'chunk',
        'text': 'text',
        'metadata': {},
    }
    envelope = {'index_version': index_version,
                'catalog_fingerprint': CATALOG.fingerprint, 'embedding_model': 'test'}
    if operation == 'tombstone':
        payload = {'record_id': record_id, 'version_id': f'version-{seq}'}
        envelope['record_id'] = record_id
    else:
        envelope['row'] = build_projection(ProjectionInput(payload), CATALOG,
                                           index_version=index_version)
    payload['_milvus_projection'] = envelope
    return {'seq': seq, 'operation': operation,
            'version_id': f'version-{seq}', 'payload': payload}


def test_milvus_adapter_replays_frozen_rows_and_rejects_stale_pg_claims():
    store = RecordingMilvus()
    claim = {'seq': 2, 'token': 'lease-new'}
    adapter = MilvusProjectionAdapter(store, encoder=RecordingEncoder(),
        fence_is_current=lambda project, seq, fence: (
            project == 'project' and seq == claim['seq'] and fence == claim['token']))

    kwargs = {'index_version': 1, 'catalog': CATALOG}
    assert adapter.apply('project', event(2), fence='lease-new', **kwargs) is True
    assert adapter.apply('project', event(2), fence='lease-new', **kwargs) is True
    assert len(store.rows) == 1
    for stale_event, token in ((event(2), 'lease-retry'), (event(1), 'lease-old')):
        with pytest.raises(ProjectionLeaseLost):
            adapter.apply('project', stale_event, fence=token, **kwargs)
    assert len(store.upserts) == 2
    expected = dict(event(2)['payload']['_milvus_projection']['row'], embedding=[1.0])
    assert store.rows == {('project', 1, 'record'): expected}
    claim.update(seq=3, token='lease-next')
    assert adapter.apply(
        'project', event(3, 'tombstone'), fence='lease-next', **kwargs) is True
    assert store.rows == {}
    assert store.deletes == [(['record'], 'project', 1, CATALOG.fingerprint)]


@pytest.mark.parametrize('mismatch', ['project', 'row_generation', 'envelope_generation', 'catalog'])
def test_milvus_adapter_rejects_envelopes_outside_the_frozen_project_build(mismatch):
    store, encoder = RecordingMilvus(), RecordingEncoder()
    value = event(2)
    envelope = value['payload']['_milvus_projection']
    if mismatch == 'project':
        envelope['row']['project_id'] = 'other'
    elif mismatch == 'row_generation':
        envelope['row']['index_version'] = 2
    elif mismatch == 'envelope_generation':
        envelope['index_version'] = 2
    else:
        envelope['catalog_fingerprint'] = 'another-catalog'
    with pytest.raises(ValueError):
        MilvusProjectionAdapter(store, encoder=encoder).apply(
            'project', value, fence='claim', index_version=1, catalog=CATALOG)
    assert encoder.encoded == []
    assert store.upserts == store.deletes == []


def test_milvus_adapter_resolves_a_store_reconnected_after_startup():
    current = {'store': None}
    encoder = RecordingEncoder()
    adapter = MilvusProjectionAdapter(lambda: current['store'], encoder=encoder)

    with pytest.raises(RuntimeError, match='unavailable'):
        adapter.apply('project', event(1), fence='lease-one', index_version=1, catalog=CATALOG)
    assert encoder.encoded == []

    current['store'] = RecordingMilvus()
    assert adapter.apply('project', event(1), fence='lease-two',
                         index_version=1, catalog=CATALOG) is True
    assert len(current['store'].upserts) == 1
    assert encoder.encoded == [['text']]


def test_milvus_adapter_lost_fence_after_write_refuses_ack_and_further_writes():
    store = RecordingMilvus()
    current = {'valid': True}
    upsert = store.upsert_rows

    def upsert_then_lose_fence(records, **kwargs):
        result = upsert(records, **kwargs)
        current['valid'] = False
        return result

    store.upsert_rows = upsert_then_lose_fence
    encoder = RecordingEncoder()
    adapter = MilvusProjectionAdapter(
        store, encoder=encoder,
        fence_is_current=lambda project, seq, fence: current['valid'])

    with pytest.raises(ProjectionLeaseLost):
        adapter.apply('project', event(4), fence='expired-lease', index_version=1, catalog=CATALOG)
    with pytest.raises(ProjectionLeaseLost):
        adapter.apply('project', event(5), fence='expired-lease', index_version=1, catalog=CATALOG)
    assert len(store.upserts) == 1
    assert encoder.encoded == [['text']]
    assert store.deletes == []


def test_tombstone_deletes_only_the_claimed_project_and_generation():
    store = RecordingMilvus()
    for project, generation in (('project', 1), ('project', 2), ('other', 1)):
        store.rows[(project, generation, 'record')] = {'version_id': 'keep'}
    encoder = RecordingEncoder()
    adapter = MilvusProjectionAdapter(store, encoder=encoder)
    for _ in range(2):
        assert adapter.apply('project', event(8, 'tombstone'), fence='claim-8',
                             index_version=1, catalog=CATALOG) is True
    assert store.rows == {('project', 2, 'record'): {'version_id': 'keep'},
                          ('other', 1, 'record'): {'version_id': 'keep'}}
    assert store.deletes == [(['record'], 'project', 1, CATALOG.fingerprint)] * 2
    assert encoder.encoded == []


@pytest.mark.parametrize('vectors, error', [
    (None, MilvusSchemaMismatch), ([], RuntimeError),
    ([[]], VectorDimensionMismatch), ([[0.0]], ValueError),
])
def test_milvus_adapter_never_acknowledges_an_indexed_record_without_valid_embedding(vectors, error):
    writes = []
    store = MilvusStore(dim=1, encoder_identity='test')
    store.check_collection = lambda **kwargs: store
    store._client = SimpleNamespace(upsert=lambda *args, **kwargs: writes.append(args))
    encoder = None if vectors is None else SimpleNamespace(identity='test', encode=lambda texts: vectors)
    adapter = MilvusProjectionAdapter(store, encoder=encoder)
    value = event(5)
    assert 'embedding' not in value['payload']['_milvus_projection']['row']
    with pytest.raises(error):
        adapter.apply('project', value, fence='claim', index_version=1, catalog=CATALOG)
    assert writes == []


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
        self.generation_maintenance = []

    def resume_pending(self, target=None):
        self.resumed.append(target)
        return []

    def drain_once(self, project_id, target):
        self.drained.append((project_id, target))
        return 0

    def maintain_rollbacks(self, target=None):
        self.maintained.append(target)
        return 0

    def maintain_generations(self):
        self.generation_maintenance.append(True)
        return {'claimed': 0, 'cleaned': 0, 'failed': 0}


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


def test_build_lost_lease_stops_before_loading_encoding_writing_or_receipts():
    store, encoder, receipts = RecordingMilvus(), RecordingEncoder(), []

    def unread_rows():
        raise AssertionError('lost build must not hydrate captured versions')
        yield

    with pytest.raises(ProjectionLeaseLost):
        MilvusProjectionAdapter(store, encoder=encoder).load_rows(
            'project', unread_rows(), {'index_version': 1}, CATALOG,
            lease_is_current=lambda: False, receipt_page=receipts.append)
    assert encoder.encoded == []
    assert store.upserts == store.deletes == receipts == []


@pytest.mark.parametrize('loss_phase', ['encoding', 'write'])
def test_build_lost_lease_never_confirms_rows_or_starts_the_next_batch(loss_phase):
    store, receipts = RecordingMilvus(), []
    current = {'valid': True}
    upsert = store.upsert_rows

    def encode(texts):
        if loss_phase == 'encoding':
            current['valid'] = False
        return [[1.0] for _ in texts]

    def write(records, **kwargs):
        result = upsert(records, **kwargs)
        current['valid'] = False
        return result

    store.upsert_rows = write
    encoder = SimpleNamespace(identity='test', encode=encode)
    records = [event(i, record_id=f'r{i}')['payload']['_milvus_projection']['row']
               for i in range(65)]
    with pytest.raises(ProjectionLeaseLost):
        MilvusProjectionAdapter(store, encoder=encoder).load_rows(
            'project', records, {'index_version': 1}, CATALOG,
            lease_is_current=lambda: current['valid'],
            receipt_page=receipts.append)
    assert receipts == []
    assert [len(call[0]) for call in store.upserts] == ([] if loss_phase == 'encoding' else [64])


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


def test_snapshot_load_bounds_encoding_writes_and_confirmed_receipt_pages_at_64_rows():
    store, encoder, receipts = RecordingMilvus(), RecordingEncoder(), []
    records = [event(i, record_id=f'r{i}')['payload']['_milvus_projection']['row']
               for i in range(600)]

    def confirm(page):
        assert len(store.upserts) == len(receipts) + 1
        assert [row['version_id'] for row in store.upserts[-1][0]] == [row['version_id'] for row in page]
        assert all('embedding' not in row for row in page)
        receipts.append(list(page))

    indexed = MilvusProjectionAdapter(store, encoder=encoder).load_rows(
        'project', records, {'index_version': 1}, CATALOG,
        lease_is_current=lambda: True, receipt_page=confirm)
    assert indexed == 600
    expected_sizes = [64] * 9 + [24]
    assert [len(page) for page in encoder.encoded] == expected_sizes
    assert [len(call[0]) for call in store.upserts] == expected_sizes
    assert [len(page) for page in receipts] == expected_sizes
    assert [row for page in receipts for row in page] == records
    assert all(call[1:] == ('project', 1, CATALOG.fingerprint) for call in store.upserts)


def test_projection_worker_runs_delivery_cleanup_generation_and_other_target_maintenance():
    repository = FakeRepository()
    sync = FakeSync()
    sync.adapters['neo4j'] = object()
    cleaned = []
    reconnects = []
    worker = ProjectionWorker(
        repository, sync,
        cleanup_handlers={'milvus': cleaned.append}, interval_seconds=60,
        reconnect=lambda: reconnects.append(True))

    result = worker.run_once()

    assert sync.resumed == []
    assert sync.drained == [('p1', 'milvus'), ('p1', 'neo4j'),
                            ('p2', 'milvus'), ('p2', 'neo4j')]
    assert sync.maintained == ['neo4j']
    assert sync.generation_maintenance == [True]
    assert reconnects == [True]
    assert repository.directory_calls == 1
    assert cleaned == ['gone']
    assert result['cleanup'] == {'claimed': 1, 'cleaned': 1, 'failed': 0}


def test_projection_worker_bounds_delivery_per_project_and_keeps_cleanup_running():
    repository, sync = FakeRepository(), FakeSync()

    def always_has_work(project, target):
        sync.drained.append((project, target))
        return DrainResult('applied', 1)

    sync.drain_once = always_has_work
    worker = ProjectionWorker(repository, sync, cleanup_handlers={'milvus': lambda project: None},
                              max_batches_per_project=2)
    result = worker.run_once()
    assert sync.drained == [('p1', 'milvus')] * 2 + [('p2', 'milvus')] * 2
    assert result == {'delivered': 4, 'cleanup': {'claimed': 1, 'cleaned': 1, 'failed': 0}}
    assert sync.generation_maintenance == [True]


def test_projection_worker_requests_forced_reconnect_when_delivery_fails():
    sync, reconnects = FakeSync(), []

    def failed_delivery(project, target):
        raise RuntimeError('store disconnected')

    sync.drain_once = failed_delivery
    worker = ProjectionWorker(FakeRepository(), sync, cleanup_handlers={},
                              reconnect=lambda **kwargs: reconnects.append(kwargs))
    with pytest.raises(RuntimeError, match='store disconnected'):
        worker.drain_project('p1')
    assert reconnects == [{'force': True}]


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
