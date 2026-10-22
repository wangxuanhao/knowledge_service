"""Real reader enrichment and QA qualification retain authenticated source scope."""
from threading import RLock
from types import SimpleNamespace

import pytest

from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services import answers
from knowledge_service.services.answer_validity import apply_validity
from knowledge_service.services.retrieval_context import enrich_source_context
from knowledge_service.utils.filters import matches_filter


NOW = '2026-10-21T08:00:00.000000Z'


def _system(monkeypatch, *, forge=False, platforms=None):
    common = {'valid_from': '2020-01-01T00:00:00.000000Z', 'valid_until': None,
              'recorded_at': '2020-01-01T00:00:00.000000Z', 'superseded_at': None,
              'version': 1, 'project_id': 'p'}
    metadata = {'region': '华东', 'applicability': {'platforms': {'mode': 'all', 'values': []}}}
    if platforms:
        metadata['applicability'] = {'platforms': {'mode': 'specific', 'values': list(platforms)}}
    doc = {**common, 'id': 'doc', 'version_id': 'doc-v1', 'kind': 'document', 'text': '原文',
           'metadata': dict(metadata)}
    chunk = {**common, 'id': 'chunk', 'version_id': 'chunk-v1', 'kind': 'chunk', 'text': '原文',
             'source_id': 'doc', 'type': 'urn:Chunk',
             'metadata': {**metadata, 'source_version_id': 'doc-v1', 'start_char': 0}}
    adjacent = {**chunk, 'id': 'adjacent', 'version_id': 'adjacent-v1', 'text': '例外',
                'metadata': {**chunk['metadata'], 'start_char': 10}}
    fact = {**common, 'id': 'fact', 'version_id': 'fact-v1', 'kind': 'entity', 'type': 'urn:Rule',
            'ontology_id': 'release', 'text': '事实', 'metadata': dict(metadata)}
    rows = [fact, chunk, adjacent, doc]
    assertion = {'id': 'a', 'created_at': common['recorded_at'], 'document_version_id': 'doc-v1',
                 'document_id': 'doc', 'chunk_id': 'chunk'}
    def execute(statement, params):
        if 'CROSS JOIN LATERAL' in statement:
            found = [{**adjacent, 'anchor_chunk_id': 'chunk'}]
        elif 'JOIN assertions a' in statement and any(
                isinstance(value, list) and 'fact-v1' in value for value in params):
            found = [{**chunk, 'anchor_record_version_id': 'fact-v1'}]
        else:
            found = []
        return SimpleNamespace(fetchall=lambda: found)
    repo = SimpleNamespace(_lock=RLock(), _db=SimpleNamespace(execute=execute), _record=lambda row: dict(row),
        get_record_version=lambda *_: doc, history=lambda _p, id: [doc if id == 'doc' else chunk],
        records_by_version_ids=lambda _p, ids: [doc] if 'doc-v1' in ids else [],
        records_at_knowledge_time=lambda *_: [doc],
        list_record_version_assertions=lambda *_a, **_kw: [{'assertion_id': 'a', 'assertion_event_id': 'accepted'}],
        get_assertion=lambda *_: assertion,
        list_assertion_events=lambda *_: [{'id': 'accepted', 'created_at': common['recorded_at']}])
    def by_ids(_reader, _project, scope, ids, *, kinds=None):
        return [row for row in rows if row['id'] in ids and row['kind'] in kinds
                and matches_filter(row, scope.get('filters')) and
                (scope.get('ontology_scope') != 'ids' or row.get('ontology_id') in scope.get('ontology_ids', []))]
    monkeypatch.setattr(ScopedReads, 'by_ids', by_ids)
    class Service:
        repository = repo
        encoder = SimpleNamespace(identity='test', semantic=False)
        def annotate_structure_pending(self, *_args):
            pass
        def search(self, _project, request):
            hit = chunk if (request.get('filters') or {}).get('field') == 'source_id' else fact
            hit = {**hit, 'source_scope': 'all', 'query_platform': None}
            result = {'hits': [hit], 'candidate_count': 1, 'active_mode': 'keyword',
                      '_backend': 'milvus', 'complete': True,
                      'platform_groups': [{'hits': [hit], 'platform': None, 'source_scope': 'all'}]}
            if platforms:
                result['platform_groups'] = [{'hits': [{**hit, 'query_platform': platform, 'source_scope': 'specific'}],
                                             'platform': platform, 'source_scope': 'specific'} for platform in platforms]
                # Search flattens duplicate versions globally; each platform
                # still owns its separately qualified hit occurrence.
                result['hits'] = list(result['platform_groups'][0]['hits'])
            enrich_source_context(repo, 'p', request, result)
            if forge and result['platform_groups'][0].get('context_chunks'):
                legitimate = result['platform_groups'][0]['context_chunks'][0]
                alien = {**legitimate, 'id': 'alien', 'version_id': 'alien-v1', 'source_id': 'other-doc'}
                result['platform_groups'][0]['context_chunks'].append(alien)
            return result
    return Service()


@pytest.mark.parametrize('selection,expected', [
    ({'filters': {'field': 'id', 'op': 'eq', 'value': 'fact'}}, {'fact', 'chunk', 'adjacent'}),
    ({'filters': {'field': 'type', 'op': 'eq', 'value': 'urn:Rule'}}, {'fact', 'chunk', 'adjacent'}),
    ({'filters': {'field': 'source_id', 'op': 'eq', 'value': 'doc'}}, {'chunk', 'adjacent'}),
    ({'ontology_scope': 'ids', 'ontology_ids': ['release']}, {'fact', 'chunk', 'adjacent'}),
])
def test_enriched_sources_survive_real_qa_filter_and_classification(monkeypatch, selection, expected):
    service = _system(monkeypatch)
    request = {'query': '退款', 'known_at': NOW, 'valid_at': NOW, 'graph_context': False,
               'include_references': False, **selection}
    context = answers._retrieved_question_context(service, 'p', request)
    context = apply_validity(service, 'p', context, request)
    assert {row['id'] for row in context['evidence_rows']} == expected
    assert all(row['answer_eligible'] for row in context['evidence_rows'])


def test_copied_source_context_authorization_cannot_authorize_another_artifact(monkeypatch):
    service = _system(monkeypatch, forge=True)
    request = {'query': '退款', 'known_at': NOW, 'valid_at': NOW, 'graph_context': False,
               'include_references': False, 'filters': {'field': 'id', 'op': 'eq', 'value': 'fact'}}
    context = answers._retrieved_question_context(service, 'p', request)
    context = apply_validity(service, 'p', context, request)
    assert {'fact', 'chunk', 'adjacent'} <= {row['id'] for row in context['evidence_rows']}
    assert all(row['id'] != 'alien' for row in context['evidence_rows'])


def test_each_platform_uses_its_own_direct_hit_even_when_flat_hits_deduplicate(monkeypatch):
    service = _system(monkeypatch, platforms=['A', 'B'])
    request = {'query': '退款', 'known_at': NOW, 'valid_at': NOW, 'graph_context': False,
               'include_references': False, 'filters': {'field': 'id', 'op': 'eq', 'value': 'fact'},
               'applicability': {'platforms': {'mode': 'specific', 'values': ['A', 'B']}}}
    context = answers._retrieved_question_context(service, 'p', request)
    context = apply_validity(service, 'p', context, request)
    assert {(row['id'], row['query_platform']) for row in context['evidence_rows']} == {
        (id, platform) for id in ('fact', 'chunk', 'adjacent') for platform in ('A', 'B')}
