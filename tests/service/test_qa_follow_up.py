import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from knowledge_service.api.communities import CommunityAsk
from knowledge_service.models import Question


@pytest.fixture
def postgres_backend():
    """Question rewriting and request validation use no persistence."""


HISTORY = [{'question': '盗播首次违规有哪些处置措施？', 'answer': '原文写明可能中断直播。'}]


def test_both_question_requests_accept_bounded_context():
    for model, field in ((Question, 'query'), (CommunityAsk, 'question')):
        request = {field: '那第二次呢？', 'conversation_context': HISTORY}
        assert model.model_validate(request).model_dump()['conversation_context'] == HISTORY
        with pytest.raises(ValidationError):
            model.model_validate({**request, 'conversation_context': HISTORY * 4})


def test_follow_up_is_rewritten_with_a_small_model_request(monkeypatch):
    from knowledge_service.services import qa_follow_up

    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'question': '盗播第二次违规有哪些处置措施？',
        }, ensure_ascii=False)}}]})

    monkeypatch.setattr(qa_follow_up, 'external_client', lambda *args, **kwargs:
                        httpx.Client(transport=httpx.MockTransport(respond)))
    config = SimpleNamespace(configured=True, base_url='https://example.invalid/v1',
                             proxy=None, api_key='fixture', model='fixture')
    assert qa_follow_up.resolve_question('那第二次呢？', HISTORY, model=lambda: config) == '盗播第二次违规有哪些处置措施？'
    assert json.loads(seen[0]['messages'][1]['content'])['conversation'] == HISTORY
    assert seen[0]['max_tokens'] <= 300


def test_evidence_only_follow_up_adds_the_topic_without_a_model():
    from knowledge_service.services.qa_follow_up import resolve_knowledge_request

    request = {'query': '那第三次呢？', 'conversation_context': [*HISTORY, {'question': '那第二次呢？'}],
               'generate': False, 'applicability': {'platforms': {'mode': 'specific', 'values': ['抖音']}}}
    result = resolve_knowledge_request(request)
    assert '盗播' in result['query'] and '第三次' in result['query']
    assert '第二次' not in result['query']
    assert result['applicability'] == request['applicability']
    assert 'conversation_context' not in result
    assert request['query'] == '那第三次呢？'


def test_standalone_question_does_not_use_history_and_model_failure_keeps_topic():
    from knowledge_service.services.qa_follow_up import resolve_question

    def unavailable():
        raise RuntimeError('fixture provider unavailable')

    standalone = '商户如何申请退出？'
    assert resolve_question(standalone, HISTORY, model=unavailable) == standalone
    fallback = resolve_question('那第二次呢？', HISTORY, model=unavailable)
    assert '盗播' in fallback and '第二次' in fallback
