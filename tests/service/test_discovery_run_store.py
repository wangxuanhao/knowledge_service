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


def test_transition_updates_status_draft_link_and_outcomes_in_one_cas(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    repo.create_discovery_run({
        **_run(project_id), 'status': 'ready_to_finalize',
        'unified_draft_id': None,
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


def test_transition_preserves_immutable_diagnostic_outcomes(tmp_path):
    repo = Repository(tmp_path / 'store.sqlite')
    project_id = repo.create_project('project')['id']
    conflict = {
        'candidate_id': 'conflict', 'status': 'skipped',
        'code': 'ontology_term_conflict',
        'diagnostic_code': 'class_property_name_collision',
    }
    repo.create_discovery_run({
        **_run(project_id), 'candidate_outcomes': [conflict],
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
        **_run(project_id), 'candidate_outcomes': [conflict],
    })

    with pytest.raises(DiscoveryRunConflict):
        repo.transition_discovery_run(
            project_id, 'discovery-run:abc', 'draft_created', 'published',
            candidate_outcomes=[{
                'candidate_id': 'conflict', 'status': 'materialized',
            }])

    assert repo.get_discovery_run(project_id, original['id']) == original


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
