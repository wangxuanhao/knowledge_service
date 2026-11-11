"""A new, traceable generation rebuilds one saved document from its fixed source."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.models import IngestBatchSettings
from knowledge_service.services.document_ingest_runner import DocumentIngestRunner


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path / 'reprocessing', encoder=HashingEncoder())
    # No lifespan: these tests drive the real fenced runner synchronously.
    return app, TestClient(app)


def saved_document(app, text='固定原文，保存与检索。', generation=0):
    service = app.state.service
    project = service.repository.create_project('reprocessing')['id']
    settings = IngestBatchSettings(extraction_mode='documents', chunk_size=100, chunk_overlap=0).model_dump()
    store = service.repository.ingest_batches
    batch = store.create_batch(project, settings)
    raw = text.encode()
    item = store.add_item(project, batch['id'], 'rule.md', raw, hashlib.sha256(raw).hexdigest(), 'original', 100, 100000)
    if generation:
        service.repository._db.execute('UPDATE ingest_batch_items SET document_generation=? WHERE id=?', (generation, item['id']))
    store.transition(project, batch['id'], 'start')
    leased = store.claim(batch['id'], 'test-worker', 120)
    result = run_item(app, leased, settings)
    assert store.finish(item['id'], 'test-worker', leased['lease_token'], 'succeeded', result=result)
    return project, result['document'], batch, item


def run_item(app, item, settings):
    service = app.state.service
    return DocumentIngestRunner(service, service.repository.ingest_batches,
        service.repository.ingest_checkpoints, app.state.source_documents).run(item, settings, 'test-worker')


def submit(client, project, document, mode='documents', key='rebuild-one'):
    return client.post(f'/api/projects/{project}/documents/{document}/reprocess',
        json={'extraction_mode': mode, 'idempotency_key': key})


def test_reprocess_creates_new_generation_for_same_document_and_preserves_history(env):
    app, client = env
    project, document, old_batch, old_item = saved_document(app, generation=4)
    repo = app.state.service.repository
    repo.ingest_checkpoints.put(document['id'], 0, 'old-checkpoint', {'records': []}, document_generation=4)
    response = submit(client, project, document['id'])
    assert response.status_code == 202, response.text
    operation = response.json()
    assert operation['batch']['id'] != old_batch['id']
    assert operation['item']['id'] != old_item['id']
    assert operation['item']['document_id'] == document['id']
    assert operation['item']['document_generation'] == 5
    assert operation['item']['source_version_id'] == document['metadata']['source_document_version_id']
    assert operation['batch']['status'] == 'running'
    assert not {'content', 'upload_object_id', 'lease_token'} & operation['item'].keys()
    old = repo.ingest_batches.list_items(project, old_batch['id'])['items'][0]
    assert old['status'] == 'succeeded' and old['document_generation'] == 4
    assert repo.ingest_checkpoints.get(document['id'], 0, 'old-checkpoint')
    item = repo.ingest_batches.claim(operation['batch']['id'], 'test-worker', 120)
    result = run_item(app, item, operation['batch']['settings'])
    assert result.get('replayed') is not True
    assert result['document']['id'] == document['id']
    assert result['document']['metadata']['document_generation'] == 5
    assert result['document']['version'] > document['version']
    assert len(repo.current_records(project, kinds=['document'])) == 1


def test_reprocess_is_idempotent_and_rejects_conflicting_active_work(env):
    app, client = env
    project, document, _, _ = saved_document(app)
    first = submit(client, project, document['id'])
    assert first.status_code == 202, first.text
    replay = submit(client, project, document['id'])
    assert replay.status_code == 202
    assert replay.json()['item']['id'] == first.json()['item']['id']
    conflict = submit(client, project, document['id'], key='another-operation')
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()['detail']['code'] == 'document_reprocessing_active'
    reused = submit(client, project, document['id'], mode='discovery')
    assert reused.status_code == 409
    assert reused.json()['detail']['code'] == 'reprocess_idempotency_conflict'


def test_reprocess_checks_published_ontology_and_project_source_ownership(env):
    app, client = env
    project, document, _, _ = saved_document(app)
    missing = submit(client, project, document['id'], mode='ontology')
    assert missing.status_code == 404
    assert missing.json().get('code') == 'ontology_not_published'
    other = app.state.service.repository.create_project('other')['id']
    assert submit(client, other, document['id']).status_code == 404
    forged = app.state.service.repository.put_record(other, {'id': 'forged', 'kind': 'document', 'text': 'copy',
        'metadata': {'source_document_version_id': document['metadata']['source_document_version_id']}})
    assert submit(client, other, forged['id']).status_code == 404
    same_project = app.state.service.repository.put_record(project, {'id': 'wrong-document', 'kind': 'document', 'text': 'copy',
        'metadata': {'source_document_version_id': document['metadata']['source_document_version_id']}})
    assert submit(client, project, same_project['id']).status_code == 404


def test_legacy_record_without_frozen_source_explains_reupload(env):
    app, client = env
    repo = app.state.service.repository
    project = repo.create_project('legacy')['id']
    document = repo.put_record(project, {'kind': 'document', 'text': '旧正文'})
    response = submit(client, project, document['id'])
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'reprocess_source_missing'
    assert '重新上传' in response.json()['detail']['message']


def test_failed_prior_generation_cannot_be_retried_after_new_reprocess(env):
    app, client = env
    project, document, _, _ = saved_document(app)
    store = app.state.service.repository.ingest_batches
    first = submit(client, project, document['id'], key='first')
    assert first.status_code == 202, first.text
    operation = first.json()
    leased = store.claim(operation['batch']['id'], 'test-worker', 120)
    assert store.finish(leased['id'], 'test-worker', leased['lease_token'], 'failed')
    newer = submit(client, project, document['id'], key='newer')
    assert newer.status_code == 202, newer.text
    with pytest.raises(ValueError, match='更新|取代|superseded'):
        store.retry(project, operation['batch']['id'], leased['id'], 'restart_document')
    assert store.list_items(project, operation['batch']['id'])['items'][0]['status'] == 'failed'


@pytest.mark.parametrize('receipt_status,job_status', [('processing', None), ('ready', 'queued'), ('failed', 'running')])
def test_reprocess_refuses_legacy_processing_receipt_without_batch_item(env, receipt_status, job_status):
    app, client = env
    repo = app.state.service.repository
    project = repo.create_project('legacy active upload')['id']
    source = app.state.source_documents.persist(project, 'legacy-running', b'body', 'body.md')
    metadata = {'status': receipt_status, 'source_document_version_id': source['id']}
    if job_status:
        repo.save_artifact('job', {'id': 'legacy-active-job', 'kind': 'ingest_upload',
            'project_id': project, 'status': job_status})
        metadata['durable_task_id'] = 'legacy-active-job'
    doc = repo.put_record(project, {'id': 'legacy-running', 'kind': 'document', 'text': 'body', 'metadata': metadata})
    response = submit(client, project, doc['id'])
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'document_reprocessing_active'


def test_new_generation_does_not_inherit_previous_execution_candidates_or_durable_job(env):
    app, client = env
    project, document, _, _ = saved_document(app)
    repo = app.state.service.repository
    prior = {key: value for key, value in document.items() if key in ('id', 'kind', 'text', 'metadata', 'valid_from', 'valid_until')}
    prior['metadata'] = {**prior['metadata'], 'discovery_candidates': [{'old': 'candidate'}],
        'review_candidates': [{'old': 'review'}], 'durable_task_id': 'completed-legacy-job',
        'error_code': 'old-error', 'public_error': 'previous failure'}
    repo.put_record(project, prior, expected_version=document['version'])
    response = submit(client, project, document['id'])
    assert response.status_code == 202, response.text
    operation = response.json()
    leased = repo.ingest_batches.claim(operation['batch']['id'], 'test-worker', 120)
    rebuilt = run_item(app, leased, operation['batch']['settings'])['document']
    assert rebuilt['metadata'].get('review_candidates') == []
    assert not rebuilt['metadata'].get('discovery_candidates')
    for key in ('durable_task_id', 'error_code', 'public_error'):
        assert key not in rebuilt['metadata'], key


def test_reprocess_discovery_reexecutes_extraction_per_generation_without_publishing_graph(env, monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor
    app, client = env
    project, document, _, _ = saved_document(app)
    repo = app.state.service.repository
    calls = []
    monkeypatch.setattr(SemanticaExtractor, 'discover', lambda self, text, **kwargs: calls.append(text) or [])
    for key in ('discovery-one', 'discovery-two'):
        response = submit(client, project, document['id'], mode='discovery', key=key)
        assert response.status_code == 202, response.text
        operation = response.json()
        item = repo.ingest_batches.claim(operation['batch']['id'], 'test-worker', 120)
        result = run_item(app, item, operation['batch']['settings'])
        assert result.get('replayed') is not True
        assert repo.ingest_batches.finish(item['id'], 'test-worker', item['lease_token'], 'succeeded', result=result)
        assert not repo.current_records(project, kinds=['entity', 'relation', 'attribute'])
    assert len(calls) == 2, 'old extraction checkpoints must not hide the new generation'
    assert repo.get_record(project, document['id'])['metadata']['document_generation'] == 2


def test_completed_partial_ocr_requires_explicit_reprocess_to_replace_searchable_chunks(env):
    from source_test_images import png
    from knowledge_service.integrations.image_ocr import OCRError
    class Adapter:
        engine = 'test'; languages = 'en'; configuration_fingerprint = 'stable-test'
        calls = 0
        def recognize(self, data):
            self.calls += 1
            text = '第一部分的文字' if self.calls == 1 else '第一部分的文字以及补全的新内容'
            output = dict(engine_version='test', preprocess_fingerprint='test', average_confidence=.99,
                width=16, height=16, blocks=[dict(text=text, normalized_text=text, confidence=.99,
                x=0, y=0, width=1, height=1)])
            if self.calls == 1:
                error = OCRError('ocr_timeout', 'second tile failed')
                error.partial_result = output
                raise error
            return output
    app, client = env
    sources = app.state.source_documents
    adapter = Adapter(); sources.image_ocr.adapter = adapter
    repo = app.state.service.repository
    project = repo.create_project('partial OCR rebuild')['id']
    settings = IngestBatchSettings(extraction_mode='documents', chunk_size=100, chunk_overlap=0).model_dump()
    batch = repo.ingest_batches.create_batch(project, settings)
    raw = b'![image](rule.png)'
    original = repo.ingest_batches.add_item(project, batch['id'], 'rule.md', raw,
        hashlib.sha256(raw).hexdigest(), 'original', 100, 10000)
    source = sources.persist(project, original['document_id'], raw, 'rule.md', assets=[dict(
        kind='local', status='available', original_path='rule.png', data=png())])
    repo.ingest_batches.set_source(project, batch['id'], original['id'], source['id'])
    repo.ingest_batches.transition(project, batch['id'], 'start')
    first = repo.ingest_batches.claim(batch['id'], 'test-worker', 120)
    result = run_item(app, first, settings)
    assert repo.ingest_batches.finish(first['id'], 'test-worker', first['lease_token'], 'succeeded', result=result)
    before = repo.get_record(project, original['document_id'])
    assert '补全的新内容' not in before['text']
    asset_id = source['assets'][0]['id']
    assert sources.image_ocr.process_asset(project, source['id'], asset_id, retry=True)['status'] == 'completed'
    assert repo.get_record(project, original['document_id'])['version'] == before['version']
    response = submit(client, project, original['document_id'])
    assert response.status_code == 202, response.text
    operation = response.json()
    item = repo.ingest_batches.claim(operation['batch']['id'], 'test-worker', 120)
    rebuilt = run_item(app, item, operation['batch']['settings'])
    assert '补全的新内容' in rebuilt['document']['text']
    assert any('补全的新内容' in row['text'] for row in repo.current_records(project, kinds=['chunk']))
    assert adapter.calls == 2, 'successful OCR must be reused without calling the engine again'
    assert len(sources.store.ocr_runs(project, source['id'], asset_id)) == 2


def test_reprocess_api_requires_write_permission(tmp_path):
    from knowledge_service.repository.users_store import UserStore
    from knowledge_service.core.security import hash_password
    app = create_app(tmp_path / 'reprocessing-auth', encoder=HashingEncoder(), auth_disabled=False)
    users = UserStore(app.state.service.repository)
    users.create('reprocessing-viewer', hash_password('viewer-password'), role='viewer')
    client = TestClient(app)
    token = client.post('/api/auth/login', json={'username': 'reprocessing-viewer', 'password': 'viewer-password'}).json()['access_token']
    response = client.post('/api/projects/missing/documents/missing/reprocess',
        json={'extraction_mode': 'documents', 'idempotency_key': 'key'}, headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 403
