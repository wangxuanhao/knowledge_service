import json

import pytest

from knowledge_service.repository import Repository
from knowledge_service.repository.discovery_run_store import (
    DiscoveryRunConflict,
    discovery_result_kind,
)


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

    run = repo.create_discovery_run({
        **_run(project_id),
        'candidate_snapshot': [{
            'id': 'candidate-1', 'kind': candidate_kind}],
        'candidate_bindings': [{
            **_binding(), 'target_kind': target_kind,
        }],
    })

    assert run['candidate_bindings'][0]['target_kind'] == target_kind


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

    with pytest.raises(ValueError, match='only reusable bindings'):
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
