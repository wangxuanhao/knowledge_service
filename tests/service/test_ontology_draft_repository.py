import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import knowledge_service.repository as repository_module
import knowledge_service.repository.core as repository_core
import knowledge_service.repository.ontology_draft_store as draft_store_module
from knowledge_service.repository import Repository
from knowledge_service.repository.connection import IntegrityError


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


def test_restore_dependency_order_is_linear_for_long_reverse_chain():
    rows = [
        {'id': f'op-{index}', 'supersedes_operation_id': (
            f'op-{index - 1}' if index else None)}
        for index in reversed(range(10_000))
    ]

    started_at = time.perf_counter()
    ordered = draft_store_module._dependency_order(
        rows, 'supersedes_operation_id', '本体操作')

    assert ordered[0]['id'] == 'op-0'
    assert ordered[-1]['id'] == 'op-9999'
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


def _insert_decision_sql(repo, project_id, identifier, supersedes,
                         operation_id='op-valid', fingerprint='sha256:op-valid'):
    repo._db.execute(
        '''INSERT INTO ontology_review_decisions
           (id,project_id,draft_id,operation_id,operation_fingerprint,
            action,reason,actor,supersedes_decision_id,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (identifier, project_id, 'draft-1', operation_id, fingerprint,
         'approve', 'test', 'reviewer', supersedes,
         '2026-01-01T00:00:00.000000Z'))


def _governed_snapshot(tmp_path, name):
    source = Repository(tmp_path / f'{name}-source.sqlite')
    project_id = source.create_project(name)['id']
    artifact = {
        'id': f'{name}-artifact',
        'project_id': project_id,
        'kind': 'ontology_change',
        'summary': 'legacy governed artifact',
    }
    source.save_artifact('ontology_change', artifact)
    source._ontology_drafts.create(project_id, {
        **_draft(project_id),
        'legacy_artifact_id': artifact['id'],
    })
    source._ontology_drafts.append_operations(
        project_id, 'draft-1', [_operation('op-valid')])
    source._ontology_drafts.append_decisions(project_id, 'draft-1', [
        _decision('decision-valid', 'op-valid', 'sha256:op-valid')])
    return source.export_projection(project_id), artifact


# PostgreSQL 基线（0001_core.sql）没有 SQLite 时代的「重开时修补缺失触发器」机制，
# 也没有递归触发器开关。这里只断言契约本身：台账追加写、重复键被不可变守卫拒绝。
#   普通重复插入与 ON CONFLICT DO UPDATE（SQLite `INSERT OR REPLACE` 的等价物）
#   都必须在 BEFORE INSERT 守卫处被拒，且不改变已有行。
@pytest.mark.parametrize('statement', [
    '''INSERT INTO ontology_history_repairs
       (repair_key,schema_version,reason,operations_repaired,decisions_repaired,repaired_at)
       SELECT repair_key,schema_version,reason,operations_repaired,
              decisions_repaired,repaired_at
       FROM ontology_history_repairs WHERE repair_key=?''',
    '''INSERT INTO ontology_history_repairs
       (repair_key,schema_version,reason,operations_repaired,decisions_repaired,repaired_at)
       SELECT repair_key,schema_version,reason,operations_repaired,
              decisions_repaired,repaired_at
       FROM ontology_history_repairs WHERE repair_key=?
       ON CONFLICT (repair_key) DO UPDATE SET
           schema_version=EXCLUDED.schema_version,
           reason=EXCLUDED.reason,
           operations_repaired=EXCLUDED.operations_repaired,
           decisions_repaired=EXCLUDED.decisions_repaired,
           repaired_at=EXCLUDED.repaired_at''',
])
def test_ontology_history_repair_rows_reject_duplicate_insert(tmp_path, statement):
    repo = Repository(tmp_path / 'immutable-repair-ledger.sqlite')
    repair_key = 'repair-immutable-guard'
    repo._db.execute(
        'INSERT INTO ontology_history_repairs '
        '(repair_key,schema_version,reason,operations_repaired,'
        'decisions_repaired,repaired_at) VALUES (?,?,?,?,?,?)',
        (repair_key, 14, 'baseline', 0, 0, '2026-01-01T00:00:00.000000Z'))

    with pytest.raises(IntegrityError, match='immutable'):
        repo._db.execute(statement, (repair_key,))

    assert repo._db.execute(
        'SELECT COUNT(*) FROM ontology_history_repairs WHERE repair_key=?',
        (repair_key,)).fetchone()[0] == 1


def test_store_rejects_self_supersession_and_two_node_cycles(tmp_path):
    repo = Repository(tmp_path / 'supersession-cycles.sqlite')
    project_id = repo.create_project('cycles')['id']
    repo._ontology_drafts.create(project_id, _draft(project_id))

    with pytest.raises(ValueError, match='不能替代自身'):
        repo._ontology_drafts.append_operations(
            project_id, 'draft-1', [_operation('op-self', supersedes='op-self')])
    assert repo._ontology_drafts.effective_operations(project_id, 'draft-1') == []

    with pytest.raises((ValueError, IntegrityError)):
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

    with pytest.raises((ValueError, IntegrityError)):
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

    with pytest.raises(IntegrityError, match='cycle'):
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
    with pytest.raises(IntegrityError, match='cycle'):
        with repo._transaction():
            repo._db.execute('SET CONSTRAINTS ALL DEFERRED')
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

    source._ontology_drafts.append_operations(project_id, 'draft-1', [
        _operation('op-new', supersedes='op-middle'),
        _operation('op-middle', supersedes='op-root'),
        _operation('op-root'),
    ])
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


def test_decision_fingerprint_is_bound_to_immutable_operation(tmp_path):
    repo = Repository(tmp_path / 'decision-fingerprint.sqlite')
    project_id = repo.create_project('decision binding')['id']
    repo._ontology_drafts.create(project_id, _draft(project_id))
    repo._ontology_drafts.append_operations(
        project_id, 'draft-1', [_operation('op-valid')])

    with pytest.raises(IntegrityError, match='fingerprint'):
        repo._ontology_drafts.append_decisions(project_id, 'draft-1', [
            _decision('decision-forged', 'op-valid', 'sha256:forged')])
    with pytest.raises(IntegrityError, match='fingerprint'):
        with repo._transaction():
            _insert_decision_sql(
                repo, project_id, 'decision-direct-forged', None,
                fingerprint='sha256:forged')

    assert repo._ontology_drafts.export(project_id)['decisions'] == []


def test_superseded_decisions_are_bound_to_same_operation_atomically(tmp_path):
    repo = Repository(tmp_path / 'decision-supersession-binding.sqlite')
    project_id = repo.create_project('decision supersession binding')['id']
    repo._ontology_drafts.create(project_id, _draft(project_id))
    repo._ontology_drafts.append_operations(project_id, 'draft-1', [
        _operation('op-one'), _operation('op-two')])
    repo._ontology_drafts.append_decisions(project_id, 'draft-1', [
        _decision('decision-one', 'op-one', 'sha256:op-one')])

    with pytest.raises(IntegrityError, match='same operation'):
        repo._ontology_drafts.append_decisions(project_id, 'draft-1', [
            _decision('decision-cross-operation', 'op-two', 'sha256:op-two',
                      supersedes='decision-one')])

    with pytest.raises(IntegrityError, match='same operation'):
        with repo._transaction():
            repo._db.execute('SET CONSTRAINTS ALL DEFERRED')
            _insert_decision_sql(
                repo, project_id, 'decision-forward', 'decision-target',
                operation_id='op-two', fingerprint='sha256:op-two')
            _insert_decision_sql(
                repo, project_id, 'decision-target', None,
                operation_id='op-one', fingerprint='sha256:op-one')

    assert [row['id'] for row in repo._ontology_drafts.export(project_id)['decisions']] == [
        'decision-one']


@pytest.mark.parametrize('section_path', [
    ('records',), ('ontologies',), ('artifacts',),
    ('governance', 'assertions'),
    ('governance', 'assertion_events'),
    ('governance', 'fact_keys'),
    ('governance', 'ingest_runs'),
    ('governance', 'resolution_reviews'),
    ('governance', 'merge_operations'),
    ('governance', 'ontology', 'drafts'),
    ('governance', 'ontology', 'operations'),
    ('governance', 'ontology', 'decisions'),
    ('governance', 'ontology', 'publish_requests'),
    ('provenance', 'record_version_assertions'),
    ('provenance', 'activities'),
    ('provenance', 'edges'),
])
def test_restore_rejects_cross_project_rows_before_any_mutation(
        tmp_path, section_path):
    snapshot, _ = _governed_snapshot(tmp_path, 'project-isolation')
    target = Repository(tmp_path / ('target-' + '-'.join(section_path) + '.sqlite'))
    unrelated_id = target.create_project('unrelated')['id']
    before = target.export_projection(unrelated_id)
    container = snapshot
    for key in section_path:
        container = container[key]
    container.append({'project_id': unrelated_id})

    with pytest.raises(ValueError, match='project_id'):
        target.restore_projection(snapshot)

    with pytest.raises(KeyError):
        target.get_project(snapshot['project']['id'])
    assert target.export_projection(unrelated_id) == before


@pytest.mark.parametrize('mutation', [
    'future-schema', 'missing-artifacts', 'unknown-top-level',
    'unknown-governance', 'unknown-provenance', 'unknown-ontology-governance',
])
def test_full_restore_fails_closed_for_schema_and_contract_drift(tmp_path, mutation):
    snapshot, _ = _governed_snapshot(tmp_path, f'contract-{mutation}')
    if mutation == 'future-schema':
        snapshot['schema_version'] = 999
    elif mutation == 'missing-artifacts':
        del snapshot['artifacts']
    elif mutation == 'unknown-top-level':
        snapshot['future_section'] = []
    elif mutation == 'unknown-governance':
        snapshot['governance']['future_section'] = []
    elif mutation == 'unknown-provenance':
        snapshot['provenance']['future_section'] = []
    else:
        snapshot['governance']['ontology']['future_section'] = []

    target = Repository(tmp_path / f'contract-{mutation}-target.sqlite')
    with pytest.raises(ValueError, match='备份|schema|字段'):
        target.restore_projection(snapshot)
    with pytest.raises(KeyError):
        target.get_project(snapshot['project']['id'])


_FULL_ROW_SHAPES = {
    ('project',): {
        'id', 'name', 'metadata', 'created_at',
    },
    ('records',): {
        'id', 'kind', 'text', 'metadata', 'valid_from', 'valid_until',
        'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at',
    },
    ('ontologies',): {
        'id', 'project_id', 'turtle', 'summary', 'created_at', 'metadata',
    },
    ('artifacts',): {'id', 'kind', 'project_id', 'payload'},
    ('governance', 'assertions'): {
        'id', 'project_id', 'kind', 'document_id', 'document_version_id',
        'chunk_id', 'source_hash', 'start_char', 'end_char', 'quote', 'payload',
        'status', 'canonical_record_id', 'decision_reason', 'decision_version',
        'created_at', 'decided_at', 'actor',
    },
    ('governance', 'assertion_events'): {
        'id', 'assertion_id', 'project_id', 'from_status', 'to_status',
        'decision_version', 'reason', 'actor', 'canonical_record_id', 'created_at',
    },
    ('governance', 'fact_keys'): {
        'project_id', 'fact_key', 'canonical_record_id', 'created_at', 'retired_at',
    },
    ('governance', 'ingest_runs'): {
        'id', 'project_id', 'document_id', 'document_version_id', 'attempt',
        'retry_of', 'status', 'active_stage', 'readiness', 'counts', 'failure',
        'version', 'created_at', 'updated_at',
    },
    ('governance', 'resolution_reviews'): {
        'id', 'project_id', 'source_entity_id', 'candidate_entity_id', 'score',
        'status', 'decision_version', 'payload', 'reason', 'actor', 'created_at',
        'decided_at',
    },
    ('governance', 'merge_operations'): {
        'id', 'project_id', 'operation', 'status', 'redirects', 'assertion_moves',
        'before_state', 'after_state', 'expected_versions', 'reversal_of',
        'created_at',
    },
    ('governance', 'ontology', 'drafts'): {
        'id', 'project_id', 'base_ontology_id', 'source_kind', 'status', 'revision',
        'title', 'summary', 'source_context', 'validation_report',
        'validation_fingerprint', 'published_ontology_id', 'legacy_artifact_id',
        'created_at', 'updated_at',
    },
    ('governance', 'ontology', 'operations'): {
        'id', 'project_id', 'draft_id', 'action', 'target_iri', 'risk',
        'fingerprint', 'reason', 'supersedes_operation_id', 'created_at', 'before',
        'after', 'evidence', 'impact', 'validation',
    },
    ('governance', 'ontology', 'decisions'): {
        'id', 'project_id', 'draft_id', 'operation_id', 'operation_fingerprint',
        'action', 'reason', 'actor', 'supersedes_decision_id', 'created_at',
    },
    ('governance', 'ontology', 'publish_requests'): {
        'id', 'project_id', 'draft_id', 'idempotency_key', 'request_hash',
        'result_ontology_id', 'created_at', 'completed_at',
    },
    ('provenance', 'record_version_assertions'): {
        'project_id', 'record_id', 'record_version_id', 'assertion_id',
        'assertion_event_id', 'created_at',
    },
    ('provenance', 'activities'): {
        'id', 'project_id', 'kind', 'status', 'payload', 'started_at', 'completed_at',
    },
    ('provenance', 'edges'): {
        'id', 'project_id', 'activity_id', 'source_ref', 'relation', 'target_ref',
        'ordinal', 'payload', 'created_at',
    },
}


_ROW_CONTRACT_MUTATIONS = [
    (mutation, section_path, fields)
    for section_path, fields in _FULL_ROW_SHAPES.items()
    for mutation in ('missing', 'unknown')
    if mutation == 'missing' or section_path != ('records',)
]


@pytest.mark.parametrize(
    'mutation,section_path,fields', _ROW_CONTRACT_MUTATIONS,
    ids=lambda value: '.'.join(value) if isinstance(value, tuple) else None)
def test_full_restore_validates_exact_row_contracts_before_mutation(
        tmp_path, section_path, fields, mutation):
    snapshot, _ = _governed_snapshot(
        tmp_path, f'row-contract-{mutation}-{len(section_path)}-{section_path[-1]}')
    project_id = snapshot['project']['id']
    if section_path == ('project',):
        row = snapshot['project']
    else:
        container = snapshot
        for key in section_path[:-1]:
            container = container[key]
        row = {field: None for field in fields}
        row['project_id'] = project_id
        container[section_path[-1]] = [row]
    if mutation == 'missing':
        row.pop(sorted(fields - {'project_id'})[0])
    else:
        row['future_field'] = 'must not be ignored'

    target = Repository(
        tmp_path / f'row-contract-target-{mutation}-{section_path[-1]}.sqlite')
    with pytest.raises(ValueError, match='字段'):
        target.restore_projection(snapshot)
    with pytest.raises(KeyError):
        target.get_project(project_id)


@pytest.mark.parametrize('flag', ['missing', 0, 1, 'true', None],
                         ids=['missing', 'zero', 'one', 'string', 'null'])
def test_restore_requires_literal_boolean_governance_history_flag(tmp_path, flag):
    snapshot, _ = _governed_snapshot(tmp_path, f'flag-{type(flag).__name__}-{flag}')
    if flag == 'missing':
        del snapshot['governance_history_included']
    else:
        snapshot['governance_history_included'] = flag
    target = Repository(tmp_path / f'flag-target-{type(flag).__name__}-{flag}.sqlite')

    with pytest.raises(ValueError, match='governance_history_included'):
        target.restore_projection(snapshot)
    with pytest.raises(KeyError):
        target.get_project(snapshot['project']['id'])


@pytest.mark.parametrize('version', ['missing', None, True, '14', 13, 999],
                         ids=['missing', 'null', 'bool', 'string', 'too-old', 'future'])
def test_full_restore_requires_supported_integer_schema_version(tmp_path, version):
    snapshot, _ = _governed_snapshot(tmp_path, f'version-{version}')
    if version == 'missing':
        del snapshot['schema_version']
    else:
        snapshot['schema_version'] = version
    target = Repository(tmp_path / f'version-target-{version}.sqlite')

    with pytest.raises(ValueError, match='schema_version'):
        target.restore_projection(snapshot)
    with pytest.raises(KeyError):
        target.get_project(snapshot['project']['id'])


def test_explicit_light_legacy_restore_keeps_permissive_section_contract(tmp_path):
    project_id = 'legacy-light-project'
    snapshot = {
        'governance_history_included': False,
        'project': {
            'id': project_id,
            'name': 'legacy light',
            'metadata': {},
            'created_at': '2026-01-01T00:00:00.000000Z',
        },
        'records': [],
        'ontologies': [],
    }

    target = Repository(tmp_path / 'legacy-light-target.sqlite')
    assert target.restore_projection(snapshot)['id'] == project_id


def test_full_record_row_contract_preserves_arbitrary_domain_fields(tmp_path):
    source = Repository(tmp_path / 'record-contract-source.sqlite')
    project_id = source.create_project('record contract')['id']
    source.put_record(project_id, {
        'id': 'entity-1', 'kind': 'entity', 'text': 'Entity', 'type': 'Thing',
        'source_id': 'document-1', 'ontology_id': 'ontology-1',
        'embedding_model': 'model-1', 'properties': {'stable': True},
        'confidence': 0.875,
        'business_context': {
            'market': 'north', 'policy': {'threshold': 12, 'enabled': True}},
        'business_tags': ['regulated', 'priority'],
    })
    snapshot = source.export_projection(project_id)

    target = Repository(tmp_path / 'record-contract-target.sqlite')
    target.restore_projection(snapshot)

    assert target.export_projection(project_id)['records'] == snapshot['records']


def test_full_record_row_contract_rejects_storage_reserved_embedding_atomically(
        tmp_path):
    source = Repository(tmp_path / 'reserved-embedding-source.sqlite')
    project_id = source.create_project('reserved embedding')['id']
    source.put_record(project_id, {
        'id': 'entity-1', 'kind': 'entity', 'text': 'Entity',
        'custom': {'preserved': True},
    })
    snapshot = source.export_projection(project_id)
    snapshot['records'][0]['embedding'] = [1.0, 0.0]
    target = Repository(tmp_path / 'reserved-embedding-target.sqlite')

    with pytest.raises(ValueError, match=r'records\[0\]\.embedding.*存储'):
        target.restore_projection(snapshot)
    with pytest.raises(KeyError):
        target.get_project(project_id)


@pytest.mark.parametrize(('field', 'value'), [
    ('id', 7),
    ('kind', 'unsupported'),
    ('kind', {'not': 'a string'}),
    ('text', {'not': 'text'}),
    ('metadata', []),
    ('valid_from', 7),
    ('valid_until', []),
    ('version', True),
    ('version_id', ''),
    ('recorded_at', None),
    ('superseded_at', 7),
])
def test_full_record_row_contract_rejects_malformed_reserved_fields_before_mutation(
        tmp_path, field, value):
    source = Repository(tmp_path / f'malformed-record-{field}-source.sqlite')
    project_id = source.create_project('malformed record')['id']
    source.put_record(project_id, {
        'id': 'entity-1', 'kind': 'entity', 'text': 'Entity',
        'custom': {'preserved': True},
    })
    snapshot = source.export_projection(project_id)
    snapshot['records'][0][field] = value
    target = Repository(tmp_path / f'malformed-record-{field}-target.sqlite')

    with pytest.raises(ValueError, match=rf'records\[0\].*{field}'):
        target.restore_projection(snapshot)
    with pytest.raises(KeyError):
        target.get_project(project_id)


def test_full_export_restores_artifacts_before_legacy_draft_references(tmp_path):
    snapshot, artifact = _governed_snapshot(tmp_path, 'artifact-history')
    assert snapshot['artifacts'] == [{
        'id': artifact['id'],
        'kind': 'ontology_change',
        'project_id': artifact['project_id'],
        'payload': artifact,
    }]

    target = Repository(tmp_path / 'artifact-history-target.sqlite')
    target.restore_projection(snapshot)
    assert target.get_artifact('ontology_change', artifact['id']) == artifact
    assert target._ontology_drafts.get(
        artifact['project_id'], 'draft-1')['legacy_artifact_id'] == artifact['id']

    missing = json.loads(json.dumps(snapshot))
    missing['artifacts'] = []
    with pytest.raises(ValueError, match='legacy_artifact_id'):
        Repository(tmp_path / 'artifact-missing-target.sqlite').restore_projection(missing)


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

    with pytest.raises((ValueError, IntegrityError)):
        repo._ontology_drafts.append_operations(
            project_id, 'draft-b', [_operation('op-b', supersedes='op-a')])
    with pytest.raises((ValueError, IntegrityError)):
        repo._ontology_drafts.append_decisions(
            project_id, 'draft-b', [_decision('decision-b', 'op-a', 'sha256:op-a')])

    repo._ontology_drafts.append_operations(
        project_id, 'draft-b', [_operation('op-b')])
    repo._ontology_drafts.append_decisions(
        project_id, 'draft-a', [_decision('decision-a', 'op-a', 'sha256:op-a')])
    with pytest.raises((ValueError, IntegrityError)):
        repo._ontology_drafts.append_decisions(project_id, 'draft-b', [
            _decision('decision-b', 'op-b', 'sha256:op-b', supersedes='decision-a')])


def test_store_rejects_cross_project_ontology_references_and_invalid_cas(tmp_path):
    repo = Repository(tmp_path / 'ownership.sqlite')
    first = repo.create_project('first')['id']
    second = repo.create_project('second')['id']
    other_ontology = repo.save_ontology(second, '', {})
    with pytest.raises((ValueError, IntegrityError)):
        repo._ontology_drafts.create(
            first, _draft(first, base_ontology_id=other_ontology['id']))

    repo._ontology_drafts.create(first, _draft(first))
    with pytest.raises(ValueError):
        repo._ontology_drafts.compare_and_set(
            first, 'draft-1', 1, {'source_context': []})
    with pytest.raises(ValueError):
        repo._ontology_drafts.compare_and_set(
            first, 'draft-1', 1, {'title': 42})
    with pytest.raises((ValueError, IntegrityError)):
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
