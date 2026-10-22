"""Real PostgreSQL/TestClient regression for the first manual ontology publication."""
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


def test_empty_project_manual_terms_review_publish_and_baseline_guards(tmp_path):
    with TestClient(create_app(tmp_path / 'empty-ontology-flow.sqlite', HashingEncoder())) as client:
        response = client.post('/api/projects', json={
            'name': 'empty manual ontology', 'use_default_ontology': False})
        assert response.status_code == 201, response.text
        base = '/api/projects/' + response.json()['id']
        missing = client.get(base + '/ontology')
        assert missing.status_code == 404
        assert missing.json()['code'] == 'ontology_not_published'

        created = client.post(base + '/ontology-drafts', json={
            'base_ontology_id': None, 'source_kind': 'manual', 'title': 'First manual ontology'})
        assert created.status_code == 201, created.text
        draft = created.json()
        assert draft['base_ontology_id'] is None
        path = base + '/ontology-drafts/' + draft['id']
        initial_revision = draft['revision']

        terms = [
            {'kind': 'class', 'uri': 'urn:test:ManualEntity', 'label': 'Manual entity'},
            {'kind': 'relation', 'uri': 'urn:test:ManualRelation', 'label': 'Manual relation',
             'domains': ['urn:test:ManualEntity'], 'ranges': ['urn:test:ManualEntity']},
            {'kind': 'attribute', 'uri': 'urn:test:ManualAttribute', 'label': 'Manual attribute',
             'domain': 'urn:test:ManualEntity', 'range': 'http://www.w3.org/2001/XMLSchema#string'},
        ]
        for index, term in enumerate(terms):
            iri = term['uri']
            commands = [
                {'action': 'create_term', 'target_iri': iri, 'kind': term['kind']},
                {'action': 'add_annotation', 'target_iri': iri,
                 'predicate': 'http://www.w3.org/2000/01/rdf-schema#label', 'value': term['label']},
            ]
            for value in term.get('domains', [term['domain']] if term.get('domain') else []):
                commands.append({'action': 'add_domain', 'target_iri': iri, 'value': value})
            for value in term.get('ranges', []):
                commands.append({'action': 'add_range', 'target_iri': iri, 'value': value})
            if term['kind'] == 'attribute':
                commands.append({'action': 'set_datatype', 'target_iri': iri, 'datatype': term['range']})
            saved = client.post(path + '/commands', json={
                'command': {'action': 'batch', 'commands': commands}, 'expected_revision': draft['revision']})
            assert saved.status_code == 200, saved.text
            draft = saved.json()
            assert draft['base_ontology_id'] is None
            if index == 0:
                stale = client.post(path + '/commands', json={
                    'command': {'action': 'create_term', 'kind': 'class', 'target_iri': 'urn:test:StaleWrite'},
                    'expected_revision': initial_revision})
                assert stale.status_code == 409, stale.text
                assert stale.json()['code'] == 'revision_conflict'

        stored = client.get(path)
        assert stored.status_code == 200, stored.text
        draft = stored.json()
        expected = {'classes': {'urn:test:ManualEntity'},
                    'relations': {'urn:test:ManualRelation'},
                    'attributes': {'urn:test:ManualAttribute'}}
        for collection, ids in expected.items():
            assert {row['id'] for row in draft['ontology'][collection]} == ids
        relation = draft['ontology']['relations'][0]
        assert relation['domain'] == relation['range'] == ['urn:test:ManualEntity']
        attribute = draft['ontology']['attributes'][0]
        assert attribute['domain'] == ['urn:test:ManualEntity']
        assert attribute['range'] == ['http://www.w3.org/2001/XMLSchema#string']
        assert client.get(base + '/ontology').status_code == 404

        submitted = client.post(path + '/submit', json={'expected_revision': draft['revision']})
        assert submitted.status_code == 200, submitted.text
        draft = submitted.json()
        operation_ids = [operation['id'] for operation in draft['operations']]
        assert operation_ids
        for operation_id in operation_ids:
            operation = next(item for item in draft['operations'] if item['id'] == operation_id)
            reviewed = client.post(path + '/decisions', json={
                'expected_revision': draft['revision'], 'expected_ontology_id': None,
                'validation_fingerprint': draft['validation_fingerprint'],
                'acknowledged_warning_codes': [item['code'] for item in draft['validation_report']['warnings']
                                               if item.get('code')],
                'decisions': [{'operation_id': operation_id,
                               'operation_fingerprint': operation['fingerprint'],
                               'action': 'approve', 'reason': 'Verified first manual ontology'}]})
            assert reviewed.status_code == 200, reviewed.text
            draft = reviewed.json()

        published = client.post(path + '/publish', json={
            'expected_revision': draft['revision'], 'expected_ontology_id': None,
            'validation_fingerprint': draft['validation_fingerprint'],
            'acknowledged_warning_codes': [item['code'] for item in draft['validation_report']['warnings']
                                           if item.get('code')],
            'idempotency_key': 'first-manual-ontology', 'note': 'Publish the first manual ontology'})
        assert published.status_code == 200, published.text
        current = client.get(base + '/ontology')
        assert current.status_code == 200, current.text
        publication = current.json()
        for collection, ids in expected.items():
            assert {row['id'] for row in publication['summary'][collection]} == ids

        before = client.get(base + '/ontology-drafts').json()['total']
        for empty_id in (None, ''):
            conflict = client.post(base + '/ontology-drafts', json={
                'base_ontology_id': empty_id, 'source_kind': 'manual', 'title': 'Must conflict'})
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()['code'] == 'stale_base'
        assert client.get(base + '/ontology-drafts').json()['total'] == before
        assert client.get(base + '/ontology').json()['id'] == publication['id']
