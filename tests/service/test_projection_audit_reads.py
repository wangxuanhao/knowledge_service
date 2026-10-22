from contextlib import contextmanager
from types import SimpleNamespace
import threading

import pytest

from knowledge_service.repository.core import Repository
from knowledge_service.repository.pg_engine import SqliteishConnection
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter, ProjectionWorker


class AuditStore:
    collection_name = 'audit-test'

    def __init__(self, repair_ids=()):
        self.repair_ids = set(repair_ids)
        self.applied = []

    def authority_repair_ids(self, project, destination, expected, heartbeat):
        assert heartbeat()
        return self.repair_ids

    def apply_authority_repairs(self, project, destination, rows, ids, **kwargs):
        assert kwargs['before_write']()
        self.applied.append((rows, ids))
        return {'repaired': len(rows)}

    def write_fence_marker(self, *args):
        pass


def worker_for(repo, store):
    watermark = repo.projections.project_watermark
    repo.projections = SimpleNamespace(
        project_watermark=watermark,
        claim_maintenance=lambda *args: SimpleNamespace(owner='worker', token='lease'),
        renew_maintenance=lambda *args: True,
        maintenance_is_current=lambda claim, sequence: watermark('project') == sequence,
        release_maintenance=lambda claim: None,
    )
    sync = SimpleNamespace(schema_version=1, adapters={'milvus': MilvusProjectionAdapter(store)})
    return ProjectionWorker(repo, sync, cleanup_handlers={})


def test_authority_scan_does_not_hold_primary_repository_lock(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'audit-reader')
    project = repo.create_project('audit reader')['id']
    repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'a'})
    watermark = repo.projections.project_watermark
    worker = worker_for(repo, AuditStore())
    repo.projections.maintenance_is_current = lambda claim, seq: watermark(project) == seq
    entered, resume, read_done = threading.Event(), threading.Event(), threading.Event()
    failures = []
    original = SqliteishConnection.execute

    def gated(connection, statement, params=()):
        if threading.current_thread() is audit_thread and 'FROM record_versions' in statement:
            entered.set()
            assert resume.wait(5)
        return original(connection, statement, params)

    monkeypatch.setattr(SqliteishConnection, 'execute', gated)

    def audit():
        try:
            worker.audit_project(project)
        except BaseException as exc:
            failures.append(exc)

    audit_thread = threading.Thread(target=audit)
    reader = threading.Thread(target=lambda: (repo.get_project(project), read_done.set()))
    audit_thread.start()
    try:
        assert entered.wait(5), 'audit did not enter the authoritative scan'
        reader.start()
        assert read_done.wait(.5), 'background authority scan blocks the primary Repository reader'
    finally:
        resume.set()
        audit_thread.join(5)
        if reader.ident:
            reader.join(5)
    assert not failures


def test_audit_fetches_only_captured_repair_versions_in_bounded_batches():
    expected = {f'r{i}': f'captured-v{i}' for i in range(600)}
    fetched = []
    class Repo:
        projections = SimpleNamespace(project_watermark=lambda project: 7)

        @contextmanager
        def read_snapshot(self):
            raise AssertionError('compact audit must not hold the primary snapshot')
            yield

        def current_records(self, *args, **kwargs):
            raise AssertionError('compact audit must not read the entire payload corpus')

        def projection_authority_manifest(self, project, kinds):
            return 7, expected

        def projection_authority_records(self, project, versions):
            assert len(versions) <= 256
            fetched.extend(versions)
            return [{'id': version.replace('captured-v', 'r'), 'version_id': version,
                     'kind': 'entity', 'text': 'repair', 'embedding': [1.0]} for version in versions]

    store = AuditStore({f'r{i}' for i in range(530)} | {'unexpected-ghost'})
    worker_for(Repo(), store).audit_project('project')
    assert set(fetched) == {f'captured-v{i}' for i in range(530)}
    assert len(fetched) == 530
    assert len(store.applied[0][0]) == 530
    assert 'unexpected-ghost' in store.applied[0][1]


def test_compact_manifest_preserves_deleted_falsies_and_audit_records(tmp_path):
    repo = Repository(tmp_path / 'audit-falsies')
    project = repo.create_project('audit falsies')['id']
    other = repo.create_project('other')['id']
    expected = {}
    for index, deleted in enumerate([None, False, 0, '', [], {}, True, 1, 'false', [1], {'x': 1}]):
        row = repo.put_record(project, {'id': f'e{index}', 'kind': 'entity', 'text': 'a',
            'metadata': {'_deleted': deleted, '_audit': index == 1}})
        if not deleted:
            expected[row['id']] = row['version_id']
    repo.put_record(project, {'id': 'document', 'kind': 'document', 'text': 'not indexed'})
    repo.put_record(other, {'id': 'foreign', 'kind': 'entity', 'text': 'foreign'})
    sequence, manifest = repo.projection_authority_manifest(project, {'entity', 'relation', 'chunk'})
    assert sequence == repo.projections.project_watermark(project)
    assert manifest == expected


def test_independent_snapshot_is_read_only_and_payload_fetch_uses_current_versions(tmp_path):
    repo = Repository(tmp_path / 'audit-versions')
    project = repo.create_project('audit versions')['id']
    old = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'old'})
    current = repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'current'})
    with repo._db.independent_read_snapshot() as snapshot:
        assert snapshot.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
    from psycopg.errors import ReadOnlySqlTransaction
    with pytest.raises(ReadOnlySqlTransaction):
        with repo._db.independent_read_snapshot() as snapshot:
            snapshot.execute('UPDATE projects SET name=name WHERE id=?', (project,))
    assert repo.projection_authority_records(project, [old['version_id']]) == []
    assert [r['version_id'] for r in repo.projection_authority_records(project, [current['version_id']])] == [current['version_id']]
    with pytest.raises(ValueError, match='256'):
        repo.projection_authority_records(project, ['v'] * 257)


def test_compact_audit_aborts_when_a_captured_version_is_no_longer_current():
    store = AuditStore({'a'})
    result = MilvusProjectionAdapter(store).audit_authority_manifest(
        'project', {'a': 'captured-v1'}, 7, lambda: True,
        load_records=lambda versions: [])
    assert result == {'aborted': True, 'repaired': 0}
    assert not store.applied


def test_compact_audit_rechecks_project_watermark_before_external_write():
    store = AuditStore({'a'})
    result = MilvusProjectionAdapter(store).audit_authority_manifest(
        'project', {'a': 'captured-v1'}, 7, lambda: True,
        load_records=lambda versions: [{'id': 'a', 'version_id': 'captured-v1',
            'kind': 'entity', 'embedding': [1.0]}],
        before_write=lambda: False)
    assert result == {'aborted': True, 'repaired': 0}
    assert not store.applied
