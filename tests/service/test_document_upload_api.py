import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.integrations.document_parser import ParsedDocument
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.document_uploads import DocumentUploads


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'service.sqlite', encoder=HashingEncoder(),
                               upload_temp=tmp_path / 'uploads')) as value:
        yield value


def make_project(client):
    response = client.post('/api/projects', json={'name': 'uploads'})
    assert response.status_code == 201
    return response.json()['id']


def test_multipart_preview_parses_without_writing(client):
    project = make_project(client)
    response = client.post(
        f'/api/projects/{project}/documents/upload/preview',
        files={'file': ('rules.md', '# 标题\n规则正文'.encode())},
        data={'options': json.dumps({'extract': False, 'chunk_size': 100, 'chunk_overlap': 10})},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['parsed']['source_format'] == 'md'
    assert body['total'] == 1
    assert body['chunks'][0]['text'] == '# 标题\n规则正文'
    assert client.post(f'/api/projects/{project}/records/query', json={}).json()['records'] == []


def test_multipart_job_ingests_and_removes_staged_file(client):
    project = make_project(client)
    response = client.post(
        f'/api/projects/{project}/documents/upload/jobs',
        files={'file': ('rules.txt', '退款规则'.encode())},
        data={'options': json.dumps({'extract': False, 'metadata': {'source_file': 'spoofed.txt'}})},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()['id']

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] not in ('queued', 'running'):
            break
        time.sleep(.02)
    assert job['status'] == 'completed', job
    records = client.post(f'/api/projects/{project}/records/query', json={}).json()['records']
    document = next(row for row in records if row['kind'] == 'document')
    assert document['metadata']['source_file'] == 'rules.txt'
    assert document['metadata']['document_parser'] == 'builtin-text'
    assert list(client.app.state.document_uploads.root.iterdir()) == []


def test_failed_background_parse_removes_staged_file(client, monkeypatch):
    project = make_project(client)

    def fail(*args, **kwargs):
        raise RuntimeError('simulated parser failure')

    monkeypatch.setattr(client.app.state.document_uploads.parser, 'parse', fail)
    response = client.post(
        f'/api/projects/{project}/documents/upload/jobs',
        files={'file': ('broken.pdf', b'%PDF')},
        data={'options': json.dumps({'extract': False})},
    )
    job_id = response.json()['id']
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] not in ('queued', 'running'):
            break
        time.sleep(.02)
    assert job['status'] == 'failed'
    assert list(client.app.state.document_uploads.root.iterdir()) == []


def test_stale_upload_cleanup_only_removes_old_files(tmp_path):
    uploads = DocumentUploads(tmp_path)
    old = tmp_path / 'old.pdf'
    recent = tmp_path / 'recent.pdf'
    old.write_bytes(b'old')
    recent.write_bytes(b'recent')
    stale_time = time.time() - 25 * 60 * 60
    os.utime(old, (stale_time, stale_time))

    uploads.cleanup_old()

    assert not old.exists()
    assert recent.exists()


def test_docling_upload_uses_adapter_and_preserves_file_title(client, monkeypatch):
    project = make_project(client)

    def fake_parse(path, original_name, **kwargs):
        return ParsedDocument('parsed PDF', {
            'source_file': original_name,
            'source_format': 'pdf',
            'source_size_bytes': Path(path).stat().st_size,
            'source_sha256': 'x' * 64,
            'document_parser': 'semantica-docling',
            'semantica_version': '0.7.0',
            'docling_version': '2.130.0',
            'page_count': 1,
            'table_count': 0,
            'ocr_mode': 'auto',
            'warnings': [],
        })

    monkeypatch.setattr(client.app.state.document_uploads.parser, 'parse', fake_parse)
    response = client.post(
        f'/api/projects/{project}/documents/upload',
        files={'file': ('manual.pdf', b'%PDF')},
        data={'options': json.dumps({'extract': False, 'title': ''})},
    )
    assert response.status_code == 201, response.text
    assert response.json()['document']['text'] == 'parsed PDF'
    assert response.json()['document']['metadata']['source_file'] == 'manual.pdf'


@pytest.mark.parametrize(
    ('filename', 'options', 'code'),
    [
        ('deck.pptx', '{}', 'unsupported_format'),
        ('rules.txt', '[]', 'invalid_options'),
        ('rules.txt', '{broken', 'invalid_options'),
        ('rules.txt', '{"unknown": true}', 'invalid_options'),
    ],
)
def test_upload_errors_have_stable_codes(client, filename, options, code):
    project = make_project(client)
    response = client.post(
        f'/api/projects/{project}/documents/upload/preview',
        files={'file': (filename, b'content')},
        data={'options': options},
    )
    assert response.status_code == 422
    assert response.json()['code'] == code
