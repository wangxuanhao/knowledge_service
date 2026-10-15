"""Durable ordering, fenced delivery, rebuild recovery, and readiness."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from ..core.time import normalize_time, utc_now


class ProjectionGap(RuntimeError):
    pass


class ProjectionLeaseLost(RuntimeError):
    pass


class ProjectionRebuildRequired(RuntimeError):
    """A normal delivery cannot cross a projection schema boundary."""

    requires_rebuild = True
    state = 'requires_rebuild'

    def __init__(self, project_id, target, *, current_schema, required_schema):
        self.project_id = project_id
        self.target = target
        self.current_schema = current_schema
        self.required_schema = required_schema
        super().__init__(
            f'projection rebuild required for {project_id}/{target}: '
            f'schema {current_schema} -> {required_schema}')


@dataclass
class ClaimedBatch:
    events: tuple
    owner: str
    token: str
    expires_at: str
    from_seq: int
    to_seq: int
    project_id: str = ''
    target: str = ''
    schema_version: int = 0
    build_id: str | None = None
    active_destination: str | None = None
    previous_destination: str | None = None
    rollback_until: str | None = None

    def assert_matches(self, cursor, now=None):
        now = now or utc_now()
        if not claim_matches(cursor, self, now):
            raise ProjectionLeaseLost('projection claim lease was lost')


def contiguous_end(applied_seq, events):
    expected = applied_seq + 1
    for event in events:
        actual = int(event['seq'])
        if actual != expected:
            raise ProjectionGap(f'projection gap: expected {expected}, got {actual}')
        expected += 1
    return expected - 1


def projection_is_ready(watermark, cursor, schema_version):
    return bool(cursor and cursor.get('status') == 'ready'
                and cursor.get('schema_version') == schema_version
                and cursor.get('applied_seq') == watermark)


def claim_is_available(claim_token, claim_until, now):
    return (not claim_token or not claim_until
            or normalize_time(claim_until, allow_none=False)
            <= normalize_time(now, allow_none=False))


def claim_matches(cursor, claimed, now):
    return bool(
        cursor
        and cursor.get('claim_owner') == claimed.owner
        and cursor.get('claim_token') == claimed.token
        and int(cursor.get('claimed_from_seq') or 0) == claimed.from_seq
        and int(cursor.get('claimed_to_seq') or 0) == claimed.to_seq
        and cursor.get('claim_until')
        and normalize_time(cursor['claim_until'], allow_none=False)
        > normalize_time(now, allow_none=False)
    )


def rebuild_matches(cursor, claimed, schema_version, now):
    state = (cursor or {}).get('rebuild_state') or {}
    return bool(claimed.build_id and claim_matches(cursor, claimed, now)
                and int(cursor.get('schema_version') or 0) == int(schema_version)
                and cursor.get('status') in {
                    'rebuilding', 'validating', 'switching', 'failed'}
                and state.get('build_id') == claimed.build_id)


def rebuild_needs_capture(state):
    return not state or 'snapshot_seq' not in state


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))


def _decode_row(row):
    result = dict(row)
    for name in ('payload', 'rebuild_state'):
        if isinstance(result.get(name), str):
            result[name] = json.loads(result[name])
    return result


class ProjectionStore:
    def __init__(self, repository):
        self.repository = repository
        self._db = repository._db

    @staticmethod
    def rollback_deadline(switched_at=None):
        return (switched_at or datetime.now(timezone.utc)) + timedelta(days=7)

    def enqueue(self, project_id, operation, version_id, payload):
        if operation not in {'upsert', 'tombstone'}:
            raise ValueError('projection operation must be upsert or tombstone')
        if not self._db.in_transaction:
            raise RuntimeError('projection outbox writes require repository transaction')
        self._db.execute(
            '''INSERT INTO project_projection_sequences(project_id,projection_seq)
               VALUES (?,0) ON CONFLICT(project_id) DO NOTHING''', (project_id,))
        row = self._db.execute(
            '''SELECT projection_seq FROM project_projection_sequences
               WHERE project_id=? FOR UPDATE''', (project_id,)).fetchone()
        seq = int(row['projection_seq']) + 1
        self._db.execute(
            '''UPDATE project_projection_sequences SET projection_seq=?
               WHERE project_id=?''', (seq, project_id))
        self._db.execute(
            '''INSERT INTO projection_outbox
               (project_id,seq,operation,version_id,payload,created_at)
               VALUES (?,?,?,?,?,?)''',
            (project_id, seq, operation, version_id, _json(payload), utc_now()))
        return seq

    def enqueue_record_change(self, project_id, old_record, new_record):
        sequences = []
        if old_record is not None:
            sequences.append(self.enqueue(
                project_id, 'tombstone', old_record['version_id'], {
                    'record_id': old_record['id'],
                    'version_id': old_record['version_id']}))
        if (new_record.get('metadata') or {}).get('_deleted') is not True:
            sequences.append(self.enqueue(
                project_id, 'upsert', new_record['version_id'], new_record))
        return sequences

    def project_watermark(self, project_id):
        row = self._db.execute(
            '''SELECT projection_seq FROM project_projection_sequences
               WHERE project_id=?''', (project_id,)).fetchone()
        return 0 if row is None else int(row['projection_seq'])

    def _ensure_cursor(self, project_id, target, schema_version):
        self._db.execute(
            '''INSERT INTO projection_cursors
               (project_id,target,schema_version,applied_seq,status,updated_at)
               VALUES (?,?,?,0,'pending',?)
               ON CONFLICT(project_id,target) DO NOTHING''',
            (project_id, target, schema_version, utc_now()))

    def cursor(self, project_id, target):
        row = self._db.execute(
            'SELECT * FROM projection_cursors WHERE project_id=? AND target=?',
            (project_id, target)).fetchone()
        return None if row is None else _decode_row(row)

    @staticmethod
    def _expiry(seconds):
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
            timespec='microseconds').replace('+00:00', 'Z')

    def claim_contiguous_batch(self, project_id, target, schema_version,
                               limit=100, after_seq=None, owner=None,
                               lease_seconds=30, build=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('projection batch limit must be 1..1000')
        owner = owner or target
        with self.repository._transaction():
            self._ensure_cursor(project_id, target, schema_version)
            cursor = _decode_row(self._db.execute(
                '''SELECT * FROM projection_cursors
                   WHERE project_id=? AND target=? FOR UPDATE''',
                (project_id, target)).fetchone())
            now = utc_now()
            if build is None:
                current_schema = int(cursor.get('schema_version') or 0)
                if current_schema != int(schema_version):
                    raise ProjectionRebuildRequired(
                        project_id, target, current_schema=current_schema,
                        required_schema=int(schema_version))
                rebuild_state = cursor.get('rebuild_state') or {}
                if (cursor['status'] in {'rebuilding', 'validating', 'switching'}
                        or (rebuild_state.get('build_id')
                            and rebuild_state.get('phase') != 'ready')):
                    return None
                if not claim_is_available(
                        cursor.get('claim_token'), cursor.get('claim_until'), now):
                    return None
                base = int(cursor['applied_seq'])
            else:
                if not rebuild_matches(cursor, build, schema_version, now):
                    raise ProjectionLeaseLost('projection rebuild lease was lost')
                base = int(after_seq if after_seq is not None else cursor['applied_seq'])
            rows = self._db.execute(
                '''SELECT project_id,seq,operation,version_id,payload,created_at
                   FROM projection_outbox WHERE project_id=? AND seq>?
                   ORDER BY seq LIMIT ?''', (project_id, base, limit)).fetchall()
            events = tuple(_decode_row(row) for row in rows)
            if not events:
                return None
            contiguous_end(base, events)
            token, expires = str(uuid4()), self._expiry(lease_seconds)
            start, end = int(events[0]['seq']), int(events[-1]['seq'])
            self._db.execute(
                '''UPDATE projection_cursors SET
                       status=CASE WHEN status IN ('rebuilding','validating','switching')
                                   THEN status ELSE 'applying' END,
                       last_error=NULL,claim_owner=?,claim_token=?,claim_until=?,
                       claimed_from_seq=?,claimed_to_seq=?,updated_at=?
                   WHERE project_id=? AND target=?''',
                (owner, token, expires, start, end, now, project_id, target))
            return ClaimedBatch(
                events, owner, token, expires, start, end, project_id, target,
                schema_version, build.build_id if build else None,
                cursor.get('active_destination'), cursor.get('previous_destination'),
                cursor.get('rollback_until'))

    def renew_claim(self, claimed, lease_seconds=30):
        with self.repository._transaction():
            cursor = _decode_row(self._db.execute(
                '''SELECT * FROM projection_cursors
                   WHERE project_id=? AND target=? FOR UPDATE''',
                (claimed.project_id, claimed.target)).fetchone())
            claimed.assert_matches(cursor)
            expires = self._expiry(lease_seconds)
            result = self._db.execute(
                '''UPDATE projection_cursors SET claim_until=?,updated_at=?
                   WHERE project_id=? AND target=? AND claim_owner=?
                     AND claim_token=? AND claim_until>clock_timestamp()''',
                (expires, utc_now(), claimed.project_id, claimed.target,
                 claimed.owner, claimed.token))
            if result.rowcount != 1:
                raise ProjectionLeaseLost('projection claim renewal lost CAS')
            claimed.expires_at = expires
            return claimed

    def _locked_claim(self, claimed):
        cursor = _decode_row(self._db.execute(
            '''SELECT * FROM projection_cursors
               WHERE project_id=? AND target=? FOR UPDATE''',
            (claimed.project_id, claimed.target)).fetchone())
        claimed.assert_matches(cursor)
        if claimed.build_id and not rebuild_matches(
                cursor, claimed, claimed.schema_version, utc_now()):
            raise ProjectionLeaseLost('projection rebuild lease was lost')
        return cursor

    def ack_contiguous(self, claimed, schema_version):
        with self.repository._transaction():
            cursor = self._locked_claim(claimed)
            if int(cursor['schema_version']) != int(schema_version):
                raise ProjectionGap('projection schema changed while acknowledging')
            rows = self._db.execute(
                '''SELECT seq FROM projection_outbox
                   WHERE project_id=? AND seq BETWEEN ? AND ? ORDER BY seq''',
                (claimed.project_id, claimed.from_seq, claimed.to_seq)).fetchall()
            actual = [int(row['seq']) for row in rows]
            expected = [int(event['seq']) for event in claimed.events]
            if actual != expected:
                raise ProjectionGap('claimed events no longer match authoritative outbox')
            end = contiguous_end(int(cursor['applied_seq']), claimed.events)
            watermark = self.project_watermark(claimed.project_id)
            rebuilding = claimed.build_id is not None
            status = ('rebuilding' if rebuilding else
                      'ready' if end == watermark else 'pending')
            previous = cursor.get('previous_destination')
            rollback_seq = end if previous else int(cursor.get('rollback_applied_seq') or 0)
            if rebuilding:
                empty_from, empty_to = end + 1, end
                self._db.execute(
                    '''UPDATE projection_cursors SET applied_seq=?,rollback_applied_seq=?,
                           status=?,claimed_from_seq=?,claimed_to_seq=?,updated_at=?
                       WHERE project_id=? AND target=? AND claim_token=?''',
                    (end, rollback_seq, status, empty_from, empty_to, utc_now(),
                     claimed.project_id, claimed.target, claimed.token))
                claimed.events = ()
                claimed.from_seq, claimed.to_seq = empty_from, empty_to
            else:
                self._db.execute(
                    '''UPDATE projection_cursors SET applied_seq=?,rollback_applied_seq=?,
                           status=?,claim_owner=NULL,claim_token=NULL,claim_until=NULL,
                           claimed_from_seq=NULL,claimed_to_seq=NULL,updated_at=?
                       WHERE project_id=? AND target=? AND claim_token=?''',
                    (end, rollback_seq, status, utc_now(), claimed.project_id,
                     claimed.target, claimed.token))
            return end

    def mark_failed(self, claimed, error, schema_version=None):
        with self.repository._transaction():
            cursor = self._locked_claim(claimed)
            if schema_version is not None and int(cursor['schema_version']) != int(schema_version):
                raise ProjectionLeaseLost('projection failure schema CAS failed')
            result = self._db.execute(
                '''UPDATE projection_cursors SET status='failed',last_error=?,
                       claim_owner=NULL,claim_token=NULL,claim_until=NULL,
                       claimed_from_seq=NULL,claimed_to_seq=NULL,updated_at=?
                   WHERE project_id=? AND target=? AND claim_owner=? AND claim_token=?''',
                (str(error), utc_now(), claimed.project_id, claimed.target,
                 claimed.owner, claimed.token))
            if result.rowcount != 1:
                raise ProjectionLeaseLost('projection failure lost CAS')

    def projection_ready(self, project_id, target, schema_version):
        return projection_is_ready(self.project_watermark(project_id),
                                   self.cursor(project_id, target), schema_version)

    def delivery_state(self, project_id, target):
        cursor = self.cursor(project_id, target)
        if cursor is None:
            return 'empty'
        if not claim_is_available(
                cursor.get('claim_token'), cursor.get('claim_until'), utc_now()):
            return 'leased'
        return 'empty'

    def claim_maintenance(self, project_id, target, schema_version, owner,
                          lease_seconds=60):
        token, expires, now = str(uuid4()), self._expiry(lease_seconds), utc_now()
        with self.repository._transaction():
            self._ensure_cursor(project_id, target, schema_version)
            cursor = _decode_row(self._db.execute(
                '''SELECT * FROM projection_cursors
                   WHERE project_id=? AND target=? FOR UPDATE''',
                (project_id, target)).fetchone())
            if (not claim_is_available(cursor.get('claim_token'),
                                       cursor.get('claim_until'), now)
                    or cursor.get('status') in {
                        'rebuilding', 'validating', 'switching'}):
                return None
            seq = int(cursor.get('applied_seq') or 0)
            self._db.execute(
                '''UPDATE projection_cursors SET claim_owner=?,claim_token=?,
                       claim_until=?,claimed_from_seq=?,claimed_to_seq=?,updated_at=?
                   WHERE project_id=? AND target=?''',
                (owner, token, expires, seq + 1, seq, now, project_id, target))
        return ClaimedBatch((), owner, token, expires, seq + 1, seq,
                            project_id, target, schema_version)

    def maintenance_is_current(self, claimed, watermark):
        cursor = self.cursor(claimed.project_id, claimed.target)
        try:
            claimed.assert_matches(cursor)
        except ProjectionLeaseLost:
            return False
        return (normalize_time(cursor.get('claim_until'), allow_none=False)
                > normalize_time(utc_now(), allow_none=False)
                and self.project_watermark(claimed.project_id) == int(watermark))

    def renew_maintenance(self, project_id, target, owner, token,
                          lease_seconds=60):
        expires = self._expiry(lease_seconds)
        with self.repository._transaction():
            result = self._db.execute(
                '''UPDATE projection_cursors SET claim_until=?,updated_at=?
                   WHERE project_id=? AND target=? AND claim_owner=?
                     AND claim_token=? AND claim_until>clock_timestamp()
                     AND claimed_from_seq>claimed_to_seq''',
                (expires, utc_now(), project_id, target, owner, token))
            return result.rowcount == 1

    def release_maintenance(self, claimed):
        with self.repository._transaction():
            self._db.execute(
                '''UPDATE projection_cursors SET claim_owner=NULL,claim_token=NULL,
                       claim_until=NULL,claimed_from_seq=NULL,claimed_to_seq=NULL,
                       updated_at=? WHERE project_id=? AND target=? AND claim_token=?''',
                (utc_now(), claimed.project_id, claimed.target, claimed.token))

    def begin_rebuild(self, project_id, target, schema_version, owner):
        token, expires, now = str(uuid4()), self._expiry(30), utc_now()
        with self.repository._transaction():
            self._ensure_cursor(project_id, target, schema_version)
            existing = _decode_row(self._db.execute(
                '''SELECT * FROM projection_cursors
                   WHERE project_id=? AND target=? FOR UPDATE''',
                (project_id, target)).fetchone())
            state = existing.get('rebuild_state') or {}
            unfinished = state.get('build_id') and state.get('phase') != 'ready'
            if (not unfinished and not claim_is_available(
                    existing.get('claim_token'), existing.get('claim_until'), now)):
                raise ProjectionLeaseLost(
                    'projection delivery or maintenance is already leased')
            if unfinished and int(existing['schema_version']) != int(schema_version):
                raise ProjectionLeaseLost('projection rebuild schema CAS failed')
            if unfinished and not claim_is_available(
                    existing.get('claim_token'), existing.get('claim_until'), now):
                raise ProjectionLeaseLost('projection rebuild is already leased')
            build_id = state['build_id'] if unfinished else str(uuid4())
            if not unfinished:
                state = {'build_id': build_id, 'phase': 'capturing'}
            self._db.execute(
                '''UPDATE projection_cursors SET schema_version=?,applied_seq=?,
                       status='rebuilding',last_error=NULL,rebuild_state=?,
                       claim_owner=?,claim_token=?,claim_until=?,
                       claimed_from_seq=?,claimed_to_seq=?,updated_at=?
                   WHERE project_id=? AND target=?''',
                (schema_version, int(state.get('applied_seq', existing['applied_seq'])),
                 _json(state), owner, token, expires,
                 int(state.get('applied_seq', existing['applied_seq'])) + 1,
                 int(state.get('applied_seq', existing['applied_seq'])), now,
                 project_id, target))
        claimed = ClaimedBatch(
            (), owner, token, expires,
            int(state.get('applied_seq', existing['applied_seq'])) + 1,
            int(state.get('applied_seq', existing['applied_seq'])), project_id, target,
            schema_version, build_id, (existing or {}).get('active_destination'),
            (existing or {}).get('previous_destination'),
            (existing or {}).get('rollback_until'))
        needs_capture = not unfinished or rebuild_needs_capture(state)
        try:
            if needs_capture:
                with self.repository.read_snapshot():
                    snapshot_seq = self.project_watermark(project_id)
                    records = self.repository.current_records(project_id)
                state.update(phase='snapshot', snapshot_seq=snapshot_seq,
                             applied_seq=snapshot_seq)
                with self.repository._transaction():
                    cursor = self._locked_claim(claimed)
                    if not rebuild_matches(cursor, claimed, schema_version, utc_now()):
                        raise ProjectionLeaseLost('projection rebuild snapshot lost CAS')
                    self._db.execute(
                        '''DELETE FROM projection_rebuild_snapshots
                           WHERE project_id=? AND target=? AND build_id=?''',
                        (project_id, target, build_id))
                    self._db.executemany(
                        '''INSERT INTO projection_rebuild_snapshots
                           (project_id,target,build_id,ordinal,version_id)
                           VALUES (?,?,?,?,?)''',
                        [(project_id, target, build_id, index, row['version_id'])
                         for index, row in enumerate(records)])
                    result = self._db.execute(
                        '''UPDATE projection_cursors SET applied_seq=?,rebuild_state=?,
                               claimed_from_seq=?,claimed_to_seq=?,updated_at=?
                           WHERE project_id=? AND target=? AND claim_token=?''',
                        (snapshot_seq, _json(state), snapshot_seq + 1, snapshot_seq,
                         utc_now(), project_id, target, token))
                    if result.rowcount != 1:
                        raise ProjectionLeaseLost('projection rebuild snapshot lost CAS')
                claimed.from_seq, claimed.to_seq = snapshot_seq + 1, snapshot_seq
            else:
                snapshot_seq = int(state['snapshot_seq'])
                ids = [row['version_id'] for row in self._db.execute(
                    '''SELECT version_id FROM projection_rebuild_snapshots
                       WHERE project_id=? AND target=? AND build_id=?
                       ORDER BY ordinal''', (project_id, target, build_id)).fetchall()]
                records = self.repository.records_by_version_ids(project_id, ids)
        except Exception as exc:
            try:
                self.mark_failed(claimed, exc, schema_version)
            except ProjectionLeaseLost:
                pass
            raise
        return {'claim': claimed, 'snapshot_seq': snapshot_seq,
                'records': records, 'state': state}

    def record_rebuild_progress(self, claimed, schema_version, state):
        with self.repository._transaction():
            cursor = self._locked_claim(claimed)
            if not rebuild_matches(cursor, claimed, schema_version, utc_now()):
                raise ProjectionLeaseLost('projection rebuild progress lost CAS')
            result = self._db.execute(
                '''UPDATE projection_cursors SET rebuild_state=?,status=?,updated_at=?
                   WHERE project_id=? AND target=? AND claim_token=?''',
                (_json(state), state['phase'] if state['phase'] in {
                    'rebuilding', 'validating', 'switching'} else 'rebuilding',
                 utc_now(), claimed.project_id, claimed.target, claimed.token))
            if result.rowcount != 1:
                raise ProjectionLeaseLost('projection rebuild progress lost CAS')

    @contextmanager
    def alias_switch_guard(self, project_id, claimed, schema_version):
        with self.repository._transaction():
            cursor = self._locked_claim(claimed)
            if not rebuild_matches(cursor, claimed, schema_version, utc_now()):
                raise ProjectionLeaseLost('projection rebuild switch lost CAS')
            row = self._db.execute(
                '''SELECT projection_seq FROM project_projection_sequences
                   WHERE project_id=? FOR UPDATE''', (project_id,)).fetchone()
            yield int(row['projection_seq'])

    def finish_rebuild(self, claimed, schema_version, applied_seq,
                       rollback_until, state, previous_destination):
        with self.repository._transaction():
            cursor = self._locked_claim(claimed)
            if (not rebuild_matches(cursor, claimed, schema_version, utc_now())
                    or cursor['status'] != 'switching'):
                raise ProjectionLeaseLost('projection rebuild finish lost CAS')
            if int(applied_seq) != self.project_watermark(claimed.project_id):
                raise ProjectionGap('projection switch is stale')
            active = state['destination']
            result = self._db.execute(
                '''UPDATE projection_cursors SET status='ready',applied_seq=?,
                       active_destination=?,previous_destination=?,
                       rollback_until=?,rollback_applied_seq=?,rebuild_state=?,
                       last_error=NULL,claim_owner=NULL,claim_token=NULL,
                       claim_until=NULL,claimed_from_seq=NULL,claimed_to_seq=NULL,
                       updated_at=? WHERE project_id=? AND target=? AND claim_token=?''',
                (applied_seq, active, previous_destination, rollback_until,
                 applied_seq, _json(state), utc_now(), claimed.project_id,
                 claimed.target, claimed.token))
            if result.rowcount != 1:
                raise ProjectionLeaseLost('projection rebuild finish lost CAS')
            self._db.execute(
                '''DELETE FROM projection_rebuild_snapshots
                   WHERE project_id=? AND target=? AND build_id=?''',
                (claimed.project_id, claimed.target, claimed.build_id))

    def list_incomplete_rebuilds(self, target=None):
        sql = "SELECT project_id,target FROM projection_cursors WHERE status IN ('rebuilding','validating','switching','failed') AND rebuild_state<>'{}'::jsonb"
        params = []
        if target is not None:
            sql += ' AND target=?'
            params.append(target)
        return [(row['project_id'], row['target'])
                for row in self._db.execute(sql, params).fetchall()]

    def clear_previous_destination(self, project_id, target, previous):
        with self.repository._transaction():
            self._db.execute(
                '''UPDATE projection_cursors SET previous_destination=NULL,
                       rollback_until=NULL,rollback_applied_seq=0,updated_at=?
                   WHERE project_id=? AND target=? AND previous_destination=?''',
                (utc_now(), project_id, target, previous))

    def list_expired_rollbacks(self, target=None):
        sql = '''SELECT project_id,target,previous_destination
                 FROM projection_cursors
                 WHERE previous_destination IS NOT NULL
                   AND rollback_until<=clock_timestamp()'''
        params = []
        if target is not None:
            sql += ' AND target=?'
            params.append(target)
        return [(row['project_id'], row['target'], row['previous_destination'])
                for row in self._db.execute(sql, params).fetchall()]


__all__ = [
    'ClaimedBatch', 'ProjectionGap', 'ProjectionLeaseLost',
    'ProjectionRebuildRequired', 'ProjectionStore', 'claim_is_available',
    'claim_matches', 'contiguous_end', 'projection_is_ready', 'rebuild_matches',
    'rebuild_needs_capture',
]
