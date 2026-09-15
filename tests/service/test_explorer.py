from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.embeddings import HashingEncoder


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
