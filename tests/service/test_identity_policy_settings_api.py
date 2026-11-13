"""PostgreSQL-backed HTTP regressions for governed class identity settings."""
from fastapi.testclient import TestClient
import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import XSD

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.ontology import Ontology


CLASS = 'https://example.test/Organization'
IDENTITY = 'https://knowledge.local/schema/'
NOTE = 'https://example.test/note'
BASE = '''
@prefix ex: <https://example.test/> .
@prefix kg: <https://knowledge.local/schema/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Organization a owl:Class ; rdfs:label "机构"@zh ;
    kg:identityPolicy "scoped"^^xsd:string ;
    kg:identityScope "旧登记目录"@zh ;
    kg:identityKey "old-code"^^xsd:string ;
    ex:note "Preserve this annotation"@en .
'''


@pytest.fixture
def identity_workbench(tmp_path):
    # tests/conftest.py maps this repository target to an isolated PostgreSQL slot.
    app = create_app(tmp_path / 'identity-policy-api.sqlite', encoder=HashingEncoder())
    repository = app.state.service.repository
    project = repository.create_project('Identity policy settings API')
    original = repository.bootstrap_ontology(project['id'], BASE, Ontology(BASE).summary())
    with TestClient(app) as client:
        project_path = f"/api/projects/{project['id']}"
        response = client.post(project_path + '/ontology-drafts', json={
            'base_ontology_id': original['id'], 'source_kind': 'manual',
            'title': 'Change organization identity settings',
        })
        assert response.status_code == 201, response.text
        yield client, project_path, original, response.json()


def _annotation(action, name, value, **qualifiers):
    return {'action': action, 'target_iri': CLASS,
            'predicate': IDENTITY + name, 'value': value, **qualifiers}


def _review_request(draft, original):
    return {
        'expected_revision': draft['revision'],
        'expected_ontology_id': original['id'],
        'validation_fingerprint': draft['validation_fingerprint'],
        'acknowledged_warning_codes': [item['code'] for item in
            draft['validation_report']['warnings'] if item.get('code')],
    }


def test_identity_policy_requires_manual_review_and_publishes_a_new_immutable_release(identity_workbench):
    client, project_path, original, draft = identity_workbench
    original_response = client.get(project_path + '/ontology')
    assert original_response.status_code == 200, original_response.text
    original_view = original_response.json()
    path = project_path + f"/ontology-drafts/{draft['id']}"
    commands = [
        _annotation('remove_annotation', 'identityPolicy', 'scoped', datatype=str(XSD.string)),
        _annotation('remove_annotation', 'identityScope', '旧登记目录', language='zh'),
        _annotation('remove_annotation', 'identityKey', 'old-code', datatype=str(XSD.string)),
        _annotation('add_annotation', 'identityPolicy', 'reference'),
        _annotation('add_annotation', 'identityScope', 'organization-register', datatype=str(XSD.string)),
        _annotation('add_annotation', 'identityKey', 'registered-code', datatype=str(XSD.string)),
    ]
    changed_response = client.post(path + '/commands', json={
        'expected_revision': draft['revision'], 'command': {'action': 'batch', 'commands': commands},
    })
    assert changed_response.status_code == 200, changed_response.text
    changed = changed_response.json()
    assert changed['revision'] == draft['revision'] + 1
    assert len(changed['operations']) == len(commands)
    assert {operation['risk'] for operation in changed['operations']} == {'high'}

    validated_response = client.post(path + '/validate', json={'expected_revision': changed['revision']})
    assert validated_response.status_code == 200, validated_response.text
    validated = validated_response.json()
    assert validated['validation_report']['conforms'] is True, validated['validation_report']
    submitted_response = client.post(path + '/submit', json={'expected_revision': validated['revision']})
    assert submitted_response.status_code == 200, submitted_response.text
    submitted = submitted_response.json()
    review_body = _review_request(submitted, original)
    operation_ids = {operation['id'] for operation in submitted['operations']}

    plan_response = client.get(path + '/review-plan')
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert {item['operation_id'] for item in plan['items']} == operation_ids
    assert all(item['risk'] == 'high' and item['classification'] == 'manual'
               and item['requires_reason'] and not item['auto_approvable']
               and not item['batch_eligible'] for item in plan['items']), plan
    automatic = client.post(path + '/decisions/batch-approve', json=review_body)
    assert automatic.status_code == 200, automatic.text
    automatic_result = automatic.json()
    assert automatic_result['batch']['approved'] == 0
    assert set(automatic_result['batch']['skipped_operation_ids']) == operation_ids
    assert automatic_result['draft']['decisions'] == []
    assert automatic_result['draft']['revision'] == submitted['revision']

    # A caller-supplied approval list must not bypass the server's high-risk floor.
    attempted_batch = client.post(path + '/decisions', json={**review_body, 'decisions': [
        {'operation_id': operation['id'], 'operation_fingerprint': operation['fingerprint'],
         'action': 'approve', 'reason': 'Caller attempts a batch approval'}
        for operation in submitted['operations']
    ]})
    assert attempted_batch.status_code == 422, attempted_batch.text
    assert attempted_batch.json()['code'] == 'batch_not_allowed'
    after_attempt = client.get(path)
    assert after_attempt.status_code == 200, after_attempt.text
    assert after_attempt.json()['revision'] == submitted['revision']
    assert after_attempt.json()['decisions'] == []

    premature = client.post(path + '/publish', json={**review_body,
        'idempotency_key': 'identity-policy-before-review', 'note': 'Attempt before manual review'})
    assert premature.status_code == 409, premature.text
    assert premature.json()['code'] == 'validation_changed'
    assert set(premature.json()['details']['operation_ids']) == operation_ids
    assert client.get(project_path + '/ontology').json()['id'] == original['id']

    # This is the workbench's explicit human decision about one exact definition.
    manual = client.post(path + '/decisions/by-definition', json={**review_body,
        'target_iri': CLASS, 'action': 'approve',
        'reason': 'Verified long-lived organization identity and registered code against the class definition',
    })
    assert manual.status_code == 200, manual.text
    reviewed = manual.json()['draft']
    assert set(manual.json()['definition']['operation_ids']) == operation_ids
    assert {decision['operation_id'] for decision in reviewed['decisions']
            if decision['action'] == 'approve'} == operation_ids
    published_response = client.post(path + '/publish', json={**_review_request(reviewed, original),
        'idempotency_key': 'identity-policy-manually-reviewed',
        'note': 'Enable cross-document organization identity after manual review',
    })
    assert published_response.status_code == 200, published_response.text
    published = published_response.json()
    current_response = client.get(project_path + '/ontology')
    assert current_response.status_code == 200, current_response.text
    current = current_response.json()
    assert current['id'] == published['id'] != original['id']
    assert current['metadata']['version'] == original['metadata']['version'] + 1
    assert current['metadata']['version_reused'] is False
    cls = next(item for item in current['summary']['classes'] if item['id'] == CLASS)
    assert cls['identity_policy'] == {
        'mode': 'reference', 'declared': True, 'scope': 'organization-register',
        'keys': ['registered-code'],
    }
    assert {item['predicate']: item for item in cls['identity_annotations']} == {
        IDENTITY + 'identityPolicy': {'predicate': IDENTITY + 'identityPolicy', 'value': 'reference'},
        IDENTITY + 'identityScope': {'predicate': IDENTITY + 'identityScope',
            'value': 'organization-register', 'datatype': str(XSD.string)},
        IDENTITY + 'identityKey': {'predicate': IDENTITY + 'identityKey',
            'value': 'registered-code', 'datatype': str(XSD.string)},
    }
    graph = Graph().parse(data=current['turtle'], format='turtle')
    assert (URIRef(CLASS), URIRef(IDENTITY + 'identityKey'),
            Literal('registered-code', datatype=XSD.string)) in graph
    assert (URIRef(CLASS), URIRef(NOTE), Literal('Preserve this annotation', lang='en')) in graph
    archived_response = client.get(project_path + '/ontology', params={'ontology_id': original['id']})
    assert archived_response.status_code == 200, archived_response.text
    assert archived_response.json() == original_view


def test_invalid_or_ambiguous_identity_policy_batches_leave_no_partial_operations_or_revision(identity_workbench):
    client, project_path, original, draft = identity_workbench
    path = project_path + f"/ontology-drafts/{draft['id']}"
    existing = client.post(path + '/commands', json={
        'expected_revision': draft['revision'], 'command': {
            'action': 'add_annotation', 'target_iri': CLASS,
            'predicate': NOTE, 'value': 'Existing unrelated draft edit', 'language': 'en',
        },
    })
    assert existing.status_code == 200, existing.text
    before_response = client.get(path)
    assert before_response.status_code == 200, before_response.text
    before = before_response.json()
    invalid_cases = [
        [
            _annotation('remove_annotation', 'identityPolicy', 'scoped', datatype=str(XSD.string)),
            _annotation('add_annotation', 'identityPolicy', 'automatic'),
        ],
        # Both modes are individually allowed, but this leaves two values on one class.
        [_annotation('add_annotation', 'identityPolicy', 'reference')],
    ]
    for index, invalid_commands in enumerate(invalid_cases):
        marker = {'action': 'add_annotation', 'target_iri': CLASS,
                  'predicate': NOTE, 'value': f'Must roll back with invalid policy {index}'}
        rejected = client.post(path + '/commands', json={
            'expected_revision': before['revision'],
            'command': {'action': 'batch', 'commands': [marker, *invalid_commands]},
        })
        assert rejected.status_code == 422, rejected.text
        after_response = client.get(path)
        assert after_response.status_code == 200, after_response.text
        after = after_response.json()
        for field in ('revision', 'operations', 'decisions', 'turtle', 'ontology'):
            assert after[field] == before[field], (index, field)
        assert len(after['operations']) == 1
        assert client.get(project_path + '/ontology').json()['id'] == original['id']
