"""Pure adapter contracts plus PG integration for selected project builds."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection
from knowledge_service.repository.projection_store import ProjectionLeaseLost
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter


def body(project='p', version='v', text='body'):
    return ProjectionInput({'project_id': project, 'id': 'r', 'version_id': version,
                            'kind': 'chunk', 'text': text})


class SharedMilvusAdapterTests(unittest.TestCase):
    def adapter(self, current=lambda *args: True):
        store = Mock()
        store.collection_name = 'knowledge_vectors'
        encoder = SimpleNamespace(identity='model', dimension=2, encode=Mock(return_value=[[1., 0.]]))
        return MilvusProjectionAdapter(lambda: store, encoder=encoder, fence_is_current=current), store, encoder

    def event(self, index_version=1):
        row = build_projection(body(), default_catalog(), index_version=index_version)
        return {'seq': 1, 'operation': 'upsert', 'payload': {'kind': 'chunk',
            '_milvus_projection': {'index_version': index_version,
                'catalog_fingerprint': default_catalog().fingerprint,
                'embedding_model': 'model', 'row': row}}}

    def test_unbuilt_raw_event_performs_zero_encoding_and_zero_milvus_io(self):
        adapter, store, encoder = self.adapter()
        adapter.apply('p', {'seq': 1, 'operation': 'upsert', 'payload': {'kind': 'chunk'}},
                      fence='f', index_version=1, catalog=default_catalog())
        encoder.encode.assert_not_called()
        store.upsert_rows.assert_not_called()

    def test_ready_incremental_encodes_frozen_text_once_into_same_project_version(self):
        adapter, store, encoder = self.adapter()
        self.assertTrue(adapter.apply('p', self.event(3), fence='f', index_version=3, catalog=default_catalog()))
        encoder.encode.assert_called_once_with(['body'])
        kwargs = store.upsert_rows.call_args.kwargs
        self.assertEqual((kwargs['project_id'], kwargs['index_version']), ('p', 3))
        self.assertNotIn('embedding_model', store.upsert_rows.call_args.args[0][0])

    def test_combined_row_byte_budget_is_checked_before_encoding(self):
        adapter, store, encoder = self.adapter()
        store.dim = encoder.dimension = 10_000_000
        value = body()
        with self.assertRaisesRegex(ValueError, '字节'):
            adapter.preflight('p', {'index_version': 1}, [value])
        encoder.encode.assert_not_called()
        store.upsert_rows.assert_not_called()

    def test_old_event_cannot_write_active_new_version(self):
        adapter, store, encoder = self.adapter()
        with self.assertRaises(ValueError):
            adapter.apply('p', self.event(1), fence='f', index_version=2, catalog=default_catalog())
        encoder.encode.assert_not_called()
        store.upsert_rows.assert_not_called()

    def test_lease_loss_after_rpc_fails_closed_and_never_repairs_active_version(self):
        lost = False
        def current(*args):
            return not lost
        adapter, store, encoder = self.adapter(current)
        def lose_after_rpc(*args, **kwargs):
            nonlocal lost
            lost = True
        store.upsert_rows.side_effect = lose_after_rpc
        with self.assertRaises(ProjectionLeaseLost):
            adapter.apply('p', self.event(1), fence='f', index_version=1, catalog=default_catalog())
        self.assertEqual(store.upsert_rows.call_args.kwargs['index_version'], 1)
        store.delete_versions.assert_not_called()


class SharedMilvusCursorSafetyTests(unittest.TestCase):
    def cursor(self, **changes):
        from datetime import datetime, timedelta, timezone
        return dict(project_id='p', target='milvus', schema_version=3, status='applying',
                    active_destination='knowledge_vectors', applied_seq=0,
                    claim_owner='owner', claim_token='token', claimed_from_seq=1, claimed_to_seq=1,
                    claim_until=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
                    rebuild_state={'phase': 'ready', 'index_version': 1}, **changes)

    def test_marked_rebuild_cursor_refuses_existing_claim_ack(self):
        from knowledge_service.repository.projection_store import ProjectionStore, ClaimedBatch
        cursor = self.cursor()
        cursor['status'] = 'requires_rebuild'
        db = Mock()
        db.execute.return_value.fetchone.return_value = cursor
        store = ProjectionStore(SimpleNamespace(_db=db))
        claim = ClaimedBatch(({'seq': 1},), 'owner', 'token', cursor['claim_until'], 1, 1,
                             'p', 'milvus', 3)
        with self.assertRaises(ProjectionLeaseLost):
            store._locked_claim(claim)

    def test_expired_claim_keeps_uncertainty_after_clearing_claim_fields(self):
        from knowledge_service.repository.projection_store import ProjectionStore
        cursor = self.cursor()
        cursor['claim_until'] = '2020-01-01T00:00:00Z'
        db = Mock()
        store = ProjectionStore(SimpleNamespace(_db=db))
        self.assertEqual(store._milvus_delivery_state_locked('p', 'milvus', cursor, 3,
                         '2026-10-21T00:00:00Z'), 'requires_rebuild')
        writes = [(call.args[0], call.args[1]) for call in db.execute.call_args_list]
        self.assertTrue(any('rebuild_state' in sql and any('"uncertain_write":true' in str(value) for value in params)
                            for sql, params in writes))


class MemoryMilvus:
    collection_name = 'knowledge_vectors'
    dim = 2

    def __init__(self):
        self.rows = {}
        self.creations = []

    def ensure_collection(self, *, project_id, index_version, catalog):
        self.creations.append((project_id, index_version, catalog))

    def check_collection(self, *, catalog):
        assert catalog == default_catalog()
        if not self.creations:
            raise RuntimeError('No explicitly prepared shared collection')
        return self

    def upsert_rows(self, rows, *, project_id, index_version, catalog):
        for row in rows:
            assert row['project_id'] == project_id and row['index_version'] == index_version
            self.rows[row['vector_id']] = dict(row)
        return len(rows)

    def delete_records(self, ids, *, project_id, index_version, catalog):
        targets = [key for key, row in self.rows.items() if row['project_id'] == project_id
                   and row['index_version'] == index_version and (ids is None or row['record_id'] in ids)]
        for key in targets:
            del self.rows[key]
        return len(targets)

    def validate_projection(self, expected, *, project_id, index_version, catalog):
        from knowledge_service.services.projection_sync import ProjectionSync
        rows = [row for row in self.rows.values() if row['project_id'] == project_id and row['index_version'] == index_version]
        return {'ok': ProjectionSync._milvus_manifest(rows) == expected, 'count': len(rows)}

    def delete_versions(self, project_id, versions):
        for key in list(self.rows):
            row = self.rows[key]
            if row['project_id'] == project_id and row['index_version'] in versions:
                del self.rows[key]



class ShortActivationGuardTests(unittest.TestCase):
    def test_no_external_work_inside_activation_guard_and_no_duplicate_validation(self):
        from contextlib import contextmanager
        from knowledge_service.repository.projection_store import ClaimedBatch
        from knowledge_service.services.projection_sync import ProjectionSync
        class Receipts:
            def __init__(self, authority):
                self.authority, self.rows = authority, {}
            def reset(self, claimed):
                self.assert_outside_guard()
                self.rows.clear()
            def replace_page(self, claimed, rows):
                self.assert_outside_guard()
                self.rows.update(ProjectionSync._milvus_manifest(rows))
            def manifest(self, project, generation):
                self.assert_outside_guard()
                return dict(self.rows)
            def count(self, project, generation):
                return len(self.rows)
            def assert_outside_guard(self):
                assert not self.authority.in_guard
        class Authority:
            def __init__(self):
                self.in_guard = False
                self.activated = False
                self.receipts = Receipts(self)
            def begin_rebuild(self, *args, **kwargs):
                return {'claim': ClaimedBatch((), 'owner', 'token', '2099-01-01T00:00:00Z',
                          1, 0, 'p', 'milvus', 3, 'build', index_version=1),
                        'state': {'build_id': 'build', 'index_version': 1, 'phase': 'snapshot'},
                        'snapshot_seq': 0, 'records': [body()]}
            def renew_claim(self, claim):
                return claim
            def record_rebuild_progress(self, *args):
                pass
            def project_watermark(self, project):
                return 0
            @contextmanager
            def activation_guard(self, *args):
                self.in_guard = True
                try:
                    yield 0
                finally:
                    self.in_guard = False
            def activate_index(self, *args):
                self.activated = True
            def mark_failed(self, *args):
                pass
        authority = Authority()
        store = MemoryMilvus()
        validations = []
        original = store.validate_projection
        def validate(*args, **kwargs):
            self.assertFalse(authority.in_guard, 'external validation held the PG activation guard')
            validations.append(1)
            return original(*args, **kwargs)
        store.validate_projection = validate
        encoder = SimpleNamespace(identity='model', dimension=2, encode=lambda texts: [[1., 0.] for _ in texts])
        sync = ProjectionSync(authority, {'milvus': MilvusProjectionAdapter(store, encoder=encoder)}, schema_version=3)
        self.assertTrue(sync.rebuild('p', 'milvus')['ready'])
        self.assertTrue(authority.activated)
        self.assertEqual(validations, [1])


def test_pg_explicit_build_incremental_and_late_old_rpc_are_project_isolated(tmp_path):
    from knowledge_service.repository.core import Repository
    from knowledge_service.services.projection_sync import ProjectionSync
    repo = Repository(tmp_path / 'shared-minimal-pipeline')
    first, other = [repo.create_project(name)['id'] for name in ('selected', 'unselected')]
    for project in (first, other):
        repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'initial', 'metadata': {'category': 'A'}})
    store = MemoryMilvus()
    encoder = SimpleNamespace(identity='model', dimension=2, encode=Mock(side_effect=lambda texts: [[1., 0.] for _ in texts]))
    adapter = MilvusProjectionAdapter(store, encoder=encoder)
    sync = ProjectionSync(repo.projections, {'milvus': adapter}, schema_version=3)
    assert sync.drain_once(other, 'milvus').status == 'not_built'
    encoder.encode.assert_not_called()
    result = sync.rebuild(first, 'milvus')
    assert result['indexed'] == 1
    index = repo.projections.search_index(first, 3)
    assert index['index_version'] == 1
    assert repo.projections.cursor(other, 'milvus') is None
    old_row = dict(next(iter(store.rows.values())))
    repo.put_record(other, {'id': 'r', 'kind': 'chunk', 'text': 'unbuilt edit'})
    repo.put_record(first, {'id': 'r', 'kind': 'chunk', 'text': 'incremental', 'metadata': {'category': 'B'}})
    events = [dict(row) for row in repo._db.execute('SELECT payload FROM projection_outbox WHERE project_id=?', (first,)).fetchall()]
    assert all('"embedding":' not in row['payload'] for row in events)
    assert int(sync.drain_once(first, 'milvus')) == 2
    assert repo.projections.search_index(first, 3)['index_version'] == 1
    assert len(store.rows) == 1 and next(iter(store.rows.values()))['text'] == 'incremental'
    rebuilt = sync.rebuild(first, 'milvus')
    assert rebuilt['index_version'] == 2
    # A previous owner's late RPC has the old deterministic PK/version. It can
    # neither overwrite the new row nor be returned by the activated scope.
    store.upsert_rows([old_row], project_id=first, index_version=1, catalog=default_catalog())
    active = [row for row in store.rows.values() if row['project_id'] == first and row['index_version'] == 2]
    assert len(active) == 1 and active[0]['text'] == 'incremental'
    assert all(row['project_id'] == first for row in store.rows.values())
    assert all(project == first for project, _, _ in store.creations)
    repo.close()


def test_pg_build_catches_changes_created_during_snapshot_encoding(tmp_path):
    from knowledge_service.repository.core import Repository
    from knowledge_service.services.projection_sync import ProjectionSync
    repo = Repository(tmp_path / 'shared-catchup')
    project = repo.create_project('selected')['id']
    repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'snapshot'})
    changed = False
    def encode(texts):
        nonlocal changed
        if not changed:
            changed = True
            repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'catchup'})
        return [[1., 0.] for _ in texts]
    store = MemoryMilvus()
    sync = ProjectionSync(repo.projections, {'milvus': MilvusProjectionAdapter(store,
         encoder=SimpleNamespace(identity='model', dimension=2, encode=encode))}, schema_version=3)
    assert sync.rebuild(project, 'milvus')['indexed'] == 1
    assert next(iter(store.rows.values()))['text'] == 'catchup'
    assert repo.projection_ready(project, 'milvus', 3)
    repo.close()


def test_pg_clean_encoder_failure_resumes_same_version(tmp_path):
    import pytest
    from knowledge_service.repository.core import Repository
    from knowledge_service.services.projection_sync import ProjectionSync
    repo = Repository(tmp_path / 'shared-resume')
    project = repo.create_project('selected')['id']
    repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'snapshot'})
    encoder = SimpleNamespace(identity='model', dimension=2, encode=Mock(side_effect=RuntimeError('encoder unavailable')))
    store = MemoryMilvus()
    def coordinator():
        return ProjectionSync(repo.projections, {'milvus': MilvusProjectionAdapter(store, encoder=encoder)}, schema_version=3)
    with pytest.raises(RuntimeError, match='encoder unavailable'):
        coordinator().rebuild(project, 'milvus')
    assert repo.projections.cursor(project, 'milvus')['rebuild_state']['index_version'] == 1
    encoder.encode.side_effect = lambda texts: [[1., 0.] for _ in texts]
    assert coordinator().rebuild(project, 'milvus')['index_version'] == 1
    repo.close()


def test_pg_preflight_failure_rechecks_fixed_business_data_without_encoding_invalid_rows(tmp_path):
    import pytest
    from knowledge_service.repository.core import Repository
    from knowledge_service.services.projection_sync import ProjectionSync
    repo = Repository(tmp_path / 'shared-preflight-retry')
    project = repo.create_project('selected')['id']
    repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': '中' * 21846})
    encoder = SimpleNamespace(identity='model', dimension=2, encode=Mock(side_effect=lambda texts: [[1., 0.] for _ in texts]))
    store = MemoryMilvus()
    sync = ProjectionSync(repo.projections, {'milvus': MilvusProjectionAdapter(store, encoder=encoder)}, schema_version=3)
    with pytest.raises(ValueError, match='UTF-8'):
        sync.rebuild(project, 'milvus')
    encoder.encode.assert_not_called()
    repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'fixed'})
    assert sync.rebuild(project, 'milvus')['indexed'] == 1
    assert next(iter(store.rows.values()))['text'] == 'fixed'
    repo.close()


def test_pg_capture_window_metadata_changes_keep_fixed_catalog_and_catch_up(tmp_path, monkeypatch):
    import pytest
    from knowledge_service.repository.core import Repository
    from knowledge_service.services.projection_sync import ProjectionSync
    repo = Repository(tmp_path / 'shared-capture-fields')
    project = repo.create_project('selected')['id']
    repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'snapshot'})
    persist = repo.projections._persist_rebuild_snapshot
    def concurrent_change(*args, **kwargs):
        persist(*args, **kwargs)
        repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'changed', 'metadata': {'category': 'new'}})
    monkeypatch.setattr(repo.projections, '_persist_rebuild_snapshot', concurrent_change)
    encoder = SimpleNamespace(identity='model', dimension=2, encode=Mock(side_effect=lambda texts: [[1., 0.] for _ in texts]))
    store = MemoryMilvus()
    sync = ProjectionSync(repo.projections, {'milvus': MilvusProjectionAdapter(store, encoder=encoder)}, schema_version=3)
    assert sync.rebuild(project, 'milvus')['index_version'] == 1
    assert repo.projection_ready(project, 'milvus', 3)
    frozen = repo.projections.search_index(project, 3)
    assert frozen['catalog']['fields'] == []
    assert next(iter(store.rows.values()))['text'] == 'changed'
    repo.close()


def test_pg_model_change_after_loaded_snapshot_builds_new_version_and_encodes_every_row(tmp_path):
    import pytest
    from knowledge_service.repository.core import Repository
    from knowledge_service.services.projection_sync import ProjectionSync
    repo = Repository(tmp_path / 'shared-model-resume')
    project = repo.create_project('selected')['id']
    repo.put_record(project, {'id': 'r', 'kind': 'chunk', 'text': 'snapshot'})
    encoder = SimpleNamespace(identity='model-a', dimension=2, encode=Mock(return_value=[[1., 0.]]))
    store = MemoryMilvus()
    original_validate = store.validate_projection
    store.validate_projection = Mock(side_effect=RuntimeError('validation unavailable'))
    def coordinator():
        return ProjectionSync(repo.projections, {'milvus': MilvusProjectionAdapter(store, encoder=encoder)}, schema_version=3)
    with pytest.raises(RuntimeError, match='validation unavailable'):
        coordinator().rebuild(project, 'milvus')
    failed = repo.projections.cursor(project, 'milvus')['rebuild_state']
    assert failed['phase'] == 'validating' and failed['embedding_model'] == 'model-a'
    assert next(iter(store.rows.values()))['embedding'] == [1., 0.]
    store.validate_projection = original_validate
    encoder.identity = 'model-b'
    encoder.encode.return_value = [[0., 1.]]
    result = coordinator().rebuild(project, 'milvus')
    assert result['index_version'] == 2
    active = [row for row in store.rows.values() if row['index_version'] == 2]
    assert len(active) == 1 and active[0]['embedding'] == [0., 1.]
    assert encoder.encode.call_count == 2
    assert repo.projections.search_index(project, 3)['embedding_model'] == 'model-b'
    repo.close()


if __name__ == '__main__':
    unittest.main()
