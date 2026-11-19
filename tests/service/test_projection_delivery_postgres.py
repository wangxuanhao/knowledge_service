from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync
from test_projection_sync import Encoder, MemoryMilvus


def test_project_sequence_is_gapless_across_rollback_and_competing_writers(tmp_path):
    left = Repository(tmp_path / 'sequence')
    right = Repository(tmp_path / 'sequence')
    project = left.create_project('competing writers')['id']

    def enqueue(repo, version):
        return repo.projections.enqueue(project, 'tombstone', version,
                                        {'record_id': 'r', 'version_id': version})

    with pytest.raises(RuntimeError, match='abort'):
        with left._transaction():
            assert enqueue(left, 'discard') == 1
            raise RuntimeError('abort')
    assert right.projections.project_watermark(project) == 0

    start = Barrier(2)

    def writer(repo, label):
        with repo._transaction():
            start.wait(timeout=10)
            return [(enqueue(repo, label + suffix), label + suffix) for suffix in ('1', '2')]

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(writer, left, 'a')
        second = pool.submit(writer, right, 'b')
        a, b = first.result(timeout=15), second.result(timeout=15)
    rows = left._db.execute('SELECT seq AS n,version_id FROM projection_outbox '
                            'WHERE project_id=? ORDER BY seq', (project,)).fetchall()
    assert [tuple(row) for row in rows] == sorted(a + b)
    assert sorted([[n for n, _ in a], [n for n, _ in b]]) == [[1, 2], [3, 4]]
    assert left.projections.project_watermark(project) == 4


def test_outbox_sequence_survives_public_row_filter_and_acknowledges_revisions(tmp_path):
    repo = Repository(tmp_path / 'delivery')
    project = repo.create_project('delivery')['id']
    # Incremental events belong to a genuinely activated empty generation.
    external = MemoryMilvus()
    sync = ProjectionSync(repo.projections, {'milvus': MilvusProjectionAdapter(
        external, encoder=Encoder())}, schema_version=3)
    built = sync.rebuild(project, 'milvus')
    assert built['indexed'] == built['applied_seq'] == 0
    assert repo.projection_ready(project, 'milvus', 3)
    repo.put_record(project, {'id': 'c', 'kind': 'chunk', 'text': 'first'})

    claim = repo.projections.claim_contiguous_batch(project, 'milvus', 3)
    assert claim is not None
    assert [event['seq'] for event in claim.events] == [1]
    assert claim.events[0]['payload']['_milvus_projection']['index_version'] == built['index_version']
    repo.projections.ack_contiguous(claim, 3)
    assert repo.projection_ready(project, 'milvus', 3)

    repo.put_record(project, {'id': 'c', 'kind': 'chunk', 'text': 'second'},
                    expected_version=1)
    assert not repo.projection_ready(project, 'milvus', 3)
    claim = repo.projections.claim_contiguous_batch(project, 'milvus', 3)
    assert claim is not None
    assert [event['seq'] for event in claim.events] == [2, 3]
    assert [event['operation'] for event in claim.events] == ['tombstone', 'upsert']
    repo.projections.ack_contiguous(claim, 3)
    assert repo.projections.cursor(project, 'milvus')['applied_seq'] == 3
    assert repo.projection_ready(project, 'milvus', 3)
    receipts = repo.projections.receipts.for_record_ids(project, built['index_version'], ['c'])
    assert receipts['c']['version_id'] == repo.get_record(project, 'c')['version_id']
