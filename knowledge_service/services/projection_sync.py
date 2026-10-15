"""Fenced projection delivery and recoverable rebuild orchestration.

Adapters receive the opaque claim token as ``fence`` on every external write.
Task 4/6 adapters must persist the highest sequence/fence and reject late writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
import hashlib
import json
from uuid import uuid4

from ..core.time import normalize_time
from ..domain.applicability import normalize_applicability, scope_identity
from ..repository.projection_store import (
    ProjectionLeaseLost, ProjectionRebuildRequired,
)


def _validation_ok(result):
    return result is True or (isinstance(result, dict)
                              and result.get('ok') is True)


@dataclass(frozen=True)
class DrainResult:
    status: str
    count: int = 0
    active_lease: bool = False

    def __int__(self):
        return self.count

    def __eq__(self, other):
        if isinstance(other, int):
            return self.count == other
        return super().__eq__(other)


class ProjectionSync:
    def __init__(self, store, adapters, *, schema_version):
        self.store = store
        self.adapters = dict(adapters)
        self.schema_version = schema_version
        self.owner = str(uuid4())

    def _adapter(self, target):
        try:
            return self.adapters[target]
        except KeyError:
            raise ValueError(f'projection target is not configured: {target}') from None

    @staticmethod
    def _rollback_active(claimed):
        return bool(claimed.previous_destination and claimed.rollback_until
                    and normalize_time(claimed.rollback_until, allow_none=False)
                    > normalize_time(datetime.now(timezone.utc).isoformat(),
                                     allow_none=False))

    @staticmethod
    def _expected_from_records(records):
        return {
            str(row['id']): str(row['version_id']) for row in records
            if row.get('kind') in {'entity', 'relation', 'chunk'}
            and not (row.get('metadata') or {}).get('_deleted')}

    @staticmethod
    def _projection_value(row):
        applicability = normalize_applicability(
            (row.get('metadata') or {}).get('applicability'))
        def instant(value):
            normalized = normalize_time(value)
            if normalized is None:
                return [False, 0]
            return [True, int(datetime.fromisoformat(
                normalized.replace('Z', '+00:00')).timestamp() * 1000)]
        return {
            'version_id': str(row['version_id']),
            'platform': applicability['platforms'],
            'business': applicability['businesses'],
            'audience': applicability['audiences'],
            'scope_identity': scope_identity(
                (row.get('metadata') or {}).get('applicability')),
            'valid_from': instant(row.get('valid_from')),
            'valid_until': instant(row.get('valid_until')),
        }

    @classmethod
    def _projection_from_records(cls, records):
        return {str(row['id']): cls._projection_value(row) for row in records
                if row.get('kind') in {'entity', 'relation', 'chunk'}
                and not (row.get('metadata') or {}).get('_deleted')}

    @staticmethod
    def _advance_expected(expected, events):
        for event in events:
            payload = event.get('payload') or {}
            record_id = payload.get('id') or payload.get('record_id')
            if not record_id:
                continue
            if event.get('operation') == 'tombstone':
                expected.pop(str(record_id), None)
            elif (payload.get('kind') in {'entity', 'relation', 'chunk'}
                  and not (payload.get('metadata') or {}).get('_deleted')):
                expected[str(record_id)] = str(
                    payload.get('version_id') or event.get('version_id'))

    @classmethod
    def _advance_projection(cls, expected, events):
        for event in events:
            payload = event.get('payload') or {}
            record_id = payload.get('id') or payload.get('record_id')
            if not record_id:
                continue
            if event.get('operation') == 'tombstone':
                expected.pop(str(record_id), None)
            elif (payload.get('kind') in {'entity', 'relation', 'chunk'}
                  and not (payload.get('metadata') or {}).get('_deleted')):
                expected[str(record_id)] = cls._projection_value(payload)

    @staticmethod
    def _expected_digest(expected):
        return hashlib.sha256(
            json.dumps(expected, sort_keys=True, ensure_ascii=False,
                       separators=(',', ':')).encode()).hexdigest()

    def _apply_claimed(self, adapter, project_id, claimed, destination=None):
        active = destination if destination is not None else claimed.active_destination
        rollback = self._rollback_active(claimed)
        destinations = [active]
        if rollback:
            destinations.append(claimed.previous_destination)
        elif claimed.previous_destination:
            adapter.retire(project_id, claimed.previous_destination)
            self.store.clear_previous_destination(
                project_id, claimed.target, claimed.previous_destination)
            claimed.previous_destination = None
        for event in claimed.events:
            for selected in destinations:
                self.store.renew_claim(claimed)
                applied = adapter.apply(
                    project_id, event, destination=selected,
                    fence=claimed.token)
                if applied is False:
                    raise ProjectionLeaseLost(
                        'projection fence was lost during external write')

    def drain_once(self, project_id, target, *, destination=None, after_seq=None,
                   limit=100):
        adapter = self._adapter(target)
        claimed = self.store.claim_contiguous_batch(
            project_id, target, self.schema_version, limit=limit,
            after_seq=after_seq, owner=self.owner)
        if claimed is None:
            state = (self.store.delivery_state(project_id, target)
                     if hasattr(self.store, 'delivery_state') else 'empty')
            return DrainResult(state, 0, state == 'leased')
        try:
            self._apply_claimed(adapter, project_id, claimed, destination)
            self.store.ack_contiguous(claimed, self.schema_version)
            return DrainResult('applied', len(claimed.events), False)
        except Exception as exc:
            try:
                self.store.mark_failed(claimed, exc, self.schema_version)
            except ProjectionLeaseLost:
                pass
            if getattr(exc, 'requires_rebuild', False):
                raise
            return DrainResult('empty', 0, False)

    def _fail_rebuild(self, claimed, exc):
        try:
            self.store.mark_failed(claimed, exc, self.schema_version)
        except ProjectionLeaseLost:
            pass

    def rebuild(self, project_id, target):
        adapter = self._adapter(target)
        claimed = None
        try:
            rebuild = self.store.begin_rebuild(
                project_id, target, self.schema_version, self.owner)
            claimed = rebuild['claim']
            state = dict(rebuild['state'])
            destination = state.get('destination')
            if destination is None:
                self.store.renew_claim(claimed)
                destination = adapter.begin_rebuild(
                    project_id, self.schema_version, state, fence=claimed.token)
                state.update(destination=destination, phase='snapshot_loading')
                self.store.record_rebuild_progress(
                    claimed, self.schema_version, state)
            phase = state.get('phase')
            if (phase in {'snapshot_loaded', 'catchup', 'validating'}
                    and ('expected_versions' not in state
                         or 'expected_projection' not in state)):
                # Pre-receipt/incomplete builds cannot be safely validated.
                # Reload the captured snapshot and replay its outbox tail.
                phase = state['phase'] = 'snapshot_loading'
                state['applied_seq'] = rebuild['snapshot_seq']
                self.store.record_rebuild_progress(
                    claimed, self.schema_version, state)
            if phase in {'snapshot', 'snapshot_loading', 'capturing'}:
                if phase == 'snapshot_loading':
                    self.store.renew_claim(claimed)
                    adapter.reset_rebuild(
                        project_id, destination, state['build_id'],
                        fence=claimed.token)
                state['phase'] = 'snapshot_loading'
                self.store.record_rebuild_progress(
                    claimed, self.schema_version, state)
                self.store.renew_claim(claimed)
                adapter.load_snapshot(
                    project_id, destination, rebuild['records'],
                    rebuild['snapshot_seq'], fence=claimed.token)
                expected = self._expected_from_records(rebuild['records'])
                projection = self._projection_from_records(rebuild['records'])
                state.update(phase='snapshot_loaded',
                             applied_seq=rebuild['snapshot_seq'],
                             expected_versions=expected,
                             expected_projection=projection,
                             expected_digest=self._expected_digest(projection))
                self.store.record_rebuild_progress(
                    claimed, self.schema_version, state)
                state['phase'] = 'catchup'
                self.store.record_rebuild_progress(
                    claimed, self.schema_version, state)
            applied = int(state.get('applied_seq', rebuild['snapshot_seq']))

            while applied < self.store.project_watermark(project_id):
                next_claim = self.store.claim_contiguous_batch(
                    project_id, target, self.schema_version, after_seq=applied,
                    owner=self.owner, build=claimed)
                if next_claim is None:
                    raise RuntimeError('projection rebuild cannot reach watermark')
                claimed = next_claim
                self._apply_claimed(adapter, project_id, claimed, destination)
                expected = dict(state.get('expected_versions') or {})
                self._advance_expected(expected, claimed.events)
                projection = dict(state.get('expected_projection') or {})
                self._advance_projection(projection, claimed.events)
                applied = self.store.ack_contiguous(
                    claimed, self.schema_version)
                state.update(phase='catchup', applied_seq=applied,
                             expected_versions=expected,
                             expected_projection=projection,
                             expected_digest=self._expected_digest(projection))
                self.store.record_rebuild_progress(
                    claimed, self.schema_version, state)

            if state.get('phase') != 'switching':
                state['phase'] = 'validating'
                self.store.record_rebuild_progress(
                    claimed, self.schema_version, state)
                self.store.renew_claim(claimed)
                try:
                    validation = adapter.validate_rebuild(
                        project_id, destination, fence=claimed.token,
                        state=state)
                except TypeError as exc:
                    if 'unexpected keyword argument' not in str(exc):
                        raise
                    validation = adapter.validate_rebuild(
                        project_id, destination, fence=claimed.token)
                state['validation'] = validation
                if not _validation_ok(validation):
                    raise RuntimeError('projection rebuild validation failed')
                self.store.record_rebuild_progress(
                    claimed, self.schema_version, state)

            with self.store.alias_switch_guard(
                    project_id, claimed, self.schema_version) as locked_watermark:
                while applied < locked_watermark:
                    next_claim = self.store.claim_contiguous_batch(
                        project_id, target, self.schema_version,
                        after_seq=applied, owner=self.owner, build=claimed)
                    if next_claim is None:
                        raise RuntimeError('projection changed before alias switch')
                    claimed = next_claim
                    self._apply_claimed(adapter, project_id, claimed, destination)
                    expected = dict(state.get('expected_versions') or {})
                    self._advance_expected(expected, claimed.events)
                    projection = dict(state.get('expected_projection') or {})
                    self._advance_projection(projection, claimed.events)
                    applied = self.store.ack_contiguous(
                        claimed, self.schema_version)
                    state.update(expected_versions=expected,
                                 expected_projection=projection,
                                 expected_digest=self._expected_digest(projection))
                self.store.renew_claim(claimed)
                try:
                    locked_validation = adapter.validate_rebuild(
                        project_id, destination, fence=claimed.token,
                        state=state)
                except TypeError as exc:
                    if 'unexpected keyword argument' not in str(exc):
                        raise
                    locked_validation = adapter.validate_rebuild(
                        project_id, destination, fence=claimed.token)
                if not _validation_ok(locked_validation):
                    raise RuntimeError(
                        'projection rebuild locked validation failed')
                state['validation'] = locked_validation
                if 'previous_destination' in state:
                    previous = state['previous_destination']
                else:
                    self.store.renew_claim(claimed)
                    previous = adapter.current_destination(project_id)
                    state.update(phase='switching', applied_seq=applied,
                                 previous_destination=previous)
                    self.store.record_rebuild_progress(
                        claimed, self.schema_version, state)
                self.store.renew_claim(claimed)
                adapter.switch_alias(
                    project_id, destination, build_id=state['build_id'],
                    fence=claimed.token)
                rollback_until = self.store.rollback_deadline(
                    datetime.now(timezone.utc))
                state['phase'] = 'ready'
                self.store.finish_rebuild(
                    claimed, self.schema_version, applied, rollback_until,
                    state, previous)
            return {'ready': True, 'applied_seq': applied,
                    'rollback_until': rollback_until,
                    'destination': destination,
                    'previous_destination': previous}
        except Exception as exc:
            if claimed is not None:
                self._fail_rebuild(claimed, exc)
            raise

    def resume_pending(self, target=None):
        results = []
        for project_id, selected_target in self.store.list_incomplete_rebuilds(target):
            results.append(self.rebuild(project_id, selected_target))
        return results

    def maintain_rollbacks(self, target=None):
        retired = 0
        for project_id, selected_target, previous in self.store.list_expired_rollbacks(
                target):
            adapter = self._adapter(selected_target)
            adapter.retire(project_id, previous)
            self.store.clear_previous_destination(
                project_id, selected_target, previous)
            retired += 1
        return retired


__all__ = ['DrainResult', 'ProjectionRebuildRequired', 'ProjectionSync']
