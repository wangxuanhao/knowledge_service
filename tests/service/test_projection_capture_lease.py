"""Exercise real capture claims with a tiny TTL and no PostgreSQL."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from importlib import import_module
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from knowledge_service.repository.projection_store import ProjectionLeaseLost, ProjectionStore
from knowledge_service.services.projection_sync import ProjectionSync


@pytest.fixture
def postgres_backend():
    """The capture and lease I/O here are doubles; do not require PostgreSQL."""
    return None


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
        if sql.startswith('SELECT version_id,projection_input FROM projection_rebuild_snapshots'):
            assert self.reader, 'receipt hydration must use an independent view'
            return Result(rows=repo.receipts[params[3]:params[3] + params[4]])
        if sql.startswith('DELETE FROM projection_rebuild_snapshots'):
            repo.receipts.clear()
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
            repo.renewed.notify_all()
            return Result()
        if sql.startswith('UPDATE projection_cursors SET applied_seq='):
            applied, state, first, last, *_ = params
            repo.cursor.update(applied_seq=applied, rebuild_state=json.loads(state),
                               claimed_from_seq=first, claimed_to_seq=last)
            return Result()
        if sql.startswith('SELECT r.*, (o.id IS NOT NULL) AS ontology_known'):
            assert self.reader, 'Milvus capture must read through an independent view'
            assert params[0] == 'project'
            rows = sorted(repo.rows, key=lambda row: (row['id'], row['version_id']))
            if 'r.version_id=ANY(?::text[])' in sql:
                repo.version_reads += 1
                rows = [row for row in rows if row['version_id'] in params[1]]
            else:
                repo.capture_reads += 1
                if 'r.id>?' in sql:
                    rows = [row for row in rows if row['id'] > params[1]]
            if rows:
                repo.read_started.set()
                if repo.on_read is not None:
                    repo.on_read()
                repo.simulate_slow_read()
            return Result(rows=rows[:params[-1]])
        raise AssertionError('unexpected capture SQL: ' + sql)
    def executemany(self, statement, params):
        rows = list(params)
        self.repository.receipt_batch_sizes.append(len(rows))
        self.repository.receipts.extend(
            {'version_id': version, 'projection_input': body}
            for _, _, _, _, version, body in rows)
        return Result()


class CaptureRepository:
    TTL_SECONDS = 2

    def __init__(self, *, resumed=False, slow_read=False, lock_delay=0, count=2, on_read=None):
        self._lock = threading.RLock()
        self.renewed = threading.Condition(self._lock)
        self.clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.slow_read, self.lock_delay = slow_read, lock_delay
        self.read_started = threading.Event()
        self.on_read = on_read
        self.renewals = self.capture_reads = self.version_reads = 0
        self.receipt_batch_sizes = []
        # Before preflight a resumable snapshot has no catalog. Legacy
        # receipts with null bodies exercise slow by-version hydration.
        self.receipts = [{'version_id': version, 'projection_input': None}
                         for version in ('v1', 'v2')] if resumed else []
        self.rows = [{'project_id': 'project', 'id': f'r{i:06}', 'version': 1,
            'version_id': f'v{i}', 'recorded_at': '2026-01-01T00:00:00Z',
            'superseded_at': None, 'ontology_known': False,
            'payload': json.dumps({'kind': 'chunk', 'text': 'x', 'metadata': {}})}
            for i in range(1, count + 1)]
        self.cursor = {'schema_version': 2, 'applied_seq': 3 if resumed else 0,
            'status': 'failed', 'claim_owner': None, 'claim_token': None,
            'claim_until': None, 'rebuild_state': ({'build_id': 'build-1',
                'phase': 'snapshot', 'snapshot_seq': 3, 'applied_seq': 3,
                'index_version': 1,
                'embedding_model': 'test', 'dim': 1}
                if resumed else None)}
        self._db = Database(self)
    @property
    def snapshot_ids(self):
        return [row['version_id'] for row in self.receipts]
    def source_context_diagnostics(self, project_id):
        assert project_id == 'project'
        # Only chunks in this fixture; no accepted facts need backfill.
        return {'missing': 0, 'invalid': 0, 'missing_active': 0, 'invalid_active': 0}

    def simulate_slow_read(self):
        if self.slow_read:
            # Move the logical clock past the original expiration in steps.
            # A real background renewal must occur at each step while the
            # read is blocked; foreground calls cannot satisfy this wait.
            with self.renewed:
                for _ in range(self.TTL_SECONDS + 1):
                    previous = self.renewals
                    self.clock += timedelta(seconds=1)
                    assert self.renewed.wait_for(lambda: self.renewals > previous, timeout=10), (
                        'No heartbeat renewal during capture/hydration')

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self.lock_delay:
                delay, self.lock_delay = self.lock_delay, 0
                self.clock += timedelta(seconds=delay)
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
        yield Reader()


class ShortClaimStore(ProjectionStore):
    def _expiry(self, seconds):
        return (self.repository.clock + timedelta(seconds=self.repository.TTL_SECONDS)).isoformat()
    def project_watermark(self, project_id):
        return 3
    def mark_failed(self, claimed, exc, schema_version=None):
        pass


class CaptureLeaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The short test lease measures capture, not cold dependency imports.
        for module in ('services.projection_runtime', 'repository.projection_snapshot',
                       'repository.projection_contexts'):
            import_module(f'knowledge_service.{module}')

    def begin(self, repository):
        with patch('knowledge_service.repository.projection_store.utc_now',
                   lambda: repository.clock.isoformat()):
            return self._begin(repository)

    def _begin(self, repository):
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
            self.assertEqual([item.record['version_id'] for item in result['records']],
                             repository.snapshot_ids)
        return result

    def test_waiting_for_writer_lock_does_not_consume_a_new_claim(self):
        result = self.begin(CaptureRepository(resumed=True, lock_delay=3))
        self.assertEqual(result['state']['build_id'], 'build-1')

    def test_slow_initial_capture_keeps_the_claim_alive(self):
        repository = CaptureRepository(slow_read=True)
        result = self.begin(repository)
        self.assertEqual(result['snapshot_seq'], 3)
        self.assertEqual(result['state']['phase'], 'snapshot')
        self.assertEqual(result['state']['index_version'], 1)
        self.assertEqual(result['state']['embedding_model'], 'test')
        self.assertEqual(repository.snapshot_ids, ['v1', 'v2'])
        self.assertEqual(repository.version_reads, 0, 'new receipts carry frozen bodies')
        self.assertGreater(repository.renewals, 5)

    def test_slow_resume_hydration_keeps_the_claim_alive(self):
        repository = CaptureRepository(resumed=True, slow_read=True)
        result = self.begin(repository)
        self.assertEqual(result['state']['build_id'], 'build-1')
        self.assertEqual(repository.capture_reads, 0)
        self.assertEqual(repository.version_reads, 2)
        self.assertEqual(repository.receipt_batch_sizes, [])
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
                release_read = threading.Event()
                repository = CaptureRepository(
                    resumed=resumed, on_read=lambda: self.assertTrue(release_read.wait(15)))
                failures = []
                def run():
                    try:
                        self.begin(repository)
                    except Exception as exc:
                        failures.append(exc)
                thread = threading.Thread(target=run)
                thread.start()
                try:
                    self.assertTrue(repository.read_started.wait(15), f'read did not start: {failures!r}')
                    acquired = repository._lock.acquire(timeout=1)
                    self.assertTrue(acquired, 'snapshot reading blocked the writer')
                    if acquired:
                        repository._lock.release()
                finally:
                    release_read.set()
                    thread.join(15)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])

    def test_lost_capture_claim_never_publishes_a_snapshot_receipt(self):
        repository = CaptureRepository()
        def take_over():
            with repository._lock:
                repository.cursor['claim_token'] = 'new-owner'
        repository.on_read = take_over
        with self.assertRaises(ProjectionLeaseLost):
            self.begin(repository)
        self.assertEqual(repository.snapshot_ids, [])
        self.assertEqual(repository.cursor['rebuild_state']['phase'], 'capturing')


if __name__ == '__main__':
    unittest.main()
