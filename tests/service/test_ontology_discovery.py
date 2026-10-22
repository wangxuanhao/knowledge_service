from ontology_seed import seed_ontology
import sys
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import DiscoveryRunConflict, Repository
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_drafts import OntologyDrafts
from knowledge_service.services.ontology_discovery import (
    _candidate_mindmap, _candidates, _materialize_candidates,
    _summary, _validated_materialization,
)
from knowledge_service.services.discovery_vocabulary import GeneratedVocabularyConflict


def test_materialization_keeps_normalizer_conflicts_out_of_provisional_records():
    candidates = [
        {'id': 'entity', 'kind': 'entity', 'text': '账号甲', 'proposed_type': '账号',
         'document_id': 'doc', 'document_version_id': 'v1', 'evidence': '账号甲'},
        {'id': 'conflict', 'kind': 'attribute', 'entity_id': 'entity',
         'proposed_type': '用户行为', 'value': '查看', 'document_id': 'doc',
         'document_version_id': 'v1', 'evidence': '备用',
         'attribute_evidence': '用户行为为查看'},
        {'id': 'low', 'kind': 'attribute', 'entity_id': 'entity',
         'proposed_type': '备注', 'value': '试用', 'document_id': 'doc',
         'document_version_id': 'v1', 'evidence': '备注为试用'},
        {'id': 'accepted', 'kind': 'attribute', 'entity_id': 'entity',
         'proposed_type': '状态', 'value': '启用', 'document_id': 'doc',
         'document_version_id': 'v1', 'evidence': '状态为启用'},
    ]
    draft = {
        'id': 'draft', 'candidate_snapshot': candidates,
        'mappings': {
            'entity_types': {'账号': 'http://example.test/Account'},
            'relation_types': {},
            'attributes': {
                '用户行为': 'http://example.test/Behavior',
                '备注': 'http://example.test/Note',
                '状态': 'http://example.test/Status',
            },
        },
    }
    outcomes = [
        {'candidate_id':'conflict','status':'skipped','reason_code':'ontology_term_conflict'},
        {'candidate_id':'low','status':'deferred','reason_code':'low_frequency_attribute'},
    ]

    records, skipped = _materialize_candidates(
        'project', draft, 'ontology', candidate_outcomes=outcomes)

    assert {record['metadata']['discovery_candidate_id'] for record in records} == {
        'entity', 'accepted'}
    assert {item['candidate_id']: item['reason_code'] for item in skipped} == {
        'conflict': 'ontology_term_conflict',
        'low': 'low_frequency_attribute',
    }
    assert next(item for item in skipped if item['candidate_id'] == 'conflict')[
        'candidate'] == candidates[1]
    assert next(item for item in skipped if item['candidate_id'] == 'low')[
        'candidate'] == candidates[2]


def test_reusing_materialized_context_keeps_actual_records_and_remaps_endpoints():
    from copy import deepcopy
    from knowledge_service.services.ontology_discovery import _reuse_materialized_records

    existing=[{'id':'canonical-entity','kind':'entity','type':'urn:Reviewed',
               'text':'已审核名称','metadata':{'discovery_candidate_ids':['one','alias']}}]
    proposed=[{'id':'generated-one','kind':'entity','type':'urn:Guessed',
               'text':'模型重猜名称','metadata':{'discovery_candidate_id':'one'}},
              {'id':'new-edge','kind':'relation','subject_id':'generated-one',
               'object_id':'generated-one','metadata':{'discovery_candidate_id':'edge'}}]
    original=deepcopy(proposed)
    result=_reuse_materialized_records(proposed,existing,{'one','alias'})
    assert result[0]==existing[0]
    assert result[1]['subject_id']==result[1]['object_id']=='canonical-entity'
    assert proposed==original
    assert _reuse_materialized_records(proposed,existing,set())==proposed


def test_discovery_exceptions_are_separate_from_normal_counts_and_views():
    candidates=[
        {'id':'entity','kind':'entity','text':'账号甲','proposed_type':'账号','document_id':'doc'},
        {'id':'attribute','kind':'attribute','entity_id':'entity','subject':'账号甲',
         'proposed_type':'状态','value':'封禁','evidence':'账号甲状态为封禁','document_id':'doc'},
        {'id':'exception','kind':'exception','source_kind':'relation','text':'账号甲',
         'proposed_type':'负责人','object':'张三','reason_code':'relation_object_not_resolved',
         'reason':'关系宾语未命中实体','evidence':'账号甲由张三负责','document_id':'doc'},
    ]
    summary=_summary(candidates)
    mindmap=_candidate_mindmap(candidates,{'entity':'pending','attribute':'pending'})
    assert summary['candidate_count']==2
    assert summary['exception_count']==1
    assert len(mindmap['attributes'])==1
    assert mindmap['attributes'][0]['subject']=='账号甲'
    assert len(mindmap['exceptions'])==1
    assert mindmap['summary']['candidate_count']==2
    assert mindmap['summary']['exception_count']==1


def test_open_discovery_reserves_active_and_retired_baseline_classes(tmp_path, monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    reservations = []

    def discover(self, text, include_attributes=False, *, reserved_class_names=()):
        reservations.append(reserved_class_names)
        return []

    monkeypatch.setattr(SemanticaExtractor, 'discover', discover)
    app = create_app(tmp_path / 'baseline-reservations.sqlite', HashingEncoder())
    turtle = '''
        @prefix ex: <https://example.test/> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        ex:ActiveClass a owl:Class ; rdfs:label "活跃类" .
        ex:RetiredClass a owl:Class ; rdfs:label "退役类" ; owl:deprecated true .
    '''
    with TestClient(app) as client:
        project = client.post('/api/projects', json={
            'name': '基线保留字', 'use_default_ontology': False,
            'ontology_mode': 'discovery'}).json()
        seed_ontology(app.state.service.repository,
            project['id'], turtle, Ontology(turtle).summary())
        response = client.post(f"/api/projects/{project['id']}/documents", json={
            'title': '多片原文', 'text': '账号甲当前封禁。' * 40,
            'extraction_mode': 'discovery', 'chunk_size': 100, 'chunk_overlap': 0,
            'resolve_entities': False,
        })

    assert response.status_code == 201, response.text
    assert len(reservations) > 1
    assert all(isinstance(item, frozenset) for item in reservations)
    assert all(item is reservations[0] for item in reservations)
    assert reservations[0] == frozenset({
        'ActiveClass', '活跃类', 'RetiredClass', '退役类'})


@pytest.mark.parametrize(('ontology_id', 'expected_detail'), [
    ('missing-ontology', '未找到：missing-ontology'),
    ('', '未找到：'),
])
def test_open_discovery_invalid_explicit_ontology_has_no_ingestion_side_effects(
        tmp_path, monkeypatch, ontology_id, expected_detail):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    called = False

    def discover(self, text, include_attributes=False, *, reserved_class_names=()):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(SemanticaExtractor, 'discover', discover)
    app = create_app(tmp_path / 'invalid-baseline.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project = client.post('/api/projects', json={
            'name': '无效基线', 'use_default_ontology': False,
            'ontology_mode': 'discovery'}).json()
        base = f"/api/projects/{project['id']}"
        response = client.post(base + '/documents', json={
            'title': '不应保存', 'text': '账号甲当前封禁',
            'extraction_mode': 'discovery', 'ontology_id': ontology_id,
            'resolve_entities': False,
        })

        assert response.status_code == 404
        assert response.json()['detail'] == expected_detail
        assert client.post(base + '/records/page', json={}).json()['records'] == []
        assert client.get(base + '/ingest-runs').json()['runs'] == []
        overview = client.get(base + '/ontology-discovery').json()
        assert overview['candidate_count'] == 0
        assert overview['exception_count'] == 0
        assert called is False


def test_candidate_mindmap_visually_clusters_repeated_occurrences_without_formal_ids():
    candidates=[]
    for index in range(2):
        prefix=f'doc-{index}'
        candidates.extend([
            {'id':prefix+':merchant','kind':'entity','text':'同一商户','proposed_type':'商户',
             'document_id':prefix,'document_title':f'来源 {index}','evidence':'商户使用平台'},
            {'id':prefix+':platform','kind':'entity','text':'同一平台','proposed_type':'平台',
             'document_id':prefix,'document_title':f'来源 {index}','evidence':'商户使用平台'},
            {'id':prefix+':uses','kind':'relation','subject_id':prefix+':merchant',
             'object_id':prefix+':platform','subject':'同一商户','object':'同一平台',
             'proposed_type':'使用','document_id':prefix,'document_title':f'来源 {index}',
             'evidence':'商户使用平台'},
        ])
    result=_candidate_mindmap(candidates,{item['id']:'pending' for item in candidates})
    assert len(result['nodes'])==2
    assert {node['occurrence_count'] for node in result['nodes']}=={2}
    assert all(len(node['sources'])==2 for node in result['nodes'])
    assert len(result['edges'])==1 and result['edges'][0]['occurrence_count']==2
    assert all(node['id'].startswith('candidate:') for node in result['nodes'])


def test_candidates_keep_current_document_version_and_attach_assertion_provenance(
        tmp_path, monkeypatch):
    repo = Repository(tmp_path / 'candidate-provenance.sqlite')
    project_id = repo.create_project('candidate provenance')['id']
    candidates = [
        {'id': 'entity', 'kind': 'entity', 'text': '甲', 'proposed_type': '主体'},
        {'id': 'relation', 'kind': 'relation', 'subject_id': 'entity',
         'object_id': 'entity', 'proposed_type': '关联'},
        {'id': 'attribute', 'kind': 'attribute', 'entity_id': 'entity',
         'proposed_type': '状态', 'value': '启用'},
    ]
    original = repo.put_record(project_id, {
        'id': 'doc', 'kind': 'document', 'text': '甲关联甲，状态启用。',
        'metadata': {'title': '原文', 'discovery_candidates': candidates},
    })
    for index, candidate in enumerate(candidates):
        repo.create_assertion(project_id, {
            'id': candidate['id'], 'kind': candidate['kind'],
            'document_id': 'doc', 'document_version_id': original['version_id'],
            'chunk_id': f'chunk-{index}', 'start_char': index * 2,
            'end_char': index * 2 + 1, 'quote': '甲', 'payload': candidate,
        })
    current = repo.put_record(project_id, {
        'id': 'doc', 'kind': 'document', 'text': '甲关联甲，状态启用。修订。',
        'metadata': {'title': '原文（修订）', 'discovery_candidates': candidates},
    }, expected_version=original['version'])
    calls = 0
    original_list_assertions = repo.list_assertions

    def counted_list_assertions(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_list_assertions(*args, **kwargs)

    monkeypatch.setattr(repo, 'list_assertions', counted_list_assertions)
    from knowledge_service.repository.page_reads import PageReads
    inputs = PageReads(repo).discovery_inputs(project_id)
    result = _candidates(inputs['documents'], inputs['assertions'])

    assert calls == 0
    assert {item['document_version_id'] for item in result} == {current['version_id']}
    assert {item['assertion_document_version_id'] for item in result} == {
        original['version_id']}
    for index, item in enumerate(result):
        assert item['assertion_id'] == item['id']
        assert item['assertion_chunk_id'] == f'chunk-{index}'
        assert item['assertion_start_char'] == index * 2
        assert item['assertion_end_char'] == index * 2 + 1


def test_candidate_mindmap_uses_assertion_sources_for_every_normal_kind():
    shared = {
        'document_id': 'current-doc', 'document_title': '来源',
        'document_version_id': 'current-version',
        'assertion_document_id': 'pinned-doc',
        'assertion_document_version_id': 'pinned-version',
        'assertion_chunk_id': 'chunk-pinned',
        'assertion_start_char': 7, 'assertion_end_char': 12,
        'assertion_status': 'pending', 'confidence': .91, 'evidence_status': 'exact',
    }
    candidates = [
        {**shared, 'id': 'entity-a', 'assertion_id': 'entity-a',
         'kind': 'entity', 'text': '甲', 'proposed_type': '主体', 'evidence': '实体证据'},
        {**shared, 'id': 'entity-b', 'assertion_id': 'entity-b',
         'kind': 'entity', 'text': '乙', 'proposed_type': '主体', 'evidence': '实体证据'},
        {**shared, 'id': 'relation', 'assertion_id': 'relation',
         'kind': 'relation', 'subject_id': 'entity-a', 'object_id': 'entity-b',
         'subject': '甲', 'object': '乙', 'proposed_type': '关联', 'evidence': '关系证据'},
        {**shared, 'id': 'attribute', 'assertion_id': 'attribute',
         'kind': 'attribute', 'entity_id': 'entity-a', 'subject': '甲',
         'proposed_type': '状态', 'value': '启用', 'evidence': '备用证据',
         'attribute_evidence': '属性证据'},
    ]
    result = _candidate_mindmap(
        candidates, {item['id']: 'pending' for item in candidates})

    aggregates = [*result['nodes'], *result['edges'], *result['attributes']]
    sources = {source['assertion_id']: source
               for aggregate in aggregates for source in aggregate['sources']}
    assert set(sources) == {'entity-a', 'entity-b', 'relation', 'attribute'}
    for assertion_id, source in sources.items():
        assert source['document_id'] == 'pinned-doc'
        assert source['document_version_id'] == 'pinned-version'
        assert source['chunk_id'] == 'chunk-pinned'
        assert source['start_char'] == 7 and source['end_char'] == 12
        assert source['confidence'] == .91
        assert source['status'] == 'pending'
        assert source['evidence_status'] == 'exact'
        assert source['resolvable'] is True
        assert assertion_id in next(
            aggregate['candidate_ids'] for aggregate in aggregates
            if assertion_id in aggregate['candidate_ids'])
    assert sources['attribute']['evidence_preview'] == '属性证据'
    assert sources['attribute']['evidence_preview_truncated'] is False
    assert all(aggregate['source_count'] == len(aggregate['sources'])
               and aggregate['sources_truncated'] is False
               for aggregate in aggregates)


def test_candidate_mindmap_marks_missing_assertions_and_exceptions_unresolvable():
    candidates = [
        {'id': 'missing', 'kind': 'entity', 'text': '无断言', 'proposed_type': '主体',
         'document_id': 'doc', 'document_title': '来源', 'evidence': '预览'},
        {'id': 'exception', 'kind': 'exception', 'source_kind': 'entity',
         'text': '异常', 'document_id': 'doc', 'document_title': '来源',
         'evidence': '异常预览', 'reason_code': 'evidence_not_in_source'},
    ]
    result = _candidate_mindmap(candidates, {'missing': 'pending'})

    missing = result['nodes'][0]
    assert missing['candidate_ids'] == ['missing']
    assert missing['source_count'] == 1 and missing['sources_truncated'] is False
    assert missing['sources'][0]['assertion_id'] is None
    assert missing['sources'][0]['resolvable'] is False
    exception = result['exceptions'][0]
    assert exception['source_count'] == 1 and exception['sources_truncated'] is False
    assert exception['sources'][0]['assertion_id'] is None
    assert exception['sources'][0]['resolvable'] is False


def test_candidate_mindmap_sorts_and_truncates_lightweight_sources():
    long_evidence = '证' * 501
    sort_fields = [
        ('乙', 'doc-b', 7, 'assertion-10'),
        ('甲', 'doc-z', 9, 'assertion-09'),
        ('甲', 'doc-a', 8, 'assertion-08'),
        ('甲', 'doc-a', 3, 'assertion-07'),
        ('甲', 'doc-a', 3, 'assertion-06'),
        ('丙', 'doc-c', 1, 'assertion-05'),
        ('丁', 'doc-d', 2, 'assertion-04'),
        ('戊', 'doc-e', 4, 'assertion-03'),
        ('己', 'doc-f', 5, 'assertion-02'),
        ('庚', 'doc-g', 6, 'assertion-01'),
        ('辛', 'doc-h', 0, 'assertion-00'),
    ]
    candidates = [{
        'id': assertion_id, 'assertion_id': assertion_id, 'kind': 'entity',
        'text': '同一候选', 'proposed_type': '主体', 'document_title': title,
        'document_id': document_id, 'document_version_id': 'current',
        'assertion_document_version_id': 'pinned',
        'assertion_chunk_id': f'chunk-{assertion_id}',
        'assertion_start_char': start, 'assertion_end_char': start + 1,
        'evidence': long_evidence, 'confidence': .8,
    } for title, document_id, start, assertion_id in reversed(sort_fields)]
    result = _candidate_mindmap(
        candidates, {item['id']: 'pending' for item in candidates})
    node = result['nodes'][0]

    expected = [item[3] for item in sorted(sort_fields)][:10]
    assert node['source_count'] == 11
    assert node['sources_truncated'] is True
    assert len(node['sources']) == 10
    assert [source['assertion_id'] for source in node['sources']] == expected
    assert all(source['assertion_id'] in node['candidate_ids']
               for source in node['sources'])
    assert all(source['evidence_preview'] == long_evidence[:500]
               and source['evidence_preview_truncated'] is True
               for source in node['sources'])


def test_missing_ontology_reports_clear_state_without_project_id(tmp_path,monkeypatch):
    from knowledge_service.integrations.semantica_adapter import SemanticaExtractor

    def discover(self,text,include_attributes=False,*,reserved_class_names=()):
        return [{'id':'merchant','kind':'entity','text':'测试商户','proposed_type':'Merchant','confidence':.93}]

    monkeypatch.setattr(SemanticaExtractor,'discover',discover)
    app=create_app(tmp_path/'missing.sqlite',HashingEncoder())
    with TestClient(app) as client:
        project=client.post('/api/projects',json={
            'name':'无本体','use_default_ontology':False,'ontology_mode':'discovery'}).json()
        pid=project['id'];base=f'/api/projects/{pid}'

        missing=client.get(base+'/ontology')
        assert missing.status_code==404
        body=missing.json()
        assert body['code']=='ontology_not_published'
        assert pid not in body['detail']

        overview=client.get(base+'/ontology-discovery').json()
        assert overview['published'] is False and overview['ontology_id'] is None
        assert overview['unpublished_candidate_count']==0

        client.post(base+'/documents',json={'title':'原文','text':'平台规则适用于测试商户。',
            'extraction_mode':'discovery','resolve_entities':False})
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_count']==1
        assert overview['unpublished_candidate_count']==1

        candidate_id=client.get(base+'/ontology-discovery/candidates').json()['items'][0]['id']
        selection={'candidate_ids':[candidate_id],'expected_ontology_id':None,'create_new_draft':True}
        response=client.post(base+'/ontology-discovery/selection-preview',json=selection)
        assert response.status_code==200,response.text
        preview=response.json()
        added=client.post(base+'/ontology-discovery/add-selection',json={**selection,
            'preview_fingerprint':preview['preview_fingerprint'],
            'source_fingerprint':preview['source_fingerprint'],'idempotency_key':'selected'}).json()
        assert added['draft']['id']
        overview=client.get(base+'/ontology-discovery').json()
        assert overview['candidate_status_counts']['included_in_draft']==1
        assert not overview['published']


def test_materialization_validation_keeps_invalid_relation_pending():
    turtle='''
        @prefix ex: <urn:test:> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        ex:A a owl:Class . ex:B a owl:Class . ex:C a owl:Class .
        ex:links a owl:ObjectProperty ; rdfs:domain ex:A ; rdfs:range ex:B .
        ex:count a owl:DatatypeProperty ; rdfs:domain ex:C ; rdfs:range xsd:integer .
    '''
    def entity(identifier,type_iri):
        return {'id':identifier,'kind':'entity','type':type_iri,'text':identifier,'ontology_id':'draft',
            'properties':{},'metadata':{'discovery_candidate_id':'candidate-'+identifier}}
    records=[entity('source','urn:test:C'),entity('target','urn:test:B'),
        {'id':'valid-count','kind':'attribute','type':'urn:test:count','text':'source count 2',
         'ontology_id':'draft','subject_id':'source','value':2,
         'datatype':'http://www.w3.org/2001/XMLSchema#integer',
         'metadata':{'discovery_candidate_id':'candidate-valid-count'}},
        {'id':'bad-count','kind':'attribute','type':'urn:test:count','text':'source count two',
         'ontology_id':'draft','subject_id':'source','value':'two',
         'datatype':'http://www.w3.org/2001/XMLSchema#string',
         'metadata':{'discovery_candidate_id':'candidate-bad-count'}},
        {'id':'edge','kind':'relation','type':'urn:test:links','text':'bad edge','ontology_id':'draft',
         'subject_id':'source','object_id':'target','metadata':{'discovery_candidate_id':'candidate-edge'}}]
    accepted,skipped,report=_validated_materialization(turtle,records,[])
    assert [row['id'] for row in accepted]==['source','target','valid-count']
    assert {item['candidate_id'] for item in skipped}=={'candidate-bad-count','candidate-edge'}
    assert report=={'conforms':True,'accepted_count':3,'skipped_count':2}
from rdflib import RDFS, URIRef
