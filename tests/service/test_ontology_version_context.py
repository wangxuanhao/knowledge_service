from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.reclassify import Reclassify


TTL = '''
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Thing a owl:Class .
ex:knows a owl:ObjectProperty .
ex:name a owl:DatatypeProperty .
'''


def _system(tmp_path, name='ontology-context.sqlite'):
    app = create_app(tmp_path / name, HashingEncoder())
    client = TestClient(app)
    client.__enter__()
    project_id = client.post('/api/projects', json={
        'name': '本体知识上下文', 'use_default_ontology': False,
    }).json()['id']
    repository = app.state.service.repository
    ontology = repository.save_ontology(project_id, TTL, {})
    return client, app.state.service, project_id, ontology


def _context_path(project_id, ontology_id):
    return f'/api/projects/{project_id}/ontologies/{ontology_id}/knowledge-context'


def test_current_context_has_complete_shape_and_skips_plan(tmp_path, monkeypatch):
    client, service, project_id, ontology = _system(tmp_path)
    service.write(project_id, [{
        'id': 'e1', 'kind': 'entity', 'type': 'Thing', 'text': '甲',
        'ontology_id': ontology['id'],
    }])

    def forbidden_plan(self, requested_project_id):
        raise AssertionError('current ontology must not build a migration plan')

    monkeypatch.setattr(Reclassify, 'plan', forbidden_plan)
    response = client.get(_context_path(project_id, ontology['id']))

    assert response.status_code == 200, response.text
    assert response.json() == {
        'ontology': {
            'id': ontology['id'], 'version': 1, 'is_current': True,
            'created_at': ontology['created_at'],
        },
        'current_bound': {
            'total': 1,
            'by_kind': {'entity': 1, 'relation': 0, 'attribute': 0},
        },
        'migration': {'pending': 0, 'blocked': 0},
        'history': {'revision_count': 1, 'record_count': 1, 'migrated_away': 0},
    }


def test_bound_counts_kinds_after_ontology_scope_removes_dangling_facts(tmp_path):
    client, service, project_id, first = _system(tmp_path, 'bound-kinds.sqlite')
    repository = service.repository
    second = repository.save_ontology(project_id, TTL, {})
    rows = [
        {'id': 'a', 'kind': 'entity', 'type': 'Thing', 'text': '甲',
         'ontology_id': first['id']},
        {'id': 'b', 'kind': 'entity', 'type': 'Thing', 'text': '乙',
         'ontology_id': first['id']},
        {'id': 'other', 'kind': 'entity', 'type': 'Thing', 'text': '新版本端点',
         'ontology_id': second['id']},
        {'id': 'local-r', 'kind': 'relation', 'type': 'knows', 'text': '本地关系',
         'subject_id': 'a', 'object_id': 'b', 'ontology_id': first['id']},
        {'id': 'cross-r', 'kind': 'relation', 'type': 'knows', 'text': '跨版本关系',
         'subject_id': 'a', 'object_id': 'other', 'ontology_id': first['id']},
        {'id': 'local-a', 'kind': 'attribute', 'type': 'name', 'text': '本地属性',
         'subject_id': 'a', 'value': 'A',
         'datatype': 'http://www.w3.org/2001/XMLSchema#string',
         'ontology_id': first['id']},
        {'id': 'cross-a', 'kind': 'attribute', 'type': 'name', 'text': '跨版本属性',
         'subject_id': 'other', 'value': 'X',
         'datatype': 'http://www.w3.org/2001/XMLSchema#string',
         'ontology_id': first['id']},
        {'id': 'deleted', 'kind': 'entity', 'type': 'Thing', 'text': '已删',
         'ontology_id': first['id'], 'metadata': {'_deleted': True}},
    ]
    for row in rows:
        repository.put_record(project_id, row)

    response = client.get(_context_path(project_id, first['id']))

    assert response.status_code == 200, response.text
    assert response.json()['current_bound'] == {
        'total': 4,
        'by_kind': {'entity': 2, 'relation': 1, 'attribute': 1},
    }


def test_history_counts_revisions_records_and_records_migrated_away(tmp_path):
    client, service, project_id, first = _system(tmp_path, 'history.sqlite')
    repository = service.repository
    second = repository.save_ontology(project_id, TTL, {})
    repository.put_record(project_id, {
        'id': 'moving', 'kind': 'entity', 'type': 'Thing', 'text': '第一稿',
        'ontology_id': first['id'],
    })
    repository.put_record(project_id, {
        'id': 'moving', 'kind': 'entity', 'type': 'Thing', 'text': '第二稿',
        'ontology_id': first['id'],
    }, expected_version=1)
    repository.put_record(project_id, {
        'id': 'moving', 'kind': 'entity', 'type': 'Thing', 'text': '已经迁走',
        'ontology_id': second['id'],
    }, expected_version=2)
    repository.put_record(project_id, {
        'id': 'staying', 'kind': 'entity', 'type': 'Thing', 'text': '仍在旧版',
        'ontology_id': first['id'],
    })

    body = client.get(_context_path(project_id, first['id'])).json()

    assert body['ontology'] == {
        'id': first['id'], 'version': 1, 'is_current': False,
        'created_at': first['created_at'],
    }
    assert body['history'] == {
        'revision_count': 3, 'record_count': 2, 'migrated_away': 1,
    }
    assert body['current_bound']['total'] == 1


def test_migrated_away_uses_only_current_visible_knowledge_rows(tmp_path):
    client, service, project_id, first = _system(tmp_path, 'visible-history.sqlite')
    repository = service.repository
    second = repository.save_ontology(project_id, TTL, {})
    repository.put_record(project_id, {
        'id': 'deleted', 'kind': 'entity', 'type': 'Thing', 'text': '旧知识',
        'ontology_id': first['id'],
    })
    repository.put_record(project_id, {
        'id': 'deleted', 'kind': 'entity', 'type': 'Thing', 'text': '已软删除',
        'ontology_id': second['id'], 'metadata': {'_deleted': True},
    }, expected_version=1)
    repository.put_record(project_id, {
        'id': 'audit', 'kind': 'entity', 'type': 'Thing', 'text': '旧知识',
        'ontology_id': first['id'],
    })
    repository.put_record(project_id, {
        'id': 'audit', 'kind': 'entity', 'type': 'Thing', 'text': '审计记录',
        'ontology_id': second['id'], 'metadata': {'_audit': True},
    }, expected_version=1)
    repository.put_record(project_id, {
        'id': 'document', 'kind': 'document', 'type': 'text', 'text': '旧文档',
        'ontology_id': first['id'],
    })
    repository.put_record(project_id, {
        'id': 'document', 'kind': 'document', 'type': 'text', 'text': '新文档',
        'ontology_id': second['id'],
    }, expected_version=1)

    body = client.get(_context_path(project_id, first['id'])).json()

    assert body['history']['record_count'] == 3
    assert body['history']['migrated_away'] == 0


def test_migration_counts_group_record_counts_for_only_the_target(tmp_path, monkeypatch):
    client, service, project_id, first = _system(tmp_path, 'migration-counts.sqlite')
    second = service.repository.save_ontology(project_id, TTL, {})

    def plan(self, requested_project_id):
        assert requested_project_id == project_id
        return {'groups': [
            {'from_ontology_id': first['id'], 'migratable': True, 'record_count': 4},
            {'from_ontology_id': first['id'], 'migratable': False, 'record_count': 3},
            {'from_ontology_id': second['id'], 'migratable': True, 'record_count': 99},
        ]}

    monkeypatch.setattr(Reclassify, 'plan', plan)
    body = client.get(_context_path(project_id, first['id'])).json()

    assert body['migration'] == {'pending': 4, 'blocked': 3}


def test_unreadable_migration_plan_does_not_hide_other_context(tmp_path, monkeypatch):
    client, service, project_id, first = _system(tmp_path, 'plan-failure.sqlite')
    service.repository.save_ontology(project_id, TTL, {})
    service.repository.put_record(project_id, {
        'id': 'old', 'kind': 'entity', 'type': 'Thing', 'text': '旧知识',
        'ontology_id': first['id'],
    })

    def broken_plan(self, requested_project_id):
        raise RuntimeError('plan temporarily unreadable')

    monkeypatch.setattr(Reclassify, 'plan', broken_plan)
    response = client.get(_context_path(project_id, first['id']))

    assert response.status_code == 200, response.text
    assert response.json()['migration'] == {'pending': None, 'blocked': None}
    assert response.json()['current_bound']['total'] == 1
    assert response.json()['history']['revision_count'] == 1


def test_context_moves_from_old_to_new_after_reclassification(tmp_path):
    client, service, project_id, first = _system(tmp_path, 'migration-change.sqlite')
    repository = service.repository
    for record_id in ('one', 'two'):
        repository.put_record(project_id, {
            'id': record_id, 'kind': 'entity', 'type': 'Thing', 'text': record_id,
            'ontology_id': first['id'],
        })
    second = repository.save_ontology(project_id, TTL, {})

    old_before = client.get(_context_path(project_id, first['id'])).json()
    new_before = client.get(_context_path(project_id, second['id'])).json()
    plan = Reclassify(service).plan(project_id)
    keys = [group['key'] for group in plan['groups'] if group['migratable']]

    result = Reclassify(service).apply(project_id, keys, 'tester')

    assert result['migrated'] == 2
    old_after = client.get(_context_path(project_id, first['id'])).json()
    new_after = client.get(_context_path(project_id, second['id'])).json()
    assert old_before['current_bound']['total'] == 2
    assert old_before['history']['migrated_away'] == 0
    assert new_before['current_bound']['total'] == 0
    assert old_after['current_bound']['total'] == 0
    assert old_after['history'] == {
        'revision_count': 2, 'record_count': 2, 'migrated_away': 2,
    }
    assert new_after['current_bound']['total'] == 2


def test_unknown_and_cross_project_ontologies_are_404_without_history_leak(tmp_path):
    client, service, project_id, _ = _system(tmp_path, 'project-scope.sqlite')
    repository = service.repository
    other_project = repository.create_project('其他项目')
    foreign = repository.save_ontology(other_project['id'], TTL, {})
    repository.put_record(project_id, {
        'id': 'ghost-record', 'kind': 'entity', 'type': 'Thing', 'text': '幽灵版本',
        'ontology_id': 'ghost-ontology',
    })

    foreign_response = client.get(_context_path(project_id, foreign['id']))
    unknown_response = client.get(_context_path(project_id, 'ghost-ontology'))

    assert foreign_response.status_code == 404
    assert unknown_response.status_code == 404
    assert set(foreign_response.json()) == {'detail'}
    assert set(unknown_response.json()) == {'detail'}


def test_every_context_read_runs_inside_one_repository_snapshot(tmp_path, monkeypatch):
    client, service, project_id, first = _system(tmp_path, 'snapshot-spy.sqlite')
    repository = service.repository
    repository.put_record(project_id, {
        'id': 'old', 'kind': 'entity', 'type': 'Thing', 'text': '旧知识',
        'ontology_id': first['id'],
    })
    repository.save_ontology(project_id, TTL, {})
    state = {'inside': False, 'entries': 0}
    scopes = []
    original_snapshot = repository.read_snapshot
    original_list = repository.list_ontologies
    original_history = repository.ontology_record_history
    original_scoped = service.scoped
    original_plan = Reclassify.plan

    @contextmanager
    def watched_snapshot():
        state['entries'] += 1
        with original_snapshot():
            state['inside'] = True
            try:
                yield
            finally:
                state['inside'] = False

    def watched_list(requested_project_id):
        assert state['inside'] and repository._db.in_transaction
        return original_list(requested_project_id)

    def watched_history(requested_project_id, ontology_id, current_rows):
        assert state['inside'] and repository._db.in_transaction
        assert all(row['kind'] in {'entity', 'relation', 'attribute'}
                   for row in current_rows)
        return original_history(requested_project_id, ontology_id, current_rows)

    def watched_scoped(requested_project_id, scope):
        assert state['inside'] and repository._db.in_transaction
        scopes.append(dict(scope))
        return original_scoped(requested_project_id, scope)

    def watched_plan(reclassify, requested_project_id):
        assert state['inside'] and repository._db.in_transaction
        return original_plan(reclassify, requested_project_id)

    monkeypatch.setattr(repository, 'read_snapshot', watched_snapshot)
    monkeypatch.setattr(repository, 'list_ontologies', watched_list)
    monkeypatch.setattr(repository, 'ontology_record_history', watched_history)
    monkeypatch.setattr(service, 'scoped', watched_scoped)
    monkeypatch.setattr(Reclassify, 'plan', watched_plan)

    response = client.get(_context_path(project_id, first['id']))

    assert response.status_code == 200, response.text
    assert state == {'inside': False, 'entries': 1}
    assert any(scope.get('ontology_scope') == 'ids' for scope in scopes)
    assert any(scope.get('ontology_scope', 'all') == 'all' for scope in scopes)


def test_read_snapshot_propagates_exceptions_and_closes_transaction(tmp_path):
    client, service, _, _ = _system(tmp_path, 'snapshot-error.sqlite')
    repository = service.repository

    with pytest.raises(RuntimeError, match='read failed'):
        with repository.read_snapshot():
            assert repository._db.in_transaction
            raise RuntimeError('read failed')

    assert not repository._db.in_transaction


def test_read_snapshot_uses_repeatable_read_without_changing_write_transactions(
        tmp_path, monkeypatch):
    client, service, _, _ = _system(tmp_path, 'snapshot-isolation.sqlite')
    repository = service.repository
    statements = []
    original_execute = repository._db.execute

    def watched_execute(statement, params=()):
        statements.append(statement)
        return original_execute(statement, params)

    monkeypatch.setattr(repository._db, 'execute', watched_execute)

    with repository.read_snapshot():
        pass
    with repository._transaction():
        pass

    begin_statements = [statement for statement in statements
                        if statement.startswith('BEGIN')]
    assert begin_statements == [
        'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY', 'BEGIN',
    ]
