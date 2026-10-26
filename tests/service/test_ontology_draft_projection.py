"""Lossless browser review transport without changing draft governance."""

from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_service.api.ontology_drafts import install
from knowledge_service.services.ontology_drafts import OntologyDrafts
from test_ontology_draft_api import command, create_draft, workbench


def preview_fixture(operation_count=3):
    evidence = [
        f'document:rule-{index:03d}:version:immutable-snapshot-2026-10-26:'
        f'candidate:review-fact-{index:03d}:source-fragment'
        for index in range(120)]
    raw = {
        'id': 'draft-test', 'project_id': 'project-test', 'revision': 7,
        'base_ontology_id': 'ontology-test', 'source_kind': 'discovery',
        'status': 'pending', 'title': 'Review the extracted definitions',
        'source_context': {
            'documents': [{'document_id': 'document-test', 'version_id': 'frozen-4',
                           'candidate_ids': ['candidate-test']}],
            'intake_bindings': [{'discovery_run_id': 'run-test',
                                'selected_candidate_ids': ['candidate-test']}],
            'audit_identity': {'id': 'reviewer-test'},
        },
        'validation_fingerprint': 'validation-test',
        'validation_report': {'warnings': [{'code': 'unresolved_reference'}]},
    }
    return {
        **raw, 'draft': deepcopy(raw), 'turtle': 'ex:Root a owl:Class .\n' * 5000,
        'operations': [{
            'id': f'operation-{index}', 'fingerprint': f'fingerprint-{index}',
            'action': 'create_term', 'target_iri': f'urn:test:Term{index}',
            'evidence': list(evidence), 'validation': {'warnings': []},
        } for index in range(operation_count)],
        'decisions': [{'id': 'decision-test', 'operation_id': 'operation-0',
                       'operation_fingerprint': 'fingerprint-0', 'action': 'approve'}],
        'associated_knowledge': {'selected': 1, 'per_batch': [{'run_id': 'run-test'}]},
        'ontology': {'classes': [{'id': 'urn:test:Root'}], 'relations': []},
    }


def rehydrate(review):
    result = deepcopy(review)
    sets = result.pop('evidence_sets')
    for operation in result['operations']:
        set_id = operation.pop('evidence_set_id')
        count = operation.pop('evidence_count')
        operation['evidence'] = deepcopy(sets[set_id])
        assert count == len(operation['evidence'])
    return result


def expected_preview(full):
    return {key: value for key, value in full.items() if key not in {'draft', 'turtle'}}


def test_review_projection_reduces_188_operations_without_losing_review_data():
    full = preview_fixture(188)
    before = deepcopy(full)
    # A pure projection must work without any repository or store available.
    engine = object.__new__(OntologyDrafts)
    review = engine.review_projection(full)

    assert len(review['evidence_sets']) == 1
    assert all('evidence' not in operation for operation in review['operations'])
    assert rehydrate(review) == expected_preview(full)
    assert full == before
    full_bytes = len(json.dumps(full, ensure_ascii=False).encode('utf-8'))
    review_bytes = len(json.dumps(review, ensure_ascii=False).encode('utf-8'))
    assert full_bytes > 2_000_000
    assert review_bytes < full_bytes * 0.3
    assert review_bytes < 400_000


def test_review_projection_preserves_distinct_ordered_mixed_and_empty_evidence():
    full = preview_fixture(5)
    mixed = ['unknown:reference', {'document_id': 'doc-1', 'nested': [1, None]},
             'unknown:reference', {'unrecognized_kind': {'raw': True}}]
    full['operations'][0]['evidence'] = deepcopy(mixed)
    full['operations'][1]['evidence'] = deepcopy(mixed)
    full['operations'][2]['evidence'] = list(reversed(mixed))
    full['operations'][3]['evidence'] = []
    full['operations'][4]['evidence'] = []
    review = OntologyDrafts.review_projection(full)

    operations = review['operations']
    assert len(review['evidence_sets']) == 3
    assert operations[0]['evidence_set_id'] == operations[1]['evidence_set_id']
    assert operations[0]['evidence_set_id'] != operations[2]['evidence_set_id']
    assert operations[3]['evidence_set_id'] == operations[4]['evidence_set_id']
    assert review['evidence_sets'][operations[3]['evidence_set_id']] == []
    assert operations[3]['evidence_count'] == 0
    assert rehydrate(review) == expected_preview(full)


def test_response_projection_requires_explicit_receipt_envelope():
    engine = object.__new__(OntologyDrafts)
    full = preview_fixture()
    assert engine.project_response(full) is full
    # A full preview itself has a nested raw draft, but is not a receipt envelope.
    assert rehydrate(engine.project_response(full, view='review')) == expected_preview(full)
    for receipt in ({'added_count': 2, 'skipped_count': 1},
                    {'definition': {'decided': 2, 'operation_ids': ['operation-0']}},
                    {'batch': {'approved': 2, 'skipped_reasons': {'blocked': 1}}}):
        wrapped = {'draft': full, **receipt}
        before = deepcopy(wrapped)
        assert engine.project_response(wrapped, envelope=True) is wrapped
        compact = engine.project_response(wrapped, view='review', envelope=True)
        assert rehydrate(compact['draft']) == expected_preview(full)
        assert {key: value for key, value in compact.items() if key != 'draft'} == receipt
        assert wrapped == before


def test_review_view_routes_delegate_with_original_arguments_and_server_actors(monkeypatch):
    full = preview_fixture()
    engine = object.__new__(OntologyDrafts)
    calls = []
    identity = {'id': 'system:auth-disabled', 'username': 'auth-disabled',
                'display_name': '未启用认证'}
    actor = '未启用认证 (auth-disabled; user:system:auth-disabled)'
    revision = {'expected_revision': 7}
    guarded = {**revision, 'expected_ontology_id': 'ontology-test',
               'validation_fingerprint': 'validation-test',
               'acknowledged_warning_codes': ['warning-test']}
    cases = [
        ('', 'create', {'base_ontology_id': 'ontology-test', 'title': 'New draft',
                        'source_context': {'audit_identity': {'id': 'spoofed'}}},
         ('project-test', 'ontology-test', 'manual', 'New draft', actor),
         {'source_context': {'audit_identity': identity}, 'summary': ''}, False),
        ('/draft-test/commands', 'command', {**revision, 'command': {'action': 'test'}},
         ('project-test', 'draft-test', 7, {'action': 'test'}), {}, False),
        ('/draft-test/discover-parents', 'discover_parents', revision,
         ('project-test', 'draft-test', 7), {}, True),
        ('/draft-test/validate', 'validate', revision,
         ('project-test', 'draft-test', 7), {}, False),
        ('/draft-test/submit', 'submit', revision,
         ('project-test', 'draft-test', 7), {}, False),
        ('/draft-test/decisions', 'decide', {**guarded, 'decisions': [{'action': 'approve'}]},
         ('project-test', 'draft-test', 7, 'ontology-test', 'validation-test',
          [{'action': 'approve'}], ['warning-test'], actor), {}, False),
        ('/draft-test/decisions/by-definition', 'decide_by_definition',
         {**guarded, 'target_iri': 'urn:test:Root', 'action': 'reject', 'reason': 'reviewed'},
         ('project-test', 'draft-test', 7, 'ontology-test', 'validation-test',
          'urn:test:Root', 'reject', 'reviewed', ['warning-test'], actor), {}, True),
        ('/draft-test/decisions/batch-approve', 'batch_approve', guarded,
         ('project-test', 'draft-test', 7, 'ontology-test', 'validation-test',
          ['warning-test'], actor), {}, True),
        ('/draft-test/rebase', 'rebase', {**revision, 'expected_ontology_id': 'ontology-test'},
         ('project-test', 'draft-test', 7, 'ontology-test'), {}, False),
        ('/draft-test/close', 'close', {**revision, 'reason': 'superseded'},
         ('project-test', 'draft-test', 7, actor, 'superseded'), {}, False),
    ]

    def stub(name, response):
        def invoke(*args, **kwargs):
            calls.append((name, args, kwargs))
            return deepcopy(response)
        return invoke

    for _, name, _, _, _, envelope in cases:
        receipt = {'added_count': 2, 'skipped_count': 1} if name == 'discover_parents' else {
            'batch' if name == 'batch_approve' else 'definition': {'unchanged': True}}
        response = {'draft': full, **receipt} if envelope else full
        monkeypatch.setattr(engine, name, stub(name, response))
    monkeypatch.setattr(engine, 'get', stub('get', full))
    app = FastAPI()
    app.state.auth_disabled = True
    install(app, SimpleNamespace(ontology_drafts=lambda **_: engine))
    prefix = '/api/projects/project-test/ontology-drafts'
    with TestClient(app) as client:
        for suffix, name, body, args, kwargs, envelope in cases:
            normal = client.post(prefix + suffix, json=body)
            assert normal.status_code == (201 if name == 'create' else 200), normal.text
            assert calls[-1] == (name, args, kwargs)
            compact = client.post(prefix + suffix, params={'view': 'review'}, json=body)
            assert compact.status_code == normal.status_code, compact.text
            assert calls[-1] == (name, args, kwargs)
            normal_data, compact_data = normal.json(), compact.json()
            if envelope:
                assert rehydrate(compact_data.pop('draft')) == expected_preview(normal_data.pop('draft'))
                assert compact_data == normal_data
            else:
                assert rehydrate(compact_data) == expected_preview(normal_data)
            previous_calls = len(calls)
            rejected = client.post(prefix + suffix, params={'view': 'canvas'}, json=body)
            assert rejected.status_code == 422
            assert len(calls) == previous_calls
        for view in ('full', 'canvas', 'review'):
            response = client.get(prefix + '/draft-test', params={'view': view})
            assert response.status_code == 200, response.text
            assert calls[-1] == ('get', ('project-test', 'draft-test'), {'canvas': view == 'canvas'})
            if view == 'review':
                assert rehydrate(response.json()) == expected_preview(full)
            else:
                assert response.json() == full
        assert client.get(prefix + '/draft-test', params={'view': 'unknown'}).status_code == 422


def test_review_reads_are_read_only_and_keep_full_canvas_and_raw_create_compatibility(
        workbench, monkeypatch):
    client, project_id, ontology = workbench
    prefix = f'/api/projects/{project_id}/ontology-drafts'
    created = client.post(prefix, params={'view': 'review'}, json={
        'base_ontology_id': ontology['id'], 'title': 'Compact create',
        'source_context': {'note': 'immutable source snapshot ' * 500},
    })
    assert created.status_code == 201, created.text
    initial = created.json()
    assert initial['evidence_sets'] == {} and initial['operations'] == []
    assert 'ontology' not in initial
    draft = command(client, project_id, initial, {
        'action': 'create_term', 'target_iri': 'urn:test:Review', 'kind': 'class',
        'evidence_refs': ['unknown:reference'],
    })
    engine = client.app.state.ontology_drafts
    stored = deepcopy(engine.store.get(project_id, draft['id']))
    history = deepcopy(engine.store.history(project_id, draft['id']))

    def forbid_write(*args, **kwargs):
        raise AssertionError('read projection attempted a persistence write')

    monkeypatch.setattr(engine.repository, 'unit_of_work', forbid_write)
    monkeypatch.setattr(engine.store, 'compare_and_set', forbid_write)
    monkeypatch.setattr(engine.store, 'create', forbid_write)
    path = prefix + '/' + draft['id']
    full = client.get(path).json()
    explicit_full = client.get(path, params={'view': 'full'}).json()
    response = client.get(path, params={'view': 'review'})
    assert response.status_code == 200, response.text
    assert rehydrate(response.json()) == expected_preview(full)
    assert explicit_full == full
    assert full['draft'] == stored and 'turtle' in full
    assert full['operations'][0]['evidence'] == ['unknown:reference']
    canvas = client.get(path, params={'view': 'canvas'}).json()
    assert canvas == {
        **{key: value for key, value in full.items()
           if key not in {'source_context', 'draft', 'turtle', 'operations'}},
        'operations': [{key: value for key, value in operation.items() if key != 'evidence'}
                       for operation in full['operations']],
    }
    assert engine.store.get(project_id, draft['id']) == stored
    assert engine.store.history(project_id, draft['id']) == history


def test_review_mutations_preserve_revision_and_terminal_guards(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    path = f"/api/projects/{project_id}/ontology-drafts/{draft['id']}"
    changed = client.post(path + '/commands', params={'view': 'review'}, json={
        'expected_revision': draft['revision'], 'command': {
            'action': 'create_term', 'target_iri': 'urn:test:Review', 'kind': 'class'},
    })
    assert changed.status_code == 200, changed.text
    current = changed.json()
    assert rehydrate(current) == expected_preview(client.get(path).json())
    for suffix, extra in [
            ('commands', {'command': {'action': 'create_term'}}),
            ('discover-parents', {}), ('validate', {}), ('submit', {}),
            ('decisions', {'expected_ontology_id': ontology['id'],
                           'validation_fingerprint': 'stale', 'decisions': []}),
            ('decisions/by-definition', {'expected_ontology_id': ontology['id'],
                                         'validation_fingerprint': 'stale',
                                         'target_iri': 'urn:test:Review', 'action': 'approve'}),
            ('rebase', {'expected_ontology_id': ontology['id']}),
            ('close', {'reason': 'superseded'})]:
        body = {'expected_revision': draft['revision'], **extra}
        full_error = client.post(path + '/' + suffix, json=body)
        review_error = client.post(path + '/' + suffix, params={'view': 'review'}, json=body)
        assert full_error.status_code == review_error.status_code == 409
        assert full_error.json() == review_error.json()
        assert review_error.json()['code'] == 'revision_conflict'
    closed = client.post(path + '/close', params={'view': 'review'}, json={
        'expected_revision': current['revision'], 'reason': 'superseded'})
    assert closed.status_code == 200, closed.text
    assert closed.json()['status'] == 'rejected'
    full_after = client.get(path).json()
    # Close, like create, deliberately returns a raw record rather than a preview.
    assert rehydrate(closed.json()) == {**full_after['draft'], 'operations': []}
    for view in ('full', 'review'):
        immutable = client.post(path + '/commands', params={'view': view}, json={
            'expected_revision': full_after['revision'], 'command': {
                'action': 'create_term', 'target_iri': 'urn:test:Forbidden', 'kind': 'class'}})
        assert immutable.status_code == 422
        assert immutable.json()['code'] == 'draft_immutable'
