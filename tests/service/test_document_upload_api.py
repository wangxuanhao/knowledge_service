import json
import base64
import io
import os
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.integrations.document_parser import ParsedDocument
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.document_uploads import DocumentUploads
from source_test_images import png


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
        data={'options': json.dumps({'extraction_mode': 'documents', 'chunk_size': 100, 'chunk_overlap': 10})},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['parsed']['source_format'] == 'md'
    assert body['total'] == 1
    assert body['chunks'][0]['text'] == '# 标题\n规则正文'
    assert client.post(f'/api/projects/{project}/records/page', json={}).json()['records'] == []


def markdown_package(selected=0):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('platform/first.md',
                         '# 首份规则\n\n发布日期：2026年9月1日\n\n生效日期：2026年9月2日\n\n'
                         '<table><tr><td>示例</td><td><img src="assets/example.png"></td></tr></table>\n\n'
                         '![同图](assets/example.png)')
        archive.writestr('platform/second.md',
                         '# 第二份规则\n\n第二份独立正文\n\n![缺图](assets/missing.png)\n\n'
                         '![外链](https://example.invalid/image.png)\n\n'
                         '<script>alert(1)</script><p onclick="alert(2)">安全正文</p>')
        archive.writestr('platform/assets/example.png', png())
        choices = [
            {'path': 'platform/first.md', 'title': '首份规则', 'metadata': {'rule_id': 'first'}},
            {'path': 'platform/second.md', 'title': '第二份规则'},
        ]
        archive.writestr('manifest.json', json.dumps({'documents': [choices[selected]]}, ensure_ascii=False))
    return buffer.getvalue()


def test_zip_preview_reads_one_document_and_its_images_without_writing(client, monkeypatch):
    project = make_project(client)
    sources = client.app.state.source_documents
    monkeypatch.setattr(sources.object_store, 'put', lambda *a, **k: pytest.fail('preview wrote source bytes'))
    monkeypatch.setattr(sources.image_ocr, 'process_asset', lambda *a, **k: pytest.fail('preview called OCR'))
    raw = markdown_package()
    url = f'/api/projects/{project}/documents/upload/preview'
    options = json.dumps({'document_time_mode': 'auto', 'chunk_size': 100, 'chunk_overlap': 10})

    first = client.post(url, files={'file': ('rules.zip', raw)}, data={'options': options})

    assert first.status_code == 200, first.text
    body = first.json()
    assert body['package']['selected_path'] == 'platform/first.md'
    assert [row['path'] for row in body['package']['documents']] == ['platform/first.md']
    assert body['package']['document_count'] == 1
    assert body['package']['image_count'] == 2
    assert body['title'] == '首份规则'
    assert body['metadata']['rule_id'] == 'first'
    assert body['document_time']['valid_from'] == '2026-09-01T16:00:00.000000Z'
    assert '<table' in body['source_preview']['html']
    image = body['source_preview']['images'][0]
    assert image['status'] == 'available'
    asset = body['source_preview']['assets'][image['asset_key']]
    assert asset['mime_type'] == 'image/png'
    assert base64.b64decode(asset['base64']) == png()
    assert len(body['source_preview']['images']) == 2
    assert len(body['source_preview']['assets']) == 1
    assert body['total'] >= 1
    assert '第二份独立正文' not in ''.join(row['text'] for row in body['chunks'])

    second = client.post(url, files={'file': ('second-rule.zip', markdown_package(1))},
                         data={'options': options, 'package_path': 'platform/second.md'})

    assert second.status_code == 200, second.text
    body = second.json()
    assert body['package']['selected_path'] == 'platform/second.md'
    assert '第二份独立正文' in ''.join(row['text'] for row in body['chunks'])
    assert [image['status'] for image in body['source_preview']['images']] == ['missing', 'external']
    assert body['source_preview']['assets'] == {}
    assert '<script' not in body['source_preview']['html']
    assert 'onclick=' not in body['source_preview']['html']
    assert any(warning['code'] == 'source_asset_missing' for warning in body['warnings'])
    assert client.post(f'/api/projects/{project}/records/page', json={}).json()['records'] == []
    assert list(client.app.state.document_uploads.root.iterdir()) == []


@pytest.mark.parametrize('case', ['malformed', 'unsafe', 'unknown-document', 'disabled', 'file-limit'])
def test_zip_preview_rejects_invalid_or_disabled_packages_and_cleans_staging(client, monkeypatch, case):
    project = make_project(client)
    raw, data = markdown_package(), {}
    if case == 'malformed':
        raw = b'not a zip archive'
    elif case == 'unsafe':
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('../outside.md', '# unsafe')
        raw = buffer.getvalue()
    elif case == 'unknown-document':
        data['package_path'] = 'other.md'
    elif case == 'disabled':
        monkeypatch.setattr(client.app.state.ingest_batches.limits, 'markdown_package', False)
    else:
        monkeypatch.setattr(client.app.state.ingest_batches.limits, 'max_file_bytes', 100)
    response = client.post(f'/api/projects/{project}/documents/upload/preview',
                           files={'file': ('rules.zip', raw)}, data=data)
    assert response.status_code == 422, response.text
    assert response.json()['message']
    assert response.json()['code'] == {
        'malformed': 'invalid_package', 'unsafe': 'invalid_package',
        'unknown-document': 'package_document_not_found', 'disabled': 'unsupported_format',
        'file-limit': 'file_too_large',
    }[case]
    assert list(client.app.state.document_uploads.root.iterdir()) == []


def test_zip_preview_rejects_mismatched_inline_mime_before_source_publication(client):
    project = make_project(client)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('a.md', '![valid](assets/a.png)\n\n'
                         + '![mismatched](data:image/jpeg;base64,' + base64.b64encode(png()).decode() + ')')
        archive.writestr('assets/a.png', png())
    response = client.post(f'/api/projects/{project}/documents/upload/preview',
                           files={'file': ('rules.zip', buffer.getvalue())})
    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'invalid_package'
    assert list(client.app.state.document_uploads.root.iterdir()) == []


@pytest.mark.parametrize('filename', ['a.md', 'rules.zip'])
def test_source_and_chunk_preview_responses_are_separate(client, filename):
    project = make_project(client)
    raw = markdown_package() if filename.endswith('.zip') else '# 原文标题\n\n完整原文\n\n![外链](https://example.invalid/image.png)'.encode()
    url = f'/api/projects/{project}/documents/upload/preview'
    chunks = client.post(url, files={'file': (filename, raw)}, data={'preview_view': 'chunks'})
    assert chunks.status_code == 200, chunks.text
    assert chunks.json()['chunks']
    assert 'source_preview' not in chunks.json()
    original = client.post(url, files={'file': (filename, raw)}, data={'preview_view': 'source'})
    assert original.status_code == 200, original.text
    assert original.json()['source_preview']['html']
    assert 'chunks' not in original.json()
    assert list(client.app.state.document_uploads.root.iterdir()) == []


@pytest.mark.parametrize('text', ['![example](data:image/png;base64,@@)',
                                '<img src="data:image/svg+xml;base64,PHN2Zy8+">'])
def test_txt_source_preview_preserves_literal_image_syntax(client, text):
    from html import escape
    project = make_project(client)
    url = f'/api/projects/{project}/documents/upload/preview'
    response = client.post(url, files={'file': ('literal.txt', text.encode())}, data={'preview_view': 'source'})
    assert response.status_code == 200, response.text
    assert response.json()['source_preview']['html'] == '<pre>' + escape(text) + '</pre>'
    assert response.json()['source_preview']['images'] == []
    legacy = client.post(url, files={'file': ('literal.txt', text.encode())})
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()['chunks'][0]['text'] == text


def test_legacy_markdown_preview_retains_plain_chunk_behavior_for_literal_broken_image_markup(client):
    project = make_project(client)
    text = '![example](data:image/png;base64,@@)'
    response = client.post(f'/api/projects/{project}/documents/upload/preview',
                           files={'file': ('literal.md', text.encode())})
    assert response.status_code == 200, response.text
    assert response.json()['chunks'][0]['text'] == text


def test_multipart_job_ingests_and_removes_staged_file(client):
    project = make_project(client)
    response = client.post(
        f'/api/projects/{project}/documents/upload/jobs',
        files={'file': ('rules.txt', '退款规则'.encode())},
        data={'options': json.dumps({'extraction_mode': 'documents', 'metadata': {'source_file': 'spoofed.txt'}})},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()['id']

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] not in ('queued', 'running'):
            break
        time.sleep(.02)
    assert job['status'] == 'completed', job
    records = client.post(f'/api/projects/{project}/records/page', json={}).json()['records']
    document = next(row for row in records if row['kind'] == 'document')
    assert document['metadata']['source_file'] == 'rules.txt'
    assert document['metadata']['document_parser'] == 'builtin-text'
    assert list(client.app.state.document_uploads.root.iterdir()) == []


def test_failed_background_parse_retains_file_and_can_resume(client, monkeypatch):
    project = make_project(client)
    original = client.app.state.document_uploads.parser.parse

    def fail(*args, **kwargs):
        raise RuntimeError('simulated parser failure')

    monkeypatch.setattr(client.app.state.document_uploads.parser, 'parse', fail)
    response = client.post(
        f'/api/projects/{project}/documents/upload/jobs',
        files={'file': ('broken.txt', b'original source')},
        data={'options': json.dumps({'extraction_mode': 'documents'})},
    )
    job_id = response.json()['id']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] not in ('queued', 'running'):
            break
        time.sleep(.02)
    assert job['status'] == 'failed'
    assert job['error']
    staged = list(client.app.state.document_uploads.root.iterdir())
    assert len(staged) == 1 and staged[0].read_bytes() == b'original source'
    monkeypatch.setattr(client.app.state.document_uploads.parser, 'parse', original)
    resumed = client.post(f'/api/jobs/{job_id}/resume')
    assert resumed.status_code == 202, resumed.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] not in ('queued', 'running'):
            break
        time.sleep(.02)
    assert job['status'] == 'completed', job
    assert job['attempts'] == 2
    assert not staged[0].exists()


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
        data={'options': json.dumps({'extraction_mode': 'documents', 'title': ''})},
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
