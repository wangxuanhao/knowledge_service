"""Stable read budgets and semantic parity; no wall-clock performance assertions."""
from collections import Counter
import json

import pytest

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.answers import stream_events, answer_messages
from knowledge_service.services.provenance import ProvenanceService


@pytest.fixture
def qa(tmp_path):
    service = create_app(tmp_path / 'oag-perf', encoder=HashingEncoder()).state.service
    repo = service.repository
    project = repo.create_project('oag-performance')['id']
    return service, repo, project


def chunks(repo, project, count):
    doc = repo.put_record(project, {'id': 'doc', 'kind': 'document',
        'text': '退款条款：七天内退货，定制商品除外。', 'valid_from': '2020-01-01',
        'metadata': {'applicability': {'platforms': {'mode': 'all'}}}})
    return repo.put_batch(project, [{'id': f'chunk-{i:04}', 'kind': 'chunk',
        'text': doc['text'], 'source_id': doc['id'], 'valid_from': doc['valid_from'],
        'metadata': {**doc['metadata'], 'source_version_id': doc['version_id'],
                     'start_char': 0, 'end_char': len(doc['text'])}}
        for i in range(count)])


def test_simple_prompt_does_not_require_repeating_three_sections():
    prompt = answer_messages('退款期限？', [])[0]['content']
    assert '三层输出' not in prompt
    assert '直接回答' in prompt and '[E编号]' in prompt


def test_native_keyword_search_never_invokes_embedding():
    from types import SimpleNamespace
    from knowledge_service.services.retrieval import RetrievalEngine
    def unavailable_encoder(_texts):
        raise AssertionError('keyword search must not load or call the embedding model')
    encoder = SimpleNamespace(encode=unavailable_encoder)
    repo = SimpleNamespace(current_records_by_version_ids=lambda *_: [])
    store = SimpleNamespace(search_bm25=lambda *args, **kwargs: [])
    result = RetrievalEngine(repo, encoder, store)._search_via_milvus(
        'p', '退款', 'keyword', ['chunk'], 5, {'chunk': 5}, {})
    assert result['_backend'] == 'milvus' and result['active_mode'] == 'keyword'


@pytest.mark.parametrize('count', [10, 100])
def test_batch_trace_matches_single_trace_with_constant_shared_reads(qa, monkeypatch, count):
    _, repo, project = qa
    rows = chunks(repo, project, count)
    provenance = ProvenanceService(repo)
    run = provenance.begin_retrieval(project, {'query': '退款', 'retrieval_mode': 'keyword'})
    evidence = [{'citation': f'E{i+1}', **row} for i, row in enumerate(rows)]
    answer = provenance.complete_retrieval(project, run, {'active_modes': ['keyword']}, evidence)
    citations = [row['citation'] for row in evidence]
    expected = {citation: provenance.trace_answer_evidence(project, answer, citation)
                for citation in citations}
    calls = Counter()
    for name in ('get_provenance_activity', 'list_provenance_edges',
                 'get_record_version', 'records_by_version_ids'):
        original = getattr(repo, name)
        def counted(*args, _name=name, _fn=original, **kw):
            calls[_name] += 1
            return _fn(*args, **kw)
        monkeypatch.setattr(repo, name, counted)
    assert provenance.trace_answer_evidences(project, answer, citations) == expected
    assert calls['get_provenance_activity'] == 2
    assert calls['list_provenance_edges'] == 2
    assert calls['records_by_version_ids'] == 1
    assert calls['get_record_version'] == 0
    # No shared cache may survive a call or leak an answer to another project.
    other = repo.create_project('other')['id']
    with pytest.raises(KeyError):
        provenance.trace_answer_evidences(other, answer, citations)
    with pytest.raises(KeyError):
        provenance.trace_answer_evidences(project, answer, ['E999'])


def test_prepare_summarizes_all_temporal_groups_in_one_batch(qa, monkeypatch):
    service, repo, project = qa
    chunks(repo, project, 2)
    calls = []
    original = getattr(ProvenanceService, 'trace_answer_evidences', None)
    def traced(self, project, answer, citations):
        calls.append(list(citations))
        return original(self, project, answer, citations)
    monkeypatch.setattr(ProvenanceService, 'trace_answer_evidences', traced, raising=False)
    result = service.answer(project, {'query': '退款', 'retrieval_mode': 'keyword',
                                    'valid_at': '2026-10-16', 'k_chunks': 5})
    assert len(calls) == 1
    assert calls[0] == [row['citation'] for row in result['evidence']]


def test_json_and_sse_expose_numeric_stage_timings_without_fake_model_time(qa):
    service, repo, project = qa
    chunks(repo, project, 1)
    request = {'query': '退款', 'retrieval_mode': 'keyword', 'valid_at': '2026-10-16'}
    result = service.answer(project, request)
    events = []
    for block in stream_events(service, project, request):
        name, data = block.strip().split('\n', 1)
        events.append((name[7:], json.loads(data[6:])))
    assert events[-1][0] == 'done'
    for payload in (result, events[-1][1]):
        timing = payload['timings_ms']
        for key in ('retrieval', 'eligibility', 'freeze', 'summaries', 'complete', 'total'):
            assert isinstance(timing[key], (int, float)) and timing[key] >= 0
        assert timing['total'] >= sum(timing[key] for key in ('retrieval', 'eligibility', 'freeze', 'summaries', 'complete')) - 1
        assert 'llm_first_content' not in timing and 'llm' not in timing


def test_reference_matching_happens_before_the_501_row_budget(qa):
    service, repo, project = qa
    # The relevant source sorts after a full budget of unrelated current chunks.
    repo.put_batch(project, [{'id': f'a-noise-{i:04}', 'kind': 'chunk', 'text': '无关设备资料'}
                             for i in range(510)])
    doc = repo.put_record(project, {'id': 'z-source', 'kind': 'document',
        'text': '退款旧条款：九十一天。', 'valid_from': '2020-01-01', 'valid_until': '2021-01-01',
        'metadata': {'applicability': {'platforms': {'mode': 'all'}}}})
    repo.put_record(project, {'id': 'z-relevant', 'kind': 'chunk', 'text': doc['text'],
        'source_id': doc['id'], 'valid_from': doc['valid_from'], 'valid_until': doc['valid_until'],
        'metadata': {**doc['metadata'], 'source_version_id': doc['version_id'],
                     'start_char': 0, 'end_char': len(doc['text'])}})
    result = service.answer(project, {'query': '退款', 'retrieval_mode': 'keyword', 'valid_at': '2026-10-16'})
    assert any(row['id'] == 'z-relevant' for row in result['historical_references'])


def test_source_capture_does_not_reread_shared_documents(qa, monkeypatch):
    _, repo, project = qa
    rows = chunks(repo, project, 10)
    provenance = ProvenanceService(repo)
    run = provenance.begin_retrieval(project, {'query': '退款'})
    calls = []
    original = repo.get_record_version
    def read(*args):
        calls.append(args[-1])
        return original(*args)
    monkeypatch.setattr(repo, 'get_record_version', read)
    answer = provenance.complete_retrieval(project, run, {}, [
        {'citation': f'E{i+1}', **row} for i, row in enumerate(rows)])
    assert answer
    assert not calls, 'exact chunk and document versions should be prefetched once'


def test_document_eligibility_uses_batched_governance_snapshot(qa, monkeypatch):
    from knowledge_service.services.answer_validity import apply_validity, freeze_request
    service, repo, project = qa
    rows = chunks(repo, project, 10)
    calls = []
    original = repo.history
    def read(*args):
        calls.append(args[-1])
        return original(*args)
    monkeypatch.setattr(repo, 'history', read)
    context = apply_validity(service, project, {'hits': rows, 'evidence_rows': rows},
        freeze_request({'query': '退款', 'valid_at': '2026-10-16', 'include_references': False}))
    assert len(context['evidence_rows']) == 10
    assert not calls, 'governance must read only the version visible at knowledge time'


def test_reference_index_rechecks_substrings_and_literal_punctuation(qa):
    _, repo, project = qa
    repo.put_batch(project, [
        {'id': 'false-positive', 'kind': 'chunk', 'text': '退货...货款'},
        {'id': 'match', 'kind': 'chunk', 'text': '退货款以及50%费用'},
        {'id': 'wrong', 'kind': 'chunk', 'text': '50元费用'},
    ])
    for terms, expected in [(['退货款'], ['match']), (['50%'], ['match']), ([], [])]:
        result = repo.query(project, kinds=['chunk'], _reference_mode=True, _reference_terms=terms)
        assert [row['id'] for row in result] == expected


def test_ten_thousand_reference_rows_decode_only_matching_candidates(qa, monkeypatch):
    import time
    import os
    from pathlib import Path
    _, repo, project = qa
    # Set-based synthetic seeding only in the isolated test DB: this benchmarks
    # authority retrieval, not thousands of write-side outbox operations.
    repo.put_record(project, {'id': 'seed', 'kind': 'chunk', 'text': '设备基础资料与操作说明'})
    repo._db.execute('''INSERT INTO record_versions
        (project_id,id,version,version_id,payload,recorded_at,superseded_at,
         platform_mode,platform_values,business_mode,business_values,audience_mode,audience_values,
         scope_identity,valid_from_known,valid_from_epoch_ms,valid_until_known,valid_until_epoch_ms)
        SELECT project_id,'noise-'||i,version,version_id||'-scale-'||i,
            jsonb_set(payload::jsonb,'{id}',to_jsonb('noise-'||i))::text,recorded_at,superseded_at,
            platform_mode,platform_values,business_mode,business_values,audience_mode,audience_values,
            scope_identity,valid_from_known,valid_from_epoch_ms,valid_until_known,valid_until_epoch_ms
        FROM record_versions CROSS JOIN generate_series(1,9999) AS i
        WHERE project_id=? AND id='seed' ''', (project,))
    repo.put_record(project, {'id': 'z-match', 'kind': 'chunk', 'text': '罕见退款条款：九十一天。'})
    decoded = []
    statements = []
    original_execute = repo._db.execute
    def execute(sql, params=()):
        if 'FROM record_versions' in sql and not sql.startswith('EXPLAIN'):
            statements.append((sql, params))
        return original_execute(sql, params)
    monkeypatch.setattr(repo._db, 'execute', execute)
    original = repo._record
    def decode(row, **kwargs):
        decoded.append(row['id'])
        return original(row, **kwargs)
    monkeypatch.setattr(repo, '_record', decode)
    started = time.perf_counter()
    result = repo.query(project, kinds=['chunk'], _reference_mode=True,
                        _reference_terms=['罕见退款'], include_embeddings=False)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert [row['id'] for row in result] == ['z-match']
    assert decoded == ['z-match']
    repo._db.execute('ANALYZE record_versions')
    sql, params = statements[-1]
    plan = original_execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + sql,
                            params).fetchone()['QUERY PLAN'][0]
    report = {'rows': 10001, 'decoded': len(decoded), 'query_ms': round(elapsed_ms, 2), 'explain': plan}
    if os.getenv('KG_OAG_PERF_REPORT'):
        Path(os.environ['KG_OAG_PERF_REPORT']).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
