"""Definition decisions compile their operation set on the server atomically."""

from types import SimpleNamespace

from test_ontology_draft_api import (
    BASE, command, create_draft, large_review_draft, submit, workbench,
)


CHILD = 'https://example.test/ns#Child'
COMMENT = 'http://www.w3.org/2000/01/rdf-schema#comment'


def annotation(target=CHILD, value='reviewed'):
    return {'action': 'add_annotation', 'target_iri': target,
            'predicate': COMMENT, 'value': value, 'language': 'en'}


def definition_draft(workbench, *commands):
    client, project_id, ontology = workbench
    client.app.state.ontology_drafts.quality_engine = SimpleNamespace(
        check=lambda payload: {'engine': 'semantica', 'passed': True, 'issues': []})
    draft = create_draft(client, project_id, ontology)
    for payload in commands or (annotation(value='first'), annotation(value='second')):
        draft = command(client, project_id, draft, payload)
    return submit(client, project_id, draft)


def decide_definition(workbench, draft, target=CHILD, action='approve', **overrides):
    client, project_id, ontology = workbench
    body = {
        'expected_revision': draft['revision'],
        'expected_ontology_id': ontology['id'],
        'validation_fingerprint': draft['validation_fingerprint'],
        'acknowledged_warning_codes': [],
        'target_iri': target, 'action': action,
    }
    body.update(overrides)
    return client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/decisions/by-definition",
        json=body)


def current_draft(workbench, draft):
    client, project_id, _ = workbench
    return client.get(f"/api/projects/{project_id}/ontology-drafts/{draft['id']}").json()


def assert_unchanged(workbench, submitted):
    after = current_draft(workbench, submitted)
    assert after['revision'] == submitted['revision']
    assert after['decisions'] == submitted['decisions']


def test_definition_decision_atomically_approves_more_than_100_operations(workbench):
    submitted = large_review_draft(workbench, 188)

    response = decide_definition(workbench, submitted)

    assert response.status_code == 200, response.text
    result = response.json()
    operation_ids = [row['id'] for row in submitted['operations']]
    assert result['definition'] == {
        'target_iri': CHILD, 'action': 'approve', 'decided': 188,
        'already_decided': False, 'operation_ids': operation_ids,
    }
    assert result['draft']['revision'] == submitted['revision'] + 1
    assert len(result['draft']['decisions']) == 188
    assert {row['operation_id'] for row in result['draft']['decisions']} == set(operation_ids)
    assert all(row['action'] == 'approve' for row in result['draft']['decisions'])
    assert result['draft']['validation_fingerprint'] == submitted['validation_fingerprint']


def test_definition_selection_uses_exact_target_and_ignores_references(workbench):
    other = 'urn:other:Child'
    submitted = definition_draft(workbench, annotation(value='first'),
                                 annotation(value='second'),
                                 {'action': 'create_term', 'target_iri': other,
                                  'kind': 'class'},
                                 {'action': 'add_parent', 'target_iri': other,
                                  'parent_iri': CHILD})

    response = decide_definition(workbench, submitted)

    assert response.status_code == 200, response.text
    result = response.json()
    selected = {row['id'] for row in submitted['operations'] if row['target_iri'] == CHILD}
    assert result['definition']['decided'] == 2
    assert set(result['definition']['operation_ids']) == selected
    assert {row['operation_id'] for row in result['draft']['decisions']} == selected


def test_definition_decision_keeps_existing_current_decisions(workbench):
    client, project_id, ontology = workbench
    submitted = definition_draft(workbench)
    operation = submitted['operations'][0]
    first = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{submitted['id']}/decisions",
        json={'expected_revision': submitted['revision'],
              'expected_ontology_id': ontology['id'],
              'validation_fingerprint': submitted['validation_fingerprint'],
              'decisions': [{'operation_id': operation['id'],
                             'operation_fingerprint': operation['fingerprint'],
                             'action': 'reject', 'reason': 'keep this rejection'}]})
    assert first.status_code == 200, first.text
    previous = first.json()

    response = decide_definition(workbench, previous)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result['definition']['decided'] == 1
    assert len(result['draft']['decisions']) == 2
    retained = next(row for row in result['draft']['decisions']
                    if row['operation_id'] == operation['id'])
    assert retained['action'] == 'reject'
    assert retained['reason'] == 'keep this rejection'


def test_repeated_definition_approval_is_guarded_noop_at_current_revision(workbench):
    submitted = definition_draft(workbench)
    first = decide_definition(workbench, submitted)
    assert first.status_code == 200, first.text
    reviewed = first.json()['draft']

    response = decide_definition(workbench, reviewed)

    assert response.status_code == 200, response.text
    assert response.json()['definition'] == {
        'target_iri': CHILD, 'action': 'approve', 'decided': 0,
        'already_decided': True, 'operation_ids': [],
    }
    assert response.json()['draft']['revision'] == reviewed['revision']
    assert response.json()['draft']['decisions'] == reviewed['decisions']
    stale = decide_definition(workbench, reviewed, expected_revision=submitted['revision'])
    assert stale.status_code == 409
    assert stale.json()['code'] == 'revision_conflict'
    changed = decide_definition(workbench, reviewed, validation_fingerprint='outdated')
    assert changed.status_code == 409
    assert changed.json()['code'] == 'validation_changed'


def test_definition_rejection_requires_reason_for_the_atomic_group(workbench):
    submitted = definition_draft(workbench)
    missing = decide_definition(workbench, submitted, action='reject')
    assert missing.status_code == 422, missing.text
    assert_unchanged(workbench, submitted)

    response = decide_definition(workbench, submitted, action='reject', reason='not needed')

    assert response.status_code == 200, response.text
    result = response.json()
    assert result['definition']['decided'] == 2
    assert result['draft']['revision'] == submitted['revision'] + 1
    assert result['draft']['status'] == 'rejected'
    assert all(row['action'] == 'reject' and row['reason'] == 'not needed'
               for row in result['draft']['decisions'])


def test_definition_decisions_preserve_submission_and_final_status_guards(workbench):
    client, project_id, ontology = workbench
    unsubmitted = command(client, project_id,
                          create_draft(client, project_id, ontology), annotation())
    response = decide_definition(workbench, unsubmitted,
                                 validation_fingerprint='not-submitted')
    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'draft_immutable'
    assert_unchanged(workbench, unsubmitted)

    submitted = submit(client, project_id, unsubmitted)
    rejected = decide_definition(workbench, submitted, action='reject', reason='not needed')
    assert rejected.status_code == 200, rejected.text
    final = rejected.json()['draft']
    repeat = decide_definition(workbench, final, action='reject', reason='again')
    assert repeat.status_code == 422, repeat.text
    assert repeat.json()['code'] == 'draft_immutable'
    assert_unchanged(workbench, final)


def test_high_risk_definition_approval_still_requires_reason(workbench):
    client, project_id, _ = workbench
    ontology = client.app.state.service.repository.bootstrap_ontology(
        project_id, BASE + '\n<urn:test:Legacy> a owl:Class .', {})
    workbench = client, project_id, ontology
    submitted = definition_draft(workbench, annotation('urn:test:Legacy'),
                                 {'action': 'retire_term', 'target_iri': 'urn:test:Legacy'})
    assert any(row['risk'] == 'high' for row in submitted['operations'])
    missing = decide_definition(workbench, submitted, target='urn:test:Legacy')
    assert missing.status_code == 422, missing.text
    assert_unchanged(workbench, submitted)

    response = decide_definition(workbench, submitted, target='urn:test:Legacy',
                                 reason='reviewed retirement and annotations')

    assert response.status_code == 200, response.text
    assert response.json()['definition']['decided'] == 2
    assert all(row['reason'] == 'reviewed retirement and annotations'
               for row in response.json()['draft']['decisions'])


def test_warning_definition_requires_acknowledgements_and_reason(workbench):
    client, project_id, _ = workbench
    ontology = client.app.state.service.repository.bootstrap_ontology(
        project_id, BASE + '\nex:Legacy a owl:Class ; owl:deprecated true .', {})
    workbench = client, project_id, ontology
    submitted = definition_draft(workbench, {
        'action': 'add_annotation', 'target_iri': CHILD,
        'predicate': 'https://example.test/note',
        'value': 'https://example.test/ns#Legacy', 'value_type': 'iri'},
        annotation(value='second'))
    codes = [row['code'] for row in submitted['validation_report']['warnings']]
    assert codes
    missing_ack = decide_definition(workbench, submitted, reason='checked')
    assert missing_ack.status_code == 422, missing_ack.text
    assert missing_ack.json()['code'] == 'validation_failed'
    missing_reason = decide_definition(workbench, submitted, acknowledged_warning_codes=codes)
    assert missing_reason.status_code == 422, missing_reason.text
    assert_unchanged(workbench, submitted)

    response = decide_definition(workbench, submitted, reason='checked',
                                 acknowledged_warning_codes=codes)

    assert response.status_code == 200, response.text
    assert response.json()['definition']['decided'] == 2


def test_blocked_definition_cannot_approve_any_operations_but_can_reject(workbench):
    target = 'https://example.test/ns#Root'
    submitted = definition_draft(workbench, annotation(target),
                                 {'action': 'retire_term', 'target_iri': target})
    assert submitted['validation_report']['conforms'] is False

    response = decide_definition(workbench, submitted, target=target, reason='checked')

    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'validation_failed'
    assert_unchanged(workbench, submitted)
    rejected = decide_definition(workbench, submitted, target=target,
                                 action='reject', reason='blocked dependency')
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()['definition']['decided'] == 2


def test_definition_decision_preserves_stale_guards(workbench):
    submitted = definition_draft(workbench)
    for override, code in [
            ({'expected_revision': 1}, 'revision_conflict'),
            ({'expected_ontology_id': 'wrong-baseline'}, 'stale_base'),
            ({'validation_fingerprint': 'wrong-fingerprint'}, 'validation_changed')]:
        response = decide_definition(workbench, submitted, **override)
        assert response.status_code == 409, response.text
        assert response.json()['code'] == code
        assert_unchanged(workbench, submitted)


def test_definition_decision_preserves_real_baseline_change_guard(workbench):
    client, project_id, _ = workbench
    submitted = definition_draft(workbench)
    client.app.state.service.repository.bootstrap_ontology(
        project_id, BASE + '\n<urn:test:Latest> a owl:Class .', {})

    response = decide_definition(workbench, submitted)

    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'stale_base'
    assert_unchanged(workbench, submitted)


def test_definition_decision_rolls_back_the_entire_group_on_revision_failure(
        workbench, monkeypatch):
    client, _, _ = workbench
    submitted = large_review_draft(workbench, 188)

    def fail_after_append(*args, **kwargs):
        raise RuntimeError('definition revision write failed')

    monkeypatch.setattr(client.app.state.ontology_drafts, '_cas', fail_after_append)

    response = decide_definition(workbench, submitted)

    assert response.status_code == 503, response.text
    assert response.json()['detail'] == 'definition revision write failed'
    assert_unchanged(workbench, submitted)


def test_definition_decision_rejects_unknown_exact_target(workbench):
    submitted = definition_draft(workbench)
    response = decide_definition(workbench, submitted, target='urn:other:Child')
    assert response.status_code == 422, response.text
    assert_unchanged(workbench, submitted)


def test_definition_endpoint_does_not_accept_operation_lists_or_bypass_flags(workbench):
    submitted = definition_draft(workbench)
    for override in [{'decisions': []}, {'operation_ids': []},
                     {'trusted_definition': True}, {'action': 'request_changes'}]:
        response = decide_definition(workbench, submitted, **override)
        assert response.status_code == 422, response.text
        assert_unchanged(workbench, submitted)


def test_ordinary_decisions_keep_the_100_item_limit(workbench):
    client, project_id, ontology = workbench
    submitted = large_review_draft(workbench, 101)
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{submitted['id']}/decisions",
        json={'expected_revision': submitted['revision'],
              'expected_ontology_id': ontology['id'],
              'validation_fingerprint': submitted['validation_fingerprint'],
              'decisions': [{'operation_id': row['id'],
                             'operation_fingerprint': row['fingerprint'],
                             'action': 'approve'} for row in submitted['operations']]})
    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'batch_not_allowed'
    assert_unchanged(workbench, submitted)


def test_ordinary_decisions_keep_strict_multi_operation_risk_policy(workbench):
    client, project_id, ontology = workbench
    target = 'urn:test:Leaf'
    submitted = definition_draft(workbench,
                                 {'action': 'create_term', 'target_iri': target,
                                  'kind': 'class'},
                                 {'action': 'add_parent', 'target_iri': target,
                                  'parent_iri': CHILD})
    assert any(row['risk'] == 'medium' for row in submitted['operations'])
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{submitted['id']}/decisions",
        json={'expected_revision': submitted['revision'],
              'expected_ontology_id': ontology['id'],
              'validation_fingerprint': submitted['validation_fingerprint'],
              'decisions': [{'operation_id': row['id'],
                             'operation_fingerprint': row['fingerprint'],
                             'action': 'approve'} for row in submitted['operations']]})
    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'batch_not_allowed'
    assert_unchanged(workbench, submitted)
    grouped = decide_definition(workbench, submitted, target=target)
    assert grouped.status_code == 200, grouped.text
    assert grouped.json()['definition']['decided'] == 2
