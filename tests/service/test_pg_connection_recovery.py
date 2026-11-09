from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from knowledge_service.repository import Repository


def test_idle_connection_recovers_and_reapplies_session_settings(tmp_path):
    repository = Repository(tmp_path / 'recover-idle.sqlite')
    project = repository.create_project('connection recovery')
    settings = repository._db.execute('SHOW statement_timeout').fetchone()[0]
    old_connection = repository._db.raw
    old_connection.close()

    assert repository.get_project(project['id'])['name'] == 'connection recovery'
    assert repository._db.raw is not old_connection
    assert repository._db.execute('SHOW statement_timeout').fetchone()[0] == settings
    assert repository.create_project('after recovery')['name'] == 'after recovery'


def test_concurrent_idle_operations_share_one_replacement(tmp_path, monkeypatch):
    repository = Repository(tmp_path / 'recover-concurrent.sqlite')
    repository._db.raw.close()
    connect = psycopg.connect
    replacements = []

    def observe_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        replacements.append(connection)
        return connection

    monkeypatch.setattr(psycopg, 'connect', observe_connect)
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(
            lambda _: repository._db.execute('SELECT 1').fetchone()[0], range(8)))
    assert values == [1] * 8
    assert len(replacements) == 1


def test_disconnect_inside_transaction_does_not_continue_writes(tmp_path):
    repository = Repository(tmp_path / 'recover-transaction.sqlite')
    with pytest.raises(psycopg.OperationalError):
        with repository.unit_of_work():
            repository.create_project('rolled back')
            repository._db.raw.close()
            repository.create_project('must not commit separately')

    assert repository.list_projects() == []
    assert repository.create_project('new operation')['name'] == 'new operation'


def test_disconnect_before_commit_cannot_report_success(tmp_path):
    repository = Repository(tmp_path / 'recover-commit.sqlite')
    with pytest.raises(psycopg.OperationalError):
        with repository.unit_of_work():
            repository.create_project('lost transaction')
            repository._db.raw.close()

    assert repository.list_projects() == []


def test_explicit_close_does_not_reopen_repository(tmp_path):
    repository = Repository(tmp_path / 'recover-explicit-close.sqlite')
    repository.close()
    for operation in [lambda: repository._db.execute('SELECT 1'),
                      repository._db.commit, repository._db.rollback]:
        with pytest.raises(psycopg.InterfaceError, match='closed'):
            operation()


def test_borrowed_snapshot_failure_keeps_its_physical_connection(tmp_path):
    repository = Repository(tmp_path / 'recover-reader.sqlite')
    with pytest.raises(psycopg.OperationalError):
        with repository._db.independent_read_snapshot() as reader:
            physical = reader.raw
            physical.close()
            reader.execute('SELECT 1')
    assert reader.raw is physical and physical.closed
    assert repository.create_project('writer still usable')['name'] == 'writer still usable'


def test_server_terminated_connection_recovers_on_the_next_operation(tmp_path):
    repository = Repository(tmp_path / 'recover-server-close.sqlite')
    project = repository.create_project('server disconnect')
    physical = repository._db.raw
    with psycopg.connect(repository._db._dsn, autocommit=True) as supervisor:
        assert supervisor.execute('SELECT pg_terminate_backend(%s)',
                                  (physical.info.backend_pid,)).fetchone()[0]
    with pytest.raises(psycopg.OperationalError):
        repository._db.execute('SELECT 1')
    assert repository.get_project(project['id'])['name'] == 'server disconnect'
    assert repository._db.raw is not physical


@pytest.mark.parametrize('error', [psycopg.OperationalError, psycopg.InterfaceError])
def test_graph_connection_failure_is_json_503_without_connection_details(tmp_path, monkeypatch, error):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.services.graph_sessions import GraphSessions

    app = create_app(tmp_path / 'connection-api', encoder=HashingEncoder(), auth_disabled=True)
    project = app.state.service.repository.create_project('connection API')['id']

    def unavailable(*args, **kwargs):
        raise error('password=must-not-leak')

    monkeypatch.setattr(GraphSessions, 'create', unavailable)
    response = TestClient(app, raise_server_exceptions=False).post(
        f'/api/projects/{project}/graph-sessions', json={})
    assert response.status_code == 503
    assert response.headers['content-type'].startswith('application/json')
    assert response.json()['code'] == 'database_unavailable'
    assert '数据库' in response.json()['detail']
    assert 'must-not-leak' not in response.text


@pytest.mark.parametrize('error', [psycopg.OperationalError, psycopg.InterfaceError])
def test_authenticated_connection_failure_fails_closed_with_json_503(tmp_path, monkeypatch, error):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.repository.users_store import UserStore
    from knowledge_service.services.graph_sessions import GraphSessions

    app = create_app(tmp_path / 'connection-auth', encoder=HashingEncoder(), auth_disabled=False)
    project = app.state.service.repository.create_project('authenticated graph')['id']
    client = TestClient(app, raise_server_exceptions=False)
    login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    assert login.status_code == 200
    token = login.json()['access_token']
    calls = []

    def unavailable(*args, **kwargs):
        raise error('password=must-not-leak')

    monkeypatch.setattr(UserStore, 'get_for_authentication', unavailable)
    monkeypatch.setattr(GraphSessions, 'create', lambda *args, **kwargs: calls.append('reached'))
    response = client.post(f'/api/projects/{project}/graph-sessions', json={},
                           headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 503
    assert response.json()['code'] == 'database_unavailable'
    assert 'must-not-leak' not in response.text
    assert calls == []
