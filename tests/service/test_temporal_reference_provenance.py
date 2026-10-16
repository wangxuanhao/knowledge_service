"""All temporal namespaces trace the exact frozen, purpose-scoped sources."""
import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.answer_evidence import AnswerEvidence
from knowledge_service.services.provenance import ProvenanceService


def test_trace_route_accepts_only_supported_strict_namespaces():
    from knowledge_service.api.provenance import _CITATION
    assert all(_CITATION.fullmatch(value) for value in ['E1', 'U1', 'R2', 'F3'])
    assert not any(_CITATION.fullmatch(value) for value in ['P1', 'U01', 'R0', 'f1', 'F1\n'])


@pytest.fixture
def temporal_provenance(tmp_path):
    repo = Repository(tmp_path / 'temporal-provenance.sqlite')
    project = repo.create_project('temporal-provenance')['id']
    yield repo, ProvenanceService(repo), project
    repo.close()


def source(repo, project, suffix, **dates):
    doc = repo.put_record(project, {
        'id': 'doc:' + suffix, 'kind': 'document', 'text': '文档条款' + suffix,
        'metadata': {'title': suffix, 'applicability': {'platforms': ['甲']},
                     'published_at': '2020-01-01', **dates.pop('metadata', {})}, **dates})
    chunk = repo.put_record(project, {
        'id': 'chunk:' + suffix, 'kind': 'chunk', 'text': doc['text'], 'source_id': doc['id'],
        'metadata': {'source_version_id': doc['version_id'], 'start_char': 0,
                     'end_char': len(doc['text'])}})
    return doc, chunk


def capture(service, project, evidence):
    run = service.begin_retrieval(project, {'query': '条款'})
    answer = service.complete_retrieval(project, run, {'active_modes': ['keyword']}, evidence)
    return run, answer


def test_all_temporal_namespaces_trace_frozen_versions_and_purposes(temporal_provenance):
    repo, service, project = temporal_provenance
    rows = []
    for citation, status, usage in [('E1', 'effective', 'effective_basis'),
                                  ('U1', 'unknown', 'qualified_quote'),
                                  ('R1', 'expired', 'reference'),
                                  ('F1', 'not_yet_effective', 'reference')]:
        doc, chunk = source(repo, project, citation, valid_from='2020-01-01',
                            valid_until='2027-01-01')
        rows.append({**chunk, 'citation': citation, 'validity_status': status,
                     'validity_basis': 'explicit', 'answer_eligible': citation == 'E1',
                     'usage': usage, 'exclusion_reasons': [], 'source_eligibility': [{
                         'document_version_id': doc['version_id'],
                         'governance_version_id': doc['version_id'], 'validity_status': status,
                         'valid_from': doc['valid_from'], 'valid_until': doc['valid_until'],
                         'effective_time_source': 'explicit',
                         'untrusted_extra': 'not business audit'}]})
    run, answer = capture(service, project, rows)
    before = [service.trace_answer_evidence(project, answer, row['citation']) for row in rows]
    for row in rows:
        repo.put_record(project, {'id': row['source_id'], 'kind': 'document', 'text': '替换正文'})
    assert before == [service.trace_answer_evidence(project, answer, row['citation']) for row in rows]
    summaries = AnswerEvidence(service).summaries(project, answer, rows)
    for row, summary in zip(rows, summaries):
        assert summary['usage'] == row['usage']
        assert summary['sources'][0]['valid_from'].startswith('2020-01-01T00:00:00')
        assert summary['sources'][0]['usage'] == row['usage']
        assert summary['sources'][0]['document_version_id']
        assert summary['source_eligibility'] == [{key: value for key, value in
            row['source_eligibility'][0].items() if key != 'untrusted_extra'}]


def test_answer_completion_only_records_effective_and_qualified_citations(temporal_provenance):
    repo, service, project = temporal_provenance
    doc, chunk = source(repo, project, 'rules')
    rows = [{**chunk, 'citation': prefix + '1', 'usage': usage,
             'answer_eligible': prefix == 'E', 'validity_status': status}
            for prefix, usage, status in [('E', 'effective_basis', 'effective'),
                                         ('U', 'qualified_quote', 'unknown'),
                                         ('R', 'reference', 'expired'),
                                         ('F', 'reference', 'not_yet_effective')]]
    run, answer = capture(service, project, rows)
    service.complete_answer(project, answer, run,
                            '[E1]\n文档表述（有效性待核对，不作为已确认有效规则依据）\n'
                            '[U1] 文档写明：条款\n[R1] [F1] [E99] [U99]', 'llm', rows)
    payload = repo.get_provenance_activity(project, answer)['payload']
    assert payload['cited'] == ['E1', 'U1']
    assert {w['code'] for w in payload['warnings']} == {'unknown_citation', 'ineligible_citation'}
    assert service.trace_answer_evidence(project, answer, 'R1')['answer']['citation_status'] == 'uncited'


def test_entity_capture_excludes_disallowed_assertion_before_freezing_text(temporal_provenance):
    repo, service, project = temporal_provenance
    current, current_chunk = source(repo, project, 'current')
    expired, expired_chunk = source(repo, project, 'expired')
    entity = repo.put_record(project, {'id': 'entity', 'kind': 'entity', 'text': '规则主体'})
    for doc, chunk in [(current, current_chunk), (expired, expired_chunk)]:
        assertion = repo.create_assertion(project, {
            'id': 'assertion:' + doc['id'], 'kind': 'entity', 'payload': {}, 'document_id': doc['id'],
            'document_version_id': doc['version_id'], 'chunk_id': chunk['id'], 'quote': chunk['text']})
        assertion = repo.transition_assertion(project, assertion['id'], 1, 'accepted',
                                              'approved', 'reviewer', canonical_record_id=entity['id'])
        event = repo.list_assertion_events(project, assertion['id'])[-1]
        repo.add_record_version_assertion(project, entity['id'], entity['version_id'],
                                          assertion['id'], event['id'])
    row = {**entity, 'citation': 'E1', '_allowed_source_version_ids': [current['version_id']]}
    _, answer = capture(service, project, [row])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert [n['details']['document_id'] for n in trace['nodes']
            if n['type'] == 'document_version'] == [current['id']]
    assert all(expired_chunk['text'] not in str(n) for n in trace['nodes'])


@pytest.mark.parametrize('allowed_assertions', [['current'], []])
def test_same_document_assertion_allowlist_is_applied_before_capture(
        temporal_provenance, allowed_assertions):
    repo, service, project = temporal_provenance
    text = 'current clause; expired clause'
    doc = repo.put_record(project, {'id': 'one-doc', 'kind': 'document', 'text': text})
    entity = repo.put_record(project, {'id': 'entity', 'kind': 'entity', 'text': 'shared entity'})
    for name, start, end in [('current', 0, 14), ('expired', 16, len(text))]:
        chunk = repo.put_record(project, {
            'id': 'chunk:' + name, 'kind': 'chunk', 'text': text[start:end], 'source_id': doc['id'],
            'metadata': {'source_version_id': doc['version_id'], 'start_char': start, 'end_char': end}})
        assertion = repo.create_assertion(project, {
            'id': name, 'kind': 'entity', 'payload': {}, 'document_id': doc['id'],
            'document_version_id': doc['version_id'], 'chunk_id': chunk['id'],
            'quote': chunk['text'], 'start_char': start, 'end_char': end})
        repo.transition_assertion(project, assertion['id'], 1, 'accepted',
                                  'approved', 'reviewer', canonical_record_id=entity['id'])
        event = repo.list_assertion_events(project, assertion['id'])[-1]
        repo.add_record_version_assertion(project, entity['id'], entity['version_id'],
                                          assertion['id'], event['id'])
    _, answer = capture(service, project, [{
        **entity, 'citation': 'E1', '_allowed_source_version_ids': [doc['version_id']],
        '_allowed_assertion_ids': allowed_assertions}])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert [n['details']['assertion_id'] for n in trace['nodes']
            if n['type'] == 'assertion'] == allowed_assertions
    assert [n['details']['record_id'] for n in trace['nodes']
            if n['type'] == 'chunk_version'] == ['chunk:' + name for name in allowed_assertions]


@pytest.mark.parametrize('kind', ['chunk', 'document'])
def test_empty_source_allowlist_never_freezes_source_text(temporal_provenance, kind):
    repo, service, project = temporal_provenance
    doc, chunk = source(repo, project, 'disallowed')
    row = {**(chunk if kind == 'chunk' else doc), 'citation': 'E1', '_allowed_source_version_ids': []}
    _, answer = capture(service, project, [row])
    trace = service.trace_answer_evidence(project, answer, 'E1')
    assert not [n for n in trace['nodes'] if n['type'] in {'chunk_version', 'document_version', 'source_occurrence'}]


def test_unqualified_unknown_citation_cannot_become_answer_support(temporal_provenance):
    repo, service, project = temporal_provenance
    _, chunk = source(repo, project, 'unknown')
    row = {**chunk, 'citation': 'U1', 'validity_status': 'unknown',
           'answer_eligible': False, 'usage': 'qualified_quote'}
    run, answer = capture(service, project, [row])
    service.complete_answer(project, answer, run, '当前规则要求如此 [U1]', 'llm', [row])
    payload = repo.get_provenance_activity(project, answer)['payload']
    assert payload['cited'] == []
    assert any(w['code'] == 'ineligible_citation' for w in payload['warnings'])


def test_final_safe_answer_retains_sanitized_draft_citation_audit(temporal_provenance):
    repo, service, project = temporal_provenance
    _, chunk = source(repo, project, 'audit')
    row = {**chunk, 'citation': 'E1'}
    run, answer = capture(service, project, [row])
    service.complete_answer(project, answer, run, 'safe fallback [E1]', 'evidence_only', [row],
                            eligibility_audit={'passed': False, 'invalid_citations': ['R1'],
                                               'untrusted_extra': 'discard'})
    assert repo.get_provenance_activity(project, answer)['payload']['eligibility_audit'] == {
        'passed': False, 'invalid_citations': ['R1']}
