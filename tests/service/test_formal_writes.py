from decimal import Decimal

import pytest
from pydantic import ValidationError

from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.models import RecordWrite
from knowledge_service.repository.connection import IntegrityError
from knowledge_service.services.formal_writes import FormalFactWriter
from knowledge_service.services.ontology import Ontology
from knowledge_service.repository import Repository
from knowledge_service.services.service import KnowledgeService


def _system(tmp_path):
    repo = Repository(tmp_path / 'formal.sqlite')
    service = KnowledgeService(repo, HashingEncoder())
    project_id = repo.create_project('正式图谱')['id']
    ttl = ('@prefix ex: <http://ex/> . '
           '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
           '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . '
           '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> . '
           'ex:Person a owl:Class . ex:knows a owl:ObjectProperty . '
           'ex:age a owl:DatatypeProperty; rdfs:domain ex:Person; rdfs:range xsd:integer .')
    repo.save_ontology(project_id, ttl, Ontology(ttl).summary())
    return repo, service, project_id


def test_new_facts_reject_deprecated_terms_but_old_version_records_remain_valid(tmp_path):
    repo, service, project_id = _system(tmp_path)
    old = repo.get_ontology(project_id)
    historical = service.write(project_id, [{
        'id': 'historical-person', 'kind': 'entity', 'type': 'Person',
        'text': '历史人物', 'ontology_id': old['id'],
    }])
    deprecated_ttl = old['turtle'] + ' ex:Person owl:deprecated true .'
    current = repo.save_ontology(project_id, deprecated_ttl, Ontology(deprecated_ttl).summary())

    with pytest.raises(ValueError, match='停用'):
        service.write(project_id, [{
            'id': 'new-person', 'kind': 'entity', 'type': 'Person', 'text': '新人物',
            'ontology_id': current['id'],
        }])

    stored = repo.get_record(project_id, historical[0]['id'])
    assert stored['ontology_id'] == old['id']
    assert Ontology(old['turtle']).validate([stored], shacl=False)['conforms'] is True


@pytest.mark.parametrize(('kind', 'record', 'deprecated_term'), [
    ('entity', {
        'id': 'fact', 'kind': 'entity', 'type': 'Person', 'text': 'v1'}, 'Person'),
    ('relation', {
        'id': 'fact', 'kind': 'relation', 'type': 'knows', 'text': 'v1',
        'subject_id': 'a', 'object_id': 'b'}, 'knows'),
    ('attribute', {
        'id': 'fact', 'kind': 'attribute', 'type': 'age', 'text': 'v1',
        'subject_id': 'a', 'value': 20,
        'datatype': 'http://www.w3.org/2001/XMLSchema#integer'}, 'age'),
])
def test_revision_rejects_term_deprecated_after_v1(
        tmp_path, kind, record, deprecated_term):
    repo, service, project_id = _system(tmp_path)
    prerequisites = []
    if kind in {'relation', 'attribute'}:
        prerequisites.append({
            'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': 'A'})
    if kind == 'relation':
        prerequisites.append({
            'id': 'b', 'kind': 'entity', 'type': 'Person', 'text': 'B'})
    service.write(project_id, [*prerequisites, record])
    current = repo.get_ontology(project_id)
    deprecated = current['turtle'] + (
        f' ex:{deprecated_term} owl:deprecated true .')
    repo.save_ontology(
        project_id, deprecated, Ontology(deprecated).summary())

    revision = {**record, 'text': 'v2'}
    with pytest.raises(ValueError, match='停用'):
        service.write(
            project_id, [revision], expected_versions={'fact': 1})
    assert repo.get_record(project_id, 'fact')['version'] == 1


@pytest.mark.parametrize(('value', 'datatype'), [
    (b'20', 'http://www.w3.org/2001/XMLSchema#string'),
    (Decimal('20.5'), 'http://www.w3.org/2001/XMLSchema#double'),
])
def test_attribute_value_rejects_non_native_scalars_before_coercion(value, datatype):
    with pytest.raises(ValidationError):
        RecordWrite.model_validate({
            'kind': 'attribute', 'type': 'age', 'text': '年龄', 'subject_id': 'person',
            'value': value, 'datatype': datatype,
        })


def test_attribute_value_preserves_bool_int_and_finite_float_types():
    base = {'kind': 'attribute', 'type': 'age', 'text': '年龄', 'subject_id': 'person'}
    cases = [
        (True, 'http://www.w3.org/2001/XMLSchema#boolean', bool),
        (20, 'http://www.w3.org/2001/XMLSchema#integer', int),
        (20.5, 'http://www.w3.org/2001/XMLSchema#double', float),
    ]
    for value, datatype, expected_type in cases:
        record = RecordWrite.model_validate({**base, 'value': value, 'datatype': datatype})
        assert type(record.value) is expected_type


def test_entity_property_becomes_supported_attribute_and_same_fact_reuses_it(tmp_path):
    repo, service, project_id = _system(tmp_path)
    first_source = repo.put_record(
        project_id, {'id': 'doc-1', 'kind': 'document', 'text': '张三今年 20 岁'})
    second_source = repo.put_record(
        project_id, {'id': 'doc-2', 'kind': 'document', 'text': '年龄资料也记载为 20'})

    created = service.write(project_id, [{
        'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': '张三',
        'properties': {'age': 20}, 'source_id': 'doc-1',
        'metadata': {'chunk_id': 'chunk-1'},
    }])
    attribute = next(row for row in created if row['kind'] == 'attribute')
    assert next(row for row in created if row['kind'] == 'entity')['properties'] == {}
    assert attribute['value'] == 20
    assert attribute['datatype'] == 'http://www.w3.org/2001/XMLSchema#integer'
    assert attribute['subject_id'] == 'person'
    assert 'source_id' not in attribute
    assert 'chunk_id' not in attribute['metadata']

    replay = service.write(project_id, [{
        'id': 'duplicate-age', 'kind': 'attribute', 'type': 'age',
        'text': '年龄资料也记载为 20', 'subject_id': 'person', 'value': 20,
        'datatype': 'http://www.w3.org/2001/XMLSchema#integer', 'source_id': 'doc-2',
        'metadata': {'chunk_id': 'chunk-2'},
    }])

    assert replay[0]['id'] == attribute['id']
    current = [row for row in repo.current_records(project_id) if row['kind'] == 'attribute']
    assert [row['id'] for row in current] == [attribute['id']]
    support = repo.list_assertions(
        project_id, status='accepted', canonical_record_id=attribute['id'])
    assert len(support) == 2
    assert {row['document_version_id'] for row in support} == {
        first_source['version_id'], second_source['version_id']}
    assert repo.list_fact_keys(project_id)[0]['canonical_record_id'] == attribute['id']


def test_attribute_retraction_counts_support_for_current_version_only(tmp_path):
    repo, service, project_id = _system(tmp_path)
    service.write(project_id, [
        {'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': '张三'}])
    source_20 = repo.put_record(
        project_id, {'id': 'doc-20', 'kind': 'document', 'text': '年龄 20'})
    source_21 = repo.put_record(
        project_id, {'id': 'doc-21', 'kind': 'document', 'text': '年龄 21'})
    datatype = 'http://www.w3.org/2001/XMLSchema#integer'
    first = service.write(project_id, [{
        'id': 'age-fact', 'kind': 'attribute', 'type': 'age', 'text': '年龄',
        'subject_id': 'person', 'value': 20, 'datatype': datatype,
        'source_id': 'doc-20', 'metadata': {'chunk_id': 'age'},
    }])[0]
    service.write(project_id, [{
        'id': 'age-fact', 'kind': 'attribute', 'type': 'age', 'text': '年龄',
        'subject_id': 'person', 'value': 21, 'datatype': datatype,
        'source_id': 'doc-21', 'metadata': {'chunk_id': 'age'},
    }], expected_versions={'age-fact': first['version']})

    service.write(project_id, [{
        'id': 'doc-21', 'kind': 'document', 'text': source_21['text'],
        'metadata': {'_deleted': True},
    }], expected_versions={'doc-21': source_21['version']}, operation='retract_source')

    current = repo.get_record(project_id, 'age-fact')
    assert current['value'] == 21
    assert current['metadata']['_deleted'] is True
    old_support = repo.list_assertions(
        project_id, status='accepted', canonical_record_id='age-fact')
    assert [row['document_version_id'] for row in old_support] == [source_20['version_id']]


def test_attribute_retraction_keeps_multi_supported_fact_until_last_source(tmp_path):
    repo, service, project_id = _system(tmp_path)
    service.write(project_id, [
        {'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': '张三'}])
    documents = [repo.put_record(project_id, {
        'id': f'doc-{index}', 'kind': 'document', 'text': '年龄 20'})
        for index in (1, 2)]
    datatype = 'http://www.w3.org/2001/XMLSchema#integer'
    for index, document in enumerate(documents, 1):
        service.write(project_id, [{
            'id': f'age-{index}', 'kind': 'attribute', 'type': 'age', 'text': '年龄 20',
            'subject_id': 'person', 'value': 20, 'datatype': datatype,
            'source_id': document['id'], 'metadata': {'chunk_id': f'age-{index}'},
        }])
    fact = next(row for row in repo.current_records(project_id)
                if row['kind'] == 'attribute')

    for index, document in enumerate(documents, 1):
        service.write(project_id, [{
            'id': document['id'], 'kind': 'document', 'text': document['text'],
            'metadata': {'_deleted': True},
        }], expected_versions={document['id']: document['version']},
            operation='retract_source')
        current = repo.get_record(project_id, fact['id'])
        assert bool(current.get('metadata', {}).get('_deleted')) is (index == 2)


def test_same_relation_key_reuses_immutable_fact_and_adds_support(tmp_path):
    repo, service, project_id = _system(tmp_path)
    service.write(project_id, [
        {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'b', 'kind': 'entity', 'type': 'Person', 'text': '李四'},
        {'id': 'fact-z', 'kind': 'relation', 'type': 'knows', 'text': '张三认识李四',
         'subject_id': 'a', 'object_id': 'b'},
    ])
    replay = service.write(project_id, [
        {'id': 'fact-a', 'kind': 'relation', 'type': 'knows', 'text': '另一个来源也说他们认识',
         'subject_id': 'a', 'object_id': 'b'},
    ])

    assert replay[0]['id'] == 'fact-z'
    relations = [row for row in repo.current_records(project_id) if row['kind'] == 'relation']
    assert [row['id'] for row in relations] == ['fact-z']
    support = repo.list_assertions(project_id, status='accepted', canonical_record_id='fact-z')
    assert len(support) == 2
    assert repo.list_fact_keys(project_id)[0]['canonical_record_id'] == 'fact-z'


def test_source_anchor_is_stored_on_assertion_not_canonical_relation(tmp_path):
    repo, service, project_id = _system(tmp_path)
    document = repo.put_record(project_id, {'id': 'doc', 'kind': 'document', 'text': '张三认识李四'})
    service.write(project_id, [
        {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '张三'},
        {'id': 'b', 'kind': 'entity', 'type': 'Person', 'text': '李四'},
        {'id': 'r', 'kind': 'relation', 'type': 'knows', 'text': '张三认识李四',
         'subject_id': 'a', 'object_id': 'b', 'source_id': 'doc',
         'metadata': {'source_version_id': document['version_id'], 'chunk_id': 'chunk-1',
                      'start_char': 0, 'end_char': 8}},
    ])

    relation = repo.get_record(project_id, 'r')
    assert 'source_id' not in relation
    assert 'chunk_id' not in relation['metadata']
    assertion = repo.list_assertions(project_id, canonical_record_id='r')[0]
    assert assertion['document_id'] == 'doc'
    assert assertion['document_version_id'] == document['version_id']
    assert assertion['chunk_id'] == 'chunk-1'
    assert assertion['start_char'] == 0
    assert assertion['end_char'] == 8


def test_stale_formal_write_rolls_back_record_and_assertion(tmp_path):
    repo, service, project_id = _system(tmp_path)
    first = service.write(project_id, [
        {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '张三'}])[0]

    try:
        service.write(project_id, [
            {'id': 'a', 'kind': 'entity', 'type': 'Person', 'text': '新张三'}],
            expected_versions={'a': first['version'] - 1})
    except ValueError as exc:
        assert '冲突' in str(exc)
    else:
        raise AssertionError('stale write should fail')

    assert repo.get_record(project_id, 'a')['text'] == '张三'
    assert len(repo.list_assertions(project_id, canonical_record_id='a')) == 1


def _entity(record_id, text=None):
    return {'id': record_id, 'kind': 'entity', 'type': 'Person', 'text': text or record_id}


def _assertion(assertion_id, target):
    return {'id': assertion_id, 'kind': 'entity', 'payload': {},
            'canonical_record_id': target}


def _write(repo, project_id, records=(), assertions=(), expected=None, **policy):
    return FormalFactWriter(repo).apply(
        'manual_write', project_id, assertions, expected or {},
        {'records': list(records), **policy})


def _mapping_ids(repo, project_id):
    return {(row['record_id'], row['record_version_id'], row['assertion_id'],
             row['assertion_event_id'])
            for row in repo.list_record_version_assertions(project_id)}


def test_attribute_revision_collision_reuses_target_and_tombstones_source(tmp_path):
    repo, _, project_id = _system(tmp_path)
    datatype = 'http://www.w3.org/2001/XMLSchema#integer'
    attribute = {
        'kind': 'attribute', 'type': 'age', 'text': '年龄',
        'subject_id': 'person', 'datatype': datatype,
    }
    _write(repo, project_id, [
        _entity('person'),
        {**attribute, 'id': 'a20', 'value': 20},
        {**attribute, 'id': 'a21', 'value': 21},
    ])

    result = _write(
        repo, project_id, [{**attribute, 'id': 'a20', 'value': 21}],
        expected={'a20': 1})

    source = repo.get_record(project_id, 'a20')
    target = repo.get_record(project_id, 'a21')
    assert source['version'] == 2
    assert source['value'] == 20
    assert source['metadata']['_deleted'] is True
    assert source['metadata']['merged_into_fact'] == 'a21'
    assert target['value'] == 21
    assert not target.get('metadata', {}).get('_deleted')
    assert [row['canonical_record_id'] for row in repo.list_fact_keys(project_id)] == ['a21']
    retired = repo.list_fact_keys(project_id, include_retired=True)
    assert next(row for row in retired if row['canonical_record_id'] == 'a20')['retired_at']
    assert any(row['canonical_record_id'] == 'a21' and row['payload']['id'] == 'a20'
               and row['payload']['value'] == 21 for row in result['assertion_updates'])


def test_merge_collision_source_winner_keeps_retargeted_key_active(tmp_path):
    repo, _, project_id = _system(tmp_path)
    datatype = 'http://www.w3.org/2001/XMLSchema#integer'
    attribute = {
        'kind': 'attribute', 'type': 'age', 'text': '年龄 20',
        'value': 20, 'datatype': datatype,
    }
    _write(repo, project_id, [
        _entity('keep'), _entity('drop'),
        {**attribute, 'id': 'z-fact', 'subject_id': 'keep'},
        {**attribute, 'id': 'a-fact', 'subject_id': 'drop'},
    ])

    FormalFactWriter(repo).apply('merge_rewrite', project_id, [], {'a-fact': 1}, {
        'records': [{**attribute, 'id': 'a-fact', 'subject_id': 'keep'}],
        'ledger': {'id': 'merge-asymmetric', 'redirects': {}},
    })

    assert [row['canonical_record_id'] for row in repo.list_fact_keys(project_id)] == [
        'a-fact']
    replay = _write(repo, project_id, [
        {**attribute, 'id': 'later-copy', 'subject_id': 'keep'},
    ])
    assert replay['accepted_records'][0]['id'] == 'a-fact'


def test_new_entity_and_relation_freeze_exact_saved_versions_and_acceptance_events(tmp_path):
    repo, _, project_id = _system(tmp_path)
    result = _write(repo, project_id, [
        _entity('a'), _entity('b'),
        {'id': 'r', 'kind': 'relation', 'type': 'knows', 'text': 'a knows b',
         'subject_id': 'a', 'object_id': 'b'}])
    versions = {row['id']: row['version_id'] for row in result['accepted_records']}
    expected = set()
    for assertion in result['assertion_updates']:
        event = repo.list_assertion_events(project_id, assertion['id'])[-1]
        assert event['decision_version'] == assertion['decision_version']
        assert event['to_status'] == 'accepted'
        target = assertion['canonical_record_id']
        expected.add((target, versions[target], assertion['id'], event['id']))
    assert len(expected) == 3
    assert _mapping_ids(repo, project_id) == expected


def test_record_revision_keeps_old_and_new_support_on_their_exact_versions(tmp_path):
    repo, _, project_id = _system(tmp_path)
    first = _write(repo, project_id, [_entity('a', 'old')])
    old_mappings = _mapping_ids(repo, project_id)
    second = _write(repo, project_id, [_entity('a', 'new')], expected={'a': 1})

    assert len(old_mappings) == 1
    assert old_mappings < _mapping_ids(repo, project_id)
    for result in (first, second):
        record = result['accepted_records'][0]
        assertion = result['assertion_updates'][0]
        mappings = repo.list_record_version_assertions(
            project_id, record_version_id=record['version_id'])
        assert [row['assertion_id'] for row in mappings] == [assertion['id']]


def test_duplicate_relation_support_uses_selected_existing_version(tmp_path):
    repo, _, project_id = _system(tmp_path)
    relation = {'id': 'r', 'kind': 'relation', 'type': 'knows', 'text': 'a knows b',
                'subject_id': 'a', 'object_id': 'b'}
    first = _write(repo, project_id, [relation])['accepted_records'][0]
    second = _write(repo, project_id, [{**relation, 'id': 'duplicate'}])
    assertion = second['assertion_updates'][0]
    mappings = repo.list_record_version_assertions(project_id, assertion_id=assertion['id'])

    assert len(mappings) == 1
    assert mappings[0]['record_id'] == 'r'
    assert mappings[0]['record_version_id'] == first['version_id']
    assert mappings[0]['assertion_event_id'] == repo.list_assertion_events(
        project_id, assertion['id'])[-1]['id']


def test_assertion_only_approval_selects_version_before_transition(tmp_path, monkeypatch):
    repo, _, project_id = _system(tmp_path)
    target = repo.put_record(project_id, _entity('a', 'approved version'))
    pending = repo.create_assertion(project_id, {
        'id': 'pending', 'kind': 'entity', 'payload': {}})
    transition = repo._transition_assertion

    def transition_then_revise(*args, **kwargs):
        accepted = transition(*args, **kwargs)
        repo._put(project_id, _entity('a', 'later version'), 1)
        return accepted

    monkeypatch.setattr(repo, '_transition_assertion', transition_then_revise)
    result = FormalFactWriter(repo).apply('approve_review', project_id, [], {}, {
        'records': [], 'assertion_decisions': [{
            'id': pending['id'], 'expected_version': 1, 'status': 'accepted',
            'canonical_record_id': 'a', 'reason': 'review accepted', 'actor': 'reviewer'}]})

    assert result['accepted_records'] == []
    mappings = repo.list_record_version_assertions(project_id, assertion_id='pending')
    assert len(mappings) == 1
    assert mappings[0]['record_version_id'] == target['version_id']
    assert repo.get_record(project_id, 'a')['version_id'] != target['version_id']


def test_identical_assertion_replay_is_idempotent_but_new_version_conflicts(tmp_path):
    repo, _, project_id = _system(tmp_path)
    assertion = _assertion('support', 'a')
    _write(repo, project_id, [_entity('a')], [assertion], suppress_auto_assertions=True)
    before = _mapping_ids(repo, project_id)
    events = repo.list_assertion_events(project_id)
    _write(repo, project_id, assertions=[assertion])

    assert len(before) == 1
    assert _mapping_ids(repo, project_id) == before
    assert repo.list_assertion_events(project_id) == events
    with pytest.raises(ValueError, match='冲突'):
        _write(repo, project_id, [_entity('a', 'revision')], [assertion],
               expected={'a': 1}, suppress_auto_assertions=True)
    assert repo.get_record(project_id, 'a')['version'] == 1
    assert _mapping_ids(repo, project_id) == before
    assert repo.list_assertion_events(project_id) == events


def test_previously_accepted_unmapped_assertion_cannot_guess_a_historical_version(tmp_path):
    repo, _, project_id = _system(tmp_path)
    repo.put_record(project_id, _entity('a', 'original'))
    assertion = _assertion('legacy-support', 'a')
    repo.create_assertion(project_id, {key: value for key, value in assertion.items()
                                      if key != 'canonical_record_id'})
    repo.transition_assertion(project_id, 'legacy-support', 1, 'accepted',
                              'legacy acceptance', 'reviewer', 'a')
    repo.put_record(project_id, _entity('a', 'later revision'), expected_version=1)

    with pytest.raises(ValueError, match='冲突'):
        _write(repo, project_id, assertions=[assertion])
    assert repo.list_record_version_assertions(project_id) == []
    assert len(repo.list_assertion_events(project_id, 'legacy-support')) == 2


@pytest.mark.parametrize('write_target', [False, True])
def test_merge_rebind_freezes_new_event_without_redirecting_old_mapping(tmp_path, write_target):
    repo, _, project_id = _system(tmp_path)
    _write(repo, project_id, [_entity('source'), _entity('target')])
    before = _mapping_ids(repo, project_id)
    source_assertion = repo.list_assertions(project_id, canonical_record_id='source')[0]
    result = FormalFactWriter(repo).apply('merge_rewrite', project_id, [], {}, {
        'records': [_entity('target', 'merged')] if write_target else [],
        'ledger': {'id': 'merge', 'redirects': {'source': 'target'}}})
    target = (result['accepted_records'][0] if write_target
              else repo.get_record(project_id, 'target'))
    event = repo.list_assertion_events(project_id, source_assertion['id'])[-1]

    assert event['decision_version'] == source_assertion['decision_version'] + 1
    assert event['canonical_record_id'] == 'target'
    assert _mapping_ids(repo, project_id) == before | {(
        'target', target['version_id'], source_assertion['id'], event['id'])}


def test_mapping_failure_rolls_back_records_assertions_events_and_prior_mappings(tmp_path, monkeypatch):
    repo, _, project_id = _system(tmp_path)
    insert_mapping = repo._provenance._insert_mapping
    inserted = []

    def insert_then_fail(*args, **kwargs):
        result = insert_mapping(*args, **kwargs)
        inserted.append(result)
        if len(inserted) == 2:
            raise RuntimeError('mapping failed after insert')
        return result

    monkeypatch.setattr(repo._provenance, '_insert_mapping', insert_then_fail)
    with pytest.raises(RuntimeError, match='mapping failed after insert'):
        _write(repo, project_id, [_entity('a'), _entity('b')])
    assert len(inserted) == 2
    assert repo.current_records(project_id) == []
    assert repo.list_assertions(project_id) == []
    assert repo.list_assertion_events(project_id) == []
    assert repo.list_record_version_assertions(project_id) == []


def test_wrong_assertion_event_fk_rolls_back_formal_write(tmp_path, monkeypatch):
    repo, _, project_id = _system(tmp_path)
    insert_mapping = repo._provenance._insert_mapping

    def use_wrong_event(project_id, record_id, version_id, assertion_id, event_id, created_at=None):
        return insert_mapping(project_id, record_id, version_id, assertion_id,
                              'nonexistent-event', created_at)

    monkeypatch.setattr(repo._provenance, '_insert_mapping', use_wrong_event)
    with pytest.raises(IntegrityError):
        _write(repo, project_id, [_entity('a')])
    assert repo.current_records(project_id) == []
    assert repo.list_assertions(project_id) == []
    assert repo.list_assertion_events(project_id) == []
    assert repo.list_record_version_assertions(project_id) == []
