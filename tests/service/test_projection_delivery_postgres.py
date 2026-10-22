from knowledge_service.repository import Repository


def test_outbox_sequence_survives_public_row_filter_and_acknowledges_revisions(tmp_path):
    repo = Repository(tmp_path / 'delivery')
    project = repo.create_project('delivery')['id']
    # This test exercises incremental delivery of a previously activated build.
    with repo._transaction():
        repo.projections._ensure_cursor(project, 'milvus', 3)
        repo._db.execute(
            "UPDATE projection_cursors SET status='ready',active_destination=? "
            'WHERE project_id=? AND target=?',
            ('delivery-generation', project, 'milvus'))
    repo.put_record(project, {'id': 'c', 'kind': 'chunk', 'text': 'first'})

    claim = repo.projections.claim_contiguous_batch(project, 'milvus', 3)
    assert [event['seq'] for event in claim.events] == [1]
    repo.projections.ack_contiguous(claim, 3)
    assert repo.projection_ready(project, 'milvus', 3)

    repo.put_record(project, {'id': 'c', 'kind': 'chunk', 'text': 'second'},
                    expected_version=1)
    assert not repo.projection_ready(project, 'milvus', 3)
    claim = repo.projections.claim_contiguous_batch(project, 'milvus', 3)
    assert [event['seq'] for event in claim.events] == [2, 3]
    assert [event['operation'] for event in claim.events] == ['tombstone', 'upsert']
    repo.projections.ack_contiguous(claim, 3)
    assert repo.projections.cursor(project, 'milvus')['applied_seq'] == 3
    assert repo.projection_ready(project, 'milvus', 3)
