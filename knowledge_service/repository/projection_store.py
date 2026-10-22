"""Durable ordering, fenced delivery, rebuild recovery, and readiness."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from itertools import islice
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
    index_version: int | None = None
    catalog: dict | None = None

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


def milvus_delivery_state(cursor, schema_version=None, now=None):
    """Only an explicitly activated, healthy generation accepts incremental work."""
    if cursor is None:
        return 'not_built'
    if cursor.get('status') in {'failed', 'requires_rebuild'}:
        return 'requires_rebuild'
    if (schema_version is not None
            and int(cursor.get('schema_version') or 0) != int(schema_version)):
        return 'requires_rebuild'
    now = now or utc_now()
    leased = not claim_is_available(
        cursor.get('claim_token'), cursor.get('claim_until'), now)
    rebuild = cursor.get('rebuild_state') or {}
    if (rebuild.get('catalog') or {}).get('fields'):
        return 'requires_rebuild'
    if (cursor.get('status') in {'rebuilding', 'validating', 'switching'}
            or (rebuild.get('build_id') and rebuild.get('phase') != 'ready')):
        return 'building' if leased else 'requires_rebuild'
    if not cursor.get('active_destination') or not rebuild.get('index_version'):
        return 'not_built'
    if leased:
        return 'leased'
    if (cursor.get('claim_token')
            and cursor.get('claimed_from_seq') is not None
            and cursor.get('claimed_to_seq') is not None
            and int(cursor['claimed_from_seq']) <= int(cursor['claimed_to_seq'])):
        # The previous RPC may still complete. An ordinary claim must never
        # adopt this generation or turn a new token into a new generation.
        return 'requires_rebuild'
    if cursor.get('status') not in {'ready', 'pending'}:
        return 'requires_rebuild'
    return 'available'


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
    # The shared row factory hides legacy internal ordering columns named seq.
    # Outbox sequence numbers are part of the internal delivery contract.
    if 'outbox_seq' in result:
        result['seq'] = result.pop('outbox_seq')
    for name in ('payload', 'rebuild_state'):
        if isinstance(result.get(name), str):
            result[name] = json.loads(result[name])
    return result


class ProjectionStore:
    def __init__(self, repository):
        self.repository = repository
        self._db = repository._db

    @property
    def receipts(self):
        from .projection_receipts import ProjectionReceipts
        return ProjectionReceipts(self)

    @property
    def generation_cleanups(self):
        from .generation_cleanup import GenerationCleanupStore
        return GenerationCleanupStore(self)

    def authority_bundle(self, project_id, record_id):
        """Current target, incident facts and endpoints for projection repair."""
        with self.repository._lock:
            rows = self._db.execute(
                '''WITH current_rows AS (
                     SELECT * FROM record_versions
                     WHERE project_id=? AND superseded_at IS NULL
                   ), incident AS (
                     SELECT payload::jsonb AS doc FROM current_rows
                     WHERE (payload::jsonb->>'kind'='relation' AND
                            (payload::jsonb->>'id'=? OR
                             payload::jsonb->>'subject_id'=? OR
                             payload::jsonb->>'object_id'=?))
                        OR (payload::jsonb->>'kind'='attribute' AND
                            (payload::jsonb->>'id'=? OR
                             payload::jsonb->>'subject_id'=?))
                   ), wanted AS (
                     SELECT ?::text AS id
                     UNION SELECT doc->>'id' FROM incident
                     UNION SELECT doc->>'subject_id' FROM incident
                     UNION SELECT doc->>'object_id' FROM incident
                   )
                   SELECT current_rows.* FROM current_rows
                   WHERE id IN (SELECT id FROM wanted WHERE id IS NOT NULL)
                   ORDER BY id''',
                (project_id, record_id, record_id, record_id, record_id,
                 record_id, record_id)).fetchall()
        return [self.repository._record(row) for row in rows]

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
        payload = {key: value for key, value in payload.items() if key != 'embedding'}
        envelope = self._milvus_envelope(project_id, operation, version_id, payload)
        if envelope is not None:
            payload['_milvus_projection'] = envelope
        self._db.execute(
            '''INSERT INTO projection_outbox
               (project_id,seq,operation,version_id,payload,created_at)
               VALUES (?,?,?,?,?,?)''',
            (project_id, seq, operation, version_id, _json(payload), utc_now()))
        return seq

    def _milvus_envelope(self, project_id, operation, version_id, payload):
        from ..domain.search_filter_catalog import FilterCatalog
        from ..domain.search_projection import build_projection
        from .projection_inputs import ProjectionInputs
        cursor = self.cursor(project_id, 'milvus')
        if cursor is None or cursor.get('status') in {'failed', 'requires_rebuild'}:
            return None
        state = cursor.get('rebuild_state') or {}
        if not state.get('index_version') or not state.get('catalog'):
            return None
        if state['catalog'].get('fields'):
            return None
        if cursor.get('status') not in {'ready', 'pending', 'applying', 'rebuilding', 'validating', 'switching'}:
            return None
        envelope = {'index_version': state['index_version'],
                    'catalog_fingerprint': FilterCatalog.from_dict(state['catalog']).fingerprint,
                    'embedding_model': state['embedding_model']}
        if operation == 'tombstone':
            return {**envelope, 'record_id': payload['record_id']}
        try:
            catalog = FilterCatalog.from_dict(state['catalog'])
            inputs = ProjectionInputs(self.repository).collect(project_id, version_ids=[version_id])
            if not inputs:
                return {**envelope, 'row': None}
            row = build_projection(inputs[0], catalog, index_version=state['index_version'])
            return {**envelope, 'row': row}
        except ValueError as exc:
            self._db.execute("""UPDATE projection_cursors SET status='requires_rebuild',
                last_error=?,updated_at=? WHERE project_id=? AND target='milvus'""",
                (str(exc), utc_now(), project_id))
            return None

    def enqueue_record_change(self, project_id, old_record, new_record):
        sequences = []
        if old_record is not None:
            sequences.append(self.enqueue(
                project_id, 'tombstone', old_record['version_id'], {
                    'record_id': old_record['id'],
                    'version_id': old_record['version_id']}))
        if not any((new_record.get('metadata') or {}).get(key) for key in ('_deleted', '_audit')):
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
            if target != 'milvus' or build is not None:
                self._ensure_cursor(project_id, target, schema_version)
            row = self._db.execute(
                '''SELECT * FROM projection_cursors
                   WHERE project_id=? AND target=? FOR UPDATE''',
                (project_id, target)).fetchone()
            cursor = None if row is None else _decode_row(row)
            now = utc_now()
            if build is None:
                if target == 'milvus':
                    state = self._milvus_delivery_state_locked(
                        project_id, target, cursor, schema_version, now)
                    if state != 'available':
                        return None
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
                '''SELECT project_id,seq AS outbox_seq,operation,version_id,payload,created_at
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
                cursor.get('rollback_until'),
                (cursor.get('rebuild_state') or {}).get('index_version'),
                (cursor.get('rebuild_state') or {}).get('catalog'))

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
        if claimed.target == 'milvus' and cursor.get('status') == 'requires_rebuild':
            raise ProjectionLeaseLost('project index was invalidated while delivery was in flight')
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
                '''SELECT seq AS outbox_seq FROM projection_outbox
                   WHERE project_id=? AND seq BETWEEN ? AND ? ORDER BY seq''',
                (claimed.project_id, claimed.from_seq, claimed.to_seq)).fetchall()
            actual = [int(row['outbox_seq']) for row in rows]
            expected = [int(event['seq']) for event in claimed.events]
            if actual != expected:
                raise ProjectionGap('claimed events no longer match authoritative outbox')
            if claimed.target == 'milvus':
                state = dict(cursor.get('rebuild_state') or {})
                self.receipts.apply_events(claimed, claimed.events, cursor=cursor)
                state.pop('expected_projection', None)
                state.update(applied_seq=claimed.to_seq)
                self._db.execute('UPDATE projection_cursors SET rebuild_state=? WHERE project_id=? AND target=? AND claim_token=?',
                                 (_json(state), claimed.project_id, claimed.target, claimed.token))
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
            if claimed.target == 'milvus' and claimed.build_id and getattr(error, 'requires_rebuild', False):
                state = dict(cursor.get('rebuild_state') or {})
                state['requires_new_snapshot'] = True
                self._db.execute('UPDATE projection_cursors SET rebuild_state=? WHERE project_id=? AND target=?', (_json(state), claimed.project_id, claimed.target))
            if claimed.target == 'milvus' and (isinstance(error, ProjectionLeaseLost) or getattr(error, 'uncertain_write', False)):
                state = dict(cursor.get('rebuild_state') or {})
                state['uncertain_write'] = True
                self._db.execute('UPDATE projection_cursors SET rebuild_state=? WHERE project_id=? AND target=?', (_json(state), claimed.project_id, claimed.target))
            status = ('requires_rebuild'
                      if claimed.target == 'milvus' and not claimed.build_id
                      else 'failed')
            result = self._db.execute(
                '''UPDATE projection_cursors SET status=?,last_error=?,
                       claim_owner=NULL,claim_token=NULL,claim_until=NULL,
                       claimed_from_seq=NULL,claimed_to_seq=NULL,updated_at=?
                   WHERE project_id=? AND target=? AND claim_owner=? AND claim_token=?''',
                (status, str(error), utc_now(), claimed.project_id, claimed.target,
                 claimed.owner, claimed.token))
            if result.rowcount != 1:
                raise ProjectionLeaseLost('projection failure lost CAS')

    def projection_ready(self, project_id, target, schema_version):
        cursor = self.cursor(project_id, target)
        if (target == 'milvus'
                and milvus_delivery_state(cursor, schema_version)
                not in {'available', 'leased'}):
            return False
        return projection_is_ready(self.project_watermark(project_id),
                                   cursor, schema_version)

    def _milvus_delivery_state_locked(self, project_id, target, cursor,
                                      schema_version, now):
        state = milvus_delivery_state(cursor, schema_version, now)
        if state == 'requires_rebuild' and cursor.get('status') != state:
            if cursor.get('claim_token'):
                frozen = dict(cursor.get('rebuild_state') or {})
                frozen['uncertain_write'] = True
                self._db.execute('UPDATE projection_cursors SET rebuild_state=? WHERE project_id=? AND target=?',
                                 (_json(frozen), project_id, target))
            self._db.execute(
                '''UPDATE projection_cursors SET status='requires_rebuild',last_error=?,
                       claim_owner=NULL,claim_token=NULL,claim_until=NULL,
                       claimed_from_seq=NULL,claimed_to_seq=NULL,updated_at=?
                   WHERE project_id=? AND target=?''',
                ('Milvus generation is incomplete, incompatible, or has an unconfirmed write; '
                 'rebuild this project explicitly', now, project_id, target))
        return state

    def delivery_state(self, project_id, target, schema_version=None):
        cursor = self.cursor(project_id, target)
        if target == 'milvus':
            state = milvus_delivery_state(cursor, schema_version)
            return 'empty' if state == 'available' else state
        if cursor is None:
            return 'empty'
        if not claim_is_available(
                cursor.get('claim_token'), cursor.get('claim_until'), utc_now()):
            return 'leased'
        return 'empty'

    def search_index(self, project_id, schema_version):
        from ..domain.search_filter_catalog import FilterCatalog
        with self.repository.independent_read_view() as reader:
            store = reader.projections
            cursor = store.cursor(project_id, 'milvus')
            state = milvus_delivery_state(cursor, schema_version)
            if state != 'available':
                error = RuntimeError('项目向量索引不可用: ' + state + '；请在项目页面向量化或等待同步')
                error.state = state
                raise error
            watermark = store.project_watermark(project_id)
            if not projection_is_ready(watermark, cursor, schema_version):
                error = RuntimeError('项目向量索引正在追平，请稍后重试')
                error.state = 'catching_up'
                raise error
            frozen = cursor.get('rebuild_state') or {}
            catalog = FilterCatalog.from_dict(frozen['catalog'])
            return {'index_version': int(frozen['index_version']), 'catalog': catalog.to_dict(),
                    'embedding_model': frozen['embedding_model'], 'dim': int(frozen['dim']),
                    'watermark': watermark}

    def search_index_is_current(self, project_id, index_version, schema_version, watermark):
        try:
            selected = self.search_index(project_id, schema_version)
        except RuntimeError:
            return False
        return selected['index_version'] == index_version and selected['watermark'] == watermark

    def claim_maintenance(self, project_id, target, schema_version, owner,
                          lease_seconds=60):
        token, expires, now = str(uuid4()), self._expiry(lease_seconds), utc_now()
        with self.repository._transaction():
            if target != 'milvus':
                self._ensure_cursor(project_id, target, schema_version)
            row = self._db.execute(
                '''SELECT * FROM projection_cursors
                   WHERE project_id=? AND target=? FOR UPDATE''',
                (project_id, target)).fetchone()
            cursor = None if row is None else _decode_row(row)
            if target == 'milvus':
                state = self._milvus_delivery_state_locked(
                    project_id, target, cursor, schema_version, now)
                if (state != 'available'
                        or not projection_is_ready(
                            self.project_watermark(project_id), cursor, schema_version)):
                    return None
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

    def begin_rebuild(self, project_id, target, schema_version, owner, *, lease_guard,
                      embedding_model=None, dim=None):
        token = str(uuid4())
        with self.repository._transaction():
            self._db.execute('INSERT INTO project_projection_sequences(project_id,projection_seq) VALUES (?,0) ON CONFLICT(project_id) DO NOTHING', (project_id,))
            self._ensure_cursor(project_id, target, schema_version)
            existing = _decode_row(self._db.execute(
                '''SELECT * FROM projection_cursors
                   WHERE project_id=? AND target=? FOR UPDATE''',
                (project_id, target)).fetchone())
            # Lock waits must not consume the lease we are about to issue.
            now, expires = utc_now(), self._expiry(30)
            state = existing.get('rebuild_state') or {}
            unfinished = state.get('build_id') and state.get('phase') != 'ready'
            if target == 'milvus' and ((state.get('catalog') or {}).get('fields') or
                    state.get('uncertain_write') or state.get('requires_new_snapshot') or
                    (embedding_model is not None and state.get('embedding_model') != embedding_model) or
                    (dim is not None and state.get('dim') != dim) or
                    (existing.get('claim_token') and claim_is_available(existing.get('claim_token'), existing.get('claim_until'), now))):
                unfinished = False
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
                previous_state = state
                state = {'build_id': build_id, 'phase': 'capturing'}
                if target == 'milvus':
                    version = int(previous_state.get('index_version') or 0) + 1
                    for obsolete in {previous_state.get('index_version'), previous_state.get('previous_index_version')}:
                        if obsolete and obsolete < version:
                            self.generation_cleanups.enqueue(project_id, obsolete)
                    state.update(index_version=version, previous_index_version=previous_state.get('index_version'))
                    if embedding_model is not None and dim is not None:
                        state.update(embedding_model=embedding_model, dim=dim)
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
            (existing or {}).get('rollback_until'), state.get('index_version'), state.get('catalog'))
        needs_capture = not unfinished or rebuild_needs_capture(state)
        try:
            with lease_guard(claimed) as lease_is_current:
                # Hydration may decode hundreds of thousands of versions. Its
                # private, consistent snapshot leaves the writer free to renew
                # this claim and to serve other projects while we read.
                with self.repository.independent_read_view() as reader:
                    if needs_capture:
                        snapshot_seq = reader.projections.project_watermark(project_id)
                        if target == 'milvus':
                            from .projection_inputs import ProjectionInputs
                            records = (value for batch in ProjectionInputs(reader).batches(project_id) for value in batch)
                        else:
                            records = reader.current_records(project_id)
                        self._persist_rebuild_snapshot(
                            claimed, state, records, snapshot_seq, lease_is_current)
                    else:
                        snapshot_seq = int(state['snapshot_seq'])
                        if target != 'milvus':
                            ids = [row['version_id'] for row in reader._db.execute(
                                '''SELECT version_id FROM projection_rebuild_snapshots
                                   WHERE project_id=? AND target=? AND build_id=?
                                   ORDER BY ordinal''',
                                (project_id, target, build_id)).fetchall()]
                            records = reader.records_by_version_ids(project_id, ids)
                lease_is_current()
                if target == 'milvus':
                    from .projection_snapshot import CapturedProjectionInputs
                    records = CapturedProjectionInputs(self.repository, claimed)
        except Exception as exc:
            try:
                self.mark_failed(claimed, exc, schema_version)
            except ProjectionLeaseLost:
                pass
            raise
        return {'claim': claimed, 'snapshot_seq': snapshot_seq,
                'records': records, 'state': state}

    def _persist_rebuild_snapshot(self, claimed, state, records, snapshot_seq,
                                  lease_is_current):
        project_id, target, build_id = claimed.project_id, claimed.target, claimed.build_id
        with self.repository._transaction():
            self._locked_claim(claimed)
            self._db.execute(
                '''DELETE FROM projection_rebuild_snapshots
                   WHERE project_id=? AND target=? AND build_id=?''',
                (project_id, target, build_id))
            lease_is_current()
        # A partial receipt remains in phase capturing and is discarded on
        # resume. Publish snapshot_seq only after every ordinal is durable.
        iterator, offset = iter(records), 0
        while True:
            page = list(islice(iterator, 500))
            if not page:
                break
            lease_is_current()
            with self.repository._transaction():
                self._locked_claim(claimed)
                self._db.executemany(
                    '''INSERT INTO projection_rebuild_snapshots
                       (project_id,target,build_id,ordinal,version_id)
                       VALUES (?,?,?,?,?)''',
                    [(project_id, target, build_id, index, (row.record if hasattr(row, 'record') else row)['version_id'])
                     for index, row in enumerate(page, offset)])
                lease_is_current()
            offset += len(page)
        with self.repository._transaction():
            self._locked_claim(claimed)
            state.update(phase='snapshot', snapshot_seq=snapshot_seq,
                         applied_seq=snapshot_seq)
            result = self._db.execute(
                '''UPDATE projection_cursors SET applied_seq=?,rebuild_state=?,
                       claimed_from_seq=?,claimed_to_seq=?,updated_at=?
                   WHERE project_id=? AND target=? AND claim_token=?''',
                (snapshot_seq, _json(state), snapshot_seq + 1, snapshot_seq,
                 utc_now(), project_id, target, claimed.token))
            if result.rowcount != 1:
                raise ProjectionLeaseLost('projection rebuild snapshot lost CAS')
            claimed.from_seq, claimed.to_seq = snapshot_seq + 1, snapshot_seq
            lease_is_current()

    def discard_preflight_capture(self, claimed, schema_version):
        """A rejected snapshot wrote no vectors; allow corrected PG data retry."""
        with self.repository._transaction():
            cursor = self._locked_claim(claimed)
            if not rebuild_matches(cursor, claimed, schema_version, utc_now()):
                raise ProjectionLeaseLost('project preflight capture lost CAS')
            previous = cursor.get('rebuild_state') or {}
            state = {key: previous[key] for key in
                     ('build_id', 'index_version', 'previous_index_version', 'embedding_model', 'dim') if key in previous}
            state['phase'] = 'capturing'
            self._db.execute('UPDATE projection_cursors SET rebuild_state=? WHERE project_id=? AND target=? AND claim_token=?',
                             (_json(state), claimed.project_id, claimed.target, claimed.token))

    def freeze_milvus_event(self, claimed, event, envelope):
        with self.repository._transaction():
            self._locked_claim(claimed)
            self._db.execute("""UPDATE projection_outbox SET payload=
                (payload::jsonb || jsonb_build_object('_milvus_projection', ?::jsonb))
                WHERE project_id=? AND seq=?""",
                (_json(envelope), claimed.project_id, event['seq']))
            event['payload'] = {**event['payload'], '_milvus_projection': envelope}

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

    @contextmanager
    def activation_guard(self, project_id, claimed, schema_version):
        with self.repository._transaction():
            row = self._db.execute('SELECT projection_seq FROM project_projection_sequences WHERE project_id=? FOR UPDATE', (project_id,)).fetchone()
            cursor = self._locked_claim(claimed)
            if not rebuild_matches(cursor, claimed, schema_version, utc_now()):
                raise ProjectionLeaseLost('project index activation lease was lost')
            yield int(row['projection_seq']) if row is not None else 0

    def activate_index(self, claimed, schema_version, applied_seq, state):
        cursor = self._locked_claim(claimed)
        if not rebuild_matches(cursor, claimed, schema_version, utc_now()):
            raise ProjectionLeaseLost('project index activation CAS failed')
        if int(applied_seq) != self.project_watermark(claimed.project_id):
            raise ProjectionGap('project index activation is stale')
        state = {**state, 'phase': 'ready'}
        previous = state.get('previous_index_version')
        if previous and previous < state['index_version']:
            self.generation_cleanups.enqueue(claimed.project_id, previous)
        self._db.execute("""UPDATE projection_cursors SET status='ready',applied_seq=?,
            active_destination='knowledge_vectors',previous_destination=NULL,rollback_until=NULL,
            rollback_applied_seq=0,rebuild_state=?,last_error=NULL,
            claim_owner=NULL,claim_token=NULL,claim_until=NULL,
            claimed_from_seq=NULL,claimed_to_seq=NULL,updated_at=?
            WHERE project_id=? AND target='milvus' AND claim_token=?""",
            (applied_seq, _json(state), utc_now(), claimed.project_id, claimed.token))
        self._db.execute('DELETE FROM projection_rebuild_snapshots WHERE project_id=? AND target=? AND build_id=?',
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
