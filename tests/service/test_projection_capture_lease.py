"""Exercise real capture claims with a tiny TTL and no PostgreSQL."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from knowledge_service.repository.projection_store import ProjectionLeaseLost, ProjectionStore
from knowledge_service.services.projection_sync import ProjectionSync
from knowledge_service.domain.search_filter_catalog import default_catalog


class Result:
    rowcount = 1
    def __init__(self, row=None, rows=()):
        self.row, self.rows = row, rows
    def fetchone(self):
        return self.row
    def fetchall(self):
        return self.rows


class Database:
    def __init__(self, repository, *, reader=False):
        self.repository, self.reader = repository, reader
    def execute(self, statement, params=()):
        sql = ' '.join(statement.split())
        repo = self.repository
        if sql.startswith('INSERT INTO projection_cursors'):
            return Result()
        if sql.startswith('INSERT INTO project_projection_sequences'):
            return Result()
        if sql.startswith('SELECT * FROM projection_cursors'):
            return Result(dict(repo.cursor))
        if sql.startswith('SELECT version_id FROM projection_rebuild_snapshots'):
            ids = repo.snapshot_ids
            if len(params) == 5:
                ids = ids[params[3]:params[3] + params[4]]
            return Result(rows=[{'version_id': value} for value in ids])
        if sql.startswith('DELETE FROM projection_rebuild_snapshots'):
            repo.snapshot_ids.clear()
            return Result()
        if sql.startswith('UPDATE projection_cursors SET schema_version='):
            schema, applied, state, owner, token, expires, first, last, updated, *_ = params
            repo.cursor.update(schema_version=schema, applied_seq=applied,
                rebuild_state=json.loads(state), claim_owner=owner, claim_token=token,
                claim_until=expires, claimed_from_seq=first, claimed_to_seq=last,
                status='rebuilding')
            return Result()
        if sql.startswith('UPDATE projection_cursors SET claim_until='):
            repo.cursor['claim_until'] = params[0]
            repo.renewals += 1
            return Result()
        if sql.startswith('UPDATE projection_cursors SET applied_seq='):
            applied, state, first, last, *_ = params
            repo.cursor.update(applied_seq=applied, rebuild_state=json.loads(state),
                               claimed_from_seq=first, claimed_to_seq=last)
            return Result()
        if sql.startswith('SELECT r.*, EXISTS'):
            assert self.reader, 'Milvus capture must read through an independent view'
            repo.read_started.set()
            time.sleep(repo.delay)
            rows = sorted(repo.rows, key=lambda row: (row['id'], row['version_id']))
            if 'r.version_id=ANY(?::text[])' in sql:
                rows = [row for row in rows if row['version_id'] in params[1]]
            elif 'r.id>?' in sql:
                rows = [row for row in rows if row['id'] > params[1]]
            return Result(rows=rows[:params[-1]])
        raise AssertionError('unexpected capture SQL: ' + sql)
    def executemany(self, statement, params):
        rows = list(params)
        self.repository.receipt_batch_sizes.append(len(rows))
        self.repository.snapshot_ids.extend(row[-1] for row in rows)
        return Result()


class CaptureRepository:
    def __init__(self, *, resumed=False, delay=0, lock_delay=0, count=2):
        self._lock = threading.RLock()
        self.delay, self.lock_delay = delay, lock_delay
        self.read_started = threading.Event()
        self.renewals = 0
        self.receipt_batch_sizes = []
        self.snapshot_ids = ['v1', 'v2'] if resumed else []
        self.rows = [{'project_id': 'project', 'id': f'r{i:06}', 'version': 1,
            'version_id': f'v{i}', 'recorded_at': '2026-01-01T00:00:00Z',
            'superseded_at': None, 'ontology_known': False,
            'payload': json.dumps({'kind': 'chunk', 'text': 'x', 'metadata': {}})}
            for i in range(1, count + 1)]
        self.cursor = {'schema_version': 2, 'applied_seq': 3 if resumed else 0,
            'status': 'failed', 'claim_owner': None, 'claim_token': None,
            'claim_until': None, 'rebuild_state': ({'build_id': 'build-1',
                'phase': 'snapshot', 'snapshot_seq': 3, 'applied_seq': 3,
                'index_version': 1, 'catalog': default_catalog().to_dict(),
                'embedding_model': 'test', 'dim': 1}
                if resumed else None)}
        self._db = Database(self)
    @contextmanager
    def _transaction(self):
        with self._lock:
            if self.lock_delay:
                delay, self.lock_delay = self.lock_delay, 0
                time.sleep(delay)
            yield
    @contextmanager
    def read_snapshot(self):
        with self._lock:
            yield
    @contextmanager
    def independent_read_view(self):
        repo = self
        class Reader:
            _db = Database(repo, reader=True)
            _lock = threading.RLock()
            _is_read_view = True
            @contextmanager
            def independent_read_view(self):
                yield self
            class projections:
                @staticmethod
                def project_watermark(project_id):
                    return 3
            def current_records(self, project_id):
                repo.read_started.set()
                time.sleep(repo.delay)
                return list(repo.rows)
            def records_by_version_ids(self, project_id, ids):
                repo.read_started.set()
                time.sleep(repo.delay)
                return list(repo.rows)
        yield Reader()
    def current_records(self, project_id):
        time.sleep(self.delay)
        return list(self.rows)
    def records_by_version_ids(self, project_id, ids):
        with self._lock:
            time.sleep(self.delay)
            return list(self.rows)


class ShortClaimStore(ProjectionStore):
    @staticmethod
    def _expiry(seconds):
        return (datetime.now(timezone.utc) + timedelta(seconds=.08)).isoformat()
    def project_watermark(self, project_id):
        return 3
    def mark_failed(self, claimed, exc, schema_version=None):
        pass


class CaptureLeaseTests(unittest.TestCase):
    def begin(self, repository):
        store = ShortClaimStore(repository)
        sync = ProjectionSync(store, {}, schema_version=2)
        sync.REBUILD_HEARTBEAT_SECONDS = .01
        result = store.begin_rebuild('project', 'milvus', 2, 'owner',
                                    lease_guard=sync._rebuild_lease_guard,
                                    embedding_model='test', dim=1)
        result['claim'].assert_matches(repository.cursor)
        # Hydration now occurs when the bounded captured view is consumed by
        # preflight/loading, under the same external-work heartbeat guard.
        with sync._rebuild_lease_guard(result['claim']):
            list(result['records'])
        return result

    def test_waiting_for_writer_lock_does_not_consume_a_new_claim(self):
        self.begin(CaptureRepository(resumed=True, lock_delay=.12))

    def test_slow_initial_capture_keeps_the_claim_alive(self):
        repository = CaptureRepository(delay=.25)
        result = self.begin(repository)
        self.assertEqual(result['snapshot_seq'], 3)
        self.assertEqual(result['state']['phase'], 'snapshot')
        self.assertEqual(result['state']['index_version'], 1)
        self.assertEqual(result['state']['embedding_model'], 'test')
        self.assertEqual(repository.snapshot_ids, ['v1', 'v2'])
        self.assertGreater(repository.renewals, 5)

    def test_slow_resume_hydration_keeps_the_claim_alive(self):
        repository = CaptureRepository(resumed=True, delay=.25)
        self.begin(repository)
        self.assertGreater(repository.renewals, 5)

    def test_initial_capture_persists_snapshot_receipts_in_bounded_batches(self):
        repository = CaptureRepository(count=1200)
        self.begin(repository)
        self.assertEqual(len(repository.snapshot_ids), 1200)
        self.assertGreater(len(repository.receipt_batch_sizes), 1)
        self.assertLessEqual(max(repository.receipt_batch_sizes), 500)

    def test_capture_and_resume_reading_leave_the_writer_connection_available(self):
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                repository = CaptureRepository(resumed=resumed, delay=.25)
                failures = []
                def run():
                    try:
                        self.begin(repository)
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                thread = threading.Thread(target=run)
                thread.start()
                try:
                    self.assertTrue(repository.read_started.wait(2))
                    acquired = repository._lock.acquire(timeout=.04)
                    self.assertTrue(acquired, 'snapshot reading blocked the writer')
                    if acquired:
                        repository._lock.release()
                finally:
                    thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])

    def test_lost_capture_claim_never_publishes_a_snapshot_receipt(self):
        repository = CaptureRepository(delay=.25)
        def take_over():
            with repository._lock:
                repository.cursor['claim_token'] = 'new-owner'
        timer = threading.Timer(.04, take_over)
        timer.start()
        try:
            with self.assertRaises(ProjectionLeaseLost):
                self.begin(repository)
        finally:
            timer.cancel()
        self.assertEqual(repository.snapshot_ids, [])
        self.assertEqual(repository.cursor['rebuild_state']['phase'], 'capturing')


if __name__ == '__main__':
    unittest.main()
