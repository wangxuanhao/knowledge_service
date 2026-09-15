from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.embeddings import HashingEncoder


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'service.sqlite', encoder=HashingEncoder())) as client:
        yield client


def project(client):
    result = client.post('/api/projects', json={'name':'规则平台'})
    assert result.status_code == 201, result.text
    return result.json()['id']


def test_filtered_ingestion_search_and_restart(client):
    p = project(client)
    for city in ['北京','上海']:
        result = client.post(f'/api/projects/{p}/documents', json={
            'title': city+'规则', 'text': '商户必须提供退款。', 'extract':False,
            'metadata':{'region':{'city':city}, 'tags':['规则']}, 'valid_from':'2025-01-01'})
        assert result.status_code == 201, result.text
    scope = {'query':'退款', 'k':1, 'filters':{'field':'region.city','op':'eq','value':'北京'}}
    result = client.post(f'/api/projects/{p}/search', json=scope).json()
    assert len(result['hits']) == 1
    assert result['hits'][0]['metadata']['region']['city'] == '北京'
    assert result['candidate_count'] == 1
    p2 = project(client)
    assert client.post(f'/api/projects/{p2}/search',json=scope).json()['hits'] == []


def test_background_ingestion_has_stage_logs(client):
    import time
    p=project(client)
    response=client.post(f'/api/projects/{p}/documents/jobs',json={
        'title':'日志测试','text':'中文片段。'*100,'extract':False,
        'chunk_size':180,'chunk_overlap':20})
    assert response.status_code==202
    job_id=response.json()['id']
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        jobs=client.get('/api/jobs').json()['jobs']
        job=next(j for j in jobs if j['id']==job_id)
        if job['status'] not in ('queued','running'):break
        time.sleep(.02)
    assert job['status']=='completed',job
    logs='\n'.join(job['logs'])
    for message in ('文档开始','原文收据已保存','切片完成','向量化','SQLite 原子落库','落库完成'):
        assert message in logs


def test_revision_and_half_open_time(client):
    p = project(client)
    record = {'id':'rule', 'kind':'entity', 'type':'Merchant', 'text':'旧条款',
              'valid_from':'2020-01-01', 'valid_until':'2022-01-01', 'metadata':{'version':'old'}}
    path = f'/api/projects/{p}/records'
    old = client.post(path,json={'records':[record]})
    assert old.status_code == 201, old.text
    old = old.json()['records'][0]
    assert client.post(f'/api/projects/{p}/search',json={'query':'条款','valid_at':'2022-01-01'}).json()['hits'] == []
    record.update(text='修正条款',metadata={'version':'corrected'})
    new = client.put(path+'/rule',json={'record':record,'expected_version':1})
    assert new.status_code == 200, new.text
    assert client.put(path+'/rule',json={'record':record,'expected_version':1}).status_code == 409
    historical = client.post(f'/api/projects/{p}/search',json={
        'query':'条款','valid_at':'2021-01-01','known_at':old['recorded_at']}).json()['hits']
    assert historical[0]['text'] == '旧条款'
    assert len(client.get(path+'/rule/history').json()['versions']) == 2


def test_invalid_ontology_and_filter_rollback(client):
    p = project(client)
    path = f'/api/projects/{p}'
    assert client.post(path+'/records',json={'records':[{'kind':'entity','text':'x','type':'Unknown'}]}).status_code == 422
    assert client.post(path+'/records/query',json={}).json()['records'] == []
    assert client.post(path+'/search',json={'query':'x','filters':{'field':'x','op':'arbitrary','value':1}}).status_code == 422
    assert client.post(path+'/ontologies',json={'turtle':'invalid'}).status_code == 422


def test_relationships_cannot_leak_filtered_or_inactive_endpoints(client):
    p = project(client)
    path = f'/api/projects/{p}'
    rows = [dict(id='a',kind='entity',type='Platform',text='平台',metadata={'city':'上海'}),
            dict(id='b',kind='entity',type='Merchant',text='商户',metadata={'city':'北京'}),
            dict(id='r',kind='relation',type='onboards',text='入驻平台',subject_id='b',object_id='a',metadata={'city':'北京'})]
    response = client.post(path+'/records',json={'records':rows})
    assert response.status_code == 201, response.text
    scope = {'filters':{'field':'city','op':'eq','value':'北京'}}
    graph = client.post(path+'/graph',json=scope).json()
    assert [n['id'] for n in graph['nodes']] == ['b']
    assert graph['edges'] == []
    result = client.post(path+'/search',json={**scope,'query':'规则'}).json()
    assert {r['id'] for r in result['hits']} == {'b'}


def test_failed_extraction_keeps_receipt_without_partial_records(client, monkeypatch):
    monkeypatch.delenv('KG_LLM_API_KEY', raising=False)
    p = project(client)
    path = f'/api/projects/{p}'
    response = client.post(path+'/documents', json={'title':'失败回执','text':'退款规则','extract':True})
    assert response.status_code == 503
    rows = client.post(path+'/records/query', json={}).json()['records']
    assert len(rows) == 1 and rows[0]['kind'] == 'document'
    assert rows[0]['metadata']['status'] == 'failed'
    assert len(client.get(path+'/records/'+rows[0]['id']+'/history').json()['versions']) == 2


def test_api_persists_across_restart(tmp_path):
    database = tmp_path/'restart.sqlite'
    with TestClient(create_app(database, HashingEncoder())) as first:
        p = project(first)
        assert first.post(f'/api/projects/{p}/documents', json={
            'title':'持久化','text':'重启后的退款证据','extract':False}).status_code == 201
    with TestClient(create_app(database, HashingEncoder())) as second:
        result = second.post(f'/api/projects/{p}/search', json={'query':'退款'}).json()
        assert result['candidate_count'] == 1
        assert '重启后的退款证据' in result['hits'][0]['text']


def test_malformed_llm_response_is_service_unavailable(client, monkeypatch):
    import httpx
    for key in ('KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL'):
        monkeypatch.setenv(key, 'configured')
    p = project(client)
    client.post(f'/api/projects/{p}/documents', json={'title':'证据','text':'退款规则','extract':False})
    monkeypatch.setattr(httpx.Client, 'post', lambda *a, **k: httpx.Response(
        200, json={'choices':[]}, request=httpx.Request('POST','https://example.invalid')))
    # TestClient inherits httpx.Client, so invoke the ASGI transport through request.
    response = client.request('POST', f'/api/projects/{p}/qa', json={'query':'退款','generate':True})
    assert response.status_code == 503


def test_ingest_failure_does_not_overwrite_concurrent_revision(client, monkeypatch):
    service = client.app.state.service
    p = project(client)
    def edit_then_fail(texts):
        receipt = service.repository.current_records(p)[0]
        replacement = {k:v for k,v in receipt.items() if k not in {
            'project_id','version','version_id','recorded_at','superseded_at'}}
        replacement['text'] = 'USER EDIT TO PRESERVE'
        service.repository.put_record(p, replacement, expected_version=receipt['version'])
        raise RuntimeError('simulated embedding failure')
    monkeypatch.setattr(service.encoder, 'encode', edit_then_fail)
    response = client.post(f'/api/projects/{p}/documents', json={
        'title':'并发文档','text':'ORIGINAL','extract':False})
    assert response.status_code == 503
    current = service.repository.current_records(p)
    assert len(current) == 1
    assert current[0]['text'] == 'USER EDIT TO PRESERVE'
    assert len(service.repository.history(p, current[0]['id'])) == 2


def test_ingest_prefers_relation_extracted_validity_over_request(client, monkeypatch):
    import knowledge_service.semantica_adapter as adapter
    p = project(client)
    base = f'/api/projects/{p}'
    ttl='@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . :Person a owl:Class . :Company a owl:Class . :worksAt a owl:ObjectProperty .'
    client.post(base+'/ontologies', json={'turtle': ttl})
    def fake_extract(self, text, ontology):
        return [
            {'id':'e1','kind':'entity','text':'张三','type':'Person','metadata':{}},
            {'id':'e2','kind':'entity','text':'甲公司','type':'Company','metadata':{}},
            {'id':'r1','kind':'relation','type':'worksAt','subject_id':'e1','object_id':'e2',
             'text':'张三 worksAt 甲公司','valid_from':'2020-01-01T00:00:00Z','valid_until':'2022-01-01T00:00:00Z',
             'metadata':{'temporal_source_text':'自2020年起'}},
        ]
    monkeypatch.setattr(adapter.SemanticaExtractor, 'extract', fake_extract)
    response = client.post(base+'/documents', json={
        'title':'时间事实','text':'张三任职于甲公司','resolve_entities':False,
        'relation_constraint_mode':'off','valid_from':'2019-01-01','valid_until':'2024-01-01'})
    assert response.status_code == 201, response.text
    rows = client.app.state.service.repository.current_records(p)
    relation = next(r for r in rows if r['kind']=='relation')
    assert relation['valid_from'] == '2020-01-01T00:00:00.000000Z'
    assert relation['valid_until'] == '2022-01-01T00:00:00.000000Z'
    entity = next(r for r in rows if r['kind']=='entity')
    assert entity['valid_from'] == '2019-01-01T00:00:00.000000Z'
