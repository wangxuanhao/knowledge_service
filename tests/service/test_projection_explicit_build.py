"""Explicit Milvus builds and healthy incremental delivery, without a database."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from knowledge_service.repository.projection_store import ProjectionStore
from knowledge_service.services.job_handlers import job_handlers
from knowledge_service.services.projection_runtime import ProjectionWorker
from knowledge_service.services.projection_sync import DrainResult, ProjectionSync
from knowledge_service.repository.projection_store import ProjectionRebuildRequired
from knowledge_service.integrations.milvus_store import COLLECTION, MilvusSchemaMismatch, MilvusStore
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection


def instant(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def active_cursor(**changes):
    cursor = dict(schema_version=3, applied_seq=0, status='ready',
                active_destination=COLLECTION,
                previous_destination=None, rollback_until=None,
                claim_owner=None, claim_token=None, claim_until=None,
                claimed_from_seq=None, claimed_to_seq=None,
                rebuild_state={'index_version': 1, 'catalog': default_catalog().to_dict(),
                               'embedding_model': 'test', 'dim': 1})
    return {**cursor, **changes}


class Rows:
    rowcount = 1

    def __init__(self, row=None, rows=()):
        self.row, self.rows = row, rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Database:
    """Only the delivery SQL boundary; production claim/CAS code runs unchanged."""

    def __init__(self, repository):
        self.repository = repository
        self.statements = []

    @property
    def in_transaction(self):
        return self.repository.transaction_depth > 0

    def executemany(self, statement, values):
        assert 'INSERT INTO milvus_projection_receipts' in statement
        for project, generation, record, version, fingerprint in values:
            self.repository.receipt_rows[(project, generation, record)] = {
                'version_id': version, 'fingerprint': fingerprint}
        return Rows()

    def execute(self, statement, params=()):
        sql = ' '.join(statement.split())
        self.statements.append(sql)
        repo = self.repository
        if sql.startswith('SELECT * FROM milvus_generation_cleanup_outbox'):
            return Rows(rows=[])
        if sql.startswith('INSERT INTO projection_cursors'):
            project, target, schema, _ = params
            repo.cursors.setdefault((project, target), dict(
                schema_version=schema, applied_seq=0, status='pending',
                rebuild_state={}))
            return Rows()
        if sql.startswith('SELECT * FROM projection_cursors'):
            cursor = repo.cursors.get(tuple(params))
            return Rows(None if cursor is None else dict(cursor))
        if sql.startswith('SELECT projection_seq FROM project_projection_sequences'):
            return Rows({'projection_seq': len(repo.events.get(params[0], ()))})
        if sql.startswith('SELECT project_id,seq AS outbox_seq'):
            project, base, limit = params
            return Rows(rows=[dict(row, outbox_seq=row['seq'])
                              for row in repo.events.get(project, ())
                              if row['seq'] > base][:limit])
        if sql.startswith('SELECT seq AS outbox_seq'):
            project, first, last = params
            return Rows(rows=[{'outbox_seq': row['seq']}
                              for row in repo.events.get(project, ())
                              if first <= row['seq'] <= last])
        if sql.startswith('UPDATE projection_cursors SET status=CASE'):
            owner, token, expires, first, last, _, project, target = params
            repo.cursors[(project, target)].update(
                status='applying', last_error=None, claim_owner=owner,
                claim_token=token, claim_until=expires,
                claimed_from_seq=first, claimed_to_seq=last)
            return Rows()
        if sql.startswith('UPDATE projection_cursors SET claim_until='):
            expires, _, project, target, *_ = params
            repo.cursors[(project, target)]['claim_until'] = expires
            return Rows()
        if sql.startswith('UPDATE projection_cursors SET applied_seq='):
            end, _, status, _, project, target, _ = params
            repo.cursors[(project, target)].update(
                applied_seq=end, status=status, claim_owner=None,
                claim_token=None, claim_until=None,
                claimed_from_seq=None, claimed_to_seq=None)
            return Rows()
        if sql.startswith('UPDATE projection_cursors SET rebuild_state='):
            value, project, target, *_ = params
            repo.cursors[(project, target)]['rebuild_state'] = json.loads(value)
            return Rows()
        if sql.startswith('UPDATE projection_cursors SET status='):
            if "status='requires_rebuild'" in sql:
                error, _, project, target, *_ = params
                status = 'requires_rebuild'
            elif "status='failed'" in sql:
                error, _, project, target, *_ = params
                status = 'failed'
            else:
                status, error, _, project, target, *_ = params
            repo.cursors[(project, target)].update(
                status=status, last_error=error, claim_owner=None,
                claim_token=None, claim_until=None,
                claimed_from_seq=None, claimed_to_seq=None)
            return Rows()
        if sql.startswith('UPDATE projection_cursors SET claim_owner='):
            if 'claim_owner=NULL' in sql:
                _, project, target, _ = params
                repo.cursors[(project, target)].update(
                    claim_owner=None, claim_token=None, claim_until=None,
                    claimed_from_seq=None, claimed_to_seq=None)
                return Rows()
            owner, token, expires, first, last, _, project, target = params
            repo.cursors[(project, target)].update(
                claim_owner=owner, claim_token=token, claim_until=expires,
                claimed_from_seq=first, claimed_to_seq=last)
            return Rows()
        raise AssertionError(f'unexpected delivery SQL: {sql}')


class Repository:
    def __init__(self, cursors=None, events=None):
        self.cursors = cursors or {}
        self.events = events or {}
        self._lock = threading.RLock()
        self.transaction_depth = 0
        self.receipt_rows = {}
        self._db = Database(self)
        self.projections = ProjectionStore(self)

    @contextmanager
    def _transaction(self):
        with self._lock:
            self.transaction_depth += 1
            try:
                yield
            finally:
                self.transaction_depth -= 1

    def list_project_directory(self):
        return [{'id': project} for project in self.events]

    read_snapshot = _transaction

    def current_records(self, project):
        return []

    def claim_projection_cleanups(self, owner, limit=100):
        return []


class Adapter:
    def __init__(self, error=None):
        self.applied, self.audited = [], []
        self.builds = []
        self.error = error

    def apply(self, project, event, destination=None, fence=None, *, index_version=None, catalog=None,
              lease_is_current=None):
        if index_version is not None:
            assert lease_is_current is not None and lease_is_current()
            assert index_version == 1
            assert catalog.to_dict() == default_catalog().to_dict()
            assert len(catalog.column_defs()) == 21
            envelope = event['payload']['_milvus_projection']
            assert envelope['index_version'] == index_version
            assert envelope['catalog_fingerprint'] == catalog.fingerprint
            assert envelope['row']['project_id'] == project
            assert isinstance(fence, str) and fence
            self.builds.append((project, index_version, catalog.fingerprint))
            destination = COLLECTION
        self.applied.append((project, event['seq'], destination))
        if self.error is not None:
            raise self.error
        if lease_is_current is not None:
            assert lease_is_current()
        return True

    def retire_versions(self, project, versions):
        raise AssertionError('fixture has no retired-generation cleanup jobs')

    def audit_authority(self, *args):
        self.audited.append(args)


def sync_for(repository, adapter=None):
    return ProjectionSync(repository.projections, {'milvus': adapter or Adapter()},
                          schema_version=3)


def events(count, project='p'):
    catalog = default_catalog()
    rows = []
    for i in range(1, count + 1):
        payload = {'project_id': project, 'id': f'r{i}', 'version_id': f'v{i}',
                   'kind': 'chunk', 'text': 'text'}
        payload['_milvus_projection'] = {
            'index_version': 1, 'catalog_fingerprint': catalog.fingerprint,
            'embedding_model': 'test',
            'row': build_projection(ProjectionInput(dict(payload)), catalog, index_version=1)}
        rows.append({'seq': i, 'operation': 'upsert', 'version_id': f'v{i}', 'payload': payload})
    return rows


class ExplicitBuildTests(unittest.TestCase):
    def test_startup_does_not_resume_incomplete_project_builds(self):
        resumed = []
        sync = SimpleNamespace(adapters={'milvus': object()},
                               resume_pending=resumed.append)
        worker = ProjectionWorker(Repository(), sync, cleanup_handlers={},
                                  interval_seconds=60)
        try:
            worker.start()
        finally:
            worker.close()
        self.assertEqual(resumed, [])

    def test_schema_exception_does_not_trigger_a_worker_rebuild(self):
        rebuilt = []
        def mismatch(*args):
            raise ProjectionRebuildRequired('p', 'milvus', current_schema=2,
                                            required_schema=3)
        sync = SimpleNamespace(adapters={'milvus': object()},
                               drain_once=mismatch, rebuild=lambda *args: rebuilt.append(args))
        worker = ProjectionWorker(Repository(), sync, cleanup_handlers={})
        with self.assertLogs('knowledge_service.projection_runtime', level='WARNING'):
            self.assertEqual(worker.drain_project('p'), 0)
        self.assertEqual(rebuilt, [])

    def test_empty_unready_queue_does_not_trigger_a_worker_rebuild(self):
        rebuilt = []
        repo = Repository()
        repo.projection_ready = lambda *args: False
        sync = SimpleNamespace(adapters={'milvus': object()}, schema_version=3,
            drain_once=lambda *args: DrainResult('empty'),
            rebuild=lambda *args: rebuilt.append(args))
        self.assertEqual(ProjectionWorker(repo, sync, cleanup_handlers={}).drain_project('p'), 0)
        self.assertEqual(rebuilt, [])

    def test_no_cursor_is_not_built_without_creating_cursor_or_encoding(self):
        repo = Repository(events={'unselected': events(1, 'unselected')})
        adapter = Adapter()
        result = sync_for(repo, adapter).drain_once('unselected', 'milvus')
        self.assertEqual(result.status, 'not_built')
        self.assertEqual(adapter.applied, [])
        self.assertEqual(repo.cursors, {})

    def test_pending_cursor_without_active_generation_is_not_built(self):
        repo = Repository({('p', 'milvus'): active_cursor(
            active_destination=None, status='pending')}, {'p': events(1)})
        adapter = Adapter()
        self.assertEqual(sync_for(repo, adapter).drain_once('p', 'milvus').status, 'not_built')
        self.assertEqual(adapter.applied, [])

    def test_ready_label_without_active_generation_cannot_be_search_ready(self):
        repo = Repository({('p', 'milvus'): active_cursor(active_destination=None)})
        self.assertFalse(repo.projections.projection_ready('p', 'milvus', 3))

    def test_schema_change_marks_existing_generation_requires_rebuild(self):
        repo = Repository({('p', 'milvus'): active_cursor(schema_version=2)}, {'p': events(1)})
        adapter = Adapter()
        try:
            result = sync_for(repo, adapter).drain_once('p', 'milvus')
        except ProjectionRebuildRequired:
            self.fail('schema mismatch must publish requires_rebuild instead of escaping the drain')
        self.assertEqual(result.status, 'requires_rebuild')
        self.assertEqual(repo.cursors[('p', 'milvus')]['status'], 'requires_rebuild')
        self.assertEqual(adapter.applied, [])

    def test_failed_generation_waits_for_explicit_build(self):
        repo = Repository({('p', 'milvus'): active_cursor(status='failed')}, {'p': events(1)})
        adapter = Adapter()
        self.assertEqual(sync_for(repo, adapter).drain_once('p', 'milvus').status, 'requires_rebuild')
        self.assertEqual(adapter.applied, [])

    def test_expired_unconfirmed_batch_cannot_be_reclaimed_or_audited(self):
        cursor = active_cursor(status='applying', claim_owner='old',
            claim_token='expired', claim_until=instant(-10),
            claimed_from_seq=1, claimed_to_seq=1)
        repo = Repository({('p', 'milvus'): cursor}, {'p': events(1)})
        adapter = Adapter()
        self.assertEqual(sync_for(repo, adapter).drain_once('p', 'milvus').status, 'requires_rebuild')
        self.assertEqual(cursor['status'], 'requires_rebuild')
        self.assertIsNone(repo.projections.claim_maintenance('p', 'milvus', 3, 'audit'))
        self.assertEqual(adapter.applied, [])

    def test_normal_batches_and_clean_restart_keep_current_generation(self):
        repo = Repository({('p', 'milvus'): active_cursor()}, {'p': events(2)})
        adapter = Adapter()
        self.assertEqual(int(sync_for(repo, adapter).drain_once('p', 'milvus', limit=1)), 1)
        self.assertEqual(repo.cursors[('p', 'milvus')]['status'], 'pending')
        self.assertEqual(int(sync_for(repo, adapter).drain_once('p', 'milvus', limit=1)), 1)
        self.assertEqual(repo.cursors[('p', 'milvus')]['status'], 'ready')
        self.assertEqual(adapter.applied, [('p', 1, COLLECTION), ('p', 2, COLLECTION)])
        self.assertEqual(adapter.builds, [('p', 1, default_catalog().fingerprint)] * 2)
        self.assertEqual(repo.cursors[('p', 'milvus')]['rebuild_state']['index_version'], 1)

    def test_live_batch_lease_is_untouched(self):
        cursor = active_cursor(status='applying', claim_owner='other',
            claim_token='live', claim_until=instant(30),
            claimed_from_seq=1, claimed_to_seq=1)
        repo = Repository({('p', 'milvus'): cursor}, {'p': events(1)})
        self.assertEqual(sync_for(repo).drain_once('p', 'milvus').status, 'leased')
        self.assertEqual(cursor['claim_token'], 'live')

    def test_live_explicit_build_is_never_claimed_by_delivery_or_audit(self):
        cursor = active_cursor(status='rebuilding', active_destination=None,
            claim_owner='page-job', claim_token='build', claim_until=instant(30),
            rebuild_state={'build_id': 'chosen', 'phase': 'snapshot'})
        repo = Repository({('p', 'milvus'): cursor}, {'p': events(1)})
        adapter = Adapter()
        self.assertEqual(sync_for(repo, adapter).drain_once('p', 'milvus').status, 'building')
        self.assertIsNone(repo.projections.claim_maintenance('p', 'milvus', 3, 'audit'))
        self.assertEqual(cursor['claim_token'], 'build')
        self.assertEqual(adapter.applied, [])

    def test_expired_explicit_build_waits_for_page_retry(self):
        cursor = active_cursor(status='rebuilding', active_destination=None,
            claim_owner='page-job', claim_token='build', claim_until=instant(-1),
            rebuild_state={'build_id': 'chosen', 'phase': 'snapshot'})
        repo = Repository({('p', 'milvus'): cursor}, {'p': events(1)})
        self.assertEqual(sync_for(repo).drain_once('p', 'milvus').status, 'requires_rebuild')
        self.assertEqual(cursor['status'], 'requires_rebuild')

    def test_clean_expired_empty_claim_allows_the_next_batch(self):
        cursor = active_cursor(claim_owner='old', claim_token='empty',
            claim_until=instant(-1), claimed_from_seq=1, claimed_to_seq=0)
        repo = Repository({('p', 'milvus'): cursor}, {'p': events(1)})
        self.assertEqual(int(sync_for(repo).drain_once('p', 'milvus')), 1)
        self.assertEqual(cursor['status'], 'ready')

    def test_other_projects_continue_when_one_project_requires_rebuild(self):
        repo = Repository({('broken', 'milvus'): active_cursor(status='failed'),
                           ('ready', 'milvus'): active_cursor()},
                          {project: events(1, project) for project in ('broken', 'ready', 'unselected')})
        adapter = Adapter()
        sync = sync_for(repo, adapter)
        sync.maintain_rollbacks = lambda target: 0
        worker = ProjectionWorker(repo, sync, cleanup_handlers={})
        worker._next_audit = float('inf')
        self.assertEqual(worker.run_once()['delivered'], 1)
        self.assertEqual(adapter.applied, [('ready', 1, COLLECTION)])
        self.assertNotIn(('unselected', 'milvus'), repo.cursors)

    def test_audit_does_not_repair_before_incremental_watermark_is_caught_up(self):
        repo = Repository({('p', 'milvus'): active_cursor()}, {'p': events(1)})
        self.assertIsNone(repo.projections.claim_maintenance('p', 'milvus', 3, 'audit'))

    def test_healthy_active_generation_still_allows_audit(self):
        repo = Repository({('p', 'milvus'): active_cursor()})
        adapter = Adapter()
        ProjectionWorker(repo, sync_for(repo, adapter), cleanup_handlers={}).audit_project('p')
        self.assertEqual(len(adapter.audited), 1)

    def test_audit_schema_failure_invalidates_only_the_selected_generation(self):
        cursor = active_cursor()
        repo = Repository({('p', 'milvus'): cursor, ('other', 'milvus'): active_cursor()})
        class UnhealthyAudit(Adapter):
            def audit_authority(self, *args):
                raise MilvusSchemaMismatch('generation disappeared')
        worker = ProjectionWorker(repo, sync_for(repo, UnhealthyAudit()), cleanup_handlers={})
        with self.assertRaises(MilvusSchemaMismatch):
            worker.audit_project('p')
        self.assertEqual(cursor['status'], 'requires_rebuild')
        self.assertEqual(repo.cursors[('other', 'milvus')]['status'], 'ready')

    def test_neo4j_initial_delivery_keeps_its_independent_lifecycle(self):
        repo = Repository(events={'p': events(1)})
        adapter = Adapter()
        sync = ProjectionSync(repo.projections, {'neo4j': adapter}, schema_version=3)
        self.assertEqual(int(sync.drain_once('p', 'neo4j')), 1)
        self.assertEqual(adapter.applied, [('p', 1, None)])

    def test_unselected_project_audit_does_not_claim_or_build(self):
        repo = Repository(events={'unselected': events(1, 'unselected')})
        adapter = Adapter()
        worker = ProjectionWorker(repo, sync_for(repo, adapter), cleanup_handlers={})
        worker.audit_project('unselected')
        self.assertEqual(adapter.audited, [])
        self.assertEqual(repo.cursors, {})

    def test_uncertain_write_requires_explicit_rebuild_and_cannot_retry(self):
        repo = Repository({('p', 'milvus'): active_cursor()}, {'p': events(1)})
        adapter = Adapter(error=TimeoutError('RPC completion unknown'))
        sync = sync_for(repo, adapter)
        self.assertEqual(sync.drain_once('p', 'milvus').status, 'requires_rebuild')
        self.assertEqual(repo.cursors[('p', 'milvus')]['status'], 'requires_rebuild')
        self.assertEqual(sync.drain_once('p', 'milvus').status, 'requires_rebuild')
        self.assertEqual(len(adapter.applied), 1)

    def test_missing_physical_generation_does_not_encode_or_create_collection(self):
        encoded, written, created = [], [], []
        class MissingDestination:
            collection_name = COLLECTION
            def check_collection(self, *, catalog):
                assert len(catalog.column_defs()) == 21
                raise MilvusSchemaMismatch('shared collection is missing')
            def ensure_collection(self, **kwargs):
                created.append(kwargs)
            def upsert_rows(self, rows, *, project_id, index_version, catalog):
                self.check_collection(catalog=catalog)
                written.extend(rows)
        encoder = SimpleNamespace(identity='test', encode=lambda texts: (
            encoded.extend(texts) or [[1.0] for _ in texts]))
        repo = Repository({('p', 'milvus'): active_cursor()}, {'p': events(1)})
        adapter = MilvusProjectionAdapter(MissingDestination(), encoder=encoder)
        with self.assertRaises(MilvusSchemaMismatch):
            sync_for(repo, adapter).drain_once('p', 'milvus')
        self.assertEqual(repo.cursors[('p', 'milvus')]['status'], 'requires_rebuild')
        self.assertEqual(encoded, [])
        self.assertEqual(written, [])
        self.assertEqual(created, [])

    def test_normal_store_operations_do_not_create_missing_collection(self):
        created = []
        store = MilvusStore(dim=1, encoder_identity='test')
        store._client = SimpleNamespace(has_collection=lambda name, **kwargs: False)
        store._create_collection = lambda **kwargs: created.append(kwargs) or store
        catalog = default_catalog()
        row = dict(events(1)[0]['payload']['_milvus_projection']['row'], embedding=[1.0])
        for operation in (
                lambda: store.upsert_rows([row], project_id='p', index_version=1, catalog=catalog),
                lambda: store.delete_records(['r1'], project_id='p', index_version=1, catalog=catalog),
                lambda: store.validate_projection({}, project_id='p', index_version=1, catalog=catalog)):
            with self.subTest(operation=operation), self.assertRaises(MilvusSchemaMismatch):
                operation()
        self.assertEqual(created, [])

    def test_run_once_and_write_nudge_leave_unselected_project_unbuilt(self):
        from knowledge_service.services.service import KnowledgeService
        repo = Repository(events={'unselected': events(1, 'unselected')})
        adapter = Adapter()
        sync = sync_for(repo, adapter)
        sync.maintain_rollbacks = lambda target: 0
        worker = ProjectionWorker(repo, sync, cleanup_handlers={})
        worker.run_once()
        service = KnowledgeService.__new__(KnowledgeService)
        service.projection_drain = worker.drain_project
        service._drain_projections('unselected')
        self.assertEqual(adapter.applied, [])
        self.assertEqual(repo.cursors, {})

    def test_page_job_still_explicitly_rebuilds_only_selected_project(self):
        rebuilt = []
        service = SimpleNamespace(
            reconnect_vector_store=lambda: SimpleNamespace(dim=4),
            projection_sync=SimpleNamespace(rebuild=lambda *args: (
                rebuilt.append(args) or {'destination': COLLECTION, 'indexed': 0, 'index_version': 1})),
            data=SimpleNamespace(current_records=lambda project: []),
            encoder=SimpleNamespace(identity='test'))
        result = job_handlers(service, None)['semantic_index'](
            {'rebuild': True}, 'selected', lambda *args: None, 'task')
        self.assertEqual(rebuilt, [('selected', 'milvus')])
        self.assertEqual(result['restored_projects'], ['selected'])
        self.assertEqual(result['destination'], COLLECTION)
        self.assertEqual(result['index_version'], 1)


if __name__ == '__main__':
    unittest.main()
