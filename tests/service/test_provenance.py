"""Frozen answer lineage contracts, exercised through real SQLite repositories."""
import importlib.util
import json
import sqlite3

import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.formal_writes import FormalFactWriter


def test_provenance_service_module_exists():
    assert importlib.util.find_spec('knowledge_service.services.provenance') is not None


@pytest.fixture
def provenance(tmp_path):
    from knowledge_service.services.provenance import ProvenanceService
    repo = Repository(tmp_path / 'provenance.sqlite')
    project = repo.create_project('provenance')['id']
    yield repo, ProvenanceService(repo), project
    repo.close()


def _source(repo, project, suffix='1'):
    doc = repo.put_record(project, {
        'id': 'doc:' + suffix, 'kind': 'document', 'text': 'before ' + 'evidence ' * 100 + 'after'})
    run = repo.create_ingest_run(project, doc['id'], doc['version_id'])
    chunk = repo.put_record(project, {
        'id': 'chunk:#' + suffix, 'kind': 'chunk', 'text': 'evidence', 'source_id': doc['id'],
        'metadata': {'source_version_id': doc['version_id'], 'run_id': run['id'],
                     'start_char': 7, 'end_char': 15}})
    return doc, chunk, run


def _mapped(repo, project, *, kind='entity', sources=()):
    record = repo.put_record(project, {'id': 'record:#opaque', 'kind': kind, 'text': 'fact'})
    assertions = []
    for index, source in enumerate(sources or [None]):
        item = {'id': 'assertion-' + str(index), 'kind': kind, 'payload': {}, 'actor': 'reviewer'}
        if source:
            doc, chunk, _ = source
            item.update(document_id=doc['id'], document_version_id=doc['version_id'],
                        chunk_id=chunk['id'], quote='evidence', start_char=7, end_char=15)
        assertion = repo.create_assertion(project, item)
        assertion = repo.transition_assertion(project, assertion['id'], 1, 'accepted',
                                              'approved', 'reviewer', canonical_record_id=record['id'])
        event = repo.list_assertion_events(project, assertion['id'])[-1]
        repo.add_record_version_assertion(project, record['id'], record['version_id'],
                                          assertion['id'], event['id'])
        assertions.append(assertion)
    return record, assertions


def _capture(service, project, records, **context):
    run = service.begin_retrieval(project, {'query': '  evidence?  ', 'retrieval_mode': 'hybrid'})
    evidence = [{'citation': f'E{i + 1}', **r} for i, r in enumerate(records)]
    answer = service.complete_retrieval(project, run, {'active_modes': ['keyword'], **context}, evidence)
    return run, answer, evidence


def _nodes(trace, kind):
    return [node for node in trace['nodes'] if node['type'] == kind]


def _codes(trace):
    return {warning['code'] for warning in trace['integrity']['warnings']}


def test_begin_only_records_normalized_public_query_scope_mode(provenance):
    repo, service, project = provenance
    run = service.begin_retrieval(project, {
        'query': '  evidence?  ', 'retrieval_mode': 'keyword', 'kinds': ['entity'],
        'generate': True, 'provider_response': {'secret': 'sentinel'},
        'Authorization': 'sentinel', 'chain_of_thought': 'sentinel'})
    activity = repo.get_provenance_activity(project, run)
    assert run.startswith('rr_')
    assert activity['kind'] == 'retrieval' and activity['status'] == 'running'
    assert set(activity['payload']) == {'query', 'scope', 'requested_mode'}
    assert activity['payload']['query'] == 'evidence?'
    assert activity['payload']['scope']['kinds'] == ['entity']
    other = repo.create_project('other')['id']
    with pytest.raises(KeyError):
        repo.get_provenance_activity(other, run)


@pytest.mark.parametrize('kind', ['entity', 'relation'])
def test_freezes_all_exact_support_branches_and_fixed_contract(provenance, kind):
    repo, service, project = provenance
    sources = [_source(repo, project, '1'), _source(repo, project, '2')]
    record, assertions = _mapped(repo, project, kind=kind, sources=sources)
    run, answer, evidence = _capture(service, project, [record])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert answer.startswith('ans_')
    assert repo.get_provenance_activity(project, run)['status'] == 'completed'
    assert trace['schema_version'] == '1.0'
    assert set(trace) == {'schema_version', 'subject', 'answer', 'retrieval', 'nodes', 'edges', 'integrity'}
    assert trace['subject'] == {'type': 'answer_evidence', 'ref': f'answer:{answer}#E1',
                                'answer_id': answer, 'citation': 'E1'}
    assert trace['answer'] == {'status': 'running', 'citation_status': 'offered'}
    assert trace['retrieval']['run_ref'] == f'retrieval-run:{run}'
    assert trace['integrity'] == {'complete': True, 'warnings': []}
    assert len(_nodes(trace, 'assertion')) == len(_nodes(trace, 'review_event')) == 2
    assert {node['ref'] for node in _nodes(trace, 'chunk_version')} == {
        f"chunk-version:{source[1]['version_id']}" for source in sources}
    for node in trace['nodes']:
        assert set(node) == {'type', 'ref', 'label', 'status', 'occurred_at', 'details'}
    offered = [edge for edge in trace['edges'] if edge['relation'] == 'offered']
    assert len(offered) == 1 and offered[0]['target_ref'] == f"record-version:{record['version_id']}"
    assert trace == service.trace_answer_evidence(project, answer, 'E1')


@pytest.mark.parametrize('relation', ['offered', 'extracted-from'])
def test_partial_source_graph_failure_rolls_back_entire_retrieval(provenance, monkeypatch, relation):
    repo, service, project = provenance
    record, _ = _mapped(repo, project, sources=[_source(repo, project)])
    run = service.begin_retrieval(project, {'query': 'fact'})
    original = repo._provenance._insert_edge

    def fail(project_id, edge, default_created_at=None):
        result = original(project_id, edge, default_created_at)
        if edge['relation'] == relation:
            raise RuntimeError('source insert failed')
        return result

    monkeypatch.setattr(repo._provenance, '_insert_edge', fail)
    with pytest.raises(RuntimeError, match='source insert failed'):
        service.complete_retrieval(project, run, {}, [{'citation': 'E1', **record}])
    assert repo.get_provenance_activity(project, run)['status'] == 'running'
    assert repo.list_provenance_activities(project, kind='answer') == []
    assert repo.list_provenance_edges(project) == []


def test_legacy_without_mapping_never_guesses_current_canonical_support(provenance):
    repo, service, project = provenance
    record, _ = _mapped(repo, project, sources=[_source(repo, project)])
    legacy = repo.put_record(project, {'id': record['id'], 'kind': 'entity', 'text': 'legacy'},
                             expected_version=1)
    _, answer, _ = _capture(service, project, [legacy])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert not trace['integrity']['complete']
    assert 'record_version_support_ambiguous' in _codes(trace)
    assert _nodes(trace, 'assertion') == []


def test_direct_chunk_uses_exact_history_and_no_review_warning(provenance):
    repo, service, project = provenance
    doc, chunk, run = _source(repo, project)
    _, answer, _ = _capture(service, project, [chunk])
    before = service.trace_answer_evidence(project, answer, 'E1')
    assert before['integrity']['complete']
    assert not _nodes(before, 'assertion') and not _nodes(before, 'review_event')
    assert _nodes(before, 'chunk_version')[0]['ref'] == f"chunk-version:{chunk['version_id']}"
    details = _nodes(before, 'document_version')[0]['details']
    assert details['version_id'] == doc['version_id']
    assert details['highlight'] == doc['text'][7:15]
    assert len(details['excerpt_before']) <= 240 and len(details['excerpt_after']) <= 240
    repo.put_record(project, {'id': doc['id'], 'kind': 'document', 'text': 'new document'}, expected_version=1)
    repo.put_record(project, {**{k: chunk[k] for k in ('id', 'kind', 'source_id')},
                              'text': 'new chunk', 'metadata': {}}, expected_version=1)
    repo.create_ingest_run(project, doc['id'], doc['version_id'], retry_of=run['id'])
    assert service.trace_answer_evidence(project, answer, 'E1') == before


@pytest.mark.parametrize('match_count', [0, 2])
def test_assertion_chunk_resolution_requires_unique_exact_document_version(provenance, match_count):
    repo, service, project = provenance
    doc, chunk, run = _source(repo, project)
    record, assertions = _mapped(repo, project, sources=[(doc, chunk, run)])
    if match_count == 0:
        repo._db.execute('DELETE FROM record_versions WHERE version_id=?', (chunk['version_id'],))
        repo._db.commit()
    repo.put_record(project, {'id': chunk['id'], 'kind': 'chunk', 'text': 'current',
                              'source_id': doc['id'], 'metadata': {
                                  'source_version_id': doc['version_id'] if match_count == 2 else 'wrong'}},
                    expected_version=1 if match_count == 2 else None)
    _, answer, _ = _capture(service, project, [record])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert not trace['integrity']['complete']
    assert ('chunk_version_missing' if match_count == 0 else 'chunk_version_ambiguous') in _codes(trace)
    assert not _nodes(trace, 'chunk_version')


def test_ingest_run_snapshot_is_frozen_in_edge_and_node(provenance):
    repo, service, project = provenance
    doc, chunk, run = _source(repo, project)
    _, answer, _ = _capture(service, project, [chunk])
    before = service.trace_answer_evidence(project, answer, 'E1')
    details = _nodes(before, 'ingest_run')[0]['details']
    assert details == {'run_id': run['id'], 'attempt': 1, 'status_at_capture': 'queued',
                       'created_at': run['created_at'], 'updated_at_at_capture': run['updated_at']}
    edge = next(edge for edge in repo.list_provenance_edges(project) if edge['relation'] == 'processed-by')
    assert edge['payload']['status_at_capture'] == 'queued'
    assert edge['payload']['attempt'] == 1
    repo.update_ingest_run(project, run['id'], 1, status='completed')
    assert service.trace_answer_evidence(project, answer, 'E1') == before


@pytest.mark.parametrize('mismatch', ['document', 'version', 'project'])
def test_ingest_run_mismatch_never_selects_current_or_retry_run(provenance, mismatch):
    repo, service, project = provenance
    doc, chunk, run = _source(repo, project)
    if mismatch == 'project':
        foreign = repo.create_project('foreign')['id']
        wrong_run = _source(repo, foreign)[2]
    elif mismatch == 'document':
        wrong_run = _source(repo, project, 'other')[2]
    else:
        updated = repo.put_record(project, {'id': doc['id'], 'kind': 'document', 'text': 'new'}, expected_version=1)
        wrong_run = repo.create_ingest_run(project, doc['id'], updated['version_id'])
    changed = repo.put_record(project, {'id': chunk['id'], 'kind': 'chunk', 'text': 'evidence',
        'source_id': doc['id'], 'metadata': {**chunk['metadata'], 'run_id': wrong_run['id']}}, expected_version=1)
    _, answer, _ = _capture(service, project, [changed])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert not trace['integrity']['complete']
    assert not _nodes(trace, 'ingest_run')
    assert _codes(trace) & {'ingest_run_missing', 'ingest_run_mismatch'}


@pytest.mark.parametrize('kind', ['entity', 'relation'])
def test_manual_formal_record_is_complete_terminal(provenance, kind):
    repo, service, project = provenance
    record = FormalFactWriter(repo).apply('manual_write', project, [], {}, {'records': [
        {'id': 'manual', 'kind': kind, 'type': 'related', 'text': 'manual', 'subject_id': 'a', 'object_id': 'b'}]
    })['accepted_records'][0]
    _, answer, _ = _capture(service, project, [record])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert trace['integrity']['complete']
    assert _nodes(trace, 'record_version')[0]['details']['terminal_reason'] == 'manual_record'


def test_mutating_assertion_and_canonical_binding_cannot_change_frozen_trace(provenance):
    repo, service, project = provenance
    record, assertions = _mapped(repo, project, sources=[_source(repo, project)])
    _, answer, _ = _capture(service, project, [record])
    before = service.trace_answer_evidence(project, answer, 'E1')
    repo.transition_assertion(project, assertions[0]['id'], 2, 'superseded',
                              'withdrawn', 'reviewer', canonical_record_id='merged-target')
    assert service.trace_answer_evidence(project, answer, 'E1') == before


def test_answer_citations_are_strict_deduplicated_and_offered_only(provenance):
    repo, service, project = provenance
    one = _source(repo, project, '1')[1]
    two = _source(repo, project, '2')[1]
    run, answer, evidence = _capture(service, project, [one, two])
    service.complete_answer(project, answer, run, '[E1] [E1] [E99] [e2] [E02] [E0]', 'llm', evidence)
    first = service.trace_answer_evidence(project, answer, 'E1')
    second = service.trace_answer_evidence(project, answer, 'E2')
    assert first['answer'] == {'status': 'completed', 'citation_status': 'cited'}
    assert second['answer']['citation_status'] == 'uncited'
    cites = [edge for edge in repo.list_provenance_edges(project) if edge['relation'] == 'cites']
    assert len(cites) == 1 and cites[0]['source_ref'] == f'answer:{answer}#E1'
    assert 'unknown_citation' in _codes(first)
    assert all(set(warning) == {'code', 'node_ref', 'message'} for warning in first['integrity']['warnings'])


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_failure_preserves_offered_graph_and_filters_public_error(provenance, status):
    repo, service, project = provenance
    run, answer, evidence = _capture(service, project, [_source(repo, project)[1]])
    service.fail_activity(project, answer, status, {
        'type': 'RuntimeError', 'message': 'generation unavailable', 'headers': {'Authorization': 'secret'},
        'provider_response': 'secret', 'chain_of_thought': 'secret'})
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert trace['answer'] == {'status': status, 'citation_status': 'offered'}
    assert 'secret' not in json.dumps(repo.get_provenance_activity(project, answer))
    assert _nodes(trace, 'chunk_version')


def test_untrusted_context_metadata_never_persists_provider_internals(provenance):
    repo, service, project = provenance
    chunk = _source(repo, project)[1]
    chunk.update(headers={'Authorization': 'secret'}, chain_of_thought='secret')
    run, answer, evidence = _capture(service, project, [chunk], provider_response='secret',
        headers={'Authorization': 'secret'}, chain_of_thought='secret')
    service.complete_answer(project, answer, run, '[E1]', 'evidence_only', evidence)
    stored = json.dumps([repo.list_provenance_activities(project), repo.list_provenance_edges(project)])
    assert 'secret' not in stored and 'Authorization' not in stored and 'chain_of_thought' not in stored


def test_trace_is_project_scoped_and_requires_offered_citation(provenance):
    repo, service, project = provenance
    _, answer, _ = _capture(service, project, [_source(repo, project)[1]])
    other = repo.create_project('other')['id']
    for project_id, answer_id, citation in [(other, answer, 'E1'), (project, 'missing', 'E1'),
                                           (project, answer, 'E99'), (project, answer, 'e1')]:
        with pytest.raises(KeyError):
            service.trace_answer_evidence(project_id, answer_id, citation)


def test_answer_completion_retry_is_idempotent(provenance):
    repo, service, project = provenance
    run, answer, evidence = _capture(service, project, [_source(repo, project)[1]])
    service.complete_answer(project, answer, run, '[E1]', 'evidence_only', evidence)
    before = service.trace_answer_evidence(project, answer, 'E1')
    service.complete_answer(project, answer, run, '[E1]', 'evidence_only', evidence)
    assert service.trace_answer_evidence(project, answer, 'E1') == before


@pytest.mark.parametrize('missing', ['document', 'source_version'])
def test_missing_historical_source_reports_partial_graph_without_guessing(provenance, missing):
    repo, service, project = provenance
    doc, chunk, _ = _source(repo, project)
    metadata = dict(chunk['metadata'])
    if missing == 'document':
        metadata['source_version_id'] = 'missing-version'
    else:
        metadata.pop('source_version_id')
    changed = repo.put_record(project, {'id': chunk['id'], 'kind': 'chunk', 'text': 'evidence',
                                       'source_id': doc['id'], 'metadata': metadata}, expected_version=1)
    _, answer, _ = _capture(service, project, [changed])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert not trace['integrity']['complete']
    assert not _nodes(trace, 'document_version')
    assert ('document_version_missing' if missing == 'document' else 'legacy_unversioned_source') in _codes(trace)


def test_empty_evidence_still_links_answer_to_its_retrieval(provenance):
    repo, service, project = provenance
    run, answer, evidence = _capture(service, project, [])
    edges = repo.list_provenance_edges(project, activity_id=answer)
    assert [(edge['source_ref'], edge['relation'], edge['target_ref']) for edge in edges] == [
        (f'answer:{answer}', 'used', f'retrieval-run:{run}')]
    service.complete_answer(project, answer, run, 'No evidence.', 'evidence_only', evidence)
    assert repo.get_provenance_activity(project, answer)['status'] == 'completed'


def test_arbitrarily_large_unknown_citation_remains_a_warning(provenance):
    repo, service, project = provenance
    run, answer, evidence = _capture(service, project, [_source(repo, project)[1]])
    service.complete_answer(project, answer, run, '[E' + '9' * 4500 + ']', 'llm', evidence)
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert 'unknown_citation' in _codes(trace)
    assert trace['answer']['citation_status'] == 'uncited'


def test_all_source_reads_share_the_completion_transaction(provenance, monkeypatch):
    repo, service, project = provenance
    record, _ = _mapped(repo, project, sources=[_source(repo, project)])
    reads = []
    methods = ('get_record_version', 'list_record_version_assertions', 'get_assertion',
               'list_assertion_events', 'history', 'get_ingest_run')

    def observe(name, original):
        def read(*args, **kwargs):
            assert repo._db.in_transaction, f'{name} escaped the completion transaction'
            reads.append(name)
            return original(*args, **kwargs)
        return read

    for name in methods:
        monkeypatch.setattr(repo, name, observe(name, getattr(repo, name)))
    run, answer, _ = _capture(service, project, [record])
    assert set(reads) == set(methods)
    assert not repo._db.in_transaction
    assert repo.get_provenance_activity(project, run)['status'] == 'completed'
    assert service.trace_answer_evidence(project, answer, 'E1')['integrity']['complete']


def test_same_answer_cannot_capture_mixed_ingest_states(provenance, monkeypatch):
    repo, service, project = provenance
    _, chunk, ingest = _source(repo, project)
    database = repo._db.execute('PRAGMA database_list').fetchone()[2]
    original = repo.get_ingest_run
    observed = []
    writer_blocked = []

    def concurrent_change(project_id, run_id):
        snapshot = original(project_id, run_id)
        observed.append(snapshot['status'])
        if len(observed) == 1:
            connection = sqlite3.connect(database, timeout=0.01)
            try:
                with connection:
                    connection.execute("UPDATE ingest_runs SET status='completed' WHERE id=?", (ingest['id'],))
                writer_blocked.append(False)
            except sqlite3.OperationalError as exc:
                assert 'locked' in str(exc)
                writer_blocked.append(True)
            finally:
                connection.close()
        return snapshot

    monkeypatch.setattr(repo, 'get_ingest_run', concurrent_change)
    _, answer, _ = _capture(service, project, [chunk, chunk])
    assert observed == ['queued', 'queued']
    assert writer_blocked == [True]
    assert {_nodes(service.trace_answer_evidence(project, answer, citation), 'ingest_run')[0]
            ['details']['status_at_capture'] for citation in ('E1', 'E2')} == {'queued'}
    # The competing update may proceed once the capture/commit boundary ends.
    repo.update_ingest_run(project, ingest['id'], 1, status='completed')
    assert {_nodes(service.trace_answer_evidence(project, answer, citation), 'ingest_run')[0]
            ['details']['status_at_capture'] for citation in ('E1', 'E2')} == {'queued'}


def test_retrieval_errors_are_bounded_public_reason_summaries(provenance):
    repo, service, project = provenance
    run, _, _ = _capture(service, project, [_source(repo, project)[1]], degraded=True,
        retrieval_errors={
            'semantic': 'TimeoutError: Authorization: Bearer private-token ' + 'raw response ' * 100,
            'keyword': {'error': 'provider response secret', 'api_key': 'private-key'},
            'untrusted-header': 'secret',
        })
    payload = repo.get_provenance_activity(project, run)['payload']
    assert payload['degraded'] is True
    reasons = payload['retrieval_errors']
    assert set(reasons) == {'semantic', 'keyword'}
    assert reasons['semantic'] == '检索后端请求超时。'
    assert reasons['keyword'] == '检索后端不可用。'
    assert all(isinstance(reason, str) and len(reason) <= 240 for reason in reasons.values())
    assert not any(value in json.dumps(payload) for value in ('private-token', 'private-key', 'raw response', 'secret'))


@pytest.mark.parametrize('raw, summary', [
    ('语义索引尚未就绪，请先构建语义索引', '语义索引尚未就绪。'),
    ('嵌入模型与已存向量不一致；请显式重新编码记录', '嵌入模型与已存向量不一致。'),
    ('嵌入维度不匹配', '嵌入向量维度不匹配。'),
    ('ConnectionError: secret host details', '检索后端连接失败。'),
])
def test_retrieval_error_summaries_preserve_public_failure_category(provenance, raw, summary):
    repo, service, project = provenance
    run, _, _ = _capture(service, project, [], degraded=True, retrieval_errors={'semantic': raw})
    assert repo.get_provenance_activity(project, run)['payload']['retrieval_errors'] == {'semantic': summary}


@pytest.mark.parametrize('start, end', [
    ({'Authorization': 'REVIEW_SENTINEL'}, 15), (7, {'api_key': 'REVIEW_SENTINEL'}),
    (True, 15), (7, False), (-1, 15), (15, 7), (7, 7), ('7', 15), (7, 9000),
])
def test_untrusted_chunk_positions_are_never_frozen(provenance, start, end):
    repo, service, project = provenance
    doc, chunk, _ = _source(repo, project)
    malicious = repo.put_record(project, {'id': chunk['id'], 'kind': 'chunk', 'text': 'evidence',
        'source_id': doc['id'], 'metadata': {**chunk['metadata'], 'start_char': start, 'end_char': end}},
        expected_version=1)
    _, answer, _ = _capture(service, project, [malicious])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    details = _nodes(trace, 'chunk_version')[0]['details']
    assert details['start_char'] is None and details['end_char'] is None
    assert 'source_span_invalid' in _codes(trace)
    stored = json.dumps([trace, repo.list_provenance_activities(project), repo.list_provenance_edges(project)])
    assert 'REVIEW_SENTINEL' not in stored and 'Authorization' not in stored and 'api_key' not in stored


@pytest.mark.parametrize('field', ['source_version_id', 'run_id'])
@pytest.mark.parametrize('value', [{'Authorization': 'REVIEW_SENTINEL'}, ['REVIEW_SENTINEL'], True, 1, '', '   '])
def test_untrusted_source_identifiers_warn_without_entering_sqlite(provenance, field, value):
    repo, service, project = provenance
    doc, chunk, _ = _source(repo, project)
    malicious = repo.put_record(project, {'id': chunk['id'], 'kind': 'chunk', 'text': 'evidence',
        'source_id': doc['id'], 'metadata': {**chunk['metadata'], field: value}}, expected_version=1)
    run, answer, _ = _capture(service, project, [malicious])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert repo.get_provenance_activity(project, run)['status'] == 'completed'
    assert ('source_version_invalid' if field == 'source_version_id' else 'ingest_run_invalid') in _codes(trace)
    assert not _nodes(trace, 'document_version' if field == 'source_version_id' else 'ingest_run')
    stored = json.dumps([trace, repo.list_provenance_activities(project), repo.list_provenance_edges(project)])
    assert 'REVIEW_SENTINEL' not in stored and 'Authorization' not in stored
