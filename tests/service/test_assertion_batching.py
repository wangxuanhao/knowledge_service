"""Source guards use bounded, project-scoped reads without thawing snapshots."""
from contextlib import contextmanager
from copy import deepcopy
import json
from math import ceil

import pytest

from knowledge_service.repository import OntologyPublicationConflict, Repository
from knowledge_service.services.ontology_drafts_core import StaleSource, _fingerprint
from knowledge_service.services.ontology_intake import OntologyIntake, assert_intake_sources
from test_assertions import _assertion
from test_discovery_run_store import _run, _seed_linked_draft
from test_ontology_intake import _scoped_source, _setup


BATCH_LIMIT = 1000


def _seed_assertions(repo, project, count):
    if count > 3:
        # These are read-budget fixtures. Clone a genuinely created decoded
        # row with PostgreSQL's pipelined executemany instead of spending the
        # test's time repeating creation transactions and unrelated reads.
        first = _seed_assertions(repo, project, 1)[0]
        rows = [first, *[{**deepcopy(first), 'id': f'assertion-{index}',
            'payload': {**deepcopy(first['payload']), 'index': index}}
            for index in range(1, count)]]
        columns = tuple(first)
        with repo.unit_of_work():
            repo._db.executemany(
                f"INSERT INTO assertions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [tuple(json.dumps(row[key], ensure_ascii=False) if key == 'payload' else row[key]
                       for key in columns) for row in rows[1:]])
        return rows
    with repo.unit_of_work():
        return [repo.create_assertion(project, _assertion(
            id=f'assertion-{index}', payload={
                'index': index, 'nested': [True, False, None, {'文字': '值'}]}))
            for index in range(count)]


def _observe_sql(repo, monkeypatch):
    statements = []
    execute = repo._db.execute

    def counted(sql, params=()):
        statements.append((' '.join(sql.split()).upper(), deepcopy(params)))
        return execute(sql, params)

    monkeypatch.setattr(repo._db, 'execute', counted)
    return statements


def _assertion_reads(statements):
    return [(sql, params) for sql, params in statements
            if sql.startswith('SELECT') and 'FROM ASSERTIONS ' in sql]


@pytest.mark.parametrize('count', [1, 100, BATCH_LIMIT, BATCH_LIMIT + 1])
def test_get_assertions_batches_unique_ids_with_exact_decoding(tmp_path, monkeypatch, count):
    repo = Repository(tmp_path / 'assertion-batch')
    project = repo.create_project('batch')['id']
    expected = _seed_assertions(repo, project, count)
    # Include a decided assertion so all decision fields, not only payloads,
    # must match the existing single-record decoder.
    expected[0] = repo.transition_assertion(
        project, expected[0]['id'], 1, 'accepted', '核验', 'reviewer', 'canonical')
    ids = [row['id'] for row in reversed(expected)]
    expected_by_id = {row['id']: row for row in reversed(expected)}
    assert repo.get_assertion(project, ids[0]) == expected_by_id[ids[0]]
    assert repo.get_assertion(project, ids[-1]) == expected_by_id[ids[-1]]
    statements = _observe_sql(repo, monkeypatch)

    actual = repo.get_assertions(project, [*ids, *ids[:3]])

    assert actual == expected_by_id
    assert len(statements) == 1 + ceil(count / BATCH_LIMIT)
    reads = _assertion_reads(statements)
    assert len(reads) == ceil(count / BATCH_LIMIT)
    assert all('PROJECT_ID=?' in sql for sql, _ in reads)
    assert [key for _, params in reads for key in params[1]] == ids
    assert all(len(params[1]) <= BATCH_LIMIT for _, params in reads)


def test_get_assertions_handles_missing_empty_and_long_ids_without_lineage_limits(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'assertion-batch-scope')
    project = repo.create_project('batch')['id']
    other = repo.create_project('other')['id']
    long_id = 'assertion-' + 'x' * 300
    expected = repo.create_assertion(project, _assertion(id=long_id))
    repo.create_assertion(other, _assertion(id='other-project'))
    statements = _observe_sql(repo, monkeypatch)

    assert repo.get_assertions(project, []) == {}
    assert _assertion_reads(statements) == []
    statements.clear()
    assert repo.get_assertions(project, ['', 'missing', 'other-project', long_id, long_id]) == {
        long_id: expected}
    assert len(_assertion_reads(statements)) == 1
    assert repo.get_assertion(project, long_id) == expected
    with pytest.raises(KeyError):
        repo.get_assertion(project, 'other-project')
    with pytest.raises(KeyError):
        repo.get_assertions('missing-project', [])
    # The existing public lineage endpoint keeps its separate bounded contract.
    with pytest.raises(ValueError, match='100'):
        repo.list_assertions(project, assertion_ids=['missing'] * 101)
    with pytest.raises(ValueError, match='100'):
        repo.list_assertions(project, assertion_ids=[long_id])


def _guard_runs(repo, project, assertions, *, snapshots=None):
    frozen = snapshots if snapshots is not None else [
        {'id': row['id'], 'fingerprint': _fingerprint(row)} for row in assertions]
    runs = []
    for index in range(2):
        run = {**_run(project, run_id=f'guard-run-{index}'),
               'intake_request_hash': f'request-{index}', 'source_documents': [],
               'assertion_snapshots': deepcopy(frozen)}
        _seed_linked_draft(repo, project, run)
        runs.append(repo.create_discovery_run(project, run))
    context = {'intake_bindings': [{'discovery_run_id': run['id']} for run in runs]}
    return runs, context


def _check_guard(repo, project, context, guard):
    if guard == 'service':
        return assert_intake_sources(repo, project, context)
    return repo._recheck_ontology_publish_source(project, {
        'id': 'draft-1', 'source_kind': 'manual', 'source_context': context})


@pytest.mark.parametrize('guard', ['service', 'publication'])
@pytest.mark.parametrize('count', [1, 100, BATCH_LIMIT, BATCH_LIMIT + 1])
def test_each_source_guard_reads_shared_assertions_once_in_bounded_batches(
        tmp_path, monkeypatch, guard, count):
    repo = Repository(tmp_path / 'source-guard-batch')
    project = repo.create_project('guard')['id']
    assertions = _seed_assertions(repo, project, count)
    _, context = _guard_runs(repo, project, assertions)
    statements = _observe_sql(repo, monkeypatch)

    _check_guard(repo, project, context, guard)

    reads = _assertion_reads(statements)
    assert len(reads) == ceil(count / BATCH_LIMIT)
    assert len(statements) <= 5 + ceil(count / BATCH_LIMIT)
    assert [key for _, params in reads for key in params[1]] == [row['id'] for row in assertions]


@pytest.mark.parametrize('guard', ['service', 'publication'])
@pytest.mark.parametrize('change', ['missing', 'withdrawn', 'changed', 'other-project'])
def test_source_guards_reject_missing_withdrawn_changed_and_foreign_assertions(tmp_path, guard, change):
    repo = Repository(tmp_path / 'stale-assertions')
    project = repo.create_project('guard')['id']
    assertion = _seed_assertions(repo, project, 1)[0]
    if change == 'other-project':
        other = repo.create_project('other')['id']
        assertion = repo.create_assertion(other, _assertion(id='foreign-assertion'))
    _, context = _guard_runs(repo, project, [assertion])
    if change == 'missing':
        with repo.unit_of_work():
            repo._db.execute('DELETE FROM assertions WHERE project_id=? AND id=?',
                             (project, assertion['id']))
    elif change == 'withdrawn':
        repo.transition_assertion(project, assertion['id'], 1, 'superseded', '撤回', 'reviewer')
    elif change == 'changed':
        repo.transition_assertion(project, assertion['id'], 1, 'rejected', '拒绝', 'reviewer')

    error_type = StaleSource if guard == 'service' else OntologyPublicationConflict
    with pytest.raises(error_type) as caught:
        _check_guard(repo, project, context, guard)
    assert caught.value.code == 'stale_source'
    assert '断言' in str(caught.value)


@pytest.mark.parametrize('guard', ['service', 'publication'])
def test_source_guard_compares_every_duplicate_snapshot_in_original_run_order(tmp_path, guard):
    repo = Repository(tmp_path / 'duplicate-snapshots')
    project = repo.create_project('guard')['id']
    assertion = _seed_assertions(repo, project, 1)[0]
    snapshots = [{'id': assertion['id'], 'fingerprint': _fingerprint(assertion)}]
    # The second run freezes an older/different fingerprint for the same ID.
    first = {**_run(project, run_id='first'), 'intake_request_hash': 'first',
             'source_documents': [], 'assertion_snapshots': snapshots}
    _seed_linked_draft(repo, project, first)
    repo.create_discovery_run(project, first)
    second = {**first, 'id': 'second', 'intake_request_hash': 'second',
              'assertion_snapshots': [*snapshots, {**snapshots[0], 'fingerprint': 'old'}]}
    repo.create_discovery_run(project, second)
    context = {'intake_bindings': [{'discovery_run_id': key} for key in ['first', 'second']]}

    error_type = StaleSource if guard == 'service' else OntologyPublicationConflict
    with pytest.raises(error_type, match='断言.*变化'):
        _check_guard(repo, project, context, guard)
    # Run status and assertion errors retain the old validation order.
    repo.transition_discovery_run(project, 'first', 'draft_created', 'closed',
        candidate_outcomes=[{'candidate_id': 'candidate-1', 'status': 'skipped',
                             'reason_code': 'draft_closed'}])
    with pytest.raises(error_type, match='运行'):
        _check_guard(repo, project, context, guard)
    reversed_context = {'intake_bindings': list(reversed(context['intake_bindings']))}
    with pytest.raises(error_type, match='断言.*变化'):
        _check_guard(repo, project, reversed_context, guard)
    # Prefetching a later missing run must not hide the first assertion error.
    reversed_context['intake_bindings'][1] = {'discovery_run_id': 'missing-run'}
    with pytest.raises(error_type, match='断言.*变化'):
        _check_guard(repo, project, reversed_context, guard)


@pytest.mark.parametrize('guard', ['service', 'publication'])
@pytest.mark.parametrize('position', ['same-run', 'later-run'])
@pytest.mark.parametrize('malformed', [[{}], {'bad': 'snapshot'}, 7])
def test_malformed_assertion_snapshots_do_not_preempt_earlier_run_status_checks(
        tmp_path, guard, position, malformed):
    repo = Repository(tmp_path / 'malformed-snapshot-order')
    project = repo.create_project('guard')['id']
    runs, context = _guard_runs(repo, project, [], snapshots=(
        malformed if position == 'same-run' else []))
    if position == 'later-run':
        # Extra run snapshot fields are deliberately not normalized by the
        # store; the guard must encounter this persisted malformed value only
        # at its original point in the validation sequence.
        later = {**runs[1], 'id': 'malformed-later', 'assertion_snapshots': malformed}
        repo.create_discovery_run(project, later)
        context['intake_bindings'][1]['discovery_run_id'] = later['id']
    repo.transition_discovery_run(project, runs[0]['id'], 'draft_created', 'closed',
        candidate_outcomes=[{'candidate_id': 'candidate-1', 'status': 'skipped',
                             'reason_code': 'draft_closed'}])

    error_type = StaleSource if guard == 'service' else OntologyPublicationConflict
    with pytest.raises(error_type, match='运行') as caught:
        _check_guard(repo, project, context, guard)
    assert caught.value.code == 'stale_source'


@pytest.mark.parametrize('guard', ['service', 'publication'])
@pytest.mark.parametrize('malformed, legacy_error, message', [
    ([{}], 'source', '断言已撤回'),
    ({'bad': 'snapshot'}, TypeError, 'string indices'),
    (7, TypeError, 'not iterable'),
    ([{'id': None}], 'source', '断言已撤回'),
    ([{'id': 'assertion-0'}], KeyError, 'fingerprint'),
])
def test_malformed_assertion_snapshots_retain_original_error_semantics(
        tmp_path, guard, malformed, legacy_error, message):
    repo = Repository(tmp_path / 'malformed-snapshot-error')
    project = repo.create_project('guard')['id']
    _seed_assertions(repo, project, 1)
    _, context = _guard_runs(repo, project, [], snapshots=malformed)
    error_type = (StaleSource if guard == 'service' else OntologyPublicationConflict
                  ) if legacy_error == 'source' else legacy_error

    with pytest.raises(error_type, match=message):
        _check_guard(repo, project, context, guard)


@pytest.mark.parametrize('malformed', [[{}], {'bad': 'snapshot'}, 7])
def test_publication_checks_frozen_documents_before_malformed_assertion_snapshots(tmp_path, malformed):
    repo = Repository(tmp_path / 'malformed-snapshot-documents')
    project = repo.create_project('guard')['id']
    run = {**_run(project, run_id='malformed-documents'), 'intake_request_hash': 'request',
           'source_documents': None, 'assertion_snapshots': malformed}
    _seed_linked_draft(repo, project, run)
    repo.create_discovery_run(project, run)
    context = {'intake_bindings': [{'discovery_run_id': run['id']}]}

    with pytest.raises(OntologyPublicationConflict, match='不可变来源快照') as caught:
        _check_guard(repo, project, context, 'publication')
    assert caught.value.code == 'stale_source'


@pytest.mark.parametrize('guard', ['service', 'publication'])
def test_empty_source_guard_does_not_scan_assertions(tmp_path, monkeypatch, guard):
    repo = Repository(tmp_path / 'empty-source-guard')
    project = repo.create_project('guard')['id']
    _, context = _guard_runs(repo, project, [])
    statements = _observe_sql(repo, monkeypatch)
    _check_guard(repo, project, context, guard)
    _check_guard(repo, project, {}, guard)
    assert _assertion_reads(statements) == []


def test_intake_freezes_assertions_in_candidate_order_with_one_bulk_read(tmp_path, monkeypatch):
    app, data, project, _ = _setup(tmp_path)
    repo = app.state.service.repository
    assertions = _seed_assertions(repo, project, 3)
    candidates = [{'id': f'candidate-{index}', 'assertion_id': row['id']}
                  for index, row in enumerate(reversed(assertions))]
    candidates.insert(1, {'id': 'document-only'})
    candidates.append({**candidates[0], 'id': 'duplicate-occurrence'})
    result = {'associated_fact_scope': candidates, 'source_fingerprint': 'source',
              'target': {'base_ontology_id': None}, 'candidate_ids': [row['id'] for row in candidates]}
    expected = [{'id': row['assertion_id'], 'fingerprint': _fingerprint(
        repo.get_assertion(project, row['assertion_id']))}
        for row in candidates if row.get('assertion_id')]
    statements = _observe_sql(repo, monkeypatch)

    run = OntologyIntake(app.state.service)._run(project, 'run', 'request', result, {}, [], {}, {})

    assert run['assertion_snapshots'] == expected
    # One bounded freeze read plus the existing full assertion read used by
    # materialized_records to attach canonical candidate identities.
    assert len(_assertion_reads(statements)) == 2


def _publication_state(repo, project):
    records = repo.current_records(project)
    return {
        'drafts': repo._ontology_drafts.export(project),
        'runs': repo.list_intake_runs(project), 'records': records,
        'record_history': {row['id']: repo.history(project, row['id']) for row in records},
        'assertions': repo.list_assertions(project),
        'assertion_events': repo.list_assertion_events(project),
        'ontologies': repo.list_ontologies(project),
        'activities': repo.list_provenance_activities(project),
        'edges': repo.list_provenance_edges(project),
    }


def test_publication_final_guard_rereads_assertion_after_preflight_and_rolls_back_exact_state(
        tmp_path, monkeypatch):
    app, data, project, _ = _setup(tmp_path)
    repo = app.state.service.repository
    _scoped_source(data, project, 'assertion-race-source', [
        {'id': 'race-assertion', 'kind': 'entity', 'text': 'race instance', 'proposed_type': 'RaceType'}])
    intake = OntologyIntake(app.state.service)
    drafts = intake.drafts
    body = {'candidate_ids': ['race-assertion'], 'expected_ontology_id': None,
            'create_new_draft': True, 'name': 'assertion race'}
    preview, _, _ = intake._plan(project, body)
    added = intake.add(project, {**body, 'source_fingerprint': preview['source_fingerprint'],
        'preview_fingerprint': preview['preview_fingerprint'], 'idempotency_key': 'assertion-race'}, actor='author')
    current = drafts.submit(project, added['draft']['id'], added['draft']['revision'])
    warnings = [row['code'] for row in current['validation_report']['warnings']]
    for operation in current['operations']:
        current = drafts.decide(project, current['id'], current['revision'], None,
            current['validation_fingerprint'], [{'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'], 'action': 'approve',
                'reason': '核对来源'}], warnings, 'reviewer')
    prepared = drafts.publish_preflight(project, current['id'], current['revision'], None,
        current['validation_fingerprint'], warnings, 'race-publish', 'publisher', '来源事务复核')
    # Preflight and an explicit source guard both succeed before entering the
    # actual repository publication transaction.
    assert_intake_sources(data, project, current['source_context'])
    before = _publication_state(repo, project)
    publication_transaction = repo._publication_transaction
    observed = {}

    @contextmanager
    def publication_with_assertion_change():
        with publication_transaction():
            observed['changed'] = repo.transition_assertion(
                project, 'race-assertion', 1, 'rejected', '来源已撤回', 'concurrent-reviewer')
            yield

    monkeypatch.setattr(repo, '_publication_transaction', publication_with_assertion_change)
    with pytest.raises(OntologyPublicationConflict, match='断言') as caught:
        repo.publish_ontology_draft(**prepared)
    assert caught.value.code == 'stale_source'
    assert observed['changed']['decision_version'] == 2
    assert _publication_state(repo, project) == before
