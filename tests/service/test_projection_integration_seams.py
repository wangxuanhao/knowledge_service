from contextlib import contextmanager
import json
from pathlib import Path
import threading

from knowledge_service.repository.core import Repository
from knowledge_service.repository.projection_store import ProjectionStore
from knowledge_service.services.retrieval import RetrievalEngine


class Cursor:
    def __init__(self, row=None, rowcount=0, rows=()):
        self.row = row
        self.rowcount = rowcount
        self.rows = list(rows)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class TransactionDatabase:
    def __init__(self):
        self.in_transaction = False
        self.statements = []

    def execute(self, statement, params=()):
        normalized = ' '.join(statement.split())
        self.statements.append((normalized, params, self.in_transaction))
        if normalized == 'BEGIN':
            self.in_transaction = True
        if normalized.startswith('SELECT COUNT(*) FROM assertions'):
            return Cursor((0,))
        rowcount = 1 if normalized.startswith('DELETE FROM projects') else 0
        return Cursor(rowcount=rowcount)

    def commit(self):
        self.in_transaction = False

    def rollback(self):
        self.in_transaction = False


def bare_repository(database):
    repository = Repository.__new__(Repository)
    repository._db = database
    repository._lock = threading.RLock()
    repository.get_project = lambda project_id: {'id': project_id}
    return repository


def test_insert_if_absent_runs_put_and_outbox_inside_one_transaction():
    database = TransactionDatabase()
    repository = bare_repository(database)
    observed = []

    def put(project_id, record, expected_version):
        observed.append((project_id, expected_version, database.in_transaction))
        return record

    repository._put = put
    repository.insert_if_absent('project', {'id': 'rule'})

    assert observed == [('project', 0, True)]
    assert database.in_transaction is False


class Encoder:
    identity = 'test-v1'

    def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


class ReadinessRepository:
    def __init__(self):
        self.ready = False
        self.readiness_calls = []
        self.query_calls = []
        self.authority_rows = []

    def projection_ready(self, project_id, target, schema_version):
        self.readiness_calls.append((project_id, target, schema_version))
        return self.ready

    def query(self, project_id, **kwargs):
        self.query_calls.append((project_id, kwargs))
        return list(self.authority_rows)

    def current_records_by_version_ids(self, project_id, version_ids):
        by_version = {row['version_id']: row for row in self.authority_rows}
        return [by_version[item] for item in version_ids if item in by_version]


class RecordingMilvus:
    available = True

    def __init__(self):
        self.calls = []

    def search_dense(self, project_id, vector, **kwargs):
        self.calls.append((project_id, kwargs))
        return [{'version_id': 'version-1', 'score': 0.9}]


def test_new_authoritative_write_blocks_milvus_until_projection_catches_up():
    repository = ReadinessRepository()
    milvus = RecordingMilvus()
    engine = RetrievalEngine(repository, Encoder(), milvus)
    candidates = [{
        'id': 'rule', 'version_id': 'version-1', 'kind': 'chunk', 'text': '规则',
        'metadata': {}, 'embedding': [1.0, 0.0], 'embedding_model': 'test-v1',
    }]
    repository.authority_rows = candidates

    stale = engine.search(
        'project', '规则', retrieval_mode='semantic', candidates=candidates)
    assert milvus.calls == []
    assert stale['_backend'] == 'postgres_local'
    assert stale['degraded_reason'] == 'milvus_projection_stale'

    repository.ready = True
    ready = engine.search(
        'project', '规则', retrieval_mode='semantic', candidates=candidates)
    assert len(milvus.calls) == 1
    assert ready['_backend'] == 'milvus'
    assert repository.readiness_calls[-1][:2] == ('project', 'milvus')


def test_stale_projection_discards_passed_candidates_and_reapplies_id_scope():
    repository = ReadinessRepository()
    repository.authority_rows = [
        {'id': 'authority', 'version_id': 'version-2', 'kind': 'chunk',
         'text': '权威规则', 'metadata': {}, 'embedding': [1.0, 0.0],
         'embedding_model': 'test-v1'},
        {'id': 'outside-explicit-scope', 'version_id': 'version-3',
         'kind': 'chunk', 'text': '不应返回', 'metadata': {},
         'embedding': [1.0, 0.0], 'embedding_model': 'test-v1'},
    ]
    stale_candidates = [
        {'id': 'stale', 'version_id': 'version-1', 'kind': 'chunk',
         'text': '陈旧规则', 'metadata': {}, 'embedding': [1.0, 0.0],
         'embedding_model': 'test-v1'},
    ]
    engine = RetrievalEngine(repository, Encoder(), RecordingMilvus())

    result = engine.search(
        'project', '规则', retrieval_mode='semantic',
        scope={'_candidate_ids': ['authority']}, candidates=stale_candidates)

    assert repository.query_calls
    assert [row['id'] for row in result['hits']] == ['authority']


class EmptyOntologyDrafts:
    def delete_counts(self, project_id):
        return {}


def test_delete_project_enqueues_cleanup_before_authority_is_deleted():
    database = TransactionDatabase()
    repository = bare_repository(database)
    repository._ontology_drafts = EmptyOntologyDrafts()

    repository.delete_project('project')

    cleanup = [entry for entry in database.statements
               if entry[0].startswith('INSERT INTO projection_cleanup_outbox')]
    project_delete_index = next(
        index for index, entry in enumerate(database.statements)
        if entry[0].startswith('DELETE FROM projects'))
    cleanup_indices = [database.statements.index(entry) for entry in cleanup]
    assert [entry[1][1] for entry in cleanup] == ['milvus', 'neo4j']
    assert all(entry[2] for entry in cleanup)
    assert max(cleanup_indices) < project_delete_index


def test_cleanup_claim_and_ack_seam_uses_a_lease():
    class CleanupDatabase(TransactionDatabase):
        def execute(self, statement, params=()):
            normalized = ' '.join(statement.split())
            if normalized.startswith(
                    'SELECT project_id,target,created_at,attempts,last_error'):
                assert 'LIMIT ? FOR UPDATE SKIP LOCKED' in normalized
                return Cursor(rows=[{
                    'project_id': 'project', 'target': 'milvus',
                    'created_at': '2026-01-01T00:00:00Z',
                    'attempts': 0, 'last_error': None,
                }])
            if normalized.startswith('UPDATE projection_cleanup_outbox'):
                return Cursor(rowcount=1)
            if normalized.startswith('DELETE FROM projection_cleanup_outbox'):
                return Cursor(rowcount=1)
            return super().execute(statement, params)

    repository = bare_repository(CleanupDatabase())
    claimed = repository.claim_projection_cleanups('worker')
    assert claimed[0]['claim_owner'] == 'worker'
    assert claimed[0]['attempts'] == 1
    token = claimed[0]['claim_token']
    assert repository.ack_projection_cleanup(
        'project', 'milvus', 'worker', token) is True


def test_cleanup_ack_rejects_late_token_even_for_same_owner():
    class CasDatabase(TransactionDatabase):
        active_token = 'new-token'

        def execute(self, statement, params=()):
            normalized = ' '.join(statement.split())
            if normalized.startswith('DELETE FROM projection_cleanup_outbox'):
                assert 'claim_owner=? AND claim_token=?' in normalized
                return Cursor(rowcount=int(
                    tuple(params[-2:]) == ('worker', self.active_token)))
            return super().execute(statement, params)

    repository = bare_repository(CasDatabase())
    assert not repository.ack_projection_cleanup(
        'project', 'milvus', 'worker', 'old-token')
    assert repository.ack_projection_cleanup(
        'project', 'milvus', 'worker', 'new-token')


def test_cleanup_migration_has_no_projects_foreign_key_and_supports_leases():
    migration = (Path(__file__).resolve().parents[2] / 'knowledge_service' /
                 'repository' / 'migrations' /
                 '0017_projection_cleanup_outbox.sql').read_text(encoding='utf-8')
    assert 'projection_cleanup_outbox' in migration
    assert 'REFERENCES projects' not in migration
    assert 'claim_owner' in migration and 'claim_until' in migration
    assert 'claim_token' in migration
    assert "'milvus','neo4j'" in migration.replace(' ', '')


def test_synchronous_cleanup_acks_success_and_keeps_failures_for_retry():
    from knowledge_service.api.projects import _cleanup_project_projections

    class CleanupRepository:
        def __init__(self):
            self.acked = []
            self.failed = []

        def claim_projection_cleanup(self, project_id, target, owner,
                                     lease_seconds=60):
            self.owner = owner
            return {"project_id": project_id, "target": target,
                    "claim_owner": owner, "claim_token": f'{target}-token'}

        def ack_projection_cleanup(self, project_id, target, owner, token):
            self.acked.append((project_id, target, owner, token))

        def release_projection_cleanup(self, project_id, target, owner, token,
                                       error):
            self.failed.append((project_id, target, owner, token, str(error)))

    class Milvus:
        def delete(self, project_id):
            return 1

    class Neo4j:
        def delete_project(self, project_id):
            raise RuntimeError('offline')

    class State:
        neo4j = Neo4j()

    class App:
        state = State()

    class Service:
        milvus_store = Milvus()

    repository = CleanupRepository()
    cleaned = _cleanup_project_projections(App(), Service(), repository, 'project')

    assert cleaned == {'milvus': True, 'neo4j': False}
    assert repository.acked == [
        ('project', 'milvus', repository.owner, 'milvus-token')]
    assert repository.failed[0][:2] == ('project', 'neo4j')


def test_synchronous_cleanup_respects_an_existing_lease():
    from knowledge_service.api.projects import _cleanup_project_projections

    class Repository:
        def claim_projection_cleanup(self, *args, **kwargs):
            return None

    class Store:
        def delete(self, project_id):
            raise AssertionError('leased cleanup must not execute')

    class Neo4j:
        def delete_project(self, project_id):
            raise AssertionError('leased cleanup must not execute')

    class App:
        state = type('State', (), {'neo4j': Neo4j()})()

    class Service:
        milvus_store = Store()

    assert _cleanup_project_projections(
        App(), Service(), Repository(), 'project') == {
            'milvus': False, 'neo4j': False}


def test_background_cleanup_drain_acks_success_and_releases_failure():
    class Repository:
        def __init__(self):
            self.acked = []
            self.released = []

        def claim_projection_cleanups(self, owner, limit=100):
            return [
                {'project_id': 'p1', 'target': 'milvus',
                 'claim_owner': owner, 'claim_token': 'one'},
                {'project_id': 'p2', 'target': 'neo4j',
                 'claim_owner': owner, 'claim_token': 'two'},
            ]

        def ack_projection_cleanup(self, project_id, target, owner, token):
            self.acked.append((project_id, target, owner, token))
            return True

        def release_projection_cleanup(self, project_id, target, owner, token,
                                       error):
            self.released.append(
                (project_id, target, owner, token, str(error)))
            return True

    repository = Repository()
    from knowledge_service.repository.core import drain_projection_cleanups
    result = drain_projection_cleanups(repository, {
        'milvus': lambda project_id: None,
        'neo4j': lambda project_id: (_ for _ in ()).throw(RuntimeError('offline')),
    }, 'worker', limit=2)
    assert result == {'claimed': 2, 'cleaned': 1, 'failed': 1}
    assert repository.acked == [('p1', 'milvus', 'worker', 'one')]
    assert repository.released[0][:4] == ('p2', 'neo4j', 'worker', 'two')


def test_repository_put_persists_embedding_only_in_projection_upsert_payload():
    class OutboxDatabase:
        in_transaction = True

        def __init__(self):
            self.record_payload = None
            self.projection_seq = 0
            self.outbox = []

        def execute(self, statement, params=()):
            normalized = ' '.join(statement.split())
            if normalized.startswith('SELECT version,recorded_at'):
                return Cursor({
                    'id': 'rule', 'version': 1, 'version_id': 'old-version',
                    'recorded_at': '2026-01-01T00:00:00Z',
                    'payload': json.dumps({
                        'id': 'rule', 'kind': 'entity', 'text': 'old',
                        'embedding_model': 'test-v1',
                    }),
                })
            if normalized.startswith('INSERT INTO record_versions'):
                self.record_payload = params[4]
                return Cursor()
            if normalized.startswith(
                    'SELECT projection_seq FROM project_projection_sequences'):
                return Cursor({'projection_seq': self.projection_seq})
            if normalized.startswith(
                    'UPDATE project_projection_sequences SET projection_seq='):
                self.projection_seq = params[0]
                return Cursor()
            if normalized.startswith('INSERT INTO projection_outbox'):
                self.outbox.append({
                    'project_id': params[0], 'seq': params[1],
                    'operation': params[2], 'version_id': params[3],
                    'payload': params[4],
                })
                return Cursor()
            if normalized.startswith(
                    'SELECT payload FROM projection_outbox'):
                return Cursor({'payload': self.outbox[-1]['payload']})
            return Cursor()

    repository = Repository.__new__(Repository)
    repository._db = OutboxDatabase()
    repository._validate_record = lambda record: dict(record)
    repository._refresh_fts_record = lambda project_id, record: None
    repository.projections = ProjectionStore(repository)

    saved = repository._put('project', {
        'id': 'rule', 'kind': 'entity', 'text': 'new',
        'embedding': [0.25, 0.75], 'embedding_model': 'test-v1',
    }, expected_version=1, recorded_at='2026-01-02T00:00:00Z')

    record_payload = json.loads(repository._db.record_payload)
    outbox_row = repository._db.execute(
        'SELECT payload FROM projection_outbox ORDER BY seq DESC LIMIT 1').fetchone()
    event_payload = json.loads(outbox_row['payload'])

    assert 'embedding' not in record_payload
    assert saved['embedding'] == [0.25, 0.75]
    assert [row['operation'] for row in repository._db.outbox] == [
        'tombstone', 'upsert']
    assert 'embedding' not in json.loads(repository._db.outbox[0]['payload'])
    assert event_payload['embedding'] == [0.25, 0.75]
    assert event_payload['version_id'] == saved['version_id']
