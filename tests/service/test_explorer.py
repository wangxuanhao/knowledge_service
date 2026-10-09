from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


ATTRIBUTE_TTL = '''
@prefix ex: <https://test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Person a owl:Class ; rdfs:label "人员"@zh .
ex:knows a owl:ObjectProperty ; rdfs:domain ex:Person ; rdfs:range ex:Person .
ex:age a owl:DatatypeProperty ; rdfs:label "年龄"@zh ;
  rdfs:domain ex:Person ; rdfs:range xsd:integer .
'''


def _assertion(repo, project_id, assertion_id, status, *, subject_id, value):
    datatype = 'http://www.w3.org/2001/XMLSchema#integer'
    repo.create_assertion(project_id, {
        'id': assertion_id, 'kind': 'attribute',
        'document_id': 'candidate-source', 'document_version_id': 'candidate-source-v1',
        'chunk_id': 'candidate-chunk', 'quote': f'年龄 {value}',
        'payload': {'subject_id': subject_id, 'type': 'https://test/age',
                    'value': value, 'datatype': datatype,
                    'valid_from': None, 'valid_until': None},
        'actor': 'test',
    })
    repo.transition_assertion(
        project_id, assertion_id, 1, status, 'test decision', 'test')


def test_subgraph_attribute_modes_group_values_and_expand_only_selected_entity(tmp_path):
    app = create_app(tmp_path/'attribute-explorer.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project_id = client.post('/api/projects', json={
            'name': 'attribute explorer', 'use_default_ontology': False,
        }).json()['id']
        base = f'/api/projects/{project_id}'
        ontology = client.post(base+'/ontologies', json={'turtle': ATTRIBUTE_TTL}).json()
        service = app.state.service
        datatype = 'http://www.w3.org/2001/XMLSchema#integer'
        saved = service.write(project_id, [
            {'id': 'a', 'kind': 'entity', 'type': 'https://test/Person', 'text': '甲'},
            {'id': 'b', 'kind': 'entity', 'type': 'https://test/Person', 'text': '乙'},
            {'id': 'a-knows-b', 'kind': 'relation', 'type': 'https://test/knows',
             'text': '甲认识乙', 'subject_id': 'a', 'object_id': 'b'},
            {'id': 'a-age-one', 'kind': 'attribute', 'type': 'https://test/age',
             'text': '甲年龄十九', 'subject_id': 'a', 'value': 19, 'datatype': datatype,
             'ontology_id': ontology['id']},
            {'id': 'a-age-two', 'kind': 'attribute', 'type': 'https://test/age',
             'text': '甲年龄二十一', 'subject_id': 'a', 'value': 21, 'datatype': datatype,
             'ontology_id': ontology['id']},
            {'id': 'b-age', 'kind': 'attribute', 'type': 'https://test/age',
             'text': '乙年龄三十', 'subject_id': 'b', 'value': 30, 'datatype': datatype,
             'ontology_id': ontology['id']},
        ])
        first_age = next(row for row in saved if row['id'] == 'a-age-one')
        service.write(project_id, [{
            'id': 'a-age-one', 'kind': 'attribute', 'type': 'https://test/age',
            'text': '甲年龄二十', 'subject_id': 'a', 'value': 20, 'datatype': datatype,
            'ontology_id': ontology['id'],
        }], expected_versions={'a-age-one': first_age['version']})

        repo = service.repository
        _assertion(repo, project_id, 'conflict-a', 'contradicting', subject_id='a', value=99)
        _assertion(repo, project_id, 'rejected-a', 'rejected', subject_id='a', value=98)
        _assertion(repo, project_id, 'conflict-b', 'contradicting', subject_id='b', value=31)
        scoped_calls = []
        original_scoped = service.scoped

        def counted_scoped(project, scope):
            scoped_calls.append(dict(scope))
            return original_scoped(project, scope)

        service.scoped = counted_scoped

        summary = client.post(base+'/subgraph', json={}).json()
        entity_a = next(row for row in summary['nodes'] if row['id'] == 'a')
        assert len(entity_a['attributes']) == 1
        group = entity_a['attributes'][0]
        assert group['predicate'] == 'https://test/age'
        assert group['label'] == '年龄'
        assert [(value['value'], value['status']) for value in group['values']] == [
            (20, 'accepted'), (21, 'accepted'), (99, 'contradicting')]
        conflict = next(value for value in group['values'] if value['status'] == 'contradicting')
        assert conflict['candidate_id'] == conflict['assertion_id'] == 'conflict-a'
        assert conflict['provenance'] == {
            'document_id': 'candidate-source',
            'document_version_id': 'candidate-source-v1',
            'chunk_id': 'candidate-chunk', 'source_hash': None,
            'start_char': None, 'end_char': None, 'quote': '年龄 99',
        }
        assert next(value for value in group['values'] if value['record_id'] == 'a-age-one')[
            'accepted_support_count'] == 1
        assert all(row['kind'] == 'entity' for row in summary['nodes'])
        assert all(edge['kind'] == 'relation' for edge in summary['edges'])

        hidden = client.post(base+'/subgraph', json={'attribute_mode': 'none'}).json()
        assert all('attributes' not in row for row in hidden['nodes'])
        assert all(row['kind'] == 'entity' for row in hidden['nodes'])

        expanded = client.post(base+'/subgraph', json={
            'node_id': 'a', 'hops': 1, 'attribute_mode': 'expanded',
        }).json()
        attribute_nodes = [row for row in expanded['nodes'] if row['kind'] == 'attribute_value']
        assert [(row['value'], row['status']) for row in attribute_nodes] == [
            (20, 'accepted'), (21, 'accepted'), (99, 'contradicting')]
        assert {row['subject_id'] for row in attribute_nodes} == {'a'}
        assert len({row['id'] for row in attribute_nodes}) == 3
        attribute_edges = [edge for edge in expanded['edges'] if edge['type'] == 'KS_ATTRIBUTE']
        assert len(attribute_edges) == 3
        assert {edge['subject_id'] for edge in attribute_edges} == {'a'}
        assert {edge['object_id'] for edge in attribute_edges} == {
            row['id'] for row in attribute_nodes}
        assert all(row['value'] not in (30, 31) for row in attribute_nodes)
        expanded_again = client.post(base+'/subgraph', json={
            'node_id': 'a', 'hops': 1, 'attribute_mode': 'expanded',
        }).json()
        assert [row['id'] for row in expanded_again['nodes'] if row['kind'] == 'attribute_value'] == [
            row['id'] for row in attribute_nodes]
        assert len(scoped_calls) == 4
        assert scoped_calls[0]['kinds'] == scoped_calls[2]['kinds'] == scoped_calls[3]['kinds'] == [
            'entity', 'relation', 'attribute']
        assert scoped_calls[1]['kinds'] == ['entity', 'relation']
        assert all(not {'node_id', 'hops', 'attribute_mode'} & call.keys()
                   for call in scoped_calls)

        assert client.post(base+'/subgraph', json={'attribute_mode': 'invalid'}).status_code == 422


def test_subgraph_summary_does_not_require_a_published_ontology(tmp_path):
    app = create_app(tmp_path/'ontology-free-explorer.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project_id = client.post('/api/projects', json={
            'name': 'documents only', 'use_default_ontology': False,
        }).json()['id']
        repo = app.state.service.repository
        repo.put_record(project_id, {
            'id': 'a', 'kind': 'entity', 'type': 'https://unpublished.test/Person',
            'text': '甲', 'metadata': {},
        })
        repo.put_record(project_id, {
            'id': 'age', 'kind': 'attribute', 'type': 'https://unpublished.test/age',
            'text': '甲年龄二十', 'subject_id': 'a', 'value': 20,
            'datatype': 'http://www.w3.org/2001/XMLSchema#integer', 'metadata': {},
        })
        base = f'/api/projects/{project_id}/subgraph'
        summary = client.post(base, json={})
        hidden = client.post(base, json={
            'node_id': 'a', 'attribute_mode': 'none',
        })
        expanded = client.post(base, json={
            'node_id': 'a', 'attribute_mode': 'expanded',
        })
        assert [response.status_code for response in (summary, hidden, expanded)] == [200, 200, 200]
        group = summary.json()['nodes'][0]['attributes'][0]
        assert group['predicate'] == group['label'] == 'https://unpublished.test/age'
        assert 'attributes' not in hidden.json()['nodes'][0]
        assert [row['value'] for row in expanded.json()['nodes']
                if row['kind'] == 'attribute_value'] == [20]


def test_subgraph_filters_entity_type_and_predicate_without_dangling_edges(tmp_path):
    app = create_app(tmp_path/'filtered-explorer.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project_id = client.post('/api/projects', json={
            'name': 'filtered explorer', 'use_default_ontology': False,
        }).json()['id']
        repo = app.state.service.repository
        repo.put_batch(project_id, [
            {'id': 'a', 'kind': 'entity', 'type': 'Thing', 'text': '甲', 'metadata': {}},
            {'id': 'b', 'kind': 'entity', 'type': 'Thing', 'text': '乙', 'metadata': {}},
            {'id': 's', 'kind': 'entity', 'type': 'Service', 'text': '服务', 'metadata': {}},
            {'id': 'r-main', 'kind': 'relation', 'type': 'mentions', 'text': '甲提到乙',
             'subject_id': 'a', 'object_id': 'b', 'metadata': {}},
            {'id': 'r-other', 'kind': 'relation', 'type': 'ignores', 'text': '乙忽略甲',
             'subject_id': 'b', 'object_id': 'a', 'metadata': {}},
            {'id': 'r-cross', 'kind': 'relation', 'type': 'mentions', 'text': '甲提到服务',
             'subject_id': 'a', 'object_id': 's', 'metadata': {}},
        ])
        scoped_calls = []
        original_scoped = app.state.service.scoped

        def counted_scoped(project, scope):
            scoped_calls.append(dict(scope))
            return original_scoped(project, scope)

        app.state.service.scoped = counted_scoped
        endpoint = f'/api/projects/{project_id}/subgraph'

        default = client.post(endpoint, json={}).json()
        assert {row['id'] for row in default['nodes']} == {'a', 'b', 's'}
        assert {row['id'] for row in default['edges']} == {'r-main', 'r-other', 'r-cross'}

        graph_filter = {'entity_type': 'Thing', 'predicate': 'mentions'}
        full = client.post(endpoint, json=graph_filter).json()
        seeded = client.post(endpoint, json={
            **graph_filter, 'node_id': 'a', 'hops': 1,
        }).json()
        for result in (full, seeded):
            node_ids = {row['id'] for row in result['nodes']}
            assert node_ids == {'a', 'b'}
            assert {row['id'] for row in result['edges']} == {'r-main'}
            assert all(edge['subject_id'] in node_ids and edge['object_id'] in node_ids
                       for edge in result['edges'])
        assert all(not {'entity_type', 'predicate'} & call.keys() for call in scoped_calls)


def test_explorer_scope_sources_snapshot_and_evaluation(tmp_path):
    with TestClient(create_app(tmp_path/'x.sqlite',HashingEncoder())) as client:
        p=client.post('/api/projects',json={'name':'explorer'}).json()['id']
        base=f'/api/projects/{p}'
        assert client.post(base+'/documents',json={'title':'原文','text':'退款证据','extract':False}).status_code==201
        records=[dict(id='a',kind='entity',type='Merchant',text='商户',metadata={'city':'北京'}),
                 dict(id='b',kind='entity',type='Platform',text='平台',metadata={'city':'北京'}),
                 dict(id='r',kind='relation',type='onboards',text='入驻',subject_id='a',object_id='b',metadata={'city':'北京'})]
        assert client.post(base+'/records',json={'records':records}).status_code==201
        response=client.post(base+'/dashboard',json={})
        assert response.status_code==200,response.text
        assert response.json()['counts']['entity']==2
        assert client.post(base+'/sources',json={}).json()['documents'][0]['text']=='退款证据'
        assert len(client.post(base+'/subgraph',json={'node_id':'a','hops':1}).json()['nodes'])==2
        assert len(client.post(base+'/mindmap',json={'root_id':'a','depth':2}).json()['tree']['children'])==1
        scoped={'filters':{'field':'city','op':'eq','value':'上海'}}
        assert client.post(base+'/subgraph',json={**scoped,'node_id':'a'}).status_code==404
        snap=client.post(base+'/snapshots',json={'name':'before'}).json()
        client.post(base+'/delete',json={'record_id':'a','expected_version':1})
        assert client.post(base+'/dashboard',json={}).json()['counts']['entity']==1
        restored=client.post(base+'/snapshots/'+snap['id']+'/restore',json={})
        assert restored.status_code==200,restored.text
        assert client.post(base+'/dashboard',json={}).json()['counts']['entity']==2
        score=client.post(base+'/evaluate',json={'entities':['商户','平台'],'relations':[['商户','onboards','平台']]}).json()
        assert score['entities']['f1']==1 and score['relations']['f1']==1


def test_document_job_and_stream(tmp_path):
    import time
    with TestClient(create_app(tmp_path/'x.sqlite',HashingEncoder())) as client:
        p=client.post('/api/projects',json={'name':'tasks'}).json()['id']
        base=f'/api/projects/{p}'
        submitted=client.post(base+'/documents/jobs',json={'title':'job','text':'异步原文','extract':False})
        assert submitted.status_code==202,submitted.text
        job_id=submitted.json()['id']
        for _ in range(200):
            job=client.get('/api/jobs/'+job_id).json()
            if job['status'] in ('completed','failed'):break
            time.sleep(.02)
        assert job['status']=='completed',job
        assert len(client.get(base+'/jobs').json()['jobs'])==1
        response=client.post(base+'/qa/stream',json={'query':'原文','generate':False})
        assert response.status_code==200 and 'event: evidence' in response.text and 'event: done' in response.text


def test_qa_stream_reads_scoped_records_once_and_returns_evidence_with_answer(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path/'qa-once.sqlite',HashingEncoder())) as client:
        p=client.post('/api/projects',json={'name':'qa-once'}).json()['id']
        base=f'/api/projects/{p}'
        assert client.post(base+'/documents',json={
            'title':'退款规则','text':'退款需要原始凭证','extract':False,
        }).status_code==201
        repository=client.app.state.service.repository
        original_query=repository.query
        calls=[]

        def counted_query(*args, **kwargs):
            calls.append((args, kwargs))
            return original_query(*args, **kwargs)

        monkeypatch.setattr(repository,'query',counted_query)
        response=client.post(base+'/qa/stream',json={
            'query':'退款','generate':False,'retrieval_mode':'keyword',
        })

        assert response.status_code==200
        assert len(calls)==1
        assert 'event: evidence' in response.text
        assert '退款需要原始凭证' in response.text
        assert 'event: done' in response.text


def test_qa_and_stream_preserve_ontology_scope_for_shared_context(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path/'qa-ontology-scope.sqlite', HashingEncoder())) as client:
        p = client.post('/api/projects', json={
            'name': 'qa ontology scope', 'use_default_ontology': False,
        }).json()['id']
        service = client.app.state.service
        scopes = []

        def capture_scoped(project_id, scope):
            assert project_id == p
            scopes.append(dict(scope))
            return []

        monkeypatch.setattr(service, 'scoped', capture_scoped)
        request = {
            'query': '退款', 'generate': False, 'retrieval_mode': 'keyword',
            'ontology_scope': 'ids',
            'ontology_ids': [' ontology-v1 ', 'ontology-v1'],
        }

        answer = client.post(f'/api/projects/{p}/qa', json=request)
        stream = client.post(f'/api/projects/{p}/qa/stream', json=request)

        assert answer.status_code == stream.status_code == 200
        assert len(scopes) == 2
        for scope in scopes:
            assert scope['ontology_scope'] == 'ids'
            assert scope['ontology_ids'] == ['ontology-v1']
