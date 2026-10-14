from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def test_fixed_version_source_api_and_cross_project_isolation(tmp_path):
    with TestClient(create_app(tmp_path / 'source-api', encoder=HashingEncoder())) as client:
        project = client.post('/api/projects', json={'name': 'source-api'}).json()['id']
        other = client.post('/api/projects', json={'name': 'other'}).json()['id']
        sources = client.app.state.source_documents
        raw = b'# Rules\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script>\n'
        fixed = sources.persist(project, 'doc-source', raw, 'rules.md')
        path = f'/api/projects/{project}/source-documents/{fixed["id"]}'
        metadata = client.get(path)
        assert metadata.status_code == 200
        assert 'storage_key' not in metadata.json()
        rendered = client.get(path + '/rendered')
        assert '<table' in rendered.text and '<script>' not in rendered.text
        assert 'data-block-id' in rendered.text
        source = client.get(path + '/source')
        assert source.content == raw
        assert 'attachment' in source.headers['content-disposition']
        assert client.get(f'/api/projects/{other}/source-documents/{fixed["id"]}').status_code == 404
        unmapped = client.get(path + '/locate?chunk_version_id=missing').json()
        assert unmapped.get('code') == 'source_mapping_unavailable' or unmapped['status'] == 'mapping_unavailable'
