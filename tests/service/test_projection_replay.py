from datetime import datetime, timezone

import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync


class MilvusDestination:
    collection_name = 'active'

    def __init__(self):
        self.rows = {}
        self.writes = []
        self.markers = []
        self.fail_once = None

    def upsert(self, records, *, destination=None, projection_seq=None,
               fence=None, flush=True):
        if projection_seq == self.fail_once:
            self.fail_once = None
            raise RuntimeError('Milvus RPC failed after an applied prefix')
        for row in records:
            self.rows[(destination, row['id'])] = dict(row)
        self.writes.append((destination, projection_seq, fence))

    def write_fence_marker(self, project, destination, seq, fence):
        self.markers.append((project, destination, seq, fence))

    def recreate_collection(self, *, destination):
        self.rows = {key: row for key, row in self.rows.items()
                     if key[0] != destination}


def event(seq):
    return {'seq': seq, 'operation': 'upsert', 'payload': {
        'id': 'record', 'version_id': f'v{seq}', 'kind': 'chunk',
        'text': f'text {seq}', 'embedding': [1.0], 'metadata': {},
    }}


def test_partial_batch_retry_with_new_lease_acknowledges_watermark(tmp_path):
    repo = Repository(tmp_path / 'projection-replay')
    project = repo.create_project('projection replay')['id']
    for record_id in ('first', 'second'):
        repo.put_record(project, {
            'id': record_id, 'kind': 'chunk', 'text': record_id,
            'embedding': [1.0], 'embedding_model': 'test',
        })
    milvus = MilvusDestination()
    milvus.fail_once = 2

    def current(project_id, seq, fence):
        cursor = repo.projections.cursor(project_id, 'milvus') or {}
        expiry = cursor.get('claim_until')
        return bool(
            cursor.get('claim_token') == fence
            and int(cursor.get('claimed_from_seq') or 0) <= seq
            <= int(cursor.get('claimed_to_seq') or -1)
            and expiry
            and datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            > datetime.now(timezone.utc))

    adapter = MilvusProjectionAdapter(milvus, fence_is_current=current)
    sync = ProjectionSync(repo.projections, {'milvus': adapter}, schema_version=3)
    assert int(sync.drain_once(project, 'milvus')) == 0
    assert repo.projections.cursor(project, 'milvus')['applied_seq'] == 0
    old_token = milvus.writes[0][2]

    assert int(sync.drain_once(project, 'milvus')) == 2
    cursor = repo.projections.cursor(project, 'milvus')
    assert cursor['applied_seq'] == repo.projections.project_watermark(project) == 2
    assert repo.projection_ready(project, 'milvus', 3)
    assert [seq for _, seq, _ in milvus.writes] == [1, 2]
    assert milvus.writes[-1][2] != old_token
    assert {row['id'] for row in milvus.rows.values()} == {'first', 'second'}


@pytest.mark.parametrize('seq', [1, 2, 3])
def test_stale_fence_cannot_acknowledge_or_overwrite_newer_state(seq):
    milvus = MilvusDestination()
    adapter = MilvusProjectionAdapter(
        milvus, fence_is_current=lambda project, seq, fence: fence == 'current',
        repair_authority=lambda *args: None)
    assert adapter.apply('project', event(2), fence='current') is True
    writes = list(milvus.writes)
    markers = list(milvus.markers)

    assert adapter.apply('project', event(seq), fence='stale') is False
    assert milvus.writes == writes
    assert milvus.markers == markers
    assert milvus.rows[('active', 'record')]['version_id'] == 'v2'


def test_verified_replay_preserves_the_newer_applied_record_and_marker():
    milvus = MilvusDestination()
    adapter = MilvusProjectionAdapter(
        milvus, fence_is_current=lambda project, seq, fence: True)
    assert adapter.apply('project', event(1), fence='first-lease') is True
    assert adapter.apply('project', event(2), fence='first-lease') is True
    writes = list(milvus.writes)
    markers = list(milvus.markers)

    assert adapter.apply('project', event(1), fence='retry-lease') is True
    assert adapter.apply('project', event(2), fence='retry-lease') is True
    assert milvus.writes == writes
    assert milvus.markers == markers
    assert milvus.rows[('active', 'record')]['version_id'] == 'v2'


def test_expired_fence_during_encoding_stops_before_external_write():
    milvus = MilvusDestination()
    current = {'valid': True}

    class Encoder:
        identity = 'test'

        def encode(self, texts):
            current['valid'] = False
            return [[1.0]]

    adapter = MilvusProjectionAdapter(
        milvus, encoder=Encoder(),
        fence_is_current=lambda *args: current['valid'],
        repair_authority=lambda *args: None)
    missing = event(1)
    missing['payload'].pop('embedding')

    assert adapter.apply('project', missing, fence='expired') is False
    assert milvus.writes == []
    assert milvus.markers == []


def test_verified_replay_watermark_is_scoped_to_destination():
    milvus = MilvusDestination()
    adapter = MilvusProjectionAdapter(
        milvus, fence_is_current=lambda *args: True)
    assert adapter.apply('project', event(2), destination='new', fence='claim')
    assert adapter.apply('project', event(1), destination='old', fence='claim')
    assert milvus.rows[('new', 'record')]['version_id'] == 'v2'
    assert milvus.rows[('old', 'record')]['version_id'] == 'v1'


def test_reset_rebuild_invalidates_previously_applied_prefix():
    milvus = MilvusDestination()
    adapter = MilvusProjectionAdapter(
        milvus, fence_is_current=lambda *args: True)
    assert adapter.apply('project', event(2), destination='build', fence='old')
    adapter.reset_rebuild('project', 'build', 'build-id', fence='new')

    assert adapter.apply('project', event(1), destination='build', fence='new')
    assert milvus.rows[('build', 'record')]['version_id'] == 'v1'
