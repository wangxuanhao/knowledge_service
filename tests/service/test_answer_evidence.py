from knowledge_service.services.answer_evidence import AnswerEvidence


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
                {'code': 'legacy_unversioned_source', 'message': '无历史版本'}]}
            return value

    result = AnswerEvidence(Missing()).summaries(
        'p', 'a', [{'citation': 'E1', 'text': '手工知识'}])[0]

    assert result['sources'] == []
    assert result['source_status'] == 'missing'
    assert result['source_reason'] == 'legacy_unversioned_source'
