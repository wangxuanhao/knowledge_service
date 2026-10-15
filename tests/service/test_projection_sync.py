from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from knowledge_service.repository.projection_store import ClaimedBatch
from knowledge_service.services.projection_sync import (
    ProjectionRebuildRequired, ProjectionSync,
)


def batch(*seqs, token='claim-1', destination=None, previous=None,
          rollback_until=None, build_id=None):
    return ClaimedBatch(
        events=tuple({'seq': seq} for seq in seqs), owner='worker', token=token,
        expires_at='2099-01-01T00:00:00Z',
        from_seq=seqs[0] if seqs else 1, to_seq=seqs[-1] if seqs else 0,
        active_destination=destination, previous_destination=previous,
        rollback_until=rollback_until, build_id=build_id)


class FakeStore:
    def __init__(self, batches=(), watermark=0, rebuild=None):
        self.batches = list(batches)
        self.watermark = watermark
        self.rebuild_state = rebuild
        self.acks = []
        self.failures = []
        self.renewals = []
        self.finished = []
        self.guard_active = False
        self.cleared = []
        self.progress = []
        self.expired = []

    def claim_contiguous_batch(self, *args, **kwargs):
        return self.batches.pop(0) if self.batches else None

    def renew_claim(self, claimed, lease_seconds=30):
        self.renewals.append(claimed.token)
        return claimed

    def ack_contiguous(self, claimed, schema_version):
        self.acks.append(claimed.token)
        return claimed.to_seq

    def mark_failed(self, claimed, error, schema_version=None):
        self.failures.append((claimed.token, str(error)))

    def project_watermark(self, project_id):
        return self.watermark

    def begin_rebuild(self, project_id, target, schema_version, owner):
        if self.rebuild_state is not None:
            return self.rebuild_state
        return {
            'claim': batch(build_id='build-1', token='build-token'),
            'snapshot_seq': 3,
            'records': [{'version_id': 'snapshot-v3'}],
            'state': {'build_id': 'build-1', 'phase': 'snapshot'},
        }

    def record_rebuild_progress(self, claimed, schema_version, state):
        claimed.build_id = state['build_id']
        self.progress.append(dict(state))

    def finish_rebuild(self, claimed, schema_version, applied_seq,
                       rollback_until, state, previous_destination):
        assert self.guard_active
        self.finished.append({
            'claim': claimed, 'applied_seq': applied_seq,
            'rollback_until': rollback_until, 'state': state,
            'previous_destination': previous_destination,
        })

    def list_incomplete_rebuilds(self, target=None):
        return [('project', target or 'milvus')]

    def list_expired_rollbacks(self, target=None):
        return list(self.expired)

    def clear_previous_destination(self, project_id, target, previous):
        self.cleared.append(previous)

    @staticmethod
    def rollback_deadline(switched_at):
        return switched_at + timedelta(days=7)

    @contextmanager
    def alias_switch_guard(self, project_id, claimed, schema_version):
        self.guard_active = True
        try:
            yield self.watermark
        finally:
            self.guard_active = False


_DEFAULT_VALIDATION = object()


class FakeAdapter:
    def __init__(self, fail=False, validation=_DEFAULT_VALIDATION):
        self.fail = fail
        self.validation = ({'ok': True}
                           if validation is _DEFAULT_VALIDATION else validation)
        self.applied = []
        self.phases = []
        self.retired = []
        self.reset = []
        self.current = 'old-live'

    def apply(self, project_id, event, destination=None, fence=None):
        if self.fail:
            raise RuntimeError('adapter failed')
        self.applied.append((event['seq'], destination, fence))

    def begin_rebuild(self, project_id, schema_version, state, fence=None):
        self.phases.append('begin')
        return 'build-1'

    def load_snapshot(self, project_id, destination, records, snapshot_seq,
                      fence=None):
        self.phases.append(('snapshot', snapshot_seq, len(records)))

    def reset_rebuild(self, project_id, destination, build_id, fence=None):
        self.reset.append((destination, build_id, fence))

    def validate_rebuild(self, project_id, destination, fence=None):
        self.phases.append('validate')
        return self.validation

    def current_destination(self, project_id):
        return self.current

    def switch_alias(self, project_id, destination, build_id=None, fence=None):
        self.phases.append(('switch', build_id))
        self.current = destination
        return 'old-live'

    def retire(self, project_id, destination):
        self.retired.append(destination)


def test_drain_renews_before_each_apply_and_fences_ack():
    claimed = batch(1, 2, token='lease-a')
    store = FakeStore([claimed])
    adapter = FakeAdapter()
    assert ProjectionSync(store, {'milvus': adapter}, schema_version=2).drain_once(
        'project', 'milvus') == 2
    assert store.renewals == ['lease-a', 'lease-a']
    assert adapter.applied == [(1, None, 'lease-a'), (2, None, 'lease-a')]
    assert store.acks == ['lease-a']


def test_failed_delivery_uses_same_claim_token_and_never_acks():
    store = FakeStore([batch(1, token='lease-a')])
    adapter = FakeAdapter(fail=True)
    ProjectionSync(store, {'neo4j': adapter}, schema_version=2).drain_once(
        'project', 'neo4j')
    assert store.acks == []
    assert store.failures == [('lease-a', 'adapter failed')]


def test_schema_mismatch_surfaces_rebuild_required_without_adapter_calls():
    class MismatchedStore(FakeStore):
        def claim_contiguous_batch(self, *args, **kwargs):
            raise ProjectionRebuildRequired(
                'project', 'milvus', current_schema=1, required_schema=2)

    adapter = FakeAdapter()
    with pytest.raises(ProjectionRebuildRequired) as raised:
        ProjectionSync(
            MismatchedStore(), {'milvus': adapter}, schema_version=2
        ).drain_once('project', 'milvus')

    assert raised.value.requires_rebuild is True
    assert adapter.applied == []
    assert adapter.phases == []
    assert adapter.retired == []


def test_validation_failure_marks_build_failed_and_never_switches():
    store = FakeStore(watermark=3)
    adapter = FakeAdapter(validation={'ok': False, 'reason': 'count mismatch'})
    with pytest.raises(RuntimeError, match='validation failed'):
        ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild(
            'project', 'milvus')
    assert not any(isinstance(phase, tuple) and phase[0] == 'switch'
                   for phase in adapter.phases)
    assert store.failures and store.failures[0][0] == 'build-token'


def test_rebuild_resumes_persisted_destination_without_starting_new_build():
    claimed = batch(build_id='build-1', token='resume-token')
    resumed = {
        'claim': claimed, 'snapshot_seq': 3, 'records': [],
        'state': {'build_id': 'build-1', 'phase': 'catchup',
                  'destination': 'build-1', 'snapshot_seq': 3,
                  'applied_seq': 3, 'expected_versions': {},
                  'expected_projection': {},
                  'expected_digest': hashlib.sha256(b'{}').hexdigest()},
    }
    store = FakeStore([batch(4, token='resume-token', build_id='build-1')],
                      watermark=4, rebuild=resumed)
    adapter = FakeAdapter()
    result = ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild(
        'project', 'milvus')
    assert 'begin' not in adapter.phases
    assert not any(isinstance(phase, tuple) and phase[0] == 'snapshot'
                   for phase in adapter.phases)
    assert result['applied_seq'] == 4


def test_rollback_window_dual_writes_then_retires_previous_projection():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    store = FakeStore([batch(7, token='lease-a', destination='new-live',
                             previous='old-live', rollback_until=future)])
    adapter = FakeAdapter()
    sync = ProjectionSync(store, {'milvus': adapter}, schema_version=2)
    sync.drain_once('project', 'milvus')
    assert [(item[1], item[2]) for item in adapter.applied] == [
        ('new-live', 'lease-a'), ('old-live', 'lease-a')]

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.batches.append(batch(8, token='lease-b', destination='new-live',
                               previous='old-live', rollback_until=past))
    sync.drain_once('project', 'milvus')
    assert adapter.retired == ['old-live']
    assert store.cleared == ['old-live']


def test_resume_pending_enumerates_and_resumes_incomplete_builds():
    resumed = {
        'claim': batch(build_id='build-1', token='resume-token'),
        'snapshot_seq': 0, 'records': [],
        'state': {'build_id': 'build-1', 'phase': 'catchup',
                  'destination': 'build-1', 'snapshot_seq': 0,
                  'applied_seq': 0},
    }
    store = FakeStore(watermark=0, rebuild=resumed)
    adapter = FakeAdapter()
    results = ProjectionSync(
        store, {'milvus': adapter}, schema_version=2).resume_pending('milvus')
    assert results[0]['ready'] is True


@pytest.mark.parametrize('validation', [None, {}, {'ok': False}, {'ok': 1}, 1])
def test_validation_is_fail_closed(validation):
    store = FakeStore(watermark=3)
    adapter = FakeAdapter(validation=validation)
    with pytest.raises(RuntimeError, match='validation failed'):
        ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild(
            'project', 'milvus')
    assert not any(isinstance(phase, tuple) and phase[0] == 'switch'
                   for phase in adapter.phases)


def test_snapshot_loading_resume_resets_and_replays_snapshot():
    claimed = batch(build_id='build-1', token='resume-token')
    resumed = {
        'claim': claimed, 'snapshot_seq': 3,
        'records': [{'version_id': 'snapshot-v3'}],
        'state': {'build_id': 'build-1', 'phase': 'snapshot_loading',
                  'destination': 'build-1', 'snapshot_seq': 3,
                  'applied_seq': 3},
    }
    store = FakeStore(watermark=3, rebuild=resumed)
    adapter = FakeAdapter()
    ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild(
        'project', 'milvus')
    assert adapter.reset == [('build-1', 'build-1', 'resume-token')]
    assert ('snapshot', 3, 1) in adapter.phases
    assert any(state['phase'] == 'snapshot_loading' for state in store.progress)
    assert any(state['phase'] == 'snapshot_loaded' for state in store.progress)


def test_switching_resume_reuses_persisted_previous_and_idempotent_build_id():
    claimed = batch(build_id='build-1', token='resume-token')
    resumed = {
        'claim': claimed, 'snapshot_seq': 3, 'records': [],
        'state': {'build_id': 'build-1', 'phase': 'switching',
                  'destination': 'build-1', 'previous_destination': 'stable-old',
                  'snapshot_seq': 3, 'applied_seq': 3},
    }
    store = FakeStore(watermark=3, rebuild=resumed)
    adapter = FakeAdapter()
    adapter.current = 'build-1'  # alias already switched before the crash
    result = ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild(
        'project', 'milvus')
    assert ('switch', 'build-1') in adapter.phases
    assert store.finished[0]['previous_destination'] == 'stable-old'
    assert result['previous_destination'] == 'stable-old'


def test_maintenance_retires_expired_previous_for_idle_projects():
    store = FakeStore()
    store.expired = [('project', 'milvus', 'old-live')]
    adapter = FakeAdapter()
    count = ProjectionSync(
        store, {'milvus': adapter}, schema_version=2).maintain_rollbacks('milvus')
    assert count == 1
    assert adapter.retired == ['old-live']
    assert store.cleared == ['old-live']


def test_begin_rebuild_exception_is_handled_without_unbound_claim_error():
    class FailingStore(FakeStore):
        def begin_rebuild(self, *args, **kwargs):
            raise RuntimeError('capture failed')

    with pytest.raises(RuntimeError, match='capture failed'):
        ProjectionSync(
            FailingStore(), {'milvus': FakeAdapter()}, schema_version=2
        ).rebuild('project', 'milvus')
