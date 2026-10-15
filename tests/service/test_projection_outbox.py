from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge_service.repository.projection_store import (
    ClaimedBatch, ProjectionGap, ProjectionLeaseLost,
    ProjectionRebuildRequired, ProjectionStore, claim_is_available,
    claim_matches, contiguous_end, projection_is_ready, rebuild_matches,
    rebuild_needs_capture,
)
from knowledge_service.repository.core import Repository


class RecordingProjectionStore(ProjectionStore):
    def __init__(self):
        self.events = []

    def enqueue(self, project_id, operation, version_id, payload):
        self.events.append((project_id, operation, version_id, payload))
        return len(self.events)


class ClaimDatabase:
    class Cursor:
        rowcount = 1

        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    def __init__(self, cursor):
        self.cursor = cursor
        self.statements = []

    def execute(self, statement, params=()):
        normalized = ' '.join(statement.split())
        self.statements.append(normalized)
        if normalized.startswith('INSERT INTO projection_cursors'):
            return self.Cursor()
        if normalized.startswith('SELECT * FROM projection_cursors'):
            return self.Cursor(dict(self.cursor))
        raise AssertionError(f'claim must stop before cursor mutation: {normalized}')


class ClaimRepository:
    def __init__(self, cursor):
        self._db = ClaimDatabase(cursor)

    @contextmanager
    def _transaction(self):
        yield


def test_record_change_tombstones_old_version_before_upserting_new_version():
    store = RecordingProjectionStore()
    old = {'id': 'rule', 'version_id': 'v1', 'text': 'old'}
    new = {'id': 'rule', 'version_id': 'v2', 'text': 'new'}

    store.enqueue_record_change('project', old, new)

    assert [(event[1], event[2]) for event in store.events] == [
        ('tombstone', 'v1'), ('upsert', 'v2')]


def test_deleted_record_only_emits_tombstone_for_projected_old_version():
    store = RecordingProjectionStore()
    old = {'id': 'rule', 'version_id': 'v1', 'text': 'old'}
    deleted = {
        'id': 'rule', 'version_id': 'v2', 'text': 'deleted',
        'metadata': {'_deleted': True},
    }

    store.enqueue_record_change('project', old, deleted)

    assert [(event[1], event[2]) for event in store.events] == [
        ('tombstone', 'v1')]


def test_contiguous_ack_rejects_a_gap():
    assert contiguous_end(4, [{'seq': 5}, {'seq': 6}]) == 6
    with pytest.raises(ProjectionGap, match='expected 5, got 6'):
        contiguous_end(4, [{'seq': 6}])


def test_projection_ready_requires_exact_watermark_schema_and_ready_status():
    cursor = {'schema_version': 2, 'applied_seq': 8, 'status': 'ready'}
    assert projection_is_ready(8, cursor, 2)
    assert not projection_is_ready(9, cursor, 2)
    assert not projection_is_ready(8, {**cursor, 'schema_version': 1}, 2)
    assert not projection_is_ready(8, {**cursor, 'status': 'failed'}, 2)
    assert not projection_is_ready(8, None, 2)


def test_claim_lease_blocks_every_concurrent_claim_including_same_owner():
    now = '2026-01-01T00:00:00Z'
    future = '2026-01-01T00:00:30Z'
    past = '2025-12-31T23:59:59Z'
    assert not claim_is_available('token-a', future, now)
    assert claim_is_available('token-a', past, now)
    assert claim_is_available(None, None, now)


def test_schema_mismatch_requires_rebuild_before_claiming_or_reading_outbox():
    cursor = {
        'schema_version': 1, 'applied_seq': 8, 'status': 'ready',
        'claim_owner': None, 'claim_token': None, 'claim_until': None,
        'rebuild_state': None,
    }
    repository = ClaimRepository(cursor)

    with pytest.raises(ProjectionRebuildRequired) as raised:
        ProjectionStore(repository).claim_contiguous_batch(
            'project', 'milvus', schema_version=2, owner='worker')

    assert raised.value.requires_rebuild is True
    assert raised.value.current_schema == 1
    assert raised.value.required_schema == 2
    assert cursor['status'] == 'ready'
    assert cursor['claim_token'] is None
    assert len(repository._db.statements) == 2


def test_failed_rebuild_cannot_be_claimed_by_ordinary_drain():
    cursor = {
        'schema_version': 2, 'applied_seq': 8, 'status': 'failed',
        'claim_owner': None, 'claim_token': None, 'claim_until': None,
        'rebuild_state': {'build_id': 'build-1', 'phase': 'validating'},
    }
    repository = ClaimRepository(cursor)

    claimed = ProjectionStore(repository).claim_contiguous_batch(
        'project', 'milvus', schema_version=2, owner='worker')

    assert claimed is None
    assert cursor['status'] == 'failed'
    assert cursor['claim_token'] is None
    assert len(repository._db.statements) == 2


def test_late_ack_or_failure_token_cannot_mutate_newer_claim():
    batch = ClaimedBatch(
        events=({'seq': 5}, {'seq': 6}), owner='worker', token='old-token',
        expires_at='2026-01-01T00:00:30Z', from_seq=5, to_seq=6)
    cursor = {
        'claim_owner': 'worker', 'claim_token': 'new-token',
        'claim_until': '2026-01-01T00:01:00Z',
        'claimed_from_seq': 5, 'claimed_to_seq': 6,
    }
    assert not claim_matches(cursor, batch, '2026-01-01T00:00:10Z')
    with pytest.raises(ProjectionLeaseLost):
        batch.assert_matches(cursor, '2026-01-01T00:00:10Z')


def test_rebuild_cas_requires_build_schema_status_and_live_lease():
    batch = ClaimedBatch(
        events=(), owner='worker', token='build-token',
        expires_at='2026-01-01T00:00:30Z', from_seq=4, to_seq=3,
        build_id='build-1')
    cursor = {
        'schema_version': 2, 'status': 'rebuilding',
        'claim_owner': 'worker', 'claim_token': 'build-token',
        'claim_until': '2026-01-01T00:00:30Z',
        'rebuild_state': {'build_id': 'build-1'},
        'claimed_from_seq': 4, 'claimed_to_seq': 3,
    }
    assert rebuild_matches(cursor, batch, 2, '2026-01-01T00:00:10Z')
    assert not rebuild_matches({**cursor, 'schema_version': 3}, batch, 2,
                               '2026-01-01T00:00:10Z')
    assert not rebuild_matches({**cursor, 'status': 'ready'}, batch, 2,
                               '2026-01-01T00:00:10Z')


def test_capturing_state_without_snapshot_sequence_is_recaptured():
    assert rebuild_needs_capture({'build_id': 'build-1', 'phase': 'capturing'})
    assert not rebuild_needs_capture({
        'build_id': 'build-1', 'phase': 'snapshot_loading', 'snapshot_seq': 3})


def test_projection_migration_defines_locked_sequence_outbox_and_cursors():
    migration = (Path(__file__).resolve().parents[2] / 'knowledge_service' /
                 'repository' / 'migrations' /
                 '0016_projection_outbox.sql').read_text(encoding='utf-8')

    assert 'project_projection_sequences' in migration
    assert 'projection_outbox' in migration
    assert 'projection_cursors' in migration
    assert 'UNIQUE (project_id, seq)' in migration
    assert 'rollback_until' in migration
    assert 'claim_token' in migration
    assert 'claimed_from_seq' in migration and 'claimed_to_seq' in migration
    assert 'previous_destination' in migration
    assert 'rollback_applied_seq' in migration
    assert 'projection_rebuild_snapshots' in migration


def test_rollback_deadline_is_seven_days_after_switch():
    switched_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert ProjectionStore.rollback_deadline(switched_at).isoformat() == (
        '2026-01-08T00:00:00+00:00')


def test_repository_put_routes_supersede_and_upsert_through_projection_store():
    class Cursor:
        rowcount = 1

        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, params=()):
            if statement.startswith('SELECT version,recorded_at'):
                return Cursor({
                    'id': 'rule', 'version': 1, 'version_id': 'v1',
                    'recorded_at': '2026-01-01T00:00:00Z',
                    'payload': '{"id":"rule","kind":"entity","text":"old"}',
                })
            return Cursor()

    repository = Repository.__new__(Repository)
    repository._db = Database()
    repository._validate_record = lambda record: dict(record)
    repository._refresh_fts_record = lambda project_id, record: None
    repository.projections = RecordingProjectionStore()

    saved = repository._put('project', {
        'id': 'rule', 'kind': 'entity', 'text': 'new',
    }, expected_version=1, recorded_at='2026-01-02T00:00:00Z')

    assert [(event[1], event[2]) for event in repository.projections.events] == [
        ('tombstone', 'v1'), ('upsert', saved['version_id'])]
