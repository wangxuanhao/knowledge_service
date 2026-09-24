import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import knowledge_service.repository as repository_module
from knowledge_service.repository import Repository
from knowledge_service.repository.core import _supersession_cycle


def _draft(project_id, *, draft_id='draft-1', base_ontology_id=None):
    return {
        'id': draft_id,
        'project_id': project_id,
        'base_ontology_id': base_ontology_id,
        'source_kind': 'manual',
        'status': 'editing',
        'revision': 1,
        'title': 'Partner hierarchy',
        'summary': 'Add a governed parent edge',
        'source_context': {'actor': 'local-user', 'document_refs': []},
    }


def _operation(operation_id, *, supersedes=None, fingerprint=None, created_at=None):
    return {
        'id': operation_id,
        'action': 'add_parent',
        'target_iri': 'urn:term:partner',
        'before': None,
        'after': {'parent_iri': 'urn:term:participant'},
        'evidence': [{'ref': 'document-version:1'}],
        'impact': {'facts': 2},
        'validation': {'conforms': True},
        'risk': 'medium',
        'fingerprint': fingerprint or f'sha256:{operation_id}',
        'reason': 'Explicit hierarchy',
        'supersedes_operation_id': supersedes,
        **({'created_at': created_at} if created_at else {}),
    }


def test_supersession_cycle_check_is_linear_for_long_valid_history():
    rows = [
        {'id': f'op-{index}', 'supersedes_operation_id': (
            f'op-{index - 1}' if index else None)}
        for index in range(10_000)
    ]

    started_at = time.perf_counter()
    cycle = _supersession_cycle(rows, 'supersedes_operation_id')

    assert cycle is None
    assert time.perf_counter() - started_at < 3


def _decision(decision_id, operation_id, fingerprint, *, supersedes=None,
              created_at=None):
    return {
        'id': decision_id,
        'operation_id': operation_id,
        'operation_fingerprint': fingerprint,
        'action': 'approve',
        'reason': 'Reviewed',
        'actor': 'reviewer',
        'supersedes_decision_id': supersedes,
        **({'created_at': created_at} if created_at else {}),
    }


def _insert_operation_sql(repo, project_id, identifier, supersedes):
    repo._db.execute(
        '''INSERT INTO ontology_operations
           (id,project_id,draft_id,action,target_iri,before_json,after_json,
            evidence_json,impact_json,validation_json,risk,fingerprint,reason,
            supersedes_operation_id,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (identifier, project_id, 'draft-1', 'add_parent', f'urn:{identifier}',
         'null', '{}', '[]', '{}', '{}', 'low', f'sha256:{identifier}', 'test',
         supersedes, '2026-01-01T00:00:00.000000Z'))


def _insert_decision_sql(repo, project_id, identifier, supersedes):
    repo._db.execute(
        '''INSERT INTO ontology_review_decisions
           (id,project_id,draft_id,operation_id,operation_fingerprint,
            action,reason,actor,supersedes_decision_id,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (identifier, project_id, 'draft-1', 'op-valid', 'sha256:op-valid',
         'approve', 'test', 'reviewer', supersedes,
         '2026-01-01T00:00:00.000000Z'))


def test_migration_14_upgrades_v13_database_with_constrained_append_only_tables(
        tmp_path, monkeypatch):
    path = tmp_path / 'migration-14.sqlite'
    migrations = repository_module._SCHEMA_MIGRATIONS
    assert migrations[-1][0] == 14
    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations[:-1])
    legacy = Repository(path)
    project_id = legacy.create_project('legacy')['id']
    legacy.close()

    monkeypatch.setattr(repository_module, '_SCHEMA_MIGRATIONS', migrations)
    repo = Repository(path)
    tables = {
        row[0] for row in repo._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        'ontology_drafts', 'ontology_operations', 'ontology_review_decisions',
        'ontology_publish_requests',
    } <= tables
    assert repo._db.execute(
        'SELECT COUNT(*) FROM schema_migrations WHERE version=14'
    ).fetchone()[0] == 1
    draft_columns = {
        row['name']: row for row in repo._db.execute('PRAGMA table_info(ontology_drafts)')
    }
    assert draft_columns['base_ontology_id']['notnull'] == 0
    draft_fks = repo._db.execute('PRAGMA foreign_key_list(ontology_drafts)').fetchall()
    assert any(row['from'] == 'project_id' and row['table'] == 'projects'
               and row['on_delete'] == 'CASCADE' for row in draft_fks)

    draft = repo._ontology_drafts.create(project_id, _draft(project_id))
    operations = repo._ontology_drafts.append_operations(
        project_id, draft['id'], [_operation('op-1')])
    decisions = repo._ontology_drafts.append_decisions(
        project_id, draft['id'], [_decision('decision-1', 'op-1', 'sha256:op-1')])
    repo._ontology_drafts.append_operations(
        project_id, draft['id'], [_operation('op-unreviewed')])
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        repo._db.execute("UPDATE ontology_operations SET risk='high' WHERE id='op-1'")
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        repo._db.execute(
            "UPDATE ontology_review_decisions SET reason='changed' WHERE id='decision-1'"
        )
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        repo._db.execute("DELETE FROM ontology_operations WHERE id='op-unreviewed'")
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        repo._db.execute(
            "DELETE FROM ontology_review_decisions WHERE id='decision-1'"
        )
    assert repo._db.execute('PRAGMA recursive_triggers').fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        repo._db.execute(
            '''INSERT INTO ontology_operations
               SELECT * FROM ontology_operations WHERE id='op-unreviewed' ''')
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        repo._db.execute(
            '''INSERT OR REPLACE INTO ontology_operations
               SELECT * FROM ontology_operations WHERE id='op-unreviewed' ''')
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        repo._db.execute(
            '''INSERT INTO ontology_review_decisions
               SELECT * FROM ontology_review_decisions WHERE id='decision-1' ''')
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        repo._db.execute(
            '''INSERT OR REPLACE INTO ontology_review_decisions
               SELECT * FROM ontology_review_decisions WHERE id='decision-1' ''')
    assert operations[0]['before'] is None
    assert decisions[0]['operation_fingerprint'] == 'sha256:op-1'

    with repo._transaction():
        values = ('publish-1', project_id, draft['id'], 'key-1', 'sha256:request', None,
                  '2026-01-01T00:00:00.000000Z', None)
        repo._db.execute(
            '''INSERT INTO ontology_publish_requests
               (id,project_id,draft_id,idempotency_key,request_hash,result_ontology_id,
                created_at,completed_at) VALUES (?,?,?,?,?,?,?,?)''', values)
        with pytest.raises(sqlite3.IntegrityError):
            repo._db.execute(
                '''INSERT INTO ontology_publish_requests
                   (id,project_id,draft_id,idempotency_key,request_hash,result_ontology_id,
                    created_at,completed_at) VALUES (?,?,?,?,?,?,?,?)''',
                ('publish-2', *values[1:]))


def test_v14_reopen_repairs_missing_delete_guards_without_consuming_migration_15(
        tmp_path):
    path = tmp_path / 'v14-delete-guard-repair.sqlite'
    original = Repository(path)
    project_id = original.create_project('old v14')['id']
    original._ontology_drafts.create(project_id, _draft(project_id))
    original._ontology_drafts.append_operations(
        project_id, 'draft-1', [_operation('op-old-v14')])
    with original._transaction():
        original._db.execute('DROP TRIGGER ontology_operations_delete_immutable')
        original._db.execute('DROP TRIGGER ontology_review_decisions_delete_immutable')
        original._db.execute('DROP TRIGGER ontology_operations_insert_cycle_guard')
        original._db.execute(
            'DROP TRIGGER ontology_review_decisions_insert_cycle_guard')
    original.close()

    reopened = Repository(path)

    assert reopened._db.execute(
        'SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 14
    triggers = {
        row[0] for row in reopened._db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert {
        'ontology_operations_delete_immutable',
        'ontology_review_decisions_delete_immutable',
        'ontology_operations_insert_cycle_guard',
        'ontology_review_decisions_insert_cycle_guard',
    } <= triggers
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        reopened._db.execute(
            "DELETE FROM ontology_operations WHERE id='op-old-v14'")


def test_v14_reopen_repairs_self_supersession_and_round_trips(tmp_path):
    path = tmp_path / 'v14-self-supersession.sqlite'
    old = Repository(path)
    project_id = old.create_project('self supersession')['id']
    old._ontology_drafts.create(project_id, _draft(project_id))
    with old._transaction():
        old._db.execute('DROP TRIGGER IF EXISTS ontology_operations_insert_immutable')
        old._db.execute('DROP TRIGGER IF EXISTS ontology_operations_insert_cycle_guard')
        old._db.execute(
            '''INSERT INTO ontology_operations
               (id,project_id,draft_id,action,target_iri,before_json,after_json,
                evidence_json,impact_json,validation_json,risk,fingerprint,reason,
                supersedes_operation_id,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('op-self-v14', project_id, 'draft-1', 'add_parent', 'urn:self',
             'null', '{}', '[]', '{}', '{}', 'low', 'sha256:self', 'legacy',
             'op-self-v14', '2026-01-01T00:00:00.000000Z'))
        old._db.execute('DROP TRIGGER IF EXISTS ontology_review_decisions_insert_immutable')
        old._db.execute(
            'DROP TRIGGER IF EXISTS ontology_review_decisions_insert_cycle_guard')
        old._db.execute(
            '''INSERT INTO ontology_review_decisions
               (id,project_id,draft_id,operation_id,operation_fingerprint,
                action,reason,actor,supersedes_decision_id,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)''',
            ('decision-self-v14', project_id, 'draft-1', 'op-self-v14',
             'sha256:self', 'approve', 'legacy', 'reviewer', 'decision-self-v14',
             '2026-01-01T00:00:01.000000Z'))
    old.close()

    reopened = Repository(path)
    history = reopened._ontology_drafts.export(project_id)
    assert history['operations'][0]['supersedes_operation_id'] is None
    assert history['decisions'][0]['supersedes_decision_id'] is None
    assert [item['id'] for item in reopened._ontology_drafts.effective_operations(
        project_id, 'draft-1')] == ['op-self-v14']

    snapshot = reopened.export_projection(project_id)
    restored = Repository(tmp_path / 'v14-self-restored.sqlite')
    restored.restore_projection(snapshot)
    assert restored.export_projection(project_id)['governance']['ontology'] == (
        snapshot['governance']['ontology'])


@pytest.mark.parametrize('ledger', ['operations', 'decisions'])
def test_v14_reopen_rejects_ambiguous_two_node_supersession_cycles(
        tmp_path, ledger):
    path = tmp_path / f'v14-{ledger}-cycle.sqlite'
    old = Repository(path)
    project_id = old.create_project(f'{ledger} cycle')['id']
    old._ontology_drafts.create(project_id, _draft(project_id))
    old._ontology_drafts.append_operations(
        project_id, 'draft-1', [_operation('op-valid-cycle')])
    with old._transaction():
        old._db.execute('DROP TRIGGER IF EXISTS ontology_operations_insert_cycle_guard')
        old._db.execute(
            'DROP TRIGGER IF EXISTS ontology_review_decisions_insert_cycle_guard')
    old.close()

    with sqlite3.connect(path) as raw:
        assert raw.execute('PRAGMA foreign_keys').fetchone()[0] == 0
        if ledger == 'operations':
            for identifier, supersedes in (
                    ('op-cycle-a', 'op-cycle-b'), ('op-cycle-b', 'op-cycle-a')):
                raw.execute(
                    '''INSERT INTO ontology_operations
                       (id,project_id,draft_id,action,target_iri,before_json,after_json,
                        evidence_json,impact_json,validation_json,risk,fingerprint,reason,
                        supersedes_operation_id,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identifier, project_id, 'draft-1', 'add_parent',
                     f'urn:{identifier}', 'null', '{}', '[]', '{}', '{}', 'low',
                     f'sha256:{identifier}', 'legacy', supersedes,
                     '2026-01-01T00:00:00.000000Z'))
        else:
            for identifier, supersedes in (
                    ('decision-cycle-a', 'decision-cycle-b'),
                    ('decision-cycle-b', 'decision-cycle-a')):
                raw.execute(
                    '''INSERT INTO ontology_review_decisions
                       (id,project_id,draft_id,operation_id,operation_fingerprint,
                        action,reason,actor,supersedes_decision_id,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (identifier, project_id, 'draft-1', 'op-valid-cycle',
                     'sha256:op-valid-cycle', 'approve', 'legacy', 'reviewer',
                     supersedes, '2026-01-01T00:00:00.000000Z'))

    with pytest.raises(ValueError, match='循环'):
        Repository(path)


def test_store_rejects_self_supersession_and_two_node_cycles(tmp_path):
    repo = Repository(tmp_path / 'supersession-cycles.sqlite')
    project_id = repo.create_project('cycles')['id']
    repo._ontology_drafts.create(project_id, _draft(project_id))

    with pytest.raises(ValueError, match='不能替代自身'):
        repo._ontology_drafts.append_operations(
            project_id, 'draft-1', [_operation('op-self', supersedes='op-self')])
    assert repo._ontology_drafts.effective_operations(project_id, 'draft-1') == []

    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo._ontology_drafts.append_operations(project_id, 'draft-1', [
            _operation('op-cycle-a', supersedes='op-cycle-b'),
            _operation('op-cycle-b', supersedes='op-cycle-a'),
        ])
    assert repo._ontology_drafts.effective_operations(project_id, 'draft-1') == []

    repo._ontology_drafts.append_operations(
        project_id, 'draft-1', [_operation('op-valid')])
    with pytest.raises(ValueError, match='不能替代自身'):
        repo._ontology_drafts.append_decisions(project_id, 'draft-1', [
            _decision('decision-self', 'op-valid', 'sha256:op-valid',
                      supersedes='decision-self')])
    assert repo._ontology_drafts.export(project_id)['decisions'] == []

    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo._ontology_drafts.append_decisions(project_id, 'draft-1', [
            _decision('decision-cycle-a', 'op-valid', 'sha256:op-valid',
                      supersedes='decision-cycle-b'),
            _decision('decision-cycle-b', 'op-valid', 'sha256:op-valid',
                      supersedes='decision-cycle-a'),
        ])
    assert repo._ontology_drafts.export(project_id)['decisions'] == []


@pytest.mark.parametrize('ledger', ['operations', 'decisions'])
@pytest.mark.parametrize('cycle_size', [2, 3])
def test_append_rejects_deferred_supersession_cycles_atomically(
        tmp_path, ledger, cycle_size):
    repo = Repository(tmp_path / f'append-{ledger}-{cycle_size}-cycle.sqlite')
    project_id = repo.create_project('deferred append cycle')['id']
    repo._ontology_drafts.create(project_id, _draft(project_id))
    if ledger == 'decisions':
        repo._ontology_drafts.append_operations(
            project_id, 'draft-1', [_operation('op-valid')])

    identifiers = [f'{ledger}-{index}' for index in range(cycle_size)]
    if ledger == 'operations':
        rows = [
            _operation(identifier, supersedes=identifiers[(index + 1) % cycle_size])
            for index, identifier in enumerate(identifiers)
        ]
        append = repo._ontology_drafts.append_operations
    else:
        rows = [
            _decision(identifier, 'op-valid', 'sha256:op-valid',
                      supersedes=identifiers[(index + 1) % cycle_size])
            for index, identifier in enumerate(identifiers)
        ]
        append = repo._ontology_drafts.append_decisions

    repo._db.execute('PRAGMA defer_foreign_keys=ON')
    with pytest.raises(sqlite3.IntegrityError, match='cycle'):
        append(project_id, 'draft-1', rows)

    assert repo._db.execute(
        f'SELECT COUNT(*) FROM ontology_{"operations" if ledger == "operations" else "review_decisions"} '
        f'WHERE id IN ({",".join("?" for _ in identifiers)})', identifiers
    ).fetchone()[0] == 0


@pytest.mark.parametrize('ledger', ['operations', 'decisions'])
def test_direct_sql_rejects_deferred_supersession_cycle_atomically(tmp_path, ledger):
    repo = Repository(tmp_path / f'direct-{ledger}-cycle.sqlite')
    project_id = repo.create_project('direct SQL cycle')['id']
    repo._ontology_drafts.create(project_id, _draft(project_id))
    if ledger == 'decisions':
        repo._ontology_drafts.append_operations(
            project_id, 'draft-1', [_operation('op-valid')])
        insert = _insert_decision_sql
    else:
        insert = _insert_operation_sql

    identifiers = [f'{ledger}-a', f'{ledger}-b']
    repo._db.execute('PRAGMA defer_foreign_keys=ON')
    with pytest.raises(sqlite3.IntegrityError, match='cycle'):
        with repo._transaction():
            insert(repo, project_id, identifiers[0], identifiers[1])
            insert(repo, project_id, identifiers[1], identifiers[0])

    table = 'ontology_operations' if ledger == 'operations' else 'ontology_review_decisions'
    assert repo._db.execute(
        f'SELECT COUNT(*) FROM {table} WHERE id IN (?,?)', identifiers
    ).fetchone()[0] == 0


def test_deferred_acyclic_supersession_chains_export_and_restore(tmp_path):
    source = Repository(tmp_path / 'deferred-acyclic-source.sqlite')
    project_id = source.create_project('deferred acyclic')['id']
    source._ontology_drafts.create(project_id, _draft(project_id))

    source._db.execute('PRAGMA defer_foreign_keys=ON')
    source._ontology_drafts.append_operations(project_id, 'draft-1', [
        _operation('op-new', supersedes='op-middle'),
        _operation('op-middle', supersedes='op-root'),
        _operation('op-root'),
    ])
    source._db.execute('PRAGMA defer_foreign_keys=ON')
    source._ontology_drafts.append_decisions(project_id, 'draft-1', [
        _decision('decision-new', 'op-new', 'sha256:op-new',
                  supersedes='decision-middle'),
        _decision('decision-middle', 'op-new', 'sha256:op-new',
                  supersedes='decision-root'),
        _decision('decision-root', 'op-new', 'sha256:op-new'),
    ])

    snapshot = source.export_projection(project_id)
    target = Repository(tmp_path / 'deferred-acyclic-target.sqlite')
    target.restore_projection(snapshot)

    restored = target.export_projection(project_id)['governance']['ontology']
    expected = snapshot['governance']['ontology']
    assert restored['drafts'] == expected['drafts']
    assert restored['publish_requests'] == expected['publish_requests']
    for section in ('operations', 'decisions'):
        assert {row['id']: row for row in restored[section]} == {
            row['id']: row for row in expected[section]}
    assert [row['id'] for row in source._ontology_drafts.effective_operations(
        project_id, 'draft-1')] == ['op-new']


def test_store_crud_supersession_json_and_export_are_project_scoped(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('drafts')['id']
    other_id = repo.create_project('other')['id']
    first = repo._ontology_drafts.create(project_id, _draft(project_id))
    repo._ontology_drafts.create(other_id, _draft(other_id, draft_id='other-draft'))

    operations = repo._ontology_drafts.append_operations(project_id, first['id'], [
        _operation('op-old'),
        _operation('op-current', supersedes='op-old', fingerprint='sha256:current'),
    ])
    repo._ontology_drafts.append_decisions(project_id, first['id'], [
        _decision('decision-current', 'op-current', 'sha256:current'),
    ])
    updated = repo._ontology_drafts.compare_and_set(
        project_id, first['id'], 1,
        {'title': 'Reviewed hierarchy', 'status': 'submitted',
         'validation_report': {'conforms': True},
         'validation_fingerprint': 'sha256:validation'},
    )

    assert updated['revision'] == 2
    assert updated['validation_report'] == {'conforms': True}
    assert repo._ontology_drafts.get(project_id, first['id']) == updated
    assert repo._ontology_drafts.list(project_id, status='submitted') == [updated]
    assert [row['id'] for row in operations] == ['op-old', 'op-current']
    assert [row['id'] for row in repo._ontology_drafts.effective_operations(
        project_id, first['id'])] == ['op-current']
    exported = repo._ontology_drafts.export(project_id)
    assert [row['id'] for row in exported['drafts']] == ['draft-1']
    assert [row['id'] for row in exported['operations']] == ['op-old', 'op-current']
    assert [row['id'] for row in exported['decisions']] == ['decision-current']
    assert exported['publish_requests'] == []
    assert json.loads(json.dumps(exported, allow_nan=False)) == exported


def test_store_rejects_cross_draft_audit_references(tmp_path):
    repo = Repository(tmp_path / 'cross-draft.sqlite')
    project_id = repo.create_project('cross draft')['id']
    repo._ontology_drafts.create(project_id, _draft(project_id, draft_id='draft-a'))
    repo._ontology_drafts.create(project_id, _draft(project_id, draft_id='draft-b'))
    repo._ontology_drafts.append_operations(
        project_id, 'draft-a', [_operation('op-a')])

    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo._ontology_drafts.append_operations(
            project_id, 'draft-b', [_operation('op-b', supersedes='op-a')])
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo._ontology_drafts.append_decisions(
            project_id, 'draft-b', [_decision('decision-b', 'op-a', 'sha256:op-a')])

    repo._ontology_drafts.append_operations(
        project_id, 'draft-b', [_operation('op-b')])
    repo._ontology_drafts.append_decisions(
        project_id, 'draft-a', [_decision('decision-a', 'op-a', 'sha256:op-a')])
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo._ontology_drafts.append_decisions(project_id, 'draft-b', [
            _decision('decision-b', 'op-b', 'sha256:op-b', supersedes='decision-a')])


def test_store_rejects_cross_project_ontology_references_and_invalid_cas(tmp_path):
    repo = Repository(tmp_path / 'ownership.sqlite')
    first = repo.create_project('first')['id']
    second = repo.create_project('second')['id']
    other_ontology = repo.save_ontology(second, '', {})
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo._ontology_drafts.create(
            first, _draft(first, base_ontology_id=other_ontology['id']))

    repo._ontology_drafts.create(first, _draft(first))
    with pytest.raises(ValueError):
        repo._ontology_drafts.compare_and_set(
            first, 'draft-1', 1, {'source_context': []})
    with pytest.raises(ValueError):
        repo._ontology_drafts.compare_and_set(
            first, 'draft-1', 1, {'title': 42})
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo._ontology_drafts.compare_and_set(
            first, 'draft-1', 1, {'published_ontology_id': other_ontology['id']})


def test_two_repository_instances_allow_only_one_revision_cas(tmp_path):
    path = tmp_path / 'cas.sqlite'
    first = Repository(path)
    project_id = first.create_project('cas')['id']
    first._ontology_drafts.create(project_id, _draft(project_id))
    second = Repository(path)
    barrier = Barrier(2)

    def update(repo, title):
        barrier.wait()
        try:
            return repo._ontology_drafts.compare_and_set(
                project_id, 'draft-1', 1, {'title': title})
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: update(*args), [
            (first, 'writer one'), (second, 'writer two'),
        ]))

    winners = [item for item in results if isinstance(item, dict)]
    conflicts = [item for item in results if isinstance(item, ValueError)]
    assert len(winners) == len(conflicts) == 1
    assert winners[0]['revision'] == 2
    assert 'revision' in str(conflicts[0]).lower() or '版本冲突' in str(conflicts[0])


def test_full_export_restore_preserves_governance_ids_and_references(tmp_path):
    source = Repository(tmp_path / 'source.sqlite')
    project_id = source.create_project('source')['id']
    base = source.save_ontology(project_id, '@prefix ex: <urn:ex:> .', {'classes': []})
    draft = source._ontology_drafts.create(
        project_id, _draft(project_id, base_ontology_id=base['id']))
    source._ontology_drafts.append_operations(
        project_id, draft['id'], [_operation('op-1')])
    source._ontology_drafts.append_decisions(
        project_id, draft['id'], [_decision('decision-1', 'op-1', 'sha256:op-1')])
    with source._transaction():
        source._db.execute(
            '''INSERT INTO ontology_publish_requests
               (id,project_id,draft_id,idempotency_key,request_hash,result_ontology_id,
                created_at,completed_at) VALUES (?,?,?,?,?,?,?,?)''',
            ('publish-1', project_id, draft['id'], 'key-1', 'sha256:request', base['id'],
             '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:01.000000Z'))
    source.begin_provenance_activity(project_id, 'activity-1', 'retrieval', {'query': 'q'})
    source.complete_provenance_activity(project_id, 'activity-1', {'query': 'q'}, [{
        'id': 'edge-1', 'activity_id': 'activity-1', 'source_ref': 'ontology-draft:draft-1',
        'relation': 'used', 'target_ref': f'ontology-version:{base["id"]}',
        'ordinal': 0, 'payload': {'stable': True},
    }])

    snapshot = source.export_projection(project_id)
    assert snapshot['governance_history_included'] is True
    assert set(snapshot['governance']['ontology']) == {
        'drafts', 'operations', 'decisions', 'publish_requests'}

    target = Repository(tmp_path / 'target.sqlite')
    restored = target.restore_projection(snapshot)
    assert restored['id'] == project_id
    target_snapshot = target.export_projection(project_id)
    assert target_snapshot['project'] == snapshot['project']
    assert target_snapshot['ontologies'] == snapshot['ontologies']
    assert target_snapshot['governance']['ontology'] == snapshot['governance']['ontology']
    assert target_snapshot['provenance']['activities'] == snapshot['provenance']['activities']
    assert target_snapshot['provenance']['edges'] == snapshot['provenance']['edges']

    partial = json.loads(json.dumps(snapshot))
    del partial['governance']['ontology']['decisions']
    with pytest.raises(ValueError, match='完整治理备份'):
        Repository(tmp_path / 'partial.sqlite').restore_projection(partial)

    missing_assertions = json.loads(json.dumps(snapshot))
    del missing_assertions['governance']['assertions']
    with pytest.raises(ValueError, match='完整治理备份'):
        Repository(tmp_path / 'missing-assertions.sqlite').restore_projection(
            missing_assertions)


def test_backdated_supersession_chains_round_trip_in_dependency_order(tmp_path):
    source = Repository(tmp_path / 'backdated-source.sqlite')
    project_id = source.create_project('backdated')['id']
    source._ontology_drafts.create(project_id, _draft(project_id))
    source._ontology_drafts.append_operations(project_id, 'draft-1', [
        _operation('op-old', created_at='2026-01-02T00:00:00.000000Z'),
        _operation('op-new', supersedes='op-old',
                   created_at='2026-01-01T00:00:00.000000Z'),
    ])
    source._ontology_drafts.append_decisions(project_id, 'draft-1', [
        _decision('decision-old', 'op-new', 'sha256:op-new',
                  created_at='2026-01-02T00:00:00.000000Z'),
        _decision('decision-new', 'op-new', 'sha256:op-new',
                  supersedes='decision-old',
                  created_at='2026-01-01T00:00:00.000000Z'),
    ])

    snapshot = source.export_projection(project_id)
    history = snapshot['governance']['ontology']
    assert [item['id'] for item in history['operations']] == ['op-old', 'op-new']
    assert [item['id'] for item in history['decisions']] == [
        'decision-old', 'decision-new']
    target = Repository(tmp_path / 'backdated-target.sqlite')
    target.restore_projection(snapshot)
    restored = target.export_projection(project_id)['governance']['ontology']
    assert {item['id'] for item in restored['operations']} == {'op-old', 'op-new'}
    assert {item['id'] for item in restored['decisions']} == {
        'decision-old', 'decision-new'}
