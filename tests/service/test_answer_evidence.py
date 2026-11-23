from copy import deepcopy
from types import SimpleNamespace

import pytest

from knowledge_service.services.answers import _retrieved_question_context
from knowledge_service.services.answer_evidence import AnswerEvidence
from knowledge_service.services.provenance import ProvenanceService
from knowledge_service.services.qa_conversations import sanitize_messages


class FrozenTrace:
    def trace_answer_evidences(self, project_id, answer_id, citations):
        return {citation: self.trace_answer_evidence(project_id, answer_id, citation)
                for citation in citations}

    def trace_answer_evidence(self, project_id, answer_id, citation):
        return {
            'subject': {'answer_id': answer_id},
            'retrieval': {'requested_mode': 'hybrid', 'rank': 2},
            'nodes': [
                {'type': 'source_occurrence', 'ref': 'occ:2', 'details': {
                    'document_id': 'd2', 'version_id': 'dv2', 'version': 3,
                    'title': '附件二', 'highlight': '乙' * 300,
                    'start_char': 30, 'end_char': 330}},
                {'type': 'source_occurrence', 'ref': 'occ:1', 'details': {
                    'document_id': 'd1', 'version_id': 'dv1', 'version': 1,
                    'title': '制度原文', 'highlight': '甲条款',
                    'start_char': 10, 'end_char': 13}},
            ],
            'edges': [
                {'source_ref': 'chunk:1', 'relation': 'sourced-from',
                 'target_ref': 'occ:1', 'ordinal': 4, 'payload': {}},
                {'source_ref': 'chunk:2', 'relation': 'sourced-from',
                 'target_ref': 'occ:2', 'ordinal': 8, 'payload': {}},
            ],
            'integrity': {'complete': True, 'warnings': []},
        }


def test_summaries_expose_frozen_sources_in_edge_order_and_bound_quotes():
    result = AnswerEvidence(FrozenTrace()).summaries(
        'p', 'a', [{'citation': 'E1', 'text': '证据正文', 'version_id': 'rv1'}])

    assert result[0]['citation'] == 'E1'
    assert result[0]['record_version_id'] == 'rv1'
    assert result[0]['retrieval_channel'] == 'hybrid'
    assert result[0]['source_status'] == 'complete'
    assert [item['document_id'] for item in result[0]['sources']] == ['d1', 'd2']
    assert result[0]['sources'][0]['quote'] == '甲条款'
    assert len(result[0]['sources'][1]['quote']) == 240


def test_missing_frozen_source_is_explicit_and_never_falls_back():
    class Missing(FrozenTrace):
        def trace_answer_evidence(self, *args):
            value = super().trace_answer_evidence(*args)
            value['nodes'] = []
            value['edges'] = []
            value['integrity'] = {'complete': False, 'warnings': [
                {'code': 'source_version_missing', 'message': '无历史版本'}]}
            return value

    result = AnswerEvidence(Missing()).summaries(
        'p', 'a', [{'citation': 'E1', 'text': '手工知识'}])[0]

    assert result['sources'] == []
    assert result['source_status'] == 'missing'
    assert result['source_reason'] == 'source_version_missing'


@pytest.mark.parametrize('grouped', [False, True])
def test_qa_context_preserves_rank_metadata_only_for_exact_versions(grouped):
    match = {'mode': 'keyword', 'strategy': None, 'channels': ['keyword'],
             'fields': ['title'], 'indexed_fields': [], 'score': 0.25}
    matched = {'id': 'matched', 'version_id': 'v1', 'kind': 'chunk', 'source_scope': 'all'}
    newer = {**matched, 'id': 'changed', 'version_id': 'v2'}
    older = {**newer, 'version_id': 'v3'}
    hits = [{**row, 'retrieval_match': match} for row in [matched, newer]]
    result = {'hits': hits, 'context_chunks': [matched, older]}
    if grouped:
        result['platform_groups'] = [dict(result)]
    service = SimpleNamespace(search=lambda *_args: result, repository=object(),
        encoder=SimpleNamespace(identity='test', semantic=False),
        annotate_structure_pending=lambda *_args: None)
    context = _retrieved_question_context(service, 'p', {'query': 'test', 'graph_context': False})
    rows = {row['version_id']: row for row in context['evidence_rows']}
    assert rows['v1']['retrieval_match'] == match
    assert 'retrieval_match' not in rows['v3']


def test_frozen_match_survives_provenance_and_history_without_query_text():
    record = {'id': 'rule', 'version_id': 'v1', 'kind': 'entity', 'version': 1,
              'recorded_at': '2026-11-23T00:00:00Z', 'text': 'Example rule'}
    activities, edges = {}, []

    def save_activity(_project, activity_id, kind, payload):
        activities[activity_id] = {'kind': kind, 'status': 'running', 'payload': payload,
                                   'started_at': record['recorded_at']}

    def freeze(project, *, retrieval_id, answer_id, snapshot_factory):
        retrieval, answer, frozen = deepcopy(snapshot_factory())
        activities[retrieval_id].update(status='completed', payload=retrieval)
        save_activity(project, answer_id, 'answer', answer)
        edges.extend(frozen)

    provenance = ProvenanceService(SimpleNamespace(begin_provenance_activity=save_activity,
        complete_retrieval_and_begin_answer=freeze, get_provenance_activity=lambda p, id: activities[id],
        list_provenance_edges=lambda p, *, activity_id: [e for e in edges if e['activity_id'] == activity_id],
        records_by_version_ids=lambda p, ids: [record] if record['version_id'] in ids else [],
        list_record_version_assertions=lambda *_args, **_kwargs: []))
    match = {'mode': 'hybrid', 'strategy': 'fusion', 'channels': ['keyword', 'semantic'],
             'fields': ['title'], 'indexed_fields': ['body'], 'score': 0, 'rrf_score': 0.02}
    evidence = {**record, 'citation': 'E1', 'retrieval_match': {**match, 'query': 'private-query'}}
    reference = {**record, 'citation': 'R1', 'score': 0.9}
    run = provenance.begin_retrieval('p', {'query': 'test'})
    answer = provenance.complete_retrieval('p', run, {}, [evidence, reference])
    evidence['retrieval_match'] = {'mode': 'semantic'}

    trace = provenance.trace_answer_evidence('p', answer, 'E1')
    assert trace['retrieval']['retrieval_match'] == match
    assert [edge['payload']['retrieval_match'] for edge in trace['edges']
            if edge['relation'] in ('offered', 'considered')] == [match, match]
    ranked, historical = AnswerEvidence(provenance).summaries('p', answer, [evidence, reference])
    assert ranked['retrieval_match'] == match
    assert 'retrieval_match' not in historical

    snapshot = sanitize_messages('p', [{'kind': 'assistant', 'text': 'answer', 'scope': {},
        'evidence': [{**ranked, 'retrieval_match': {**match, 'fields': ['title', 'private-query', 'title'],
                      'channels': ['keyword', 'secret', 'semantic'], 'query': 'private-query'}}],
        'historical_references': [historical]}])[0]
    assert snapshot['evidence'][0]['retrieval_match'] == match
    assert 'retrieval_match' not in snapshot['historical_references'][0]
