from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def test_authenticated_draft_ignores_forged_actor_and_identity(tmp_path):
    with TestClient(create_app(tmp_path / 'audit', encoder=HashingEncoder(),
                               auth_disabled=False)) as client:
        login = client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'admin123'}).json()
        headers = {'Authorization': 'Bearer ' + login['access_token']}
        project = client.post('/api/projects', json={'name': 'audit',
                              'use_default_ontology': False, 'ontology_mode': 'discovery'},
                              headers=headers).json()
        response = client.post(f"/api/projects/{project['id']}/ontology-drafts",
            headers=headers, json={'title': 'identity test', 'actor': 'forged',
                'source_context': {'actor': 'forged',
                                   'audit_identity': {'id': 'forged'}}})
        assert response.status_code == 201, response.text
        draft = response.json()
        identity = draft['source_context']['audit_identity']
        assert identity['id'] == login['user']['id']
        assert identity['username'] == 'admin'
        actor = draft['source_context']['actor']
        assert 'forged' not in actor
        assert 'user:' + login['user']['id'] in actor
        closed = client.post(
            f"/api/projects/{project['id']}/ontology-drafts/{draft['id']}/close",
            headers=headers, json={'expected_revision': draft['revision'],
                                   'reason': 'close', 'actor': 'forged'})
        assert closed.status_code == 200, closed.text
        assert 'forged' not in closed.text


def test_actor_is_absent_from_ontology_http_schema(tmp_path):
    with TestClient(create_app(tmp_path / 'audit-schema',
                               encoder=HashingEncoder(), auth_disabled=True)) as client:
        schemas = client.get('/openapi.json').json()['components']['schemas']
        for name in ('DraftCreate', 'DecisionsRequest', 'CloseRequest',
                     'BatchApproveRequest', 'PublishRequest'):
            assert 'actor' not in schemas[name]['properties']
