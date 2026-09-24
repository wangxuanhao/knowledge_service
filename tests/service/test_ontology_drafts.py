import pytest
from rdflib import RDFS, URIRef

from knowledge_service.repository import OntologyPublicationConflict, Repository
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_drafts import (
    BatchNotAllowed,
    OntologyDrafts,
    RevisionConflict,
    StaleBase,
    StaleSource,
    ValidationChanged,
    ValidationFailed,
)


BASE = '''
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Root a owl:Class ; rdfs:label "Root" .
ex:Child a owl:Class ; rdfs:subClassOf ex:Root ; rdfs:label "Child" .
ex:Legacy a owl:Class ; owl:deprecated true .
ex:rel a owl:ObjectProperty ; rdfs:domain ex:Root ; rdfs:range ex:Child .
'''


def setup_service(tmp_path, *, publisher=None, with_base=True):
    repo = Repository(tmp_path / 'drafts.sqlite')
    project_id = repo.create_project('draft lifecycle')['id']
    base = repo.save_ontology(project_id, BASE, {}) if with_base else None
    return repo, OntologyDrafts(repo, publisher=publisher), project_id, base


def add_label(service, project_id, draft, value='Renamed'):
    return service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': value,
        'language': 'en',
    })


def test_create_command_supersede_preview_submit_and_close(tmp_path):
    repo, service, project_id, _ = setup_service(tmp_path, with_base=False)
    draft = service.create(
        project_id, None, 'manual', 'First ontology', 'author',
        source_context={'ticket': 'ONT-1'})
    assert draft['base_ontology_id'] is None
    assert draft['status'] == 'editing'
    assert draft['source_context']['actor'] == 'author'

    preview = service.command(project_id, draft['id'], 1, {
        'action': 'create_term', 'target_iri': 'urn:test:Thing', 'kind': 'class'})
    first = preview['operations'][0]
    adjusted = service.command(project_id, draft['id'], 2, {
        'action': 'create_term', 'target_iri': 'urn:test:Thing', 'kind': 'class',
        'supersedes_operation_id': first['id'], 'reason': 'clarified'})
    assert adjusted['revision'] == 3
    assert adjusted['operations'][0]['supersedes_operation_id'] == first['id']
    assert 'urn:test:Thing' in adjusted['turtle']

    submitted = service.submit(project_id, draft['id'], 3)
    assert submitted['status'] == 'submitted'
    disposable = service.create(
        project_id, None, 'manual', 'Disposable', 'author')
    closed = service.close(
        project_id, disposable['id'], disposable['revision'], 'author', 'abandoned')
    assert closed['status'] == 'closed'
    assert closed['source_context']['closure'] == {
        'actor': 'author', 'reason': 'abandoned'}
    assert len(repo._ontology_drafts.export(project_id)['operations']) == 2


def test_command_uses_sql_cas_without_leaking_an_operation(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'CAS', 'author')
    current = add_label(service, project_id, draft, 'Current')

    with pytest.raises(RevisionConflict) as caught:
        add_label(service, project_id, draft, 'Stale')

    assert caught.value.code == 'revision_conflict'
    history = repo._ontology_drafts.export(project_id)
    assert [row['after']['value'] for row in history['operations']] == ['Current']
    assert repo._ontology_drafts.get(project_id, draft['id'])['revision'] == (
        current['revision'])


def test_restore_freezes_explicit_same_project_source_template(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    retired_turtle = BASE.replace(
        'ex:Child a owl:Class ;',
        'ex:Child a owl:Class ; owl:deprecated true ;')
    retired = repo.save_ontology(project_id, retired_turtle, {})
    draft = service.create(project_id, retired['id'], 'manual', 'restore', 'a')

    with pytest.raises(ValueError, match='source_ontology_id'):
        service.command(project_id, draft['id'], 1, {
            'action': 'restore_term', 'target_iri': 'https://example.test/Child',
            'selected_fields': ['annotations']})
    with pytest.raises(ValueError, match='显式选择'):
        service.command(project_id, draft['id'], 1, {
            'action': 'restore_term', 'target_iri': 'https://example.test/Child',
            'source_ontology_id': base['id']})

    result = service.command(project_id, draft['id'], 1, {
        'action': 'restore_term', 'target_iri': 'https://example.test/Child',
        'source_ontology_id': base['id'],
        'selected_fields': ['annotations', 'parents']})
    operation = result['operations'][0]
    assert operation['after']['source_ontology_id'] == base['id']
    assert operation['after']['template']['parents'] == ['https://example.test/Root']

    other = repo.create_project('other')['id']
    foreign = repo.save_ontology(other, BASE, {})
    another = service.create(project_id, retired['id'], 'manual', 'bad restore', 'a')
    with pytest.raises(ValueError, match='不属于此项目'):
        service.command(project_id, another['id'], 1, {
            'action': 'restore_term', 'target_iri': 'https://example.test/Child',
            'source_ontology_id': foreign['id'], 'selected_fields': ['annotations']})


def test_decisions_require_reasons_bind_fingerprints_and_complete_states(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'review', 'a')
    preview = add_label(service, project_id, draft)
    submitted = service.submit(project_id, draft['id'], preview['revision'])
    operation = submitted['operations'][0]

    with pytest.raises(ValueError, match='reason'):
        service.decide(project_id, draft['id'], submitted['revision'], base['id'],
                       submitted['validation_fingerprint'], [{
                           'operation_id': operation['id'],
                           'operation_fingerprint': operation['fingerprint'],
                           'action': 'reject'}], [], 'reviewer')

    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    assert reviewed['status'] == 'reviewed'
    assert reviewed['decisions'][0]['operation_fingerprint'] == operation['fingerprint']


def test_request_changes_returns_to_editing_and_all_rejected_closes(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'review', 'a')
    submitted = service.submit(
        project_id, draft['id'], add_label(service, project_id, draft)['revision'])
    op = submitted['operations'][0]
    changed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': op['id'], 'operation_fingerprint': op['fingerprint'],
            'action': 'request_changes', 'reason': 'use another label'}], [], 'reviewer')
    assert changed['status'] == 'editing'

    second = service.create(project_id, base['id'], 'manual', 'reject', 'a')
    second = service.submit(
        project_id, second['id'], add_label(service, project_id, second, 'No')['revision'])
    op = second['operations'][0]
    rejected = service.decide(
        project_id, second['id'], second['revision'], base['id'],
        second['validation_fingerprint'], [{
            'operation_id': op['id'], 'operation_fingerprint': op['fingerprint'],
            'action': 'reject', 'reason': 'not useful'}], [], 'reviewer')
    assert rejected['status'] == 'closed'


def test_batch_approval_is_capped_and_low_risk_only(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'batch', 'a')
    preview = service.command(project_id, draft['id'], 1, {
        'action': 'remove_parent', 'target_iri': 'https://example.test/Child',
        'parent_iri': 'https://example.test/Root'})
    submitted = service.submit(project_id, draft['id'], preview['revision'])
    op = submitted['operations'][0]
    with pytest.raises(BatchNotAllowed):
        service.decide(
            project_id, draft['id'], submitted['revision'], base['id'],
            submitted['validation_fingerprint'], [
                {'operation_id': op['id'], 'operation_fingerprint': op['fingerprint'],
                 'action': 'approve'},
                {'operation_id': op['id'], 'operation_fingerprint': op['fingerprint'],
                 'action': 'approve'},
            ], [], 'reviewer')


def test_low_risk_batch_succeeds_and_requests_are_capped_at_100(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'safe batch', 'author')
    first = add_label(service, project_id, draft, 'Child label')
    second = service.command(project_id, draft['id'], first['revision'], {
        'action': 'add_annotation', 'target_iri': 'https://example.test/Root',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Root label', 'language': 'en'})
    submitted = service.submit(project_id, draft['id'], second['revision'])
    decisions = [{
        'operation_id': operation['id'],
        'operation_fingerprint': operation['fingerprint'],
        'action': 'approve',
    } for operation in submitted['operations']]

    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], decisions, [], 'reviewer')
    assert reviewed['status'] == 'reviewed'

    capped = service.create(project_id, base['id'], 'manual', 'cap', 'author')
    capped = service.submit(
        project_id, capped['id'], add_label(
            service, project_id, capped, 'Cap')['revision'])
    operation = capped['operations'][0]
    repeated = [{
        'operation_id': operation['id'],
        'operation_fingerprint': operation['fingerprint'],
        'action': 'approve',
    }] * 101
    with pytest.raises(BatchNotAllowed) as caught:
        service.decide(
            project_id, capped['id'], capped['revision'], base['id'],
            capped['validation_fingerprint'], repeated, [], 'reviewer')
    assert caught.value.code == 'batch_not_allowed'


def test_request_changes_requires_reason_and_medium_approval_does_not(tmp_path):
    repo, service, project_id, _ = setup_service(tmp_path)
    current = repo.save_ontology(
        project_id, BASE + '\n<https://example.test/Other> a '
        '<http://www.w3.org/2002/07/owl#Class> .\n', {})
    draft = service.create(project_id, current['id'], 'manual', 'medium', 'author')
    changed = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_parent', 'target_iri': 'https://example.test/Other',
        'parent_iri': 'https://example.test/Root'})
    submitted = service.submit(project_id, draft['id'], changed['revision'])
    operation = submitted['operations'][0]
    assert operation['risk'] == 'medium'

    with pytest.raises(ValueError, match='reason'):
        service.decide(
            project_id, draft['id'], submitted['revision'], current['id'],
            submitted['validation_fingerprint'], [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'request_changes'}], [], 'reviewer')

    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], current['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    assert reviewed['status'] == 'reviewed'


def test_validation_fingerprint_and_warning_acknowledgement(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'warnings', 'a')
    preview = service.command(project_id, draft['id'], 1, {
        'action': 'add_annotation', 'target_iri': 'https://example.test/Child',
        'predicate': 'https://example.test/note',
        'value': 'https://example.test/Legacy', 'value_type': 'iri'})
    submitted = service.submit(project_id, draft['id'], preview['revision'])
    report = submitted['validation_report']
    assert set(report) >= {
        'graph_integrity', 'prospective_new_write_contract', 'historical_impact',
        'errors', 'warnings', 'info', 'conforms'}
    op = submitted['operations'][0]
    with pytest.raises(ValidationChanged):
        service.decide(project_id, draft['id'], submitted['revision'], base['id'],
                       'wrong', [{
                           'operation_id': op['id'],
                           'operation_fingerprint': op['fingerprint'],
                           'action': 'approve', 'reason': 'checked'}],
                       ['confirm_note'], 'reviewer')
    with pytest.raises(
            ValidationFailed, match='active_custom_annotation_dependency') as caught:
        service.decide(project_id, draft['id'], submitted['revision'], base['id'],
                       submitted['validation_fingerprint'], [{
                           'operation_id': op['id'],
                           'operation_fingerprint': op['fingerprint'],
                           'action': 'approve', 'reason': 'checked'}], [], 'reviewer')
    assert caught.value.code == 'validation_failed'
    with pytest.raises(ValueError, match='reason'):
        service.decide(project_id, draft['id'], submitted['revision'], base['id'],
                       submitted['validation_fingerprint'], [{
                           'operation_id': op['id'],
                           'operation_fingerprint': op['fingerprint'],
                           'action': 'approve', 'reason': ''}],
                       ['active_custom_annotation_dependency'], 'reviewer')


def test_validation_fingerprint_binds_the_rule_version(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'rules', 'author')
    submitted = service.submit(
        project_id, draft['id'], add_label(service, project_id, draft)['revision'])
    operation = submitted['operations'][0]
    changed_rules = OntologyDrafts(
        repo, validation_rule_version='ontology-drafts/next')

    with pytest.raises(ValidationChanged) as caught:
        changed_rules.decide(
            project_id, draft['id'], submitted['revision'], base['id'],
            submitted['validation_fingerprint'], [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve'}], [], 'reviewer')

    assert caught.value.code == 'validation_changed'
    assert caught.value.details['report']['rule_version'] == 'ontology-drafts/next'


def test_stale_base_and_source_are_persisted(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'stale', 'a')
    repo.save_ontology(project_id, BASE + '\n# next\n', {})
    with pytest.raises(StaleBase):
        service.submit(project_id, draft['id'], 1)
    assert repo._ontology_drafts.get(project_id, draft['id'])['status'] == 'stale_base'

    current = repo.get_ontology(project_id)
    document = repo.put_record(project_id, {
        'id': 'doc', 'kind': 'document', 'text': 'source',
        'metadata': {'status': 'ready', 'review_candidates': [
            {'id': 'candidate', 'status': 'pending'}]}})
    source = {'document_id': 'doc', 'expected_document_version': document['version'],
              'candidate_id': 'candidate', 'expected_candidate_status': 'pending'}
    candidate_draft = service.create(
        project_id, current['id'], 'candidate', 'candidate', 'a',
        source_context=source)
    repo.put_record(project_id, {
        'id': document['id'], 'kind': document['kind'], 'text': 'changed',
        'metadata': document['metadata']},
                    expected_version=document['version'])
    with pytest.raises(StaleSource):
        service.validate(project_id, candidate_draft['id'], 1)
    assert repo._ontology_drafts.get(
        project_id, candidate_draft['id'])['status'] == 'stale_source'


def test_source_context_is_frozen_by_the_service_and_detects_later_changes(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    document = repo.put_record(project_id, {
        'id': 'source-doc', 'kind': 'document', 'text': 'source',
        'metadata': {'review_candidates': [
            {'id': 'candidate', 'status': 'pending', 'label': 'before'}]}})
    draft = service.create(
        project_id, base['id'], 'candidate', 'frozen source', 'author',
        source_context={'document_id': document['id'], 'candidate_id': 'candidate'})

    context = draft['source_context']
    assert context['expected_document_version'] == document['version']
    assert context['candidate_statuses']['candidate'] == 'pending'
    assert context['candidate_fingerprints']['candidate']

    repo.put_record(project_id, {
        'id': document['id'], 'kind': document['kind'], 'text': 'changed',
        'metadata': document['metadata']}, expected_version=document['version'])
    with pytest.raises(StaleSource) as caught:
        service.validate(project_id, draft['id'], draft['revision'])
    assert caught.value.code == 'stale_source'


def test_candidate_command_rejects_evidence_outside_frozen_source_snapshot(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    document = repo.put_record(project_id, {
        'id': 'doc-1', 'kind': 'document', 'text': 'source',
        'metadata': {'review_candidates': [
            {'id': 'cand-1', 'status': 'pending'}]}})
    draft = service.create(
        project_id, base['id'], 'candidate', 'candidate evidence', 'author',
        source_context={'document_id': document['id'], 'candidate_id': 'cand-1'})

    command = {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Candidate label', 'language': 'en',
        'evidence_refs': ['forged:anything'],
    }
    with pytest.raises(ValueError, match='evidence'):
        service.command(project_id, draft['id'], draft['revision'], command)

    assert repo._ontology_drafts.export(project_id)['operations'] == []
    accepted = service.command(
        project_id, draft['id'], draft['revision'],
        {key: value for key, value in command.items() if key != 'evidence_refs'})
    evidence = accepted['operations'][0]['evidence']
    assert 'candidate:cand-1' in evidence
    assert any(ref.startswith('document-version:doc-1:') for ref in evidence)


def test_create_rejects_an_explicitly_stale_source_snapshot(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    document = repo.put_record(project_id, {
        'id': 'source-doc', 'kind': 'document', 'text': 'v1', 'metadata': {}})
    updated = repo.put_record(project_id, {
        'id': document['id'], 'kind': document['kind'], 'text': 'v2',
        'metadata': document['metadata']}, expected_version=document['version'])

    with pytest.raises(StaleSource) as caught:
        service.create(
            project_id, base['id'], 'discovery', 'stale source', 'author',
            source_context={
                'document_id': updated['id'],
                'expected_document_version': document['version'],
            })

    assert caught.value.code == 'stale_source'
    assert repo._ontology_drafts.list(project_id) == []


def test_rebase_cannot_refresh_a_source_candidate_that_disappeared(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    document = repo.put_record(project_id, {
        'id': 'source-doc', 'kind': 'document', 'text': 'source',
        'metadata': {'review_candidates': [
            {'id': 'candidate', 'status': 'pending'}]}})
    draft = service.create(
        project_id, base['id'], 'candidate', 'candidate source', 'author',
        source_context={'document_id': document['id'], 'candidate_id': 'candidate'})
    repo.put_record(project_id, {
        'id': document['id'], 'kind': document['kind'], 'text': 'source',
        'metadata': {'review_candidates': []}},
                    expected_version=document['version'])
    with pytest.raises(StaleSource):
        service.validate(project_id, draft['id'], draft['revision'])
    stale = repo._ontology_drafts.get(project_id, draft['id'])

    with pytest.raises(StaleSource):
        service.rebase(project_id, draft['id'], stale['revision'], base['id'])

    persisted = repo._ontology_drafts.get(project_id, draft['id'])
    assert persisted['status'] == 'stale_source'
    assert persisted['revision'] == stale['revision']


def test_rebase_preserves_each_operation_source_selection(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    document = repo.put_record(project_id, {
        'id': 'source-doc', 'kind': 'document', 'text': 'source-v1',
        'metadata': {'review_candidates': [
            {'id': 'c1', 'status': 'pending'},
            {'id': 'c2', 'status': 'pending'},
        ]}})
    draft = service.create(
        project_id, base['id'], 'candidate', 'candidate source', 'author',
        source_context={
            'document_id': document['id'], 'candidate_ids': ['c1', 'c2']})
    changed = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Selected candidate', 'language': 'en',
        'evidence_refs': ['candidate:c1'],
    })
    old_version = draft['source_context']['expected_document_version']
    repo.put_record(project_id, {
        'id': document['id'], 'kind': document['kind'], 'text': 'source-v2',
        'metadata': document['metadata']}, expected_version=document['version'])
    with pytest.raises(StaleSource):
        service.validate(project_id, draft['id'], changed['revision'])
    stale = repo._ontology_drafts.get(project_id, draft['id'])

    rebased = service.rebase(
        project_id, draft['id'], stale['revision'], base['id'])

    operation = rebased['operations'][0]
    assert operation['evidence'] == ['candidate:c1']
    assert 'candidate:c2' not in operation['evidence']
    assert operation['validation']['source_snapshot_fingerprint']
    assert rebased['source_context']['expected_document_version'] != old_version


def test_wrong_expected_ontology_does_not_poison_a_current_draft(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'current', 'author')
    submitted = service.submit(
        project_id, draft['id'], add_label(service, project_id, draft)['revision'])
    operation = submitted['operations'][0]

    with pytest.raises(StaleBase) as caught:
        service.decide(
            project_id, draft['id'], submitted['revision'], 'not-current',
            submitted['validation_fingerprint'], [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve'}], [], 'reviewer')

    assert caught.value.code == 'stale_base'
    persisted = repo._ontology_drafts.get(project_id, draft['id'])
    assert persisted['status'] == 'submitted'
    assert persisted['revision'] == submitted['revision']


def test_rebase_latest_classifies_noop_and_invalidates_changed_decision(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'rebase', 'a')
    preview = add_label(service, project_id, draft, 'Already there')
    submitted = service.submit(project_id, draft['id'], preview['revision'])
    op = submitted['operations'][0]
    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': op['id'], 'operation_fingerprint': op['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    new_turtle = BASE + '''\n<https://example.test/Child>
      <http://www.w3.org/2000/01/rdf-schema#label> "Already there"@en .\n'''
    latest = repo.save_ontology(project_id, new_turtle, {})
    with pytest.raises(StaleBase):
        service.validate(project_id, draft['id'], reviewed['revision'])
    stale = repo._ontology_drafts.get(project_id, draft['id'])
    rebased = service.rebase(
        project_id, draft['id'], stale['revision'], latest['id'])
    assert rebased['status'] == 'editing'
    assert rebased['operations'][0]['validation']['rebase_status'] == 'no-op'
    assert rebased['decisions'] == []


def test_clean_rebase_retains_decision_and_resubmit_completes_review(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'clean rebase', 'a')
    submitted = service.submit(
        project_id, draft['id'], add_label(service, project_id, draft, 'Stable')['revision'])
    operation = submitted['operations'][0]
    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    latest = repo.save_ontology(
        project_id, BASE + '\n<https://example.test/Root> <https://example.test/note> "x" .', {})
    with pytest.raises(StaleBase):
        service.validate(project_id, draft['id'], reviewed['revision'])
    stale = repo._ontology_drafts.get(project_id, draft['id'])
    rebased = service.rebase(project_id, draft['id'], stale['revision'], latest['id'])
    assert rebased['rebase'][0]['classification'] == 'clean'
    assert rebased['operations'][0]['fingerprint'] == operation['fingerprint']
    assert rebased['decisions'][0]['action'] == 'approve'
    resubmitted = service.submit(project_id, draft['id'], rebased['revision'])
    assert resubmitted['status'] == 'reviewed'


def test_rebase_classifies_cycle_against_latest_as_conflict(tmp_path):
    repo, service, project_id, _ = setup_service(tmp_path)
    starting = repo.save_ontology(
        project_id, BASE + '\n<https://example.test/Other> a '
        '<http://www.w3.org/2002/07/owl#Class> .\n', {})
    draft = service.create(project_id, starting['id'], 'manual', 'conflict', 'author')
    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_parent', 'target_iri': 'https://example.test/Other',
        'parent_iri': 'https://example.test/Child'})
    latest = repo.save_ontology(
        project_id, BASE + '''
<https://example.test/Other> a <http://www.w3.org/2002/07/owl#Class> .
<https://example.test/Child> <http://www.w3.org/2000/01/rdf-schema#subClassOf>
  <https://example.test/Other> .
''', {})

    rebased = service.rebase(
        project_id, draft['id'], preview['revision'], latest['id'])

    assert rebased['rebase'][0]['classification'] == 'conflict'
    assert rebased['operations'][0]['validation']['rebase_status'] == 'conflict'
    submitted = service.submit(project_id, draft['id'], rebased['revision'])
    assert submitted['status'] == 'submitted'
    assert submitted['validation_report']['conforms'] is False


def test_rebase_recomputes_retirement_dependencies_on_latest_base(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'retire root', 'author')
    blocked = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'retire_term', 'target_iri': 'https://example.test/Root'})
    original = blocked['operations'][0]
    assert {issue['code'] for issue in original['validation']['errors']} >= {
        'active_child_dependency', 'active_domain_dependency'}

    independent = BASE.replace(
        'ex:Child a owl:Class ; rdfs:subClassOf ex:Root ; rdfs:label "Child" .',
        'ex:Child a owl:Class ; rdfs:label "Child" .').replace(
            'ex:rel a owl:ObjectProperty ; rdfs:domain ex:Root ; rdfs:range ex:Child .',
            'ex:rel a owl:ObjectProperty ; rdfs:domain ex:Child ; rdfs:range ex:Child .')
    latest = repo.save_ontology(project_id, independent, {})
    rebased = service.rebase(
        project_id, draft['id'], blocked['revision'], latest['id'])

    operation = rebased['operations'][0]
    assert rebased['rebase'][0]['classification'] == 'clean'
    assert operation['id'] != original['id']
    assert operation['validation'].get('errors') == []
    assert operation['validation'].get('warnings') == []
    assert operation['impact']['descendants'] == 0
    assert operation['impact']['constraints'] == 0
    assert operation['impact']['dependency_report']['errors'] == []
    submitted = service.submit(
        project_id, draft['id'], rebased['revision'])
    assert submitted['validation_report']['conforms'] is True


def test_publish_preflight_delegates_without_partial_service_commit(tmp_path):
    calls = []

    def publisher(**prepared):
        calls.append(prepared)
        return {'id': 'published-by-task-5', 'project_id': prepared['project_id']}

    repo, service, project_id, base = setup_service(tmp_path, publisher=publisher)
    draft = service.create(project_id, base['id'], 'manual', 'publish', 'a')
    submitted = service.submit(
        project_id, draft['id'], add_label(service, project_id, draft)['revision'])
    op = submitted['operations'][0]
    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': op['id'], 'operation_fingerprint': op['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    with pytest.raises(RevisionConflict) as caught:
        service.publish(
            project_id, draft['id'], reviewed['revision'] - 1, base['id'],
            reviewed['validation_fingerprint'], [], 'stale', 'publisher')
    assert caught.value.code == 'revision_conflict'
    assert calls == []
    result = service.publish(
        project_id, draft['id'], reviewed['revision'], base['id'],
        reviewed['validation_fingerprint'], [], 'once', 'publisher')
    assert result['id'] == 'published-by-task-5'
    assert calls[0]['turtle']
    assert calls[0]['idempotency_key'] == 'once'
    assert repo._ontology_drafts.get(project_id, draft['id'])['status'] == 'reviewed'


def test_atomic_publish_rolls_back_is_idempotent_and_records_provenance(
        tmp_path, monkeypatch):
    repo, service, project_id, base = setup_service(tmp_path)
    service.publisher = repo
    draft = service.create(project_id, base['id'], 'manual', 'atomic publish', 'author')
    submitted = service.submit(
        project_id, draft['id'], add_label(service, project_id, draft)['revision'])
    operation = submitted['operations'][0]
    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    prepared = service.publish_preflight(
        project_id, draft['id'], reviewed['revision'], base['id'],
        reviewed['validation_fingerprint'], [], 'publish-once', 'publisher')
    conflicting = service.publish_preflight(
        project_id, draft['id'], reviewed['revision'], base['id'],
        reviewed['validation_fingerprint'], [], 'publish-once', 'other-publisher')

    insert = repo._insert_ontology_version

    def fail_after_insert(*args, **kwargs):
        insert(*args, **kwargs)
        raise RuntimeError('after ontology insert')

    monkeypatch.setattr(repo, '_insert_ontology_version', fail_after_insert)
    with pytest.raises(RuntimeError, match='after ontology insert'):
        repo.publish_ontology_draft(**prepared)
    assert [row['id'] for row in repo.list_ontologies(project_id)] == [base['id']]
    assert repo._ontology_drafts.get(project_id, draft['id'])['status'] == 'reviewed'
    assert repo._ontology_drafts.export(project_id)['publish_requests'] == []
    assert repo.list_provenance_activities(project_id, kind='ontology_publish') == []

    monkeypatch.setattr(repo, '_insert_ontology_version', insert)
    ontology = repo.publish_ontology_draft(**prepared)
    assert repo.publish_ontology_draft(**prepared)['id'] == ontology['id']
    with pytest.raises(OntologyPublicationConflict) as caught:
        repo.publish_ontology_draft(**conflicting)
    assert caught.value.code == 'idempotency_conflict'
    assert service.publish(
        project_id, draft['id'], reviewed['revision'], base['id'],
        reviewed['validation_fingerprint'], [], 'publish-once', 'publisher'
    )['id'] == ontology['id']

    published = repo._ontology_drafts.get(project_id, draft['id'])
    assert published['status'] == 'published'
    assert published['published_ontology_id'] == ontology['id']
    assert len(repo.list_ontologies(project_id)) == 2
    activities = repo.list_provenance_activities(project_id)
    assert {row['kind'] for row in activities} >= {
        'ontology_draft', 'ontology_publish'}
    edges = repo.list_provenance_edges(project_id)
    assert {row['relation'] for row in edges} >= {
        'published-from', 'contains-operation', 'decided-by', 'proposed-by',
        'based-on'}
    assert any(
        row['source_ref'] == f"ontology-version:{ontology['id']}"
        and row['target_ref'] == f"ontology-draft:{draft['id']}"
        for row in edges)


def _review_candidate_draft(repo, service, project_id, base, document_id):
    document = repo.put_record(project_id, {
        'id': document_id, 'kind': 'document', 'text': 'source',
        'metadata': {'review_candidates': [
            {'id': 'candidate-1', 'status': 'pending'}]}})
    draft = service.create(
        project_id, base['id'], 'candidate', 'candidate publish', 'author',
        source_context={
            'document_id': document_id, 'candidate_id': 'candidate-1'})
    changed = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Candidate publish', 'language': 'en',
        'evidence_refs': ['candidate:candidate-1'],
    })
    submitted = service.submit(project_id, draft['id'], changed['revision'])
    operation = submitted['operations'][0]
    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    return document, draft, reviewed


def test_atomic_publish_rechecks_source_inside_repository_transaction(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    service.publisher = repo
    document, draft, reviewed = _review_candidate_draft(
        repo, service, project_id, base, 'stale-publish-source')
    prepared = service.publish_preflight(
        project_id, draft['id'], reviewed['revision'], base['id'],
        reviewed['validation_fingerprint'], [], 'stale-source', 'publisher')
    repo.put_record(project_id, {
        'id': document['id'], 'kind': document['kind'], 'text': 'changed',
        'metadata': document['metadata']}, expected_version=document['version'])

    with pytest.raises(OntologyPublicationConflict) as caught:
        repo.publish_ontology_draft(**prepared)

    assert caught.value.code == 'stale_source'
    assert len(repo.list_ontologies(project_id)) == 1
    assert repo._ontology_drafts.export(project_id)['publish_requests'] == []
    assert repo._ontology_drafts.get(project_id, draft['id'])['status'] == 'reviewed'


def test_candidate_publish_persists_sync_job_before_after_commit_runner(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    service.publisher = repo
    _, draft, reviewed = _review_candidate_draft(
        repo, service, project_id, base, 'sync-source')
    observed = []

    def fail_sync(job):
        ontology = repo.get_ontology(project_id, job['ontology_id'])
        artifact = repo.get_artifact('ontology_sync_job', job['id'])
        observed.append((ontology['id'], artifact['status']))
        raise RuntimeError('vector service unavailable')

    repo._ontology_sync_runner = fail_sync
    ontology = service.publish(
        project_id, draft['id'], reviewed['revision'], base['id'],
        reviewed['validation_fingerprint'], [], 'sync-once', 'publisher')

    jobs = repo.list_artifacts('ontology_sync_job', project_id)
    assert observed == [(ontology['id'], 'pending')]
    assert len(jobs) == 1
    assert jobs[0]['status'] == 'pending'
    assert jobs[0]['ontology_id'] == ontology['id']
    assert repo._ontology_drafts.get(project_id, draft['id'])['status'] == 'published'


def test_publish_preflight_revalidates_only_the_current_approved_subset(tmp_path):
    _, service, project_id, base = setup_service(tmp_path, publisher=lambda **_: None)
    draft = service.create(project_id, base['id'], 'manual', 'subset', 'author')
    created = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'create_term', 'target_iri': 'urn:test:New', 'kind': 'class'})
    labelled = service.command(project_id, draft['id'], created['revision'], {
        'action': 'add_annotation', 'target_iri': 'urn:test:New',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'New', 'language': 'en'})
    submitted = service.submit(project_id, draft['id'], labelled['revision'])
    create_op, label_op = submitted['operations']
    rejected = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': create_op['id'],
            'operation_fingerprint': create_op['fingerprint'],
            'action': 'reject', 'reason': 'do not create it'}], [], 'reviewer')
    reviewed = service.decide(
        project_id, draft['id'], rejected['revision'], base['id'],
        rejected['validation_fingerprint'], [{
            'operation_id': label_op['id'],
            'operation_fingerprint': label_op['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    assert reviewed['status'] == 'reviewed'

    with pytest.raises(ValidationFailed) as caught:
        service.publish_preflight(
            project_id, draft['id'], reviewed['revision'], base['id'],
            reviewed['validation_fingerprint'], [], 'subset', 'publisher')
    assert caught.value.code == 'validation_failed'


def test_server_computes_leaf_and_large_descendant_risk(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    leaf_draft = service.create(project_id, base['id'], 'manual', 'leaf', 'author')
    leaf = service.command(project_id, leaf_draft['id'], leaf_draft['revision'], {
        'action': 'create_term', 'target_iri': 'urn:test:Leaf', 'kind': 'class'})
    assert leaf['operations'][0]['risk'] == 'low'

    descendants = '\n'.join(
        f'<urn:test:Child{i}> a <http://www.w3.org/2002/07/owl#Class> ; '
        '<http://www.w3.org/2000/01/rdf-schema#subClassOf> '
        '<https://example.test/Root> .'
        for i in range(51))
    large = repo.save_ontology(project_id, BASE + descendants, {})
    large_draft = service.create(
        project_id, large['id'], 'manual', 'large impact', 'author')
    changed = service.command(project_id, large_draft['id'], large_draft['revision'], {
        'action': 'add_annotation', 'target_iri': 'https://example.test/Root',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Large', 'language': 'en'})
    operation = changed['operations'][0]
    assert operation['impact']['descendants'] == 52
    assert operation['risk'] == 'high'
    submitted = service.submit(project_id, large_draft['id'], changed['revision'])
    with pytest.raises(ValueError, match='reason'):
        service.decide(
            project_id, large_draft['id'], submitted['revision'], large['id'],
            submitted['validation_fingerprint'], [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve'}], [], 'reviewer')


def test_impact_includes_formal_endpoint_and_constraint_references(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    repo.put_record(project_id, {
        'id': 'root-entity', 'kind': 'entity',
        'type': 'https://example.test/Root', 'text': 'root'})
    repo.put_record(project_id, {
        'id': 'root-edge', 'kind': 'relation',
        'type': 'https://example.test/rel', 'text': 'edge',
        'subject_id': 'root-entity', 'object_id': 'root-entity'})
    draft = service.create(project_id, base['id'], 'manual', 'impact', 'author')

    changed = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_annotation', 'target_iri': 'https://example.test/Root',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Impacted', 'language': 'en'})

    impact = changed['operations'][0]['impact']
    assert impact['formal_records'] == 2
    assert impact['constraints'] >= 2
    assert changed['operations'][0]['risk'] == 'high'


def test_discovery_candidates_contribute_to_pending_risk(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    repo.put_record(project_id, {
        'id': 'source-doc', 'kind': 'document', 'text': 'source',
        'metadata': {'discovery_candidates': [{
            'id': f'candidate-{index}', 'status': 'pending',
            'target_type': 'https://example.test/Root',
        } for index in range(21)]}})
    draft = service.create(project_id, base['id'], 'manual', 'pending risk', 'author')

    changed = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_annotation', 'target_iri': 'https://example.test/Root',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Pending', 'language': 'en'})

    operation = changed['operations'][0]
    assert operation['impact']['pending'] == 21
    assert operation['risk'] == 'high'


def test_hierarchy_reads_are_paginated_and_overlay_uses_canonical_iris(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'browse', 'a')
    draft = service.command(project_id, draft['id'], 1, {
        'action': 'create_term', 'target_iri': 'https://example.test/Second',
        'kind': 'class'})
    draft = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_parent', 'target_iri': 'https://example.test/Second',
        'parent_iri': 'https://example.test/Root'})
    draft = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_parent', 'target_iri': 'https://example.test/Second',
        'parent_iri': 'https://example.test/Child'})

    roots = service.roots(project_id, ontology_id=base['id'], draft_id=draft['id'],
                          limit=1)
    assert roots['items'][0]['iri'] == 'https://example.test/Root'
    children = service.children(
        project_id, 'https://example.test/Root', ontology_id=base['id'],
        draft_id=draft['id'], limit=10)
    assert {item['iri'] for item in children['items']} == {
        'https://example.test/Child', 'https://example.test/Second'}
    assert all('child_count' in item and 'other_parent_count' in item
               and 'display_path' in item for item in children['items'])
    second_from_root = next(
        item for item in children['items']
        if item['iri'] == 'https://example.test/Second')
    second_from_child = service.children(
        project_id, 'https://example.test/Child', draft_id=draft['id'])['items'][0]
    assert second_from_root['canonical_iri'] == second_from_child['canonical_iri']
    assert second_from_root['is_reference'] is True
    assert second_from_root['other_parent_count'] == 1
    first_page = service.children(
        project_id, 'https://example.test/Root', draft_id=draft['id'], limit=1)
    second_page = service.children(
        project_id, 'https://example.test/Root', draft_id=draft['id'],
        cursor=first_page['next_cursor'], limit=1)
    assert first_page['next_cursor']
    assert first_page['items'][0]['iri'] != second_page['items'][0]['iri']
    neighborhood = service.neighborhood(
        project_id, 'https://example.test/Second', draft_id=draft['id'])
    assert neighborhood['term']['canonical_iri'] == 'https://example.test/Second'
    assert {item['iri'] for item in neighborhood['items']} == {
        'https://example.test/Child', 'https://example.test/Root'}
    assert service.search(project_id, 'second', draft_id=draft['id'])['items'][0][
        'iri'] == 'https://example.test/Second'
    assert service.matrix(project_id, ontology_id=base['id'])['items'][0]['iri'] == (
        'https://example.test/rel')


def test_turtle_diff_operations_are_rebuilt_with_source_evidence_and_impact(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(
        project_id, base['id'], 'turtle', 'Turtle edit', 'author',
        source_context={'evidence_refs': ['document-version:1']})
    edited = BASE + '''
<https://example.test/Child>
  <http://www.w3.org/2000/01/rdf-schema#comment> "edited"@en .
'''

    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'replace_turtle', 'edited_turtle': edited,
        'evidence_refs': ['document-version:1'],
        'risk': 'low', 'impact': {}})

    operation = preview['operations'][0]
    assert operation['validation']['source'] == 'turtle'
    assert operation['risk'] == 'medium'
    assert operation['evidence'] == ['document-version:1']
    assert set(operation['impact']) >= {
        'formal_records', 'descendants', 'constraints', 'pending'}


def test_validation_rescans_final_overlay_dependency_warnings(tmp_path):
    repo, service, project_id, _ = setup_service(tmp_path)
    active_old = BASE.replace(
        'ex:Legacy a owl:Class ; owl:deprecated true .',
        'ex:Legacy a owl:Class .')
    current = repo.save_ontology(project_id, active_old, {})
    draft = service.create(project_id, current['id'], 'manual', 'overlay', 'author')
    annotated = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'add_annotation', 'target_iri': 'https://example.test/Child',
        'predicate': 'https://example.test/note',
        'value': 'https://example.test/Legacy', 'value_type': 'iri'})
    retired = service.command(project_id, draft['id'], annotated['revision'], {
        'action': 'retire_term', 'target_iri': 'https://example.test/Legacy'})

    submitted = service.submit(project_id, draft['id'], retired['revision'])

    codes = {issue['code'] for issue in submitted['validation_report']['warnings']}
    assert 'active_custom_annotation_dependency' in codes
    assert submitted['validation_fingerprint']


def test_request_changes_never_counts_as_a_final_decision(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'final decisions', 'author')
    first = add_label(service, project_id, draft, 'first')
    second = service.command(project_id, draft['id'], first['revision'], {
        'action': 'add_annotation', 'target_iri': 'https://example.test/Root',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'second', 'language': 'en'})
    submitted = service.submit(project_id, draft['id'], second['revision'])
    first_op, second_op = submitted['operations']
    changes = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': first_op['id'],
            'operation_fingerprint': first_op['fingerprint'],
            'action': 'request_changes', 'reason': 'adjust it'}], [], 'reviewer')
    resubmitted = service.submit(project_id, draft['id'], changes['revision'])

    partial = service.decide(
        project_id, draft['id'], resubmitted['revision'], base['id'],
        resubmitted['validation_fingerprint'], [{
            'operation_id': second_op['id'],
            'operation_fingerprint': second_op['fingerprint'],
            'action': 'approve'}], [], 'reviewer')

    assert partial['status'] == 'submitted'


def test_publish_preflight_requires_final_current_decisions_for_every_operation(tmp_path):
    repo, service, project_id, base = setup_service(
        tmp_path, publisher=lambda **prepared: prepared)
    draft = service.create(project_id, base['id'], 'manual', 'preflight', 'author')
    submitted = service.submit(
        project_id, draft['id'], add_label(service, project_id, draft)['revision'])
    operation = submitted['operations'][0]
    repo._ontology_drafts.append_decisions(project_id, draft['id'], [{
        'operation_id': operation['id'],
        'operation_fingerprint': operation['fingerprint'],
        'action': 'request_changes', 'reason': 'not final', 'actor': 'reviewer'}])
    reviewed = repo._ontology_drafts.compare_and_set(
        project_id, draft['id'], submitted['revision'], {'status': 'reviewed'})

    with pytest.raises(ValidationChanged):
        service.publish_preflight(
            project_id, draft['id'], reviewed['revision'], base['id'],
            reviewed['validation_fingerprint'], [], 'invalid-history', 'publisher')


def test_withdraw_is_append_only_and_removes_operation_from_all_effective_views(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'withdraw', 'author')
    created = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'create_term', 'target_iri': 'urn:test:Temporary', 'kind': 'class'})
    operation = created['operations'][0]

    withdrawn = service.command(project_id, draft['id'], created['revision'], {
        'action': 'withdraw_operation', 'operation_id': operation['id']})

    assert withdrawn['operations'] == []
    assert 'urn:test:Temporary' not in withdrawn['turtle']
    history = repo._ontology_drafts.export(project_id)['operations']
    assert len(history) == 2
    assert history[1]['supersedes_operation_id'] == operation['id']
    assert history[1]['validation']['withdrawn'] is True
    with pytest.raises(ValidationFailed):
        service.submit(project_id, draft['id'], withdrawn['revision'])


def test_replacement_preserves_logical_order_before_dependent_operations(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'ordered', 'author')
    created = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'create_term', 'target_iri': 'urn:test:Ordered', 'kind': 'class'})
    create_op = created['operations'][0]
    labelled = service.command(project_id, draft['id'], created['revision'], {
        'action': 'add_annotation', 'target_iri': 'urn:test:Ordered',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Ordered', 'language': 'en'})

    adjusted = service.command(project_id, draft['id'], labelled['revision'], {
        'action': 'create_term', 'target_iri': 'urn:test:Ordered', 'kind': 'class',
        'supersedes_operation_id': create_op['id'], 'reason': 'same declaration'})

    assert [row['action'] for row in adjusted['operations']] == [
        'create_term', 'add_annotation']
    assert 'Ordered' in adjusted['turtle']


def test_restore_splits_activation_from_selected_definition_operations(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    retired_turtle = BASE.replace(
        'ex:Child a owl:Class ; rdfs:subClassOf ex:Root ; rdfs:label "Child" .',
        'ex:Child a owl:Class ; owl:deprecated true .')
    retired = repo.save_ontology(project_id, retired_turtle, {})
    draft = service.create(project_id, retired['id'], 'manual', 'restore split', 'author')

    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'restore_term', 'target_iri': 'https://example.test/Child',
        'source_ontology_id': base['id'],
        'selected_fields': ['annotations', 'parents']})

    assert [row['action'] for row in preview['operations']] == [
        'restore_term', 'add_annotation', 'add_parent']
    restore = preview['operations'][0]
    assert restore['after']['activation_only'] is True
    assert restore['after']['selected_fields'] == ['annotations', 'parents']
    assert restore['after']['template']['parents'] == ['https://example.test/Root']


def test_restore_definition_edges_can_be_rejected_independently(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    retired_turtle = BASE.replace(
        'ex:Child a owl:Class ; rdfs:subClassOf ex:Root ; rdfs:label "Child" .',
        'ex:Child a owl:Class ; owl:deprecated true .')
    retired = repo.save_ontology(project_id, retired_turtle, {})
    draft = service.create(project_id, retired['id'], 'manual', 'selective', 'author')
    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'restore_term', 'target_iri': 'https://example.test/Child',
        'source_ontology_id': base['id'],
        'selected_fields': ['annotations', 'parents']})
    current = service.submit(project_id, draft['id'], preview['revision'])

    for operation in current['operations']:
        approve = operation['action'] == 'restore_term'
        current = service.decide(
            project_id, draft['id'], current['revision'], retired['id'],
            current['validation_fingerprint'], [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve' if approve else 'reject',
                'reason': 'activate only' if approve else 'exclude definition edge',
            }], [], 'reviewer')
    assert current['status'] == 'reviewed'

    prepared = service.publish_preflight(
        project_id, draft['id'], current['revision'], retired['id'],
        current['validation_fingerprint'], [], 'selective', 'publisher')
    assert [row['action'] for row in prepared['operations']] == ['restore_term']
    result = Ontology(prepared['turtle'])
    child = URIRef('https://example.test/Child')
    assert result.is_active_term(child) is True
    assert list(result.graph.objects(child, RDFS.subClassOf)) == []
    assert list(result.graph.objects(child, RDFS.label)) == []


def test_blocking_operation_is_reviewable_but_cannot_be_approved(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'blocked', 'author')

    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'retire_term', 'target_iri': 'https://example.test/Root'})

    operation = preview['operations'][0]
    error_codes = {issue['code'] for issue in operation['validation']['errors']}
    assert {'active_child_dependency', 'active_domain_dependency'} <= error_codes
    submitted = service.submit(project_id, draft['id'], preview['revision'])
    assert submitted['status'] == 'submitted'
    assert submitted['validation_report']['conforms'] is False
    with pytest.raises(ValidationFailed):
        service.decide(
            project_id, draft['id'], submitted['revision'], base['id'],
            submitted['validation_fingerprint'], [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve', 'reason': 'unsafe'}], [], 'reviewer')
    rejected = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': 'reject', 'reason': 'blocked'}], [], 'reviewer')
    assert rejected['status'] == 'closed'


def test_retire_impact_keeps_dependency_details_not_only_counts(tmp_path):
    repo, service, project_id, base = setup_service(tmp_path)
    repo.put_record(project_id, {
        'id': 'root-record', 'kind': 'entity',
        'type': 'https://example.test/Root', 'text': 'root'})
    repo.put_record(project_id, {
        'id': 'source', 'kind': 'document', 'text': 'source',
        'metadata': {'review_candidates': [{
            'id': 'candidate-root', 'status': 'pending',
            'target_type': 'https://example.test/Root'}]}})
    draft = service.create(project_id, base['id'], 'manual', 'retire impact', 'author')

    preview = service.command(project_id, draft['id'], draft['revision'], {
        'action': 'retire_term', 'target_iri': 'https://example.test/Root'})

    impact = preview['operations'][0]['impact']
    assert impact['record_ids'] == ['root-record']
    assert 'candidate-root' in impact['pending_candidate_ids']
    assert impact['constraints_detail']
    assert impact['dependency_report']['retained_definition']['kind'] == 'class'


def test_command_holds_repository_write_transaction_through_check_and_compile(
        tmp_path, monkeypatch):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(project_id, base['id'], 'manual', 'transaction', 'author')
    original = service._compile_command
    observed = []

    def checked_compile(*args, **kwargs):
        observed.append(service.repository._db.in_transaction)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, '_compile_command', checked_compile)
    add_label(service, project_id, draft)
    assert observed == [True]


def test_hierarchy_paginates_nodes_before_building_expensive_items(
        tmp_path, monkeypatch):
    repo, service, project_id, _ = setup_service(tmp_path)
    many = BASE + '\n'.join(
        f'<urn:test:Root{index:03}> a <http://www.w3.org/2002/07/owl#Class> .'
        for index in range(50))
    ontology = repo.save_ontology(project_id, many, {})
    calls = []
    original = service._class_item

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(service, '_class_item', counted)
    page = service.roots(project_id, ontology_id=ontology['id'], limit=2)
    assert len(page['items']) == 2
    assert len(calls) == 2


def test_matrix_pages_nodes_before_projection_without_full_summary(
        tmp_path, monkeypatch):
    repo, service, project_id, _ = setup_service(tmp_path)
    many = BASE + '\n'.join(
        f'<urn:test:rel{index:02}> a '
        '<http://www.w3.org/2002/07/owl#ObjectProperty> ; '
        '<http://www.w3.org/2000/01/rdf-schema#domain> '
        '<https://example.test/Root> ; '
        '<http://www.w3.org/2000/01/rdf-schema#range> '
        '<https://example.test/Child> .'
        for index in range(25))
    ontology = repo.save_ontology(project_id, many, {})
    calls = []
    original = service._term_item

    def counted(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(service, '_term_item', counted)
    page = service.matrix(project_id, ontology_id=ontology['id'], limit=1)
    assert len(page['items']) == 1
    assert page['next_cursor']
    assert len(calls) == 1


def test_neighborhood_caps_relation_projection_without_full_summary(
        tmp_path, monkeypatch):
    repo, service, project_id, _ = setup_service(tmp_path)
    many = BASE + '\n'.join(
        f'<urn:test:rel{index:02}> a '
        '<http://www.w3.org/2002/07/owl#ObjectProperty> ; '
        '<http://www.w3.org/2000/01/rdf-schema#domain> '
        '<https://example.test/Root> ; '
        '<http://www.w3.org/2000/01/rdf-schema#range> '
        '<https://example.test/Child> .'
        for index in range(25))
    ontology = repo.save_ontology(project_id, many, {})

    def forbidden_summary(*args, **kwargs):
        raise AssertionError('neighborhood must not materialize the full summary')

    monkeypatch.setattr(Ontology, 'summary', forbidden_summary)
    result = service.neighborhood(
        project_id, 'https://example.test/Root',
        ontology_id=ontology['id'], limit=1)
    assert len(result['relations']) == 1
    assert result['relation_count'] == 26
    assert result['relations_truncated'] is True


def test_search_matches_every_rdfs_label_language(tmp_path):
    repo, service, project_id, _ = setup_service(tmp_path)
    multilingual = BASE + '''
<https://example.test/Child>
  <http://www.w3.org/2000/01/rdf-schema#label> "Bonjour"@fr,
                                                     "Guten Tag"@de .
'''
    ontology = repo.save_ontology(project_id, multilingual, {})
    assert service.search(
        project_id, 'bonjour', ontology_id=ontology['id'])['items'][0]['iri'] == (
            'https://example.test/Child')
    assert service.search(
        project_id, 'guten', ontology_id=ontology['id'])['items'][0]['iri'] == (
            'https://example.test/Child')


def test_actor_is_authoritative_and_state_boundaries_are_strict(tmp_path):
    _, service, project_id, base = setup_service(tmp_path)
    draft = service.create(
        project_id, base['id'], 'manual', 'states', 'author',
        source_context={'actor': 'spoofed'})
    assert draft['source_context']['actor'] == 'author'
    with pytest.raises(ValueError, match='rebase'):
        service.rebase(project_id, draft['id'], draft['revision'], base['id'])
    submitted = service.submit(
        project_id, draft['id'], add_label(service, project_id, draft)['revision'])
    with pytest.raises(ValueError, match='close|editing'):
        service.close(
            project_id, draft['id'], submitted['revision'], 'author', 'too late')
    operation = submitted['operations'][0]
    reviewed = service.decide(
        project_id, draft['id'], submitted['revision'], base['id'],
        submitted['validation_fingerprint'], [{
            'operation_id': operation['id'],
            'operation_fingerprint': operation['fingerprint'],
            'action': 'approve'}], [], 'reviewer')
    with pytest.raises(ValueError, match='submitted'):
        service.decide(
            project_id, draft['id'], reviewed['revision'], base['id'],
            reviewed['validation_fingerprint'], [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'reject', 'reason': 'late'}], [], 'reviewer')


def test_pre_governance_artifacts_are_not_lazily_migrated_or_publishable(tmp_path):
    """The new lifecycle deliberately ignores historical artifact-only drafts."""
    repo, service, project_id, _ = setup_service(tmp_path)
    repo.save_artifact('ontology_discovery_draft', {
        'id': 'legacy-only-draft', 'project_id': project_id,
        'name': '旧发现草案', 'status': 'draft', 'revision': 1,
        'created_at': '2025-01-01T00:00:00Z', 'candidate_ids': [],
        'candidate_snapshot': [], 'mappings': {}, 'turtle': BASE,
    })

    assert service.list(project_id) == []
    with pytest.raises(KeyError):
        service.get(project_id, 'legacy-only-draft')
