"""PDF preview must contain real original-page pixels, including ZIP primaries."""
import base64
import hashlib
import io
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def pdf_bytes():
    output = io.BytesIO()
    Image.new('RGB', (300, 400), 'red').save(
        output, 'PDF', save_all=True,
        append_images=[Image.new('RGB', (300, 400), 'blue')], resolution=72)
    return output.getvalue()


@pytest.fixture
def preview_source(tmp_path):
    with TestClient(create_app(tmp_path / 'pdf-preview', encoder=HashingEncoder())) as client:
        project = client.post('/api/projects', json={'name': 'pdf-preview'}).json()['id']
        sources = client.app.state.source_documents
        source = sources.persist(project, 'pdf', pdf_bytes(), 'original.pdf', text='Parsed PDF text')
        yield client, project, source


def test_pdf_preview_renders_requested_original_page_and_preserves_source(preview_source):
    client, project, source = preview_source
    path = f'/api/projects/{project}/source-documents/{source["id"]}'
    before = client.get(path).json()
    response = client.get(path + '/pdf-preview?page=2')
    assert response.status_code == 200, response.text
    page = response.json()
    assert (page['page_number'], page['page_count'], page['mime_type']) == (2, 2, 'image/png')
    assert (page['page_width'], page['page_height']) == (300, 400)
    image = Image.open(io.BytesIO(base64.b64decode(page['image_base64'], validate=True)))
    assert image.format == 'PNG' and image.size == (page['width'], page['height'])
    assert max(image.size) <= 1800 and image.width * image.height <= 3_000_000
    red, green, blue = image.convert('RGB').getpixel((image.width // 2, image.height // 2))
    assert blue > 200 and red < 40 and green < 40, 'must render page two, not parsed text or page one'
    assert client.get(path).json() == before
    assert client.get(path + '/source').content.startswith(b'%PDF-')


@pytest.mark.parametrize('page', ['0', '-1', '3', '10001', '1.5'])
def test_pdf_preview_rejects_invalid_or_missing_pages(preview_source, page):
    client, project, source = preview_source
    response = client.get(f'/api/projects/{project}/source-documents/{source["id"]}/pdf-preview?page={page}')
    assert response.status_code == 422, response.text


def test_pdf_preview_enforces_project_ownership_before_render(preview_source):
    client, _, source = preview_source
    other = client.post('/api/projects', json={'name': 'other'}).json()['id']
    response = client.get(f'/api/projects/{other}/source-documents/{source["id"]}/pdf-preview?page=1')
    assert response.status_code == 404


def test_zip_pdf_preview_uses_verified_primary_and_retains_package(preview_source):
    client, project, _ = preview_source
    raw = pdf_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('nested/original.pdf', raw)
    sources = client.app.state.source_documents
    source = sources.persist(project, 'zip-pdf', output.getvalue(), 'bundle.zip', text='PDF package',
        provenance={'package': {'entry_path': 'nested/original.pdf', 'filename': 'original.pdf',
            'mime_type': 'application/pdf', 'sha256': hashlib.sha256(raw).hexdigest()}})
    path = f'/api/projects/{project}/source-documents/{source["id"]}'
    response = client.get(path + '/pdf-preview?page=2')
    assert response.status_code == 200, response.text
    assert response.json()['page_count'] == 2
    assert client.get(path + '/source').content == raw
    assert client.get(path + '/package').content == output.getvalue()


def test_pdf_preview_limits_bytes_and_isolates_timeout(monkeypatch):
    from knowledge_service.integrations import pdf_preview
    with pytest.raises(pdf_preview.PDFPreviewError) as too_large:
        pdf_preview.render_pdf_page(b'%PDF-' + b' ' * (25 * 1024 * 1024), 1)
    assert too_large.value.code == 'pdf_too_large'
    calls = []

    def stalled(command, **options):
        calls.append((command, options))
        raise subprocess.TimeoutExpired(command, options['timeout'])

    monkeypatch.setattr(pdf_preview.subprocess, 'run', stalled)
    with pytest.raises(pdf_preview.PDFPreviewError) as timeout:
        pdf_preview.render_pdf_page(pdf_bytes(), 1)
    assert timeout.value.code == 'pdf_preview_timeout'
    command, options = calls[0]
    assert '-I' in command and options['timeout'] <= 15
    assert options['input'].startswith(b'%PDF-') and options['stdout'] == subprocess.PIPE


def test_non_pdf_preview_is_rejected_and_missing_object_is_unavailable(preview_source):
    client, project, source = preview_source
    sources = client.app.state.source_documents
    markdown = sources.persist(project, 'markdown', b'# Not PDF', 'notes.md')
    assert client.get(f'/api/projects/{project}/source-documents/{markdown["id"]}/pdf-preview').status_code == 415
    sources.object_store.root.joinpath(source['storage_key']).unlink()
    assert client.get(f'/api/projects/{project}/source-documents/{source["id"]}/pdf-preview').status_code == 503
