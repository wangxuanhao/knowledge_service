"""Replay safety is owned by PG leases and immutable index generations.

The shared Milvus adapter no longer repairs or replays an uncertain generation
in place. These checks retain the old no-stale-write intention at that boundary.
"""
import pytest

from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.domain.search_projection import ProjectionInput, build_projection
from knowledge_service.repository import Repository
from knowledge_service.repository.projection_store import ProjectionLeaseLost
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync
from knowledge_service.services.retrieval import PROJECTION_SCHEMA_VERSIONS
from test_projection_sync import Encoder, MemoryMilvus


CATALOG = default_catalog()
MILVUS_SCHEMA_VERSION = PROJECTION_SCHEMA_VERSIONS['milvus']


class MilvusDestination(MemoryMilvus):
    """The current external row protocol, with one injectable RPC failure."""

    def __init__(self):
        super().__init__()
        self.write_attempts = 0
        self.fail_once = None

    def upsert_rows(self, records, *, project_id, index_version, catalog):
        self.write_attempts += 1
        if self.write_attempts == self.fail_once:
            self.fail_once = None
            raise RuntimeError('Milvus RPC failed after an applied prefix')
        return super().upsert_rows(records, project_id=project_id,
                                   index_version=index_version, catalog=catalog)


def event(seq, *, project='project', index_version=1):
    payload = {
        'project_id': project, 'id': 'record', 'version_id': f'v{seq}',
        'kind': 'chunk', 'text': f'text {seq}', 'metadata': {},
    }
    row = build_projection(ProjectionInput(payload), CATALOG, index_version=index_version)
    assert row is not None and 'embedding' not in row
    payload['_milvus_projection'] = {
        'index_version': index_version, 'catalog_fingerprint': CATALOG.fingerprint,
        'embedding_model': Encoder.identity, 'row': row,
    }
    return {'seq': seq, 'operation': 'upsert', 'version_id': f'v{seq}', 'payload': payload}


def active_system(tmp_path):
    repo = Repository(tmp_path / 'projection-replay')
    project = repo.create_project('projection replay')['id']
    milvus, encoder = MilvusDestination(), Encoder()
    adapter = MilvusProjectionAdapter(milvus, encoder=encoder)
    sync = ProjectionSync(repo.projections, {'milvus': adapter}, schema_version=MILVUS_SCHEMA_VERSION)
    built = sync.rebuild(project, 'milvus')
    assert built['indexed'] == built['applied_seq'] == 0
    assert repo.projections.search_index(project, MILVUS_SCHEMA_VERSION)['index_version'] == built['index_version']
    assert milvus.writes == []
    return repo, project, milvus, adapter, sync


def apply_claim(repo, adapter, claim):
    state = repo.projections.cursor(claim.project_id, 'milvus')['rebuild_state']
    for item in claim.events:
        # The real lease renewal rejects old claims before adapter work.
        repo.projections.renew_claim(claim)
        assert adapter.apply(claim.project_id, item, fence=claim.token,
                             index_version=state['index_version'], catalog=CATALOG)
    return repo.projections.ack_contiguous(claim, MILVUS_SCHEMA_VERSION)


def test_partial_batch_failure_requires_explicit_rebuild_without_retry(tmp_path):
    repo, project, milvus, _adapter, sync = active_system(tmp_path)
    generation = repo.projections.search_index(project, MILVUS_SCHEMA_VERSION)['index_version']
    for record_id in ('first', 'second'):
        repo.put_record(project, {'id': record_id, 'kind': 'chunk', 'text': record_id})
    milvus.fail_once = 2

    assert sync.drain_once(project, 'milvus').status == 'requires_rebuild'
    assert repo.projections.cursor(project, 'milvus')['applied_seq'] == 0
    assert repo.projections.receipts.count(project, generation) == 0

    assert sync.drain_once(project, 'milvus').status == 'requires_rebuild'
    cursor = repo.projections.cursor(project, 'milvus')
    assert cursor['applied_seq'] == 0
    assert cursor['status'] == 'requires_rebuild'
    assert not repo.projection_ready(project, 'milvus', MILVUS_SCHEMA_VERSION)
    with pytest.raises(RuntimeError, match='requires_rebuild'):
        repo.projections.search_index(project, MILVUS_SCHEMA_VERSION)
    assert milvus.write_attempts == 2
    assert [row['record_id'] for row in milvus.writes] == ['first']
    assert {row['record_id'] for row in milvus.rows.values()} == {'first'}


@pytest.mark.parametrize('seq', [1, 2, 3])
def test_stale_fence_cannot_acknowledge_or_overwrite_newer_state(seq):
    milvus = MilvusDestination()
    encoder = Encoder()
    adapter = MilvusProjectionAdapter(milvus, encoder=encoder,
        fence_is_current=lambda project, seq, fence: fence == 'current')
    assert adapter.apply('project', event(2), fence='current', index_version=1, catalog=CATALOG)
    writes, rows = list(milvus.writes), dict(milvus.rows)
    encoded = list(encoder.encoded)

    with pytest.raises(ProjectionLeaseLost):
        adapter.apply('project', event(seq), fence='stale', index_version=1, catalog=CATALOG)
    assert milvus.writes == writes
    assert milvus.rows == rows
    assert encoder.encoded == encoded
    assert milvus.rows[('project', 1, 'record')]['version_id'] == 'v2'


def test_acknowledged_claim_cannot_replay_over_newer_record_and_receipt(tmp_path):
    # Per-adapter replay markers were retired. PG claim ownership now prevents
    # reusing an acknowledged prefix, including after another revision arrives.
    repo, project, milvus, adapter, _sync = active_system(tmp_path)
    first = repo.put_record(project, {'id': 'record', 'kind': 'chunk', 'text': 'first'})
    old_claim = repo.projections.claim_contiguous_batch(project, 'milvus', MILVUS_SCHEMA_VERSION)
    assert apply_claim(repo, adapter, old_claim) == 1
    newest = repo.put_record(project, {'id': 'record', 'kind': 'chunk', 'text': 'second'},
                             expected_version=first['version'])
    new_claim = repo.projections.claim_contiguous_batch(project, 'milvus', MILVUS_SCHEMA_VERSION)
    assert [item['seq'] for item in new_claim.events] == [2, 3]
    assert apply_claim(repo, adapter, new_claim) == 3
    writes, rows = list(milvus.writes), dict(milvus.rows)

    with pytest.raises(ProjectionLeaseLost):
        apply_claim(repo, adapter, old_claim)
    with pytest.raises(ProjectionLeaseLost):
        repo.projections.ack_contiguous(old_claim, MILVUS_SCHEMA_VERSION)
    generation = repo.projections.search_index(project, MILVUS_SCHEMA_VERSION)['index_version']
    receipt = repo.projections.receipts.for_record_ids(project, generation, ['record'])
    assert receipt['record']['version_id'] == newest['version_id']
    assert repo.projections.cursor(project, 'milvus')['applied_seq'] == 3
    assert milvus.writes == writes
    assert milvus.rows == rows
    assert milvus.rows[(project, generation, 'record')]['version_id'] == newest['version_id']


def test_expired_fence_during_encoding_stops_before_external_write():
    milvus = MilvusDestination()
    current = {'valid': True}

    class ExpiringEncoder(Encoder):
        def encode(self, texts):
            current['valid'] = False
            return super().encode(texts)

    encoder = ExpiringEncoder()
    adapter = MilvusProjectionAdapter(milvus, encoder=encoder,
        fence_is_current=lambda *args: current['valid'])

    with pytest.raises(ProjectionLeaseLost):
        adapter.apply('project', event(1), fence='expired', index_version=1, catalog=CATALOG)
    assert encoder.encoded == ['text 1']
    assert milvus.writes == []
    assert milvus.rows == {}
    assert milvus.deletes == []


def test_frozen_rows_are_isolated_by_project_and_index_version():
    # Collection destinations were replaced by project/index_version scopes.
    milvus = MilvusDestination()
    adapter = MilvusProjectionAdapter(milvus, encoder=Encoder(),
        fence_is_current=lambda *args: True)
    for project, generation, seq in [('project', 2, 2), ('project', 1, 1), ('other', 2, 3)]:
        assert adapter.apply(project, event(seq, project=project, index_version=generation),
                             fence='claim', index_version=generation, catalog=CATALOG)
    assert {key: row['version_id'] for key, row in milvus.rows.items()} == {
        ('project', 2, 'record'): 'v2', ('project', 1, 'record'): 'v1', ('other', 2, 'record'): 'v3'}


def test_explicit_rebuild_uses_new_generation_without_changing_previous_receipts(tmp_path):
    # Resetting an in-place replay prefix was retired. A real explicit rebuild
    # captures current authority into a new generation and keeps scopes isolated.
    repo, project, milvus, _adapter, sync = active_system(tmp_path)
    first = repo.put_record(project, {'id': 'record', 'kind': 'chunk', 'text': 'first'})
    assert sync.drain_once(project, 'milvus').status == 'applied'
    newest = repo.put_record(project, {'id': 'record', 'kind': 'chunk', 'text': 'second'},
                             expected_version=first['version'])
    assert sync.drain_once(project, 'milvus').status == 'applied'
    previous = repo.projections.search_index(project, MILVUS_SCHEMA_VERSION)['index_version']
    old_row = dict(milvus.rows[(project, previous, 'record')])
    old_receipts = repo.projections.receipts.for_record_ids(project, previous, ['record'])

    rebuilt = sync.rebuild(project, 'milvus')
    generation = rebuilt['index_version']
    assert generation > previous
    assert rebuilt['indexed'] == 1
    assert rebuilt['applied_seq'] == repo.projections.project_watermark(project) == 3
    assert repo.projections.search_index(project, MILVUS_SCHEMA_VERSION)['index_version'] == generation
    assert repo.projection_ready(project, 'milvus', MILVUS_SCHEMA_VERSION)
    assert milvus.rows[(project, previous, 'record')] == old_row
    assert repo.projections.receipts.for_record_ids(project, previous, ['record']) == old_receipts
    assert milvus.rows[(project, generation, 'record')]['version_id'] == newest['version_id']
    new_receipts = repo.projections.receipts.for_record_ids(project, generation, ['record'])
    assert new_receipts['record']['version_id'] == newest['version_id']
    assert repo.projections.receipts.count(project, previous) == 1
    assert repo.projections.receipts.count(project, generation) == 1
