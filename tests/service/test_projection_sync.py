from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import (
    ProjectionInput, build_projection, projection_fingerprint,
)
from knowledge_service.repository.projection_receipts import ReceiptManifest
from knowledge_service.repository.projection_store import ClaimedBatch, ProjectionLeaseLost
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import (
    ProjectionRebuildRequired, ProjectionSync,
)
from knowledge_service.services.projection_runtime import ProjectionWorker


def snapshot_record(record_id='snapshot', version='snapshot-v3', project_id='project'):
    return ProjectionInput({'project_id': project_id, 'id': record_id,
                            'version_id': version, 'kind': 'chunk',
                            'text': version, 'metadata': {}})


def milvus_event(seq, *, index_version=1):
    catalog = default_catalog()
    row = build_projection(snapshot_record(f'r-{seq}', f'v-{seq}'), catalog,
                           index_version=index_version)
    return {'seq': seq, 'operation': 'upsert', 'version_id': row['version_id'],
            'payload': {'_milvus_projection': {
                'index_version': index_version, 'embedding_model': 'test',
                'catalog_fingerprint': catalog.fingerprint, 'row': row}}}


def batch(*seqs, token='claim-1', destination=None, previous=None,
          rollback_until=None, build_id=None, index_version=1):
    return ClaimedBatch(
        events=tuple(milvus_event(seq, index_version=index_version) for seq in seqs),
        owner='worker', token=token, expires_at='2099-01-01T00:00:00Z',
        from_seq=seqs[0] if seqs else 1, to_seq=seqs[-1] if seqs else 0,
        project_id='project', target='milvus', schema_version=2,
        active_destination=destination, previous_destination=previous,
        rollback_until=rollback_until, build_id=build_id,
        index_version=index_version, catalog=default_catalog().to_dict())


class FakeReceipts:
    """Generation-scoped PG receipt contract, including owned page writes."""

    def __init__(self, store):
        self.store, self.rows = store, {}
        self.vector_rows = {}
        self.resets, self.pages_written = [], []

    def _scope(self, claimed):
        self.store.assert_current(claimed)
        state = self.store.cursor(claimed.project_id, claimed.target)['rebuild_state']
        if (claimed.target != 'milvus' or claimed.index_version != state['index_version']
                or claimed.schema_version != self.store.schema_version):
            raise ProjectionLeaseLost('receipt generation or schema changed')
        assert not self.store.guard_active
        return claimed.project_id, claimed.index_version

    def seed(self, project_id, index_version, rows):
        for row in rows:
            assert (row['project_id'], row['index_version']) == (project_id, index_version)
            self.rows[project_id, index_version, row['record_id']] = {
                'version_id': row['version_id'], 'fingerprint': projection_fingerprint(row)}
            self.vector_rows[project_id, index_version, row['vector_id']] = {
                'record_id': row['record_id'], 'version_id': row['version_id'],
                'fingerprint': projection_fingerprint(row)}

    def reset(self, claimed):
        scope = self._scope(claimed)
        self.resets.append((*scope, claimed.token))
        self.rows = {key: value for key, value in self.rows.items() if key[:2] != scope}
        self.vector_rows = {key: value for key, value in self.vector_rows.items() if key[:2] != scope}

    def replace_page(self, claimed, rows):
        assert isinstance(rows, (list, tuple)) and len(rows) <= 512
        project_id, index_version = self._scope(claimed)
        self.seed(project_id, index_version, rows)
        self.pages_written.append((claimed.token, len(rows)))

    def apply_events(self, claimed, events):
        project_id, index_version = self._scope(claimed)
        for event in events:
            envelope = event['payload']['_milvus_projection']
            assert envelope['index_version'] == index_version
            if event['operation'] == 'tombstone':
                self.rows.pop((project_id, index_version, envelope['record_id']), None)
                self.vector_rows = {key: value for key, value in self.vector_rows.items()
                    if key[:2] != (project_id, index_version) or value['record_id'] != envelope['record_id']}
            elif envelope.get('rows') is not None or envelope.get('row') is not None:
                rows = envelope.get('rows')
                if rows is None:
                    rows = [envelope['row']]
                elif envelope.get('record_id'):
                    self.vector_rows = {key: value for key, value in self.vector_rows.items()
                        if key[:2] != (project_id, index_version) or value['record_id'] != envelope['record_id']}
                self.seed(project_id, index_version, rows)

    def for_record_ids(self, project_id, index_version, record_ids):
        assert len(record_ids) <= 1000
        return {record_id: self.rows[project_id, index_version, record_id]
                for record_id in record_ids if (project_id, index_version, record_id) in self.rows}

    def for_vector_ids(self, project_id, index_version, vector_ids):
        assert len(vector_ids) <= 1000
        return {vector_id: {name: value[name] for name in ('version_id', 'fingerprint')}
                for vector_id in vector_ids
                if (value := self.vector_rows.get((project_id, index_version, vector_id))) is not None}

    def pages(self, project_id, index_version, *, batch_size=512):
        rows = {key[2]: value for key, value in self.rows.items()
                if key[:2] == (project_id, index_version)}
        ids = sorted(rows)
        for offset in range(0, len(ids), batch_size):
            yield {record_id: rows[record_id] for record_id in ids[offset:offset + batch_size]}

    def count(self, project_id, index_version):
        return sum(key[:2] == (project_id, index_version) for key in self.vector_rows)

    def manifest(self, project_id, index_version):
        assert not self.store.guard_active
        return ReceiptManifest(self, project_id, index_version)


class FakeGenerationCleanups:
    def __init__(self):
        self.retired = set()

    def enqueue(self, project_id, index_version):
        self.retired.add((project_id, index_version))

    def drain(self, owner, delete_versions):
        assert owner
        for project_id, version in sorted(self.retired):
            delete_versions(project_id, [version])
        # Retirement markers survive successful deletes so a late RPC is reaped.
        return {'claimed': len(self.retired), 'cleaned': len(self.retired), 'failed': 0}


class FakeStore:
    def __init__(self, batches=(), watermark=0, rebuild=None):
        self.batches = list(batches)
        self.watermark, self.rebuild_state = watermark, rebuild
        self.acks, self.failures, self.renewals, self.finished = [], [], [], []
        self.guard_active = False
        self.cleared, self.progress, self.expired, self.begin_requests = [], [], [], []
        self.active_index_version, self.activated_tokens = 0, []
        self.schema_version, self.current_claim = 2, None
        self.live_state = {'index_version': 1, 'catalog': default_catalog().to_dict(),
                           'embedding_model': 'test', 'dim': 1, 'phase': 'ready'}
        self.receipts = FakeReceipts(self)
        self.generation_cleanups = FakeGenerationCleanups()

    def bind_claim(self, claimed, project_id, target, schema_version, owner=None):
        claimed.project_id, claimed.target = project_id, target
        claimed.schema_version = self.schema_version = schema_version
        if owner is not None:
            claimed.owner = owner
        if target == 'milvus':
            state = self.cursor(project_id, target)['rebuild_state']
            claimed.index_version, claimed.catalog = state['index_version'], state.get('catalog')
        self.current_claim = claimed
        return claimed

    def assert_current(self, claimed):
        if self.current_claim is None or (claimed.token, claimed.owner) != (
                self.current_claim.token, self.current_claim.owner):
            raise ProjectionLeaseLost('projection claim lease was lost')

    def cursor(self, project_id, target):
        state = self.rebuild_state['state'] if self.rebuild_state else self.live_state
        return {'rebuild_state': dict(state)}

    def claim_contiguous_batch(self, project_id, target, schema_version, **kwargs):
        if not self.batches:
            return None
        claimed = self.batches.pop(0)
        if kwargs.get('build') is not None:
            self.assert_current(kwargs['build'])
            claimed.build_id = kwargs['build'].build_id
        return self.bind_claim(claimed, project_id, target, schema_version, kwargs.get('owner'))

    def renew_claim(self, claimed, lease_seconds=30):
        self.assert_current(claimed)
        self.renewals.append(claimed.token)
        return claimed

    def ack_contiguous(self, claimed, schema_version):
        self.assert_current(claimed)
        assert schema_version == claimed.schema_version
        if claimed.target == 'milvus':
            self.receipts.apply_events(claimed, claimed.events)
        self.acks.append(claimed.token)
        return claimed.to_seq

    def mark_failed(self, claimed, error, schema_version=None):
        self.assert_current(claimed)
        self.failures.append((claimed.token, str(error)))

    def project_watermark(self, project_id):
        return self.watermark

    def begin_rebuild(self, project_id, target, schema_version, owner, *, lease_guard,
                      embedding_model=None, dim=None):
        self.begin_requests.append((project_id, target))
        if self.rebuild_state is None:
            self.rebuild_state = {
                'claim': batch(build_id='build-1', token='build-token'),
                'snapshot_seq': 3, 'records': [snapshot_record()],
                'state': {'build_id': 'build-1', 'phase': 'snapshot',
                          'snapshot_seq': 3, 'applied_seq': 3, 'index_version': 1}}
        state = self.rebuild_state['state']
        if target == 'milvus':
            state.update(embedding_model=embedding_model, dim=dim)
        self.bind_claim(self.rebuild_state['claim'], project_id, target, schema_version, owner)
        return self.rebuild_state

    def record_rebuild_progress(self, claimed, schema_version, state):
        self.assert_current(claimed)
        assert schema_version == claimed.schema_version
        self.progress.append(dict(state))
        self.rebuild_state['state'] = dict(state)

    @contextmanager
    def activation_guard(self, project_id, claimed, schema_version):
        self.assert_current(claimed)
        assert (project_id, schema_version) == (claimed.project_id, claimed.schema_version)
        self.guard_active = True
        try:
            yield self.watermark
        finally:
            self.guard_active = False

    def activate_index(self, claimed, schema_version, applied_seq, state):
        self.assert_current(claimed)
        assert self.guard_active and state['validation']['ok'] is True
        assert applied_seq == self.watermark
        assert state['index_version'] == claimed.index_version
        self.active_index_version = state['index_version']
        self.activated_tokens.append(claimed.token)
        state = dict(state, phase='ready')
        self.rebuild_state['state'] = state
        self.finished.append({'claim': claimed, 'applied_seq': applied_seq, 'state': state})
        previous = state.get('previous_index_version')
        if previous and previous < state['index_version']:
            self.generation_cleanups.enqueue(claimed.project_id, previous)

    def list_incomplete_rebuilds(self, target=None):
        raise AssertionError('explicit rebuild must not enumerate other projects')

    def list_expired_rollbacks(self, target=None):
        return list(self.expired)

    def clear_previous_destination(self, project_id, target, previous):
        self.cleared.append(previous)


class MemoryMilvus:
    dim = 1

    def __init__(self):
        self.rows, self.writes, self.deletes, self.retired = {}, [], [], []

    def ensure_collection(self, *, project_id, index_version, catalog):
        assert catalog == default_catalog() and len(catalog.column_defs()) == 27
        assert {'owner_id', 'support_context_id', 'support_context_version',
                'window_index', 'window_start', 'window_end'} <= {
                    column.name for column in catalog.column_defs()}
        return self

    def check_collection(self, *, catalog):
        assert catalog == default_catalog()
        return self

    def delete_records(self, record_ids, *, project_id, index_version, catalog):
        assert catalog == default_catalog()
        self.deletes.append((record_ids, project_id, index_version))
        self.rows = {key: row for key, row in self.rows.items()
                     if key[:2] != (project_id, index_version) or
                     (record_ids is not None and key[2] not in record_ids)}

    def upsert_rows(self, rows, *, project_id, index_version, catalog):
        assert catalog == default_catalog()
        for row in rows:
            assert (row['project_id'], row['index_version']) == (project_id, index_version)
            self.rows[project_id, index_version, row['record_id']] = dict(row)
        self.writes.extend(rows)

    def validate_projection(self, expected, *, project_id, index_version, catalog):
        assert isinstance(expected, ReceiptManifest)
        rows = [row for key, row in self.rows.items() if key[:2] == (project_id, index_version)]
        ok = True
        for start in range(0, len(rows), 512):
            page = rows[start:start + 512]
            actual = {row['vector_id']: {'version_id': row['version_id'],
                'fingerprint': projection_fingerprint(row)} for row in page}
            ok = len(actual) == len(page) and actual == expected.lookup_vectors(list(actual)) and ok
        return {'ok': ok and len(rows) == len(expected), 'count': len(rows),
                'expected_count': len(expected)}

    def delete_versions(self, project_id, versions):
        self.retired.append((project_id, tuple(versions)))
        self.rows = {key: row for key, row in self.rows.items()
                     if key[0] != project_id or key[1] not in versions}


class Encoder:
    identity, dimension = 'test', 1

    def __init__(self):
        self.encoded = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return [[1.0] for _ in texts]


_DEFAULT_VALIDATION = object()


class FakeAdapter(MilvusProjectionAdapter):
    def __init__(self, fail=False, validation=_DEFAULT_VALIDATION):
        super().__init__(MemoryMilvus(), encoder=Encoder())
        self.fail, self.validation = fail, validation
        self.applied, self.phases = [], []

    def apply(self, project_id, event, destination=None, fence=None, **kwargs):
        if self.fail:
            raise RuntimeError('adapter failed')
        self.applied.append((event['seq'], kwargs['index_version'], fence))
        return super().apply(project_id, event, destination, fence, **kwargs)

    def preflight(self, project_id, state, inputs):
        self.phases.append('preflight')
        return super().preflight(project_id, state, inputs)

    def load_rows(self, project_id, rows, state, catalog, **kwargs):
        self.phases.append(('snapshot', state['snapshot_seq'], len(rows)))
        return super().load_rows(project_id, rows, state, catalog, **kwargs)

    def validate(self, project_id, state, catalog, *, expected):
        self.phases.append('validate')
        if self.validation is not _DEFAULT_VALIDATION:
            return self.validation
        return super().validate(project_id, state, catalog, expected=expected)


class FakeGraphAdapter:
    def __init__(self, fail=False):
        self.fail, self.applied, self.retired = fail, [], []

    def apply(self, project_id, event, destination=None, fence=None):
        if self.fail:
            raise RuntimeError('adapter failed')
        self.applied.append((event['seq'], destination, fence))

    def retire(self, project_id, destination):
        self.retired.append(destination)


def resumed_build(phase='catchup', *, index_version=1, token='resume-token', **state):
    return {'claim': batch(build_id='build-1', token=token, index_version=index_version),
            'snapshot_seq': 3, 'records': [],
            'state': {'build_id': 'build-1', 'phase': phase,
                      'destination': 'knowledge_vectors', 'snapshot_seq': 3, 'applied_seq': 3,
                      'index_version': index_version, 'catalog': default_catalog().to_dict(),
                      'embedding_model': 'test', 'dim': 1, **state}}


def seed_snapshot(store, adapter, *, index_version=1):
    row = build_projection(snapshot_record(), default_catalog(), index_version=index_version)
    store.receipts.seed('project', index_version, [row])
    adapter._store().upsert_rows([dict(row, embedding=[1.0])], project_id='project',
                               index_version=index_version, catalog=default_catalog())
    return row


def test_drain_renews_before_each_apply_and_fences_ack():
    store = FakeStore([batch(1, 2, token='lease-a')])
    adapter = FakeAdapter()
    result = ProjectionSync(store, {'milvus': adapter}, schema_version=2).drain_once(
        'project', 'milvus')
    assert result.status == 'applied' and result == 2
    assert len(store.renewals) >= 2 and set(store.renewals) == {'lease-a'}
    assert adapter.applied == [(1, 1, 'lease-a'), (2, 1, 'lease-a')]
    assert store.acks == ['lease-a']
    assert store.receipts.count('project', 1) == 2
    assert set(adapter._store().rows) == {('project', 1, 'r-1'), ('project', 1, 'r-2')}


@pytest.mark.parametrize('target', ['milvus', 'neo4j'])
def test_failed_delivery_uses_same_claim_token_and_never_acks(target):
    store = FakeStore([batch(1, token='lease-a')])
    adapter = FakeAdapter(fail=True) if target == 'milvus' else FakeGraphAdapter(fail=True)
    result = ProjectionSync(store, {target: adapter}, schema_version=2).drain_once('project', target)
    assert result.status == ('requires_rebuild' if target == 'milvus' else 'failed')
    assert store.acks == [] and store.receipts.count('project', 1) == 0
    assert store.failures == [('lease-a', 'adapter failed')]


def test_schema_mismatch_surfaces_rebuild_required_without_adapter_calls():
    class MismatchedStore(FakeStore):
        def claim_contiguous_batch(self, *args, **kwargs):
            raise ProjectionRebuildRequired(
                'project', 'milvus', current_schema=1, required_schema=2)

    adapter = FakeAdapter()
    with pytest.raises(ProjectionRebuildRequired) as raised:
        ProjectionSync(MismatchedStore(), {'milvus': adapter}, schema_version=2).drain_once(
            'project', 'milvus')
    assert raised.value.requires_rebuild is True
    assert adapter.applied == [] and adapter.phases == []
    assert adapter._store().writes == [] and adapter._store().retired == []


def test_validation_failure_marks_build_failed_and_never_switches():
    store = FakeStore(watermark=3)
    adapter = FakeAdapter(validation={'ok': False, 'reason': 'count mismatch'})
    with pytest.raises(RuntimeError, match='validation failed'):
        ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild('project', 'milvus')
    assert store.active_index_version == 0 and store.finished == []
    assert store.failures == [('build-token', 'project index validation failed')]
    assert store.receipts.count('project', 1) == 1
    assert store.generation_cleanups.retired == set()


def test_rebuild_resumes_persisted_destination_without_starting_new_build():
    resumed = resumed_build()
    store = FakeStore([batch(4, token='catchup-token', build_id='build-1')],
                      watermark=4, rebuild=resumed)
    adapter = FakeAdapter()
    seed_snapshot(store, adapter)
    result = ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild('project', 'milvus')
    assert store.begin_requests == [('project', 'milvus')]
    assert not any(isinstance(phase, tuple) and phase[0] == 'snapshot' for phase in adapter.phases)
    assert adapter._store().deletes == [] and store.receipts.resets == []
    assert adapter._encoder.encoded == ['v-4']
    assert result['applied_seq'] == 4 and result['indexed'] == 2
    assert result['destination'] == 'knowledge_vectors' and result['index_version'] == 1
    assert store.activated_tokens == ['catchup-token']
    assert all('expected_projection' not in state for state in store.progress)


def test_rollback_window_dual_writes_then_retires_previous_projection():
    # Alias rollback remains the Neo4j contract; Milvus uses generation retirement.
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    store = FakeStore([batch(7, token='lease-a', destination='new-live',
                             previous='old-live', rollback_until=future)])
    adapter = FakeGraphAdapter()
    sync = ProjectionSync(store, {'neo4j': adapter}, schema_version=2)
    sync.drain_once('project', 'neo4j')
    assert [(item[1], item[2]) for item in adapter.applied] == [
        ('new-live', 'lease-a'), ('old-live', 'lease-a')]
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.batches.append(batch(8, token='lease-b', destination='new-live',
                               previous='old-live', rollback_until=past))
    sync.drain_once('project', 'neo4j')
    assert adapter.retired == ['old-live'] and store.cleared == ['old-live']


def test_milvus_delivery_writes_only_current_generation_and_cleanup_reaps_late_rows():
    store = FakeStore([batch(7, token='lease-a', previous='obsolete-alias',
                             rollback_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())])
    adapter = FakeAdapter()
    old = build_projection(snapshot_record(), default_catalog(), index_version=1)
    store.live_state['index_version'] = 2
    store.batches[0].events = (milvus_event(7, index_version=2),)
    store.generation_cleanups.enqueue('project', 1)
    sync = ProjectionSync(store, {'milvus': adapter}, schema_version=2)
    assert sync.drain_once('project', 'milvus') == 1
    assert adapter.applied == [(7, 2, 'lease-a')]
    assert store.cleared == []
    for _ in range(2):
        adapter._store().upsert_rows([dict(old, embedding=[1.0])], project_id='project',
                                   index_version=1, catalog=default_catalog())
        assert sync.maintain_generations() == {'claimed': 1, 'cleaned': 1, 'failed': 0}
        assert set(adapter._store().rows) == {('project', 2, 'r-7')}
    assert store.generation_cleanups.retired == {('project', 1)}


def test_explicit_rebuild_resumes_only_the_requested_incomplete_build():
    store = FakeStore(watermark=3, rebuild=resumed_build())
    adapter = FakeAdapter()
    sync = ProjectionSync(store, {'milvus': adapter}, schema_version=2)
    assert not hasattr(sync, 'resume_pending')
    result = sync.rebuild('project', 'milvus')
    assert result['ready'] is True and result['index_version'] == 1
    assert store.begin_requests == [('project', 'milvus')]
    assert store.activated_tokens == ['resume-token']


@pytest.mark.parametrize('validation', [None, {}, {'ok': False}, {'ok': 1}, 1])
def test_validation_is_fail_closed(validation):
    store = FakeStore(watermark=3)
    adapter = FakeAdapter(validation=validation)
    with pytest.raises(RuntimeError, match='validation failed'):
        ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild('project', 'milvus')
    assert store.active_index_version == 0 and store.finished == []
    assert store.failures == [('build-token', 'project index validation failed')]
    assert all(state['phase'] != 'ready' for state in store.progress)


def test_snapshot_loading_resume_resets_and_replays_snapshot():
    resumed = resumed_build('snapshot_loading')
    resumed['records'] = [snapshot_record()]
    store, adapter = FakeStore(watermark=3, rebuild=resumed), FakeAdapter()
    stale = build_projection(snapshot_record('stale', 'stale-v'), default_catalog(), index_version=1)
    other_generation = build_projection(snapshot_record('old', 'old-v'), default_catalog(), index_version=2)
    other_project = build_projection(snapshot_record(project_id='other'), default_catalog(), index_version=1)
    for row in (stale, other_generation, other_project):
        store.receipts.seed(row['project_id'], row['index_version'], [row])
        adapter._store().upsert_rows([dict(row, embedding=[1.0])], project_id=row['project_id'],
                                   index_version=row['index_version'], catalog=default_catalog())
    result = ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild('project', 'milvus')
    assert adapter._store().deletes == [(None, 'project', 1)]
    assert store.receipts.resets == [('project', 1, 'resume-token')]
    assert ('snapshot', 3, 1) in adapter.phases
    assert store.receipts.pages_written == [('resume-token', 1)]
    assert any(state['phase'] == 'snapshot_loading' for state in store.progress)
    assert any(state['phase'] == 'catchup' for state in store.progress)
    assert set(adapter._store().rows) == {('project', 1, 'snapshot'), ('project', 2, 'old'),
                                         ('other', 1, 'snapshot')}
    assert result['indexed'] == 1
    assert store.receipts.count('project', 2) == store.receipts.count('other', 1) == 1


def test_activation_resume_reuses_persisted_generation_and_receipts_without_reencoding():
    store = FakeStore(watermark=3, rebuild=resumed_build(
        'switching', index_version=2, previous_index_version=1, token='new-owner'))
    adapter = FakeAdapter()
    seed_snapshot(store, adapter, index_version=2)
    result = ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild('project', 'milvus')
    assert adapter._encoder.encoded == [] and adapter._store().deletes == []
    assert adapter.phases == ['preflight', 'validate']
    assert store.activated_tokens == ['new-owner'] and store.active_index_version == 2
    assert store.finished[0]['state']['previous_index_version'] == 1
    assert result['index_version'] == 2 and result['indexed'] == 1
    assert store.generation_cleanups.retired == {('project', 1)}


def test_maintenance_retires_expired_previous_for_idle_projects():
    store = FakeStore()
    store.expired = [('project', 'neo4j', 'old-live')]
    adapter = FakeGraphAdapter()
    count = ProjectionSync(store, {'neo4j': adapter}, schema_version=2).maintain_rollbacks('neo4j')
    assert count == 1
    assert adapter.retired == ['old-live'] and store.cleared == ['old-live']


def test_begin_rebuild_exception_is_handled_without_unbound_claim_error():
    class FailingStore(FakeStore):
        def begin_rebuild(self, *args, **kwargs):
            raise RuntimeError('capture failed')

    store, adapter = FailingStore(), FakeAdapter()
    with pytest.raises(RuntimeError, match='capture failed'):
        ProjectionSync(store, {'milvus': adapter}, schema_version=2).rebuild('project', 'milvus')
    assert store.failures == [] and store.finished == []
    assert adapter.phases == [] and adapter._store().writes == []


class ProjectionTargetSchemaTests(unittest.TestCase):
    def test_api_drains_milvus_four_and_neo4j_three_without_cross_target_schema_changes(self):
        import knowledge_service.api as api

        acknowledged = []

        class TargetStore(FakeStore):
            def ack_contiguous(self, claimed, schema_version):
                acknowledged.append((claimed.target, schema_version))
                return super().ack_contiguous(claimed, schema_version)

        store = TargetStore([batch(1), batch(2)])
        service = MagicMock()
        service.encoder = Encoder()
        repository = SimpleNamespace(projections=store, storage_namespace='test')
        graph = FakeGraphAdapter()
        configured = []

        class AssemblyComplete(Exception):
            pass

        def configure(*args, **kwargs):
            configured.append(ProjectionSync(*args, **kwargs))
            raise AssemblyComplete

        def install_graph(app, service):
            app.state.neo4j = SimpleNamespace(
                _driver=object(), database='neo4j', delete_project=lambda project: None,
                status=lambda: {'configured': True})

        with patch.dict('os.environ', {'KG_SKIP_DOTENV': '1', 'KG_VECTOR_BACKEND': 'milvus'}), \
                patch.multiple(api, Repository=MagicMock(return_value=repository),
                    KnowledgeService=MagicMock(return_value=service), RuntimeCapabilities=MagicMock(),
                    DocumentUploads=MagicMock(), UserStore=MagicMock(), _ensure_seed_superadmin=MagicMock(),
                    OntologySync=MagicMock(), _build_milvus_store=MagicMock(return_value=MemoryMilvus()),
                    Neo4jProjectionAdapter=MagicMock(return_value=graph), install_neo4j=install_graph,
                    ProjectionSync=configure), \
                patch('knowledge_service.core.security.resolve_jwt_secret', return_value=('test', False)):
            with self.assertRaises(AssemblyComplete):
                api.create_app('unused-pure-test-dsn', encoder=service.encoder, auth_disabled=True)

        sync, = configured
        # Use a deterministic encoder/store while exercising the assembled sync
        # and real Milvus adapter delivery, including its receipt acknowledgement.
        sync.adapters['milvus'] = FakeAdapter()
        worker = ProjectionWorker(SimpleNamespace(), sync, cleanup_handlers={}, max_batches_per_project=1)
        self.assertEqual(worker.drain_project('project'), 2)
        self.assertEqual(acknowledged, [('neo4j', 3), ('milvus', 4)])
        self.assertEqual(store.activated_tokens, [])
        self.assertEqual(store.schema_version, 4)
        self.assertEqual(sync.schema_for('milvus'), 4)
        self.assertEqual(sync.schema_for('neo4j'), 3)
        self.assertEqual(sync.schema_version, 4)
        self.assertEqual(store.receipts.count('project', 1), 1)
        self.assertEqual(len(graph.applied), 1)

    def test_mapped_milvus_rebuild_fences_capture_progress_and_activation_with_four(self):
        store, adapter = FakeStore(watermark=3), FakeAdapter()
        sync = ProjectionSync(store, {'milvus': adapter}, schema_version={'milvus': 4, 'neo4j': 3})
        result = sync.rebuild('project', 'milvus')
        self.assertTrue(result['ready'])
        self.assertEqual(store.current_claim.schema_version, 4)
        self.assertEqual(store.schema_version, 4)
        self.assertEqual(store.activated_tokens, ['build-token'])

    def test_mapped_neo4j_rebuild_retains_three_through_alias_switch(self):
        store = FakeStore(watermark=3, rebuild={
            'claim': batch(build_id='graph-build', token='graph-token'),
            'snapshot_seq': 3, 'records': [],
            'state': {'build_id': 'graph-build', 'phase': 'snapshot',
                      'snapshot_seq': 3, 'applied_seq': 3, 'index_version': 1}})
        store.alias_switch_guard = store.activation_guard
        store.rollback_deadline = lambda now: '2099-01-01T00:00:00Z'

        def finish(claimed, schema_version, applied, rollback_until, state, previous):
            store.assert_current(claimed)
            self.assertEqual((claimed.target, schema_version), ('neo4j', 3))
            self.assertEqual(state['phase'], 'ready')
            store.finished.append(dict(state))

        store.finish_rebuild = finish
        graph = MagicMock()
        graph.begin_rebuild.return_value = 'new-graph'
        graph.validate_rebuild.return_value = {'ok': True}
        graph.current_destination.return_value = 'old-graph'
        sync = ProjectionSync(store, {'neo4j': graph}, schema_version={'milvus': 4, 'neo4j': 3})
        result = sync.rebuild('project', 'neo4j')
        self.assertTrue(result['ready'])
        self.assertEqual(store.current_claim.schema_version, 3)
        self.assertEqual(len(store.finished), 1)
        graph.begin_rebuild.assert_called_once_with('project', 3,
            unittest.mock.ANY, fence='graph-token')
        graph.switch_alias.assert_called_once_with('project', 'new-graph',
            build_id='graph-build', fence='graph-token')

    def test_worker_audits_claim_and_fail_with_the_target_schema(self):
        observed = []
        claim = SimpleNamespace(owner='audit', token='token')

        class Projections:
            def claim_maintenance(self, project, target, schema, owner):
                observed.append(('claim', target, schema))
                return claim

            def mark_failed(self, claimed, error, schema):
                observed.append(('failed', 'neo4j', schema))

            def release_maintenance(self, claimed):
                observed.append(('released', claimed.token))

        def failed_manifest(*args):
            raise RuntimeError('audit failed')

        repository = SimpleNamespace(projections=Projections(), projection_authority_manifest=failed_manifest)
        adapter = SimpleNamespace(audit_authority=lambda *args: None,
            audit_authority_manifest=lambda *args: None, INDEXED_KINDS={'entity'})
        sync = ProjectionSync(repository.projections, {'neo4j': adapter},
            schema_version={'milvus': 4, 'neo4j': 3})
        with self.assertRaisesRegex(RuntimeError, 'audit failed'):
            ProjectionWorker(repository, sync, cleanup_handlers={}).audit_project('project')
        self.assertEqual(observed, [('claim', 'neo4j', 3), ('failed', 'neo4j', 3), ('released', 'token')])
