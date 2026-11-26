import pytest

from knowledge_service.services import community_question_plan as planning


@pytest.fixture
def postgres_backend():
    """The planner and dependency execution use bounded retrieval fixtures."""


def test_planner_keeps_the_original_single_question_and_validates_dependencies(monkeypatch):
    payload = {'questions': [{'query': '改写过的问题'}]}
    monkeypatch.setattr(planning.community_reports, 'complete', lambda *_: payload)
    assert planning.plan_question('音频、视频盗播如何认定？', object())[0].query == '音频、视频盗播如何认定？'

    payload['questions'] = [{'query': '盗播适用平台是什么？'},
                            {'query': '{platform} 有哪些其他创作者限制？', 'platform_from': 1}]
    assert planning.plan_question('盗播属于哪个平台，这个平台还有什么限制？', object()) == (
        planning.Subquestion('盗播适用平台是什么？'),
        planning.Subquestion('{platform} 有哪些其他创作者限制？', 1),
    )
    payload['questions'][1]['platform_from'] = 2
    with pytest.raises(ValueError):
        planning.plan_question('复合问题', object())


def test_subquestions_reserve_evidence_and_use_only_a_unique_cited_platform():
    steps = (planning.Subquestion('盗播定义'), planning.Subquestion('细则适用平台'),
             planning.Subquestion('{platform} 的其他限制', 2))
    requests, events = [], []

    def retrieve(query, platform):
        requests.append((query, platform))
        if query == '细则适用平台':
            return {'evidence': [{'id': 'E-platform', 'source_platforms': ['抖音电商']}], 'channels': ['keyword'],
                    'platform_evidence_ids': ['E-platform']}
        return {'evidence': [{'id': f'E-{query}-{n}'} for n in range(20)], 'channels': ['semantic']}

    answers = planning.retrieve_subquestions(steps, retrieve, lambda name, data: events.append((name, data)))
    assert requests.index(('抖音电商 的其他限制', '抖音电商')) > requests.index(('细则适用平台', None))
    assert [len(answer['evidence']) for answer in answers] == [6, 1, 6]
    assert answers[-1]['platform'] == '抖音电商'
    assert {data['subquestion']['number'] for name, data in events if name == 'evidence'} == {1, 2, 3}

    def ambiguous(query, platform):
        assert platform is None
        return {'evidence': [{'id': 'E-1', 'source_platforms': ['抖音电商']},
                             {'id': 'E-2', 'source_platforms': ['快手']}], 'channels': [],
                'platform_evidence_ids': ['E-1', 'E-2']}

    blocked = planning.retrieve_subquestions(steps, ambiguous)[-1]
    assert blocked['state'] == 'platform_unresolved' and blocked['evidence'] == []

    unrelated = {'id': 'E-unrelated', 'title': '直播预热规则', 'quote': '平台规定', 'source_platforms': ['快手']}
    relevant = {'id': 'E-related', 'title': '盗播细则', 'quote': '盗播认定', 'source_platforms': ['抖音电商']}
    assert planning.platform_evidence([unrelated, relevant], ['盗播', '平台', '规则']) == [relevant]
