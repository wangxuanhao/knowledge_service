"""Frozen answer lineage contracts, exercised through real SQLite repositories."""
import asyncio
import importlib.util
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.formal_writes import FormalFactWriter


def test_provenance_service_module_exists():
    assert importlib.util.find_spec('knowledge_service.services.provenance') is not None


@pytest.fixture
def provenance_api(tmp_path):
    from fastapi.testclient import TestClient
    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder
    from knowledge_service.services.provenance import ProvenanceService
    app = create_app(tmp_path / 'api.sqlite', encoder=HashingEncoder())
    with TestClient(app) as client:
        repo = app.state.service.repository
        project = repo.create_project('provenance-api')['id']
        yield client, repo, ProvenanceService(repo), project


def test_health_advertises_unified_provenance(provenance_api):
    client, _, _, _ = provenance_api
    response = client.get('/api/health')
    assert response.status_code == 200
    assert 'unified_provenance' in response.json()['capabilities']


@pytest.mark.parametrize('status', ['running', 'failed', 'completed'])
def test_provenance_api_returns_frozen_contract_and_answer_status(provenance_api, status):
    client, repo, service, project = provenance_api
    doc, chunk, _ = _source(repo, project)
    record, _ = _mapped(repo, project, sources=[(doc, chunk, None)])
    run, answer, evidence = _capture(service, project, [record])
    if status == 'completed':
        service.complete_answer(project, answer, run, 'fact [E1]', 'llm', evidence)
    elif status == 'failed':
        service.fail_activity(project, answer, 'failed', 'public error')
    repo.put_record(project, {'id': record['id'], 'kind': 'entity', 'text': 'later fact'})
    repo.put_record(project, {'id': doc['id'], 'kind': 'document', 'text': 'later document'})
    before = repo.export_projection(project)
    response = client.get(f'/api/projects/{project}/answers/{answer}/evidence/E1/provenance')
    assert response.status_code == 200, response.text
    trace = response.json()
    assert set(trace) == {'schema_version', 'subject', 'answer', 'retrieval', 'nodes', 'edges', 'integrity'}
    assert trace['schema_version'] == '1.0'
    assert trace['answer'] == {'status': status, 'citation_status': 'cited' if status == 'completed' else 'offered'}
    assert _nodes(trace, 'record_version')[0]['ref'] == f"record-version:{record['version_id']}"
    assert _nodes(trace, 'chunk_version')[0]['ref'] == f"chunk-version:{chunk['version_id']}"
    assert _nodes(trace, 'document_version')[0]['ref'] == f"document-version:{doc['version_id']}"
    assert trace == service.trace_answer_evidence(project, answer, 'E1')
    assert repo.export_projection(project) == before


def test_provenance_api_uncited_missing_citations_and_project_isolation(provenance_api):
    client, repo, service, project = provenance_api
    record, _ = _mapped(repo, project)
    run, answer, evidence = _capture(service, project, [record])
    service.complete_answer(project, answer, run, 'no citation', 'llm', evidence)
    base = f'/api/projects/{project}/answers/{answer}/evidence'
    response = client.get(f'{base}/E1/provenance')
    assert response.status_code == 200
    assert response.json()['answer'] == {'status': 'completed', 'citation_status': 'uncited'}
    for citation in ['E2', 'e1', 'E0', 'E01', 'E-1', 'E1x', 'E1%0A']:
        missing = client.get(f'{base}/{citation}/provenance')
        assert missing.status_code == 404, (citation, missing.text)
        assert missing.json() == {'code': 'provenance_citation_not_found', 'detail': '未找到该答案的证据引用。'}
    other = repo.create_project('other')['id']
    unknown = client.get(f'/api/projects/{project}/answers/unknown/evidence/E1/provenance')
    crossed = client.get(f'/api/projects/{other}/answers/{answer}/evidence/E1/provenance')
    absent_project = client.get(f'/api/projects/unknown/answers/{answer}/evidence/E1/provenance')
    retrieval = client.get(f'/api/projects/{project}/answers/{run}/evidence/E1/provenance')
    for missing in [unknown, crossed, absent_project, retrieval]:
        assert missing.status_code == 404
        assert missing.json() == {'code': 'provenance_answer_not_found', 'detail': '未找到该项目的答案。'}


def test_export_projection_includes_deterministic_project_scoped_provenance(provenance):
    repo, service, project = provenance
    record, _ = _mapped(repo, project, sources=[_source(repo, project)])
    run, answer, evidence = _capture(service, project, [record])
    service.complete_answer(project, answer, run, 'fact [E1]', 'llm', evidence)
    repo.put_record(project, {'id': record['id'], 'kind': 'entity', 'text': 'updated'})
    other = repo.create_project('other')['id']
    other_doc, _, _ = _source(repo, other, 'other')
    _capture(service, other, [other_doc])
    exported = repo.export_projection(project)
    assert set(exported) == {'namespace', 'schema_version', 'project', 'records', 'ontologies', 'governance', 'provenance'}
    assert set(exported['governance']) == {'assertions', 'assertion_events', 'fact_keys', 'ingest_runs', 'resolution_reviews', 'merge_operations'}
    assert [r['version'] for r in exported['records'] if r['id'] == record['id']] == [1, 2]
    assert exported['provenance'] == {
        'record_version_assertions': repo.list_record_version_assertions(project),
        'activities': repo.list_provenance_activities(project),
        'edges': repo.list_provenance_edges(project),
    }
    assert len(exported['provenance']['record_version_assertions']) == 1
    assert len(exported['provenance']['activities']) == 2
    for rows in exported['provenance'].values():
        assert rows and all(row['project_id'] == project for row in rows)
    assert repo.export_projection(other)['provenance']['record_version_assertions'] == []
    assert json.loads(json.dumps(exported, allow_nan=False)) == exported
    assert repo.export_projection(project) == exported


def test_project_export_api_returns_the_complete_repository_snapshot(provenance_api):
    client, repo, service, project = provenance_api
    record, _ = _mapped(repo, project)
    _capture(service, project, [record])
    repo.put_record(project, {'id': record['id'], 'kind': 'entity', 'text': 'updated'})
    other = repo.create_project('other')['id']
    other_doc, _, _ = _source(repo, other, 'other')
    _capture(service, other, [other_doc])
    for project_id in [project, other]:
        response = client.get(f'/api/projects/{project_id}/export')
        assert response.status_code == 200
        assert response.json() == repo.export_projection(project_id)
    assert client.get('/api/projects/missing/export').status_code == 404


def test_delete_project_reports_provenance_counts_and_preserves_other_project(provenance_api):
    client, repo, service, project = provenance_api
    record, _ = _mapped(repo, project, sources=[_source(repo, project)])
    _, answer, _ = _capture(service, project, [record])
    edge_count = len(repo.list_provenance_edges(project))
    other = repo.create_project('keep')['id']
    other_record = repo.put_record(other, {'id': 'other-record', 'kind': 'entity', 'text': 'keep'})
    assertion = repo.create_assertion(other, {'id': 'other-assertion', 'kind': 'entity', 'payload': {}})
    repo.transition_assertion(other, assertion['id'], 1, 'accepted', 'approved', 'reviewer',
                              canonical_record_id=other_record['id'])
    event = repo.list_assertion_events(other, assertion['id'])[-1]
    repo.add_record_version_assertion(other, other_record['id'], other_record['version_id'],
                                      assertion['id'], event['id'])
    _, other_answer, _ = _capture(service, other, [other_record])
    preserved = repo.export_projection(other)
    other_trace = service.trace_answer_evidence(other, other_answer, 'E1')
    response = client.delete(f'/api/projects/{project}')
    assert response.status_code == 200, response.text
    assert response.json()['deleted'] == {
        'records': 3, 'ontologies': 0, 'artifacts': 0, 'assertions': 1,
        'record_version_assertions': 1, 'provenance_activities': 2,
        'provenance_edges': edge_count,
    }
    assert client.get(f'/api/projects/{project}/answers/{answer}/evidence/E1/provenance').status_code == 404
    with pytest.raises(KeyError):
        repo.export_projection(project)
    assert repo.export_projection(other) == preserved
    assert service.trace_answer_evidence(other, other_answer, 'E1') == other_trace


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
                        chunk_id=chunk['id'], quote=chunk['text'],
                        start_char=chunk['metadata']['start_char'],
                        end_char=chunk['metadata']['end_char'])
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


def test_answer_payload_keeps_only_frozen_refs_not_a_second_source_graph(provenance):
    repo, service, project = provenance
    doc, chunk, _ = _source(repo, project)
    record, _ = _mapped(repo, project, sources=[(doc, chunk, None)])
    _, answer, _ = _capture(service, project, [record])

    payload = repo.get_provenance_activity(project, answer)['payload']
    encoded = json.dumps(payload, ensure_ascii=False)

    assert set(payload) == {
        'run_id', 'offered', 'cited', 'warnings', 'answer', 'mode', 'graph_schema'}
    assert payload['graph_schema'] == 'edge-v1'
    assert 'graphs' not in payload
    assert all(value not in encoded for value in (
        doc['text'], chunk['text'], 'excerpt_before', 'excerpt_after', 'highlight', 'quote'))


def test_edge_v0_answer_is_migrated_before_trace_without_losing_sources(provenance):
    repo, service, project = provenance
    document, chunk, ingest = _source(repo, project)
    retrieval_id, answer_id, citation = 'rr_edge_v0', 'ans_edge_v0', 'E1'
    record_ref = f"record-version:{chunk['version_id']}"
    chunk_ref = f"chunk-version:{chunk['version_id']}"
    document_ref = f"document-version:{document['version_id']}"
    run_ref = f"ingest-run:{ingest['id']}"
    citation_ref = f'answer:{answer_id}#{citation}'
    audit = {'rank': 1, 'score': 0.75, 'channel': 'chunk', 'selected': True}
    source = {
        'document_id': document['id'], 'version_id': document['version_id'],
        'version': document['version'], 'title': document['id'],
        'excerpt_before': document['text'][:7],
        'highlight': document['text'][7:15],
        'excerpt_after': document['text'][15:30],
    }
    warning = {'code': 'legacy_warning', 'node_ref': document_ref,
               'message': '历史链路包含已知缺口。'}
    used = {'activity_id': answer_id, 'source_ref': f'answer:{answer_id}',
            'relation': 'used', 'target_ref': f'retrieval-run:{retrieval_id}',
            'ordinal': 0, 'payload': {}}
    considered = {'activity_id': retrieval_id, 'source_ref': f'retrieval-run:{retrieval_id}',
                  'relation': 'considered', 'target_ref': record_ref,
                  'ordinal': 1, 'payload': audit}
    offered = {'activity_id': answer_id, 'source_ref': citation_ref,
               'relation': 'offered', 'target_ref': record_ref,
               'ordinal': 2, 'payload': audit}
    structural = [
        {'activity_id': answer_id, 'source_ref': record_ref, 'relation': 'supported-by',
         'target_ref': chunk_ref, 'ordinal': 3, 'payload': {}},
        {'activity_id': answer_id, 'source_ref': chunk_ref, 'relation': 'sourced-from',
         'target_ref': document_ref, 'ordinal': 4, 'payload': source},
        {'activity_id': answer_id, 'source_ref': document_ref, 'relation': 'processed-by',
         'target_ref': run_ref, 'ordinal': 5, 'payload': {
             'run_id': ingest['id'], 'attempt': ingest['attempt'],
             'status_at_capture': ingest['status'], 'created_at': ingest['created_at'],
             'updated_at_at_capture': ingest['updated_at']}},
    ]
    graph = {'record_ref': record_ref, 'audit': audit, 'warnings': [warning],
             'nodes': [{'type': 'document_version', 'ref': document_ref,
                        'details': source}],
             'edges': [used, considered, offered, *structural]}
    repo.begin_provenance_activity(project, retrieval_id, 'retrieval', {
        'query': 'legacy evidence', 'scope': {}, 'requested_mode': 'keyword',
        'active_modes': ['keyword']})
    repo.complete_retrieval_and_begin_answer(
        project, retrieval_id=retrieval_id, answer_id=answer_id,
        retrieval_payload={'query': 'legacy evidence', 'scope': {},
                           'requested_mode': 'keyword', 'active_modes': ['keyword']},
        answer_payload={'run_id': retrieval_id, 'offered': [citation], 'cited': [],
                        'graphs': {citation: graph}, 'warnings': [],
                        'answer': '', 'mode': None},
        edges=[used, considered, offered, *structural])

    trace = service.trace_answer_evidence(project, answer_id, citation)

    occurrence = _nodes(trace, 'source_occurrence')[0]
    assert occurrence['details']['highlight'] == document['text'][7:15]
    assert warning in trace['integrity']['warnings']
    assert trace['integrity']['complete'] is False
    migrated = repo.get_provenance_activity(project, answer_id)['payload']
    assert migrated['graph_schema'] == 'edge-v1' and 'graphs' not in migrated
    stored = json.dumps([migrated, repo.list_provenance_edges(project)], ensure_ascii=False)
    assert all(value not in stored for value in (
        document['text'], source['highlight'], 'excerpt_before', 'excerpt_after'))


@pytest.mark.parametrize('migration_order', [('first', 'second'), ('second', 'first')])
def test_shared_legacy_retrieval_migrations_preserve_and_isolate_considered_edges(
        provenance, migration_order):
    repo, service, project = provenance
    retrieval_id, citation = 'rr_shared_edge_v0', 'E1'
    sources = {'first': _source(repo, project, 'shared-first'),
               'second': _source(repo, project, 'shared-second')}
    cases, considered_edges = {}, []
    retrieval_payload = {'query': 'shared legacy evidence', 'scope': {},
                         'requested_mode': 'keyword', 'active_modes': ['keyword']}
    repo.begin_provenance_activity(project, retrieval_id, 'retrieval', retrieval_payload)

    for retrieval_ordinal, (name, (document, chunk, ingest)) in enumerate(sources.items()):
        answer_id = f'ans_shared_{name}'
        record_ref = f"record-version:{chunk['version_id']}"
        chunk_ref = f"chunk-version:{chunk['version_id']}"
        document_ref = f"document-version:{document['version_id']}"
        citation_ref = f'answer:{answer_id}#{citation}'
        audit = {'rank': 1, 'score': 0.5, 'channel': 'chunk', 'selected': True}
        source = {'document_id': document['id'], 'version_id': document['version_id'],
                  'version': document['version'], 'title': document['id'],
                  'excerpt_before': document['text'][:7],
                  'highlight': document['text'][7:15],
                  'excerpt_after': document['text'][15:30]}
        considered = {'activity_id': retrieval_id,
                      'source_ref': f'retrieval-run:{retrieval_id}',
                      'relation': 'considered', 'target_ref': record_ref,
                      'ordinal': retrieval_ordinal, 'payload': audit}
        answer_edges = [
            {'activity_id': answer_id, 'source_ref': f'answer:{answer_id}',
             'relation': 'used', 'target_ref': f'retrieval-run:{retrieval_id}',
             'ordinal': 0, 'payload': {}},
            {'activity_id': answer_id, 'source_ref': citation_ref,
             'relation': 'offered', 'target_ref': record_ref,
             'ordinal': 1, 'payload': audit},
            {'activity_id': answer_id, 'source_ref': record_ref,
             'relation': 'supported-by', 'target_ref': chunk_ref,
             'ordinal': 2, 'payload': {}},
            {'activity_id': answer_id, 'source_ref': chunk_ref,
             'relation': 'sourced-from', 'target_ref': document_ref,
             'ordinal': 3, 'payload': source},
            {'activity_id': answer_id, 'source_ref': document_ref,
             'relation': 'processed-by', 'target_ref': f"ingest-run:{ingest['id']}",
             'ordinal': 4, 'payload': {
                 'run_id': ingest['id'], 'attempt': ingest['attempt'],
                 'status_at_capture': ingest['status'], 'created_at': ingest['created_at'],
                 'updated_at_at_capture': ingest['updated_at']}},
        ]
        graph = {'record_ref': record_ref, 'audit': audit, 'warnings': [],
                 'nodes': [], 'edges': [answer_edges[0], considered, *answer_edges[1:]]}
        answer_payload = {'run_id': retrieval_id, 'offered': [citation], 'cited': [],
                          'graphs': {citation: graph}, 'warnings': [],
                          'answer': '', 'mode': None}
        repo.begin_provenance_activity(project, answer_id, 'answer', answer_payload)
        repo.complete_provenance_activity(project, answer_id, answer_payload, answer_edges)
        cases[name] = {'answer_id': answer_id, 'record_ref': record_ref}
        considered_edges.append(considered)
    repo.complete_provenance_activity(
        project, retrieval_id, retrieval_payload, considered_edges)

    traces = {}
    for name in migration_order:
        case = cases[name]
        traces[name] = service.trace_answer_evidence(
            project, case['answer_id'], citation)
        assert traces[name]['integrity']['complete'] is True
        considered = [edge for edge in traces[name]['edges']
                      if edge['relation'] == 'considered']
        assert len(considered) == 1
        assert considered[0]['target_ref'] == case['record_ref']
        assert considered[0]['payload']['answer_id'] == case['answer_id']

    for name, case in cases.items():
        repeated = service.trace_answer_evidence(project, case['answer_id'], citation)
        considered = [edge for edge in repeated['edges'] if edge['relation'] == 'considered']
        assert len(considered) == 1 and considered[0]['target_ref'] == case['record_ref']
    stored = [edge for edge in repo.list_provenance_edges(project, activity_id=retrieval_id)
              if edge['relation'] == 'considered']
    assert {edge['payload'].get('answer_id') for edge in stored
            if edge['payload'].get('answer_id')} == {
        case['answer_id'] for case in cases.values()}


def test_edge_v0_migration_never_deletes_shared_unowned_edge_v1_considered(provenance):
    repo, service, project = provenance
    document, chunk, _ = _source(repo, project, 'mixed-shared')
    retrieval_id, first_answer, legacy_answer, citation = (
        'rr_mixed_shared', 'ans_edge_v1', 'ans_edge_v0_mixed', 'E1')
    record_ref = f"record-version:{chunk['version_id']}"
    chunk_ref = f"chunk-version:{chunk['version_id']}"
    document_ref = f"document-version:{document['version_id']}"
    occurrence_ref = f'source-occurrence:{first_answer}#{citation}:1'
    audit = {'rank': 1, 'score': 0.5, 'channel': 'chunk', 'selected': True}
    unowned = {**audit, 'citation': citation, 'warnings': []}
    retrieval_payload = {'query': 'mixed shared', 'scope': {},
                         'requested_mode': 'keyword', 'active_modes': ['keyword']}
    repo.begin_provenance_activity(project, retrieval_id, 'retrieval', retrieval_payload)
    considered = {'activity_id': retrieval_id,
                  'source_ref': f'retrieval-run:{retrieval_id}',
                  'relation': 'considered', 'target_ref': record_ref,
                  'ordinal': 0, 'payload': unowned}

    first_payload = {'run_id': retrieval_id, 'offered': [citation], 'cited': [],
                     'warnings': [], 'answer': '', 'mode': None,
                     'graph_schema': 'edge-v1'}
    first_edges = [
        {'activity_id': first_answer, 'source_ref': f'answer:{first_answer}',
         'relation': 'used', 'target_ref': f'retrieval-run:{retrieval_id}',
         'ordinal': 0, 'payload': {}},
        {'activity_id': first_answer, 'source_ref': f'answer:{first_answer}#{citation}',
         'relation': 'offered', 'target_ref': record_ref, 'ordinal': 1,
         'payload': unowned},
        {'activity_id': first_answer, 'source_ref': record_ref,
         'relation': 'supported-by', 'target_ref': chunk_ref, 'ordinal': 2,
         'payload': {'citation': citation}},
        {'activity_id': first_answer, 'source_ref': chunk_ref,
         'relation': 'sourced-from', 'target_ref': occurrence_ref, 'ordinal': 3,
         'payload': {'citation': citation, 'start_char': 7, 'end_char': 15}},
        {'activity_id': first_answer, 'source_ref': occurrence_ref,
         'relation': 'sourced-from', 'target_ref': document_ref, 'ordinal': 4,
         'payload': {'citation': citation}},
    ]
    repo.begin_provenance_activity(project, first_answer, 'answer', first_payload)
    repo.complete_provenance_activity(project, first_answer, first_payload, first_edges)

    source = {'document_id': document['id'], 'version_id': document['version_id'],
              'version': document['version'], 'title': document['id'],
              'excerpt_before': document['text'][:7],
              'highlight': document['text'][7:15],
              'excerpt_after': document['text'][15:30]}
    legacy_edges = [
        {'activity_id': legacy_answer, 'source_ref': f'answer:{legacy_answer}',
         'relation': 'used', 'target_ref': f'retrieval-run:{retrieval_id}',
         'ordinal': 0, 'payload': {}},
        {'activity_id': legacy_answer, 'source_ref': f'answer:{legacy_answer}#{citation}',
         'relation': 'offered', 'target_ref': record_ref, 'ordinal': 1,
         'payload': audit},
        {'activity_id': legacy_answer, 'source_ref': record_ref,
         'relation': 'supported-by', 'target_ref': chunk_ref, 'ordinal': 2,
         'payload': {}},
        {'activity_id': legacy_answer, 'source_ref': chunk_ref,
         'relation': 'sourced-from', 'target_ref': document_ref, 'ordinal': 3,
         'payload': source},
    ]
    graph = {'record_ref': record_ref, 'audit': audit, 'warnings': [], 'nodes': [],
             'edges': [legacy_edges[0], considered, *legacy_edges[1:]]}
    legacy_payload = {'run_id': retrieval_id, 'offered': [citation], 'cited': [],
                      'graphs': {citation: graph}, 'warnings': [],
                      'answer': '', 'mode': None}
    repo.begin_provenance_activity(project, legacy_answer, 'answer', legacy_payload)
    repo.complete_provenance_activity(project, legacy_answer, legacy_payload, legacy_edges)
    repo.complete_provenance_activity(project, retrieval_id, retrieval_payload, [considered])

    before = service.trace_answer_evidence(project, first_answer, citation)
    assert len([edge for edge in before['edges'] if edge['relation'] == 'considered']) == 1
    migrated = service.trace_answer_evidence(project, legacy_answer, citation)
    after = service.trace_answer_evidence(project, first_answer, citation)

    assert migrated['integrity']['complete'] is True
    assert after['integrity']['complete'] is True
    assert len([edge for edge in after['edges'] if edge['relation'] == 'considered']) == 1
    stored = [edge for edge in repo.list_provenance_edges(project, activity_id=retrieval_id)
              if edge['relation'] == 'considered']
    assert any('answer_id' not in edge['payload'] for edge in stored)
    assert any(edge['payload'].get('answer_id') == legacy_answer for edge in stored)


def test_missing_considered_edge_marks_trace_incomplete(provenance):
    repo, service, project = provenance
    _, answer, _ = _capture(service, project, [_source(repo, project)[1]])
    run_id = repo.get_provenance_activity(project, answer)['payload']['run_id']
    repo._db.execute(
        "DELETE FROM provenance_edges WHERE project_id=? AND activity_id=? AND relation='considered'",
        (project, run_id))
    repo._db.commit()

    trace = service.trace_answer_evidence(project, answer, 'E1')

    assert trace['integrity']['complete'] is False
    assert {'code': 'retrieval_edge_missing',
            'node_ref': f'retrieval-run:{run_id}',
            'message': '检索运行缺少该答案证据对应的 considered 关联。'} in trace['integrity']['warnings']


def test_same_document_version_preserves_each_source_occurrence(provenance):
    repo, service, project = provenance
    text = 'alpha gap beta tail'
    document = repo.put_record(project, {
        'id': 'doc:shared', 'kind': 'document', 'text': text,
        'metadata': {'title': '共享历史文档'}})
    run = repo.create_ingest_run(project, document['id'], document['version_id'])
    chunks = [repo.put_record(project, {
        'id': f'chunk:shared:{index}', 'kind': 'chunk', 'text': expected,
        'source_id': document['id'], 'metadata': {
            'source_version_id': document['version_id'], 'run_id': run['id'],
            'start_char': start, 'end_char': end}})
        for index, (expected, start, end) in enumerate((('alpha', 0, 5), ('beta', 10, 14)), 1)]
    record, _ = _mapped(repo, project, sources=[
        (document, chunks[0], run), (document, chunks[1], run)])

    _, answer, _ = _capture(service, project, [record])
    trace = service.trace_answer_evidence(project, answer, 'E1')

    occurrences = _nodes(trace, 'source_occurrence')
    assert len(_nodes(trace, 'document_version')) == 1
    assert len(occurrences) == 2
    assert {node['details']['highlight'] for node in occurrences} == {'alpha', 'beta'}
    assert {node['details']['source_content'] for node in occurrences} == {'full_version'}
    assert all(node['details']['version_id'] == document['version_id'] for node in occurrences)
    assert len({node['ref'] for node in occurrences}) == 2


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
    details = _nodes(before, 'source_occurrence')[0]['details']
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


def test_failed_source_resolution_is_not_mislabeled_as_manual(provenance):
    repo, service, project = provenance
    document, chunk, _ = _source(repo, project)
    record, _ = _mapped(repo, project, sources=[(document, chunk, None)])
    repo._db.execute('DELETE FROM record_versions WHERE version_id=?', (chunk['version_id'],))
    repo._db.commit()

    _, answer, _ = _capture(service, project, [record])
    trace = service.trace_answer_evidence(project, answer, 'E1')

    assert 'chunk_version_missing' in _codes(trace)
    assert _nodes(trace, 'record_version')[0]['details']['terminal_reason'] is None


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
    assert any(edge['relation'] == 'cites' for edge in first['edges'])
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
    monkeypatch.undo()
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


def _sse_item(raw):
    name, data = raw.strip().split('\n', 1)
    return name.removeprefix('event: '), json.loads(data.removeprefix('data: '))


def _qa_stream(provenance, monkeypatch, *, empty=False, generate=False):
    from knowledge_service.services import answers
    repo, _, project = provenance
    rows = [] if empty else [_source(repo, project)[1]]
    context = {'hits': rows, 'evidence_rows': rows, 'candidate_count': len(rows),
               'filter_stage': 'before_ranking', 'embedding_model': 'test', 'semantic': False,
               'requested_mode': 'keyword', 'active_modes': ['keyword'], 'degraded': False,
               '_backend': 'local', 'retrieval_errors': {}, 'valid_at': None, 'known_at': None,
               'channels': {'entity': 0, 'chunk': len(rows), 'graph_evidence': 0}}
    monkeypatch.setattr(answers, 'question_context', lambda *args: dict(context))
    stream = answers.stream_events(SimpleNamespace(repository=repo), project,
                                   {'query': 'evidence', 'retrieval_mode': 'keyword', 'generate': generate})
    return stream, context


def _llm_response(monkeypatch, *, text='[E1] [E1] [E99]', error=None):
    from knowledge_service.services import answers
    for key, value in {'KG_LLM_BASE_URL': 'https://provider.invalid',
                       'KG_LLM_API_KEY': 'private-key', 'KG_LLM_MODEL': 'test'}.items():
        monkeypatch.setenv(key, value)

    @contextmanager
    def response(*args, **kwargs):
        if error:
            raise error
        yield SimpleNamespace(raise_for_status=lambda: None, iter_lines=lambda: iter([
            'data: ' + json.dumps({'choices': [{'delta': {'content': text}}]}), 'data: [DONE]']))

    @contextmanager
    def client(*args, **kwargs):
        yield SimpleNamespace(stream=response)

    monkeypatch.setattr(answers.httpx, 'Client', client)


def test_sse_starts_retrieval_before_context_and_marks_retrieval_error(provenance, monkeypatch):
    from knowledge_service.services import answers
    repo, _, project = provenance
    stream, _ = _qa_stream(provenance, monkeypatch)
    observed = []

    def broken_context(*args):
        observed.extend(repo.list_provenance_activities(project))
        raise ValueError('无效查询范围')

    monkeypatch.setattr(answers, 'question_context', broken_context)
    assert [_sse_item(item) for item in stream] == [('error', {'detail': '无效查询范围'})]
    assert len(observed) == 1 and observed[0]['status'] == 'running'
    activity = repo.get_provenance_activity(project, observed[0]['id'])
    assert activity['kind'] == 'retrieval' and activity['status'] == 'failed'
    assert repo.list_provenance_edges(project) == []


@pytest.mark.parametrize('relation', ['offered', 'supported-by'])
def test_sse_retrieval_write_failure_rolls_back_before_evidence(provenance, monkeypatch, relation):
    from knowledge_service.repository.provenance_store import ProvenanceStore
    repo, _, project = provenance
    stream, _ = _qa_stream(provenance, monkeypatch)
    original = ProvenanceStore._insert_edge
    inserted = []

    def broken_insert(self, project_id, edge, *args, **kwargs):
        if edge['relation'] == relation:
            raise sqlite3.OperationalError('injected write failure')
        inserted.append(edge['relation'])
        return original(self, project_id, edge, *args, **kwargs)

    monkeypatch.setattr(ProvenanceStore, '_insert_edge', broken_insert)
    assert [name for name, _ in map(_sse_item, stream)] == ['error']
    assert inserted  # Failure occurs after writes inside the transaction.
    activities = repo.list_provenance_activities(project)
    assert len(activities) == 1 and activities[0]['kind'] == 'retrieval'
    assert activities[0]['status'] == 'failed'
    assert repo.list_provenance_edges(project) == []


def test_sse_evidence_is_committed_and_trace_running_until_done(provenance, monkeypatch):
    repo, service, project = provenance
    stream, context = _qa_stream(provenance, monkeypatch)
    name, payload = _sse_item(next(stream))
    assert name == 'evidence'
    assert {key: payload[key] for key in context if key != 'evidence_rows'} == {
        key: value for key, value in context.items() if key != 'evidence_rows'}
    assert not repo._db.in_transaction
    answer, run = payload['answer_id'], payload['retrieval_run_id']
    row = payload['evidence'][0]
    assert row == {**context['evidence_rows'][0], 'citation': 'E1',
                   'provenance_ref': f'answer:{answer}#E1'}
    assert repo.get_provenance_activity(project, run)['status'] == 'completed'
    assert service.trace_answer_evidence(project, answer, 'E1')['answer'] == {
        'status': 'running', 'citation_status': 'offered'}
    assert _sse_item(next(stream))[0] == 'delta'
    assert service.trace_answer_evidence(project, answer, 'E1')['answer']['status'] == 'running'
    name, done = _sse_item(next(stream))
    assert (name, done) == ('done', {'answer': '[E1] evidence\n\n', 'mode': 'evidence_only',
        'answer_id': answer, 'retrieval_run_id': run, 'provenance_complete': True})
    assert not repo._db.in_transaction
    assert service.trace_answer_evidence(project, answer, 'E1')['answer'] == {
        'status': 'completed', 'citation_status': 'cited'}
    stream.close()
    assert repo.get_provenance_activity(project, answer)['status'] == 'completed'


def test_sse_llm_citations_are_committed_before_done(provenance, monkeypatch):
    repo, service, project = provenance
    _llm_response(monkeypatch)
    stream, _ = _qa_stream(provenance, monkeypatch, generate=True)
    evidence = _sse_item(next(stream))[1]
    assert _sse_item(next(stream)) == ('delta', {'text': '[E1] [E1] [E99]'})
    name, done = _sse_item(next(stream))
    assert name == 'done' and done['mode'] == 'llm' and done['provenance_complete'] is True
    assert done['answer'] == '[E1] [E1] [E99]'
    trace = service.trace_answer_evidence(project, evidence['answer_id'], 'E1')
    assert trace['answer']['status'] == 'completed'
    assert 'unknown_citation' in _codes(trace)
    cites = [edge for edge in repo.list_provenance_edges(project) if edge['relation'] == 'cites']
    assert len(cites) == 1 and cites[0]['source_ref'] == f"answer:{done['answer_id']}#E1"
    stream.close()


def test_sse_answer_commit_failure_never_emits_done(provenance, monkeypatch):
    from knowledge_service.repository.provenance_store import ProvenanceStore
    repo, service, project = provenance
    stream, _ = _qa_stream(provenance, monkeypatch)
    payload = _sse_item(next(stream))[1]
    original = ProvenanceStore._insert_edge

    def broken_cite(self, project_id, edge, *args, **kwargs):
        if edge['relation'] == 'cites':
            raise sqlite3.OperationalError('injected cite failure')
        return original(self, project_id, edge, *args, **kwargs)

    monkeypatch.setattr(ProvenanceStore, '_insert_edge', broken_cite)
    assert [name for name, _ in map(_sse_item, stream)] == ['delta', 'error']
    assert service.trace_answer_evidence(project, payload['answer_id'], 'E1')['answer'] == {
        'status': 'failed', 'citation_status': 'offered'}
    assert not any(edge['relation'] == 'cites' for edge in repo.list_provenance_edges(project))


@pytest.mark.parametrize('after_delta', [False, True])
def test_sse_close_cancels_running_answer_without_error_event(provenance, monkeypatch, after_delta):
    repo, service, project = provenance
    stream, _ = _qa_stream(provenance, monkeypatch)
    payload = _sse_item(next(stream))[1]
    if after_delta:
        next(stream)
    stream.close()  # Yielding an error here would raise RuntimeError: ignored GeneratorExit.
    assert service.trace_answer_evidence(project, payload['answer_id'], 'E1')['answer'] == {
        'status': 'cancelled', 'citation_status': 'offered'}
    assert repo.get_provenance_activity(project, payload['retrieval_run_id'])['status'] == 'completed'


@pytest.mark.parametrize('after_delta', [False, True])
def test_sse_async_cancel_marks_answer_cancelled_and_reraises_original(provenance, monkeypatch, after_delta):
    repo, service, project = provenance
    stream, _ = _qa_stream(provenance, monkeypatch)
    name, payload = _sse_item(next(stream))
    assert name == 'evidence'
    if after_delta:
        assert _sse_item(next(stream))[0] == 'delta'
    cancellation = asyncio.CancelledError('consumer disconnected')
    with pytest.raises(asyncio.CancelledError) as raised:
        stream.throw(cancellation)
    assert raised.value is cancellation
    assert list(stream) == []
    stream.close()
    assert service.trace_answer_evidence(project, payload['answer_id'], 'E1')['answer'] == {
        'status': 'cancelled', 'citation_status': 'offered'}
    assert repo.get_provenance_activity(project, payload['retrieval_run_id'])['status'] == 'completed'
    assert repo.get_provenance_activity(project, payload['answer_id'])['payload']['error'] == {
        'type': 'CancelledError', 'message': '回答流已取消。'}


def test_sse_provider_error_persists_only_public_message_and_type(provenance, monkeypatch, caplog):
    repo, _, project = provenance
    _llm_response(monkeypatch, error=RuntimeError(
        'Authorization: Bearer private-key; full_response=PROVIDER_SECRET'))
    stream, _ = _qa_stream(provenance, monkeypatch, generate=True)
    items = list(map(_sse_item, stream))
    assert [name for name, _ in items] == ['evidence', 'error']
    stored = json.dumps([repo.list_provenance_activities(project), repo.list_provenance_edges(project)])
    assert all(secret not in stored for secret in ('private-key', 'PROVIDER_SECRET', 'Authorization'))
    activity = repo.get_provenance_activity(project, items[0][1]['answer_id'])
    assert activity['status'] == 'failed'
    assert set(activity['payload']['error']) == {'type', 'message'}
    assert activity['payload']['error']['type'] == 'RuntimeError'
    assert caplog.records[-1].exc_info is not None


def test_sse_empty_evidence_commits_empty_offered_and_completes(provenance, monkeypatch):
    repo, _, project = provenance
    stream, _ = _qa_stream(provenance, monkeypatch, empty=True, generate=True)
    items = list(map(_sse_item, stream))
    assert [name for name, _ in items] == ['evidence', 'delta', 'done']
    done = items[-1][1]
    assert done['provenance_complete'] is True and done['mode'] == 'evidence_only'
    assert done['answer'] == '当前查询范围没有证据。'
    answer = repo.get_provenance_activity(project, done['answer_id'])
    assert answer['status'] == 'completed' and answer['payload']['offered'] == []
    assert answer['payload']['cited'] == []
