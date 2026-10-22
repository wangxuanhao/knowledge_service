"""Independent bounded reads must not queue behind the repository writer."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from knowledge_service.api.security_middleware import install_auth_middleware
from knowledge_service.core.security import create_access_token, credential_fingerprint
from knowledge_service.repository import Repository
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.repository.users_store import UserStore


def _records(tmp_path):
    repo = Repository(tmp_path / 'independent-reads')
    project = repo.create_project('bounded project')['id']
    repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'first'})
    return repo, project


def _while_writer_locked(repo, operation):
    # Holding both locks models a main-connection scan even for callers which
    # previously bypassed the repository lock (notably authentication).
    with ThreadPoolExecutor(max_workers=1) as executor:
        with repo._lock, repo._db.raw.lock:
            future = executor.submit(operation)
            try:
                return future.result(timeout=1)
            except TimeoutError:
                pytest.fail('small independent read queued behind the writer connection')


@pytest.mark.parametrize('operation', ['page', 'by_ids', 'count', 'neighbors', 'manifest', 'batches'])
def test_bounded_reads_finish_while_main_writer_connection_is_busy(tmp_path, operation):
    repo, project = _records(tmp_path)
    repo.create_project('unrelated slow project')
    reads = ScopedReads(repo)
    operations = {
        'page': lambda: reads.page(project, {}, limit=1)['records'],
        'by_ids': lambda: reads.by_ids(project, {}, ['a']),
        'count': lambda: reads.count(project, {}),
        'neighbors': lambda: reads.neighbors(project, {}, ['a'])['records'],
        'manifest': lambda: reads.version_manifest(project, {}),
        'batches': lambda: list(reads.version_id_batches(project, {}, batch_size=1)),
    }
    assert _while_writer_locked(repo, operations[operation])


def test_page_count_and_payload_share_one_snapshot(tmp_path, monkeypatch):
    repo, project = _records(tmp_path)
    original = ScopedReads.count

    def count_then_write(reader, *args, **kwargs):
        total = original(reader, *args, **kwargs)
        repo.put_record(project, {'id': 'b', 'kind': 'entity', 'text': 'later'})
        return total

    monkeypatch.setattr(ScopedReads, 'count', count_then_write)
    page = ScopedReads(repo).page(project, {}, limit=10)
    assert page['total'] == 1
    assert [row['id'] for row in page['records']] == ['a']
    assert repo.get_record(project, 'b')['text'] == 'later'


def test_streamed_batches_keep_snapshot_until_exhausted(tmp_path):
    repo, project = _records(tmp_path)
    repo.put_record(project, {'id': 'b', 'kind': 'entity', 'text': 'second'})
    batches = ScopedReads(repo).version_id_batches(project, {}, batch_size=1)
    first = next(batches)
    repo.put_record(project, {'id': 'c', 'kind': 'entity', 'text': 'later'})
    remaining = list(batches)
    assert len(first) == 1
    assert len(remaining) == 1
    assert remaining[0] == [repo.get_record(project, 'b')['version_id']]


def test_write_transaction_reads_own_writes_and_rolls_back(tmp_path):
    repo, project = _records(tmp_path)
    with pytest.raises(RuntimeError, match='rollback'):
        with repo.unit_of_work():
            repo.put_record(project, {'id': 'pending', 'kind': 'entity', 'text': 'uncommitted'})
            with repo.independent_read_view() as reader:
                assert reader is repo
                assert reader.get_record(project, 'pending')['text'] == 'uncommitted'
            assert ScopedReads(repo).count(project, {}) == 2
            raise RuntimeError('rollback')
    assert ScopedReads(repo).count(project, {}) == 1
    with pytest.raises(KeyError):
        repo.get_record(project, 'pending')


def test_independent_view_rebinds_stores_and_reuses_one_nested_snapshot(tmp_path):
    repo, project = _records(tmp_path)
    with repo.independent_read_view() as reader:
        assert reader is not repo
        assert reader._lock is not repo._lock
        assert reader._db is not repo._db
        assert reader._assertions._db is reader._db
        assert reader.ingest_batches.db is reader._db
        assert reader.ingest_checkpoints.db is reader._db
        assert reader.source_documents.repo is reader
        assert reader._db.execute('SHOW transaction_isolation').fetchone()[0] == 'repeatable read'
        assert reader._db.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
        with reader.independent_read_view() as nested:
            assert nested is reader
        assert reader.get_project(project)['id'] == project


def test_reader_rollback_reuse_generator_close_and_repository_shutdown(tmp_path):
    import psycopg
    from psycopg.errors import ReadOnlySqlTransaction

    repo, project = _records(tmp_path)
    with pytest.raises(ReadOnlySqlTransaction):
        with repo.independent_read_view() as reader:
            connection = reader._db.raw
            reader._db.execute('UPDATE projects SET name=name WHERE id=?', (project,))
    assert connection.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    with pytest.raises(psycopg.InterfaceError, match='closed'):
        reader.get_project(project)

    connections = set()
    for _ in range(20):
        with repo.independent_read_view() as reader:
            connections.add(reader._db.raw)
            assert ScopedReads(reader).count(project, {}) == 1
    assert connections == {connection}

    batches = ScopedReads(repo).version_id_batches(project, {}, batch_size=1)
    assert next(batches)
    batches.close()
    pool = repo._db._read_pool
    assert pool.get_stats()['pool_available'] == pool.get_stats()['pool_size']
    repo.close()
    assert pool.closed
    assert connection.closed
    with pytest.raises(psycopg.InterfaceError, match='closed'):
        with repo.independent_read_view():
            pytest.fail('closed repository allowed a new reader')


def test_independent_reader_budget_waits_and_releases_borrowed_connections(tmp_path):
    from contextlib import ExitStack

    repo, project = _records(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with ExitStack() as borrowed:
            readers = [borrowed.enter_context(repo.independent_read_view()) for _ in range(8)]
            assert len({reader._db.raw for reader in readers}) == 8
            waiting = executor.submit(ScopedReads(repo).count, project, {})
            with pytest.raises(TimeoutError):
                waiting.result(timeout=0.1)
            assert repo._db._read_pool.get_stats()['pool_size'] == 8
        assert waiting.result(timeout=2) == 1
    pool = repo._db._read_pool
    assert pool.get_stats()['pool_available'] == pool.get_stats()['pool_size']


def test_user_administrative_reads_keep_transaction_local_account_changes(tmp_path):
    repo, _ = _records(tmp_path)
    users = UserStore(repo)
    with pytest.raises(RuntimeError, match='rollback'):
        with repo.unit_of_work():
            user = users.create('pending-user', 'test-hash')
            assert users.get_by_id(user['id'])['username'] == 'pending-user'
            assert users.get_by_username('PENDING-USER')['id'] == user['id']
            assert users.get_for_login('pending-user') is None
            assert users.get_for_authentication(user['id']) is None
            raise RuntimeError('rollback')
    assert users.get_by_username('pending-user') is None


@pytest.mark.parametrize('operation', ['records_page', 'neighborhood'])
def test_graph_workspace_complete_reads_do_not_wait_for_writer(tmp_path, operation):
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.services.graph_workspace import GraphWorkspace
    from knowledge_service.services.service import KnowledgeService

    repo, project = _records(tmp_path)
    workspace = GraphWorkspace(KnowledgeService(repo, HashingEncoder()))
    values = ({'query': '', 'cursor': None, 'limit': 10, 'include_total': True, 'record_ids': None}
              if operation == 'records_page' else
              {'seeds': ['a'], 'hops': 1, 'direction': 'forward', 'max_nodes': 10, 'max_edges': 10})
    result = _while_writer_locked(repo, lambda: getattr(workspace, operation)(project, values))
    assert [row['id'] for row in result['records']] == ['a']


@pytest.mark.parametrize('operation', ['records_page', 'neighborhood'])
def test_graph_workspace_cache_reads_do_not_wait_for_service_writer_lock(tmp_path, operation):
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.services.graph_workspace import GraphWorkspace
    from knowledge_service.services.ontology import Ontology
    from knowledge_service.services.service import KnowledgeService

    repo, project = _records(tmp_path)
    turtle = '@prefix owl: <http://www.w3.org/2002/07/owl#> . <urn:Thing> a owl:Class .'
    ontology = repo.bootstrap_ontology(project, turtle, Ontology(turtle).summary())
    repo.put_record(project, {'id': 'a', 'kind': 'entity', 'text': 'first',
        'type': 'urn:Thing', 'ontology_id': ontology['id']}, expected_version=1)
    service = KnowledgeService(repo, HashingEncoder())
    workspace = GraphWorkspace(service)
    values = ({'query': '', 'cursor': None, 'limit': 10, 'include_total': True, 'record_ids': None}
              if operation == 'records_page' else
              {'seeds': ['a'], 'hops': 1, 'direction': 'forward', 'max_nodes': 10, 'max_edges': 10})
    with ThreadPoolExecutor(max_workers=1) as executor:
        # Both the cold population and the warm cache lookup must avoid the
        # lock held by long-running service writes and ingestion operations.
        for _ in range(2):
            with service.lock:
                future = executor.submit(getattr(workspace, operation), project, values)
                try:
                    result = future.result(timeout=1)
                except TimeoutError:
                    pytest.fail('bounded graph presentation queued behind the service writer lock')
            assert [row['id'] for row in result['records']] == ['a']
    assert project in service._structure_pending_cache
    if operation == 'neighborhood':
        assert project in service._ontology_label_cache
        assert (project, ontology['id']) in service._ontology_family_cache


def test_service_read_view_keeps_data_port_and_ontology_snapshot(tmp_path):
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.services.ontology import Ontology
    from knowledge_service.services.service import KnowledgeService

    repo, project = _records(tmp_path)
    service = KnowledgeService(repo, HashingEncoder())
    with service.independent_read_view() as reader:
        assert reader.repository is not repo
        assert reader.data.get_project(project)['id'] == project
        assert reader.lock is service.lock
        assert reader._ontology_cache_lock is service._ontology_cache_lock
        assert reader._ontology_cache_lock is not reader.lock
        assert reader._ontology_label_cache is service._ontology_label_cache
        assert reader.ontology_labels(project) == {}
        turtle = '@prefix owl: <http://www.w3.org/2002/07/owl#> . <urn:Thing> a owl:Class .'
        repo.bootstrap_ontology(project, turtle, Ontology(turtle).summary())
        assert reader.ontology_labels(project) == {}
        with reader.independent_read_view() as nested:
            assert nested is reader
    assert service.ontology_labels(project)['urn:Thing'] == 'Thing'


def test_login_does_not_wait_for_writer_and_preserves_username_normalization(tmp_path):
    from knowledge_service.api import auth
    from knowledge_service.core.security import hash_password

    repo, _ = _records(tmp_path)
    users = UserStore(repo)
    users.create('alice', hash_password('password123'))
    app = FastAPI()
    auth.install(app, users=users, jwt_secret='read-isolation-test-secret', ttl_seconds=60)
    with TestClient(app) as client:
        result = _while_writer_locked(repo, lambda: client.post('/api/auth/login', json={
            'username': ' ALICE ', 'password': 'password123'}))
    assert result.status_code == 200
    assert result.json()['user']['username'] == 'alice'


@pytest.mark.parametrize('kind', ['entities', 'connected'])
def test_existing_graph_session_page_does_not_wait_for_writer(tmp_path, kind):
    from knowledge_service.services.graph_sessions import GraphSessions

    repo, project = _records(tmp_path)
    repo.put_batch(project, [
        {'id': 'b', 'kind': 'entity', 'text': 'second'},
        {'id': 'ab', 'kind': 'relation', 'text': 'link', 'subject_id': 'a', 'object_id': 'b'},
    ])
    sessions = GraphSessions(repo)
    manifest = sessions.create(project, {}, actor_id='alice')
    result = _while_writer_locked(repo, lambda: sessions.page(
        project, manifest['id'], kind, actor_id='alice'))
    assert result['records']
    if kind == 'connected':
        assert {row['id'] for row in result['entities']} == {'a', 'b'}


@pytest.mark.parametrize('operation', ['execute', 'executemany', 'executescript', 'commit', 'rollback', 'in_transaction'])
def test_released_reader_cannot_access_a_connection_reused_by_another_operation(tmp_path, operation):
    import psycopg

    repo, _ = _records(tmp_path)
    with repo.independent_read_view() as reader:
        database = reader._db
    with repo.independent_read_view() as current:
        assert current._db.raw is database.raw
        with pytest.raises(psycopg.InterfaceError, match='closed'):
            if operation == 'execute':
                database.execute('SELECT 1')
            elif operation == 'executemany':
                database.executemany('SELECT 1', [()])
            elif operation == 'executescript':
                database.executescript('SELECT 1')
            elif operation == 'in_transaction':
                _ = database.in_transaction
            else:
                getattr(database, operation)()
        assert current._db.in_transaction


def _auth_app(users, row):
    app = FastAPI()
    secret = 'read-isolation-test-secret'
    install_auth_middleware(app, users=users, jwt_secret=secret)

    @app.get('/api/private')
    async def private(request: Request):
        return {'id': request.state.user['id']}

    @app.get('/api/health')
    async def health():
        return {'ok': True}

    token = create_access_token(user_id=row['id'], username=row['username'],
        role=row['role'], secret=secret, ttl_seconds=60,
        fingerprint=credential_fingerprint(row['password_hash']))
    return app, {'Authorization': 'Bearer ' + token}


def test_authentication_does_not_wait_for_main_writer_connection(tmp_path):
    repo, _ = _records(tmp_path)
    users = UserStore(repo)
    user = users.create('alice', 'test-hash')
    row = users.get_by_id(user['id'])
    app, headers = _auth_app(users, row)
    with TestClient(app) as client:
        result = _while_writer_locked(repo, lambda: client.get('/api/private', headers=headers))
    assert result.status_code == 200
    assert result.json() == {'id': user['id']}


def test_waiting_authentication_keeps_public_event_loop_responsive():
    entered, release = Event(), Event()
    row = {'id': 'alice', 'username': 'alice', 'role': 'viewer',
           'display_name': 'Alice', 'is_active': True, 'password_hash': 'hash'}

    class WaitingUsers:
        def get_by_id(self, user_id):
            entered.set()
            assert release.wait(2), 'authentication blocked the event loop'
            return row

        get_for_authentication = get_by_id

    app, headers = _auth_app(WaitingUsers(), row)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            protected = asyncio.create_task(client.get('/api/private', headers=headers))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                health = await asyncio.wait_for(client.get('/api/health'), timeout=0.5)
                assert health.status_code == 200
            finally:
                release.set()
                await protected

    asyncio.run(scenario())
