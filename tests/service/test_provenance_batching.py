"""Bounded PostgreSQL writes preserve the frozen provenance edge contract."""
import json
from math import ceil

import pytest

from knowledge_service.repository import Repository
from knowledge_service.repository.connection import IntegrityError
from test_provenance_repository import _edge


BATCH_LIMIT = 1000


def _edges(count, activity_id='activity'):
    return [
        _edge(
            f'edge-{index}', activity_id, f'operation:{index}',
            'contains-operation', f'record-version:{index}', ordinal=index,
            payload={'index': index, 'nested': [True, False, None, {'文字': '值'}]})
        for index in range(count)
    ]


def _logical_edges(edges):
    return {
        (edge['activity_id'], edge['source_ref'], edge['relation'],
         edge['target_ref'], edge['ordinal']): json.dumps(
             edge['payload'], ensure_ascii=False, sort_keys=True)
        for edge in edges
    }


@pytest.mark.parametrize('edge_count', [1, 100, BATCH_LIMIT + 1])
def test_completion_sql_grows_by_unique_edge_batches(tmp_path, monkeypatch, edge_count):
    repo = Repository(tmp_path / 'batch-growth.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'activity', 'ontology_publish', {})
    edges = _edges(edge_count)
    # Logical duplicates may carry regenerated surrogate IDs and timestamps.
    proposed = [*edges, {**edges[0], 'id': 'retry-first'},
                {**edges[-1], 'id': 'retry-last',
                 'created_at': '2025-01-01T00:00:00Z'}]
    statements = []
    inserted_rows = []
    execute = repo._db.execute

    def counted(sql, params=()):
        statement = ' '.join(sql.split()).upper()
        statements.append(statement)
        cursor = execute(sql, params)
        if statement.startswith('INSERT INTO PROVENANCE_EDGES'):
            inserted_rows.append(cursor.rowcount)
        return cursor

    monkeypatch.setattr(repo._db, 'execute', counted)
    completed = repo.complete_provenance_activity(
        project_id, 'activity', {'result': 'done'}, edges=proposed)
    first_statements = list(statements)
    batches = ceil(edge_count / BATCH_LIMIT)
    assert len(first_statements) <= 6 + batches
    assert sum(sql.startswith('INSERT INTO PROVENANCE_EDGES')
               for sql in first_statements) == batches
    assert sum(sql.startswith('SELECT') and 'FROM PROVENANCE_EDGES' in sql
               for sql in first_statements) == 1
    assert inserted_rows == [min(BATCH_LIMIT, edge_count - offset)
                             for offset in range(0, edge_count, BATCH_LIMIT)]
    persisted = repo.list_provenance_edges(project_id, activity_id='activity')
    assert _logical_edges(persisted) == _logical_edges(edges)

    statements.clear()
    retried = [{**edge, 'id': f'regenerated-{index}'}
               for index, edge in enumerate(reversed(edges))]
    assert repo.complete_provenance_activity(
        project_id, 'activity', {'result': 'done'}, edges=retried) == completed
    assert len(statements) <= 4
    assert not any(sql.startswith('INSERT') for sql in statements)
    assert repo.list_provenance_edges(project_id, activity_id='activity') == persisted


@pytest.mark.parametrize('collision', ['same-batch', 'later-batch', 'other-project'])
def test_new_edge_id_collisions_are_domain_conflicts_and_atomic(tmp_path, collision):
    repo = Repository(tmp_path / 'batch-id-conflict.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'activity', 'ontology_publish', {})
    count = BATCH_LIMIT + 1 if collision == 'later-batch' else 2
    edges = _edges(count)
    if collision == 'other-project':
        other_project = repo.create_project('other')['id']
        repo.begin_provenance_activity(other_project, 'other', 'answer', {})
        repo.complete_provenance_activity(
            other_project, 'other', {},
            edges=[_edge('occupied', 'other', 'answer:other', 'used', 'retrieval:x')])
        edges[-1]['id'] = 'occupied'
    else:
        edges[-1]['id'] = edges[0]['id']

    with pytest.raises(ValueError, match='溯源边 ID 已存在'):
        repo.complete_provenance_activity(
            project_id, 'activity', {'done': True}, edges=edges)
    assert repo.get_provenance_activity(project_id, 'activity')['status'] == 'running'
    assert repo.list_provenance_edges(project_id, activity_id='activity') == []
    if collision == 'other-project':
        assert [row['id'] for row in repo.list_provenance_edges(other_project)] == ['occupied']


def test_later_batch_integrity_failure_rolls_back_without_id_conflict(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'batch-integrity-rollback.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'activity', 'ontology_publish', {})
    edges = _edges(BATCH_LIMIT + 1)
    execute = repo._db.execute
    inserted = []

    def observe(sql, params=()):
        is_insert = ' '.join(sql.split()).upper().startswith('INSERT INTO PROVENANCE_EDGES')
        if is_insert and params[0] == edges[-1]['id']:
            # Fail a real non-PK integrity constraint after batch one succeeds.
            params = list(params)
            params[4] = 'invalid-relation'
        cursor = execute(sql, params)
        if is_insert:
            inserted.append(cursor.rowcount)
        return cursor

    monkeypatch.setattr(repo._db, 'execute', observe)
    from psycopg.errors import CheckViolation

    with pytest.raises(CheckViolation) as raised:
        repo.complete_provenance_activity(
            project_id, 'activity', {'done': True}, edges=edges)
    assert raised.value.sqlstate == '23514'
    assert inserted == [BATCH_LIMIT]
    assert repo.get_provenance_activity(project_id, 'activity')['status'] == 'running'
    assert repo.list_provenance_edges(project_id, activity_id='activity') == []


def test_reconcile_retains_project_activity_foreign_key_error(tmp_path):
    repo = Repository(tmp_path / 'batch-foreign-key.sqlite')
    first = repo.create_project('first')['id']
    second = repo.create_project('second')['id']
    repo.begin_provenance_activity(first, 'activity', 'answer', {})

    with pytest.raises(IntegrityError) as raised:
        with repo._transaction():
            repo._provenance._reconcile_activity_edges(
                second, 'activity', _edges(2), allow_insert=True)
    assert raised.value.sqlstate == '23503'
    assert repo.list_provenance_edges(first) == []
    assert repo.list_provenance_edges(second) == []


def test_reconcile_rejects_other_activity_before_writing(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'batch-activity-owner.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'activity', 'answer', {})
    repo.begin_provenance_activity(project_id, 'other', 'answer', {})
    edges = [*_edges(BATCH_LIMIT), *_edges(1, activity_id='other')]
    inserts = []
    execute = repo._db.execute

    def observe(sql, params=()):
        if ' '.join(sql.split()).upper().startswith('INSERT INTO PROVENANCE_EDGES'):
            inserts.append(sql)
        return execute(sql, params)

    monkeypatch.setattr(repo._db, 'execute', observe)
    with pytest.raises(ValueError, match='不属于该活动'):
        repo.complete_provenance_activity(project_id, 'activity', {}, edges=edges)
    assert inserts == []
    assert repo.get_provenance_activity(project_id, 'activity')['status'] == 'running'
    assert repo.list_provenance_edges(project_id) == []


def test_completion_inserts_only_missing_edges_across_batch_boundary(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'batch-only-missing.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'activity', 'ontology_publish', {})
    edges = _edges(BATCH_LIMIT + 1)
    with repo._transaction():
        frozen = repo._provenance._insert_edge(project_id, edges[0])
    execute = repo._db.execute
    inserted = []

    def observe(sql, params=()):
        cursor = execute(sql, params)
        if ' '.join(sql.split()).upper().startswith('INSERT INTO PROVENANCE_EDGES'):
            inserted.append(cursor.rowcount)
        return cursor

    monkeypatch.setattr(repo._db, 'execute', observe)
    # An occupied surrogate is ignored when replaying the same logical edge.
    proposed = [{**edges[0], 'id': edges[1]['id']}, *edges[1:]]
    repo.complete_provenance_activity(project_id, 'activity', {}, edges=proposed)
    assert inserted == [BATCH_LIMIT]
    persisted = repo.list_provenance_edges(project_id, activity_id='activity')
    assert _logical_edges(persisted) == _logical_edges(edges)
    assert persisted[0] == frozen


@pytest.mark.parametrize('mutation', ['missing', 'payload'])
def test_frozen_edge_set_is_validated_before_any_missing_insert(tmp_path, monkeypatch, mutation):
    repo = Repository(tmp_path / 'batch-frozen-set.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'activity', 'ontology_publish', {})
    edges = _edges(BATCH_LIMIT + 1)
    with repo._transaction():
        frozen = repo._provenance._insert_edge(project_id, edges[-1])
    proposed = edges[:-1]
    if mutation == 'payload':
        changed = {**edges[-1], 'payload': {**edges[-1]['payload'], 'index': True}}
        proposed = [*proposed, changed]
    execute = repo._db.execute
    inserted = []

    def observe(sql, params=()):
        if ' '.join(sql.split()).upper().startswith('INSERT INTO PROVENANCE_EDGES'):
            inserted.append(sql)
        return execute(sql, params)

    monkeypatch.setattr(repo._db, 'execute', observe)
    with pytest.raises(ValueError, match='冲突'):
        repo.complete_provenance_activity(project_id, 'activity', {}, edges=proposed)
    assert inserted == []
    assert repo.get_provenance_activity(project_id, 'activity')['status'] == 'running'
    assert repo.list_provenance_edges(project_id, activity_id='activity') == [frozen]


def test_logical_uniqueness_failure_is_not_translated_to_id_conflict(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'batch-logical-uniqueness.sqlite')
    project_id = repo.create_project('p')['id']
    repo.begin_provenance_activity(project_id, 'activity', 'ontology_publish', {})
    execute = repo._db.execute

    def collide_after_read(sql, params=()):
        if ' '.join(sql.split()).upper().startswith('INSERT INTO PROVENANCE_EDGES'):
            # Simulate a logical-key insertion after the reconciliation read.
            execute(
                '''INSERT INTO provenance_edges
                   (id,project_id,activity_id,source_ref,relation,target_ref,
                    ordinal,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                ['different-id', *params[1:9]])
        return execute(sql, params)

    monkeypatch.setattr(repo._db, 'execute', collide_after_read)
    with pytest.raises(IntegrityError) as raised:
        repo.complete_provenance_activity(project_id, 'activity', {}, edges=_edges(1))
    assert raised.value.sqlstate == '23505'
    assert raised.value.diag.constraint_name != 'provenance_edges_pkey'
    assert repo.get_provenance_activity(project_id, 'activity')['status'] == 'running'
    assert repo.list_provenance_edges(project_id, activity_id='activity') == []
