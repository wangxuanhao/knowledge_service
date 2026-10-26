"""Selected intake is read-only until confirmation and composes one draft."""
from ontology_seed import seed_ontology
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.integrations.semantica_ontology import QualityEngineUnavailable


class _DeterministicQualityEngine:
    """Isolate the optional runtime while keeping the intake's real gates."""

    def __init__(self, outcome='passed'):
        self.outcome = outcome
        self.payloads = []

    def check(self, ontology):
        self.payloads.append(deepcopy(ontology))
        if self.outcome == 'unavailable':
            raise QualityEngineUnavailable('intake fixture quality engine unavailable')
        passed = self.outcome == 'passed'
        return {
            'engine': 'intake-test', 'engine_version': '1', 'passed': passed,
            'issues': [] if passed else [{
                'code': 'quality_gate_failed', 'severity': 'error',
                'message': 'intake fixture quality gate failed',
            }],
            'stats': {}, 'metrics': {}, 'thresholds': {},
            'threshold_failures': [], 'competency_questions': [],
        }


def _setup(tmp_path, *, quality_engine=None):
    app = create_app(tmp_path / 'intake.sqlite', HashingEncoder())
    app.state.ontology_drafts.quality_engine = (
        quality_engine if quality_engine is not None else _DeterministicQualityEngine())
    repo = app.state.service.data
    project = repo.create_project('selected intake')
    p = project['id']
    for number, name in ((1, '企业客户'), (2, '经销商')):
        repo.put_record(p, {
            'id': f'doc-{number}', 'kind': 'document', 'text': name,
            'metadata': {'title': name, 'discovery_candidates': [{
                'id': f'candidate-{number}', 'kind': 'entity',
                'text': f'实例{number}', 'proposed_type': name,
                'evidence': f'实例{number}属于{name}',
            }]},
        })
    return app, repo, p, f'/api/projects/{p}/ontology-discovery'


def _request(candidate='candidate-1', **extra):
    return {'candidate_ids': [candidate], 'expected_ontology_id': None,
            'create_new_draft': True, 'name': '本轮结构补充', **extra}


def _record_payload(record):
    return {key: deepcopy(value) for key, value in record.items() if key not in {
        'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}}


def _scoped_source(repo, project, document_id, candidates, *, batch_item=None):
    """Persist the same receipt -> run -> chunk -> assertion chain as ingestion."""
    try:
        previous = repo.get_record(project, document_id)
    except KeyError:
        previous = None
    metadata = {'title': document_id, 'status': 'processing'}
    if batch_item:
        metadata.update(batch_id=batch_item['batch_id'], batch_item_id=batch_item['id'],
                        document_generation=batch_item['document_generation'])
    receipt = repo.put_record(project, {
        'id': document_id, 'kind': 'document', 'text': 'source text', 'metadata': metadata},
        expected_version=previous['version'] if previous else 0)
    run = repo.create_ingest_run(project, document_id, receipt['version_id'])
    chunk_id = document_id + ':chunk:0'
    try:
        chunk = repo.get_record(project, chunk_id)
    except KeyError:
        chunk = None
    repo.put_record(project, {'id': chunk_id, 'kind': 'chunk', 'source_id': document_id,
        'text': 'source text', 'metadata': {'source_version_id': receipt['version_id'],
            'run_id': run['id'], 'start_char': 0, 'end_char': 11}},
        expected_version=chunk['version'] if chunk else 0)
    candidates = [{**candidate, 'chunk_id': chunk_id} for candidate in candidates]
    for candidate in candidates:
        repo.create_assertion(project, {'id': candidate['id'], 'kind': candidate['kind'],
            'document_id': document_id, 'document_version_id': receipt['version_id'],
            'chunk_id': chunk_id, 'payload': candidate, 'quote': 'source text'})
    completed = repo.put_record(project, {**_record_payload(receipt), 'metadata': {
        **metadata, 'status': 'ready', 'run_id': run['id'], 'discovery_candidates': candidates}},
        expected_version=receipt['version'])
    run = repo.update_ingest_run(project, run['id'], run['version'], status='completed')
    return run, completed


def _scope_entity(candidate_id):
    return {'id': candidate_id, 'kind': 'entity', 'text': candidate_id, 'proposed_type': '企业客户'}


def _scope_batch(repo, project, candidate_id, *, replayed=False):
    store = repo.ingest_batches
    batch = store.create_batch(project, {'extraction_mode': 'discovery'})
    item = store.add_item(project, batch['id'], candidate_id + '.txt', b'source text',
        candidate_id, candidate_id, 10, 10000)
    run, document = _scoped_source(repo, project, item['document_id'],
        [_scope_entity(candidate_id)], batch_item=item)
    store.transition(project, batch['id'], 'start')
    claimed = store.claim(batch['id'], 'scope-test', 120)
    result = {'document': document, 'records': [], **(
        {'replayed': True} if replayed else {'run_id': run['id']})}
    assert store.finish(item['id'], 'scope-test', claimed['lease_token'], 'succeeded', result=result)
    return batch, run, document


def test_inventory_run_scope_uses_frozen_assertion_chunk_chain(tmp_path, monkeypatch):
    app, repo, p, path = _setup(tmp_path)
    first, _ = _scoped_source(repo, p, 'scoped-a', [_scope_entity('scope-a')])
    second, _ = _scoped_source(repo, p, 'scoped-b', [_scope_entity('scope-b')])
    monkeypatch.setattr(type(app.state.service.repository), 'history',
        lambda *_args: pytest.fail('scope must read provenance in bulk'))
    with TestClient(app) as client:
        result = client.get(path + '/candidates', params={'run_id': first['id']}).json()
        assert [x['id'] for x in result['items']] == ['scope-a']
        assert result['total'] == 1
        # Ingest binds the pre-extraction receipt, not the final document version.
        assert result['items'][0]['assertion_document_version_id'] == first['document_version_id']
        assert result['items'][0]['document_version_id'] != first['document_version_id']
        empty = client.get(path + '/candidates', params={
            'run_id': second['id'], 'document_id': 'scoped-a'}).json()
        assert empty['items'] == [] and empty['scope_note']
        assert {x['id'] for x in client.get(path + '/candidates').json()['items']} == {
            'candidate-1', 'candidate-2', 'scope-a', 'scope-b'}


def test_inventory_old_run_does_not_show_reprocessed_document_candidates(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    old, _ = _scoped_source(repo, p, 'scoped-a', [_scope_entity('old-scope')])
    current, _ = _scoped_source(repo, p, 'scoped-a', [_scope_entity('new-scope')])
    with TestClient(app) as client:
        stale = client.get(path + '/candidates', params={'run_id': old['id']}).json()
        assert stale['items'] == [] and stale['total'] == 0 and stale['scope_note']
        assert [x['id'] for x in client.get(path + '/candidates',
            params={'run_id': current['id']}).json()['items']] == ['new-scope']
        assert [x['id'] for x in client.get(path + '/candidates',
            params={'document_id': 'scoped-a'}).json()['items']] == ['new-scope']


def test_inventory_run_scope_rejects_candidate_identity_copied_into_another_document(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    run, document = _scoped_source(repo, p, 'scoped-a', [_scope_entity('scope-a')])
    repo.put_record(p, {'id': 'copied-document', 'kind': 'document', 'text': 'another source',
        'metadata': {'run_id': run['id'],
            'discovery_candidates': document['metadata']['discovery_candidates']}})
    with TestClient(app) as client:
        result = client.get(path + '/candidates', params={'run_id': run['id']}).json()
        assert [(x['document_id'], x['id']) for x in result['items']] == [('scoped-a', 'scope-a')]


@pytest.mark.parametrize('broken', ['missing_chunk', 'ambiguous_chunk', 'wrong_chunk_run', 'wrong_document_run'])
def test_inventory_run_scope_fails_closed_when_provenance_is_unverifiable(tmp_path, broken):
    app, repo, p, path = _setup(tmp_path)
    run, document = _scoped_source(repo, p, 'scoped-a', [_scope_entity('scope-a')])
    chunk = repo.get_record(p, 'scoped-a:chunk:0')
    if broken == 'missing_chunk':
        storage = app.state.service.repository
        with storage._transaction():
            storage._db.execute('DELETE FROM record_versions WHERE project_id=? AND id=?',
                                (p, chunk['id']))
    elif broken == 'wrong_document_run':
        payload = _record_payload(document)
        payload['metadata']['run_id'] = 'another-run'
        repo.put_record(p, payload, expected_version=document['version'])
    else:
        payload = _record_payload(chunk)
        payload['metadata']['run_id'] = 'another-run' if broken == 'wrong_chunk_run' else run['id']
        repo.put_record(p, payload, expected_version=chunk['version'])
    with TestClient(app) as client:
        result = client.get(path + '/candidates', params={'run_id': run['id']}).json()
        assert result['items'] == [] and result['scope_note']


def test_inventory_batch_scope_checks_result_run_and_combines_filters(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    first, first_run, document = _scope_batch(repo, p, 'batch-a', replayed=True)
    second, second_run, _ = _scope_batch(repo, p, 'batch-b')
    with TestClient(app) as client:
        result = client.get(path + '/candidates', params={'batch_id': first['id']}).json()
        assert [x['id'] for x in result['items']] == ['batch-a']
        assert [x['id'] for x in client.get(path + '/candidates', params={
            'batch_id': first['id'], 'document_id': document['id'],
            'run_id': first_run['id']}).json()['items']] == ['batch-a']
        for filters in ({'batch_id': first['id'], 'run_id': second_run['id']},
                        {'batch_id': 'missing-batch'}, {'run_id': 'missing-run'}):
            empty = client.get(path + '/candidates', params=filters).json()
            assert empty['items'] == [] and empty['scope_note']
        # A frozen batch result must not identify a later run of the same document.
        _scoped_source(repo, p, document['id'], [_scope_entity('replacement')])
        empty = client.get(path + '/candidates', params={'batch_id': first['id']}).json()
        assert empty['items'] == [] and empty['scope_note']


def test_inventory_scoped_materialized_dependencies_do_not_include_other_runs(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    run, _ = _scoped_source(repo, p, 'scoped-a', [_scope_entity('scope-entity'), {
        'id': 'scope-code', 'kind': 'attribute', 'entity_id': 'scope-entity',
        'proposed_type': '编号', 'value': 'one'}])
    other_run, _ = _scoped_source(repo, p, 'scoped-b', [{
        'id': 'other-code', 'kind': 'attribute', 'entity_id': 'scope-entity',
        'proposed_type': '编号', 'value': 'two'}])
    published = seed_ontology(repo, p, '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:test:C> a owl:Class ; rdfs:label "企业客户"@zh .''', {})
    with TestClient(app) as client:
        body = _request('scope-entity', create_new_draft=False, expected_ontology_id=published['id'],
            confirmed_mappings={'scope-entity': 'urn:test:C'})
        preview = _preview(client, path, body)
        response = client.post(path + '/confirm-reuse', json={**body,
            'source_fingerprint': preview['source_fingerprint'],
            'preview_fingerprint': preview['preview_fingerprint'], 'idempotency_key': 'scoped-dependency'})
        assert response.status_code == 200, response.text
        result = client.get(path + '/candidates', params={'run_id': run['id']}).json()
        assert {x['id'] for x in result['items']} == {'scope-entity', 'scope-code'}
        entity = next(x for x in result['items'] if x['id'] == 'scope-entity')
        assert entity['materialized'] and entity['dependency_candidate']
        assert entity['required_by'] == ['scope-code']
        assert [x['id'] for x in client.get(path + '/candidates', params={
            'run_id': other_run['id']}).json()['items']] == ['other-code']


@pytest.mark.parametrize('field', ['run_id', 'batch_id'])
def test_inventory_explicit_empty_source_scope_is_invalid(tmp_path, field):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        assert client.get(path + '/candidates', params={field: ''}).status_code == 422


def _preview(client, path, body):
    repo = client.app.state.service.repository
    p = path.split('/')[3]
    existing = {a['id'] for a in repo.list_assertions(p)}
    for document in repo.current_records(p, kinds=['document']):
        for candidate in document.get('metadata', {}).get('discovery_candidates', []):
            if candidate['id'] not in existing and candidate['kind'] in {'entity', 'relation', 'attribute'}:
                repo.create_assertion(p, {'id': candidate['id'], 'kind': candidate['kind'],
                    'document_id': document['id'], 'document_version_id': document['version_id'],
                    'payload': candidate, 'quote': candidate.get('evidence', candidate.get('text', ''))})
    response = client.post(path + '/selection-preview', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _add(client, path, body, key):
    preview = _preview(client, path, body)
    frozen = {**body, 'preview_fingerprint': preview['preview_fingerprint'],
              'source_fingerprint': preview['source_fingerprint'],
              'idempotency_key': key}
    response = client.post(path + '/add-selection', json=frozen)
    assert response.status_code == 200, response.text
    return response.json(), frozen


def test_selection_preview_is_exact_and_has_no_writes(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        preview = _preview(client, path, _request())
        assert preview['candidate_ids'] == ['candidate-1']
        assert preview['can_add']
        assert preview['fact_outcomes'][0]['outcome'] == 'pending'
        assert preview['fact_outcomes'][0]['reason_code'] == 'requires_approved_structure'
        assert [x['id'] for x in preview['associated_fact_scope']] == ['candidate-1']
        assert app.state.ontology_drafts.store.list(p) == []
        assert repo.list_discovery_runs(p) == []
        assert repo.list_ontologies(p) == []


def test_two_selected_batches_preserve_manual_operations_and_retry(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    drafts = app.state.ontology_drafts
    manual = drafts.create_with_command(p, None, 'manual', '手工草案', 'author',
        command={'action': 'create_term', 'target_iri': 'urn:test:Manual', 'kind': 'class'})
    with TestClient(app) as client:
        first, frozen = _add(client, path, _request(
            create_new_draft=False, target_draft_id=manual['id'],
            expected_revision=manual['revision']), 'first')
        retry = client.post(path + '/add-selection', json=frozen)
        assert retry.status_code == 200, retry.text
        assert retry.json()['receipt'] == first['receipt']
        second, _ = _add(client, path, _request('candidate-2',
            create_new_draft=False, target_draft_id=manual['id'],
            expected_revision=first['draft']['revision']), 'second')
        current = second['draft']
        assert current['id'] == manual['id']
        assert current['source_kind'] == 'manual'
        assert manual['operations'][0]['id'] in {x['id'] for x in current['operations']}
        assert len(current['source_context']['intake_bindings']) == 2
        assert len(repo.list_discovery_runs(p)) == 2
        assert len(app.state.ontology_drafts.store.list(p)) == 1
        assert current['validation_fingerprint'] is None
        second_operation_ids = {x['id'] for x in current['operations']} - {
            x['id'] for x in first['draft']['operations']}
        evidence = [reference for operation in current['operations']
                    if operation['id'] in second_operation_ids
                    for reference in operation['evidence']]
        assert evidence and any('doc-2' in x for x in evidence)
        assert all('doc-1' not in x and 'candidate-1' not in x for x in evidence)
        overview = client.get(path).json()
        assert overview['candidate_status_counts']['included_in_draft'] == 2


def test_stale_selection_and_changed_idempotent_intent_leave_no_partial_draft(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        body = _request()
        preview = _preview(client, path, body)
        document = repo.get_record(p, 'doc-1')
        changed = _record_payload(document)
        changed['metadata']['discovery_candidates'][0]['text'] = '已更新'
        repo.put_record(p, changed, expected_version=document['version'])
        response = client.post(path + '/add-selection', json={**body,
            'source_fingerprint': preview['source_fingerprint'],
            'preview_fingerprint': preview['preview_fingerprint'], 'idempotency_key': 'stale'})
        assert response.status_code == 409, response.text
        assert not app.state.ontology_drafts.store.list(p)
        assert not repo.list_discovery_runs(p)
        added, frozen = _add(client, path, body, 'once')
        response = client.post(path + '/add-selection', json={**frozen, 'name': '不同意图'})
        assert response.status_code == 409, response.text
        assert len(app.state.ontology_drafts.store.list(p)) == 1


def test_missing_relation_endpoints_are_visible_and_not_added_implicitly(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    document = repo.get_record(p, 'doc-1')
    version = document['version']
    document = _record_payload(document)
    document['metadata']['discovery_candidates'].append({
        'id': 'relation', 'kind': 'relation', 'proposed_type': '服务',
        'subject_id': 'candidate-1', 'object_id': 'candidate-2'})
    repo.put_record(p, document, expected_version=version)
    with TestClient(app) as client:
        preview = _preview(client, path, _request('relation'))
        assert not preview['can_add']
        assert {x['candidate_id'] for x in preview['missing_dependencies']} == {
            'candidate-1', 'candidate-2'}
        assert preview['candidate_ids'] == ['relation']
        assert preview['fact_outcomes'][0]['outcome'] == 'blocked'
        assert not app.state.ontology_drafts.store.list(p)


def _publish(drafts, repo, p, draft, reject_iri=None):
    submitted = drafts.submit(p, draft['id'], draft['revision'])
    warnings = [x['code'] for x in submitted['validation_report']['warnings']]
    reviewed = submitted
    for x in submitted['operations']:
        reviewed = drafts.decide(p, draft['id'], reviewed['revision'],
        submitted['base_ontology_id'], submitted['validation_fingerprint'], [{
            'operation_id': x['id'], 'operation_fingerprint': x['fingerprint'],
            'action': 'reject' if x['target_iri'] == reject_iri else 'approve',
            'reason': '逐项核对所选来源及定义',
        }], warnings, 'reviewer')
    return drafts.publish(p, draft['id'], reviewed['revision'],
        reviewed['base_ontology_id'], reviewed['validation_fingerprint'],
        warnings, 'publish-once', 'publisher', '统一写入验收')


def test_composite_publication_applies_only_selected_approved_batches(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        first, _ = _add(client, path, _request(), 'first')
        second, _ = _add(client, path, _request('candidate-2',
            create_new_draft=False, target_draft_id=first['draft']['id'],
            expected_revision=first['draft']['revision']), 'second')
        rejected = second['new'][0]['target_iri']
        ontology = _publish(app.state.ontology_drafts, repo, p, second['draft'], rejected)
        facts = repo.current_records(p, kinds=['entity'])
        assert [x['text'] for x in facts] == ['实例1']
        assert {x['status'] for x in repo.list_discovery_runs(p)} == {'published'}
        receipt = ontology['metadata']['publication']['receipt']
        assert receipt['facts']['added'] == 1
        assert receipt['facts']['skipped'] == 1
        assert len(receipt['facts']['per_batch']) == 2
        assert repo.get_artifact('ontology_sync_job', receipt['index']['task_id'])
        assert app.state.ontology_drafts.quality_engine.payloads
        accepted = repo.get_assertion(p, 'candidate-1')
        assert accepted['status'] == 'accepted'
        assert accepted['canonical_record_id'] == facts[0]['id']
        assert facts[0].get('source_id') is None
        from knowledge_service.repository.page_reads import PageReads
        source = PageReads(repo).sources(p, {}, document_id='doc-1')['documents'][0]
        assert source['derived_breakdown']['entity'] == 1


@pytest.mark.parametrize(('outcome', 'error_code'), [
    ('failed', 'semantica_quality_gate_failed'),
    ('unavailable', 'quality_engine_unavailable'),
])
def test_intake_quality_failure_blocks_approval_and_publication(tmp_path, outcome, error_code):
    from knowledge_service.services.ontology_drafts import ValidationChanged, ValidationFailed

    engine = _DeterministicQualityEngine(outcome)
    app, repo, p, path = _setup(tmp_path, quality_engine=engine)
    drafts = app.state.ontology_drafts
    with TestClient(app) as client:
        added, _ = _add(client, path, _request(), 'quality-' + outcome)
        submitted = drafts.submit(p, added['draft']['id'], added['draft']['revision'])
        report = submitted['validation_report']
        assert engine.payloads
        assert report['graph_integrity']['conforms']
        assert report['semantica_quality']['passed'] is False
        assert not report['conforms']
        assert error_code in {issue['code'] for issue in report['errors']}
        assert submitted['validation_fingerprint']
        warnings = [issue['code'] for issue in report['warnings']]
        operation = submitted['operations'][0]
        with pytest.raises(ValidationFailed, match='blocking operations cannot be approved'):
            drafts.decide(p, submitted['id'], submitted['revision'], None,
                submitted['validation_fingerprint'], [{
                    'operation_id': operation['id'],
                    'operation_fingerprint': operation['fingerprint'],
                    'action': 'approve', 'reason': '质量检查失败时仍尝试收下',
                }], warnings, 'reviewer')
        readiness = drafts.publish_readiness(p, submitted['id'])
        assert not readiness['ready']
        assert error_code in {item['code'] for item in readiness['items']}
        with pytest.raises(ValidationChanged, match='final current decision'):
            drafts.publish(p, submitted['id'], submitted['revision'], None,
                submitted['validation_fingerprint'], warnings,
                'blocked-' + outcome, 'publisher', '质量检查未通过时仍尝试发布')
        assert drafts.store.history(p, submitted['id'])[1] == []
        assert not repo.list_ontologies(p)
        assert not repo.current_records(p, kinds=['entity'])
        assert not repo.list_artifacts('ontology_sync_job', p)
        assert repo.list_discovery_runs(p)[0]['status'] == 'draft_created'


def test_closing_manual_draft_closes_every_attached_run(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        first, _ = _add(client, path, _request(), 'first')
        second, _ = _add(client, path, _request('candidate-2',
            create_new_draft=False, target_draft_id=first['draft']['id'],
            expected_revision=first['draft']['revision']), 'second')
        app.state.ontology_drafts.close(p, first['draft']['id'],
            second['draft']['revision'], 'author', '暂不处理')
        assert {x['status'] for x in repo.list_discovery_runs(p)} == {'closed'}
        assert len(repo.get_record(p, 'doc-1')['metadata']['discovery_candidates']) == 1


def test_confirm_reuse_requires_explicit_published_identity_without_draft(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    turtle = '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:test:Customer> a owl:Class ; rdfs:label "企业客户"@zh .'''
    published = seed_ontology(repo, p, turtle, {})
    body = _request(create_new_draft=False, expected_ontology_id=published['id'],
        confirmed_mappings={'candidate-1': 'urn:test:Customer'})
    with TestClient(app) as client:
        preview = _preview(client, path, body)
        assert preview['can_confirm_reuse']
        assert preview['fact_outcomes'][0]['outcome'] == 'add'
        frozen = {**body, 'preview_fingerprint': preview['preview_fingerprint'],
                  'source_fingerprint': preview['source_fingerprint'],
                  'idempotency_key': 'reuse'}
        response = client.post(path + '/confirm-reuse', json=frozen)
        assert response.status_code == 200, response.text
        retry = client.post(path + '/confirm-reuse', json=frozen)
        assert retry.status_code == 200, retry.text
        assert retry.json()['receipt'] == response.json()['receipt']
        assert len(repo.list_ontologies(p)) == 1
        assert not app.state.ontology_drafts.store.list(p)
        assert len(repo.current_records(p, kinds=['entity'])) == 1


def test_failed_cas_rolls_back_operations_bindings_and_run(tmp_path, monkeypatch):
    from knowledge_service.repository.ontology_draft_store import OntologyDraftConflict
    app, repo, p, path = _setup(tmp_path)
    drafts = app.state.ontology_drafts
    draft = drafts.create(p, None, 'manual', '现有草案', 'author')
    body = _request(create_new_draft=False, target_draft_id=draft['id'], expected_revision=draft['revision'])
    with TestClient(app) as client:
        preview = _preview(client, path, body)
        def fail(*args, **kwargs):
            raise OntologyDraftConflict(draft['id'], draft['revision'])
        monkeypatch.setattr(drafts.store, 'compare_and_set', fail)
        response = client.post(path + '/add-selection', json={**body,
            'preview_fingerprint': preview['preview_fingerprint'],
            'source_fingerprint': preview['source_fingerprint'], 'idempotency_key': 'cas'})
        assert response.status_code == 409, response.text
        assert drafts.store.history(p, draft['id'])[0] == []
        assert not drafts.store.get(p, draft['id'])['source_context'].get('intake_bindings')
        assert not repo.list_discovery_runs(p)


def test_all_rejected_draft_closes_attached_run_without_writing_facts(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    drafts = app.state.ontology_drafts
    with TestClient(app) as client:
        added, _ = _add(client, path, _request(), 'reject-all')
        submitted = drafts.submit(p, added['draft']['id'], added['draft']['revision'])
        warnings = [x['code'] for x in submitted['validation_report']['warnings']]
        rejected = submitted
        for x in submitted['operations']:
            rejected = drafts.decide(p, submitted['id'], rejected['revision'], None,
                submitted['validation_fingerprint'], [{'operation_id': x['id'],
                'operation_fingerprint': x['fingerprint'], 'action': 'reject',
                'reason': '本轮不采纳'}], warnings, 'reviewer')
        assert rejected['status'] == 'rejected'
        assert {x['status'] for x in repo.list_discovery_runs(p)} == {'closed'}
        assert not repo.current_records(p, kinds=['entity'])


def test_concurrent_identical_intake_returns_one_run_and_draft(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        body = _request()
        preview = _preview(client, path, body)
        frozen = {**body, 'preview_fingerprint': preview['preview_fingerprint'],
            'source_fingerprint': preview['source_fingerprint'], 'idempotency_key': 'concurrent'}
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: client.post(path + '/add-selection', json=frozen), range(2)))
        assert [x.status_code for x in responses] == [200, 200]
        assert responses[0].json()['receipt'] == responses[1].json()['receipt']
        assert len(repo.list_discovery_runs(p)) == 1
        assert len(app.state.ontology_drafts.store.list(p)) == 1


def test_live_assertion_rejection_blocks_manual_bound_draft(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    version = repo.get_record(p, 'doc-1')['version_id']
    assertion = repo.create_assertion(p, {'id': 'candidate-1', 'kind': 'entity',
        'document_id': 'doc-1', 'document_version_id': version, 'chunk_id': 'chunk-1',
        'source_hash': 'source', 'start_char': 0, 'end_char': 3,
        'quote': '实例1', 'payload': {'text': '实例1', 'proposed_type': '企业客户'}})
    with TestClient(app) as client:
        added, _ = _add(client, path, _request(), 'assertion')
        rejected = repo.transition_assertion(p, assertion['id'], assertion['decision_version'],
            'rejected', '撤回此来源断言', 'reviewer')
        assert rejected['status'] == 'rejected'
        assert rejected['decision_version'] == assertion['decision_version'] + 1
        response = client.post(f'/api/projects/{p}/ontology-drafts/{added["draft"]["id"]}/submit',
            json={'expected_revision': added['draft']['revision']})
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'stale_source'
        assert not repo.list_ontologies(p)
        assert not repo.current_records(p, kinds=['entity'])


def test_request_identity_uses_immutable_user_id():
    from knowledge_service.services.ontology_intake import OntologyIntake
    request = {'idempotency_key': 'once', 'candidate_ids': ['c']}
    before = OntologyIntake._request_identity('p', request, '旧名字 (user; user:42)', 'add', '42')
    after = OntologyIntake._request_identity('p', request, '新名字 (new; user:42)', 'add', '42')
    assert before == after


def test_publication_rechecks_selected_source_inside_repository_transaction(tmp_path):
    from knowledge_service.repository import OntologyPublicationConflict
    app, repo, p, path = _setup(tmp_path)
    drafts = app.state.ontology_drafts
    with TestClient(app) as client:
        added, _ = _add(client, path, _request(), 'source-publication')
        current = drafts.submit(p, added['draft']['id'], added['draft']['revision'])
        warnings = [x['code'] for x in current['validation_report']['warnings']]
        for operation in current['operations']:
            current = drafts.decide(p, current['id'], current['revision'], None,
                current['validation_fingerprint'], [{'operation_id': operation['id'],
                    'operation_fingerprint': operation['fingerprint'], 'action': 'approve',
                    'reason': '逐项核对来源'}], warnings, 'reviewer')
        prepared = drafts.publish_preflight(p, current['id'], current['revision'], None,
            current['validation_fingerprint'], warnings, 'publish', 'publisher', '来源复核')
        document = repo.get_record(p, 'doc-1')
        changed = _record_payload(document)
        changed['metadata']['discovery_candidates'][0]['text'] = '来源修订'
        repo.put_record(p, changed, expected_version=document['version'])
        with pytest.raises(OntologyPublicationConflict) as error:
            repo.publish_ontology_draft(**prepared)
        assert error.value.code == 'stale_source'
        assert not repo.list_ontologies(p)
        assert not repo.current_records(p, kinds=['entity'])
        assert repo.list_discovery_runs(p)[0]['status'] == 'draft_created'


def test_independent_connections_retry_same_identity_without_partial_drafts(tmp_path, monkeypatch):
    from knowledge_service.repository import Repository, OntologyPublicationConflict, DiscoveryRunConflict
    from knowledge_service.services.ontology_drafts import OntologyDrafts
    from knowledge_service.services.ontology_intake import OntologyIntake
    app, repo, p, path = _setup(tmp_path)
    other = Repository(tmp_path / 'intake.sqlite')
    other_drafts = OntologyDrafts(other, publisher=other,
        quality_engine=_DeterministicQualityEngine())
    first = OntologyIntake(app.state.service)
    second = OntologyIntake(SimpleNamespace(data=other, ontology_drafts=lambda **_: other_drafts))
    barrier = Barrier(2)
    originals = []
    for intake in (first, second):
        original = intake._replay
        originals.append(original)
        def synchronize(*args, _original=original):
            found = _original(*args)
            barrier.wait(timeout=15)
            return found
        monkeypatch.setattr(intake, '_replay', synchronize)
    try:
        with TestClient(app) as client:
            body = _request()
            preview = _preview(client, path, body)
            frozen = {**body, 'preview_fingerprint': preview['preview_fingerprint'],
                'source_fingerprint': preview['source_fingerprint'], 'idempotency_key': 'independent'}
            def execute(intake):
                try:
                    return intake.add(p, frozen, actor='author', actor_identity='stable-user')
                except (OntologyPublicationConflict, DiscoveryRunConflict) as error:
                    return error
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(execute, (first, second)))
            assert any(isinstance(x, dict) for x in responses)
            for intake, original in zip((first, second), originals):
                monkeypatch.setattr(intake, '_replay', original)
            retried = second.add(p, frozen, actor='renamed author', actor_identity='stable-user')
            successful = next(x for x in responses if isinstance(x, dict))
            assert retried['receipt'] == successful['receipt']
            assert len(repo.list_discovery_runs(p)) == 1
            assert len(app.state.ontology_drafts.store.list(p)) == 1
    finally:
        other.close()


def test_inventory_keeps_materialized_entity_dependencies_selectable(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    published = seed_ontology(repo, p, '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:test:C> a owl:Class ; rdfs:label "企业客户"@zh .''', {})
    with TestClient(app) as client:
        body = _request(create_new_draft=False, expected_ontology_id=published['id'],
            confirmed_mappings={'candidate-1': 'urn:test:C'})
        preview = _preview(client, path, body)
        response = client.post(path + '/confirm-reuse', json={**body,
            'source_fingerprint': preview['source_fingerprint'],
            'preview_fingerprint': preview['preview_fingerprint'], 'idempotency_key': 'entity-first'})
        assert response.status_code == 200, response.text
        document = repo.get_record(p, 'doc-1')
        entity_before = repo.current_records(p, kinds=['entity'])[0]
        repo.create_assertion(p, {'id': 'additional-support', 'kind': 'entity',
            'document_id': 'doc-2', 'payload': {'text': 'another source'}})
        repo.transition_assertion(p, 'additional-support', 1, 'accepted', 'source confirmed', 'test',
            canonical_record_id=entity_before['id'])
        edited = _record_payload(document)
        edited['metadata']['discovery_candidates'].append({'id': 'code', 'kind': 'attribute',
            'entity_id': 'candidate-1', 'proposed_type': '统一社会信用代码', 'value': 'ABC'})
        repo.put_record(p, edited, expected_version=document['version'])
        inventory = client.get(path + '/candidates').json()
        entity = next(x for x in inventory['items'] if x['id'] == 'candidate-1')
        assert entity['materialized'] and entity['dependency_candidate']
        assert entity['required_by'] == ['code']
        preview = _preview(client, path, _request(expected_ontology_id=published['id'],
            candidate_ids=['candidate-1', 'code'], confirmed_mappings={'candidate-1': 'urn:test:C'}))
        assert preview['can_add']
        assert {x['candidate_id']: x['outcome'] for x in preview['fact_outcomes']} == {
            'candidate-1': 'reuse', 'code': 'pending'}
        added, _ = _add(client, path, _request(expected_ontology_id=published['id'],
            candidate_ids=['candidate-1', 'code'], confirmed_mappings={'candidate-1': 'urn:test:C'}),
            'extend-supported-entity')
        _publish(app.state.ontology_drafts, repo, p, added['draft'])
        assert repo.get_record(p, entity_before['id'])['version_id'] == entity_before['version_id']
        assert repo.get_assertion(p, 'additional-support')['canonical_record_id'] == entity_before['id']
        assert repo.get_assertion(p, 'code')['status'] == 'accepted'


def test_wholly_repeated_selection_is_diagnosed_before_another_binding(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        first, _ = _add(client, path, _request(), 'first')
        body = _request(create_new_draft=False, target_draft_id=first['draft']['id'],
            expected_revision=first['draft']['revision'])
        preview = _preview(client, path, body)
        assert not preview['can_add']
        assert preview['conflicts'][0]['code'] == 'candidate_already_bound'
        response = client.post(path + '/add-selection', json={**body,
            'source_fingerprint': preview['source_fingerprint'],
            'preview_fingerprint': preview['preview_fingerprint'], 'idempotency_key': 'different-key'})
        assert response.status_code == 422, response.text
        assert len(repo.list_discovery_runs(p)) == 1
        assert app.state.ontology_drafts.store.get(p, first['draft']['id'])['revision'] == first['draft']['revision']


def test_refreshing_draft_refs_cannot_thaw_immutable_selected_source(tmp_path):
    from knowledge_service.services.ontology_drafts import StaleSource
    app, repo, p, path = _setup(tmp_path)
    drafts = app.state.ontology_drafts
    with TestClient(app) as client:
        added, _ = _add(client, path, _request(), 'frozen-run')
        document = repo.get_record(p, 'doc-1')
        changed = _record_payload(document)
        changed['metadata']['discovery_candidates'][0]['text'] = '更新后的不同事实'
        repo.put_record(p, changed, expected_version=document['version'])
        current = drafts.store.get(p, added['draft']['id'])
        refreshed = drafts._refresh_source_context(p, current['source_context'])
        updated = drafts.store.compare_and_set(p, current['id'], current['revision'],
            {'source_context': refreshed})
        # This is the source refresh performed by rebase. The independent,
        # immutable run snapshot must still stop the old fact from publishing.
        with pytest.raises(StaleSource):
            drafts.submit(p, updated['id'], updated['revision'])
        assert not repo.list_ontologies(p)
        assert repo.list_discovery_runs(p)[0]['candidate_snapshot'][0]['text'] == '实例1'


def test_rebase_keeps_discovery_origin_on_manual_draft_operations(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        added, _ = _add(client, path, _request(), 'origin')
        published = seed_ontology(repo, p,
            '@prefix owl: <http://www.w3.org/2002/07/owl#> . <urn:test:Other> a owl:Class .', {})
        rebased = app.state.ontology_drafts.rebase(p, added['draft']['id'],
            added['draft']['revision'], published['id'])
        assert rebased['source_kind'] == 'manual'
        assert all(x['validation']['source'] == 'discovery' for x in rebased['operations'])


def _attribute_candidates_setup(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    document = repo.get_record(p, 'doc-1')
    edited = _record_payload(document)
    edited['metadata']['discovery_candidates'].extend([
        {'id': 'code-one', 'kind': 'attribute', 'entity_id': 'candidate-1',
         'proposed_type': '客户代码', 'value': 'one'},
        {'id': 'code-two', 'kind': 'attribute', 'entity_id': 'candidate-1',
         'proposed_type': '客户代码', 'value': 'two'},
    ])
    repo.put_record(p, edited, expected_version=document['version'])
    return app, repo, p, path


def _attribute_ontology(*, required=False):
    return '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        <urn:test:Customer> a owl:Class ; rdfs:label "企业客户"@zh .
        <urn:test:Code> a owl:DatatypeProperty ; rdfs:label "客户代码"@zh ;
            rdfs:domain <urn:test:Customer> ; rdfs:range xsd:string .
        <urn:test:Shape> a sh:NodeShape ; sh:targetClass <urn:test:Customer> ;
            sh:property [ sh:path <urn:test:Code> ; sh:maxCount 1 ''' + (
                '; sh:minCount 1' if required else '') + ' ] .'


def _confirm_selected_reuse(client, path, body, key):
    preview = _preview(client, path, body)
    frozen = {**body, 'source_fingerprint': preview['source_fingerprint'],
              'preview_fingerprint': preview['preview_fingerprint'], 'idempotency_key': key}
    return preview, frozen, client.post(path + '/confirm-reuse', json=frozen)


def test_reuse_max_count_conflict_preserves_existing_facts_runs_and_outbox(tmp_path, monkeypatch):
    app, repo, p, path = _attribute_candidates_setup(tmp_path)
    # Assert transaction effects independently of the asynchronous consumer,
    # which can legitimately update the first reuse's existing outbox job.
    monkeypatch.setattr(app.state.ontology_sync, 'start', lambda: None)
    published = seed_ontology(repo, p, _attribute_ontology(), {})
    body = _request(create_new_draft=False, expected_ontology_id=published['id'],
        candidate_ids=['candidate-1', 'code-one'],
        confirmed_mappings={'candidate-1': 'urn:test:Customer', 'code-one': 'urn:test:Code'})
    with TestClient(app) as client:
        _, _, first = _confirm_selected_reuse(client, path, body, 'first-code')
        assert first.status_code == 200, first.text
        facts_before = repo.current_records(p, kinds=['entity', 'relation', 'attribute'])
        runs_before = repo.list_discovery_runs(p)
        jobs_before = repo.list_artifacts('ontology_sync_job', p)
        body.update(candidate_ids=['candidate-1', 'code-two'],
            confirmed_mappings={'candidate-1': 'urn:test:Customer', 'code-two': 'urn:test:Code'})
        preview, _, response = _confirm_selected_reuse(client, path, body, 'conflicting-code')
        assert not preview['can_confirm_reuse']
        report = next(item['report'] for item in preview['conflicts']
                      if item['code'] == 'ontology_validation_failed')
        assert report['errors'][0]['constraint'] == 'MaxCountConstraintComponent'
        assert response.status_code == 422, response.text
        assert response.json()['code'] == 'validation_failed'
        assert repo.current_records(p, kinds=['entity', 'relation', 'attribute']) == facts_before
        assert repo.list_discovery_runs(p) == runs_before
        assert repo.list_artifacts('ontology_sync_job', p) == jobs_before
        assert [item['id'] for item in repo.list_ontologies(p)] == [published['id']]


def test_complete_required_attribute_reuse_writes_all_selected_facts_and_retries(tmp_path):
    app, repo, p, path = _attribute_candidates_setup(tmp_path)
    published = seed_ontology(repo, p, _attribute_ontology(required=True), {})
    body = _request(create_new_draft=False, expected_ontology_id=published['id'],
        candidate_ids=['candidate-1', 'code-one'],
        confirmed_mappings={'candidate-1': 'urn:test:Customer', 'code-one': 'urn:test:Code'})
    with TestClient(app) as client:
        preview, frozen, response = _confirm_selected_reuse(client, path, body, 'required-code')
        assert preview['can_confirm_reuse']
        assert {item['outcome'] for item in preview['fact_outcomes']} == {'add'}
        assert response.status_code == 200, response.text
        receipt = response.json()['receipt']
        assert {key: receipt['facts'][key] for key in ('selected', 'added', 'reused', 'skipped')} == {
            'selected': 2, 'added': 2, 'reused': 0, 'skipped': 0}
        retry = client.post(path + '/confirm-reuse', json=frozen)
        assert retry.status_code == 200, retry.text
        assert retry.json()['receipt'] == receipt
        assert len(repo.current_records(p, kinds=['entity', 'relation', 'attribute'])) == 2
        assert len(repo.list_discovery_runs(p)) == 1
        assert len(repo.list_artifacts('ontology_sync_job', p)) == 1
        assert len(repo.list_ontologies(p)) == 1


def test_composite_max_count_conflict_leaves_no_ontology_facts_or_outbox(tmp_path):
    from knowledge_service.services.ontology_drafts import ValidationFailed

    app, repo, p, path = _attribute_candidates_setup(tmp_path)
    drafts = app.state.ontology_drafts
    with TestClient(app) as client:
        first, _ = _add(client, path, _request(
            candidate_ids=['candidate-1', 'code-one']), 'first-code')
        second, _ = _add(client, path, _request(candidate_ids=['candidate-1', 'code-two'],
            create_new_draft=False, target_draft_id=first['draft']['id'],
            expected_revision=first['draft']['revision']), 'second-code')
        draft = second['draft']
        targets = {binding['candidate_id']: binding['target_iri'] for binding in second['new']}
        working, _ = drafts._ontology_for_draft(p, draft)
        shaped = working + f'''
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            <urn:test:SelectedShape> a sh:NodeShape ;
                sh:targetClass <{targets['candidate-1']}> ;
                sh:property [ sh:path <{targets['code-two']}> ; sh:maxCount 1 ] .'''
        updated = drafts.command(p, draft['id'], draft['revision'], {
            'action': 'replace_turtle', 'edited_turtle': shaped})
        runs_before = repo.list_discovery_runs(p)
        with pytest.raises(ValidationFailed) as error:
            _publish(drafts, repo, p, updated)
        assert error.value.details['report']['errors'][0]['constraint'] == 'MaxCountConstraintComponent'
        assert not repo.list_ontologies(p)
        assert not repo.current_records(p, kinds=['entity', 'relation', 'attribute'])
        assert not repo.list_artifacts('ontology_sync_job', p)
        assert repo.list_discovery_runs(p) == runs_before
        assert {item['status'] for item in runs_before} == {'draft_created'}
        assert drafts.store.get(p, draft['id'])['status'] == 'pending'


def test_retiring_existing_mapped_type_skips_selected_entity_and_attribute(tmp_path):
    app, repo, p, path = _attribute_candidates_setup(tmp_path)
    drafts = app.state.ontology_drafts
    base = seed_ontology(repo, p, '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <urn:test:Customer> a owl:Class ; rdfs:label "企业客户"@zh .''', {})
    with TestClient(app) as client:
        added, _ = _add(client, path, _request(expected_ontology_id=base['id'],
            candidate_ids=['candidate-1', 'code-one'],
            confirmed_mappings={'candidate-1': 'urn:test:Customer'}), 'mapped-type')
        run = repo.list_discovery_runs(p)[0]
        binding = next(item for item in run['candidate_bindings']
                       if item['candidate_id'] == 'candidate-1')
        assert binding['required_operation_ids'] == []
        attribute_iri = next(item['target_iri'] for item in added['new']
                             if item['candidate_id'] == 'code-one')
        # The ontology retirement itself must be valid; remove the new field's
        # domain dependency before retiring its formerly published subject type.
        without_domain = drafts.command(p, added['draft']['id'], added['draft']['revision'], {
            'action': 'remove_domain', 'target_iri': attribute_iri, 'value': 'urn:test:Customer'})
        retired = drafts.command(p, without_domain['id'], without_domain['revision'], {
            'action': 'retire_term', 'target_iri': 'urn:test:Customer'})
        ontology = _publish(drafts, repo, p, retired)
        receipt = ontology['metadata']['publication']['receipt']
        assert {key: receipt['facts'][key] for key in ('selected', 'added', 'reused', 'skipped')} == {
            'selected': 2, 'added': 0, 'reused': 0, 'skipped': 2}
        outcomes = receipt['facts']['per_batch'][0]['candidate_outcomes']
        assert {item['candidate_id']: (item['status'], item['reason_code']) for item in outcomes} == {
            'candidate-1': ('skipped', 'ontology_validation_failed'),
            'code-one': ('skipped', 'ontology_validation_failed')}
        assert not repo.current_records(p, kinds=['entity', 'relation', 'attribute'])
        assert repo.list_discovery_runs(p)[0]['status'] == 'published'
        assert len(repo.list_ontologies(p)) == 2


def test_reuse_deduplicates_same_fact_and_preserves_both_source_assertions(tmp_path):
    app, repo, p, path = _attribute_candidates_setup(tmp_path)
    document = repo.get_record(p, 'doc-1')
    edited = _record_payload(document)
    next(x for x in edited['metadata']['discovery_candidates'] if x['id']=='code-two')['value'] = 'one'
    repo.put_record(p, edited, expected_version=document['version'])
    ontology = seed_ontology(repo, p, _attribute_ontology(), {})
    body = _request(create_new_draft=False, expected_ontology_id=ontology['id'],
        candidate_ids=['candidate-1','code-one','code-two'],
        confirmed_mappings={'candidate-1':'urn:test:Customer','code-one':'urn:test:Code','code-two':'urn:test:Code'})
    with TestClient(app) as client:
        _, frozen, response = _confirm_selected_reuse(client, path, body, 'same-fact')
        assert response.status_code==200, response.text
        receipt = response.json()['receipt']
        assert receipt['facts']['added']==2 and receipt['facts']['reused']==1
        assert len(repo.current_records(p, kinds=['attribute']))==1
        first, second = (repo.get_assertion(p, id) for id in ['code-one','code-two'])
        assert first['status']==second['status']=='accepted'
        assert first['canonical_record_id']==second['canonical_record_id']
        assert client.post(path+'/confirm-reuse',json=frozen).json()['receipt']==receipt
        inventory = client.get(path+'/candidates').json()
        assert not {'code-one','code-two'} & {x['id'] for x in inventory['items']}


def test_candidates_without_current_assertions_are_blocked_before_draft_creation(tmp_path):
    app, repo, p, path = _setup(tmp_path)
    with TestClient(app) as client:
        response = client.post(path+'/selection-preview',json=_request())
        assert response.status_code==200,response.text
        preview=response.json()
        assert not preview['can_add']
        assert 'source_assertion_missing' in {x['code'] for x in preview['conflicts']}
        response=client.post(path+'/add-selection',json={**_request(),
            'source_fingerprint':preview['source_fingerprint'],
            'preview_fingerprint':preview['preview_fingerprint'],'idempotency_key':'unbound'})
        assert response.status_code==422,response.text
        assert not app.state.ontology_drafts.store.list(p)
        assert not repo.list_intake_runs(p)
