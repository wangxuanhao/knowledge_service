from knowledge_service.services import answers
from test_rule_document_oag import qa, source, request  # noqa: F401
import json


def test_unknown_material_is_found_but_not_eligible():
    context = {'structure_pending': {'count': 0}, 'uncertain_references': [
        {'citation': 'U1', 'text': '价格说明', 'usage': 'qualified_quote'}]}
    text, mode, audit = answers.final_answer('', 'evidence_only', [], context)
    assert '已找到相关材料' in text
    assert '生效时间' in text
    assert '[U1]' in text
    assert mode == 'evidence_only'
    state = answers.generation_state({'generate': True}, [], context)
    assert state == {'requested': True, 'called': False, 'skip_reason': 'no_eligible_evidence', 'material_status': 'unverified'}


def test_disabled_and_empty_generation_have_distinct_reasons():
    assert answers.generation_state({'generate': False}, [{'citation': 'E1'}], {})['skip_reason'] == 'disabled'
    assert answers.generation_state({'generate': True}, [], {})['skip_reason'] == 'no_evidence'
    state = answers.generation_state({'generate': True}, [{'citation': 'E1'}], {})
    assert not state['called'] and state['skip_reason'] is None
    assert answers.generation_state({'generate': True}, [{'citation': 'E1'}], {}, called=True)['called']


def test_identical_document_quotes_are_read_once_with_all_citations_retained():
    from knowledge_service.services.answer_validity import qualified_text
    text = qualified_text({'uncertain_references': [
        {'citation': citation, 'text': '# 价格说明\n**团购价：**销售价',
         'query_platform': '美团', 'usage': 'qualified_quote'}
        for citation in ['U1', 'U2']]})
    assert text.count('团购价') == 1
    assert '[U1]' in text and '[U2]' in text
    assert '\n# 价格说明' in text


def test_json_and_stream_explain_skip_without_calling_provider(qa, monkeypatch):
    service, repo, project = qa
    source(repo, project, 'undated')
    def forbidden(*args, **kwargs):
        raise AssertionError('Unverified sources cannot trigger a rule-answer model')
    monkeypatch.setattr(answers, 'external_client', forbidden)
    from knowledge_service.services import service as service_module
    monkeypatch.setattr(service_module, 'external_client', forbidden)
    body = request(generate=True)
    result = service.answer(project, body)
    done = [json.loads(block.split('\ndata: ', 1)[1])
            for block in answers.stream_events(service, project, body)
            if block.startswith('event: done')][0]
    for item in [result, done]:
        assert item['generation']['skip_reason'] == 'no_eligible_evidence'
        assert not item['generation']['called']
        assert item['mode'] == 'evidence_only'
        assert '已找到相关材料' in item['answer']
