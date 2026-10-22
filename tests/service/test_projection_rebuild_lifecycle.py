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
from knowledge_service.domain.search_projection import ProjectionInput, build_projection, projection_fingerprint
from knowledge_service.integrations.milvus_store import COLLECTION


def snapshot_record():
    return ProjectionInput({'project_id': 'project', 'id': 'r', 'version_id': 'snapshot-v3',
                            'kind': 'chunk', 'text': 'x', 'metadata': {}})


class SharedBuildStore(fakes['FakeStore']):
    """The shared-index PG activation contract, with no external database."""

    def __init__(self, *, rebuild=None):
        super().__init__(watermark=3, rebuild=rebuild)
        self.active_index_version = 0
        self.activated_tokens = []

    def begin_rebuild(self, project_id, target, schema_version, owner, *, lease_guard,
                      embedding_model=None, dim=None):
        if self.rebuild_state is None:
            self.rebuild_state = {
                'claim': fakes['batch'](build_id='build-1', token='build-token'),
                'snapshot_seq': 3, 'records': [snapshot_record()],
                'state': {'build_id': 'build-1', 'phase': 'snapshot',
                          'snapshot_seq': 3, 'index_version': 1}}
        claimed, state = self.rebuild_state['claim'], self.rebuild_state['state']
        claimed.project_id, claimed.target = project_id, target
        claimed.schema_version = schema_version
        state.update(embedding_model=embedding_model, dim=dim)
        claimed.index_version = state['index_version']
        return self.rebuild_state

    def record_rebuild_progress(self, claimed, schema_version, state):
        super().record_rebuild_progress(claimed, schema_version, state)
        self.rebuild_state['state'] = dict(state)

    @contextmanager
    def activation_guard(self, project_id, claimed, schema_version):
        if claimed.token != self.rebuild_state['claim'].token:
            raise ProjectionLeaseLost('activation requires the current owner')
        self.guard_active = True
        try:
            yield self.watermark
        finally:
            self.guard_active = False

    def activate_index(self, claimed, schema_version, applied_seq, state):
        assert self.guard_active
        assert claimed.token == self.rebuild_state['claim'].token
        assert state['validation']['ok']
        self.active_index_version = state['index_version']
        self.activated_tokens.append(claimed.token)
        self.finished.append({'claim': claimed, 'state': dict(state), 'applied_seq': applied_seq})


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


class External:
    dim = 1

    def __init__(self):
        self.rows, self.writes = {}, []

    def ensure_collection(self, *, project_id, index_version, catalog):
        assert len(catalog.column_defs()) == 21
        assert not catalog.fields
        return self

    def check_collection(self, *, catalog):
        return self

    def delete_records(self, record_ids, *, project_id, index_version, catalog):
        self.rows = {key: row for key, row in self.rows.items()
                     if key[:2] != (project_id, index_version) or
                     (record_ids is not None and key[2] not in record_ids)}

    def upsert_rows(self, rows, *, project_id, index_version, catalog):
        self.writes.extend(rows)
        for row in rows:
            assert row['project_id'] == project_id
            assert row['index_version'] == index_version
            self.rows[project_id, index_version, row['record_id']] = dict(row)

    def validate_projection(self, expected, *, project_id, index_version, catalog):
        actual = {key[2]: {'version_id': row['version_id'], 'fingerprint': projection_fingerprint(row)}
                  for key, row in self.rows.items() if key[:2] == (project_id, index_version)}
        return {'ok': actual == expected}


class Encoder:
    identity = 'test'
    dimension = 1

    def encode(self, texts):
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

    def test_resumed_index_activates_with_the_new_owned_fence(self):
        claimed = fakes['batch'](build_id='build-1', token='new-owner')
        store = SharedBuildStore(rebuild={
            'claim': claimed, 'snapshot_seq': 3, 'records': [],
            'state': {'build_id': 'build-1', 'phase': 'validating',
                'destination': COLLECTION, 'snapshot_seq': 3, 'applied_seq': 3,
                'index_version': 1, 'catalog': default_catalog().to_dict(),
                'embedding_model': 'test', 'dim': 1, 'expected_projection': {}}})
        adapter = MilvusProjectionAdapter(External(), encoder=Encoder())
        result = ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild(
            'project', 'milvus')
        self.assertTrue(result['ready'])
        self.assertEqual(store.activated_tokens, ['new-owner'])
        self.assertEqual(store.active_index_version, 1)

    def test_lost_ownership_during_a_slow_load_never_seals_or_switches(self):
        store, adapter = ExpiringStore(), SlowAdapter()
        original = store.renew_claim
        lost = threading.Event()
        timer = threading.Timer(.04, lost.set)
        def renew(*args, **kwargs):
            if lost.is_set():
                raise ProjectionLeaseLost('another worker owns the claim')
            return original(*args, **kwargs)
        store.renew_claim = renew
        adapter.validate = lambda *args, **kwargs: self.fail('lost worker validated the index')
        sync = ProjectionSync(store, {'milvus': adapter}, schema_version=2)
        sync.REBUILD_HEARTBEAT_SECONDS = .01
        timer.start()
        try:
            with self.assertRaises(ProjectionLeaseLost):
                sync.rebuild('project', 'milvus')
        finally:
            timer.cancel()
        self.assertEqual(store.active_index_version, 0)
        self.assertEqual(store.finished, [])
        self.assertEqual(adapter._store().writes, [])

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
        class ColdEncoder(Encoder):
            def encode(self, texts):
                time.sleep(.25)
                return super().encode(texts)
        lost = threading.Event()
        original = store.renew_claim
        def renew(*args, **kwargs):
            if lost.is_set():
                raise ProjectionLeaseLost('another worker owns the claim')
            return original(*args, **kwargs)
        store.renew_claim = renew
        adapter = MilvusProjectionAdapter(external, encoder=ColdEncoder())
        sync = ProjectionSync(store, {'milvus': adapter}, schema_version=2)
        sync.REBUILD_HEARTBEAT_SECONDS = .01
        timer = threading.Timer(.04, lost.set)
        timer.start()
        try:
            with self.assertRaises(ProjectionLeaseLost):
                sync.rebuild('project', 'milvus')
        finally:
            timer.cancel()
        self.assertEqual(external.writes, [])
        self.assertEqual(store.active_index_version, 0)
        self.assertEqual(store.finished, [])


if __name__ == '__main__':
    unittest.main()
