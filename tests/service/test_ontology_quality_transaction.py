"""Validation must use a prepared engine but still check the transaction's data."""
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from knowledge_service.repository import Repository
from knowledge_service.services import ontology_drafts as drafts_module
from knowledge_service.services.ontology_drafts import (
    OntologyDrafts, RevisionConflict, ValidationChanged,
)
from ontology_seed import seed_ontology


TERM = 'https://example.test/Thing'
BASE = f'<{TERM}> a <http://www.w3.org/2002/07/owl#Class> .'


def draft_fixture(repo):
    project = repo.create_project('quality transaction')['id']
    base = seed_ontology(repo, project, BASE, {})['id']
    service = OntologyDrafts(repo)
    draft = service.create(project, base, 'manual', 'Quality test', 'author')
    draft = service.command(project, draft['id'], draft['revision'], {
        'action': 'add_annotation', 'target_iri': TERM,
        'predicate': 'http://www.w3.org/2000/01/rdf-schema#label',
        'value': 'Thing', 'language': 'en',
    })
    return service, project, base, draft


def fixture_engine():
    return drafts_module_adapter(
        checker=lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {'passed': True}),
        version='quality-transaction-fixture',
    )


# Retain the real adapter regardless of the service's patched constructor.
drafts_module_adapter = drafts_module.SemanticaOntologyQuality


@pytest.mark.parametrize(('action', 'check_states'), [
    ('submit', [TransactionStatus.INTRANS]),
    ('validate', [TransactionStatus.INTRANS]),
    ('decide', [TransactionStatus.IDLE, TransactionStatus.INTRANS]),
    ('definition', [TransactionStatus.INTRANS] * 3),
])
def test_engine_initializes_once_outside_and_checks_in_expected_transactions(
        tmp_path, monkeypatch, action, check_states):
    repo = Repository(tmp_path / 'quality.sqlite')
    initializations = []
    checks = []
    conn = repo._db._conn

    def construct():
        initializations.append(conn.info.transaction_status)
        engine = fixture_engine()
        check = engine.check

        def traced(payload):
            checks.append(conn.info.transaction_status)
            assert {item['uri'] for item in payload['classes']} == {TERM}
            return check(payload)

        engine.check = traced
        return engine

    monkeypatch.setattr(drafts_module, 'SemanticaOntologyQuality', construct)
    service, project, base, draft = draft_fixture(repo)
    assert initializations == []  # app construction, create and edit stay lazy
    if action != 'submit':
        draft = service.submit(project, draft['id'], draft['revision'])
        initializations.clear()
        checks.clear()
    if action in {'validate', 'submit'}:
        updated = getattr(service, action)(project, draft['id'], draft['revision'])
    elif action == 'definition':
        updated = service.decide_by_definition(
            project, draft['id'], draft['revision'], base, draft['validation_fingerprint'],
            TERM, 'approve', None, [], 'reviewer')['draft']
    else:
        operation = draft['operations'][0]
        updated = service.decide(
            project, draft['id'], draft['revision'], base, draft['validation_fingerprint'],
            [{'operation_id': operation['id'], 'operation_fingerprint': operation['fingerprint'],
              'action': 'approve'}], [], 'reviewer')
    assert initializations == [TransactionStatus.IDLE]
    assert checks == check_states
    assert updated['revision'] == draft['revision'] + 1
    assert updated['validation_report']['conforms']


def test_submit_rechecks_revision_after_preparation(tmp_path, monkeypatch):
    path = tmp_path / 'quality.sqlite'
    service, project, base, draft = draft_fixture(Repository(path))
    other = OntologyDrafts(Repository(path))
    calls = []

    def construct():
        updated = other.command(project, draft['id'], draft['revision'], {
            'action': 'add_annotation', 'target_iri': TERM,
            'predicate': 'http://www.w3.org/2000/01/rdf-schema#comment',
            'value': 'Concurrent edit', 'language': 'en',
        })
        calls.append(updated['revision'])
        return fixture_engine()

    monkeypatch.setattr(drafts_module, 'SemanticaOntologyQuality', construct)
    with pytest.raises(RevisionConflict):
        service.submit(project, draft['id'], draft['revision'])
    saved = service.store.get(project, draft['id'])
    assert calls == [saved['revision']] == [draft['revision'] + 1]
    assert saved['submitted_at'] is None and saved['validation_fingerprint'] is None


def test_unavailable_engine_keeps_blocking_report_and_next_request_can_retry(
        tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'quality.sqlite')
    service, project, base, draft = draft_fixture(repo)
    attempts = []

    def construct():
        attempts.append(repo._db._conn.info.transaction_status)
        if len(attempts) <= 2:
            raise drafts_module.QualityEngineUnavailable('fixture runtime unavailable')
        return fixture_engine()

    monkeypatch.setattr(drafts_module, 'SemanticaOntologyQuality', construct)
    submitted = service.submit(project, draft['id'], draft['revision'])
    report = submitted['validation_report']
    assert not report['conforms']
    assert report['semantica_quality']['error'] == 'quality_engine_unavailable'
    assert any(issue['code'] == 'quality_engine_unavailable' for issue in report['errors'])
    assert not repo._ontology_drafts.export(project)['decisions']
    with pytest.raises(ValidationChanged) as exc:
        service.decide_by_definition(
            project, draft['id'], submitted['revision'], base, 'not-the-fingerprint',
            TERM, 'approve', None, [], 'reviewer')
    assert exc.value.details['validation_fingerprint'] == submitted['validation_fingerprint']
    assert not repo._ontology_drafts.export(project)['decisions']
    assert service.store.get(project, draft['id'])['revision'] == submitted['revision']
    recovered = service.validate(project, draft['id'], submitted['revision'])
    assert recovered['validation_report']['conforms']
    assert recovered['validation_fingerprint'] != submitted['validation_fingerprint']
    assert attempts == [TransactionStatus.IDLE] * 3
