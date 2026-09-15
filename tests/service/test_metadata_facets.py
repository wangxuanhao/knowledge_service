from fastapi.testclient import TestClient
from knowledge_service.api import create_app
from knowledge_service.embeddings import HashingEncoder


def test_discovered_facets_are_project_scoped_and_typed(tmp_path):
    with TestClient(create_app(tmp_path/'facets.sqlite',HashingEncoder())) as c:
        p=c.post('/api/projects',json={'name':'A'}).json()['id']
        other=c.post('/api/projects',json={'name':'B'}).json()['id']
        repo=c.app.state.service.repository
        repo.put_record(p,{'id':'a','kind':'entity','text':'甲','metadata':{'region':{'city':'北京'},'tags':['规则','规则'],'approved':True,'count':1,'_internal':'hidden'},'valid_from':'2025-01-01'})
        repo.put_record(other,{'id':'b','kind':'entity','text':'乙','metadata':{'only_other':'secret'}})
        body={'filters':{'field':'region.city','op':'eq','value':'不匹配'}}
        r=c.post(f'/api/projects/{p}/metadata/facets',json=body)
        assert r.status_code==200,r.text
        fields={f['field']:f for f in r.json()['fields']}
        assert 'metadata.only_other' not in fields and 'metadata._internal' not in fields
        assert fields['metadata.region.city']['values']==[{'value':'北京','op':'eq'}]
        assert fields['metadata.tags']['values']==[{'value':'规则','op':'contains'}]
        assert fields['metadata.tags']['records']==1
        assert fields['metadata.approved']['values'][0]['value'] is True
        assert fields['metadata.count']['types']==['number']
        old=c.post(f'/api/projects/{p}/metadata/facets',json={'valid_at':'2024-01-01'}).json()
        assert old['fields']==[]
        value=fields['metadata.tags']['values'][0]
        result=c.post(f'/api/projects/{p}/explore',json={'filters':{'field':'metadata.tags',**value}}).json()
        assert [n['id'] for n in result['nodes']]==['a']
        assert c.get('/').headers['cache-control']=='no-cache'
