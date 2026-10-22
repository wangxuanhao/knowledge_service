"""Pure, small-TTL regression for cold projection adapters; no PostgreSQL."""
from contextlib import contextmanager
from pathlib import Path
import runpy
import sys
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
fakes = runpy.run_path(str(ROOT / 'tests/service/test_projection_sync.py'))
from knowledge_service.repository.projection_store import ProjectionLeaseLost
from knowledge_service.services.projection_sync import ProjectionSync
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection
from knowledge_service.integrations.milvus_store import COLLECTION


def snapshot_record():
    return ProjectionInput({'project_id': 'project', 'id': 'r', 'version_id': 'snapshot-v3',
                            'kind': 'chunk', 'text': 'x', 'metadata': {}})


class SharedBuildStore(fakes['FakeStore']):
    """The shared-index PG activation contract, with no external database."""

    def __init__(self, *, rebuild=None):
        super().__init__(watermark=3, rebuild=rebuild)

    def begin_rebuild(self, project_id, target, schema_version, owner, *, lease_guard,
                      embedding_model=None, dim=None):
        if self.rebuild_state is None:
            self.rebuild_state = {
                'claim': fakes['batch'](build_id='build-1', token='build-token'),
                'snapshot_seq': 3, 'records': [snapshot_record()],
                'state': {'build_id': 'build-1', 'phase': 'snapshot',
                          'snapshot_seq': 3, 'applied_seq': 3, 'index_version': 1}}
        return super().begin_rebuild(project_id, target, schema_version, owner,
                                     lease_guard=lease_guard,
                                     embedding_model=embedding_model, dim=dim)


class ExpiringStore(SharedBuildStore):
    def __init__(self):
        super().__init__()
        self.deadline = time.monotonic() + .08

    def current(self):
        if time.monotonic() >= self.deadline:
            raise ProjectionLeaseLost('projection claim lease was lost')

    def renew_claim(self, claimed, lease_seconds=30):
        self.current()
        self.deadline = time.monotonic() + .08
        return super().renew_claim(claimed, lease_seconds)

    def record_rebuild_progress(self, *args):
        self.current()
        return super().record_rebuild_progress(*args)

    @contextmanager
    def activation_guard(self, *args):
        self.current()
        with super().activation_guard(*args) as watermark:
            yield watermark


class External(fakes['MemoryMilvus']):
    """Shared collection rows validated against the PG ReceiptManifest."""


class Encoder:
    identity = 'test'
    dimension = 1

    def __init__(self):
        self.encoded = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return [[1.0] for _ in texts]


class SlowAdapter(MilvusProjectionAdapter):
    def __init__(self):
        super().__init__(External(), encoder=Encoder())

    def load_rows(self, *args, **kwargs):
        time.sleep(.25)
        return super().load_rows(*args, **kwargs)


class RebuildLeaseTests(unittest.TestCase):
    def test_false_snapshot_lease_callback_never_writes_milvus(self):
        external, encoded = External(), []
        class CountingEncoder(Encoder):
            def encode(self, texts):
                encoded.extend(texts)
                return super().encode(texts)
        adapter = MilvusProjectionAdapter(external, encoder=CountingEncoder())
        catalog = default_catalog()
        row = build_projection(snapshot_record(), catalog, index_version=1)
        with self.assertRaises(ProjectionLeaseLost):
            adapter.load_rows('project', [row], {'index_version': 1}, catalog,
                              lease_is_current=lambda: False)
        self.assertEqual(external.writes, [])
        self.assertEqual(encoded, [])

    def test_false_snapshot_lease_callback_never_writes_neo4j(self):
        neo4j = runpy.run_path(str(ROOT / 'tests/service/test_neo4j_projection_adapter.py'))
        driver = neo4j['Driver']()
        adapter = neo4j['Neo4jProjectionAdapter'](driver, namespace='test')
        with self.assertRaises(ProjectionLeaseLost):
            adapter.load_snapshot('project', 'isolated', [neo4j['record']()], 3,
                                  fence='owner', lease_is_current=lambda: False)
        self.assertEqual(driver.calls, [])

    def test_legacy_milvus_upsert_signature_never_drops_destination_or_fence(self):
        writes = []
        class LegacyStore:
            def check_collection(self, *, catalog):
                return self
            def upsert_rows(self, records):
                writes.extend(records)
        adapter = MilvusProjectionAdapter(LegacyStore(), encoder=Encoder())
        catalog = default_catalog()
        event = {'seq': 1, 'operation': 'upsert', 'payload': {
            '_milvus_projection': {'index_version': 1, 'embedding_model': 'test',
                'catalog_fingerprint': catalog.fingerprint,
                'row': build_projection(snapshot_record(), catalog, index_version=1)}}}
        with self.assertRaises(TypeError):
            adapter.apply('project', event, fence='owner', index_version=1, catalog=catalog)
        self.assertEqual(writes, [])

    def test_legacy_milvus_delete_signature_never_drops_destination(self):
        writes = []
        class LegacyStore:
            def check_collection(self, *, catalog):
                return self
            def delete_records(self, record_ids):
                writes.extend(record_ids)
        adapter = MilvusProjectionAdapter(LegacyStore(), encoder=Encoder())
        catalog = default_catalog()
        event = {'seq': 1, 'operation': 'tombstone',
                 'payload': {'_milvus_projection': {'record_id': 'r', 'index_version': 1,
                     'embedding_model': 'test', 'catalog_fingerprint': catalog.fingerprint}}}
        with self.assertRaises(TypeError):
            adapter.apply('project', event, fence='owner', index_version=1, catalog=catalog)
        self.assertEqual(writes, [])

    def test_cold_snapshot_keeps_the_owned_lease_until_validation_and_switch(self):
        store, adapter = ExpiringStore(), SlowAdapter()
        sync = ProjectionSync(store, {'milvus': adapter}, schema_version=2)
        sync.REBUILD_HEARTBEAT_SECONDS = .01
        result = sync.rebuild('project', 'milvus')
        self.assertTrue(result['ready'])
        self.assertEqual(result['destination'], COLLECTION)
        self.assertEqual(store.active_index_version, 1)
        self.assertEqual(store.activated_tokens, ['build-token'])
        self.assertGreater(len(store.renewals), 5)
        self.assertEqual(result['indexed'], 1)
        self.assertEqual(store.receipts.count('project', 1), 1)
        self.assertEqual(store.receipts.pages_written, [('build-token', 1)])
        self.assertTrue(all('expected_projection' not in state for state in store.progress))

    def test_resumed_index_activates_with_the_new_owned_fence(self):
        claimed = fakes['batch'](build_id='build-1', token='new-owner')
        store = SharedBuildStore(rebuild={
            'claim': claimed, 'snapshot_seq': 3, 'records': [snapshot_record()],
            'state': {'build_id': 'build-1', 'phase': 'validating',
                'destination': COLLECTION, 'snapshot_seq': 3, 'applied_seq': 3,
                'index_version': 1, 'catalog': default_catalog().to_dict(),
                'embedding_model': 'test', 'dim': 1}})
        external, encoder = External(), Encoder()
        row = build_projection(snapshot_record(), default_catalog(), index_version=1)
        store.receipts.seed('project', 1, [row])
        external.upsert_rows([dict(row, embedding=[1.0])], project_id='project',
                             index_version=1, catalog=default_catalog())
        adapter = MilvusProjectionAdapter(external, encoder=encoder)
        result = ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild(
            'project', 'milvus')
        self.assertTrue(result['ready'])
        self.assertEqual(store.activated_tokens, ['new-owner'])
        self.assertEqual(store.active_index_version, 1)
        self.assertEqual(result['indexed'], 1)
        self.assertEqual(encoder.encoded, [])
        self.assertEqual(external.deletes, [])
        self.assertEqual(store.receipts.resets, [])
        self.assertEqual(store.receipts.pages_written, [])

    def test_lost_ownership_during_a_slow_load_never_seals_or_switches(self):
        entered, lost = threading.Event(), threading.Event()
        class BlockedAdapter(MilvusProjectionAdapter):
            def load_rows(self, *args, **kwargs):
                entered.set()
                if not lost.wait(1):
                    raise AssertionError('heartbeat did not observe the blocked load')
                return super().load_rows(*args, **kwargs)
        store = ExpiringStore()
        adapter = BlockedAdapter(External(), encoder=Encoder())
        original = store.renew_claim
        def renew(*args, **kwargs):
            if entered.is_set():
                lost.set()
                raise ProjectionLeaseLost('another worker owns the claim')
            return original(*args, **kwargs)
        store.renew_claim = renew
        adapter.validate = lambda *args, **kwargs: self.fail('lost worker validated the index')
        sync = ProjectionSync(store, {'milvus': adapter}, schema_version=2)
        sync.REBUILD_HEARTBEAT_SECONDS = .01
        with self.assertRaises(ProjectionLeaseLost):
            sync.rebuild('project', 'milvus')
        self.assertTrue(entered.is_set())
        self.assertTrue(lost.is_set())
        self.assertEqual(store.active_index_version, 0)
        self.assertEqual(store.finished, [])
        self.assertEqual(adapter._store().writes, [])
        self.assertEqual(store.receipts.count('project', 1), 0)
        self.assertEqual(store.receipts.pages_written, [])
        self.assertEqual(store.activated_tokens, [])

    def test_worker_directory_never_loads_project_statistics(self):
        runtime = runpy.run_path(str(ROOT / 'tests/service/test_projection_runtime.py'))
        repository, sync = runtime['FakeRepository'](), runtime['FakeSync']()
        worker = runtime['ProjectionWorker'](repository, sync,
            cleanup_handlers={'milvus': lambda project: None}, interval_seconds=60)
        worker.run_once()
        self.assertEqual(repository.directory_calls, 1)
        self.assertEqual(sync.drained, [('p1', 'milvus'), ('p2', 'milvus')])

    def test_cold_encoder_does_not_write_after_ownership_is_lost(self):
        claim = fakes['batch'](build_id='build-1', token='owner')
        store = ExpiringStore()
        store.rebuild_state = {'claim': claim, 'snapshot_seq': 3,
            'records': [snapshot_record()],
            'state': {'build_id': 'build-1', 'phase': 'snapshot_loading',
                      'destination': COLLECTION, 'snapshot_seq': 3, 'index_version': 1}}
        external = External()
        entered, lost = threading.Event(), threading.Event()
        class ColdEncoder(Encoder):
            def encode(self, texts):
                entered.set()
                if not lost.wait(1):
                    raise AssertionError('heartbeat did not observe the cold encoder')
                return super().encode(texts)
        original = store.renew_claim
        def renew(*args, **kwargs):
            if entered.is_set():
                lost.set()
                raise ProjectionLeaseLost('another worker owns the claim')
            return original(*args, **kwargs)
        store.renew_claim = renew
        encoder = ColdEncoder()
        adapter = MilvusProjectionAdapter(external, encoder=encoder)
        sync = ProjectionSync(store, {'milvus': adapter}, schema_version=2)
        sync.REBUILD_HEARTBEAT_SECONDS = .01
        with self.assertRaises(ProjectionLeaseLost):
            sync.rebuild('project', 'milvus')
        self.assertTrue(entered.is_set())
        self.assertTrue(lost.is_set())
        self.assertEqual(encoder.encoded, ['x'])
        self.assertEqual(external.writes, [])
        self.assertEqual(store.active_index_version, 0)
        self.assertEqual(store.finished, [])
        self.assertEqual(store.receipts.count('project', 1), 0)
        self.assertEqual(store.receipts.pages_written, [])
        self.assertEqual(store.activated_tokens, [])


if __name__ == '__main__':
    unittest.main()
