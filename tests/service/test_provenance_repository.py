import json
import sqlite3

import pytest

import knowledge_service.repository as repository_module
from knowledge_service.repository import Repository


def _create_v11_database(path, monkeypatch):
    migrations = repository_module._SCHEMA_MIGRATIONS
    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations[:11])
    repo = Repository(path)
    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations)
    return repo


def _insert_legacy_support(repo, project_id, *, record_id, version_id, assertion_id,
                           event_ids, timestamp='2025-01-02T03:04:05.000000Z'):
    repo._db.execute(
        '''INSERT INTO record_versions
           (project_id,id,version,version_id,payload,recorded_at,superseded_at)
           VALUES (?,?,?,?,?,?,NULL)''',
        (project_id, record_id, 1, version_id,
         json.dumps({'id': record_id, 'kind': 'entity', 'text': record_id}), timestamp))
    repo._db.execute(
        '''INSERT INTO assertions
           (id,project_id,kind,payload,status,canonical_record_id,decision_reason,
            decision_version,created_at,decided_at,actor)
           VALUES (?,?,?,'{}','accepted',?,'accepted',?,?,?,'reviewer')''',
        (assertion_id, project_id, 'entity', record_id, len(event_ids) + 1,
         timestamp, timestamp))
    for index, event_id in enumerate(event_ids, start=2):
        repo._db.execute(
            '''INSERT INTO assertion_events
               (id,assertion_id,project_id,from_status,to_status,decision_version,
                reason,actor,canonical_record_id,created_at)
               VALUES (?,?,?,'pending','accepted',?,'accepted','reviewer',?,?)''',
            (event_id, assertion_id, project_id, index, record_id, timestamp))
    repo._db.commit()


def _edge(edge_id, activity_id, source, relation, target, ordinal=0, payload=None):
    return {
        'id': edge_id,
        'activity_id': activity_id,
        'source_ref': source,
        'relation': relation,
        'target_ref': target,
        'ordinal': ordinal,
        'payload': {} if payload is None else payload,
    }


def test_fresh_database_has_schema_version_12_and_provenance_tables(tmp_path):
    repo = Repository(tmp_path / 'fresh.sqlite')

    assert repo._db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 12
    tables = {
        row[0] for row in repo._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {'record_version_assertions', 'provenance_activities', 'provenance_edges'} <= tables


def test_real_v11_database_upgrades_once_and_restart_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / 'upgrade.sqlite'
    old = _create_v11_database(path, monkeypatch)
    assert old._db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 11
    old.close()

    first = Repository(path)
    first._db.execute(
        "INSERT INTO provenance_activities VALUES (?,?,?,?,?,?,?)",
        ('rr_keep', first.create_project('p')['id'], 'retrieval', 'running', '{}',
         '2025-01-01T00:00:00.000000Z', None),
    )
    first._db.commit()
    first.close()

    second = Repository(path)
    assert second._db.execute(
        'SELECT COUNT(*) FROM schema_migrations WHERE version=12'
    ).fetchone()[0] == 1
    assert second._db.execute(
        "SELECT status FROM provenance_activities WHERE id='rr_keep'"
    ).fetchone()[0] == 'running'


def test_migration_12_failure_rolls_back_tables_indexes_and_marker(tmp_path, monkeypatch):
    path = tmp_path / 'rollback.sqlite'
    old = _create_v11_database(path, monkeypatch)
    old.close()
    migrations = repository_module._SCHEMA_MIGRATIONS
    real_migration = migrations[11][1]

    def fail_after_migration(db):
        real_migration(db)
        raise RuntimeError('migration 12 failed')

    monkeypatch.setattr(
        repository_module, '_SCHEMA_MIGRATIONS', (*migrations[:11], (12, fail_after_migration)))

    with pytest.raises(RuntimeError, match='migration 12 failed'):
        Repository(path)

    with sqlite3.connect(path) as db:
        names = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'provenance_%' "
                "OR name LIKE 'record_version_assertions%' "
                "OR name IN ('record_versions_provenance_fk', "
                "'assertion_events_provenance_fk')"
            )
        }
        assert names == set()
        assert db.execute(
            'SELECT COUNT(*) FROM schema_migrations WHERE version=12'
        ).fetchone()[0] == 0


def test_migration_12_backfills_only_unique_exact_legacy_support(tmp_path, monkeypatch):
    path = tmp_path / 'backfill.sqlite'
    old = _create_v11_database(path, monkeypatch)
    project_id = old.create_project('legacy')['id']
    _insert_legacy_support(
        old, project_id, record_id='record-exact', version_id='version-exact',
        assertion_id='assertion-exact', event_ids=['event-exact'])
    _insert_legacy_support(
        old, project_id, record_id='record-ambiguous', version_id='version-ambiguous',
        assertion_id='assertion-ambiguous', event_ids=['event-a', 'event-b'])
    old.close()

    upgraded = Repository(path)

    assert upgraded.list_record_version_assertions(project_id) == [{
        'project_id': project_id,
        'record_id': 'record-exact',
        'record_version_id': 'version-exact',
        'assertion_id': 'assertion-exact',
        'assertion_event_id': 'event-exact',
        'created_at': '2025-01-02T03:04:05.000000Z',
    }]


def test_composite_foreign_keys_reject_cross_project_edge_and_mismatched_mapping(tmp_path):
    repo = Repository(tmp_path / 'constraints.sqlite')
    first = repo.create_project('first')['id']
    second = repo.create_project('second')['id']
    record = repo.put_record(first, {'id': 'record', 'kind': 'entity', 'text': 'record'})
    assertion = repo.create_assertion(first, {
        'id': 'assertion', 'kind': 'entity', 'payload': {}, 'actor': 'reviewer'})
    accepted = repo.transition_assertion(
        first, assertion['id'], assertion['decision_version'], 'accepted',
        'accepted', 'reviewer', canonical_record_id='record')
    event = repo.list_assertion_events(first, assertion['id'])[-1]
    other = repo.create_assertion(first, {
        'id': 'other-assertion', 'kind': 'entity', 'payload': {}, 'actor': 'reviewer'})
    repo.begin_provenance_activity(first, 'rr_1', 'retrieval', {})

    with pytest.raises(sqlite3.IntegrityError):
        repo._db.execute(
            '''INSERT INTO provenance_edges
               (id,project_id,activity_id,source_ref,relation,target_ref,ordinal,payload,created_at)
               VALUES ('edge-cross',?,'rr_1','retrieval-run:rr_1','considered',
                       'record-version:version',0,'{}','2025-01-01')''',
            (second,))
    with pytest.raises(sqlite3.IntegrityError):
        repo._db.execute(
            '''INSERT INTO record_version_assertions
               (project_id,record_id,record_version_id,assertion_id,assertion_event_id,created_at)
               VALUES (?,?,?,?,?,'2025-01-01')''',
            (first, 'record', record['version_id'], other['id'], event['id']))

    assert accepted['status'] == 'accepted'


@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled'])
def test_running_activity_allows_one_terminal_transition_and_identical_retry(
        tmp_path, terminal):
    repo = Repository(tmp_path / f'{terminal}.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'activity', 'retrieval', {'query': 'q'})
    payload = {'result': terminal}
    transition = {
        'completed': repo.complete_provenance_activity,
        'failed': repo.fail_provenance_activity,
        'cancelled': repo.cancel_provenance_activity,
    }[terminal]

    first = transition(project_id, 'activity', payload)
    repeated = transition(project_id, 'activity', payload)

    assert first == repeated
    assert repeated['status'] == terminal
    with pytest.raises(ValueError, match='conflict|冲突'):
        transition(project_id, 'activity', {'result': 'different'})


def test_activity_completion_and_edges_are_atomic_and_identical_edge_is_idempotent(tmp_path):
    repo = Repository(tmp_path / 'atomic.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'answer', 'answer', {'phase': 'start'})
    edge = _edge(
        'edge-1', 'answer', 'answer:answer#E1', 'cites', 'record-version:v1',
        payload={'selected': True})

    completed = repo.complete_provenance_activity(
        project_id, 'answer', {'text': 'done'}, edges=[edge])
    repeated = repo.complete_provenance_activity(
        project_id, 'answer', {'text': 'done'},
        edges=[{**edge, 'id': 'retry-generated-a-different-id'}])

    assert completed == repeated
    assert repo.list_provenance_edges(project_id, activity_id='answer') == [{
        **edge,
        'project_id': project_id,
        'created_at': repo.list_provenance_edges(project_id, activity_id='answer')[0]['created_at'],
    }]
    with pytest.raises(ValueError, match='conflict|冲突'):
        repo.complete_provenance_activity(
            project_id, 'answer', {'text': 'changed'}, edges=[edge])
    with pytest.raises(ValueError, match='conflict|冲突'):
        repo.complete_provenance_activity(
            project_id, 'answer', {'text': 'done'},
            edges=[{**edge, 'payload': {'selected': False}}])


@pytest.mark.parametrize('retry_edges', [
    [],
    [_edge('different', 'answer', 'answer:answer#E2', 'cites', 'record-version:v2')],
    [
        _edge('edge-1', 'answer', 'answer:answer#E1', 'cites', 'record-version:v1',
              payload={'selected': True}),
        _edge('extra', 'answer', 'answer:answer#E2', 'cites', 'record-version:v2'),
    ],
])
def test_terminal_activity_retry_requires_the_complete_frozen_edge_set(
        tmp_path, retry_edges):
    repo = Repository(tmp_path / 'terminal-edge-set.sqlite')
    project_id = repo.create_project('p')['id']
    edge = _edge(
        'edge-1', 'answer', 'answer:answer#E1', 'cites', 'record-version:v1',
        payload={'selected': True})
    repo.begin_provenance_activity(project_id, 'answer', 'answer', {})
    repo.complete_provenance_activity(
        project_id, 'answer', {'text': 'done'}, edges=[edge])

    with pytest.raises(ValueError, match='conflict|冲突'):
        repo.complete_provenance_activity(
            project_id, 'answer', {'text': 'done'}, edges=retry_edges)

    assert repo.list_provenance_edges(project_id, activity_id='answer')[0]['id'] == 'edge-1'


def test_complete_retrieval_and_begin_answer_owns_one_atomic_transaction(tmp_path):
    repo = Repository(tmp_path / 'composition.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'rr_1', 'retrieval', {'query': 'q'})
    edges = [
        _edge('considered', 'rr_1', 'retrieval-run:rr_1', 'considered', 'record-version:v1'),
        _edge('used', 'ans_1', 'answer:ans_1', 'used', 'retrieval-run:rr_1'),
        _edge('offered', 'ans_1', 'answer:ans_1#E1', 'offered', 'record-version:v1'),
    ]

    retrieval, answer = repo.complete_retrieval_and_begin_answer(
        project_id, retrieval_id='rr_1', answer_id='ans_1',
        retrieval_payload={'count': 1}, answer_payload={'offered': ['E1']}, edges=edges)

    assert retrieval['status'] == 'completed'
    assert answer['status'] == 'running'
    assert [row['id'] for row in repo.list_provenance_edges(project_id)] == [
        'considered', 'offered', 'used']

    retried = [{**edge, 'id': f'retry-{index}'} for index, edge in enumerate(edges)]
    assert repo.complete_retrieval_and_begin_answer(
        project_id, retrieval_id='rr_1', answer_id='ans_1',
        retrieval_payload={'count': 1}, answer_payload={'offered': ['E1']},
        edges=retried) == (retrieval, answer)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'changed'])
def test_composed_retry_requires_the_complete_frozen_edge_set(tmp_path, mutation):
    repo = Repository(tmp_path / f'composition-{mutation}.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'rr_1', 'retrieval', {'query': 'q'})
    edges = [
        _edge('considered', 'rr_1', 'retrieval-run:rr_1', 'considered',
              'record-version:v1', payload={'rank': 1}),
        _edge('used', 'ans_1', 'answer:ans_1', 'used', 'retrieval-run:rr_1'),
        _edge('offered', 'ans_1', 'answer:ans_1#E1', 'offered', 'record-version:v1'),
    ]
    repo.complete_retrieval_and_begin_answer(
        project_id, retrieval_id='rr_1', answer_id='ans_1',
        retrieval_payload={'count': 1}, answer_payload={'offered': ['E1']}, edges=edges)
    if mutation == 'missing':
        retried = edges[:-1]
    elif mutation == 'extra':
        retried = [
            *edges,
            _edge('extra', 'rr_1', 'retrieval-run:rr_1', 'considered',
                  'record-version:v2', ordinal=1),
        ]
    else:
        retried = [*edges[:-1], {**edges[-1], 'payload': {'changed': True}}]

    with pytest.raises(ValueError, match='conflict|冲突'):
        repo.complete_retrieval_and_begin_answer(
            project_id, retrieval_id='rr_1', answer_id='ans_1',
            retrieval_payload={'count': 1}, answer_payload={'offered': ['E1']},
            edges=retried)

    assert [row['id'] for row in repo.list_provenance_edges(project_id)] == [
        'considered', 'offered', 'used']


def test_composed_retrieval_completion_rolls_back_after_partial_edge_writes(
        tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'composition-rollback.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'rr_1', 'retrieval', {'query': 'q'})
    edges = [
        _edge('considered', 'rr_1', 'retrieval-run:rr_1', 'considered',
              'record-version:v1'),
        _edge('used', 'ans_1', 'answer:ans_1', 'used', 'retrieval-run:rr_1'),
        _edge('offered', 'ans_1', 'answer:ans_1#E1', 'offered', 'record-version:v1'),
    ]
    real_insert = repo._provenance._insert_edge
    inserted = []

    def fail_after_second_insert(edge_project_id, edge, default_created_at=None):
        result = real_insert(edge_project_id, edge, default_created_at)
        inserted.append(result['id'])
        if len(inserted) == 2:
            raise RuntimeError('injected after partial edge writes')
        return result

    monkeypatch.setattr(repo._provenance, '_insert_edge', fail_after_second_insert)

    with pytest.raises(RuntimeError, match='injected after partial edge writes'):
        repo.complete_retrieval_and_begin_answer(
            project_id, retrieval_id='rr_1', answer_id='ans_1',
            retrieval_payload={'count': 1}, answer_payload={'offered': ['E1']},
            edges=edges)

    assert inserted == ['considered', 'used']
    assert repo.get_provenance_activity(project_id, 'rr_1')['status'] == 'running'
    with pytest.raises(KeyError):
        repo.get_provenance_activity(project_id, 'ans_1')
    assert repo.list_provenance_edges(project_id) == []


def test_activity_and_edge_reads_are_project_scoped(tmp_path):
    repo = Repository(tmp_path / 'scoped.sqlite')
    first = repo.create_project('first')['id']
    second = repo.create_project('second')['id']
    repo.begin_provenance_activity(first, 'same-id', 'retrieval', {'project': 'first'})
    repo.begin_provenance_activity(second, 'same-id', 'retrieval', {'project': 'second'})

    assert [row['payload'] for row in repo.list_provenance_activities(first)] == [
        {'project': 'first'}]
    assert repo.get_provenance_activity(second, 'same-id')['payload'] == {
        'project': 'second'}
    assert repo.list_provenance_edges(first) == []


def test_get_record_version_reads_exact_project_scoped_history(tmp_path):
    repo = Repository(tmp_path / 'versions.sqlite')
    project_id = repo.create_project('p')['id']
    other_project = repo.create_project('other')['id']
    first = repo.put_record(
        project_id, {'id': 'record', 'kind': 'entity', 'text': 'old'})
    repo.put_record(
        project_id, {'id': 'record', 'kind': 'entity', 'text': 'new'}, expected_version=1)

    exact = repo.get_record_version(project_id, first['version_id'])

    assert exact['text'] == 'old'
    assert exact['version'] == 1
    assert exact['superseded_at'] is not None
    with pytest.raises(KeyError):
        repo.get_record_version(other_project, first['version_id'])
