"""A thousand durable items stay bounded at the API and worker boundaries."""
import hashlib
import threading
import time

from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.models import IngestBatchSettings


def test_thousand_items_are_paged_and_idempotent(tmp_path):
    app = create_app(tmp_path / 'capacity', encoder=HashingEncoder())
    with TestClient(app) as client:
        project = client.post('/api/projects', json={'name': 'capacity'}).json()['id']
        batch = client.post(f'/api/projects/{project}/ingest-batches', json={
            'settings': {'extraction_mode': 'documents', 'requested_concurrency': 8}}).json()
        store = app.state.service.data.ingest_batches
        content = b'# Capacity\n\nDeterministic rule.'
        digest = hashlib.sha256(content).hexdigest()
        for i in range(1000):
            store.add_item(project, batch['id'], f'{i:04}.md', content, digest,
                           f'key-{i}', 1000, 1_000_000_000)
        repeated = store.add_item(project, batch['id'], '0000.md', content, digest,
                                  'key-0', 1000, 1_000_000_000)
        assert repeated['id']
        base = f'/api/projects/{project}/ingest-batches/{batch["id"]}'
        summary = client.get(base).json()
        assert sum(summary['counts'].values()) == 1000
        identifiers = set()
        cursor = ''
        for _ in range(20):
            response = client.get(base + '/items?limit=50' + (f'&cursor={cursor}' if cursor else ''))
            assert response.status_code == 200, response.text
            page = response.json()
            assert len(page['items']) == 50
            assert len(response.content) < 100_000
            assert all('content' not in item for item in page['items'])
            identifiers.update(item['id'] for item in page['items'])
            cursor = page['next_cursor']
        assert len(identifiers) == 1000 and cursor is None
        too_many = client.post(base + '/files', files={'file': ('extra.md', content)},
                               headers={'Idempotency-Key': 'extra'})
        assert too_many.status_code == 422


def test_shared_worker_and_llm_bounds_across_batches(tmp_path):
    from knowledge_service.services.ingest_batch_runner import IngestBatchRunner
    app = create_app(tmp_path / 'bounds', encoder=HashingEncoder())
    store = app.state.service.data.ingest_batches
    project = app.state.service.data.create_project('bounds')['id']
    for b in range(2):
        batch = store.create_batch(project, IngestBatchSettings(
            extraction_mode='documents', requested_concurrency=8).model_dump())
        store.set_concurrency(batch['id'], 4)
        for i in range(8):
            store.add_item(project, batch['id'], f'{i}.md', b'rule', 'hash', f'{b}-{i}', 1000, 1000)
        store.transition(project, batch['id'], 'start')
    runner = IngestBatchRunner(app.state.service, store, app.state.service.data.ingest_checkpoints,
                              app.state.source_documents, document_limit=4, llm_limit=2)
    lock = threading.Lock()
    release = threading.Event()
    active = llm_active = peak = llm_peak = 0
    def process(item, settings, owner):
        nonlocal active, llm_active, peak, llm_peak
        with lock:
            active += 1; peak = max(peak, active)
        with runner.llm_semaphore:
            with lock:
                llm_active += 1; llm_peak = max(llm_peak, llm_active)
            release.wait(3)
            with lock:
                llm_active -= 1
        with lock:
            active -= 1
        return {'document': {'id': item['document_id']}}
    runner.document_runner.run = process
    try:
        runner.run_once()
        deadline = time.monotonic() + 3
        while peak < 4 and time.monotonic() < deadline:
            time.sleep(.01)
        assert peak == 4 and llm_peak == 2
    finally:
        release.set(); runner.close(); app.state.service.data.close()
