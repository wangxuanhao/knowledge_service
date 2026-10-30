"""Shared time selection applies to search, QA and bounded reference fallback."""
import json

import pytest

from test_rule_document_oag import AS_OF, qa, request, source  # noqa: F401


@pytest.mark.parametrize('include_unknown', [False, True])
def test_search_time_scope_keeps_half_open_boundaries_and_unknown_choice(qa, include_unknown):
    service, repo, project = qa
    source(repo, project, 'starts-now', valid_from=AS_OF)
    source(repo, project, 'ends-now', valid_from='2020-01-01', valid_until=AS_OF)
    source(repo, project, 'future', valid_from='2030-01-01')
    source(repo, project, 'undated')
    body = request(include_unknown=include_unknown, kinds=['chunk'])
    hits = service.search(project, body)['hits']
    expected = {'starts-now', 'undated'} if include_unknown else {'starts-now'}
    assert {row['id'] for row in hits} == expected
    all_times = service.search(project, {**body, 'valid_at': None})['hits']
    assert {row['id'] for row in all_times} == {'starts-now', 'ends-now', 'future', 'undated'}


@pytest.mark.parametrize('include_references', [False, True])
def test_qa_strict_time_does_not_reintroduce_unknown_originals(qa, include_references):
    service, repo, project = qa
    source(repo, project, 'confirmed', valid_from='2020-01-01')
    source(repo, project, 'undated')
    source(repo, project, 'expired', valid_from='2020-01-01', valid_until=AS_OF)
    source(repo, project, 'future', valid_from='2030-01-01')
    body = request(include_unknown=False, include_references=include_references)
    result = service.answer(project, body)
    assert {row['id'] for row in result['evidence']} == {'confirmed'}
    assert result['uncertain_references'] == []
    assert '退款条款 undated' not in json.dumps(result, ensure_ascii=False)
    assert bool(result['historical_references']) is include_references
    assert bool(result['future_references']) is include_references
    permissive = service.answer(project, {**body, 'include_unknown': True})
    assert {row['id'] for row in permissive['uncertain_references']} == {'undated'}


@pytest.mark.parametrize('source_dates', [{}, {'valid_until': AS_OF}, {
    'valid_from': '2020-01-01', 'metadata': {'effective_time_source': 'published_at'}}])
@pytest.mark.parametrize('chunk_dates', [
    {'valid_from': '2020-01-01'},
    {'valid_from': '2020-01-01', 'valid_until': AS_OF},
    {'valid_from': '2030-01-01'},
])
def test_qa_strict_time_checks_unknown_source_behind_a_dated_chunk(qa, source_dates, chunk_dates):
    service, repo, project = qa
    chunk = source(repo, project, 'unverified-source', **source_dates)
    writable = {key: value for key, value in chunk.items() if key not in {
        'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}}
    writable.update(valid_from=chunk_dates['valid_from'], valid_until=chunk_dates.get('valid_until'),
                    metadata={**writable['metadata'], 'effective_time_source': 'explicit'})
    repo.put_record(project, writable)
    result = service.answer(project, request(include_unknown=False))
    assert result['evidence'] == []
    assert result['uncertain_references'] == []
    excluded = '退款条款 unverified-source'
    assert excluded not in json.dumps(result, ensure_ascii=False), [
        key for key, value in result.items() if excluded in json.dumps(value, ensure_ascii=False)]


def test_unknown_flag_without_explicit_business_date_does_not_create_a_time_filter(qa):
    service, repo, project = qa
    source(repo, project, 'undated')
    result = service.answer(project, request(valid_at=None, include_unknown=False))
    assert result['uncertain_references'][0]['id'] == 'undated'
    assert result['evidence'] == []


def test_strict_time_qualification_rejects_unknown_source_after_source_governance():
    from test_qa_record_and_source_filter_scope import NOW, _fixture
    from knowledge_service.services.answer_validity import classify

    service, _fact, chunk, doc = _fixture()
    doc['valid_from'] = None
    body = {'valid_at': NOW, 'known_at': NOW, 'include_unknown': False}
    assert classify(service, 'p', chunk, body) is None
    assert classify(service, 'p', chunk, {**body, 'include_unknown': True})['validity_status'] == 'unknown'
    assert classify(service, 'p', chunk, {**body, '_explicit_valid_at': False})['validity_status'] == 'unknown'


@pytest.mark.parametrize('target', ['record', 'source'])
def test_strict_time_rejects_missing_start_even_when_expiry_is_known(target):
    from test_qa_record_and_source_filter_scope import NOW, _fixture
    from knowledge_service.services.answer_validity import classify

    service, _fact, chunk, doc = _fixture()
    (chunk if target == 'record' else doc).update(valid_from=None, valid_until='2024-01-01')
    body = {'valid_at': NOW, 'known_at': NOW, 'include_unknown': False}
    assert classify(service, 'p', chunk, body) is None
    assert classify(service, 'p', chunk, {**body, 'include_unknown': True}) is not None


@pytest.mark.parametrize('dates', [
    {'valid_from': '2020-01-01', 'valid_until': '2024-01-01'},
    {'valid_from': '2030-01-01'},
])
def test_strict_reference_only_freezes_known_time_source_occurrences(dates):
    from test_qa_record_and_source_filter_scope import NOW, _fixture
    from knowledge_service.services.answer_validity import classify

    service, fact, chunk, doc = _fixture()
    unknown_doc = {**doc, 'id': 'unknown-doc', 'version_id': 'unknown-doc-v1', 'valid_from': None}
    unknown_chunk = {**chunk, 'id': 'unknown-chunk', 'version_id': 'unknown-chunk-v1',
                     'source_id': unknown_doc['id'],
                     'metadata': {**chunk['metadata'], 'source_version_id': unknown_doc['version_id']}}
    rows = {row['id']: row for row in (doc, chunk, unknown_doc, unknown_chunk)}
    docs = {row['version_id']: row for row in (doc, unknown_doc)}
    assertions = {row['id']: {'id': row['id'], 'created_at': '2020-01-01',
                  'document_id': row['source_id'], 'document_version_id': row['metadata']['source_version_id'],
                  'chunk_id': row['id']} for row in (chunk, unknown_chunk)}
    repo = service.repository
    repo.get_record_version = lambda _p, version: docs[version]
    repo.history = lambda _p, record_id: [rows[record_id]]
    repo.list_record_version_assertions = lambda *_a, **_kw: [
        {'assertion_id': name, 'assertion_event_id': 'accepted'} for name in assertions]
    repo.get_assertion = lambda _p, name: assertions[name]
    repo.list_assertion_events = lambda *_: [{'id': 'accepted', 'created_at': '2020-01-01'}]
    item = classify(service, 'p', {**fact, **dates}, {
        'valid_at': NOW, 'known_at': NOW, 'include_unknown': False})
    assert item is not None
    assert item['_allowed_source_version_ids'] == [doc['version_id']]
    assert all(source['validity_status'] != 'unknown' for source in item['source_eligibility'])
