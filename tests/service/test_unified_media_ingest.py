"""Media upload behavior: one original file or ZIP always denotes one document."""
import io
import json
import zipfile

import pytest
from PIL import Image

from knowledge_service.integrations.document_parser import DocumentParser, DocumentParseError
from test_document_upload_api import client, make_project


def image_bytes(fmt='PNG', size=(108, 2000)):
    stream = io.BytesIO()
    Image.new('RGB', size, 'white').save(stream, format=fmt)
    return stream.getvalue()


@pytest.mark.parametrize('filename,fmt', [('long.png', 'PNG'), ('photo.jpg', 'JPEG'), ('screen.webp', 'WEBP')])
def test_standalone_image_preserves_original_and_has_no_invented_text(tmp_path, filename, fmt):
    raw = image_bytes(fmt)
    path = tmp_path / filename
    path.write_bytes(raw)
    parsed = DocumentParser().parse(path, filename)
    assert parsed.text == ''
    assert parsed.provenance['media_type'] == 'image'
    assert len(parsed.assets) == 1
    assert parsed.assets[0]['data'] == raw
    assert parsed.assets[0]['width'] == 108
    assert parsed.assets[0]['height'] == 2000


def test_standalone_image_source_preview_does_not_call_ocr(client, monkeypatch):
    project = make_project(client)
    monkeypatch.setattr(client.app.state.source_documents.image_ocr, 'process_asset',
                        lambda *a, **kw: pytest.fail('Upload preview must not run OCR'))
    response = client.post(f'/api/projects/{project}/documents/upload/preview',
                           files={'file': ('long.png', image_bytes())}, data={'preview_view': 'source'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['source_preview']['images'][0]['status'] == 'available'
    assert '<span' in body['source_preview']['html']
    assert body['source_preview']['assets']
    assert list(client.app.state.document_uploads.root.iterdir()) == []


def test_multi_document_zip_is_rejected_before_preview_or_import(client):
    project = make_project(client)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('one.md', '# One')
        archive.writestr('two.md', '# Two')
    response = client.post(f'/api/projects/{project}/documents/upload/preview',
                           files={'file': ('rules.zip', stream.getvalue())})
    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'invalid_package'
    assert client.post(f'/api/projects/{project}/records/page', json={}).json()['records'] == []


def test_ordered_image_zip_preview_is_one_document(client):
    project = make_project(client)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'schema_version': 2, 'document': {
            'title': 'One rule', 'pages': ['assets/second.png', 'assets/first.png']}}))
        archive.writestr('assets/first.png', image_bytes(size=(100, 200)))
        archive.writestr('assets/second.png', image_bytes(size=(120, 300)))
    response = client.post(f'/api/projects/{project}/documents/upload/preview',
                           files={'file': ('one-rule.zip', stream.getvalue())}, data={'preview_view': 'source'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['package']['document_count'] == 1
    assert body['title'] == 'One rule'
    assert len(body['source_preview']['images']) == 2
    assert body['source_preview']['images'][0]['url'].endswith('second.png')


def test_reprocessing_shorter_text_retires_old_tail_chunks_without_erasing_history(client):
    from knowledge_service.models import Ingest
    project=make_project(client)
    service=client.app.state.service
    initial=Ingest(title='Rule',text='The original rule has a long explanation. '*20,
                   extraction_mode='documents',chunk_size=100,chunk_overlap=0).model_dump()
    initial.update(_document_id='same-document',metadata={'document_generation':0})
    first=service.ingest(project,initial)
    old_chunks=[r for r in first['records'] if r['kind']=='chunk']
    assert len(old_chunks)>1
    changed=Ingest(title='Rule',text='The revised short rule.',extraction_mode='documents',
                   chunk_size=100,chunk_overlap=0).model_dump()
    changed.update(_document_id='same-document',metadata={'document_generation':1})
    service.ingest(project,changed)
    active=[r for r in service.repository.current_records(project,kinds=['chunk'])
            if r.get('source_id')=='same-document' and not r['metadata'].get('_deleted')]
    assert len(active)==1
    tail=service.repository.get_record(project,old_chunks[-1]['id'])
    assert tail['metadata']['_deleted'] is True
    assert len(service.repository.history(project,tail['id']))>=2
