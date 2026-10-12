from knowledge_service.repository import Repository
import pytest


@pytest.mark.parametrize('first_kind', ['job', 'ontology_sync_job'])
def test_shared_collection_rebuild_and_outbox_do_not_overlap(tmp_path, first_kind):
    repo = Repository(tmp_path / 'index-tasks')
    p1, p2, p3 = [repo.create_project(p)['id'] for p in ('p1','p2','p3')]
    repo.save_artifact('job', {'id': 'rebuild-a', 'kind': 'semantic_index',
                               'project_id': p1, 'status': 'queued'})
    repo.save_artifact('job', {'id': 'rebuild-b', 'kind': 'semantic_index',
                               'project_id': p2, 'status': 'queued'})
    repo.save_artifact('ontology_sync_job', {'id': 'outbox', 'project_id': p3,
                                            'status': 'pending'})
    first = repo.claim_task(first_kind, 'a')
    assert first is not None
    assert repo.claim_task('job', 'b') is None
    assert repo.claim_task('ontology_sync_job', 'b') is None
    assert repo.update_task(first_kind, first, {'status': 'completed'})
    assert repo.claim_task('job', 'b') is not None


def test_lease_claim_is_exclusive_and_cancel_fences_completion(tmp_path):
    repo = Repository(tmp_path / 'tasks.sqlite')
    repo.save_artifact('job', {'id': 'task', 'status': 'queued'})
    task = repo.claim_task('job', 'worker-a')
    assert task['attempts'] == 1
    assert repo.claim_task('job', 'worker-b') is None
    repo.control_task('job', 'task', 'cancel')
    assert repo.update_task('job', task, {'status': 'completed'}) is None
    assert repo.get_artifact('job', 'task')['status'] == 'cancelled'


def test_expired_task_is_reclaimed_and_old_worker_is_fenced(tmp_path):
    repo = Repository(tmp_path / 'tasks.sqlite')
    repo.save_artifact('job', {'id': 'task', 'status': 'queued'})
    old = repo.claim_task('job', 'worker-a')
    repo.save_artifact('job', {**old, 'lease_until': '2000-01-01T00:00:00Z'})
    new = repo.claim_task('job', 'worker-b')
    assert new['attempts'] == 2
    assert new['lease_token'] != old['lease_token']
    assert repo.update_task('job', old, {'status': 'completed'}) is None
    assert repo.update_task('job', new, {'status': 'completed'})['status'] == 'completed'
