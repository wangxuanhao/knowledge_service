import json

import pytest

from knowledge_service.repository import Repository
from knowledge_service.repository.discovery_run_store import (
    DiscoveryRunConflict,
    discovery_result_kind,
)


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
        'candidate_bindings': [],
        'initial_candidate_outcomes': [],
        'candidate_outcomes': [],
        'diagnostics': {},
        'status': 'draft_created',
        'unified_draft_id': 'draft-1',
        'supersedes_run_id': None,
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
    }

    first = repo.create_discovery_run(run)
    second = repo.create_discovery_run({
        **run, 'candidate_snapshot': list(reversed(candidates))})

    assert second == first
    assert [item['id'] for item in first['candidate_snapshot']] == [
        'candidate-1', 'candidate-2']


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
        })

    assert repo.get_discovery_run(project_id, 'discovery-run:abc')[
        'candidate_snapshot'] == [{'id': 'candidate-1', 'kind': 'entity'}]


def test_same_id_with_different_initial_diagnostic_outcomes_conflicts(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = {
        'candidate_id': 'candidate-1', 'status': 'skipped',
        'code': 'ontology_term_conflict',
    }
    repo.create_discovery_run({
        **_run(project_id),
        'initial_candidate_outcomes': [conflict],
        'candidate_outcomes': [conflict],
    })

    with pytest.raises(DiscoveryRunConflict):
        repo.create_discovery_run({
            **_run(project_id),
            'initial_candidate_outcomes': [{
                'candidate_id': 'candidate-1', 'status': 'skipped',
                'code': 'low_frequency_attribute',
            }],
            'candidate_outcomes': [{
                'candidate_id': 'candidate-1', 'status': 'skipped',
                'code': 'low_frequency_attribute',
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
    [{'candidate_id': 'candidate-1', 'binding_kind': 'proposed'}],
])
def test_ready_run_requires_a_reusable_existing_binding(tmp_path, bindings):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='reusable.*binding'):
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
        'candidate_bindings': [{
            'candidate_id': 'candidate-1', 'binding_kind': 'existing',
            'target_iri': 'urn:term:candidate-1',
        }],
    })

    assert run['status'] == 'ready_to_finalize'


def test_create_seeds_current_outcomes_from_explicit_initial_outcomes(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = {
        'candidate_id': 'candidate-1', 'status': 'skipped',
        'code': 'ontology_term_conflict',
    }
    payload = _run(project_id)
    payload.pop('candidate_outcomes')

    created = repo.create_discovery_run({
        **payload, 'initial_candidate_outcomes': [conflict],
    })

    assert created['candidate_outcomes'] == [conflict]


def test_create_rejects_current_outcome_that_contradicts_initial_outcome(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = {
        'candidate_id': 'candidate-1', 'status': 'skipped',
        'code': 'ontology_term_conflict',
    }

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
    conflict = {
        'candidate_id': 'candidate-1', 'status': 'skipped',
        'code': 'ontology_term_conflict',
    }

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
    {'candidate_id': 'candidate-1', 'binding_kind': 'existing'},
    {'candidate_id': 'candidate-1', 'status': 'existing'},
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
            'reuse_iri': 'urn:term:candidate-1',
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
    diagnostic = {
        'candidate_id': 'candidate-1', 'status': 'skipped',
        'code': 'manual_review_deferred',
    }

    with pytest.raises(ValueError, match='operation IDs'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'diagnosed_no_change',
            'unified_draft_id': None,
            'initial_candidate_outcomes': [diagnostic],
            'candidate_outcomes': [diagnostic],
            'candidate_bindings': [{
                'candidate_id': 'candidate-1', 'status': 'deferred',
                operation_field: ['operation-1'],
            }],
        })


def test_ready_run_rejects_operations_on_quarantined_binding(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    diagnostic = {
        'candidate_id': 'candidate-2', 'status': 'skipped',
        'code': 'quarantined_candidate',
    }

    with pytest.raises(ValueError, match='operation IDs'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'ready_to_finalize',
            'unified_draft_id': None,
            'initial_candidate_outcomes': [diagnostic],
            'candidate_outcomes': [diagnostic],
            'candidate_bindings': [{
                'candidate_id': 'candidate-1', 'binding_kind': 'existing',
                'target_iri': 'urn:term:candidate-1',
            }, {
                'candidate_id': 'candidate-2', 'status': 'quarantined',
                'optional_operation_ids': ['operation-1'],
            }],
        })


@pytest.mark.parametrize('invalid_binding', [
    {
        'candidate_id': 'candidate-2', 'binding_kind': 'proposed',
        'required_operation_ids': [],
    },
    {
        'candidate_id': 'candidate-2', 'binding_kind': 'new',
        'required_operation_ids': [],
    },
    {
        'candidate_id': 'candidate-2', 'binding_kind': 'existing',
        'required_operation_ids': ['operation-1'],
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
            'candidate_bindings': [{
                'candidate_id': 'candidate-1', 'binding_kind': 'existing',
                'required_operation_ids': [],
            }, invalid_binding],
        })


@pytest.mark.parametrize('diagnostic_status', ['quarantined', 'deferred'])
def test_ready_run_allows_diagnostic_binding_with_immutable_initial_outcome(
        tmp_path, diagnostic_status):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    diagnostic = {
        'candidate_id': 'candidate-2', 'status': 'skipped',
        'code': f'{diagnostic_status}_candidate',
    }

    run = repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'initial_candidate_outcomes': [diagnostic],
        'candidate_outcomes': [diagnostic],
        'candidate_bindings': [{
            'candidate_id': 'candidate-1', 'binding_kind': 'existing',
            'target_iri': 'urn:term:candidate-1',
            'required_operation_ids': [],
        }, {
            'candidate_id': 'candidate-2', 'binding_kind': 'proposed',
            'status': diagnostic_status,
            'required_operation_ids': [],
            'optional_operation_ids': [],
        }],
    })

    assert run['status'] == 'ready_to_finalize'


def test_diagnosed_run_rejects_actionable_proposed_binding(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']

    with pytest.raises(ValueError, match='diagnosed_no_change.*binding'):
        repo.create_discovery_run({
            **_run(project_id), 'status': 'diagnosed_no_change',
            'unified_draft_id': None,
            'candidate_bindings': [{
                'candidate_id': 'candidate-1', 'binding_kind': 'proposed',
                'target_iri': 'urn:term:candidate-1',
                'required_operation_ids': [],
            }],
        })


def test_transition_updates_status_draft_link_and_outcomes_in_one_cas(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'candidate_bindings': [{
            'candidate_id': 'candidate-1', 'binding_kind': 'existing',
            'target_iri': 'urn:term:candidate-1',
        }],
    })

    transitioned = repo.transition_discovery_run(
        project_id, 'discovery-run:abc', 'ready_to_finalize',
        'finalized_no_change', candidate_outcomes=[{
            'candidate_id': 'candidate-1', 'status': 'materialized',
        }])

    assert transitioned['status'] == 'finalized_no_change'
    assert transitioned['candidate_outcomes'] == [{
        'candidate_id': 'candidate-1', 'status': 'materialized',
    }]


def test_mapping_only_transition_cannot_attach_a_draft(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    original = repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
        'candidate_bindings': [{
            'candidate_id': 'candidate-1', 'binding_kind': 'existing',
            'target_iri': 'urn:term:candidate-1',
        }],
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
        'candidate_id': 'conflict', 'status': 'skipped',
        'code': 'ontology_term_conflict',
        'diagnostic_code': 'class_property_name_collision',
    }
    repo.create_discovery_run({
        **_run(project_id), 'initial_candidate_outcomes': [conflict],
        'candidate_outcomes': [conflict],
    })

    transitioned = repo.transition_discovery_run(
        project_id, 'discovery-run:abc', 'draft_created', 'published',
        candidate_outcomes=[{
            'candidate_id': 'candidate-1', 'status': 'materialized',
        }])

    assert transitioned['candidate_outcomes'] == [conflict, {
        'candidate_id': 'candidate-1', 'status': 'materialized',
    }]


def test_transition_cannot_overwrite_immutable_diagnostic_outcome(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = {
        'candidate_id': 'conflict', 'status': 'skipped',
        'code': 'ontology_term_conflict',
    }
    original = repo.create_discovery_run({
        **_run(project_id), 'initial_candidate_outcomes': [conflict],
        'candidate_outcomes': [conflict],
    })

    with pytest.raises(DiscoveryRunConflict):
        repo.transition_discovery_run(
            project_id, 'discovery-run:abc', 'draft_created', 'published',
            candidate_outcomes=[{
                'candidate_id': 'conflict', 'status': 'materialized',
            }])

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_transition_cannot_overwrite_any_initial_outcome_reason(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    deferred = {
        'candidate_id': 'deferred', 'status': 'skipped',
        'code': 'manual_review_deferred',
    }
    original = repo.create_discovery_run({
        **_run(project_id), 'initial_candidate_outcomes': [deferred],
        'candidate_outcomes': [deferred],
    })

    with pytest.raises(DiscoveryRunConflict, match='immutable discovery outcome'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=[{
                'candidate_id': 'deferred', 'status': 'materialized',
            }])

    assert repo.get_discovery_run(project_id, original['id']) == original


def test_transition_uses_initial_outcome_when_current_outcome_is_malformed(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = {
        'candidate_id': 'conflict', 'status': 'skipped',
        'code': 'ontology_term_conflict',
    }
    original = repo.create_discovery_run({
        **_run(project_id), 'initial_candidate_outcomes': [conflict],
        'candidate_outcomes': [conflict],
    })
    malformed = {
        **original,
        'candidate_outcomes': [{
            'candidate_id': 'conflict', 'status': 'materialized',
        }],
    }
    repo._db.execute(
        'UPDATE artifacts SET payload=? WHERE id=? AND kind=?',
        (json.dumps(malformed), original['id'], 'ontology_discovery_run'))
    repo._db.commit()

    with pytest.raises(DiscoveryRunConflict, match='immutable discovery outcome'):
        repo.transition_discovery_run(
            project_id, original['id'], 'draft_created', 'published',
            candidate_outcomes=[{
                'candidate_id': 'conflict', 'status': 'materialized',
            }])

    assert repo.get_discovery_run(project_id, original['id']) == malformed


def test_transition_without_outcome_payload_rejects_missing_initial_outcome(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    deferred = {
        'candidate_id': 'deferred', 'status': 'skipped',
        'code': 'manual_review_deferred',
    }
    original = repo.create_discovery_run({
        **_run(project_id), 'initial_candidate_outcomes': [deferred],
        'candidate_outcomes': [deferred],
    })
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
