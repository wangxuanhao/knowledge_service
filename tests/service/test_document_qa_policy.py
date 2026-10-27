"""Purpose and frozen citation authorization are server decisions."""
from knowledge_service.services.answer_validity import freeze_request
import json
from contextlib import contextmanager
import pytest
from test_rule_document_oag import qa, source  # noqa: F401


def test_document_topic_is_classified_before_implicit_clock_and_survives_refreeze():
    request = freeze_request({'query': '美团价格'})
    assert request['answer_purpose'] == 'document_explanation'
    assert request['_explicit_valid_at'] is False
    assert freeze_request(request)['answer_purpose'] == 'document_explanation'


def test_explicit_business_date_and_permission_override_definition_words():
    for query in ('美团价格当前有哪些', '哪些价格可以使用', '解释必须执行什么', '价格是否有效',
                  '退款规则有哪些', '退款流程有哪些步骤', '介绍退款操作'):
        assert freeze_request({'query': query})['answer_purpose'] == 'rule_judgment'
    assert freeze_request({'query': '价格是什么', 'valid_at': '2025-01-01'})['answer_purpose'] == 'rule_judgment'
    for query in ('what are refund rules', 'explain refund procedures', 'what are the requirements'):
        assert freeze_request({'query': query})['answer_purpose'] == 'rule_judgment'


def test_ambiguous_question_is_conservative():
    assert freeze_request({'query': '怎么办'})['answer_purpose'] == 'rule_judgment'


@pytest.mark.parametrize('dates,prefix', [({}, 'U'), ({'valid_until': '2020-01-01'}, 'R'),
                                         ({'valid_from': '2099-01-01'}, 'F')])
@pytest.mark.parametrize('illegal', [False, True])
def test_document_generation_json_sse_and_frozen_quote_audit(qa, monkeypatch, dates, prefix, illegal):
    from knowledge_service.services import answers, service as facade
    service, repo, project = qa
    source(repo, project, 'original', **dates)
    calls = []
    model_reply = 'FORBIDDEN [U99]' if illegal else f'文档介绍退款条款 [{prefix}1]'

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'choices': [{'message': {'content': model_reply}}]}

        def iter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': model_reply}}]})
            yield 'data: [DONE]'

    class Client:
        def post(self, *args, **kwargs):
            calls.append(kwargs['json'])
            return Response()

        @contextmanager
        def stream(self, *args, **kwargs):
            calls.append(kwargs['json'])
            yield Response()

    @contextmanager
    def provider(*_):
        yield Client()

    for name, value in [('KG_LLM_BASE_URL', 'https://provider.invalid'),
                        ('KG_LLM_API_KEY', 'test-placeholder'), ('KG_LLM_MODEL', 'test-model')]:
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(answers, 'external_client', provider)
    monkeypatch.setattr(facade, 'external_client', provider)
    body = {'query': '退款条款是什么', 'generate': True, 'retrieval_mode': 'keyword', 'hops': 0}
    result = service.answer(project, body)
    done = next(json.loads(event.split('\ndata: ', 1)[1])
                for event in answers.stream_events(service, project, body) if event.startswith('event: done'))
    assert len(calls) == 2
    for call in calls:
        prompt = json.loads(call['messages'][-1]['content'])
        items = [row for group in prompt['platform_evidence'] for row in group['evidence']]
        assert {row['id'] for row in items} == {prefix + '1'}
    for item in (result, done):
        assert item['answer_purpose'] == 'document_explanation'
        assert item['generation']['called'] is True
        assert item['mode'] == ('evidence_only' if illegal else 'llm')
        if illegal:
            assert 'FORBIDDEN' not in item['answer'] and '[U99]' not in item['answer']
        else:
            assert 'original' not in item['answer']  # no duplicate full quote after summary
        assert item['eligibility_audit']['passed'] is not illegal
        frozen = repo.get_provenance_activity(project, item['answer_id'])['payload']
        assert frozen['cited'] == [prefix + '1']
        assert frozen['warnings'] == []
        assert frozen['answer_purpose'] == 'document_explanation'
        assert frozen['generation']['called'] is True


def test_document_without_material_does_not_call_model(qa, monkeypatch):
    from knowledge_service.services import answers, service as facade
    service, repo, project = qa

    def forbidden(*_, **__):
        raise AssertionError('no material must skip model')

    monkeypatch.setattr(answers, 'external_client', forbidden)
    monkeypatch.setattr(facade, 'external_client', forbidden)
    body = {'query': '价格是什么', 'generate': True, 'retrieval_mode': 'keyword'}
    result = service.answer(project, body)
    done = next(json.loads(event.split('\ndata: ', 1)[1])
                for event in answers.stream_events(service, project, body) if event.startswith('event: done'))
    for item in (result, done):
        assert not item['generation']['called']
        assert item['generation']['skip_reason'] == 'no_evidence'


@pytest.mark.parametrize('dates,prefix', [({}, 'U'), ({'valid_until': '2020-01-01'}, 'R'),
                                         ({'valid_from': '2099-01-01'}, 'F')])
@pytest.mark.parametrize('budget', [1, 5])
def test_document_reference_budget_keeps_original_after_entity_hits(qa, monkeypatch, dates, prefix, budget):
    from copy import deepcopy
    from knowledge_service.services import answers
    from knowledge_service.services.qa_policy import model_evidence
    service, repo, project = qa
    text = '未成年人模式下，每日22时至次日6时无法使用直播服务。'
    meta = {'applicability': {'platforms': {'mode': 'all'}}}
    doc = repo.put_record(project, {'id': 'livestream-doc', 'kind': 'document',
                                    'text': text, 'metadata': meta, **dates})
    chunk = repo.put_record(project, {
        'id': 'livestream-chunk', 'kind': 'chunk', 'source_id': doc['id'], 'text': text,
        'metadata': {**meta, 'source_version_id': doc['version_id'],
                     'start_char': 0, 'end_char': len(text)}, **dates})
    entities = [repo.put_record(project, {
        'id': f'entity-{index}', 'kind': 'entity', 'text': f'直播限制时段 {index}',
        'metadata': {'applicability': {'platforms': {'mode': 'all'}}}, **dates})
        for index in range(5)]
    # Retrieval ranks entity hits before the original chunks. Both feed the
    # same temporal reference budget, as in the reported hybrid-search result.
    context = {'hits': [*entities, chunk], 'evidence_rows': [*entities, chunk],
               'reasoning_paths': [], 'candidate_count': 6}
    def retrieved(_service, _project, request):
        assert request['_explicit_known_at'] is False
        assert request['_qa_index_snapshot']['project_id'] == project
        return deepcopy(context)

    monkeypatch.setattr(answers, '_retrieved_question_context', retrieved)
    body = {'query': '直播限制时段是什么时间', 'retrieval_mode': 'keyword',
            'hops': 0, 'reference_k': budget}
    result = service.answer(project, body)
    material = model_evidence(result['evidence'], result)
    assert [row['id'] for row in material] == [chunk['id']]
    assert material[0]['citation'] == prefix + '1'
    assert material[0]['document_quote_allowed'] is True
    assert text in result['answer']
    assert result['evidence'] == []
    generation = answers.generation_state({**body, 'generate': True}, result['evidence'], result)
    assert generation['skip_reason'] is None
    events = {event.split('\n', 1)[0]: json.loads(event.split('\ndata: ', 1)[1])
              for event in answers.stream_events(service, project, body)}
    streamed = events['event: evidence']
    assert [row['id'] for row in model_evidence(streamed['evidence'], streamed)] == [chunk['id']]
    assert events['event: done']['answer'] == result['answer']


def test_unauthorized_document_citation_discards_model_answer():
    from knowledge_service.services.answers import final_answer
    context = {'answer_purpose': 'document_explanation', '_document_quote_citations': ['U1'],
               'uncertain_references': [{'citation': 'U1', 'kind': 'chunk', 'text': '原文',
                                          'validity_status': 'unknown', 'usage': 'qualified_quote'}]}
    answer, mode, audit = final_answer('UNSUPPORTED [U99]', 'llm', [], context)
    assert 'UNSUPPORTED' not in answer and '[U99]' not in answer
    assert '[U1]' in answer and mode == 'evidence_only'
    assert audit == {'passed': False, 'invalid_citations': ['U99']}


def test_rule_model_cannot_authorize_reference_citations():
    from knowledge_service.services.answers import final_answer
    for prefix in ('U', 'R', 'F'):
        answer, mode, audit = final_answer(f'UNSUPPORTED [{prefix}1]', 'llm', [],
                                           {'answer_purpose': 'rule_judgment'})
        assert 'UNSUPPORTED' not in answer and mode == 'evidence_only'
        assert audit['passed'] is False


def test_document_generation_uses_no_implicit_business_date_in_retrieval(qa, monkeypatch):
    service, repo, project = qa
    source(repo, project, 'expired', valid_until='2020-01-01')
    search = service.search
    seen = []

    def inspect(project, body):
        seen.append(dict(body))
        return search(project, body)

    monkeypatch.setattr(service, 'search', inspect)
    result = service.answer(project, {'query': '退款条款是什么', 'retrieval_mode': 'keyword', 'hops': 0})
    assert seen and seen[0].get('valid_at') is None
    assert result['as_of']
    assert result['historical_references'][0]['validity_status'] == 'expired'
    assert '[R1]' in result['answer']


def test_frozen_audit_rejects_forged_document_quote_permission(qa):
    from knowledge_service.services.provenance import ProvenanceService
    service, repo, project = qa
    row = source(repo, project, 'forged')
    provenance = ProvenanceService(repo)
    run = provenance.begin_retrieval(project, {'query': '定义'})
    row.update(citation='U1', validity_status='unknown', usage='qualified_quote',
               answer_eligible=False, document_quote_allowed=True)
    # No exact allowed source/chunk snapshot: a flag and model-written label
    # cannot manufacture authorization in the committed offered edge.
    answer_id = provenance.complete_retrieval(project, run, {'answer_purpose': 'document_explanation'}, [row])
    row['_allowed_chunk_version_ids'] = [row['version_id']]
    row['_allowed_source_version_ids'] = [row['metadata']['source_version_id']]
    provenance.complete_answer(project, answer_id, run,
                               '按文档说明，有效性待核对 [U1]', 'llm', [row])
    frozen = repo.get_provenance_activity(project, answer_id)['payload']
    assert frozen['cited'] == []
    assert any(item['code'] == 'ineligible_citation' for item in frozen['warnings'])


def test_deduplicated_literal_quotes_keep_every_frozen_citation(qa):
    service, repo, project = qa
    meta = {'applicability': {'platforms': {'mode': 'all'}}}
    text = '价格说明：团购价为文档所述销售价。'
    doc = repo.put_record(project, {'id': 'doc', 'kind': 'document', 'text': text, 'metadata': meta})
    for index in range(2):
        repo.put_record(project, {'id': f'chunk-{index}', 'kind': 'chunk', 'source_id': 'doc',
                                 'text': text, 'metadata': {**meta, 'source_version_id': doc['version_id'],
                                                         'start_char': 0, 'end_char': len(text)}})
    result = service.answer(project, {'query': '价格', 'valid_at': '2025-01-01', 'retrieval_mode': 'keyword'})
    assert result['answer'].count(text) == 1
    citations = {row['citation'] for row in result['uncertain_references']}
    payload = repo.get_provenance_activity(project, result['answer_id'])['payload']
    assert set(payload['cited']) == citations
    assert not payload['warnings']
