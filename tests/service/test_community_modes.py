import pytest
from pydantic import ValidationError

from knowledge_service.api.communities import CommunityAsk
from knowledge_service.services import communities


def test_global_mode_requires_a_level_and_owns_its_scope():
    assert CommunityAsk(question='主题有哪些？', mode='global', level=1).level == 1
    with pytest.raises(ValidationError):
        CommunityAsk(question='主题有哪些？', mode='global')
    with pytest.raises(ValidationError):
        CommunityAsk(question='主题有哪些？', mode='global', level=1, community_ids=['community-a'])


def test_report_fingerprint_tracks_text_and_snippets_not_report_timestamp():
    original = {'id': 'c', 'report': {'title': '履约规则', 'summary': '要按约定提供服务。',
                'findings': [{'text': '按约定提供服务', 'citations': ['e1']}]}}
    assert communities.vector_reports([original])[0]['fingerprint'] == communities.vector_reports([
        {**original, 'report': {**original['report'], 'llm': {'name': 'another'}}}])[0]['fingerprint']
    edited = {**original, 'report': {**original['report'], 'summary': '旧报告已修订。'}}
    assert communities.vector_reports([original])[0]['fingerprint'] != communities.vector_reports([edited])[0]['fingerprint']
