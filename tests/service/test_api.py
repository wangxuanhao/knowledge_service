from contextlib import closing

from ontology_seed import seed_project_ontology
from ontology_seed import seed_ontology
from fastapi.testclient import TestClient
import pytest

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.models import RecordWrite, Scope
from knowledge_service.repository import Repository
from knowledge_service.services.retrieval import PROJECTION_SCHEMA_VERSIONS
from keyword_index_fixture import install_keyword_store, ready_keyword_index


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'service.sqlite', encoder=HashingEncoder())) as client:
        yield client


def project(client, *, use_default_ontology=True):
    result = client.post('/api/projects', json={
        'name':'规则平台',
        'use_default_ontology': use_default_ontology,
    })
    assert result.status_code == 201, result.text
    return result.json()['id']


def test_project_applicability_values_endpoint_returns_current_dictionary(client):
    p = project(client, use_default_ontology=False)
    for title, applicability in [
        ('美团规则', {'platforms': {'mode': 'specific', 'values': ['美团']}, 'businesses': {'mode': 'specific', 'values': ['外卖']}}),
        ('京东规则', {'platforms': {'mode': 'specific', 'values': ['京东']},
                  'audiences': {'mode': 'specific', 'values': ['商户']}}),
    ]:
        response = client.post(f'/api/projects/{p}/documents', json={
            'title': title, 'text': title, 'extraction_mode': 'documents',
            'applicability': applicability,
        })
        assert response.status_code == 201, response.text

    response = client.get(f'/api/projects/{p}/applicability-values')

    assert response.status_code == 200, response.text
    assert response.json() == {
        'platforms': ['京东', '美团'], 'businesses': ['外卖'],
        'audiences': ['商户'],
    }


def test_project_cards_report_real_ontology_publications(client):
    empty = project(client, use_default_ontology=False)
    published = project(client)
    cards = {row['id']: row for row in client.get('/api/projects').json()['projects']}
    assert cards[empty]['ontology_publications'] == 0
    assert cards[published]['ontology_publications'] == 1
    assert cards[empty]['counts']['entities'] == 0
    url = f'/api/projects/{published}/ontologies'
    full = client.get(url).json()['versions']
    summary = client.get(url,params={'metadata_only':True})
    assert summary.status_code == 200
    assert summary.json()['versions'] == [
        {key: row[key] for key in ('id','created_at','version','version_reused')} for row in full]


def test_ontology_scope_accepts_each_legal_combination(client):
    p = project(client, use_default_ontology=False)

    for scope in [
        {'ontology_scope': 'all', 'ontology_ids': None},
        {'ontology_scope': 'ids', 'ontology_ids': ['ontology-v1']},
        {'ontology_scope': 'unknown', 'ontology_ids': None},
    ]:
        response = client.post(f'/api/projects/{p}/records/page', json=scope)
        assert response.status_code == 200, (scope, response.text)


def test_ontology_scope_rejects_each_illegal_combination(client):
    p = project(client, use_default_ontology=False)

    for scope in [
        {'ontology_scope': 'all', 'ontology_ids': ['ontology-v1']},
        {'ontology_scope': 'unknown', 'ontology_ids': ['ontology-v1']},
        {'ontology_scope': 'ids'},
        {'ontology_scope': 'ids', 'ontology_ids': None},
        {'ontology_scope': 'ids', 'ontology_ids': []},
        {'ontology_scope': 'ids', 'ontology_ids': ['']},
        {'ontology_scope': 'ids', 'ontology_ids': ['   ']},
        {'ontology_scope': 'ids', 'ontology_ids': ['\t']},
    ]:
        response = client.post(f'/api/projects/{p}/records/page', json=scope)
        assert response.status_code == 422, (scope, response.text)


def test_ontology_scope_deduplicates_ids_in_first_seen_order():
    scope = Scope.model_validate({
        'ontology_scope': 'ids',
        'ontology_ids': [' ontology-v2 ', '\tontology-v1', 'ontology-v2'],
    })

    assert scope.ontology_ids == ['ontology-v2', 'ontology-v1']


def test_packaged_echarts_asset_is_served(client):
    response = client.get('/vendor/echarts.min.js')

    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('application/javascript')
    assert b'echarts' in response.content.lower()


def test_filtered_ingestion_search_and_restart(client, monkeypatch):
    p = project(client)
    for city in ['北京','上海']:
        result = client.post(f'/api/projects/{p}/documents', json={
            'title': city+'规则', 'text': '商户必须提供退款。', 'extraction_mode':'documents',
            'metadata':{'region':{'city':city}, 'tags':['规则']}, 'valid_from':'2025-01-01'})
        assert result.status_code == 201, result.text
    ready_keyword_index(client.app.state.service, p, monkeypatch)
    scope = {'query':'退款', 'retrieval_mode':'keyword', 'k':1, 'filters':{'field':'region.city','op':'eq','value':'北京'}}
    result = client.post(f'/api/projects/{p}/search', json=scope).json()
    assert len(result['hits']) == 1
    assert result['hits'][0]['metadata']['region']['city'] == '北京'
    assert result['candidate_count'] == 1
    p2 = project(client)
    ready_keyword_index(client.app.state.service, p2, monkeypatch)
    assert client.post(f'/api/projects/{p2}/search',json=scope).json()['hits'] == []


def test_ingest_coalesces_receipt_and_completion_into_one_timeline_point(client):
    p = project(client)
    repository = client.app.state.service.repository
    future = repository.put_record(
        p, {'id': 'future', 'kind': 'document', 'text': 'future'},
        recorded_at='2030-01-01T00:00:00Z')
    response = client.post(f'/api/projects/{p}/documents', json={
        'title': '一次摄取', 'text': '商户必须提供退款凭证。', 'extraction_mode': 'documents'})
    assert response.status_code == 201, response.text

    document = response.json()['document']
    timeline = client.get(f'/api/projects/{p}/timeline').json()
    assert timeline['total'] == 2
    ingest_event = timeline['events'][-1]
    assert ingest_event['known_at'] > future['recorded_at']
    assert ingest_event['known_at'] == document['recorded_at']
    operation_records = [
        row for row in repository.current_records(p)
        if row['id'] == document['id'] or row.get('source_id') == document['id']]
    operation_times = {
        version['recorded_at']
        for row in operation_records for version in repository.history(p, row['id'])}
    assert operation_times == {ingest_event['known_at']}
    stored = repository.get_record(
        p, document['id'], known_at=ingest_event['known_at'])
    assert stored['version'] == 2
    assert stored['metadata']['status'] == 'ready'


def test_background_ingestion_has_stage_logs(client):
    import time
    p=project(client)
    response=client.post(f'/api/projects/{p}/documents/jobs',json={
        'title':'日志测试','text':'中文片段。'*100,'extraction_mode':'documents',
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
    for message in ('文档开始','原文收据已保存','切片完成','PostgreSQL 原子落库',
                    '文档检索已就绪','抽取阶段结束','落库完成'):
        assert message in logs
    repository = client.app.state.service.repository
    document = repository.get_record(p, job['result']['document']['id'])
    assert document['metadata']['status'] == 'ready'
    assert job['result']['status'] == 'completed'
    # Documents are durable before an external index is explicitly built.
    events = repository._db.execute(
        'SELECT operation,version_id FROM projection_outbox WHERE project_id=? ORDER BY seq',
        (p,)).fetchall()
    upserts = {row['version_id'] for row in events if row['operation'] == 'upsert'}
    assert {row['version_id'] for row in repository.current_records(p)} <= upserts
    assert repository.projections.project_watermark(p) == len(events) > 0
    with pytest.raises(RuntimeError, match='not_built'):
        repository.projections.search_index(p, 3)


def test_unchanged_manual_saves_reuse_version_without_new_evidence(client, monkeypatch):
    from knowledge_service.api import knowledge
    from knowledge_service.services.ontology import Ontology

    p = project(client, use_default_ontology=False)
    repository = client.app.state.service.repository
    turtle = ('@prefix ex: <http://ex/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
              'ex:Person a owl:Class . ex:knows a owl:ObjectProperty . ex:age a owl:DatatypeProperty .')
    seed_ontology(repository, p, turtle, Ontology(turtle).summary())
    monkeypatch.setattr(knowledge, 'audit_actor', lambda request: '知识维护员 (editor; user:editor)')
    path = f'/api/projects/{p}/records'
    created = client.post(path, json={'records': [
        {'id': 'person', 'kind': 'entity', 'type': 'Person', 'text': '甲'},
        {'id': 'other', 'kind': 'entity', 'type': 'Person', 'text': '乙'},
        {'id': 'relation', 'kind': 'relation', 'type': 'knows', 'text': '甲认识乙',
         'subject_id': 'person', 'object_id': 'other'},
        {'id': 'attribute', 'kind': 'attribute', 'type': 'age', 'text': '甲的年龄为 30',
         'subject_id': 'person', 'value': 30, 'datatype': 'http://www.w3.org/2001/XMLSchema#integer'},
    ]})
    assert created.status_code == 201, created.text
    prior = repository.formal_write_snapshot(p)
    event_count = repository._db.execute(
        'SELECT count(*) FROM projection_outbox WHERE project_id=?', (p,)).fetchone()[0]
    cases = [row for row in created.json()['records'] if row['id'] != 'other']
    for row in cases:
        body = {key: value for key, value in row.items() if key in RecordWrite.model_fields}
        saved = client.put(path + '/' + row['id'], json={'record': body, 'expected_version': row['version']})
        assert saved.status_code == 200, saved.text
        assert saved.json()['version_id'] == row['version_id']
    assert repository.formal_write_snapshot(p) == prior
    assert repository._db.execute(
        'SELECT count(*) FROM projection_outbox WHERE project_id=?', (p,)).fetchone()[0] == event_count

    for row in cases:
        body = {key: value for key, value in row.items() if key in RecordWrite.model_fields}
        body['text'] += '（更正）'
        if row['kind'] == 'attribute':
            body['value'] = 31
        saved = client.put(path + '/' + row['id'], json={'record': body, 'expected_version': row['version']})
        assert saved.status_code == 200, saved.text
        assert saved.json()['version'] == row['version'] + 1
        stale = client.put(path + '/' + row['id'], json={'record': body, 'expected_version': row['version']})
        assert stale.status_code == 409, stale.text
        history = client.get(path + '/' + row['id'] + '/history').json()['versions']
        assert history[-1]['manual_change'] == {'actor': '知识维护员 (editor; user:editor)'}
        assert len(history) == 2
    relation = client.get(path + '/relation').json()
    body = {key: value for key, value in relation.items() if key in RecordWrite.model_fields}
    body['metadata'].update({'_deleted': True, '_manual_actor': '伪造的操作者'})
    deleted = client.put(path + '/relation', json={'record': body, 'expected_version': relation['version']})
    assert deleted.status_code == 200, deleted.text
    history = client.get(path + '/relation/history').json()['versions']
    assert history[-1]['metadata']['_deleted'] is True
    assert history[-1]['manual_change'] == {'actor': '知识维护员 (editor; user:editor)'}


def test_adding_document_evidence_does_not_revise_an_unchanged_entity(client):
    p = project(client)
    repository = client.app.state.service.repository
    path = f'/api/projects/{p}/records'
    response = client.post(path, json={'records': [
        {'id': 'source-a', 'kind': 'document', 'text': '来源甲记载规则商户', 'metadata': {'title': '甲', 'status': 'ready'}},
        {'id': 'source-b', 'kind': 'document', 'text': '来源乙记载规则商户', 'metadata': {'title': '乙', 'status': 'ready'}},
    ]})
    assert response.status_code == 201, response.text
    response = client.post(path, json={'records': [
        {'id': 'merchant', 'kind': 'entity', 'type': 'Merchant', 'text': '规则商户', 'source_id': 'source-a'},
    ]})
    assert response.status_code == 201, response.text
    row = response.json()['records'][0]
    body = {key: value for key, value in row.items() if key in RecordWrite.model_fields}
    for source, new_evidence in [('source-a', False), ('source-b', True), ('source-b', False)]:
        before = repository._db.execute(
            'SELECT count(*) FROM projection_outbox WHERE project_id=?', (p,)).fetchone()[0]
        edited = client.put(path + '/merchant', json={
            'record': {**body, 'source_id': source}, 'expected_version': row['version'],
        })
        assert edited.status_code == 200, edited.text
        assert edited.json()['version_id'] == row['version_id']
        after = repository._db.execute(
            'SELECT count(*) FROM projection_outbox WHERE project_id=?', (p,)).fetchone()[0]
        if not new_evidence:
            assert after == before
    assert len(repository.history(p, 'merchant')) == 1
    assertions = repository.list_assertions(p, canonical_record_id='merchant', status='accepted')
    assert sorted(item['document_id'] for item in assertions if item['document_id']) == ['source-a', 'source-b']
    support = repository.list_record_version_assertions(p, record_version_id=row['version_id'])
    assert {item['assertion_id'] for item in support} == {item['id'] for item in assertions}


def test_manual_dates_record_their_source_without_rewriting_history(client):
    p = project(client, use_default_ontology=False)
    turtle = ('@prefix ex: <http://ex/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
              'ex:Person a owl:Class . ex:knows a owl:ObjectProperty .')
    path = f'/api/projects/{p}/records'
    seed_project_ontology(client, f'/api/projects/{p}', turtle)
    start = '2025-01-01T00:00:00Z'
    relation = {'id': 'link', 'kind': 'relation', 'text': '甲认识乙', 'type': 'knows',
                'subject_id': 'a', 'object_id': 'b', 'valid_from': start,
                'metadata': {'effective_time_source': 'extracted', 'valid_from': start,
                             'temporal_source_text': '自 2025 年 1 月 1 日生效', 'temporal_confidence': 0.9}}
    created = client.post(path, json={'records': [
        {'id': 'a', 'kind': 'entity', 'text': '甲', 'type': 'Person'},
        {'id': 'b', 'kind': 'entity', 'text': '乙', 'type': 'Person'},
        {'id': 'dated', 'kind': 'entity', 'text': '新记录', 'type': 'Person', 'valid_from': start},
        relation,
    ]})
    assert created.status_code == 201, created.text
    rows = {row['id']: row for row in created.json()['records']}
    original = rows['link']

    def revise(row, **changes):
        body = {key: value for key, value in row.items() if key in RecordWrite.model_fields}
        result = client.put(path + '/link', json={'record': {**body, **changes},
                                                 'expected_version': row['version']})
        assert result.status_code == 200, result.text
        return result.json()

    unchanged = revise(original, valid_from='2025-01-01T08:00:00+08:00')
    updated = revise(unchanged, valid_from='2025-02-01T00:00:00Z')
    cleared = revise(updated, valid_from=None)

    assert rows['dated']['metadata']['effective_time_source'] == 'explicit'
    assert 'effective_time_source' not in rows['a']['metadata']
    assert unchanged['version_id'] == original['version_id']
    assert unchanged['metadata']['effective_time_source'] == 'extracted'
    assert updated['metadata']['effective_time_source'] == 'explicit'
    assert 'temporal_source_text' not in updated['metadata']
    assert cleared['metadata']['effective_time_source'] == 'unknown'
    history = client.get(path + '/link/history').json()['versions']
    archived = next(row for row in history if row['version_id'] == original['version_id'])
    assert archived['metadata']['temporal_source_text'] == relation['metadata']['temporal_source_text']


def test_revision_and_half_open_time(client, monkeypatch):
    p = project(client)
    record = {'id':'rule', 'kind':'entity', 'type':'Merchant', 'text':'旧条款',
              'valid_from':'2020-01-01', 'valid_until':'2022-01-01', 'metadata':{'version':'old'}}
    path = f'/api/projects/{p}/records'
    old = client.post(path,json={'records':[record]})
    assert old.status_code == 201, old.text
    old = old.json()['records'][0]
    ready_keyword_index(client.app.state.service, p, monkeypatch)
    assert client.post(f'/api/projects/{p}/search',json={'query':'条款','retrieval_mode':'keyword','valid_at':'2022-01-01'}).json()['hits'] == []
    record.update(text='修正条款',metadata={'version':'corrected'})
    new = client.put(path+'/rule',json={'record':record,'expected_version':1})
    assert new.status_code == 200, new.text
    assert client.put(path+'/rule',json={'record':record,'expected_version':1}).status_code == 409
    historical = client.post(f'/api/projects/{p}/search',json={
        'query':'条款','retrieval_mode':'keyword','valid_at':'2021-01-01','known_at':old['recorded_at']}).json()['hits']
    assert historical[0]['text'] == '旧条款'
    assert len(client.get(path+'/rule/history').json()['versions']) == 2


def test_get_record_respects_known_at_with_mixed_offset(client):
    p = project(client)
    repository = client.app.state.service.repository
    first = repository.put_record(
        p, {'id':'temporal', 'kind':'entity', 'type':'Merchant', 'text':'旧版本'},
        recorded_at='2025-01-01T00:00:00.123000Z')
    repository.put_record(
        p, {'id':'temporal', 'kind':'entity', 'type':'Merchant', 'text':'新版本'},
        expected_version=1, recorded_at='2025-01-01T00:00:00.123100Z')

    response = client.get(
        f'/api/projects/{p}/records/temporal',
        params={'known_at':'2025-01-01T08:00:00.123050+08:00'})

    assert response.status_code == 200, response.text
    assert response.json()['text'] == '旧版本'
    assert response.json()['version'] == first['version'] == 1


def test_invalid_writes_roll_back_but_unknown_concepts_are_kept(client):
    """C1 改掉了这条契约的一半：类型不在本体里不再等于"丢掉知识"。

    旧断言是 `type='Unknown'` 必须 422 且不落库；现在它照样 422 的话，
    文档里抽出来的概念就永远进不来（这正是规划 §4.1 要修的）。新契约分两半：
      * 缺术语 → 201 收下 + 标「结构待定」（能查、能引用，但不作为正式证据）；
      * 真错误（端点不存在、过滤算子非法、Turtle 语法错）→ 照旧 422，且一条都不落库。
    """
    p = project(client)
    path = f'/api/projects/{p}'
    created = client.post(path+'/records',json={'records':[{'kind':'entity','text':'x','type':'Unknown'}]})
    assert created.status_code == 201, created.text
    record = created.json()['records'][0]
    assert record['structure_pending']['state'] == 'pending'
    assert record['structure_pending']['terms'] == ['Unknown']
    assert client.post(path+'/records/page',json={}).json()['total'] == 1
    broken = client.post(path+'/records',json={'records':[
        {'kind':'relation','type':'Unknown','text':'坏关系','subject_id':'nope','object_id':'nope'}]})
    assert broken.status_code == 422, broken.text
    assert client.post(path+'/records/page',json={}).json()['total'] == 1, '失败的写入不能留下半条记录'
    assert client.post(path+'/search',json={'query':'x','retrieval_mode':'keyword','filters':{'field':'x','op':'arbitrary','value':1}}).status_code == 422
    assert client.post(path+'/ontologies',json={'turtle':'invalid'}).status_code == 405


def test_relationships_cannot_leak_filtered_or_inactive_endpoints(client, monkeypatch):
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
    ready_keyword_index(client.app.state.service, p, monkeypatch)
    result = client.post(path+'/search',json={**scope,'query':'商户','retrieval_mode':'keyword'}).json()
    assert {r['id'] for r in result['hits']} == {'b'}


def test_ontology_scope_filters_versions_unknown_rows_and_dangling_facts(client):
    p = project(client, use_default_ontology=False)
    foreign_project = project(client, use_default_ontology=False)
    repository = client.app.state.service.repository
    turtle = (
        '@prefix ex: <https://example.test/> . '
        '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
        'ex:Thing a owl:Class .'
    )
    first = seed_ontology(repository, p, turtle, {})
    second = seed_ontology(repository, p, turtle, {})
    foreign = seed_ontology(repository, foreign_project, turtle, {})

    records = [
        {'id': 'first-a', 'kind': 'entity', 'type': 'Thing', 'text': 'first a',
         'ontology_id': first['id']},
        {'id': 'first-b', 'kind': 'entity', 'type': 'Thing', 'text': 'first b',
         'ontology_id': first['id']},
        {'id': 'second-a', 'kind': 'entity', 'type': 'Thing', 'text': 'second a',
         'ontology_id': second['id']},
        {'id': 'missing', 'kind': 'entity', 'type': 'Thing', 'text': 'missing'},
        {'id': 'foreign', 'kind': 'entity', 'type': 'Thing', 'text': 'foreign',
         'ontology_id': foreign['id']},
        {'id': 'first-local-relation', 'kind': 'relation', 'type': 'knows',
         'text': 'local relation', 'subject_id': 'first-a', 'object_id': 'first-b',
         'ontology_id': first['id']},
        {'id': 'first-cross-relation', 'kind': 'relation', 'type': 'knows',
         'text': 'cross relation', 'subject_id': 'first-a', 'object_id': 'second-a',
         'ontology_id': first['id']},
        {'id': 'first-local-attribute', 'kind': 'attribute', 'type': 'name',
         'text': 'local attribute', 'subject_id': 'first-a', 'value': 'A',
         'datatype': 'http://www.w3.org/2001/XMLSchema#string',
         'ontology_id': first['id']},
        {'id': 'first-cross-attribute', 'kind': 'attribute', 'type': 'name',
         'text': 'cross attribute', 'subject_id': 'second-a', 'value': 'B',
         'datatype': 'http://www.w3.org/2001/XMLSchema#string',
         'ontology_id': first['id']},
    ]
    for record in records:
        repository.put_record(p, record)

    def scoped_ids(body):
        response = client.post(f'/api/projects/{p}/records/page', json=body)
        assert response.status_code == 200, response.text
        return {row['id'] for row in response.json()['records']}

    default_response = client.post(f'/api/projects/{p}/records/page', json={})
    explicit_all = client.post(
        f'/api/projects/{p}/records/page', json={'ontology_scope': 'all'})
    assert default_response.status_code == explicit_all.status_code == 200
    assert default_response.json() == explicit_all.json()

    assert scoped_ids({
        'ontology_scope': 'ids', 'ontology_ids': [first['id']],
    }) == {
        'first-a', 'first-b', 'first-local-relation', 'first-local-attribute',
    }
    assert scoped_ids({
        'ontology_scope': 'ids', 'ontology_ids': [first['id'], second['id']],
    }) == {
        'first-a', 'first-b', 'second-a',
        'first-local-relation', 'first-cross-relation',
        'first-local-attribute', 'first-cross-attribute',
    }
    assert scoped_ids({'ontology_scope': 'unknown'}) == {'missing', 'foreign'}


def test_failed_extraction_keeps_receipt_without_partial_records(client, monkeypatch):
    monkeypatch.delenv('KG_LLM_API_KEY', raising=False)
    p = project(client)
    path = f'/api/projects/{p}'
    response = client.post(path+'/documents', json={'title':'失败回执','text':'退款规则','extraction_mode':'ontology'})
    assert response.status_code == 503
    rows = client.post(path+'/records/page', json={}).json()['records']
    assert len(rows) == 1 and rows[0]['kind'] == 'document'
    assert rows[0]['metadata']['status'] == 'failed'
    assert len(client.get(path+'/records/'+rows[0]['id']+'/history').json()['versions']) == 2


def test_api_persists_across_restart(tmp_path, monkeypatch):
    database = tmp_path/'restart.sqlite'
    schema_version = PROJECTION_SCHEMA_VERSIONS['milvus']
    with TestClient(create_app(database, HashingEncoder())) as first:
        p = project(first)
        assert first.post(f'/api/projects/{p}/documents', json={
            'title':'持久化','text':'重启后的退款证据','extraction_mode':'documents'}).status_code == 201
        ready_keyword_index(first.app.state.service, p, monkeypatch)
        selected = first.app.state.service.repository.projections.search_index(p, schema_version)
    with TestClient(create_app(database, HashingEncoder())) as second:
        install_keyword_store(second.app.state.service, monkeypatch)
        assert second.app.state.service.repository.projections.search_index(p, schema_version) == selected
        result = second.post(f'/api/projects/{p}/search', json={'query':'退款','retrieval_mode':'keyword'}).json()
        assert result['candidate_count'] == 1
        assert '重启后的退款证据' in result['hits'][0]['text']


def test_malformed_llm_response_is_service_unavailable(client, monkeypatch):
    import httpx
    for key in ('KG_LLM_API_KEY','KG_LLM_BASE_URL','KG_LLM_MODEL'):
        monkeypatch.setenv(key, 'configured')
    p = project(client)
    client.post(f'/api/projects/{p}/documents', json={
        'title':'证据','text':'退款规则','extraction_mode':'documents',
        'published_at':'2020-01-01',
        'valid_from':'2020-01-01',
        'applicability': {
            'platforms': {'mode': 'all', 'values': []},
            'businesses': {'mode': 'all', 'values': []},
            'audiences': {'mode': 'all', 'values': []},
        },
    })
    ready_keyword_index(client.app.state.service, p, monkeypatch)
    provider_calls = []
    def malformed(*args, **kwargs):
        provider_calls.append(kwargs['json'])
        return httpx.Response(200, json={'choices':[]},
                              request=httpx.Request('POST','https://example.invalid'))
    monkeypatch.setattr(httpx.Client, 'post', malformed)
    # TestClient inherits httpx.Client, so invoke the ASGI transport through request.
    response = client.request('POST', f'/api/projects/{p}/qa', json={'query':'退款','retrieval_mode':'keyword','generate':True})
    assert response.status_code == 503
    assert len(provider_calls) == 1


def test_ingest_failure_does_not_overwrite_concurrent_revision(client, monkeypatch, tmp_path):
    service = client.app.state.service
    repository = service.repository
    p = project(client)
    execute = repository.execute_formal_write
    update_run = repository.update_ingest_run
    attempts, conflicts = [], []

    def edit_before_completion(*args, **kwargs):
        run = update_run(*args, **kwargs)
        if kwargs.get('active_stage') == 'extraction' and 'chunks_total' in (kwargs.get('counts_patch') or {}):
            assert not attempts
            # Commit independently while extraction is still outside its completion transaction.
            with closing(Repository(tmp_path / 'service.sqlite', bootstrap=False)) as writer:
                receipt = writer.get_record(p, run['document_id'])
                assert receipt['version'] == 1
                replacement = {key: value for key, value in receipt.items() if key in RecordWrite.model_fields}
                replacement['text'] = 'USER EDIT TO PRESERVE'
                writer.put_record(p, replacement, expected_version=receipt['version'])
                attempts.append(receipt['id'])
        return run

    def observe_completion_conflict(plan):
        completions = [action for action in plan['actions']
                       if action['kind'] == 'record_put'
                       and action['record']['kind'] == 'document'
                       and action['record']['metadata']['status'] == 'ready']
        if not completions:
            return execute(plan)
        assert len(completions) == 1
        completion = completions[0]
        assert attempts == [completion['record']['id']]
        assert completion['expected_version'] == 1
        try:
            return execute(plan)
        except ValueError as exc:
            conflicts.append(str(exc))
            raise

    monkeypatch.setattr(repository, 'update_ingest_run', edit_before_completion)
    monkeypatch.setattr(repository, 'execute_formal_write', observe_completion_conflict)
    response = client.post(f'/api/projects/{p}/documents', json={
        'title':'并发文档','text':'ORIGINAL','extraction_mode':'documents'})
    assert response.status_code == 503
    assert len(attempts) == 1
    assert conflicts == ['版本冲突：记录已变更']
    current = repository.current_records(p)
    documents = [row for row in current if row['kind'] == 'document']
    assert len(documents) == 1
    assert documents[0]['text'] == 'USER EDIT TO PRESERVE'
    assert documents[0]['version'] == 2
    assert len(repository.history(p, documents[0]['id'])) == 2
    assert not [row for row in current if row['kind'] == 'chunk']


def test_ingest_prefers_relation_extracted_validity_over_request(client, monkeypatch):
    import knowledge_service.integrations.semantica_adapter as adapter
    # This test exercises temporal extraction, not ontology governance.  Start
    # without the packaged ontology so the compatibility route performs the
    # one allowed initial bootstrap instead of creating a governed edit draft.
    p = project(client, use_default_ontology=False)
    base = f'/api/projects/{p}'
    ttl='@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> . :Person a owl:Class . :Company a owl:Class . :worksAt a owl:ObjectProperty .'
    seed_project_ontology(client, base+'', ttl)
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
