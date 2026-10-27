import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from knowledge_service.api import create_app
from knowledge_service.domain.search_filter_catalog import default_catalog
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.models import Question
from knowledge_service.repository.scoped_reads import ScopedReads
from knowledge_service.services.answers import answer_messages

AS_OF = '2025-01-01T00:00:00Z'


def _ready_keyword_index(service, monkeypatch):
    """Replace the external index with a synchronously updated fixture index.

    QA still pins a current generation and runs real PostgreSQL authority,
    temporal eligibility, and provenance reads. Only native keyword ranking
    uses the local SQL implementation instead of requiring a Milvus server.
    """
    repo = service.repository

    def selected_index(project, _schema):
        return {'index_version': 1, 'watermark': repo.projections.project_watermark(project),
                'catalog': default_catalog().to_dict(), 'dim': service.encoder.dimension,
                'embedding_model': service.encoder.identity}

    def search(project, query, *, kinds, k, scope, platform_match=None, **options):
        assert options['retrieval_mode'] == 'keyword'
        rows = ScopedReads(repo).ranked_keyword(
            project, {**scope, '_platform_match': platform_match}, query,
            kinds=kinds, limit=min(k, 100))
        return [{'record_id': row['id'], 'version_id': row['version_id'], 'score': row['score']}
                for row in rows]

    monkeypatch.setattr(repo.projections, 'search_index', selected_index)
    monkeypatch.setattr(service, 'milvus_store', SimpleNamespace(available=True, search=search))


@pytest.fixture
def qa(tmp_path, monkeypatch):
    service = create_app(tmp_path / 'rules.sqlite', encoder=HashingEncoder()).state.service
    _ready_keyword_index(service, monkeypatch)
    repo = service.repository
    return service, repo, repo.create_project('rule-documents')['id']

def source(repo, p, name, **dates):
    meta = {'applicability': {'platforms': {'mode': 'all'}}, **dates.pop('metadata', {})}
    doc = repo.put_record(p, {'id': 'doc-' + name, 'kind': 'document', 'text': '退款条款 ' + name, 'metadata': meta, **dates})
    return repo.put_record(p, {'id': name, 'kind': 'chunk', 'source_id': doc['id'], 'text': doc['text'], 'metadata': {**meta, 'source_version_id': doc['version_id'], 'start_char': 0, 'end_char': len(doc['text'])}, **dates})

def request(**kw):
    return {'query': '退款条款', 'retrieval_mode': 'keyword', 'valid_at': AS_OF, 'hops': 0, **kw}

def test_question_reference_budget():
    assert Question(query='规则').include_references is True
    with pytest.raises(ValueError):
        Question(query='规则', reference_k=21)

@pytest.mark.parametrize('dates,status,prefix', [
    ({'valid_from': '2020-01-01'}, 'effective', 'E'),
    ({'valid_from': '2020-01-01', 'valid_until': AS_OF}, 'expired', 'R'),
    ({'valid_from': '2030-01-01'}, 'not_yet_effective', 'F'),
    ({}, 'unknown', 'U'),
    ({'valid_from': '2020-01-01', 'metadata': {'effective_time_source': 'published_at', 'status': 'ready'}}, 'unknown', 'U'),
])
def test_temporal_buckets_and_frozen_sources(qa, dates, status, prefix):
    service, repo, p = qa
    row = source(repo, p, 'rule', **dates)
    result = service.answer(p, request())
    key = {'E': 'evidence', 'R': 'historical_references', 'F': 'future_references', 'U': 'uncertain_references'}[prefix]
    item = result[key][0]
    assert item['citation'] == prefix + '1'
    assert item['validity_status'] == status
    assert item['answer_eligible'] is (prefix == 'E')
    assert item['version_id'] == row['version_id']
    assert item['sources'][0]['document_id'] == 'doc-rule'
    assert result['as_of'].startswith('2025-01-01')
    assert result['known_at'] > result['as_of']
    prompt = json.dumps(answer_messages('退款', result['evidence']), ensure_ascii=False)
    if prefix != 'E':
        assert '退款条款 rule' not in prompt
    if prefix == 'U':
        assert '有效性待核对' in result['answer']
    if prefix in 'RF':
        assert '[R1]' not in result['answer'] and '[F1]' not in result['answer']

def test_reference_switch_does_not_change_search(qa):
    service, repo, p = qa
    source(repo, p, 'expired', valid_until=AS_OF)
    assert not service.search(p, request())['hits']
    assert service.answer(p, request(include_references=False))['historical_references'] == []

def test_prompt_never_accepts_ineligible_rows():
    rows = [{'citation': 'R1', 'text': 'EXPIRED SECRET', 'usage': 'reference', 'answer_eligible': False}]
    assert 'EXPIRED SECRET' not in json.dumps(answer_messages('退款', rows))

def test_unknown_direct_quote_survives_disabled_references(qa):
    service, repo, p = qa
    source(repo, p, 'undated', metadata={'business_note': '保留完整业务 metadata'})
    result = service.answer(p, request(include_references=False))
    assert result['evidence'] == []
    assert result['uncertain_references'][0]['metadata']['business_note'] == '保留完整业务 metadata'
    assert '[U1]' in result['answer'] and '有效性待核对' in result['answer']

def test_source_expiry_cannot_be_restored_by_effective_chunk(qa):
    service, repo, p = qa
    row = source(repo, p, 'old-source', valid_from='2020-01-01', valid_until=AS_OF)
    repo.put_record(p, {**{k: v for k, v in row.items() if k not in {'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}}, 'valid_until': None})
    result = service.answer(p, request())
    assert result['evidence'] == []
    assert result['historical_references'][0]['validity_basis'] == 'source_validity'
    assert '退款条款 old-source' not in result['answer']

def test_json_and_sse_share_temporal_groups_and_final_answer(qa):
    from knowledge_service.services.answers import stream_events
    service, repo, p = qa
    source(repo, p, 'unknown')
    result = service.answer(p, request())
    events = [event.split('\ndata: ', 1) for event in stream_events(service, p, request())]
    payloads = {name.removeprefix('event: '): json.loads(payload) for name, payload in events}
    assert payloads['evidence']['uncertain_references'][0]['citation'] == 'U1'
    assert payloads['done']['answer'] == result['answer']
    assert payloads['done']['provenance_complete']

def test_literal_citations_in_unknown_document_are_quoted_not_audited(qa):
    service, repo, p = qa
    row = source(repo, p, 'literal')
    repo.put_record(p, {**{k: v for k, v in row.items() if k not in {'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}}, 'text': '退款条款 原文包含 [R1] 和 [U99]'})
    result = service.answer(p, request(include_references=False))
    activity = repo.get_provenance_activity(p, result['answer_id'])
    assert activity['payload']['warnings'] == []
    assert activity['payload']['cited'] == ['U1']

def test_expired_chunk_can_trace_authorized_effective_document(qa):
    service, repo, p = qa
    row = source(repo, p, 'chunk-expired', valid_from='2020-01-01')
    repo.put_record(p, {**{k: v for k, v in row.items() if k not in {
        'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}}, 'valid_until': AS_OF})
    result = service.answer(p, request())
    assert result['historical_references'][0]['sources'][0]['document_id'] == 'doc-chunk-expired'

def test_immutable_source_receipt_survives_system_supersession(qa):
    service, repo, p = qa
    source(repo, p, 'receipt', valid_from='2020-01-01')
    repo.put_record(p, {'id': 'doc-receipt', 'kind': 'document', 'text': 'ready receipt',
                        'valid_from': '2020-01-01', 'metadata': {'applicability': {'platforms': {'mode': 'all'}}}})
    result = service.answer(p, request())
    assert result['evidence'][0]['id'] == 'receipt'
    assert result['evidence'][0]['sources'][0]['document_id'] == 'doc-receipt'

def test_chinese_question_retrieves_expired_topic_reference(qa):
    service, repo, p = qa
    source(repo, p, 'old', valid_from='2020-01-01', valid_until=AS_OF)
    result = service.answer(p, request(query='退款条款是什么'))
    assert result['historical_references'][0]['id'] == 'old'

def test_document_only_ingest_with_explicit_date_is_effective_evidence(qa):
    from knowledge_service.models import Ingest
    service, repo, p = qa
    service.ingest(p, Ingest(title='退款规则', text='退款条款约定七天内办理。', extraction_mode='documents',
                            valid_from='2020-01-01', metadata={'applicability': {'platforms': {'mode': 'all'}}}).model_dump())
    result = service.answer(p, request())
    assert result['evidence']
    assert result['evidence'][0]['source_status'] == 'complete'

def test_known_document_expiry_revision_controls_old_immutable_text(qa):
    service, repo, p = qa
    source(repo, p, 'revised', valid_from='2020-01-01')
    repo.put_record(p, {'id': 'doc-revised', 'kind': 'document', 'text': 'later document',
                        'valid_from': '2020-01-01', 'valid_until': AS_OF,
                        'metadata': {'applicability': {'platforms': {'mode': 'all'}}}})
    result = service.answer(p, request())
    assert result['evidence'] == []
    assert result['historical_references'][0]['sources'][0]['quote'] == '退款条款 revised'

def test_undated_chunk_with_expired_source_is_reference_not_quote(qa):
    service, repo, p = qa
    row = source(repo, p, 'source-expired', valid_until=AS_OF)
    repo.put_record(p, {**{k: v for k, v in row.items() if k not in {
        'project_id', 'version', 'version_id', 'recorded_at', 'superseded_at'}}, 'valid_until': None})
    result = service.answer(p, request())
    assert result['uncertain_references'] == []
    assert result['historical_references'][0]['id'] == 'source-expired'
    hidden = service.answer(p, request(include_references=False))
    assert hidden['historical_references'] == hidden['future_references'] == hidden['uncertain_references'] == []
    assert hidden['evidence'] == []

def test_prompt_requires_source_supported_comparison_dimensions():
    prompt = answer_messages('比较平台', [])[0]['content']
    assert '起算点' in prompt and '例外' in prompt
    assert '未检索到' in prompt and '工作日' in prompt
    assert '总数' in prompt and '合规' in prompt

@pytest.mark.parametrize('invalid_reason', ['expired', 'other_platform', 'later_mapping', 'later_approval'])
def test_occurrences_are_checked_individually_before_fact_support(invalid_reason):
    from types import SimpleNamespace
    from knowledge_service.services.answer_validity import classify
    from knowledge_service.core.time import normalize_time
    app = lambda platform: {'applicability': {'platforms': {'mode': 'specific', 'values': [platform]}}}
    doc = {'id': 'doc', 'version_id': 'dv', 'version': 1, 'kind': 'document',
           'valid_from': '2020-01-01', 'metadata': {'applicability': {'platforms': {'mode': 'all'}}}}
    good = {'id': 'good', 'version_id': 'gv', 'kind': 'chunk', 'source_id': 'doc',
            'valid_from': '2020-01-01', 'metadata': {**app('美团'), 'source_version_id': 'dv'}}
    bad = {**good, 'id': 'bad', 'version_id': 'bv', 'metadata': dict(good['metadata'])}
    if invalid_reason == 'expired':
        bad['valid_until'] = AS_OF
    elif invalid_reason == 'other_platform':
        bad['metadata'] = {**app('京东'), 'source_version_id': 'dv'}
    mappings = [{'assertion_id': name, 'assertion_event_id': name + '-event'} for name in ('good', 'bad')]
    if invalid_reason == 'later_mapping':
        mappings[1]['created_at'] = '2030-01-01'
    assertions = {name: {'document_id': 'doc', 'document_version_id': 'dv', 'chunk_id': name}
                  for name in ('good', 'bad')}
    repo = SimpleNamespace(
        get_record_version=lambda *_: doc,
        history=lambda _p, record_id: {'doc': [doc], 'good': [good], 'bad': [bad]}[record_id],
        list_record_version_assertions=lambda *_args, **_kwargs: mappings,
        get_assertion=lambda _p, assertion_id: assertions[assertion_id],
        list_assertion_events=lambda _p, assertion_id: [{'id': assertion_id + '-event',
            'created_at': '2030-01-01' if invalid_reason == 'later_approval' and assertion_id == 'bad' else '2020-01-01'}],
    )
    row = {'id': 'entity', 'version_id': 'ev', 'kind': 'entity', 'valid_from': '2020-01-01',
           'query_platform': '美团', 'metadata': app('美团')}
    result = classify(SimpleNamespace(repository=repo), 'p', row, {
        'valid_at': normalize_time(AS_OF), 'known_at': normalize_time('2026-01-01'),
        'applicability': {'platforms': {'mode': 'specific', 'values': ['美团', '京东']}}})
    assert result['answer_eligible']
    assert result['_allowed_assertion_ids'] == ['good']
    assert result['_allowed_chunk_version_ids'] == ['gv']

@pytest.mark.parametrize('invisible_reason', ['recorded_after_snapshot', 'wrong_platform', 'superseded_before_snapshot'])
def test_invisible_direct_rows_are_absent_from_every_public_context_list(invisible_reason):
    from types import SimpleNamespace
    from knowledge_service.services.answer_validity import apply_validity
    from knowledge_service.core.time import normalize_time
    row = {'id': 'hidden', 'version_id': 'hv', 'kind': 'chunk', 'text': 'HIDDEN_ROW_TEXT',
           'valid_from': '2020-01-01', 'recorded_at': normalize_time('2020-01-01'),
           'query_platform': '美团', 'source_scope': 'specific', 'metadata': {'applicability': {
               'platforms': {'mode': 'specific', 'values': ['美团']}}}}
    if invisible_reason == 'recorded_after_snapshot':
        row['recorded_at'] = normalize_time('2030-01-01')
    elif invisible_reason == 'wrong_platform':
        row['metadata']['applicability']['platforms']['values'] = ['京东']
    else:
        row['superseded_at'] = normalize_time('2024-01-01')
    array_keys = ('hits', 'context_chunks', 'evidence_rows', 'structure_pending_rows', 'unknown_scope_rows')
    context = {key: [dict(row)] for key in array_keys}
    context['platform_groups'] = [{'platform': '美团', **{key: [dict(row)] for key in array_keys}}]
    result = apply_validity(SimpleNamespace(repository=SimpleNamespace()), 'p', context, {
        'valid_at': normalize_time(AS_OF), 'known_at': normalize_time('2026-01-01'),
        'include_references': False, 'applicability': {'platforms': {'mode': 'specific', 'values': ['美团', '京东']}}})
    assert 'HIDDEN_ROW_TEXT' not in json.dumps(result)
    for key in array_keys:
        assert result[key] == []
        assert result['platform_groups'][0][key] == []
    assert result['uncertain_references'] == []

def test_common_evidence_never_populates_unknown_scope_group():
    from types import SimpleNamespace
    from knowledge_service.services.answer_validity import apply_validity
    from knowledge_service.core.time import normalize_time
    doc = {'id': 'doc', 'version_id': 'dv', 'version': 1, 'kind': 'document',
           'valid_from': '2020-01-01', 'metadata': {'applicability': {'platforms': {'mode': 'all'}}}}
    row = {'id': 'common', 'version_id': 'cv', 'kind': 'chunk', 'text': 'COMMON_RULE',
           'source_id': 'doc', 'valid_from': '2020-01-01', 'query_platform': None, 'source_scope': 'all',
           'metadata': {**doc['metadata'], 'source_version_id': 'dv'}}
    repo = SimpleNamespace(get_record_version=lambda *_: doc, history=lambda *_: [doc],
                           records_by_version_ids=lambda *_: [doc],
                           records_at_knowledge_time=lambda *_: [doc])
    context = {'hits': [row], 'evidence_rows': [row], 'platform_groups': [
        {'platform': None, 'source_scope': 'all', 'hits': [row]},
        {'platform': None, 'source_scope': 'unknown', 'hits': []},
        {'platform': None, 'hits': [row]},
    ]}
    result = apply_validity(SimpleNamespace(repository=repo), 'p', context, {
        'valid_at': normalize_time(AS_OF), 'known_at': normalize_time('2026-01-01'),
        'include_references': False})
    groups = result['platform_groups']
    assert [item['id'] for item in groups[0]['evidence_rows']] == ['common']
    assert groups[1]['evidence_rows'] == []
    assert [item['id'] for item in groups[2]['evidence_rows']] == ['common']


@pytest.mark.parametrize('include_references', [False, True])
def test_unscoped_document_qa_keeps_undated_unknown_platform_original(qa, include_references):
    service, repo, p = qa
    source(repo, p, 'unassigned', metadata={
        'applicability': {'platforms': {'mode': 'unknown'}}})
    result = service.answer(p, request(include_references=include_references))
    assert result['evidence'] == []
    quotes = [item for item in result['uncertain_references']
              if item['usage'] == 'qualified_quote']
    assert len(quotes) == 1
    assert quotes[0]['id'] == 'unassigned'
    assert quotes[0]['answer_eligible'] is False
    assert '退款条款 unassigned' in result['answer']
    assert '有效性待核对' in result['answer']
    assert quotes[0]['sources'][0]['document_id'] == 'doc-unassigned'


def test_specific_platform_cannot_quote_unknown_platform_original(qa):
    service, repo, p = qa
    source(repo, p, 'unassigned', metadata={
        'applicability': {'platforms': {'mode': 'unknown'}}})
    result = service.answer(p, request(applicability={'platforms': {'mode': 'specific', 'values': ['美团']}}))
    assert result['evidence'] == []
    assert not any(item['usage'] == 'qualified_quote'
                   for item in result['uncertain_references'])
    assert '退款条款 unassigned' not in result['answer']



@pytest.mark.parametrize('milvus_state', ['missing', 'unavailable'])
def test_hybrid_question_requires_available_milvus(tmp_path, monkeypatch, milvus_state):
    app = create_app(tmp_path / 'hybrid-required.sqlite', encoder=HashingEncoder())
    _ready_keyword_index(app.state.service, monkeypatch)
    with TestClient(app) as client:
        service = app.state.service
        repo = service.repository
        p = repo.create_project('hybrid-required')['id']
        source(repo, p, 'effective-refund', valid_from='2020-01-01')
        service.milvus_store = (
            None if milvus_state == 'missing' else SimpleNamespace(available=False))

        response = client.post(f'/api/projects/{p}/qa', json=request(
            query='退款条款有哪些要求？', retrieval_mode='hybrid'))

    assert response.status_code == 503, response.text
    assert 'Milvus' in response.json()['detail']
    assert 'evidence' not in response.json()


def test_chinese_keyword_question_keeps_effective_original(qa):
    service, repo, p = qa
    row = source(repo, p, 'effective-refund', valid_from='2020-01-01')
    result = service.answer(p, request(query='退款条款有哪些要求？', retrieval_mode='keyword'))
    assert [row['id'] for row in result['evidence']] == ['effective-refund']
    assert '退款条款 effective-refund' in result['answer']
    assert result['evidence'][0]['validity_status'] == 'effective'
    assert result['evidence'][0]['version_id'] == row['version_id']
    assert result['evidence'][0]['sources'][0]['document_id'] == 'doc-effective-refund'



def test_answer_prompt_preserves_business_and_knowledge_query_times():
    messages = answer_messages('当时的退款期限是什么？', [],
                               as_of=AS_OF, known_at='2026-10-16T00:00:00Z')
    payload = json.loads(messages[-1]['content'])
    assert payload['query_context'] == {
        'as_of': AS_OF, 'known_at': '2026-10-16T00:00:00Z'}
    assert '业务判断时点' in messages[0]['content']
    assert '历史' in messages[0]['content']
