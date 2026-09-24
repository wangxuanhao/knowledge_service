from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


BASE = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Root a owl:Class ; rdfs:label "Root" .
ex:AltRoot a owl:Class ; rdfs:label "Alt root" .
ex:Child a owl:Class ; rdfs:label "Child" ;
  rdfs:subClassOf ex:Root, ex:AltRoot .
ex:rel a owl:ObjectProperty ; rdfs:domain ex:Root ; rdfs:range ex:Child .
'''


@pytest.fixture
def workbench(tmp_path):
    app = create_app(tmp_path / 'ontology-api.sqlite', encoder=HashingEncoder())
    repo = app.state.service.repository
    project = repo.create_project('ontology API')
    ontology = repo.bootstrap_ontology(project['id'], BASE, {})
    with TestClient(app) as client:
        yield client, project['id'], ontology


def create_draft(client, project_id, ontology, **overrides):
    body = {
        'base_ontology_id': ontology['id'],
        'source_kind': 'manual',
        'title': 'Workbench draft',
        'actor': 'architect@example.test',
    }
    body.update(overrides)
    response = client.post(
        f'/api/projects/{project_id}/ontology-drafts', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def command(client, project_id, draft, payload):
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/commands",
        json={'expected_revision': draft['revision'], 'command': payload})
    assert response.status_code == 200, response.text
    return response.json()


def test_create_list_get_command_close_and_required_revision(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)

    listing = client.get(
        f'/api/projects/{project_id}/ontology-drafts').json()
    assert listing['total'] == 1
    assert listing['items'][0]['id'] == draft['id']

    fetched = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}")
    assert fetched.status_code == 200
    assert fetched.json()['operations'] == []

    missing_revision = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/commands",
        json={'command': {'action': 'create_term'}})
    assert missing_revision.status_code == 422

    changed = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:New',
        'kind': 'class', 'reason': 'new concept'})
    assert changed['revision'] == 2
    assert changed['operations'][0]['target_iri'] == 'urn:test:New'

    disposable = create_draft(
        client, project_id, ontology, title='Disposable')
    closed = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{disposable['id']}/close",
        json={'expected_revision': 1, 'actor': 'owner', 'reason': 'obsolete'})
    assert closed.status_code == 200
    assert closed.json()['status'] == 'closed'


def test_revision_conflict_has_stable_exact_409_body(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    current = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:One',
        'kind': 'class'})

    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/validate",
        json={'expected_revision': 1})
    assert response.status_code == 409
    assert response.json() == {
        'detail': 'ontology draft revision changed',
        'code': 'revision_conflict',
        'details': {
            'draft_id': draft['id'],
            'expected_revision': 1,
            'current_revision': current['revision'],
        },
    }


def test_validation_submit_decision_and_idempotent_publish(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'Reviewed definition', 'language': 'en',
        'reason': 'improve documentation',
    })
    validated = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/validate",
        json={'expected_revision': draft['revision']}).json()
    submitted_response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/submit",
        json={'expected_revision': validated['revision']})
    assert submitted_response.status_code == 200, submitted_response.text
    submitted = submitted_response.json()
    operation = submitted['operations'][0]
    warnings = [
        item['code'] for item in submitted['validation_report']['warnings']
        if item.get('code')]
    decided_response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/decisions",
        json={
            'expected_revision': submitted['revision'],
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'actor': 'reviewer@example.test',
            'decisions': [{
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': 'approve', 'reason': 'verified',
            }],
        })
    assert decided_response.status_code == 200, decided_response.text
    reviewed = decided_response.json()
    assert reviewed['status'] == 'reviewed'

    payload = {
        'expected_revision': reviewed['revision'],
        'expected_ontology_id': ontology['id'],
        'validation_fingerprint': reviewed['validation_fingerprint'],
        'acknowledged_warning_codes': warnings,
        'idempotency_key': 'publish-api-test-1',
        'actor': 'publisher@example.test',
    }
    first = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/publish",
        json=payload)
    assert first.status_code == 200, first.text
    replay = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/publish",
        json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()['id'] == first.json()['id']


def test_validation_failure_and_batch_policy_are_stable_422(workbench):
    client, project_id, ontology = workbench
    empty = create_draft(client, project_id, ontology)
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{empty['id']}/submit",
        json={'expected_revision': 1})
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_failed'
    assert response.json()['details'] == {}

    draft = create_draft(client, project_id, ontology, title='Batch policy')
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Root',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'root comment'})
    draft = command(client, project_id, draft, {
        'action': 'add_annotation',
        'target_iri': 'https://example.test/ns#Child',
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
        'value': 'child comment'})
    submitted = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/submit",
        json={'expected_revision': draft['revision']}).json()
    warnings = [
        item['code'] for item in submitted['validation_report']['warnings']
        if item.get('code')]
    decisions = [{
        'operation_id': operation['id'],
        'operation_fingerprint': operation['fingerprint'],
        'action': action, 'reason': 'reviewed',
    } for operation, action in zip(
        submitted['operations'], ('approve', 'reject'), strict=True)]
    rejected_batch = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/decisions",
        json={
            'expected_revision': submitted['revision'],
            'expected_ontology_id': ontology['id'],
            'validation_fingerprint': submitted['validation_fingerprint'],
            'acknowledged_warning_codes': warnings,
            'actor': 'reviewer', 'decisions': decisions,
        })
    assert rejected_batch.status_code == 422
    assert rejected_batch.json()['code'] == 'batch_not_allowed'


def test_hierarchy_pagination_dag_references_and_encoded_http_iri(workbench):
    client, project_id, ontology = workbench
    roots = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/roots',
        params={'ontology_id': ontology['id'], 'limit': 1})
    assert roots.status_code == 200
    assert roots.json()['next_cursor']
    second = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/roots',
        params={
            'ontology_id': ontology['id'], 'limit': 1,
            'cursor': roots.json()['next_cursor'],
        })
    assert second.status_code == 200
    assert second.json()['items'][0]['canonical_iri'] != (
        roots.json()['items'][0]['canonical_iri'])

    root_iri = 'https://example.test/ns#Root'
    children = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/children',
        params={'ontology_id': ontology['id'], 'iri': root_iri})
    assert children.status_code == 200, children.text
    child = children.json()['items'][0]
    assert child['canonical_iri'] == 'https://example.test/ns#Child'
    assert child['is_reference'] is True
    assert child['other_parent_count'] == 1

    neighborhood = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/neighborhood',
        params={'ontology_id': ontology['id'], 'iri': root_iri})
    assert neighborhood.status_code == 200
    assert neighborhood.json()['term']['iri'] == root_iri


def test_hierarchy_draft_overlay_search_and_matrix(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    draft = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:Overlay',
        'kind': 'class'})
    draft = command(client, project_id, draft, {
        'action': 'add_parent', 'target_iri': 'urn:test:Overlay',
        'parent_iri': 'https://example.test/ns#Root'})

    children = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/children',
        params={
            'ontology_id': ontology['id'], 'draft_id': draft['id'],
            'iri': 'https://example.test/ns#Root',
        })
    assert children.status_code == 200, children.text
    assert 'urn:test:Overlay' in {
        item['canonical_iri'] for item in children.json()['items']}

    search = client.get(
        f'/api/projects/{project_id}/ontology-hierarchy/search',
        params={
            'ontology_id': ontology['id'], 'draft_id': draft['id'],
            'q': 'Overlay',
        })
    assert search.status_code == 200
    assert search.json()['items'][0]['canonical_iri'] == 'urn:test:Overlay'

    matrix = client.get(
        f'/api/projects/{project_id}/ontology-matrix',
        params={'ontology_id': ontology['id'], 'limit': 1})
    assert matrix.status_code == 200
    assert matrix.json()['items'][0]['kind'] == 'relation'


def test_rebase_requires_ontology_boundary_field(workbench):
    client, project_id, ontology = workbench
    draft = create_draft(client, project_id, ontology)
    response = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/rebase",
        json={'expected_revision': 1})
    assert response.status_code == 422

    changed = command(client, project_id, draft, {
        'action': 'create_term', 'target_iri': 'urn:test:Rebase',
        'kind': 'class'})
    latest = client.app.state.service.repository.bootstrap_ontology(
        project_id,
        BASE + '\n<urn:test:Latest> a <http://www.w3.org/2002/07/owl#Class> .',
        {})
    stale = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/validate",
        json={'expected_revision': changed['revision']})
    assert stale.status_code == 409
    assert stale.json()['code'] == 'stale_base'
    current = client.get(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}").json()
    rebased = client.post(
        f"/api/projects/{project_id}/ontology-drafts/{draft['id']}/rebase",
        json={
            'expected_revision': current['revision'],
            'expected_ontology_id': latest['id'],
        })
    assert rebased.status_code == 200, rebased.text
    assert rebased.json()['base_ontology_id'] == latest['id']
