from ontology_seed import seed_ontology
import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


TTL = '''
@prefix ex: <https://test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Person a owl:Class .
ex:knows a owl:ObjectProperty .
'''


@pytest.mark.parametrize('record_id', ['a', 'rel'])
@pytest.mark.parametrize('binding', ['omitted', 'null'])
def test_plain_revision_preserves_binding_and_all_views_agree(tmp_path, record_id, binding):
    app = create_app(tmp_path / 'revision.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project = client.post('/api/projects', json={
            'name': 'revision', 'use_default_ontology': False}).json()['id']
        base = f'/api/projects/{project}'
        repo = app.state.service.repository
        first = seed_ontology(repo, project, TTL, {})
        app.state.service.write(project, [
            {'id': 'a', 'kind': 'entity', 'type': 'https://test/Person', 'text': 'A'},
            {'id': 'b', 'kind': 'entity', 'type': 'https://test/Person', 'text': 'B'},
            {'id': 'rel', 'kind': 'relation', 'type': 'https://test/knows', 'text': 'A knows B',
             'subject_id': 'a', 'object_id': 'b'},
        ])
        second = seed_ontology(repo, project, TTL, {})
        row = repo.history(project, record_id)[-1]
        record = {key: row[key] for key in ('id', 'kind', 'type', 'text', 'metadata')}
        if row['kind'] == 'relation':
            record.update(subject_id='a', object_id='b')
        record['text'] = 'Edited'
        if binding == 'null':
            record['ontology_id'] = None
        response = client.put(base + '/records/' + record_id,
                              json={'record': record, 'expected_version': 1})
        assert response.status_code == 200, response.text
        assert response.json()['ontology_id'] == first['id']
        assert response.json()['version'] == 2
        assert response.json()['version_id'] != row['version_id']
        history = client.get(base + '/records/' + record_id + '/history').json()['versions']
        assert [item['ontology_id'] for item in history] == [first['id'], first['id']]
        ledger = client.post(base + '/records/page', json={}).json()['records']
        graph = client.post(base + '/subgraph', json={}).json()
        for rows in (ledger, graph['nodes'] + graph['edges']):
            visible = next(item for item in rows if item['id'] == record_id)
            assert (visible['version'], visible['ontology_id']) == (2, first['id'])
        # The original expected revision cannot overwrite the saved revision.
        conflict = client.put(base + '/records/' + record_id,
                              json={'record': record, 'expected_version': 1})
        assert conflict.status_code == 409, conflict.text
        # Explicitly changing the binding remains available to existing governance clients.
        migrated = client.put(base + '/records/' + record_id, json={
            'record': {**record, 'ontology_id': second['id']}, 'expected_version': 2})
        assert migrated.status_code == 200, migrated.text
        assert (migrated.json()['version'], migrated.json()['ontology_id']) == (3, second['id'])
