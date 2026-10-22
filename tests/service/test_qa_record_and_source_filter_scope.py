"""Record identity predicates select evidence without impersonating source IDs."""
from types import SimpleNamespace
from threading import RLock

import pytest

from knowledge_service.services.answer_validity import classify
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.utils.filters import matches_filter


NOW = '2026-10-21T08:00:00.000000Z'


def _fixture():
    common = {'valid_from': '2020-01-01T00:00:00.000000Z', 'valid_until': None,
              'recorded_at': '2020-01-01T00:00:00.000000Z', 'superseded_at': None}
    doc = {**common, 'id': 'doc', 'version_id': 'doc-v1', 'version': 1, 'kind': 'document',
           'text': '原文', 'metadata': {'region': '华东'}}
    chunk = {**common, 'id': 'chunk', 'version_id': 'chunk-v1', 'kind': 'chunk', 'text': '原文',
             'source_id': 'doc', 'metadata': {'region': '华东', 'source_version_id': 'doc-v1'}}
    fact = {**common, 'id': 'fact', 'version_id': 'fact-v1', 'kind': 'entity', 'type': 'urn:Rule',
            'text': '事实', 'metadata': {'region': '华东'}}
    assertion = {'id': 'a', 'created_at': common['recorded_at'], 'document_version_id': 'doc-v1',
                 'document_id': 'doc', 'chunk_id': 'chunk'}
    repo = SimpleNamespace(get_record_version=lambda *_: doc,
        history=lambda _p, record_id: [doc if record_id == 'doc' else chunk],
        list_record_version_assertions=lambda *_a, **_kw: [{'assertion_id': 'a', 'assertion_event_id': 'accepted'}],
        get_assertion=lambda *_: assertion,
        list_assertion_events=lambda *_: [{'id': 'accepted', 'created_at': common['recorded_at']}])
    return SimpleNamespace(repository=repo), fact, chunk, doc


@pytest.mark.parametrize('field,value,kind', [('id', 'fact', 'entity'), ('type', 'urn:Rule', 'entity'),
                                           ('source_id', 'doc', 'chunk')])
def test_record_selection_filters_do_not_disqualify_its_exact_source(field, value, kind):
    service, fact, chunk, _doc = _fixture()
    result = classify(service, 'p', fact if kind == 'entity' else chunk,
        {'known_at': NOW, 'valid_at': NOW, 'filters': {'field': field, 'op': 'eq', 'value': value}})
    assert result['answer_eligible'] is True
    assert result['_allowed_source_version_ids'] == ['doc-v1']


def test_business_metadata_still_filters_the_source_inside_identity_and():
    service, fact, _chunk, doc = _fixture()
    doc['metadata']['region'] = '华北'
    predicate = {'and': [{'field': 'id', 'op': 'eq', 'value': 'fact'},
                         {'field': 'region', 'op': 'eq', 'value': '华东'}]}
    result = classify(service, 'p', fact, {'known_at': NOW, 'valid_at': NOW, 'filters': predicate})
    assert result['answer_eligible'] is False
    assert not result['_allowed_source_version_ids']


def test_or_branch_uses_matched_record_identity_without_erasing_metadata_branch():
    service, fact, _chunk, _doc = _fixture()
    predicate = {'or': [{'field': 'id', 'op': 'eq', 'value': 'fact'},
                        {'field': 'region', 'op': 'eq', 'value': '华北'}]}
    result = classify(service, 'p', fact, {'known_at': NOW, 'valid_at': NOW, 'filters': predicate})
    assert result['answer_eligible'] is True
    assert result['_allowed_source_version_ids'] == ['doc-v1']


@pytest.mark.parametrize('field,value', [('id', 'chunk'), ('type', 'urn:Chunk'), ('source_id', 'doc')])
def test_selected_chunk_keeps_same_revision_neighbor_under_record_filter(monkeypatch, field, value):
    service, _fact, chunk, doc = _fixture()
    chunk['type'] = 'urn:Chunk'
    adjacent = {**chunk, 'id': 'adjacent', 'version_id': 'adjacent-v1',
                'text': '例外条件', 'anchor_chunk_id': 'chunk'}
    repo = service.repository
    repo._lock = RLock()
    repo._record = lambda row: dict(row)
    repo._db = SimpleNamespace(execute=lambda statement, _params:
        SimpleNamespace(fetchall=lambda: [adjacent] if 'CROSS JOIN LATERAL' in statement else []))
    rows = [chunk, doc]
    monkeypatch.setattr(ScopedReads, 'by_ids', lambda _self, _p, scope, ids, *, kinds=None:
        [row for row in rows if row['id'] in ids and row['kind'] in kinds
         and matches_filter(row, scope.get('filters'))])
    result = ScopedReads(repo).source_context('p', {
        'known_at': NOW, 'filters': {'field': field, 'op': 'eq', 'value': value}}, [chunk])
    assert [row['id'] for row in result['context_chunks']] == ['adjacent']
