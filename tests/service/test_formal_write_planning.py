import copy
from contextlib import contextmanager
from threading import RLock

import pytest

from knowledge_service.services.formal_writes import (
    FormalFactWriter, canonical_attribute_key)
from knowledge_service.repository import (
    FormalWriteConflict, OntologyPublicationConflict, Repository)
from knowledge_service.repository.connection import (
    DeadlockDetected, SerializationFailure)


ONTOLOGY = (
    '@prefix ex: <http://ex/> . '
    '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
    '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . '
    '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> . '
    'ex:Person a owl:Class . '
    'ex:age a owl:DatatypeProperty; rdfs:domain ex:Person; '
    'rdfs:range xsd:integer .')


@pytest.fixture
def postgres_backend():
    """Planning is source-only and must not require PostgreSQL."""
    yield


class SnapshotRepository:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def formal_write_snapshot(self, project_id):
        assert project_id == self.snapshot['project_id']
        return copy.deepcopy(self.snapshot)

    @staticmethod
    def formal_write_context(context):
        return copy.deepcopy(context)


class RecordingDatabase:
    def __init__(self):
        self.in_transaction = False
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement):
        self.statements.append(statement)
        self.in_transaction = statement.startswith('BEGIN')

    def commit(self):
        self.commits += 1
        self.in_transaction = False

    def rollback(self):
        self.rollbacks += 1
        self.in_transaction = False


def test_serializable_repository_transaction_uses_postgres_isolation_level():
    repository = object.__new__(Repository)
    repository._lock = RLock()
    repository._db = RecordingDatabase()

    with repository._transaction(serializable=True):
        pass

    assert repository._db.statements == ['BEGIN ISOLATION LEVEL SERIALIZABLE']
    assert repository._db.commits == 1


@pytest.mark.parametrize('database_error', [
    SerializationFailure('serialization failed'),
    DeadlockDetected('deadlock detected'),
])
def test_publication_transaction_maps_retryable_database_conflicts(
        database_error):
    repository = object.__new__(Repository)
    repository._lock = RLock()
    repository._db = RecordingDatabase()

    with pytest.raises(OntologyPublicationConflict) as caught:
        with repository._publication_transaction():
            raise database_error

    assert caught.value.code == 'stale_source'
    assert caught.value.retryable is True
    assert repository._db.rollbacks == 1


@pytest.mark.parametrize('database_error', [
    SerializationFailure('serialization failed'),
    DeadlockDetected('deadlock detected'),
])
def test_formal_write_transaction_maps_retryable_database_conflicts(
        database_error):
    repository = object.__new__(Repository)
    repository._lock = RLock()
    repository._db = RecordingDatabase()

    with pytest.raises(FormalWriteConflict) as caught:
        with repository._formal_write_transaction():
            raise database_error

    assert caught.value.code == 'stale_snapshot'
    assert caught.value.retryable is True
    assert repository._db.rollbacks == 1


def test_shared_project_write_lock_has_one_deterministic_order():
    repository = object.__new__(Repository)
    repository._db = RecordingDatabase()

    repository._lock_project_write_tables()

    statement = repository._db.statements[0]
    assert 'SHARE ROW EXCLUSIVE' in statement
    expected_order = [
        'projects', 'ontologies', 'record_versions', 'assertions',
        'assertion_events', 'record_version_assertions', 'fact_keys',
        'resolution_reviews', 'merge_operations',
    ]
    assert [statement.index(table) for table in expected_order] == sorted(
        statement.index(table) for table in expected_order)


def _record(record_id, subject_id):
    return {
        'id': record_id,
        'kind': 'attribute',
        'type': 'age',
        'text': f'{subject_id} age 20',
        'subject_id': subject_id,
        'value': 20,
        'datatype': 'http://www.w3.org/2001/XMLSchema#integer',
        'metadata': {},
        'properties': {},
        'ontology_id': 'ontology-1',
        'version': 1,
        'version_id': f'{record_id}:v1',
        'project_id': 'project-1',
        'recorded_at': '2026-01-01T00:00:00Z',
        'superseded_at': None,
    }


def _empty_snapshot(fingerprint='empty-snapshot'):
    return {
        'project_id': 'project-1', 'storage_namespace': 'formal-plan-test',
        'ontology_id': 'ontology-1',
        'ontology': {'id': 'ontology-1', 'turtle': ONTOLOGY},
        'records': [], 'assertions': [], 'assertion_events': [],
        'record_version_assertions': [], 'fact_keys': [],
        'resolution_reviews': [], 'merge_operations': [],
        'fingerprint': fingerprint,
    }


class ExecutionGuard:
    execute_formal_write = Repository.execute_formal_write
    _formal_write_dependencies = Repository._formal_write_dependencies
    _publish_fingerprint = staticmethod(Repository._publish_fingerprint)

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.writes = 0

    @contextmanager
    def _formal_write_transaction(self):
        yield

    def _formal_write_snapshot_data(self, project_id):
        assert project_id == self.snapshot['project_id']
        return copy.deepcopy(self.snapshot)


def test_tampered_action_is_rejected_before_repository_writes():
    snapshot = _empty_snapshot()
    plan = FormalFactWriter(SnapshotRepository(snapshot)).prepare(
        'manual_write', 'project-1', [], {}, {'records': []})
    plan['actions'].append({'kind': 'record_put'})
    repository = ExecutionGuard(snapshot)

    with pytest.raises(ValueError, match='指纹'):
        repository.execute_formal_write(plan)

    assert repository.writes == 0


def test_changed_snapshot_dependency_is_rejected_before_repository_writes():
    snapshot = _empty_snapshot()
    plan = FormalFactWriter(SnapshotRepository(snapshot)).prepare(
        'manual_write', 'project-1', [], {}, {'records': []})
    changed = copy.deepcopy(snapshot)
    changed['fingerprint'] = 'changed-snapshot'
    repository = ExecutionGuard(changed)

    with pytest.raises(ValueError, match='依赖.*变更'):
        repository.execute_formal_write(plan)

    assert repository.writes == 0


def test_merge_collision_plan_is_deterministic_and_contains_business_decisions():
    source = _record('a-fact', 'drop')
    occupied = _record('z-fact', 'keep')
    source_key = canonical_attribute_key(source)
    occupied_key = canonical_attribute_key(occupied)
    snapshot = {
        'project_id': 'project-1',
        'storage_namespace': 'formal-plan-test',
        'ontology_id': 'ontology-1',
        'ontology': {'id': 'ontology-1', 'turtle': ONTOLOGY},
        'records': [source, occupied],
        'assertions': [{
            'id': 'support-z', 'project_id': 'project-1', 'kind': 'attribute',
            'payload': {}, 'status': 'accepted',
            'canonical_record_id': 'z-fact', 'decision_version': 2,
            'decision_reason': 'accepted', 'actor': 'reviewer',
            'created_at': '2026-01-01T00:00:00Z',
            'decided_at': '2026-01-01T00:00:00Z',
        }],
        'assertion_events': [],
        'record_version_assertions': [],
        'fact_keys': [
            {'project_id': 'project-1', 'fact_key': source_key,
             'canonical_record_id': 'a-fact', 'retired_at': None},
            {'project_id': 'project-1', 'fact_key': occupied_key,
             'canonical_record_id': 'z-fact', 'retired_at': None},
        ],
        'resolution_reviews': [],
        'merge_operations': [],
        'fingerprint': 'snapshot-fingerprint',
    }
    writer = FormalFactWriter(SnapshotRepository(snapshot))
    request = {
        'operation': 'merge_rewrite',
        'project_id': 'project-1',
        'assertions': [],
        'expected': {'a-fact': 1},
        'policy': {
            'records': [{
                **{key: value for key, value in source.items() if key not in {
                    'project_id', 'version', 'version_id', 'recorded_at',
                    'superseded_at'}},
                'subject_id': 'keep',
            }],
            'ledger': {'id': 'merge-1', 'redirects': {}},
        },
    }

    first = writer.prepare(**request)
    second = writer.prepare(**request)

    assert first == second
    assert first['fingerprint'] == second['fingerprint']
    actions = first['actions']
    assert any(action['kind'] == 'fact_key_retarget'
               and action['from_record_id'] == 'z-fact'
               and action['to_record_id'] == 'a-fact'
               for action in actions)
    assert not any(action['kind'] == 'fact_key_insert'
                   and action['fact_key'] == occupied_key
                   for action in actions)
    assert any(action['kind'] == 'record_put'
               and action['record']['id'] == 'z-fact'
               and action['record']['metadata']['merged_into_fact'] == 'a-fact'
               for action in actions)
    assert any(action['kind'] == 'assertion_move'
               and action['id'] == 'support-z'
               and action['canonical_after'] == 'a-fact'
               for action in actions)
    ledger = next(action for action in actions if action['kind'] == 'merge_ledger')
    assert ledger['id'] == 'merge-1'
    assert ledger['assertion_moves'][0]['id'] == 'support-z'
    assert first['dependencies']['snapshot_fingerprint'] == 'snapshot-fingerprint'
