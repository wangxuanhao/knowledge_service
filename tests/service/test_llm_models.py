"""Platform model secrets, durable selections, authorization and provider boundary."""
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from knowledge_service.repository.core import Repository


@pytest.fixture
def models(tmp_path, monkeypatch):
    monkeypatch.delenv('KG_LLM_ENCRYPTION_KEY', raising=False)
    monkeypatch.setenv('KG_LLM_KEY_FILE', str(tmp_path / 'llm.key'))
    for name in ('BASE_URL', 'MODEL', 'API_KEY', 'PROXY', 'MAX_TOKENS'):
        monkeypatch.delenv('KG_LLM_' + name, raising=False)
    from knowledge_service.services.llm_models import LLMModels
    return LLMModels(Repository(tmp_path / 'models'))


def profile(models, name='one', secret='secret-one'):
    return models.create(name=name, base_url='https://provider.example/v1/',
                         model=name, api_key=secret)


def test_encrypted_persistence_keep_key_reopen_and_default(models, tmp_path):
    from knowledge_service.services.llm_models import LLMModels
    first, second = profile(models), profile(models, 'two', 'secret-two')
    assert not first['is_active']
    assert 'secret-one' not in json.dumps(models.list())
    rows = models.repository._db.execute('SELECT * FROM llm_models').fetchall()
    assert 'secret-one' not in str([dict(row) for row in rows])
    models.activate(first['id'])
    models.update(first['id'], name='renamed', api_key='')
    reopened = LLMModels(Repository(tmp_path / 'models'))
    assert reopened.resolve().api_key == 'secret-one'
    assert reopened.resolve().name == 'renamed'
    reopened.activate(second['id'])
    assert models.resolve().profile_id == second['id']
    assert sum(p['is_active'] for p in models.list()['items']) == 1
    assert set(first) == {'id', 'name', 'base_url', 'model', 'has_api_key', 'is_active'}


def test_snapshot_survives_switch_edit_and_reopen(models, tmp_path):
    from knowledge_service.services.llm_models import LLMModels
    first = profile(models)
    models.activate(first['id'])
    pinned = models.pin('job:123')
    sealed = models.seal(pinned)
    assert 'secret-one' not in sealed
    models.update(first['id'], api_key='replacement', model='new')
    models.activate(profile(models, 'two')['id'])
    reopened = LLMModels(Repository(tmp_path / 'models'))
    assert reopened.pin('job:123') == pinned == reopened.unseal(sealed)
    assert 'secret-one' not in json.dumps(pinned.public_identity())


@pytest.mark.parametrize('url', ['ftp://host/v1', 'https://user:pass@host/v1',
                               'https://host/v1?key=secret', 'https://host/#secret',
                               'https:///v1', 'https://host/\nheader'])
def test_unsafe_base_urls_rejected(models, url):
    with pytest.raises(ValueError):
        models.create(name='bad', base_url=url, model='m', api_key='key')


def test_environment_and_context_isolation(models, monkeypatch):
    from knowledge_service.core.llm_config import bound_llm, current_llm
    assert not models.resolve().configured
    monkeypatch.setenv('KG_LLM_BASE_URL', 'https://env.example/v1')
    monkeypatch.setenv('KG_LLM_MODEL', 'env-model')
    monkeypatch.setenv('KG_LLM_API_KEY', 'env-secret')
    env = models.resolve()
    assert env.profile_id == 'environment' and env.configured
    item = profile(models)
    models.activate(item['id'])
    platform = models.resolve()
    def use(config):
        with bound_llm(config):
            return current_llm()
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(use, [env, platform])) == [env, platform]
    assert current_llm() == env


def test_invalid_or_lost_key_fails_closed(models, monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from knowledge_service.services.llm_models import LLMModels, LLMKeyError
    item = profile(models)
    models.activate(item['id'])
    monkeypatch.setenv('KG_LLM_ENCRYPTION_KEY', Fernet.generate_key().decode())
    with pytest.raises(LLMKeyError):
        LLMModels(models.repository).resolve()
    monkeypatch.delenv('KG_LLM_ENCRYPTION_KEY')
    (tmp_path / 'llm.key').unlink()
    with pytest.raises(LLMKeyError):
        LLMModels(models.repository).resolve()


def test_connection_posts_chat_and_filters_errors(models, monkeypatch):
    from knowledge_service.services import llm_models
    item = profile(models)
    def reply(request):
        assert str(request.url) == 'https://provider.example/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer secret-one'
        assert json.loads(request.content)['model'] == 'one'
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
    monkeypatch.setattr(llm_models, 'external_client',
                        lambda **kw: httpx.Client(transport=httpx.MockTransport(reply)))
    assert models.test_connection(item['id'])['ok'] is True
    monkeypatch.setattr(llm_models, 'external_client', lambda **kw: httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(401, text='secret-one'))))
    result = models.test_connection(item['id'])
    assert result['ok'] is False and 'secret-one' not in json.dumps(result)


def test_management_routes_superadmin_only(tmp_path, monkeypatch):
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    monkeypatch.setenv('KG_LLM_KEY_FILE', str(tmp_path / 'api.key'))
    with TestClient(create_app(str(tmp_path / 'api'), encoder=HashingEncoder(),
                               auth_disabled=False)) as client:
        def login(name, password):
            token = client.post('/api/auth/login', json={
                'username': name, 'password': password}).json()['access_token']
            return {'Authorization': 'Bearer ' + token}
        superadmin = login('admin', 'admin123')
        assert client.get('/api/llm-models', headers=superadmin).status_code == 200
        created = client.post('/api/llm-models', headers=superadmin, json={
            'name': 'model', 'base_url': 'http://localhost:9000/v1',
            'model': 'test', 'api_key': 'api-secret'})
        assert created.status_code == 201
        ident = created.json()['id']
        invalid = client.patch('/api/llm-models/' + ident, headers=superadmin,
                               json={'api_key': 'private-key-marker' * 1000})
        assert invalid.status_code == 422
        assert 'private-key-marker' not in invalid.text
        for role in ('admin', 'viewer'):
            client.post('/api/users', headers=superadmin, json={
                'username': role + '-test', 'password': 'pass123', 'role': role})
            headers = login(role + '-test', 'pass123')
            for method, path, body in [
                ('GET', '', None), ('POST', '', {}),
                ('PATCH', '/' + ident, {}), ('POST', '/' + ident + '/activate', None),
                ('POST', '/' + ident + '/test', None)]:
                assert client.request(method, '/api/llm-models' + path,
                                      headers=headers, json=body).status_code == 403


def test_concurrent_initialization_and_activation_share_one_key(models, tmp_path):
    from knowledge_service.services.llm_models import LLMModels
    other = LLMModels(Repository(tmp_path / 'models'))
    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(lambda manager: profile(manager), [models, other]))
        list(pool.map(lambda args: args[0].activate(args[1]),
                      [(models, first['id']), (other, second['id'])]))
    assert models.resolve().api_key == 'secret-one'
    assert sum(item['is_active'] for item in models.list()['items']) == 1


def test_platform_profile_ignores_unused_invalid_environment_url(models, monkeypatch):
    item = profile(models)
    models.activate(item['id'])
    monkeypatch.setenv('KG_LLM_BASE_URL', 'https://user:secret@host/v1')
    assert models.resolve().model == 'one'
    assert models.list()['active']['id'] == item['id']


def test_snapshot_only_key_loss_never_regenerates(models, tmp_path):
    from knowledge_service.services.llm_models import LLMModels, LLMKeyError
    models.pin('unconfigured-job')
    (tmp_path / 'llm.key').unlink()
    with pytest.raises(LLMKeyError):
        LLMModels(models.repository).pin('unconfigured-job')
    assert not (tmp_path / 'llm.key').exists()


def test_explicit_auth_disabled_keeps_model_management_available(tmp_path):
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    app = create_app(tmp_path / 'auth-disabled', encoder=HashingEncoder(), auth_disabled=True)
    with TestClient(app) as client:
        assert client.get('/api/llm-models').status_code == 200


def test_empty_encryption_key_uses_persistent_file(models, monkeypatch, tmp_path):
    monkeypatch.setenv('KG_LLM_ENCRYPTION_KEY', '')
    item = profile(models)
    models.activate(item['id'])
    assert (tmp_path / 'llm.key').is_file()
    assert models.resolve().api_key == 'secret-one'
