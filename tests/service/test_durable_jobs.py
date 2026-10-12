import time
from threading import Event

from knowledge_service.repository import Repository
from knowledge_service.services.jobs import Jobs


def wait_job(repo, task_id, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = repo.get_artifact('job', task_id)
        if task['status'] == status:
            return task
        time.sleep(.02)
    raise AssertionError(task)


def test_new_worker_recovers_persisted_queued_input(tmp_path):
    repo = Repository(tmp_path / 'durable.sqlite')
    repo.save_artifact('job', {'id': 'queued', 'kind': 'echo', 'status': 'queued',
        'payload': {'value': 'source'}, 'logs': [], 'progress': 0})
    jobs = Jobs(repo, handlers={'echo': lambda payload, p, progress, key: payload})
    try:
        jobs.start()
        task = wait_job(repo, 'queued', 'completed')
        assert task['result'] == {'value': 'source'}
        assert task['attempts'] == 1
    finally:
        jobs.close()


def test_second_instance_does_not_interrupt_durable_ingestion(tmp_path):
    repo = Repository(tmp_path / 'shared.sqlite')
    project = repo.create_project('shared')['id']
    repo.save_artifact('job', {'id': 'active-task', 'kind': 'ingest', 'status': 'running',
                              'payload': {'text': 'source'}, 'project_id': project})
    document = repo.put_record(project, {'id': 'source', 'kind': 'document',
        'text': 'source', 'metadata': {'durable_task_id': 'active-task'}})
    run = repo.create_ingest_run(project, document['id'], document['version_id'])
    run = repo.update_ingest_run(project, run['id'], run['version'], status='running')
    Repository(tmp_path / 'shared.sqlite')
    assert repo.get_ingest_run(project, run['id'])['status'] == 'running'


def test_cancelled_handler_cannot_complete_and_does_not_block_next_job(tmp_path):
    repo = Repository(tmp_path / 'durable.sqlite')
    entered, release = Event(), Event()
    effects = []

    def handler(payload, p, progress, key):
        if payload['block']:
            entered.set()
            assert release.wait(5)
            progress('checkpoint', 50)
        effects.append(key)
        return {'done': key}

    jobs = Jobs(repo, handlers={'test': handler}, workers=1)
    try:
        first = jobs.enqueue('test', {'block': True})
        assert entered.wait(5)
        jobs.control(first['id'], 'cancel')
        second = jobs.enqueue('test', {'block': False})
        assert wait_job(repo, second['id'], 'completed')['result']['done'] == second['id']
        release.set()
        time.sleep(.1)
        assert first['id'] not in effects
        assert repo.get_artifact('job', first['id'])['status'] == 'cancelled'
    finally:
        release.set()
        jobs.close()
