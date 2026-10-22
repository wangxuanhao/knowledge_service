"""Read-bound and governance contracts for an existing entity's text revision."""
import copy
import json
import re
from threading import Event, Thread

import pytest

from ontology_seed import seed_ontology
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.domain.formal_facts import manual_assertion_id
from knowledge_service.models import RecordWrite
from knowledge_service.repository import Repository
from knowledge_service.repository.connection import LockNotAvailable
from knowledge_service.services.formal_writes import FormalFactWriter
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.service import KnowledgeService, OntologyValidationError


ONTOLOGY = (
    '@prefix ex: <http://ex/> . '
    '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
    '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . '
    'ex:Person a owl:Class . ex:knows a owl:ObjectProperty .')


def _system(tmp_path, turtle=ONTOLOGY):
    repository = Repository(tmp_path / 'scoped-text.sqlite')
    project = repository.create_project('bounded revision')['id']
    ontology = seed_ontology(repository, project, turtle, Ontology(turtle).summary())
    service = KnowledgeService(repository, HashingEncoder())
    return repository, service, project, ontology


def _changed(record, **changes):
    return {**{key: copy.deepcopy(value) for key, value in record.items()
               if key in RecordWrite.model_fields}, **changes}


def _no_corpus(*_args, **_kwargs):
    raise AssertionError('point revision materialized the full project')


def test_existing_entity_text_revision_avoids_corpus_reads_and_preserves_provenance(
        tmp_path, monkeypatch):
    repository, service, project, _ = _system(tmp_path)
    source = service.write(project, [{
        'id': 'source', 'kind': 'document', 'text': 'original source',
    }])[0]
    records = service.write(project, [
        {'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'before',
         'source_id': source['id'], 'metadata': {'audit_tag': 'unchanged'}},
        {'id': 'other', 'kind': 'entity', 'type': 'Person', 'text': 'other'},
        {'id': 'edge', 'kind': 'relation', 'type': 'knows', 'text': 'edge',
         'subject_id': 'person', 'object_id': 'other'},
    ])
    before = repository.formal_write_snapshot(project)
    original = records[0]
    original_input = _changed(original, text='after')
    frozen_input = copy.deepcopy(original_input)
    with monkeypatch.context() as guard:
        guard.setattr(repository, '_formal_write_snapshot_data', _no_corpus)
        guard.setattr(repository, 'current_records', _no_corpus)
        guard.setattr(service.encoder, 'encode', _no_corpus)
        changed = service.write(
            project, [original_input], revision=True,
            expected_version=original['version'], defer_milvus_sync=True)[0]

    assert original_input == frozen_input
    assert changed['text'] == 'after'
    assert changed['version'] == original['version'] + 1
    assert changed['recorded_at'] > original['recorded_at']
    assert _changed(changed, text='before') == _changed(original)
    assert repository.get_record(project, 'edge')['version_id'] == records[2]['version_id']
    assert repository.history(project, 'person')[0]['version_id'] == original['version_id']

    after = repository.formal_write_snapshot(project)
    assert after['fact_keys'] == before['fact_keys']
    old_assertions = {row['id']: row for row in before['assertions']}
    new_assertions = {row['id']: row for row in after['assertions']}
    assert {key: new_assertions[key] for key in old_assertions} == old_assertions
    assert all(row in after['record_version_assertions']
               for row in before['record_version_assertions'])
    created = [row for row in after['assertions'] if row['id'] not in old_assertions]
    assert len(created) == 1
    assertion = created[0]
    assert assertion['status'] == 'accepted'
    assert assertion['canonical_record_id'] == 'person'
    assert assertion['quote'] == 'after'
    events = [row for row in after['assertion_events']
              if row['assertion_id'] == assertion['id'] and row['to_status'] == 'accepted']
    assert len(events) == 1
    mappings = [row for row in after['record_version_assertions']
                if row['assertion_id'] == assertion['id']]
    assert len(mappings) == 1
    assert mappings[0]['record_version_id'] == changed['version_id']
    assert mappings[0]['assertion_event_id'] == events[0]['id']


def test_stale_text_revision_rejects_before_corpus_read_or_time_reservation(
        tmp_path, monkeypatch):
    repository, service, project, _ = _system(tmp_path)
    original = service.write(project, [{
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'before',
    }])[0]
    with monkeypatch.context() as guard:
        guard.setattr(repository, '_formal_write_snapshot_data', _no_corpus)
        guard.setattr(repository, '_reserve_record_operation', _no_corpus)
        with pytest.raises(ValueError, match='版本冲突'):
            service.write(project, [_changed(original, text='stale')],
                          revision=True, expected_version=0, defer_milvus_sync=True)
    assert repository.get_record(project, 'person')['version_id'] == original['version_id']


def test_label_shacl_revision_keeps_full_validation(tmp_path, monkeypatch):
    turtle = ONTOLOGY + (
        ' @prefix sh: <http://www.w3.org/ns/shacl#> . '
        'ex:Labels a sh:NodeShape; sh:targetClass ex:Person; '
        'sh:property [sh:path rdfs:label; sh:minLength 3] .')
    repository, service, project, _ = _system(tmp_path, turtle)
    original = service.write(project, [{
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'before',
    }])[0]
    reads = []
    original_snapshot = repository._formal_write_snapshot_data

    def snapshot(project_id):
        reads.append(project_id)
        return original_snapshot(project_id)

    monkeypatch.setattr(repository, '_formal_write_snapshot_data', snapshot)
    with pytest.raises(OntologyValidationError):
        service.write(project, [_changed(original, text='x')], revision=True,
                      expected_version=original['version'], defer_milvus_sync=True)
    assert reads
    assert repository.get_record(project, 'person')['version_id'] == original['version_id']


def test_incident_older_ontology_keeps_its_label_constraints(tmp_path):
    turtle = ONTOLOGY + (
        ' @prefix sh: <http://www.w3.org/ns/shacl#> . '
        'ex:Labels a sh:NodeShape; sh:targetClass ex:Person; '
        'sh:property [sh:path rdfs:label; sh:minLength 3] .')
    repository, service, project, old_ontology = _system(tmp_path, turtle)
    latest = seed_ontology(repository, project, ONTOLOGY, Ontology(ONTOLOGY).summary())
    original = service.write(project, [
        {'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'before',
         'ontology_id': latest['id']},
        {'id': 'other', 'kind': 'entity', 'type': 'Person', 'text': 'other',
         'ontology_id': old_ontology['id']},
        {'id': 'edge', 'kind': 'relation', 'type': 'knows', 'text': 'edge',
         'subject_id': 'person', 'object_id': 'other', 'ontology_id': old_ontology['id']},
    ])[0]
    with pytest.raises(OntologyValidationError):
        service.write(project, [_changed(original, text='x')], revision=True,
                      expected_version=original['version'], defer_milvus_sync=True)
    assert repository.get_record(project, 'person')['version_id'] == original['version_id']


def test_unrelated_bad_endpoint_still_rejects_text_revision(tmp_path, monkeypatch):
    repository, service, project, ontology = _system(tmp_path)
    original = service.write(project, [{
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'before',
    }])[0]
    repository.put_record(project, {
        'id': 'unrelated-broken-edge', 'kind': 'relation', 'type': 'knows',
        'text': 'broken', 'ontology_id': ontology['id'],
        'subject_id': 'unrelated-missing-subject', 'object_id': 'unrelated-missing-object',
    })
    reads = []
    original_snapshot = repository._formal_write_snapshot_data

    def snapshot(project_id):
        reads.append(project_id)
        return original_snapshot(project_id)

    monkeypatch.setattr(repository, '_formal_write_snapshot_data', snapshot)
    with pytest.raises(ValueError, match='缺少实体端点'):
        service.write(project, [_changed(original, text='after')], revision=True,
                      expected_version=original['version'], defer_milvus_sync=True)
    assert reads, 'uncertain integrity must return to the complete validation path'
    assert repository.get_record(project, 'person')['version_id'] == original['version_id']


def test_nonuniversal_domain_constraint_keeps_full_validation(tmp_path, monkeypatch):
    turtle = ONTOLOGY + (' ex:Other a owl:Class . '
                         'ex:knows rdfs:domain ex:Person .')
    repository, service, project, ontology = _system(tmp_path, turtle)
    original = service.write(project, [{
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'before',
    }])[0]
    repository.put_record(project, {
        'id': 'wrong-kind', 'kind': 'entity', 'type': 'Other', 'text': 'other',
        'ontology_id': ontology['id'],
    })
    repository.put_record(project, {
        'id': 'bad-domain', 'kind': 'relation', 'type': 'knows', 'text': 'bad',
        'subject_id': 'wrong-kind', 'object_id': 'person', 'ontology_id': ontology['id'],
    })
    with pytest.raises(OntologyValidationError):
        service.write(project, [_changed(original, text='after')], revision=True,
                      expected_version=original['version'], defer_milvus_sync=True)
    assert repository.get_record(project, 'person')['version_id'] == original['version_id']


@pytest.mark.parametrize('mutation', ['assertion_absence', 'incident_absence'])
def test_text_revision_protects_governance_and_incident_absence_until_commit(
        tmp_path, monkeypatch, mutation):
    repository, service, project, ontology = _system(tmp_path)
    original = service.write(project, [{
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'before',
    }])[0]
    contender = Repository(tmp_path / 'scoped-text.sqlite')
    candidate = _changed(original, text='after')
    assertion_id = manual_assertion_id(
        repository.storage_namespace, 'manual_write', candidate, original['version'])
    completed = Event()
    outcomes = []

    def mutate():
        try:
            contender._db.execute("SET lock_timeout = '200ms'")
            if mutation == 'assertion_absence':
                contender.create_assertion(project, {
                    'id': assertion_id, 'kind': 'entity', 'payload': {'conflicting': True},
                })
            else:
                contender.put_record(project, {
                    'id': 'new-invalid-incident', 'kind': 'relation', 'type': 'knows',
                    'text': 'late', 'subject_id': 'person', 'object_id': 'missing',
                    'ontology_id': ontology['id'],
                })
        except Exception as exc:
            outcomes.append(exc)
        finally:
            completed.set()

    prepare = FormalFactWriter.prepare
    threads = []

    def competing_prepare(writer, *args, **kwargs):
        thread = Thread(target=mutate)
        threads.append(thread)
        thread.start()
        assert completed.wait(5), 'concurrent mutation did not settle within its lock timeout'
        assert len(outcomes) == 1 and isinstance(outcomes[0], LockNotAvailable), outcomes
        return prepare(writer, *args, **kwargs)

    monkeypatch.setattr(FormalFactWriter, 'prepare', competing_prepare)
    with monkeypatch.context() as guard:
        guard.setattr(repository, '_formal_write_snapshot_data', _no_corpus)
        changed = service.write(project, [candidate], revision=True,
                                expected_version=original['version'], defer_milvus_sync=True)[0]
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert changed['version'] == original['version'] + 1
    assert repository.get_record(project, 'person')['text'] == 'after'


def test_failed_text_revision_rolls_back_record_assertion_and_mapping(tmp_path, monkeypatch):
    repository, service, project, _ = _system(tmp_path)
    original = service.write(project, [{
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'before',
    }])[0]
    before = repository.formal_write_snapshot(project)

    def fail_mapping(*_args, **_kwargs):
        raise RuntimeError('injected mapping failure')

    with monkeypatch.context() as guard:
        guard.setattr(repository, '_formal_write_snapshot_data', _no_corpus)
        guard.setattr(repository, '_insert_record_version_assertion', fail_mapping)
        with pytest.raises(RuntimeError, match='injected mapping failure'):
            service.write(project, [_changed(original, text='after')], revision=True,
                          expected_version=original['version'], defer_milvus_sync=True)
    assert repository.formal_write_snapshot(project) == before
    assert repository.get_record(project, 'person')['version_id'] == original['version_id']


def test_operation_time_uses_typed_max_without_loading_history(tmp_path, monkeypatch):
    repository = Repository(tmp_path / 'typed-reservation.sqlite')
    project = repository.create_project('typed maximum')['id']
    repository.put_record(project, {'id': 'future', 'kind': 'document', 'text': 'one'},
                          recorded_at='2040-01-01T08:00:00+08:00')
    reserved = repository._reserve_record_operation(project, '2050-01-01T00:00:00Z')
    other = repository.create_project('independent')['id']
    repository.put_record(other, {'id': 'other', 'kind': 'document', 'text': 'other'},
                          recorded_at='2099-01-01T00:00:00Z')
    execute = repository._db.execute
    statements = []

    def bounded_execute(statement, parameters=()):
        statements.append(statement)
        assert not re.search(r'SELECT\s+recorded_at\s+FROM\s+record_versions',
                             statement, re.IGNORECASE), 'reservation loaded all historical timestamps'
        return execute(statement, parameters)

    monkeypatch.setattr(repository._db, 'execute', bounded_execute)
    following = repository._reserve_record_operation(project, '2000-01-01T00:00:00Z')
    assert following.recorded_at == '2050-01-01T00:00:00.000001Z'
    assert following.recorded_at > reserved.recorded_at
    assert any(re.search(r'MAX\s*\(\s*recorded_at\s*\)', statement, re.IGNORECASE)
               and 'record_versions' in statement for statement in statements)


@pytest.mark.parametrize(('changes', 'expected'), [
    ({}, False),
    ({'subject_id': None}, True),
    ({'object_id': None}, True),
    ({'subject_id': 'missing'}, True),
    ({'object_id': 'missing'}, True),
    ({'left_kind': 'document'}, True),
    ({'right_kind': 'document'}, True),
    ({'left_metadata': {'_deleted': True}}, True),
    ({'right_metadata': {'_deleted': True}}, True),
    ({'left_metadata': {'_audit': True}}, False),
    ({'right_metadata': {'_audit': True}}, False),
    ({'left_time': '2026-10-21T00:00:00.000001Z'}, True),
    ({'right_time': '2026-10-21T00:00:00.000001Z'}, True),
    ({'fact_time': '2026-10-21T00:00:00.000001Z'}, True),
    ({'subject_id': 'person', 'ontology_id': None}, True),
    ({'ontology_id': None}, False),
    ({'kind': 'attribute'}, True),
    ({'kind': 'attribute', 'ontology_id': None}, False),
    ({'subject_id': 'missing', 'metadata': {'_deleted': True}}, False),
    ({'subject_id': 'missing', 'metadata': {'_audit': True}}, True),
], ids=[
    'valid', 'null-subject', 'null-object', 'missing-subject', 'missing-object',
    'nonentity-subject', 'nonentity-object', 'deleted-subject', 'deleted-object',
    'audit-subject', 'audit-object', 'microsecond-subject', 'microsecond-object',
    'microsecond-fact', 'cross-ontology-incident', 'unrelated-ontology',
    'affected-attribute', 'unaffected-attribute', 'deleted-fact', 'audit-fact',
])
def test_text_revision_gate_semantics(tmp_path, changes, expected):
    repository, _, project, ontology = _system(tmp_path)
    target = repository.put_record(project, {
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'target',
        'ontology_id': ontology['id'],
    })
    for side in ('left', 'right'):
        repository.put_record(project, {
            'id': side, 'kind': changes.get(f'{side}_kind', 'entity'),
            'type': 'Person', 'text': side, 'ontology_id': ontology['id'],
            'metadata': changes.get(f'{side}_metadata', {}),
            'valid_from': changes.get(f'{side}_time'),
        })
    fact = {
        'id': 'fact', 'kind': changes.get('kind', 'relation'), 'text': 'fact',
        'type': 'knows', 'subject_id': changes.get('subject_id', 'left'),
        'object_id': changes.get('object_id', 'right'),
        'ontology_id': changes.get('ontology_id', ontology['id']),
        'metadata': changes.get('metadata', {}),
        'valid_from': changes.get('fact_time'),
    }
    if fact['kind'] == 'attribute':
        fact.update(type='age', value=1, datatype='http://www.w3.org/2001/XMLSchema#integer')
        fact.pop('object_id')
    # Missing endpoints are represented by omitted payload keys, not rejected by
    # the storage-only writer; the service gate must still find them globally.
    fact = {key: value for key, value in fact.items() if value is not None}
    if 'ontology_id' in changes and changes['ontology_id'] is None:
        fact.pop('ontology_id', None)
    repository.put_record(project, fact)
    with repository.entity_text_revision(project, target['id'], target['version']) as session:
        assert session.requires_full_validation(ontology['id']) is expected


@pytest.mark.parametrize('value', ['', 0, False, [], [None], {}, [1],
                                  '2026-10-21T00:00:00.000001Z'])
@pytest.mark.parametrize('field', ['valid_from', 'valid_until'])
def test_gate_time_presence_uses_payload_and_retains_legacy_json_values(
        tmp_path, value, field):
    repository, _, project, ontology = _system(tmp_path)
    target = repository.put_record(project, {
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': 'target',
        'ontology_id': ontology['id'],
    })
    fact = repository.put_record(project, {
        'id': 'fact', 'kind': 'relation', 'type': 'knows', 'text': 'fact',
        'subject_id': 'person', 'object_id': 'person', 'ontology_id': ontology['id'],
    })
    payload = {key: item for key, item in fact.items() if key in RecordWrite.model_fields}
    payload.pop('valid_from', None)
    payload.pop('valid_until', None)
    payload[field] = value
    # Model a legacy payload without changing the native time columns. The gate
    # must use authoritative JSON presence, including false/empty containers.
    with repository.unit_of_work():
        repository._db.execute(
            '''UPDATE record_versions SET payload=?
               WHERE project_id=? AND id=? AND superseded_at IS NULL''',
            (json.dumps(payload), project, fact['id']))
    with repository.entity_text_revision(project, target['id'], target['version']) as session:
        assert session.requires_full_validation(ontology['id']) is True
