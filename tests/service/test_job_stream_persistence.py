from threading import Timer

from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def test_stream_observes_completion_without_local_subscriber_event(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'stream', encoder=HashingEncoder(), auth_disabled=True)
    repo = app.state.service.repository
    task = {'id': 'remote-task', 'kind': 'ingest', 'payload': {}, 'status': 'running',
            'lease_until': '2100-01-01T00:00:00Z', 'progress': 20}
    repo.save_artifact('job', task)
    original = app.state.jobs.subscribe
    timers = []

    def subscribe(task_id):
        queue = original(task_id)
        timer = Timer(.1, lambda: repo.save_artifact('job',
            {**task, 'status': 'completed', 'progress': 100}))
        timer.start()
        timers.append(timer)
        return queue

    monkeypatch.setattr(app.state.jobs, 'subscribe', subscribe)
    with TestClient(app) as client:
        response = client.get('/api/jobs/remote-task/stream')
        assert 'event: completed' in response.text
        assert '"progress": 100' in response.text
    for timer in timers:
        timer.join()


def test_cancelled_task_closes_stream_with_compatible_terminal_event(tmp_path):
    app = create_app(tmp_path / 'stream', encoder=HashingEncoder(), auth_disabled=True)
    app.state.service.repository.save_artifact('job',
        {'id': 'cancelled-task', 'kind': 'ingest', 'status': 'cancelled', 'progress': 20})
    with TestClient(app) as client:
        response = client.get('/api/jobs/cancelled-task/stream')
        assert 'event: failed' in response.text
        assert '"status": "cancelled"' in response.text
