"""Application data operations preserve the HTTP payloads they support."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from knowledge_service.api.search_indexes import install
from knowledge_service.repository import Repository
from knowledge_service.services.api_workflows import ApplicationData


def active_cursor(**changes):
    return {'status': 'ready', 'schema_version': 3, 'applied_seq': 5,
            'active_destination': 'generation-1', 'last_error': None,
            'rebuild_state': {'index_version': 1, 'phase': 'ready'}, **changes}


def index_app(cursor, watermark=5, *, projection_sync=SimpleNamespace(schema_version=3)):
    calls = []

    def get_project(project):
        calls.append(('get_project', project))
        if project != 'selected':
            raise KeyError(project)
        return {'id': project}

    def read_cursor(project, target):
        calls.append(('cursor', project, target))
        return cursor

    def read_watermark(project):
        calls.append(('watermark', project))
        return watermark

    def enqueue(kind, payload, project):
        calls.append(('enqueue', kind, payload, project))
        return {'id': 'queued-job', 'project_id': project, 'kind': kind}

    repository = SimpleNamespace(get_project=get_project, projections=SimpleNamespace(
        cursor=read_cursor, project_watermark=read_watermark))
    service = SimpleNamespace(data=ApplicationData(repository), projection_sync=projection_sync)
    app = FastAPI()
    app.state.jobs = SimpleNamespace(enqueue=enqueue)
    install(app, service)
    return app, calls


@pytest.mark.parametrize(('cursor', 'state', 'last_error', 'collection'), [
    (None, 'not_built', None, None),
    (active_cursor(), 'ready', None, 'generation-1'),
    (active_cursor(applied_seq=4), 'building', None, 'generation-1'),
    (active_cursor(claim_token='live', claim_until='2999-01-01T00:00:00Z'),
     'ready', None, 'generation-1'),
    (active_cursor(applied_seq=4, claim_token='live', claim_until='2999-01-01T00:00:00Z'),
     'building', None, 'generation-1'),
    (active_cursor(status='failed', last_error='delivery failed'),
     'requires_rebuild', 'delivery failed', 'generation-1'),
    (active_cursor(schema_version=2), 'requires_rebuild', None, 'generation-1'),
])
def test_index_status_preserves_delivery_payload(cursor, state, last_error, collection):
    app, calls = index_app(cursor)
    with TestClient(app) as client:
        response = client.get('/api/projects/selected/indexes/status')
    assert response.status_code == 200, response.text
    assert response.json() == {'project_id': 'selected', 'state': state,
                               'last_error': last_error, 'collection': collection}
    assert calls == [('get_project', 'selected'), ('cursor', 'selected', 'milvus'),
                     ('watermark', 'selected')]


def test_index_rebuild_preserves_selected_project_job():
    app, calls = index_app(None)
    with TestClient(app) as client:
        response = client.post('/api/projects/selected/indexes/rebuild')
    assert response.status_code == 202, response.text
    assert response.json() == {'id': 'queued-job', 'project_id': 'selected',
                               'kind': 'semantic_index'}
    assert calls == [('get_project', 'selected'),
                     ('enqueue', 'semantic_index', {'rebuild': True}, 'selected')]


@pytest.mark.parametrize(('method', 'endpoint'), [('get', 'status'), ('post', 'rebuild')])
def test_indexes_validate_project_before_reading_runtime_or_enqueuing(method, endpoint):
    app, calls = index_app(None, projection_sync=None)
    with TestClient(app) as client, pytest.raises(KeyError, match='missing'):
        getattr(client, method)(f'/api/projects/missing/indexes/{endpoint}')
    assert calls == [('get_project', 'missing')]


def test_metadata_groups_preserve_project_scope_and_group_weights(tmp_path):
    repo = Repository(tmp_path / 'facade-metadata')
    project = repo.create_project('selected')['id']
    other = repo.create_project('other')['id']
    for record_id, region in [('a', 'east'), ('b', 'east'), ('c', 'west')]:
        repo.put_record(project, {'id': record_id, 'kind': 'entity', 'text': record_id,
            'metadata': {'region': region}, 'valid_from': None, 'valid_until': None})
    repo.put_record(other, {'id': 'outside', 'kind': 'entity', 'text': 'outside',
        'metadata': {'region': 'other'}, 'valid_from': None, 'valid_until': None})
    groups = ApplicationData(repo).metadata_groups(project, {
        'filters': {'field': 'metadata.region', 'op': 'eq', 'value': 'absent'}})
    assert list(groups) == [({'region': 'east'}, 2), ({'region': 'west'}, 1)]
