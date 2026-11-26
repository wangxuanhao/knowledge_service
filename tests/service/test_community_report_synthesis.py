import json
from threading import Barrier, Lock

import pytest

from knowledge_service.services import community_reports


@pytest.fixture
def postgres_backend():
    """These tests exercise synthesis with model fixtures."""


def test_synthesis_streams_answer_text_before_validating_citations(monkeypatch):
    from types import SimpleNamespace

    import httpx

    progress = []
    chunks = ['```json\n{"answer":"第一段', '\\n\\uD83D', '\\uDE00\\"后续\\"',
              '","citations":["e0"]}\n```']

    class ModelStream(httpx.SyncByteStream):
        def __iter__(self):
            for index, chunk in enumerate(chunks):
                if index == 1:
                    assert ('delta', {'text': '第一段'}) in progress
                if index == 3:
                    assert ''.join(data['text'] for event, data in progress if event == 'delta') == '第一段\n😀"后续"'
                yield ('data: ' + json.dumps({'choices': [{'delta': {'content': chunk}}]}) + '\n\n').encode()
            yield b'data: [DONE]\n\n'

    def model(request):
        assert json.loads(request.content)['stream'] is True
        return httpx.Response(200, stream=ModelStream())

    monkeypatch.setattr(community_reports, 'external_client', lambda *a, **kw:
                        httpx.Client(transport=httpx.MockTransport(model)))
    config = SimpleNamespace(configured=True, base_url='https://example.com/v1', api_key='test',
                             proxy=None, model='model', max_tokens=1800)
    result = community_reports.generate_answer('问题？', [_report(0, ['e0'])], [_evidence('e0')], config,
        emit=lambda event, data: progress.append((event, data)))
    assert result == ('第一段\n😀"后续"', ['e0'])
    assert progress[0] == ('progress', {'stage': 'generating'})
    assert progress[-1] == ('progress', {'stage': 'verifying'})


def test_community_ask_negotiates_streaming_and_reports_stale_snapshots():
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from knowledge_service.api.communities import router
    from knowledge_service.services.communities import CommunitySnapshotStale

    def ask(project, scope, community_ids, question, *, emit=None, **kwargs):
        if question == '过期的问题':
            raise CommunitySnapshotStale('快照已更新')
        if emit:
            emit('delta', {'text': '草稿'})
        return {'answer': '已核对答案', 'generated': True}

    app = FastAPI()
    app.state.communities = SimpleNamespace(ask=ask)
    app.include_router(router)
    url = '/api/projects/project/communities/ask'
    headers = {'Accept': 'text/event-stream'}

    def events(response):
        return [(event.removeprefix('event: '), json.loads(data.removeprefix('data: ')))
                for block in response.text.strip().split('\n\n') for event, data in [block.splitlines()]]

    with TestClient(app) as client:
        regular = client.post(url, json={'question': '问题'})
        assert regular.json()['answer'] == '已核对答案'
        streamed = client.post(url, json={'question': '问题'}, headers=headers)
        assert streamed.headers['content-type'].startswith('text/event-stream')
        assert events(streamed) == [('progress', {'stage': 'retrieving'}), ('delta', {'text': '草稿'}),
                                    ('done', regular.json())]
        stale = client.post(url, json={'question': '过期的问题'}, headers=headers)
        assert events(stale)[-1] == ('error', {'status': 409, 'detail': '快照已更新'})
        assert client.post(url, json={'question': '过期的问题'}).status_code == 409


def test_cross_community_answer_extracts_in_batches_then_synthesizes(monkeypatch):
    evidence = [{'id': f'e{i}', 'kind': 'document', 'title': f'规则 {i}', 'quote': f'条款 {i}'}
                for i in range(5)]
    details = [{'report': {'status': 'ready', 'title': f'社区 {i}',
                           'findings': [{'text': f'发现 {i}', 'citations': [f'e{i}']}]}}
               for i in range(5)]
    requests = []

    def answer(messages, config):
        payload = json.loads(messages[-1]['content'])
        requests.append(payload)
        citations = [item['id'] for item in payload['evidence']]
        if 'mapped_findings' in payload:
            return {'answer': '跨社区汇总', 'citations': citations}
        return {'findings': [{'text': '本批相关发现', 'citations': citations}]}

    monkeypatch.setattr(community_reports, 'complete', answer)
    assert community_reports.generate_answer('哪些规则有差异？', details, evidence, object()) == (
        '跨社区汇总', [f'e{i}' for i in range(5)])
    assert [len(item['reports']) for item in requests[:-1]] == [4, 1]
    assert len(requests[-1]['mapped_findings']) == 2


def _report(index, citations):
    return {'id': f'c{index}', 'report': {'status': 'ready', 'title': f'社区 {index}',
        'findings': [{'text': f'发现 {index}', 'citations': citations}]}}


def _evidence(ident, validity_status='effective'):
    return {'id': ident, 'kind': 'document', 'title': '平台规则', 'quote': '正式条款',
            'validity_status': validity_status}


def test_global_synthesis_maps_later_reports_and_ranks_unique_findings(monkeypatch):
    evidence = {f'e{i}': _evidence(f'e{i}') for i in range(10)}
    reports = [_report(i, [f'e{i}']) for i in range(11)]
    requests = []

    def answer(messages, config):
        payload = json.loads(messages[-1]['content'])
        requests.append(payload)
        if 'mapped_findings' in payload:
            return {'answer': '全局汇总', 'citations': ['e9']}
        return {'findings': [{'text': '跨平台差异', 'score': int(ref['id'][1:]) + 1,
                             'citations': [ref['id']]} for ref in payload['evidence']]}

    monkeypatch.setattr(community_reports, 'complete', answer)
    text, citations, coverage = community_reports.generate_global_answer(
        '各平台有何差异？', iter(reports), evidence, object())

    assert (text, citations) == ('全局汇总', ['e9'])
    assert sorted(len(payload['reports']) for payload in requests[:-1]) == [2, 4, 4]
    assert requests[-1]['mapped_findings'] == [{'text': '跨平台差异', 'score': 10, 'citations': ['e9']}]
    assert coverage['reports_considered'] == 11
    assert coverage['reports_mapped'] == coverage['reports_with_eligible_evidence'] == 10
    assert coverage['source_count'] == coverage['findings_mapped'] == 10
    assert coverage['findings_synthesized'] == 1


def test_global_synthesis_bounds_batch_evidence_and_reports_no_relevant_findings(monkeypatch):
    evidence = [_evidence(f'e{i}') for i in range(30)]
    reports = [_report(i, [item['id'] for item in evidence[i*15:(i+1)*15]]) for i in range(2)]
    requests = []

    def answer(messages, config):
        payload = json.loads(messages[-1]['content'])
        requests.append(payload)
        return {'findings': []}

    monkeypatch.setattr(community_reports, 'complete', answer)
    text, citations, coverage = community_reports.generate_global_answer('政策？', reports, evidence, object())

    assert '没有发现' in text and citations == []
    assert [len(payload['evidence']) for payload in requests] == [15, 15]
    assert coverage['reports_mapped'] == 2
    assert coverage['findings_synthesized'] == 0


def test_global_synthesis_rejects_source_from_outside_map_batch(monkeypatch):
    evidence = [_evidence(f'e{i}') for i in range(5)]
    reports = [_report(i, [f'e{i}']) for i in range(5)]
    monkeypatch.setattr(community_reports, 'complete', lambda *_: {
        'findings': [{'text': '伪造出处的发现', 'score': 100, 'citations': ['e4']}]
    })

    with pytest.raises(ValueError, match='未提供的证据'):
        community_reports.generate_global_answer('在哪些场景适用？', reports, evidence, object())


def test_global_synthesis_caps_in_flight_maps_and_preserves_report_order(monkeypatch):
    reports = [_report(i, [f'e{i}']) for i in range(17)]
    evidence = [_evidence(f'e{i}') for i in range(17)]
    first_wave = Barrier(4, timeout=3)
    lock = Lock()
    completed = set()

    def answer(messages, config):
        payload = json.loads(messages[-1]['content'])
        if 'mapped_findings' in payload:
            assert [item['text'] for item in payload['mapped_findings']] == [
                f'社区 {i}' for i in range(0, 17, 4)]
            return {'answer': '全局汇总', 'citations': ['e16']}
        title = payload['reports'][0]['title']
        if title == '社区 16':
            with lock:
                assert completed == {'社区 0', '社区 4', '社区 8', '社区 12'}
        else:
            first_wave.wait()
            with lock:
                completed.add(title)
        return {'findings': [{'text': title, 'score': 50,
                             'citations': payload['reports'][0]['findings'][0]['citations']}]}

    monkeypatch.setattr(community_reports, 'complete', answer)
    _, citations, coverage = community_reports.generate_global_answer('有哪些规则？', reports, evidence, object())
    assert citations == ['e16']
    assert coverage['reports_mapped'] == 17


def test_community_rule_judgment_omits_findings_with_ineligible_citations(monkeypatch):
    evidence = [_evidence('effective'), _evidence('expired', 'expired')]
    reports = [_report(0, ['effective', 'expired']), _report(1, ['effective'])]
    requests = []

    def answer(messages, config):
        requests.append(json.loads(messages[-1]['content']))
        return {'answer': '现行条款', 'citations': ['effective']}

    monkeypatch.setattr(community_reports, 'complete', answer)
    assert community_reports.generate_answer('现行规则？', reports, evidence, object(), rule_judgment=True) == (
        '现行条款', ['effective'])
    assert [item['title'] for item in requests[0]['reports']] == ['社区 1']
    assert requests[0]['evidence'][0]['validity_status'] == 'effective'
