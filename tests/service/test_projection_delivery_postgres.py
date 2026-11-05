from knowledge_service.repository import Repository
from knowledge_service.services.projection_runtime import MilvusProjectionAdapter
from knowledge_service.services.projection_sync import ProjectionSync
from test_projection_sync import Encoder, MemoryMilvus


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
