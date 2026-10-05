import json

import pytest

from knowledge_service.repository import Repository
from knowledge_service.repository.discovery_run_store import (
    DiscoveryRunConflict,
    discovery_result_kind,
)
from knowledge_service.services.ontology_operations import build_operation


def _binding(candidate_id='candidate-1', *, kind='proposed'):
    return {
        'candidate_id': candidate_id,
        'target_iri': f'urn:term:{candidate_id}',
        'target_kind': 'class',
        'binding_kind': kind,
        'required_operation_ids': (
            [f'operation:create:{candidate_id}'] if kind == 'proposed' else []),
        'optional_operation_ids': [],
    }


def _run(project_id, *, run_id='discovery-run:abc', fingerprint='sha256:abc'):
    return {
        'id': run_id,
        'project_id': project_id,
        'base_ontology_id': None,
        'source_fingerprint': fingerprint,
        'normalizer_version': 'v1',
        'generator_version': 'semantica-0.6.7',
        'candidate_snapshot': [{'id': 'candidate-1', 'kind': 'entity'}],
        'accepted_candidate_ids': ['candidate-1'],
        'merged_groups': [],
        'conflicts': [],
        'mappings': {'classes': {'Partner': 'urn:term:partner'}},
        'candidate_bindings': [_binding()],
        'initial_candidate_outcomes': [],
        'candidate_outcomes': [],
        'diagnostics': {},
        'status': 'draft_created',
        'unified_draft_id': 'draft-1',
        'supersedes_run_id': None,
    }


def _run_with_outcome(project_id, outcome):
    payload = _run(project_id)
    payload['candidate_snapshot'].append({
        'id': outcome['candidate_id'], 'kind': 'attribute',
    })
    payload['initial_candidate_outcomes'] = [outcome]
    payload['candidate_outcomes'] = [outcome]
    return payload


@pytest.mark.parametrize('ids', ['candidate-1', ['unknown'], ['candidate-1', 'candidate-1'], [None]])
def test_materialized_context_requires_unique_snapshot_candidate_ids(tmp_path, ids):
    repo=Repository(tmp_path/'invalid-context.sqlite')
    project=repo.create_project('invalid context')
    payload=_run(project['id'])
    payload.update(status='ready_to_finalize',unified_draft_id=None,
                   candidate_bindings=[_binding(kind='existing')],
                   materialized_candidate_ids=ids)
    with pytest.raises(ValueError,match='materialized_candidate_ids'):
        repo.create_discovery_run(project['id'],payload)


def _initial_outcome(
        candidate_id='candidate-2', *, status='skipped',
        reason_code='ontology_term_conflict'):
    return {
        'candidate_id': candidate_id,
        'status': status,
        'reason_code': reason_code,
    }


def _terminal_outcome(
        candidate_id='candidate-1', *, status='materialized',
        reason_code='materialized'):
    return {
        'candidate_id': candidate_id,
        'status': status,
        'reason_code': reason_code,
    }


def _operation(operation_id, action, target_iri, *, target_kind='class'):
    arguments = {}
    if action == 'create_term':
        arguments['after'] = {'kind': target_kind}
    elif action == 'set_datatype':
        arguments.update(
            before={'datatype': None},
            after={'datatype': 'http://www.w3.org/2001/XMLSchema#string'},
        )
    elif action in {'add_parent', 'add_domain', 'add_range'}:
        arguments['after'] = {'value': 'urn:term:suggestion'}
    return {
        **build_operation(
            action, target_iri, source='discovery', **arguments),
        'id': operation_id,
    }


def _create_draft(repo, project_id, operations=(), *, draft_id='draft-1'):
    repo._ontology_drafts.create(project_id, {
        'id': draft_id,
        'base_ontology_id': None,
        'source_kind': 'discovery',
        'status': 'pending',
        'revision': 1,
        'title': draft_id,
        'summary': '',
        'source_context': {},
    })
    return repo._ontology_drafts.append_operations(
        project_id, draft_id, list(operations))


def _seed_linked_draft(repo, project_id, run):
    if run.get('status') != 'draft_created':
        return
    draft_id = run.get('unified_draft_id')
    try:
        repo._ontology_drafts.get(project_id, draft_id)
        return
    except KeyError:
        pass
    operations = []
    seen_ids = set()
    for binding in run.get('candidate_bindings', []):
        if not isinstance(binding, dict) or not ({
                binding.get('binding_kind'), binding.get('status')}
                & {'proposed', 'new'}):
            continue
        target_iri = binding.get('target_iri')
        target_kind = binding.get('target_kind')
        required_ids = binding.get('required_operation_ids')
        optional_ids = binding.get('optional_operation_ids')
        if (not isinstance(target_iri, str)
                or target_kind not in {'class', 'relation', 'attribute'}
                or not isinstance(required_ids, list)
                or not isinstance(optional_ids, list)):
            continue
        for index, operation_id in enumerate(required_ids):
            if not isinstance(operation_id, str) or operation_id in seen_ids:
                continue
            action = (
                'set_datatype'
                if target_kind == 'attribute'
                and ('datatype' in operation_id or index > 0)
                else 'create_term'
            )
            try:
                operation = _operation(
                    operation_id, action, target_iri,
                    target_kind=target_kind)
            except ValueError:
                continue
            operations.append(operation)
            seen_ids.add(operation_id)
        optional_actions = {
            'class': ['add_parent'],
            'relation': ['add_domain', 'add_range'],
            'attribute': ['add_domain'],
        }[target_kind]
        for index, operation_id in enumerate(optional_ids):
            if not isinstance(operation_id, str) or operation_id in seen_ids:
                continue
            try:
                operation = _operation(
                    operation_id,
                    optional_actions[min(index, len(optional_actions) - 1)],
                    target_iri,
                )
            except ValueError:
                continue
            operations.append(operation)
            seen_ids.add(operation_id)
    _create_draft(
        repo, project_id, operations,
        draft_id=draft_id,
    )


@pytest.fixture(autouse=True)
def _governed_draft_for_legacy_run_fixtures(monkeypatch):
    original = Repository.create_discovery_run

    def create_with_linked_draft(repo, project_id_or_item, item=None):
        run = project_id_or_item if item is None else item
        project_id = (
            run.get('project_id') if item is None else project_id_or_item)
        if isinstance(run, dict):
            _seed_linked_draft(repo, project_id, run)
        return original(repo, project_id_or_item, item)

    monkeypatch.setattr(
        Repository, 'create_discovery_run', create_with_linked_draft)


def test_create_is_insert_only_and_idempotent_for_identical_canonical_snapshot(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    run = _run(project_id)

    first = repo.create_discovery_run(run)
    second = repo.create_discovery_run({**run, 'diagnostics': {}})

    assert second == first
    assert repo.list_discovery_runs(project_id) == [first]


def test_candidate_snapshot_order_is_canonical_for_idempotent_create(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    candidates = [
        {'id': 'candidate-2', 'kind': 'entity'},
        {'id': 'candidate-1', 'kind': 'entity'},
    ]
    run = {
        **_run(project_id),
        'candidate_snapshot': candidates,
        'accepted_candidate_ids': ['candidate-1', 'candidate-2'],
        'candidate_bindings': [
            _binding('candidate-1'), _binding('candidate-2')],
    }

    first = repo.create_discovery_run(run)
    second = repo.create_discovery_run({
        **run, 'candidate_snapshot': list(reversed(candidates))})

    assert second == first
    assert [item['id'] for item in first['candidate_snapshot']] == [
        'candidate-1', 'candidate-2']


@pytest.mark.parametrize(('field', 'value'), [
    ('candidate_snapshot', [
        {'id': 'candidate-1', 'kind': 'entity'},
        {'id': 'candidate-1', 'kind': 'entity'},
    ]),
    ('accepted_candidate_ids', ['candidate-1', 'candidate-1']),
    ('accepted_candidate_ids', ['missing-candidate']),
])
def test_create_rejects_invalid_snapshot_and_accepted_candidate_ids(
        tmp_path, field, value):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='candidate'):
        repo.create_discovery_run({**_run(project_id), field: value})


def test_create_requires_every_snapshot_candidate_to_be_audited(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='partition'):
        repo.create_discovery_run({
            **_run(project_id),
            'candidate_snapshot': [
                {'id': 'candidate-1', 'kind': 'entity'},
                {'id': 'candidate-2', 'kind': 'relation'},
            ],
        })


@pytest.mark.parametrize('bindings', [
    [],
    [_binding(), _binding()],
    [_binding('candidate-2')],
])
def test_draft_run_requires_exact_unique_binding_coverage(tmp_path, bindings):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='binding.*accepted_candidate_ids'):
        repo.create_discovery_run({
            **_run(project_id),
            'candidate_snapshot': [
                {'id': 'candidate-1', 'kind': 'entity'},
                {'id': 'candidate-2', 'kind': 'entity'},
            ],
            'accepted_candidate_ids': ['candidate-1', 'candidate-2'],
            'candidate_bindings': bindings,
        })


@pytest.mark.parametrize('binding', [
    {key: value for key, value in _binding().items()
     if key != 'required_operation_ids'},
    {key: value for key, value in _binding().items()
     if key != 'optional_operation_ids'},
    {**_binding(), 'required_operation_ids': 'operation-1'},
    {**_binding(), 'optional_operation_ids': ['operation-1', 'operation-1']},
    {**_binding(), 'required_operation_ids': ['operation-1'],
     'optional_operation_ids': ['operation-1']},
    {**_binding(), 'required_operation_ids': ['']},
    {**_binding(), 'required_operation_ids': ['   ']},
])
def test_create_rejects_invalid_binding_operation_id_sets(tmp_path, binding):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='operation IDs'):
        repo.create_discovery_run({
            **_run(project_id), 'candidate_bindings': [binding],
        })


@pytest.mark.parametrize('binding', [
    {**_binding(), 'required_operation_ids': []},
    {
        **_binding(), 'binding_kind': 'new', 'status': 'proposed',
        'required_operation_ids': [],
    },
])
def test_create_requires_proposed_binding_create_operation(tmp_path, binding):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='proposed.*required operation'):
        repo.create_discovery_run({
            **_run(project_id), 'candidate_bindings': [binding],
        })


def test_proposed_attribute_binding_accepts_multiple_required_operations(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    binding = {
        **_binding(), 'target_kind': 'attribute',
        'required_operation_ids': ['operation:create', 'operation:datatype'],
    }

    run = repo.create_discovery_run({
        **_run(project_id),
        'candidate_snapshot': [{
            'id': 'candidate-1', 'kind': 'attribute'}],
        'candidate_bindings': [binding],
    })

    assert run['candidate_bindings'] == [binding]


def test_draft_binding_loads_operations_inside_create_transaction(
        tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    _create_draft(repo, project_id, [_operation(
        'operation:create:candidate-1', 'create_term',
        'urn:term:candidate-1')])
    observed_transactions = []
    original = repo._ontology_drafts.effective_operations

    def observe_transaction(*args, **kwargs):
        observed_transactions.append(repo._db.in_transaction)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        repo._ontology_drafts, 'effective_operations', observe_transaction)

    repo.create_discovery_run(_run(project_id))

    assert observed_transactions == [True]


def test_draft_run_rejects_missing_linked_governed_draft(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='governed draft'):
        repo._discovery_runs.create(project_id, _run(project_id))


@pytest.mark.parametrize(('operation', 'binding_changes'), [
    (None, {}),
    (
        _operation(
            'operation:create:candidate-1', 'create_term',
            'urn:term:another-candidate'),
        {},
    ),
    (
        _operation(
            'operation:create:candidate-1', 'add_parent',
            'urn:term:candidate-1'),
        {},
    ),
    (
        _operation(
            'operation:create:candidate-1', 'create_term',
            'urn:term:candidate-1'),
        {'optional_operation_ids': ['operation:optional:candidate-1']},
    ),
])
def test_draft_binding_rejects_missing_wrong_target_or_wrong_action_operation(
        tmp_path, operation, binding_changes):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    operations = [] if operation is None else [operation]
    if binding_changes:
        operations.append(_operation(
            'operation:optional:candidate-1', 'set_datatype',
            'urn:term:candidate-1'))
    _create_draft(repo, project_id, operations)

    with pytest.raises(ValueError, match='operation'):
        repo.create_discovery_run({
            **_run(project_id),
            'candidate_bindings': [{
                **_binding(),
                **binding_changes,
            }],
        })


def test_draft_binding_rejects_operation_from_another_draft(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    _create_draft(repo, project_id)
    _create_draft(repo, project_id, [_operation(
        'operation:create:candidate-1', 'create_term',
        'urn:term:candidate-1')], draft_id='draft-2')

    with pytest.raises(ValueError, match='operation'):
        repo.create_discovery_run(_run(project_id))


@pytest.mark.parametrize(('candidate_kind', 'target_kind', 'operations'), [
    (
        'entity', 'class', [
            _operation(
                'operation:create:candidate-1', 'create_term',
                'urn:term:candidate-1'),
            _operation(
                'operation:parent:candidate-1', 'add_parent',
                'urn:term:candidate-1'),
        ],
    ),
    (
        'relation', 'relation', [
            _operation(
                'operation:create:candidate-1', 'create_term',
                'urn:term:candidate-1', target_kind='relation'),
            _operation(
                'operation:domain:candidate-1', 'add_domain',
                'urn:term:candidate-1'),
            _operation(
                'operation:range:candidate-1', 'add_range',
                'urn:term:candidate-1'),
        ],
    ),
    (
        'attribute', 'attribute', [
            _operation(
                'operation:create:candidate-1', 'create_term',
                'urn:term:candidate-1', target_kind='attribute'),
            _operation(
                'operation:datatype:candidate-1', 'set_datatype',
                'urn:term:candidate-1'),
            _operation(
                'operation:domain:candidate-1', 'add_domain',
                'urn:term:candidate-1'),
        ],
    ),
])
def test_draft_binding_accepts_kind_specific_required_and_optional_actions(
        tmp_path, candidate_kind, target_kind, operations):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    _create_draft(repo, project_id, operations)
    binding = {
        **_binding(),
        'target_kind': target_kind,
        'required_operation_ids': [
            operation['id'] for operation in operations
            if operation['action'] in {'create_term', 'set_datatype'}
        ],
        'optional_operation_ids': [
            operation['id'] for operation in operations
            if operation['action'] not in {'create_term', 'set_datatype'}
        ],
    }

    run = repo.create_discovery_run({
        **_run(project_id),
        'candidate_snapshot': [{
            'id': 'candidate-1', 'kind': candidate_kind}],
        'candidate_bindings': [binding],
    })

    assert run['candidate_bindings'] == [binding]


def test_proposed_attribute_binding_requires_create_and_datatype_actions(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    create_operation = _operation(
        'operation:create:candidate-1', 'create_term',
        'urn:term:candidate-1', target_kind='attribute')
    _create_draft(repo, project_id, [create_operation])

    with pytest.raises(ValueError, match='required.*operation'):
        repo.create_discovery_run({
            **_run(project_id),
            'candidate_snapshot': [{
                'id': 'candidate-1', 'kind': 'attribute'}],
            'candidate_bindings': [{
                **_binding(),
                'target_kind': 'attribute',
            }],
        })


def test_draft_created_requires_at_least_one_proposed_binding(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    _create_draft(repo, project_id)

    with pytest.raises(ValueError, match='draft_created.*proposed'):
        repo.create_discovery_run({
            **_run(project_id),
            'candidate_bindings': [_binding(kind='existing')],
        })


def test_mixed_draft_requires_reusable_binding_to_remain_operation_free(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    _create_draft(repo, project_id, [
        _operation(
            'operation:create:candidate-1', 'create_term',
            'urn:term:candidate-1'),
        _operation(
            'operation:parent:candidate-2', 'add_parent',
            'urn:term:candidate-2'),
    ])

    with pytest.raises(ValueError, match='reusable.*operation'):
        repo.create_discovery_run({
            **_run(project_id),
            'candidate_snapshot': [
                {'id': 'candidate-1', 'kind': 'entity'},
                {'id': 'candidate-2', 'kind': 'entity'},
            ],
            'accepted_candidate_ids': ['candidate-1', 'candidate-2'],
            'candidate_bindings': [
                _binding(),
                {
                    **_binding('candidate-2', kind='existing'),
                    'optional_operation_ids': [
                        'operation:parent:candidate-2'],
                },
            ],
        })


def test_draft_created_accepts_mixed_proposed_and_reusable_bindings(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    _create_draft(repo, project_id, [_operation(
        'operation:create:candidate-1', 'create_term',
        'urn:term:candidate-1')])

    run = repo.create_discovery_run({
        **_run(project_id),
        'candidate_snapshot': [
            {'id': 'candidate-1', 'kind': 'entity'},
            {'id': 'candidate-2', 'kind': 'entity'},
        ],
        'accepted_candidate_ids': ['candidate-1', 'candidate-2'],
        'candidate_bindings': [
            _binding(),
            _binding('candidate-2', kind='existing'),
        ],
    })

    assert [
        binding['binding_kind'] for binding in run['candidate_bindings']
    ] == ['proposed', 'existing']


def test_draft_created_rejects_all_diagnostic_shape(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    _create_draft(repo, project_id)
    diagnostic = _initial_outcome(
        'candidate-1', status='deferred',
        reason_code='manual_review_deferred')

    with pytest.raises(ValueError, match='draft_created.*proposed'):
        repo.create_discovery_run({
            **_run(project_id),
            'accepted_candidate_ids': [],
            'candidate_bindings': [{
                'candidate_id': 'candidate-1',
                'target_kind': 'class',
                'binding_kind': 'diagnostic',
                'required_operation_ids': [],
                'optional_operation_ids': [],
            }],
            'initial_candidate_outcomes': [diagnostic],
            'candidate_outcomes': [diagnostic],
        })


@pytest.mark.parametrize('outcome', [
    {'candidate_id': 'candidate-2', 'status': 'skipped'},
    {**_initial_outcome(), 'status': 'materialized'},
    {**_initial_outcome(), 'status': 'invented'},
    {**_initial_outcome(), 'reason_code': 'invented'},
    {**_initial_outcome(), 'unexpected': True},
    {**_initial_outcome(), 'diagnostic_code': ''},
    _initial_outcome(status='skipped', reason_code='low_frequency_attribute'),
    {
        'candidate_id': 'candidate-2', 'status': 'skipped',
        'code': 'ontology_term_conflict',
    },
])
def test_create_rejects_invalid_initial_outcome_schema(tmp_path, outcome):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='initial candidate outcome'):
        repo.create_discovery_run(_run_with_outcome(project_id, outcome))


@pytest.mark.parametrize('binding', [
    {**_binding(), 'binding_kind': 'invented'},
    {**_binding(), 'binding_kind': 'mapping_only'},
    {**_binding(), 'status': 'invented'},
    {**_binding(), 'target_iri': None},
    {**_binding(), 'target_iri': 'relative/term'},
    {**_binding(), 'reuse_iri': 'urn:term:different'},
])
def test_draft_run_rejects_invalid_binding_state_or_target(tmp_path, binding):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='binding'):
        repo.create_discovery_run({
            **_run(project_id), 'candidate_bindings': [binding],
        })


@pytest.mark.parametrize('binding', [
    {key: value for key, value in _binding().items()
     if key != 'target_kind'},
    {**_binding(), 'target_kind': ''},
    {**_binding(), 'target_kind': 'ontology_term'},
])
def test_create_requires_documented_binding_target_kind(tmp_path, binding):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='target_kind'):
        repo.create_discovery_run({
            **_run(project_id), 'candidate_bindings': [binding],
        })


@pytest.mark.parametrize(('candidate_kind', 'target_kind'), [
    ('entity', 'class'),
    ('class', 'class'),
    ('relation', 'relation'),
    ('attribute', 'attribute'),
])
def test_binding_target_kind_matches_normalized_candidate_kind(
        tmp_path, candidate_kind, target_kind):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    binding = {
        **_binding(), 'target_kind': target_kind,
        'required_operation_ids': (
            ['operation:create:candidate-1',
             'operation:datatype:candidate-1']
            if target_kind == 'attribute'
            else ['operation:create:candidate-1']),
    }
    run = repo.create_discovery_run({
        **_run(project_id),
        'candidate_snapshot': [{
            'id': 'candidate-1', 'kind': candidate_kind}],
        'candidate_bindings': [binding],
    })

    assert run['candidate_bindings'] == [binding]


@pytest.mark.parametrize(('candidate_kind', 'target_kind'), [
    ('entity', 'relation'),
    ('class', 'attribute'),
    ('relation', 'class'),
    ('attribute', 'relation'),
])
def test_create_rejects_binding_target_kind_mismatch(
        tmp_path, candidate_kind, target_kind):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='target_kind.*candidate'):
        repo.create_discovery_run({
            **_run(project_id),
            'candidate_snapshot': [{
                'id': 'candidate-1', 'kind': candidate_kind}],
            'candidate_bindings': [{
                **_binding(), 'target_kind': target_kind,
            }],
        })


@pytest.mark.parametrize('binding', [
    {**_binding(), 'binding_kind': 'proposed', 'status': 'existing'},
    {**_binding(kind='existing'), 'status': 'proposed'},
])
def test_create_rejects_contradictory_binding_state_tuple(tmp_path, binding):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='binding state'):
        repo.create_discovery_run({
            **_run(project_id), 'candidate_bindings': [binding],
        })


@pytest.mark.parametrize(('kind', 'status', 'run_status'), [
    ('existing', 'reusable', 'ready_to_finalize'),
    ('reusable', 'existing', 'ready_to_finalize'),
    ('proposed', 'new', 'draft_created'),
    ('new', 'proposed', 'draft_created'),
])
def test_create_accepts_consistent_binding_state_aliases(
        tmp_path, kind, status, run_status):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    binding = {
        **_binding(), 'binding_kind': kind, 'status': status,
        'required_operation_ids': (
            [] if run_status == 'ready_to_finalize' else ['operation-1']),
    }

    run = repo.create_discovery_run({
        **_run(project_id), 'status': run_status,
        'unified_draft_id': (
            None if run_status == 'ready_to_finalize' else 'draft-1'),
        'candidate_bindings': [binding],
    })

    assert run['candidate_bindings'] == [binding]


def test_diagnostic_binding_requires_matching_outcome_and_no_operations(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    payload = _run(project_id)
    payload['candidate_snapshot'].append({
        'id': 'candidate-2', 'kind': 'attribute'})

    with pytest.raises(ValueError, match='initial outcome'):
        repo.create_discovery_run({
            **payload,
            'candidate_bindings': [
                _binding(), {
                    'candidate_id': 'candidate-2', 'target_kind': 'attribute',
                    'status': 'quarantined',
                    'required_operation_ids': [],
                    'optional_operation_ids': [],
                }],
        })

    diagnostic = _initial_outcome()
    with pytest.raises(ValueError, match='operation IDs'):
        repo.create_discovery_run({
            **payload,
            'initial_candidate_outcomes': [diagnostic],
            'candidate_outcomes': [diagnostic],
            'candidate_bindings': [
                _binding(), {
                    'candidate_id': 'candidate-2', 'target_kind': 'attribute',
                    'status': 'quarantined',
                    'required_operation_ids': ['operation-2'],
                    'optional_operation_ids': [],
                }],
        })


@pytest.mark.parametrize(('outcome_id', 'snapshot'), [
    ('candidate-1', [{'id': 'candidate-1', 'kind': 'entity'}]),
    ('candidate-2', [{'id': 'candidate-1', 'kind': 'entity'}]),
])
def test_initial_outcome_must_be_snapshot_backed_and_not_accepted(
        tmp_path, outcome_id, snapshot):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    outcome = _initial_outcome(
        outcome_id, status='deferred',
        reason_code='manual_review_deferred')

    with pytest.raises(ValueError, match='outcome candidate'):
        repo.create_discovery_run({
            **_run(project_id), 'candidate_snapshot': snapshot,
            'initial_candidate_outcomes': [outcome],
            'candidate_outcomes': [outcome],
        })


@pytest.mark.parametrize(('status', 'draft_id', 'bindings', 'expected'), [
    ('draft_created', 'draft-1', [], 'draft'),
    ('published', 'draft-1', [], 'draft'),
    ('closed', 'draft-1', [], 'draft'),
    ('stale_base', 'draft-1', [], 'draft'),
    ('ready_to_finalize', None,
     [{'candidate_id': 'one', 'binding_kind': 'existing'}], 'mapping_only'),
    ('finalized_no_change', None,
     [{'candidate_id': 'one', 'binding_kind': 'existing'}], 'mapping_only'),
    ('stale_source', None,
     [{'candidate_id': 'one', 'binding_kind': 'existing'}], 'mapping_only'),
    ('diagnosed_no_change', None, [], 'diagnosed_no_change'),
])
def test_result_kind_is_stable_across_lifecycle_status(
        status, draft_id, bindings, expected):
    assert discovery_result_kind({
        'status': status,
        'unified_draft_id': draft_id,
        'candidate_bindings': bindings,
    }) == expected


def test_same_id_with_different_immutable_snapshot_conflicts(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    repo.create_discovery_run(_run(project_id))

    with pytest.raises(DiscoveryRunConflict):
        repo.create_discovery_run({
            **_run(project_id),
            'candidate_snapshot': [{'id': 'candidate-2', 'kind': 'entity'}],
            'accepted_candidate_ids': ['candidate-2'],
            'candidate_bindings': [_binding('candidate-2')],
        })

    assert repo.get_discovery_run(project_id, 'discovery-run:abc')[
        'candidate_snapshot'] == [{'id': 'candidate-1', 'kind': 'entity'}]


def test_same_id_with_different_initial_diagnostic_outcomes_conflicts(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = _initial_outcome()
    repo.create_discovery_run(_run_with_outcome(project_id, conflict))

    with pytest.raises(DiscoveryRunConflict):
        repo.create_discovery_run({
            **_run_with_outcome(project_id, conflict),
            'initial_candidate_outcomes': [{
                'candidate_id': 'candidate-2', 'status': 'deferred',
                'reason_code': 'low_frequency_attribute',
            }],
            'candidate_outcomes': [{
                'candidate_id': 'candidate-2', 'status': 'deferred',
                'reason_code': 'low_frequency_attribute',
            }],
        })


def test_non_draft_run_cannot_be_created_with_a_draft_link(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='draft'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'diagnosed_no_change',
            'unified_draft_id': 'forged-draft',
        })


@pytest.mark.parametrize('status', [
    'published', 'finalized_no_change', 'stale_base', 'stale_source', 'closed',
])
def test_create_rejects_terminal_lifecycle_statuses(tmp_path, status):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='initial.*status'):
        repo.create_discovery_run({
            **_run(project_id), 'status': status,
        })


@pytest.mark.parametrize('bindings', [
    [],
    [_binding()],
])
def test_ready_run_requires_a_reusable_existing_binding(tmp_path, bindings):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='binding'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'ready_to_finalize',
            'unified_draft_id': None, 'candidate_bindings': bindings,
        })


def test_ready_run_accepts_a_reusable_existing_binding(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    run = repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'candidate_bindings': [_binding(kind='existing')],
    })

    assert run['status'] == 'ready_to_finalize'


def test_create_seeds_current_outcomes_from_explicit_initial_outcomes(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = _initial_outcome()
    payload = _run_with_outcome(project_id, conflict)
    payload.pop('candidate_outcomes')

    created = repo.create_discovery_run(payload)

    assert created['candidate_outcomes'] == [conflict]


def test_create_rejects_current_outcome_that_contradicts_initial_outcome(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = _initial_outcome('candidate-1')

    with pytest.raises(ValueError, match='initial candidate outcome'):
        repo.create_discovery_run({
            **_run(project_id),
            'initial_candidate_outcomes': [conflict],
            'candidate_outcomes': [{
                'candidate_id': 'candidate-1', 'status': 'materialized',
            }],
        })


def test_create_rejects_outcomes_beyond_the_exact_initial_set(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = _initial_outcome('candidate-1')

    with pytest.raises(ValueError, match='exactly match'):
        repo.create_discovery_run({
            **_run(project_id),
            'initial_candidate_outcomes': [conflict],
            'candidate_outcomes': [conflict, {
                'candidate_id': 'candidate-2', 'status': 'materialized',
            }],
        })


def test_create_rejects_current_outcomes_without_an_initial_set(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    payload = _run(project_id)
    payload.pop('initial_candidate_outcomes')

    with pytest.raises(ValueError, match='exactly match'):
        repo.create_discovery_run({
            **payload,
            'candidate_outcomes': [{
                'candidate_id': 'candidate-1', 'status': 'materialized',
            }],
        })


@pytest.mark.parametrize('binding', [
    _binding(kind='existing'),
    {
        'candidate_id': 'candidate-1', 'status': 'existing',
        'target_kind': 'class',
        'reuse_iri': 'urn:term:candidate-1',
        'required_operation_ids': [], 'optional_operation_ids': [],
    },
])
def test_diagnosed_run_rejects_mapping_only_bindings(tmp_path, binding):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    payload = {
        **_run(project_id), 'status': 'diagnosed_no_change',
        'unified_draft_id': None, 'candidate_bindings': [binding],
    }

    assert discovery_result_kind(payload) == 'mapping_only'
    with pytest.raises(ValueError, match='diagnosed_no_change.*binding'):
        repo.create_discovery_run(payload)


def test_ready_run_accepts_legacy_existing_status_binding(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    run = repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'candidate_bindings': [{
            'candidate_id': 'candidate-1', 'status': 'existing',
            'target_kind': 'class',
            'reuse_iri': 'urn:term:candidate-1',
            'required_operation_ids': [], 'optional_operation_ids': [],
        }],
    })

    assert discovery_result_kind(run) == 'mapping_only'


@pytest.mark.parametrize('target_iri', [
    None, '', 'relative/term', 'http://example.test/Order Item',
])
def test_ready_run_requires_valid_reuse_target_iri(tmp_path, target_iri):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='valid.*IRI'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'ready_to_finalize',
            'unified_draft_id': None,
            'candidate_bindings': [{
                'candidate_id': 'candidate-1', 'binding_kind': 'existing',
                'target_kind': 'class',
                'target_iri': target_iri, 'required_operation_ids': [],
                'optional_operation_ids': [],
            }],
        })


def test_ready_run_rejects_optional_ontology_operations(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='operation IDs'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'ready_to_finalize',
            'unified_draft_id': None,
            'candidate_bindings': [{
                'candidate_id': 'candidate-1', 'binding_kind': 'existing',
                'target_kind': 'class',
                'target_iri': 'urn:term:candidate-1',
                'required_operation_ids': [],
                'optional_operation_ids': ['operation-1'],
            }],
        })


@pytest.mark.parametrize('operation_field', [
    'required_operation_ids', 'optional_operation_ids',
])
def test_diagnosed_run_rejects_ontology_operation_ids(
        tmp_path, operation_field):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    diagnostic = _initial_outcome(
        status='deferred', reason_code='manual_review_deferred')
    payload = _run_with_outcome(project_id, diagnostic)

    with pytest.raises(ValueError, match='operation IDs'):
        repo.create_discovery_run({
            **payload, 'status': 'diagnosed_no_change',
            'unified_draft_id': None,
            'candidate_bindings': [{
                'candidate_id': 'candidate-2', 'target_kind': 'attribute',
                'status': 'deferred',
                'required_operation_ids': (
                    ['operation-1']
                    if operation_field == 'required_operation_ids' else []),
                'optional_operation_ids': (
                    ['operation-1']
                    if operation_field == 'optional_operation_ids' else []),
            }],
        })


def test_ready_run_rejects_operations_on_quarantined_binding(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    diagnostic = _initial_outcome()

    with pytest.raises(ValueError, match='operation IDs'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'ready_to_finalize',
            'unified_draft_id': None,
            'candidate_snapshot': [
                {'id': 'candidate-1', 'kind': 'entity'},
                {'id': 'candidate-2', 'kind': 'attribute'},
            ],
            'initial_candidate_outcomes': [diagnostic],
            'candidate_outcomes': [diagnostic],
            'candidate_bindings': [{
                'candidate_id': 'candidate-1', 'binding_kind': 'existing',
                'target_kind': 'class',
                'target_iri': 'urn:term:candidate-1',
                'required_operation_ids': [], 'optional_operation_ids': [],
            }, {
                'candidate_id': 'candidate-2', 'target_kind': 'attribute',
                'status': 'quarantined', 'required_operation_ids': [],
                'optional_operation_ids': ['operation-1'],
            }],
        })


@pytest.mark.parametrize('invalid_binding', [
    {
        'candidate_id': 'candidate-2', 'binding_kind': 'proposed',
        'target_kind': 'class',
        'target_iri': 'urn:term:candidate-2',
        'required_operation_ids': ['operation-1'],
        'optional_operation_ids': [],
    },
    {
        'candidate_id': 'candidate-2', 'binding_kind': 'existing',
        'target_kind': 'class',
        'target_iri': 'urn:term:candidate-2',
        'required_operation_ids': ['operation-1'],
        'optional_operation_ids': [],
    },
])
def test_ready_run_rejects_mixed_non_reusable_or_required_bindings(
        tmp_path, invalid_binding):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(
            ValueError, match='only reusable bindings|reusable binding'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'ready_to_finalize',
            'unified_draft_id': None,
            'candidate_snapshot': [
                {'id': 'candidate-1', 'kind': 'entity'},
                {'id': 'candidate-2', 'kind': 'entity'},
            ],
            'accepted_candidate_ids': ['candidate-1', 'candidate-2'],
            'candidate_bindings': [
                _binding(kind='existing'), invalid_binding],
        })


@pytest.mark.parametrize(('state_field', 'diagnostic_state'), [
    ('status', 'quarantined'),
    ('status', 'deferred'),
    ('binding_kind', 'diagnostic'),
])
def test_ready_run_allows_diagnostic_binding_with_immutable_initial_outcome(
        tmp_path, state_field, diagnostic_state):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    diagnostic = _initial_outcome()

    run = repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'candidate_snapshot': [
            {'id': 'candidate-1', 'kind': 'entity'},
            {'id': 'candidate-2', 'kind': 'attribute'},
        ],
        'initial_candidate_outcomes': [diagnostic],
        'candidate_outcomes': [diagnostic],
        'candidate_bindings': [_binding(kind='existing'), {
            'candidate_id': 'candidate-2', 'target_kind': 'attribute',
            state_field: diagnostic_state,
            'required_operation_ids': [],
            'optional_operation_ids': [],
        }],
    })

    assert run['status'] == 'ready_to_finalize'


def test_diagnosed_run_rejects_actionable_proposed_binding(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='no-draft.*operation'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'diagnosed_no_change',
            'unified_draft_id': None,
            'candidate_bindings': [{
                'candidate_id': 'candidate-1', 'binding_kind': 'proposed',
                'target_kind': 'class',
                'target_iri': 'urn:term:candidate-1',
                'required_operation_ids': ['operation-1'],
                'optional_operation_ids': [],
            }],
        })


def test_transition_updates_status_draft_link_and_outcomes_in_one_cas(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'candidate_bindings': [_binding(kind='existing')],
    })

    transitioned = repo.transition_discovery_run(
        project_id, 'discovery-run:abc', 'ready_to_finalize',
        'finalized_no_change', candidate_outcomes=[_terminal_outcome()])

    assert transitioned['status'] == 'finalized_no_change'
    assert transitioned['candidate_outcomes'] == [_terminal_outcome()]


@pytest.mark.parametrize('outcome', [
    {'candidate_id': 'candidate-1', 'status': 'materialized'},
    {**_terminal_outcome(), 'status': 'invented'},
    {**_terminal_outcome(), 'reason_code': 'invented'},
    {**_terminal_outcome(), 'unexpected': True},
    _terminal_outcome(status='materialized', reason_code='draft_closed'),
    _terminal_outcome(status='skipped', reason_code='materialized'),
])
def test_transition_rejects_invalid_terminal_outcome_schema(tmp_path, outcome):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))

    with pytest.raises(ValueError, match='terminal candidate outcome'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=[outcome])

    assert repo.get_discovery_run(project_id, original['id']) == original


@pytest.mark.parametrize(('source_status', 'destination', 'draft_id'), [
    ('draft_created', 'published', 'draft-1'),
    ('ready_to_finalize', 'finalized_no_change', None),
])
def test_success_transition_requires_outcome_for_every_unresolved_candidate(
        tmp_path, source_status, destination, draft_id):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run({
        **_run(project_id), 'status': source_status,
        'unified_draft_id': draft_id,
        'candidate_bindings': [
            _binding(kind=(
                'existing' if source_status == 'ready_to_finalize'
                else 'proposed'))],
    })

    with pytest.raises(ValueError, match='every unresolved'):
        repo.transition_discovery_run(
            project_id, original['id'], source_status, destination)

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_success_transition_rejects_partial_terminal_outcome_coverage(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run({
        **_run(project_id),
        'candidate_snapshot': [
            {'id': 'candidate-1', 'kind': 'entity'},
            {'id': 'candidate-2', 'kind': 'entity'},
        ],
        'accepted_candidate_ids': ['candidate-1', 'candidate-2'],
        'candidate_bindings': [_binding(), _binding('candidate-2')],
    })

    with pytest.raises(ValueError, match='every unresolved'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=[_terminal_outcome('candidate-1')])

    assert repo.get_discovery_run(project_id, original['id']) == original


@pytest.mark.parametrize('reason_code', [
    'required_operation_missing',
    'required_operation_rejected',
    'required_operation_superseded',
    'ontology_validation_failed',
])
def test_publish_accepts_documented_terminal_skip_reasons(tmp_path, reason_code):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))
    outcome = _terminal_outcome(
        status='skipped', reason_code=reason_code)

    published = repo.transition_discovery_run(
        project_id, original['id'], 'draft_created', 'published',
        candidate_outcomes=[outcome])

    assert published['candidate_outcomes'] == [outcome]


def test_finalize_rejects_required_operation_reason_for_reusable_binding(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'candidate_bindings': [_binding(kind='existing')],
    })

    with pytest.raises(ValueError, match='incompatible.*binding'):
        repo.transition_discovery_run(
            project_id, original['id'], 'ready_to_finalize',
            'finalized_no_change', candidate_outcomes=[_terminal_outcome(
                status='skipped', reason_code='required_operation_rejected')])

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_close_requires_draft_closed_outcome_for_every_unresolved_candidate(
        tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))
    outcome = _terminal_outcome(
        status='skipped', reason_code='draft_closed')

    closed = repo.transition_discovery_run(
        project_id, original['id'], 'draft_created', 'closed',
        candidate_outcomes=[outcome])

    assert closed['candidate_outcomes'] == [outcome]


def test_close_preserves_initial_outcomes_and_closes_only_unresolved(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    diagnostic = _initial_outcome()
    original = repo.create_discovery_run(
        _run_with_outcome(project_id, diagnostic))
    close_outcome = _terminal_outcome(
        status='skipped', reason_code='draft_closed')

    closed = repo.transition_discovery_run(
        project_id, original['id'], 'draft_created', 'closed',
        candidate_outcomes=[close_outcome])

    assert closed['candidate_outcomes'] == [diagnostic, close_outcome]


@pytest.mark.parametrize(('source_status', 'draft_id'), [
    ('draft_created', 'draft-1'),
    ('ready_to_finalize', None),
])
@pytest.mark.parametrize('destination', ['stale_base', 'stale_source'])
def test_stale_transition_preserves_explicitly_unresolved_outcomes(
        tmp_path, destination, source_status, draft_id):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run({
        **_run(project_id), 'status': source_status,
        'unified_draft_id': draft_id,
        'candidate_bindings': [_binding(kind=(
            'existing' if source_status == 'ready_to_finalize'
            else 'proposed'))],
    })

    stale = repo.transition_discovery_run(
        project_id, original['id'], source_status, destination)

    assert stale['candidate_outcomes'] == []
    assert stale['accepted_candidate_ids'] == ['candidate-1']


def test_stale_transition_rejects_terminal_candidate_outcomes(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))

    with pytest.raises(ValueError, match='stale.*outcome'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'stale_base',
            candidate_outcomes=[_terminal_outcome()])

    assert repo.get_discovery_run(project_id, original['id']) == original


@pytest.mark.parametrize('candidate_outcomes', [
    [_terminal_outcome('missing')],
    [
        _terminal_outcome(),
        _terminal_outcome(
            status='skipped', reason_code='ontology_validation_failed'),
    ],
])
def test_transition_rejects_unknown_or_duplicate_terminal_candidate_ids(
        tmp_path, candidate_outcomes):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))

    with pytest.raises(ValueError, match='candidate'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=candidate_outcomes)

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_transition_rejects_terminal_outcome_for_diagnostic_candidate(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    diagnostic = _initial_outcome()
    original = repo.create_discovery_run(
        _run_with_outcome(project_id, diagnostic))

    with pytest.raises(DiscoveryRunConflict, match='immutable discovery outcome'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=[diagnostic])

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_transition_rejects_already_resolved_accepted_candidate(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))
    resolved = _terminal_outcome()
    malformed = {**original, 'candidate_outcomes': [resolved]}
    repo._db.execute(
        'UPDATE artifacts SET payload=? WHERE id=? AND kind=?',
        (json.dumps(malformed), original['id'], 'ontology_discovery_run'))
    repo._db.commit()

    with pytest.raises(ValueError, match='unresolved'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=[resolved])

    assert repo.get_discovery_run(project_id, original['id']) == malformed


def test_mapping_only_transition_cannot_attach_a_draft(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'candidate_bindings': [_binding(kind='existing')],
    })

    with pytest.raises(DiscoveryRunConflict):
        repo.transition_discovery_run(
            project_id, original['id'], 'ready_to_finalize',
            'finalized_no_change', unified_draft_id='forged-draft')

    assert repo.get_discovery_run(project_id, original['id']) == original
    assert discovery_result_kind(original) == 'mapping_only'


def test_draft_transition_cannot_rebind_to_another_draft(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))

    with pytest.raises(DiscoveryRunConflict):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            unified_draft_id='draft-2')

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_transition_preserves_immutable_diagnostic_outcomes(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = {
        **_initial_outcome('conflict'),
        'diagnostic_code': 'class_property_name_collision',
    }
    repo.create_discovery_run(_run_with_outcome(project_id, conflict))

    transitioned = repo.transition_discovery_run(
        project_id, 'discovery-run:abc', 'draft_created', 'published',
        candidate_outcomes=[_terminal_outcome()])

    assert transitioned['candidate_outcomes'] == [
        conflict, _terminal_outcome()]


def test_transition_cannot_overwrite_immutable_diagnostic_outcome(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = _initial_outcome('conflict')
    original = repo.create_discovery_run(
        _run_with_outcome(project_id, conflict))

    with pytest.raises(DiscoveryRunConflict):
        repo.transition_discovery_run(
            project_id, 'discovery-run:abc', 'draft_created', 'published',
            candidate_outcomes=[_terminal_outcome('conflict')])

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_transition_cannot_overwrite_any_initial_outcome_reason(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    deferred = _initial_outcome(
        'deferred', status='deferred',
        reason_code='manual_review_deferred')
    original = repo.create_discovery_run(
        _run_with_outcome(project_id, deferred))

    with pytest.raises(DiscoveryRunConflict, match='immutable discovery outcome'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=[_terminal_outcome('deferred')])

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_transition_uses_initial_outcome_when_current_outcome_is_malformed(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = _initial_outcome('conflict')
    original = repo.create_discovery_run(
        _run_with_outcome(project_id, conflict))
    malformed = {
        **original,
        'candidate_outcomes': [_terminal_outcome('conflict')],
    }
    repo._db.execute(
        'UPDATE artifacts SET payload=? WHERE id=? AND kind=?',
        (json.dumps(malformed), original['id'], 'ontology_discovery_run'))
    repo._db.commit()

    with pytest.raises(DiscoveryRunConflict, match='immutable discovery outcome'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=[_terminal_outcome('conflict')])

    assert repo.get_discovery_run(project_id, original['id']) == malformed


def test_transition_without_outcome_payload_rejects_missing_initial_outcome(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    deferred = _initial_outcome(
        'deferred', status='deferred',
        reason_code='manual_review_deferred')
    original = repo.create_discovery_run(
        _run_with_outcome(project_id, deferred))
    malformed = {**original, 'candidate_outcomes': []}
    repo._db.execute(
        'UPDATE artifacts SET payload=? WHERE id=? AND kind=?',
        (json.dumps(malformed), original['id'], 'ontology_discovery_run'))
    repo._db.commit()

    with pytest.raises(DiscoveryRunConflict, match='immutable discovery outcome'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published')

    assert repo.get_discovery_run(project_id, original['id']) == malformed


def test_failed_transition_changes_neither_status_nor_outcomes(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))

    with pytest.raises(DiscoveryRunConflict):
        repo.transition_discovery_run(
            project_id, original['id'], 'ready_to_finalize', 'closed',
            candidate_outcomes=[{'candidate_id': 'candidate-1', 'status': 'closed'}])

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_store_never_delegates_to_generic_artifact_upsert(tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    monkeypatch.setattr(
        repo, 'save_artifact',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('upsert used')))

    assert repo.create_discovery_run(_run(project_id))['id'] == 'discovery-run:abc'


def test_generic_artifact_upsert_cannot_mutate_discovery_runs(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))

    with pytest.raises(ValueError, match='create_discovery_run'):
        repo.save_artifact('ontology_discovery_run', {
            **original, 'candidate_snapshot': [{'id': 'mutated'}]})

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_generic_artifact_upsert_cannot_overwrite_run_using_another_kind(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run(_run(project_id))

    with pytest.raises(ValueError, match='create_discovery_run'):
        repo.save_artifact('unrelated_artifact', {
            'id': original['id'], 'project_id': project_id,
            'payload': 'replacement',
        })

    assert repo.get_discovery_run(project_id, original['id']) == original
