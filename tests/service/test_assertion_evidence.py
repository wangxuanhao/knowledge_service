import hashlib

import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services import evidence as evidence_module
from knowledge_service.services.evidence import resolve_assertion_evidence
from knowledge_service.services.service import KnowledgeService


PINNED_AT = '2026-01-02T03:04:05Z'


@pytest.fixture
def evidence_repo(tmp_path):
    repo = Repository(tmp_path / 'assertion-evidence.sqlite')
    project_id = repo.create_project('assertion evidence')['id']
    yield repo, project_id
    repo.close()


def _document_and_chunk(repo, project_id, *, text='prefix Alpha suffix', chunk_id='chunk-1',
                        chunk_start=0, chunk_end=None, chunk_text=None,
                        source_id='document-1', versioned=True):
    document = repo.put_record(project_id, {
        'id': 'document-1', 'kind': 'document', 'text': text,
        'metadata': {'title': 'Pinned document'},
    }, recorded_at=PINNED_AT)
    chunk_end = len(text) if chunk_end is None else chunk_end
    metadata = {'start_char': chunk_start, 'end_char': chunk_end}
    if versioned:
        metadata['source_version_id'] = document['version_id']
    chunk = repo.put_record(project_id, {
        'id': chunk_id, 'kind': 'chunk',
        'text': text[chunk_start:chunk_end] if chunk_text is None else chunk_text,
        'source_id': source_id, 'metadata': metadata,
    }, recorded_at=PINNED_AT)
    return document, chunk


def _assertion(repo, project_id, document, chunk, *, assertion_id='assertion-1',
               kind='entity', start=None, end=None, quote=None, payload=None,
               source_hash='valid'):
    if start is None:
        start = chunk['metadata']['start_char']
    if end is None:
        end = chunk['metadata']['end_char']
    if quote is None:
        quote = document['text'][start:end]
    if source_hash == 'valid':
        source_hash = hashlib.sha256(document['text'].encode('utf-8')).hexdigest()
    return repo.create_assertion(project_id, {
        'id': assertion_id, 'kind': kind,
        'document_id': document['id'],
        'document_version_id': document['version_id'],
        'chunk_id': chunk['id'], 'source_hash': source_hash,
        'start_char': start, 'end_char': end, 'quote': quote,
        'payload': payload or {}, 'actor': 'test',
    })


def _warning_codes(result):
    return [warning['code'] for warning in result['integrity']['warnings']]


def test_exact_assertion_returns_complete_chunk_and_exact_highlight(evidence_repo):
    repo, project_id = evidence_repo
    document, chunk = _document_and_chunk(repo, project_id)
    start = document['text'].index('Alpha')
    assertion = _assertion(
        repo, project_id, document, chunk, start=start, end=start + len('Alpha'),
        quote='Alpha', payload={'text': 'Alpha', 'evidence': 'Alpha',
                                'evidence_status': 'exact'})

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['assertion_id'] == assertion['id']
    assert result['status'] == 'pending'
    assert result['kind'] == 'entity'
    assert result['document'] == {
        'id': document['id'], 'version': document['version'],
        'version_id': document['version_id'], 'title': 'Pinned document'}
    assert result['chunk'] == {
        'id': chunk['id'], 'start_char': 0, 'end_char': len(document['text']),
        'text': document['text']}
    assert result['location'] == {
        'mode': 'exact', 'start_char': start, 'end_char': start + len('Alpha'),
        'before': 'prefix ', 'highlight': 'Alpha', 'after': ' suffix'}
    assert result['integrity'] == {
        'complete': True, 'source_hash_status': 'matched', 'warnings': []}


def test_legacy_entity_after_character_500_is_recovered_inside_chunk(evidence_repo):
    repo, project_id = evidence_repo
    text = 'x' * 520 + 'Needle' + 'z' * 40
    document, chunk = _document_and_chunk(
        repo, project_id, text=text, chunk_start=500, chunk_end=len(text))
    assertion = _assertion(
        repo, project_id, document, chunk, quote=chunk['text'],
        payload={'text': 'Needle'})

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['location']['mode'] == 'recovered_in_chunk'
    assert result['location']['start_char'] == 520
    assert result['location']['highlight'] == 'Needle'
    assert result['location']['before'] == 'x' * 20


def test_duplicate_legacy_entity_degrades_to_verified_chunk(evidence_repo):
    repo, project_id = evidence_repo
    text = 'Alpha then Alpha'
    document, chunk = _document_and_chunk(repo, project_id, text=text)
    assertion = _assertion(
        repo, project_id, document, chunk, quote=chunk['text'],
        payload={'text': 'Alpha'})

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['location'] == {
        'mode': 'chunk', 'start_char': 0, 'end_char': len(text),
        'before': '', 'highlight': text, 'after': ''}
    assert result['integrity']['complete'] is True


def test_verified_modern_whole_chunk_evidence_remains_exact(evidence_repo):
    repo, project_id = evidence_repo
    document, chunk = _document_and_chunk(repo, project_id, text='Alpha')
    assertion = _assertion(
        repo, project_id, document, chunk, quote=chunk['text'],
        payload={'text': 'Alpha', 'metadata': {
            'evidence': 'Alpha', 'evidence_status': 'exact'}})

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['location']['mode'] == 'exact'
    assert result['location']['highlight'] == 'Alpha'


def test_legacy_relation_recovers_unique_minimal_subject_object_span(evidence_repo):
    repo, project_id = evidence_repo
    text = 'Acme signed with Beta yesterday.'
    document, chunk = _document_and_chunk(repo, project_id, text=text)
    assertion = _assertion(
        repo, project_id, document, chunk, kind='relation', quote=chunk['text'],
        payload={'subject': 'Acme', 'object': 'Beta'})

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['location']['mode'] == 'recovered_in_chunk'
    assert result['location']['highlight'] == 'Acme signed with Beta'
    assert result['location']['start_char'] == 0
    assert result['location']['end_char'] == len('Acme signed with Beta')


def test_legacy_attribute_prefers_attribute_evidence(evidence_repo):
    repo, project_id = evidence_repo
    text = 'Acme has 42 employees; 42 is the audited total.'
    document, chunk = _document_and_chunk(repo, project_id, text=text)
    assertion = _assertion(
        repo, project_id, document, chunk, kind='attribute', quote=chunk['text'],
        payload={'text': '42', 'evidence': '42',
                 'attribute_evidence': '42 employees'})

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['location']['mode'] == 'recovered_in_chunk'
    assert result['location']['highlight'] == '42 employees'


def test_missing_pinned_version_is_unlocated_without_current_fallback(evidence_repo):
    repo, project_id = evidence_repo
    current, chunk = _document_and_chunk(repo, project_id)
    assertion = _assertion(repo, project_id, current, chunk)
    repo._db.execute(
        'UPDATE assertions SET document_version_id=? WHERE project_id=? AND id=?',
        ('missing-version', project_id, assertion['id']))

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['document']['version_id'] == 'missing-version'
    assert result['location']['mode'] == 'unlocated'
    assert result['integrity']['complete'] is False
    assert _warning_codes(result) == ['document_version_missing']


def test_missing_source_hash_is_unavailable_but_verified_legacy_chunk_is_complete(evidence_repo):
    repo, project_id = evidence_repo
    document, chunk = _document_and_chunk(
        repo, project_id, text='prefix Alpha suffix', versioned=False)
    assertion = _assertion(
        repo, project_id, document, chunk, quote=chunk['text'],
        payload={'text': 'Alpha'}, source_hash=None)
    repo._db.execute(
        'UPDATE record_versions SET recorded_at=? WHERE project_id=? AND version_id=?',
        ('2026-01-03T03:04:05Z', project_id, chunk['version_id']))

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['integrity']['complete'] is True
    assert result['integrity']['source_hash_status'] == 'unavailable'
    assert _warning_codes(result) == [
        'legacy_chunk_version_assumed', 'source_hash_unavailable']


def test_source_version_metadata_does_not_override_recorded_at_mismatch(evidence_repo):
    repo, project_id = evidence_repo
    document, chunk = _document_and_chunk(repo, project_id)
    assertion = _assertion(
        repo, project_id, document, chunk, start=7, end=12, quote='Alpha',
        payload={'text': 'Alpha'})
    repo._db.execute(
        'UPDATE record_versions SET recorded_at=? WHERE project_id=? AND version_id=?',
        ('2026-01-03T03:04:05Z', project_id, chunk['version_id']))

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['location']['mode'] == 'unlocated'
    assert _warning_codes(result) == ['chunk_history_mismatch']


def test_source_hash_mismatch_makes_integrity_incomplete(evidence_repo):
    repo, project_id = evidence_repo
    document, chunk = _document_and_chunk(repo, project_id)
    assertion = _assertion(
        repo, project_id, document, chunk, start=7, end=12, quote='Alpha',
        payload={'text': 'Alpha'}, source_hash='0' * 64)

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['location']['mode'] == 'exact'
    assert result['integrity']['complete'] is False
    assert result['integrity']['source_hash_status'] == 'mismatched'
    assert _warning_codes(result) == ['source_hash_mismatch']


@pytest.mark.parametrize(('damage', 'expected_codes'), [
    ('ambiguous', ['chunk_history_ambiguous']),
    ('mismatched', ['chunk_history_mismatch']),
    ('invalid_bounds', ['chunk_bounds_invalid']),
    ('text_mismatch', ['chunk_text_mismatch']),
])
def test_bad_chunk_history_or_contents_returns_stable_unlocated_warning(
        evidence_repo, damage, expected_codes):
    repo, project_id = evidence_repo
    document, chunk = _document_and_chunk(repo, project_id)
    if damage == 'ambiguous':
        second = repo.put_record(project_id, {
            'id': chunk['id'], 'kind': 'chunk', 'text': chunk['text'],
            'source_id': document['id'],
            'metadata': {**chunk['metadata']}}, expected_version=chunk['version'])
        repo._db.execute(
            'UPDATE record_versions SET recorded_at=? WHERE project_id=? AND version_id=?',
            (document['recorded_at'], project_id, second['version_id']))
    elif damage == 'mismatched':
        other = repo.put_record(project_id, {
            'id': 'document-2', 'kind': 'document', 'text': document['text'],
            'metadata': {}})
        repo.put_record(project_id, {
            'id': chunk['id'], 'kind': 'chunk', 'text': chunk['text'],
            'source_id': other['id'], 'metadata': {**chunk['metadata']}},
            expected_version=chunk['version'])
        repo._db.execute(
            'DELETE FROM record_versions WHERE project_id=? AND id=? AND version=1',
            (project_id, chunk['id']))
    elif damage == 'invalid_bounds':
        second = repo.put_record(project_id, {
            'id': chunk['id'], 'kind': 'chunk', 'text': chunk['text'],
            'source_id': document['id'],
            'metadata': {**chunk['metadata'], 'end_char': len(document['text']) + 1}},
            expected_version=chunk['version'])
        repo._db.execute(
            'DELETE FROM record_versions WHERE project_id=? AND id=? AND version=1',
            (project_id, chunk['id']))
        repo._db.execute(
            'UPDATE record_versions SET recorded_at=? WHERE project_id=? AND version_id=?',
            (document['recorded_at'], project_id, second['version_id']))
    else:
        second = repo.put_record(project_id, {
            'id': chunk['id'], 'kind': 'chunk', 'text': 'not the document slice',
            'source_id': document['id'], 'metadata': {**chunk['metadata']}},
            expected_version=chunk['version'])
        repo._db.execute(
            'DELETE FROM record_versions WHERE project_id=? AND id=? AND version=1',
            (project_id, chunk['id']))
        repo._db.execute(
            'UPDATE record_versions SET recorded_at=? WHERE project_id=? AND version_id=?',
            (document['recorded_at'], project_id, second['version_id']))
    assertion = _assertion(
        repo, project_id, document, chunk, start=7, end=12, quote='Alpha',
        payload={'text': 'Alpha'})

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['location']['mode'] == 'unlocated'
    assert result['integrity']['complete'] is False
    assert result['integrity']['source_hash_status'] == 'matched'
    assert _warning_codes(result) == expected_codes
    assert all(set(warning) == {'code', 'message'} for warning in result['integrity']['warnings'])


def test_revising_current_document_does_not_replace_assertion_pinned_source(evidence_repo):
    repo, project_id = evidence_repo
    document, chunk = _document_and_chunk(repo, project_id)
    assertion = _assertion(
        repo, project_id, document, chunk, start=7, end=12, quote='Alpha',
        payload={'text': 'Alpha'})
    repo.put_record(project_id, {
        'id': document['id'], 'kind': 'document', 'text': 'new current content',
        'metadata': {'title': 'Changed title'}}, expected_version=document['version'])

    result = resolve_assertion_evidence(repo, project_id, assertion['id'])

    assert result['document']['version_id'] == document['version_id']
    assert result['document']['title'] == 'Pinned document'
    assert result['chunk']['text'] == 'prefix Alpha suffix'


def test_assertion_evidence_route_returns_stable_404_for_missing_or_cross_project(tmp_path):
    app = create_app(tmp_path / 'route.sqlite', encoder=HashingEncoder())
    repo = app.state.service.repository
    first = repo.create_project('first')['id']
    second = repo.create_project('second')['id']
    document, chunk = _document_and_chunk(repo, first)
    assertion = _assertion(repo, first, document, chunk)

    with TestClient(app) as client:
        cross_project = client.get(
            f'/api/projects/{second}/assertions/{assertion["id"]}/evidence')
        missing = client.get(f'/api/projects/{first}/assertions/missing/evidence')

    assert cross_project.status_code == missing.status_code == 404
    assert cross_project.json() == {'detail': f'未找到：{assertion["id"]}'}
    assert missing.json() == {'detail': '未找到：missing'}


def test_formal_and_assertion_evidence_share_pinned_version_and_location_mode(evidence_repo):
    repo, project_id = evidence_repo
    service = KnowledgeService(repo, HashingEncoder())
    document, chunk = _document_and_chunk(repo, project_id)
    assertion = _assertion(
        repo, project_id, document, chunk, start=7, end=12, quote='Alpha',
        payload={'text': 'Alpha', 'evidence': 'Alpha', 'evidence_status': 'exact'})
    repo.put_record(project_id, {
        'id': 'entity-1', 'kind': 'entity', 'text': 'Alpha', 'metadata': {}})
    repo.transition_assertion(
        project_id, assertion['id'], 1, 'accepted', 'accepted for parity', 'test',
        canonical_record_id='entity-1')

    direct = resolve_assertion_evidence(repo, project_id, assertion['id'])
    formal = evidence_module.evidence(service, project_id, 'entity-1', {})['documents'][0]

    assert formal['version_id'] == direct['document']['version_id']
    assert formal['mode'] == direct['location']['mode']
    assert formal['highlight'] == direct['location']['highlight']


def test_formal_evidence_does_not_expose_text_from_mismatched_document_version(
        evidence_repo):
    repo, project_id = evidence_repo
    service = KnowledgeService(repo, HashingEncoder())
    first, chunk = _document_and_chunk(repo, project_id)
    second = repo.put_record(project_id, {
        'id': 'document-2', 'kind': 'document', 'text': 'private other document',
        'metadata': {'title': 'Other'}})
    assertion = _assertion(repo, project_id, first, chunk)
    repo._db.execute(
        'UPDATE assertions SET document_version_id=? WHERE project_id=? AND id=?',
        (second['version_id'], project_id, assertion['id']))
    repo.put_record(project_id, {
        'id': 'entity-1', 'kind': 'entity', 'text': 'Alpha', 'metadata': {}})
    repo.transition_assertion(
        project_id, assertion['id'], 1, 'accepted', 'accepted for mismatch test',
        'test', canonical_record_id='entity-1')

    formal = evidence_module.evidence(service, project_id, 'entity-1', {})['documents'][0]

    assert formal['mode'] == 'unlocated'
    assert formal['before'] == formal['highlight'] == formal['after'] == ''
    assert 'private other document' not in formal['preview']


def test_mismatched_assertion_does_not_hide_valid_assertion_for_same_version(
        evidence_repo):
    repo, project_id = evidence_repo
    service = KnowledgeService(repo, HashingEncoder())
    first, first_chunk = _document_and_chunk(repo, project_id)
    second = repo.put_record(project_id, {
        'id': 'document-2', 'kind': 'document', 'text': 'Beta',
        'metadata': {'title': 'Second'}}, recorded_at='2026-01-04T03:04:05Z')
    second_chunk = repo.put_record(project_id, {
        'id': 'chunk-2', 'kind': 'chunk', 'text': 'Beta',
        'source_id': second['id'], 'metadata': {
            'start_char': 0, 'end_char': 4,
            'source_version_id': second['version_id']}},
        recorded_at=second['recorded_at'])
    bad = _assertion(
        repo, project_id, first, first_chunk, assertion_id='a-bad')
    repo._db.execute(
        'UPDATE assertions SET document_version_id=? WHERE project_id=? AND id=?',
        (second['version_id'], project_id, bad['id']))
    good = _assertion(
        repo, project_id, second, second_chunk, assertion_id='b-good',
        start=0, end=4, quote='Beta', payload={
            'text': 'Beta', 'evidence': 'Beta', 'evidence_status': 'exact'})
    repo.put_record(project_id, {
        'id': 'entity-1', 'kind': 'entity', 'text': 'Beta', 'metadata': {}})
    for assertion in (bad, good):
        repo.transition_assertion(
            project_id, assertion['id'], 1, 'accepted', 'accepted for dedup test',
            'test', canonical_record_id='entity-1')

    documents = evidence_module.evidence(
        service, project_id, 'entity-1', {})['documents']

    valid = next(item for item in documents if item['assertion_id'] == good['id'])
    assert valid['mode'] == 'exact'
    assert valid['highlight'] == 'Beta'
