from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.api.security_middleware import requires_write_access
from knowledge_service.core.time import utc_now
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.services import graph_progressive_search
from knowledge_service.services.formal_writes import FormalFactWriter
from knowledge_service.services.source_documents import SourceDocumentService


@pytest.fixture
def graph(tmp_path):
    app = create_app(tmp_path / 'progressive-graph', encoder=HashingEncoder(), auth_disabled=True)
    repo = app.state.service.repository
    project = repo.create_project('Progressive graph')['id']
    client = TestClient(app)
    path = f'/api/projects/{project}/graph-neighborhood/search'
    repo.put_batch(project, [
        {'id': id, 'kind': 'entity', 'type': kind, 'text': label}
        for id, kind, label in (
            ('seed', 'Topic', '起点'), ('a-noise', 'Topic', '其它'), ('bridge', 'Topic', '桥'),
            ('found', 'Policy', '退款细则'), ('next', 'Policy', '退款例外'), ('blocked', 'Policy', '退款兜底'),
        )
    ] + [
        {'id': id, 'kind': 'relation', 'type': kind, 'text': kind,
         'subject_id': start, 'object_id': end}
        for id, kind, start, end in (
            ('a-noise-edge', 'related', 'seed', 'a-noise'), ('b-bridge-edge', 'related', 'seed', 'bridge'),
            ('c-found-edge', 'related', 'bridge', 'found'), ('d-next-edge', 'related', 'found', 'next'),
            ('e-block-edge', 'other', 'bridge', 'blocked'),
        )
    ])

    def search(**body):
        response = client.post(path, json={'seeds': ['seed'], **body})
        assert response.status_code == 200, response.text
        return response.json()

    return repo, project, search


def test_filters_can_find_a_match_beyond_the_canvas_limit_and_keep_bridges(graph):
    _, _, search = graph
    result = search(direction='forward', relationship_types=['related'], max_nodes=3, max_edges=2,
                    filter_steps=[{'field': 'entity_type', 'value': 'Policy'},
                                  {'field': 'entity_name', 'value': '退款'}])

    assert result['total'] == 1 and result['partial'] is False
    assert [match['id'] for match in result['matches']] == ['found']
    path = result['matches'][0]['path']
    assert [step['relation_id'] for step in path['steps']] == ['b-bridge-edge', 'c-found-edge']
    assert [step['direction'] for step in path['steps']] == ['forward', 'forward']
    assert {row['id'] for row in result['records']} == {
        'seed', 'bridge', 'found', 'b-bridge-edge', 'c-found-edge',
    }


def test_continue_from_result_and_search_backwards(graph):
    _, _, search = graph
    result = search(seeds=['found'], direction='backward', relationship_types=['related'],
                    filter_steps=[{'field': 'entity_name', 'value': '起点'}])
    assert [match['id'] for match in result['matches']] == ['seed']
    path = result['matches'][0]['path']
    assert [step['relation_id'] for step in path['steps']] == ['c-found-edge', 'b-bridge-edge']
    assert [step['direction'] for step in path['steps']] == ['backward', 'backward']


def test_exact_attribute_values_and_zero_hop(graph):
    repo, project, search = graph
    repo.put_batch(project, [
        {'id': id + '-attr', 'kind': 'attribute', 'type': 'description', 'text': value,
         'subject_id': id, 'value': value, 'datatype': 'http://www.w3.org/2001/XMLSchema#string'}
        for id, value in [('found', '已上线'), ('blocked', '未完全上线')]
    ])

    result = search(filter_steps=[{'field': 'attribute_value', 'value': '已上线'}])
    assert [match['id'] for match in result['matches']] == ['found']
    assert result['matches'][0]['evidence'][0]['attribute_id'] == 'found-attr'
    result = search(seeds=['found'], hops=0,
                    filter_steps=[{'field': 'attribute_value', 'value': '已上线'}])
    assert result['matches'][0]['path']['steps'] == []
    empty = search(filter_steps=[{'field': 'attribute_value', 'value': '上线'}])
    assert empty['matches'] == [] and empty['total'] == 0 and empty['partial'] is False


def test_source_conditions_use_the_same_accepted_publication(graph, tmp_path):
    repo, project, search = graph
    originals = SourceDocumentService(repo, SourceObjectStore(tmp_path / 'originals'))
    for document_id, platform, published_at, text in (
        ('taobao', '淘宝', '2026-11-19T16:10:00Z', '退款细则：只出现在正文的售后期限'),
        ('jingdong', '京东', '2026-11-20T16:10:00Z', '其它正文'),
    ):
        source = originals.persist(project, document_id, text.encode(), f'{document_id}.txt')
        document = repo.put_record(project, {
            'id': document_id, 'kind': 'document', 'text': text, 'valid_from': '2026-11-01',
            'metadata': {'title': document_id + ' 资料', 'published_at': published_at,
                         'source_document_version_id': source['id'],
                         'applicability': {'platforms': {'mode': 'specific', 'values': [platform]}}},
        })
        chunk = repo.put_record(project, {
            'id': document_id + '-chunk', 'kind': 'chunk', 'text': text, 'source_id': document_id,
            'metadata': {'source_version_id': document['version_id']},
        })
        originals.store.add_spans(project, source['id'], chunk['version_id'], [{
            'block_id': source['blocks'][0]['block_id'],
            'chunk_start_char': 0, 'chunk_end_char': len(text),
            'block_start_char': 0, 'block_end_char': len(text),
        }])
        FormalFactWriter(repo).apply('manual_write', project, [{
            'id': document_id + '-assertion', 'kind': 'entity', 'canonical_record_id': 'found',
            'document_id': document_id, 'document_version_id': document['version_id'],
            'chunk_id': chunk['id'],
            'payload': {'kind': 'entity', 'type': 'Policy', 'text': '退款细则'},
            'quote': '退款细则', 'actor': 'reviewer',
        }], {}, {'records': []})

    keyword = {'field': 'source_keyword', 'value': '售后期限'}
    date = {'field': 'source_published_date', 'value': '2026-11-20'}
    platform = {'field': 'source_platform', 'value': '淘宝'}
    result = search(filter_steps=[platform, date, keyword])
    match, = result['matches']
    assert match['id'] == 'found'
    assert match['source']['title'] == 'taobao 资料'
    assert match['source']['document_id'] == 'taobao'
    assert match['source']['assertion_id'] == 'taobao-assertion'
    assert match['source']['platforms'] == ['淘宝']
    assert match['source']['source_version_id'] != match['source']['document_version_id']
    assert originals.get(project, match['source']['source_version_id'])['document_id'] == 'taobao'
    location = originals.locate(project, match['source']['source_version_id'],
                                match['source']['chunk_version_id'])
    assert location['status'] == 'available'
    assert location['block_id'] == originals.get(project, match['source']['source_version_id'])['blocks'][0]['block_id']
    assert match['source']['quote'] == '退款细则'

    for failing_step in ({**platform, 'value': '京东'}, {**date, 'value': '2026-11-19'},
                         {**date, 'value': '2026-11-01'}):
        empty = search(filter_steps=[failing_step, keyword])
        assert (empty['matches'], empty['total'], empty['partial']) == ([], 0, False)

    known_at = utc_now()
    assertion = repo.get_assertion(project, 'taobao-assertion')
    repo.transition_assertion(project, assertion['id'], assertion['decision_version'],
                              'superseded', 'withdraw source evidence', 'reviewer')
    assert search(filter_steps=[keyword])['matches'] == []
    historical, = search(known_at=known_at, filter_steps=[keyword])['matches']
    assert historical['source']['assertion_id'] == assertion['id']


def test_partial_results_keep_known_match_count(graph):
    _, _, search = graph
    result = search(max_nodes=2, max_edges=1,
                    filter_steps=[{'field': 'entity_type', 'value': 'Policy'}])
    assert result['total'] == 2 and result['partial'] is True
    assert result['matches'] == []


def test_protected_scan_budget_reports_an_incomplete_zero(graph, monkeypatch):
    _, _, search = graph
    monkeypatch.setattr(graph_progressive_search, '_MAX_SEARCH_EDGES', 1)
    result = search(filter_steps=[{'field': 'entity_name', 'value': '退款细则'}])
    assert result['total'] == 0 and result['matches'] == []
    assert result['scan_truncated'] is True and result['partial'] is True


def test_graph_neighborhood_search_accepts_read_only_viewers():
    assert not requires_write_access('POST', '/api/projects/p/graph-neighborhood/search')
